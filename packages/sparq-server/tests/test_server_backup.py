"""Backups on the database host: what install makes, what verify checks, what the files say."""

import pytest
from fakehost import FakeHost
from sparq_server.admin import managed
from sparq_server.admin.answers import Answers, parse_answers
from sparq_server.admin.install import InstallRefused, run_install
from sparq_server.admin.preflight import Check
from sparq_server.admin.verify import run_verify

BASE = """
role = "database_host"
[database_host.backup]
target = "/mnt/sparq-backup"
[database_host.postgres]
allowed_clients = ["192.0.2.20"]
[database_host.test_cluster]
enabled = true
allowed_clients = ["192.0.2.30"]
"""


def installed(extra: str = "") -> tuple[FakeHost, Answers]:
    host, config = FakeHost(), parse_answers(BASE + extra)
    run_install(host, config)
    return host, config


def verify(host: FakeHost, config: Answers) -> dict[str, Check]:
    return {c.name: c for c in run_verify(host, config.installable_host())}


# --- the install ----------------------------------------------------------------------------------


def test_install_makes_a_stanza_and_a_first_full_backup_for_each_cluster() -> None:
    host, _ = installed()
    assert host.stanzas == {"main": ["full"], "sparq_test": ["full"]}
    assert "/mnt/sparq-backup/pgbackrest" in host.paths
    assert "/mnt/sparq-backup/pgbackrest/manifests" in host.paths


def test_the_configuration_is_rendered_once_and_holds_no_secret() -> None:
    host, config = installed()
    text = host.files[managed.PGBACKREST_CONF]
    assert text == managed.render_pgbackrest(config.installable_host())
    assert "repo1-cipher-type=aes-256-cbc" in text and "repo1-cipher-pass" not in text
    assert host.modes[managed.PGBACKREST_CONF] == 0o640
    assert "repo1-retention-full=5" in text and "repo1-retention-diff=7" in text
    assert f"lock-path={managed.SPOOL_DIRECTORY}" in text  # never the shared /tmp default
    assert "repo1-retention-full-type=count" in text  # five backups, not five days of them
    assert "[main]" in text and "[sparq_test]" in text


def test_the_passphrase_is_made_once_readable_only_by_postgres_and_never_replaced() -> None:
    host, config = installed()
    first = host.files[managed.CIPHER_FILE]
    assert first.startswith("[global]\nrepo1-cipher-pass=") and len(first) > 60
    assert host.modes[managed.CIPHER_FILE] == 0o600
    assert host.owners[managed.CIPHER_FILE] == ("postgres", "postgres")
    run_install(host, config)
    assert host.files[managed.CIPHER_FILE] == first
    assert not any("repo1-cipher-pass" in line for line in host.log)  # never in a command line


def test_without_encryption_there_is_no_cipher_setting_and_no_passphrase_file() -> None:
    host, config = FakeHost(), parse_answers(BASE.replace("target =", "encrypt = false\ntarget ="))
    run_install(host, config)
    assert "cipher" not in host.files[managed.PGBACKREST_CONF]
    assert managed.CIPHER_FILE not in host.files
    assert "backup passphrase" not in verify(host, config)


def test_timers_and_services_are_written_enabled_and_never_rewritten() -> None:
    host, config = installed()
    units = managed.backup_units(config.installable_host())
    assert len(units) == 8  # a service and a timer for each of full and diff, for two clusters
    assert all(host.files[path] == text for path, text in units.items())
    assert host.enabled == set(managed.timer_names(config.installable_host()))
    before = len(host.log)
    run_install(host, config)
    asked = [c for c in host.log[before:] if c.startswith("systemctl")]
    assert asked and all(c.startswith("systemctl is-enabled") for c in asked)  # nothing changed


def test_a_share_the_postgres_account_cannot_write_refuses_with_the_way_out() -> None:
    host, config = FakeHost(), parse_answers(BASE)
    host.share_writable = False
    with pytest.raises(InstallRefused, match="not writable by the postgres account"):
        run_install(host, config)
    assert host.stanzas == {}


def test_a_failing_archive_check_stops_before_any_backup_is_taken() -> None:
    host, config = FakeHost(), parse_answers(BASE)
    host.archive_broken = True
    with pytest.raises(InstallRefused, match="a step failed"):
        run_install(host, config)
    assert host.stanzas["main"] == []  # created, but no backup of a cluster whose WAL is lost


def test_an_existing_stanza_with_no_backup_gets_a_backup_and_no_second_stanza() -> None:
    host, config = installed()
    host.stanzas["main"].clear()
    before = len(host.log)
    run_install(host, config)
    new = host.log[before:]
    assert any("--stanza=main --type=full backup" in c for c in new)
    assert not any("stanza-create" in c for c in new)


def test_a_missing_stanza_is_created_again() -> None:
    host, config = installed()
    del host.stanzas["sparq_test"]
    run_install(host, config)
    assert host.stanzas["sparq_test"] == ["full"]


# --- the settings and files --------------------------------------------------------------------


