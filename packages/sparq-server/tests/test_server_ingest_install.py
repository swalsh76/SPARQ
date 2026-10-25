"""``install``, ``verify`` and ``ingest-status`` for the ingest service, against the fake host."""

from pathlib import Path

import pytest
from fakehost import FakeHost
from sparq_server.admin import managed
from sparq_server.admin.answers import Answers, AnswersError, parse_answers
from sparq_server.admin.cli import main
from sparq_server.admin.ingest_status import run_ingest_status
from sparq_server.admin.install import (
    plan_install,
    render_plan,
    run_install,
)
from sparq_server.admin.preflight import Check
from sparq_server.admin.verify import run_verify

BASE = """
role = "database_host"
[database_host.backup]
target = "/mnt/sparq-backup"
[database_host.postgres]
allowed_clients = ["192.0.2.20"]
[database_host.ingest]
checkout = "/home/agent/sparq"
uv = "/home/agent/.local/bin/uv"
"""
UNIT = f"{managed.SYSTEMD_DIRECTORY}/{managed.INGEST_UNIT}"


def answers(extra: str = "") -> Answers:
    return parse_answers(BASE + extra)


def add_key(host: FakeHost) -> None:
    for name in managed.INGEST_CREDENTIALS:
        host.files[managed.credential_file(name)] = "encrypted"


def installed(key: bool = False) -> tuple[FakeHost, Answers]:
    host, config = FakeHost(), answers()
    if key:
        add_key(host)
    run_install(host, config)
    return host, config


def by_name(checks: list[Check]) -> dict[str, Check]:
    return {c.name: c for c in checks}


def verified(host: FakeHost, config: Answers) -> dict[str, Check]:
    return by_name(run_verify(host, config.installable_host()))


# --- the answers ----------------------------------------------------------------------------------


def test_the_ingest_is_off_unless_the_answers_turn_it_on() -> None:
    config = parse_answers(BASE.split("[database_host.ingest]")[0]).installable_host()
    assert config.ingest.enabled is False and config.ingest.disk_stop_percent == 90


def test_an_enabled_ingest_needs_the_checkout_and_the_program_that_builds_it() -> None:
    with pytest.raises(AnswersError, match="checkout and uv"):
        parse_answers(BASE.split("checkout")[0])


@pytest.mark.parametrize("percent", [49, 100])
def test_the_disk_limit_must_leave_room_to_act(percent: int) -> None:
    with pytest.raises(AnswersError):
        answers(f"disk_stop_percent = {percent}\n")


def test_the_plan_names_the_ingest_steps_and_the_disk_limit() -> None:
    text = render_plan(plan_install(answers()))
    assert managed.INGEST_ROLE in text and "/home/agent/sparq" in text and "90%" in text


def test_the_plan_has_no_ingest_steps_when_it_is_off() -> None:
    plain = parse_answers(BASE.split("[database_host.ingest]")[0])
    assert "ingest" not in render_plan(plan_install(plain))


# --- install --------------------------------------------------------------------------------------


def test_install_creates_the_role_builds_the_program_and_writes_the_unit() -> None:
    host, config = FakeHost(), answers()
    outcomes = {o.step: o for o in run_install(host, config)}
    assert [o.changed for k, o in outcomes.items() if k.startswith("ingest")] == [True] * 3
    assert outcomes["tpm support"].changed and "libtss2-rc0t64" in host.installed
    cluster = host.clusters["main"]
    assert managed.INGEST_ROLE in cluster.roles and cluster.grants
    assert host.files[managed.INGEST_CONFIG] == managed.render_ingest_config(
        config.installable_host()
    )
    assert host.files[UNIT] == managed.render_ingest_unit()
    assert host.files[managed.APP_COMMIT_FILE] == "a" * 40 + "\n"
    assert managed.INGEST_UNIT in host.enabled
    assert host.modes[managed.CREDENTIALS_DIRECTORY] == 0o700


