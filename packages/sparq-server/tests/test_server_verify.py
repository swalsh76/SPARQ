import pytest
from fakehost import FakeHost
from sparq_server.admin import managed
from sparq_server.admin.answers import Answers, parse_answers
from sparq_server.admin.install import run_install
from sparq_server.admin.preflight import Check, refused
from sparq_server.admin.verify import run_verify

BASE = """
role = "database_host"
[database_host.backup]
target = "/mnt/sparq-backup"
[database_host.postgres]
allowed_clients = ["192.0.2.20"]
"""
TEST = (
    '[database_host.test_cluster]\nenabled = true\nport = 5433\nallowed_clients = ["192.0.2.30"]\n'
)


def installed(extra: str = "") -> tuple[FakeHost, Answers]:
    host, config = FakeHost(), parse_answers(BASE + extra)
    run_install(host, config)
    return host, config


def verify(host: FakeHost, config: Answers) -> dict[str, Check]:
    return {c.name: c for c in run_verify(host, config.installable_host())}


def test_a_freshly_installed_host_passes_every_check() -> None:
    host, config = installed(TEST)
    checks = run_verify(host, config.installable_host())
    assert not refused(checks)
    assert all(c.status == "ok" for c in checks), [c for c in checks if c.status != "ok"]
    names = {c.name for c in checks}
    assert {"main durability and security settings", "test TimescaleDB extension"} <= names


def test_an_account_that_is_not_root_skips_the_database_questions_and_says_so() -> None:
    host, config = installed()
    host.root = False
    named = verify(host, config)
    assert named["database settings"].status == "warn"
    assert "needs root" in named["database settings"].detail
    assert "main durability and security settings" not in named
    # the access rules are readable only by their owner, which is reported and not guessed
    assert named["main access rules"].status == "warn"
    assert named["main settings"].status == "ok"


def test_a_package_at_the_wrong_version_or_missing_fails() -> None:
    host, config = installed()
    host.installed["postgresql-18"] = "18.5-1"
    del host.installed["timescaledb-2-postgresql-18"]
    named = verify(host, config)
    assert named["postgresql-18"].status == "fail" and "pins 18.6" in named["postgresql-18"].detail
    assert named["timescaledb-2-postgresql-18"].status == "fail"


def test_a_package_that_is_not_held_is_a_warning() -> None:
    host, config = installed()
    host.held.discard("postgresql-18")
    assert verify(host, config)["postgresql-18"].status == "warn"


def test_a_missing_service_account_fails() -> None:
    host, config = installed()
    host.users.clear()
    assert verify(host, config)["service account"].status == "fail"


def test_a_cluster_that_is_down_or_on_the_wrong_port_fails() -> None:
    host, config = installed()
    host.clusters["main"].status = "down"
    assert verify(host, config)["main cluster"].status == "fail"
    host.clusters["main"].status = "online"
    host.clusters["main"].port = 5999
    assert "wanted online on 5432" in verify(host, config)["main cluster"].detail
    del host.clusters["main"]
    assert "no 18/main" in verify(host, config)["main cluster"].detail


@pytest.mark.parametrize(
    ("label", "path_of"),
    [
        ("main settings", managed.conf_path),
        ("main access rules", managed.hba_path),
        ("main user map", managed.ident_path),
    ],
)
def test_a_managed_file_edited_by_hand_fails(label: str, path_of) -> None:  # type: ignore[no-untyped-def]
    host, config = installed()
    spec = managed.main_cluster(config.installable_host())
    host.files[path_of(spec)] += "# an edit\n"
    assert verify(host, config)[label].status == "fail"
    del host.files[path_of(spec)]
    assert "missing" in verify(host, config)[label].detail


def test_a_certificate_close_to_expiry_or_missing_fails() -> None:
    host, config = installed()
    host.cert_days_left = 10
    assert verify(host, config)["certificate"].status == "fail"
    host.cert_days_left = 3650
    assert "AA:BB:CC" in verify(host, config)["certificate"].detail
    del host.files[managed.TLS_CERT]
    assert verify(host, config)["certificate"].status == "fail"


def test_wrong_durability_settings_in_the_database_fail_and_are_named() -> None:
    host, config = installed()
    host.clusters["main"].settings[0] = "off"  # fsync
    check = verify(host, config)["main durability and security settings"]
    assert check.status == "fail" and "fsync=off" in check.detail


def test_a_missing_extension_fails() -> None:
    host, config = installed()
    host.clusters["main"].extensions.clear()
    assert verify(host, config)["main TimescaleDB extension"].status == "fail"


def test_a_database_that_cannot_be_asked_fails_the_check_and_does_not_crash() -> None:
    host, config = installed()
    host.fail_on = "psql"
    assert verify(host, config)["main database"].status == "fail"


def test_a_missing_test_password_file_fails_when_a_test_cluster_is_wanted() -> None:
    host, config = installed(TEST)
    del host.files[managed.TEST_PASSWORD_FILE]
    assert verify(host, config)["test cluster password"].status == "fail"


def test_an_unexpected_listener_fails_verify_too() -> None:
    host, config = installed()
    host.extra_listeners.append("LISTEN 0 128 0.0.0.0:8080 0.0.0.0:*")
    assert verify(host, config)["listeners"].status == "fail"