def test_both_clusters_archive_their_wal_to_their_own_stanza() -> None:
    host, config = installed()
    for spec in managed.clusters_of(config.installable_host()):
        text = host.files[managed.conf_path(spec)]
        assert "archive_mode = on" in text and "wal_level = replica" in text
        assert f"archive_command = 'pgbackrest --stanza={spec.name} archive-push %p'" in text
        assert "archive_timeout = 60" in text


def test_a_restored_drill_cluster_never_archives() -> None:
    spec = managed.ClusterSpec("drill_main", 5440, "1GB", (), "sparq", None, archive=False)
    text = managed.render_conf(spec)
    assert "archive_mode = off" in text and "archive_command" not in text


def test_full_backups_run_on_sundays_and_differentials_on_the_other_days_at_different_times() -> (
    None
):
    _, config = installed()
    units = managed.backup_units(config.installable_host())
    timers = {p.rsplit("/", 1)[1]: t for p, t in units.items() if p.endswith(".timer")}
    assert "OnCalendar=Sun *-*-* 01:30:00" in timers["sparq-backup-full-main.timer"]
    assert "OnCalendar=Mon..Sat *-*-* 01:30:00" in timers["sparq-backup-diff-main.timer"]
    assert "OnCalendar=Sun *-*-* 02:00:00" in timers["sparq-backup-full-sparq-test.timer"]
    assert all("Persistent=true" in t for t in timers.values())


def test_a_service_runs_as_postgres_and_writes_the_manifest_with_escaped_percent_signs() -> None:
    _, config = installed()
    service = managed.backup_units(config.installable_host())[
        "/etc/systemd/system/sparq-backup-diff-main.service"
    ]
    assert "User=postgres" in service
    assert "ExecStart=/usr/bin/pgbackrest --stanza=main --type=diff backup" in service
    assert "$$(date -u +%%Y%%m%%dT%%H%%M%%SZ)" in service  # systemd would eat a bare $ or %
    assert f"-f {managed.COUNTS_SQL}" in service and "-d sparq " in service


def test_the_manifest_query_counts_rows_exactly_and_skips_the_catalogs() -> None:
    sql = managed.render_counts_sql()
    assert "select count(*)" in sql and "information_schema.tables" in sql
    assert "'pg_catalog'" in sql and "timescaledb" in sql


# --- verify ---------------------------------------------------------------------------------------


def test_a_freshly_installed_host_passes_the_backup_checks() -> None:
    host, config = installed()
    named = verify(host, config)
    for name in ("backup configuration", "backup units", "backup timers", "backup passphrase"):
        assert named[name].status == "ok", named[name]
    for name in ("main WAL archive", "main backup", "test WAL archive", "test backup"):
        assert named[name].status == "ok", named[name]
    assert "1 hours old" in named["main backup"].detail


def test_an_account_that_is_not_root_cannot_ask_pgbackrest_and_says_so() -> None:
    host, config = installed()
    host.root = False
    named = verify(host, config)
    assert named["backup state"].status == "warn" and "needs root" in named["backup state"].detail
    assert named["backup configuration"].status == "warn"  # 0640 postgres: unreadable, not wrong
    assert named["backup timers"].status == "ok"


def test_an_edited_configuration_or_unit_fails() -> None:
    host, config = installed()
    host.files[managed.PGBACKREST_CONF] += "repo1-retention-full=1\n"
    path = next(iter(managed.backup_units(config.installable_host())))
    host.files[path] += "ExecStart=/bin/true\n"
    named = verify(host, config)
    assert named["backup configuration"].status == "fail"
    assert (
        named["backup units"].status == "fail"
        and path.rsplit("/", 1)[1] in named["backup units"].detail
    )


def test_a_timer_that_is_not_enabled_fails() -> None:
    host, config = installed()
    host.enabled.discard("sparq-backup-full-main.timer")
    check = verify(host, config)["backup timers"]
    assert check.status == "fail" and "sparq-backup-full-main.timer" in check.detail


def test_a_lost_passphrase_file_fails() -> None:
    host, config = installed()
    del host.files[managed.CIPHER_FILE]
    assert verify(host, config)["backup passphrase"].status == "fail"


def test_wal_that_does_not_reach_the_share_fails() -> None:
    host, config = installed()
    host.archive_broken = True
    assert verify(host, config)["main WAL archive"].status == "fail"


def test_a_backup_more_than_a_day_and_a_half_old_fails_and_none_at_all_fails() -> None:
    host, config = installed()
    host.backup_age = 50 * 3600
    assert verify(host, config)["main backup"].status == "fail"
    host.backup_age = 3600
    host.stanzas["main"].clear()
    assert "no backup found" in verify(host, config)["main backup"].detail


def test_the_units_are_loaded_by_systemd_before_their_timers_are_enabled() -> None:
    host, config = installed()
    reload = host.log.index("systemctl daemon-reload")
    first_enable = next(i for i, c in enumerate(host.log) if c.startswith("systemctl enable"))
    assert reload < first_enable
    before = len(host.log)
    run_install(host, config)
    assert "systemctl daemon-reload" not in host.log[before:]  # only when a unit file changed