def test_without_the_key_the_service_is_enabled_and_left_stopped_with_the_reason() -> None:
    host, config = FakeHost(), answers()
    outcomes = {o.step: o for o in run_install(host, config)}
    assert "not started" in outcomes["ingest service"].detail
    assert managed.INGEST_UNIT not in host.active


def test_with_the_key_the_service_is_started() -> None:
    host, config = FakeHost(), answers()
    add_key(host)
    outcomes = {o.step: o for o in run_install(host, config)}
    assert outcomes["ingest service"].detail == "started"
    assert managed.INGEST_UNIT in host.active


def test_a_second_run_changes_nothing_and_runs_no_changing_command() -> None:
    host, config = installed(key=True)
    host.log.clear()
    outcomes = run_install(host, config)
    assert [o.step for o in outcomes if o.changed] == []
    assert [c for c in host.mutating() if "showhold" not in c] == []  # reading holds is no change


def test_the_program_is_built_with_its_own_interpreter_pinned_to_the_commit() -> None:
    host, _ = installed()
    commands = [c for c in host.log if c.startswith("env ")]
    install, sync = commands
    assert f"UV_PYTHON_INSTALL_DIR={managed.PYTHON_DIRECTORY}" in install
    assert "python install 3.13" in install
    assert f"UV_PROJECT_ENVIRONMENT={managed.APP_DIRECTORY}/.venv" in sync
    for flag in ("--frozen", "--no-dev", "--no-editable", "--package sparq-server"):
        assert flag in sync
    assert "--directory /home/agent/sparq" in sync
    # a changed source with an unchanged pyproject.toml must still replace the installed code
    for package in ("sparq-server", "sparq-core"):
        assert f"--reinstall-package {package}" in sync
    assert f"chmod -R a+rX {managed.APP_DIRECTORY} {managed.PYTHON_DIRECTORY}" in host.log


def test_a_new_commit_rebuilds_the_program_and_restarts_the_service() -> None:
    host, config = installed(key=True)
    host.checkout_commit = "b" * 40
    host.log.clear()
    outcomes = {o.step: o for o in run_install(host, config)}
    assert outcomes["ingest program"].changed and "built commit bbbbbbbbbbbb" in (
        outcomes["ingest program"].detail
    )
    assert outcomes["ingest service"].detail.startswith("restarted")
    assert f"systemctl restart {managed.INGEST_UNIT}" in host.log
    assert host.files[managed.APP_COMMIT_FILE] == "b" * 40 + "\n"


def test_a_hand_edited_settings_file_is_put_back_and_the_service_restarted() -> None:
    host, config = installed(key=True)
    host.files[managed.INGEST_CONFIG] = "disk_stop_percent = 99\n"
    host.log.clear()
    outcomes = {o.step: o for o in run_install(host, config)}
    assert outcomes["ingest service"].changed
    assert host.files[managed.INGEST_CONFIG] == managed.render_ingest_config(
        config.installable_host()
    )
    assert f"systemctl restart {managed.INGEST_UNIT}" in host.log


def test_a_changed_unit_reloads_systemd_before_the_restart() -> None:
    host, config = installed(key=True)
    host.files[UNIT] = "# edited by hand\n"
    host.log.clear()
    run_install(host, config)
    reload_at = host.log.index("systemctl daemon-reload")
    assert reload_at < host.log.index(f"systemctl restart {managed.INGEST_UNIT}")


def test_a_stopped_service_whose_key_is_stored_is_started_again() -> None:
    host, config = installed(key=True)
    host.active.clear()
    outcomes = {o.step: o for o in run_install(host, config)}
    assert outcomes["ingest service"].detail == "started"


def test_a_key_stored_later_starts_the_service_on_the_next_install() -> None:
    host, config = installed()
    add_key(host)
    outcomes = {o.step: o for o in run_install(host, config)}
    assert outcomes["ingest service"].detail == "started"
    assert not outcomes["ingest role"].changed and not outcomes["ingest program"].changed


def test_privileges_missing_again_after_the_migration_are_granted_on_the_next_install() -> None:
    host, config = installed()
    host.clusters["main"].grants = False  # alembic_version opened up by the first migration
    outcomes = {o.step: o for o in run_install(host, config)}
    assert outcomes["ingest role"].changed and host.clusters["main"].grants


def test_with_the_ingest_off_no_ingest_step_runs() -> None:
    host = FakeHost()
    run_install(host, parse_answers(BASE.split("[database_host.ingest]")[0]))
    assert managed.INGEST_ROLE not in host.clusters["main"].roles
    assert managed.INGEST_CONFIG not in host.files


# --- the files install renders --------------------------------------------------------------------


def test_the_unit_runs_as_the_service_account_and_sees_the_key_only_as_a_credential() -> None:
    unit = managed.render_ingest_unit()
    assert "User=sparq" in unit and "Restart=always" in unit
    for name in managed.INGEST_CREDENTIALS:
        assert f"LoadCredentialEncrypted={name}:/etc/sparq/credentials/{name}.cred" in unit
    assert "Environment=" not in unit and "KALSHI" not in unit
    for hardening in ("NoNewPrivileges=yes", "ProtectSystem=strict", "PrivateTmp=yes"):
        assert hardening in unit


def test_the_unit_does_not_restart_a_service_that_stopped_on_purpose() -> None:
    unit = managed.render_ingest_unit()
    assert f"RestartPreventExitStatus={managed.EXIT_DISK_FULL} {managed.EXIT_REFUSED}" in unit
    assert "StartLimitIntervalSec=0" in unit  # a flapping database must not lock it out for good


def test_the_settings_name_the_socket_the_role_and_the_disk_limit_but_no_secret() -> None:
    text = managed.render_ingest_config(answers().installable_host())
    assert 'role = "sparq_ingest"' in text and "disk_stop_percent = 90" in text
    assert 'socket_directory = "/var/run/postgresql"' in text and "password" not in text.lower()
    # the service account cannot enter a cluster's own directory, so the guard watches its parent
    assert f'data_directory = "{managed.DATA_ROOT}"' in text


def test_the_user_map_lets_the_service_account_become_the_ingest_role() -> None:
    assert f"sparq   {managed.SERVICE_USER}   {managed.INGEST_ROLE}" in managed.render_ident()


def test_the_grants_leave_the_migration_bookkeeping_read_only() -> None:
    sql = managed.render_ingest_grants("r", "d", "o")
    assert "REVOKE INSERT, UPDATE ON public.alembic_version FROM r" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON ALL TABLES" in sql and "DELETE" not in sql
    assert "TRUNCATE" not in sql and "CREATE ON" not in sql


# --- verify ---------------------------------------------------------------------------------------


def test_a_fresh_install_without_the_key_verifies_with_one_warning_and_no_failure() -> None:
    host, config = installed()
    checks = verified(host, config)
    assert checks["ingest data key"].status == "warn"
    assert not [c for c in checks.values() if c.status == "fail"]
    assert "ingest service" not in checks


def test_with_the_key_and_the_service_running_every_ingest_check_passes() -> None:
    host, config = installed(key=True)
    checks = verified(host, config)
    for name in ("ingest settings", "ingest unit", "ingest program", "ingest enabled"):
        assert checks[name].status == "ok", name
    assert checks["ingest service"].status == "ok" and checks["ingest role"].status == "ok"
    assert not [c for c in checks.values() if c.status == "fail"]


def test_a_stored_key_with_the_service_down_is_a_failure() -> None:
    host, config = installed(key=True)
    host.active.clear()
    assert verified(host, config)["ingest service"].status == "fail"


@pytest.mark.parametrize(
    ("path", "name"),
    [(managed.INGEST_CONFIG, "ingest settings"), (UNIT, "ingest unit")],
)
def test_a_hand_edited_file_is_a_failure(path: str, name: str) -> None:
    host, config = installed(key=True)
    host.files[path] += "# edited\n"
    assert verified(host, config)[name].status == "fail"


def test_a_program_that_was_never_built_is_a_failure() -> None:
    host, config = installed(key=True)
    del host.files[managed.APP_COMMIT_FILE]
    assert verified(host, config)["ingest program"].status == "fail"


def test_a_unit_that_is_not_enabled_is_a_failure() -> None:
    host, config = installed(key=True)
    host.enabled.discard(managed.INGEST_UNIT)
    assert verified(host, config)["ingest enabled"].status == "fail"


def test_missing_privileges_are_a_failure_that_install_repairs() -> None:
    host, config = installed(key=True)
    host.clusters["main"].grants = False
    assert verified(host, config)["ingest role"].status == "fail"
    run_install(host, config)
    assert verified(host, config)["ingest role"].status == "ok"


def test_another_account_cannot_ask_the_database_and_is_told_so() -> None:
    host, config = installed(key=True)
    host.root = False
    assert verified(host, config)["ingest role"].status == "warn"


def test_with_the_ingest_off_verify_says_nothing_about_it() -> None:
    plain = parse_answers(BASE.split("[database_host.ingest]")[0])
    host = FakeHost()
    run_install(host, plain)
    assert not [n for n in verified(host, plain) if n.startswith("ingest")]


# --- ingest-status --------------------------------------------------------------------------------

HEALTHY = {
    "FROM ingest_state WHERE key = 'feed.heartbeat_ms'": "2.5\n",
    "FROM ticker WHERE ts": "120345|8123\n",
    "FROM gap_log WHERE started_at": "ticker|unrecoverable|1\ntrades|done|1\n",
    "WHERE status IN ('open', 'backfilling')": "0\n",
    "hypertable_size('ticker')": "2500000000|10000000000|2000000000\n",
    "hypertable_size('trade')": "600000000|0|0\n",
    "FROM ingest_state WHERE key IN": (
        "service.stopped_reason|running\ndiscovery.last_full|1792800000\n"
    ),
}


def status_host(**canned: str) -> tuple[FakeHost, Answers]:
    host, config = installed(key=True)
    host.clusters["main"].canned = {**HEALTHY, **canned}
    return host, config


def status(host: FakeHost, config: Answers) -> dict[str, Check]:
    return by_name(run_ingest_status(host, config.installable_host()))


def test_a_healthy_ingest_reports_every_line_ok() -> None:
    host, config = status_host()
    checks = status(host, config)
    assert [c.status for c in checks.values()] == ["ok"] * len(checks)
    assert "since Sat 2026-10-24" in checks["service"].detail
    assert checks["rows in the last hour"].detail == "120,345 ticker, 8,123 trades"
    assert (
        "5.0x smaller" in checks["ticker size"].detail
        and "2,500 MB" in checks["ticker size"].detail
    )
    assert "ticker unrecoverable 1" in checks["gaps, last 24 hours"].detail


def test_a_service_that_is_not_running_is_a_failure() -> None:
    host, config = status_host()
    host.active.clear()
    assert status(host, config)["service"].status == "fail"


@pytest.mark.parametrize(("seconds", "expected"), [("5", "ok"), ("31", "warn"), ("181", "fail")])
def test_silence_from_the_feed_grows_from_ok_to_warning_to_failure(
    seconds: str, expected: str
) -> None:
    host, config = status_host(**{"FROM ingest_state WHERE key = 'feed.heartbeat_ms'": seconds})
    assert status(host, config)["last frame"].status == expected


def test_no_frame_ever_recorded_is_a_warning() -> None:
    host, config = status_host(**{"FROM ingest_state WHERE key = 'feed.heartbeat_ms'": ""})
    assert status(host, config)["last frame"].status == "warn"


def test_a_quiet_hour_is_a_warning() -> None:
    host, config = status_host(**{"FROM ticker WHERE ts": "0|0\n"})
    assert status(host, config)["rows in the last hour"].status == "warn"


def test_gaps_still_unfinished_are_a_warning_with_their_count() -> None:
    host, config = status_host(**{"WHERE status IN ('open', 'backfilling')": "2\n"})
    check = status(host, config)["gaps, last 24 hours"]
    assert check.status == "warn" and "2 unfinished" in check.detail


@pytest.mark.parametrize(("used", "expected"), [(40, "ok"), (85, "warn"), (90, "fail")])
def test_the_data_drive_warns_five_points_before_the_limit_and_fails_at_it(
    used: int, expected: str
) -> None:
    host, config = status_host()
    host.disk_percent = used
    check = status(host, config)["data drive"]
    assert check.status == expected and "stops at 90%" in check.detail


@pytest.mark.parametrize("reason", ["disk full: x is 91% full", "failed: RuntimeError: boom"])
def test_a_stop_for_a_bad_reason_is_a_failure_that_shows_the_reason(reason: str) -> None:
    host, config = status_host(
        **{"FROM ingest_state WHERE key IN": f"service.stopped_reason|{reason}\n"}
    )
    check = status(host, config)["last stop"]
    assert check.status == "fail" and reason in check.detail


def test_no_full_discovery_yet_is_a_warning() -> None:
    host, config = status_host(
        **{"FROM ingest_state WHERE key IN": "service.stopped_reason|running\n"}
    )
    assert status(host, config)["last full discovery"].status == "warn"


def test_another_account_gets_the_service_and_disk_but_not_the_database() -> None:
    host, config = status_host()
    host.root = False
    checks = status(host, config)
    assert checks["database"].status == "warn" and "service" in checks
    assert "last frame" not in checks


def test_a_database_that_cannot_be_asked_is_reported_not_raised() -> None:
    host, config = status_host()
    host.clusters["main"].status = "down"
    assert status(host, config)["database"].status == "fail"


def test_the_command_prints_the_report_and_exits_by_the_worst_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    answers_file = tmp_path / "install.toml"
    answers_file.write_text(BASE, encoding="utf-8")
    host, _ = status_host()
    assert main(["ingest-status", "--answers", str(answers_file)], system=host) == 0
    assert "OK    service" in capsys.readouterr().out
    host.active.clear()
    assert main(["ingest-status", "--answers", str(answers_file)], system=host) == 2


# --- the TPM library and sealing ------------------------------------------------------------------


def test_install_adds_the_library_systemd_needs_to_seal_with_the_tpm() -> None:
    host, config = FakeHost(), answers()
    host.tpm_missing_library = True  # systemd says 'partial' until it is installed
    outcomes = {o.step: o for o in run_install(host, config)}
    assert outcomes["tpm support"].changed and "sealing works" in outcomes["tpm support"].detail
    assert "apt-get install -y --no-install-recommends libtss2-rc0t64" in host.log


def test_the_library_is_installed_once_and_a_second_run_leaves_it_alone() -> None:
    host, config = installed()
    host.log.clear()
    outcomes = {o.step: o for o in run_install(host, config)}
    assert not outcomes["tpm support"].changed
    assert not [c for c in host.log if "libtss2" in c and c.startswith("apt-get")]


def test_a_chip_that_still_cannot_seal_is_reported_not_refused() -> None:
    host, config = FakeHost(), answers()
    host.tpm_unusable = True
    outcomes = {o.step: o for o in run_install(host, config)}  # must not raise
    assert "still reports the TPM as partial" in outcomes["tpm support"].detail
    check = verified(host, config)["ingest TPM sealing"]
    assert check.status == "fail" and "partial" in check.detail


def test_verify_passes_the_tpm_when_systemd_can_seal() -> None:
    host, config = installed(key=True)
    assert verified(host, config)["ingest TPM sealing"].status == "ok"
