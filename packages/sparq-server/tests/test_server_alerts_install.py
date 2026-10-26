"""The watchdog's answers, rendered files, ``install``, ``verify`` and ``alert-test``."""

from pathlib import Path

import pytest
from fakehost import FakeHost
from sparq_server import names
from sparq_server.admin import managed
from sparq_server.admin.alerts import AlertTestRefused, run_alert_test
from sparq_server.admin.answers import Answers, AnswersError, parse_answers
from sparq_server.admin.cli import main
from sparq_server.admin.install import plan_install, render_plan, run_install
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
[database_host.alerts]
enabled = true
"""
SERVICE = f"{managed.SYSTEMD_DIRECTORY}/{managed.WATCHDOG_SERVICE}"
TIMER = f"{managed.SYSTEMD_DIRECTORY}/{managed.WATCHDOG_TIMER}"
TEST = f"{managed.SYSTEMD_DIRECTORY}/{managed.ALERT_TEST_SERVICE}"
TOPIC_FILE = managed.credential_file(names.ALERT_CREDENTIAL)


def answers(extra: str = "") -> Answers:
    return parse_answers(BASE + extra)


def installed(topic: bool = False) -> tuple[FakeHost, Answers]:
    host, config = FakeHost(), answers()
    if topic:
        host.files[TOPIC_FILE] = "encrypted"
    run_install(host, config)
    return host, config


def verified(host: FakeHost, config: Answers) -> dict[str, Check]:
    return {c.name: c for c in run_verify(host, config.installable_host())}


# --- the answers ------------------------------------------------------------------------------


def test_alerts_are_off_unless_the_answers_turn_them_on() -> None:
    plain = BASE.split("[database_host.alerts]")[0]
    assert parse_answers(plain).installable_host().alerts.enabled is False


def test_the_defaults_are_the_agreed_ones() -> None:
    alerts = answers().installable_host().alerts
    assert (alerts.server, alerts.silence_seconds, alerts.disk_warn_percent) == (
        "https://ntfy.sh",
        600,
        80,
    )


def test_alerts_watch_the_ingest_so_they_need_it_enabled() -> None:
    alerts_only = (
        BASE.split("[database_host.ingest]")[0] + "[database_host.alerts]\nenabled = true\n"
    )
    with pytest.raises(AnswersError, match="ingest, which is not enabled"):
        parse_answers(alerts_only)


@pytest.mark.parametrize(
    "line", ["silence_seconds = 30", "disk_warn_percent = 40", 'server = "ntfy.sh"', "topic = 'x'"]
)
def test_nonsense_alert_settings_are_refused(line: str) -> None:
    with pytest.raises(AnswersError):
        answers(line + "\n")


# --- the files install renders ----------------------------------------------------------------


def test_the_settings_name_the_read_only_role_the_server_and_the_thresholds() -> None:
    text = managed.render_alerts_config(answers("silence_seconds = 900\n").installable_host())
    assert 'server = "https://ntfy.sh"' in text and 'role = "sparq_ingest"' in text
    assert "silence_seconds = 900" in text and "disk_warn_percent = 80" in text
    assert f'data_directory = "{managed.DATA_ROOT}"' in text
    assert "topic" not in text.lower()  # the topic is a credential, never a setting


def test_the_watchdog_reads_the_ingests_own_stop_level() -> None:
    from sparq_server.watchdog import Observation, evaluate
    from sparq_server.watchdog import parse_config as watchdog_config

    host = answers().installable_host()
    host = host.model_copy(
        update={"ingest": host.ingest.model_copy(update={"disk_stop_percent": 85})}
    )
    config = watchdog_config(managed.render_alerts_config(host))
    assert config.disk_stop_percent == 85
    look = Observation(
        active=True, state_text="active", restarts=0, db_ok=True, heartbeat_age=1.0,
        stopped_reason="running", failed_gaps=0, stuck_gaps=0, disk_percent=82.0,
    )  # fmt: skip
    (problem,) = evaluate(look, config, [])
    assert problem.name == "disk" and "stops at 85%" in problem.text


def test_the_watchdog_runs_every_minute_as_the_service_account_with_a_credential() -> None:
    units = managed.watchdog_units()
    service, timer = units[SERVICE], units[TIMER]
    assert "Type=oneshot" in service and "User=sparq" in service
    assert f"LoadCredentialEncrypted=ntfy-topic:{TOPIC_FILE}" in service
    assert "StateDirectory=sparq-watchdog" in service and "sparq_server.watchdog" in service
    assert "OnUnitActiveSec=60s" in timer and "OnBootSec=3min" in timer
    assert f"Unit={managed.WATCHDOG_SERVICE}" in timer and "WantedBy=timers.target" in timer
    for hardening in ("NoNewPrivileges=yes", "ProtectSystem=strict", "PrivateTmp=yes"):
        assert hardening in service


def test_only_the_test_unit_carries_the_test_flag_and_none_is_started_by_a_target() -> None:
    units = managed.watchdog_units()
    assert units[TEST].count("--test") == 1 and "--test" not in units[SERVICE]
    assert "[Install]" not in units[TEST] and "[Install]" not in units[SERVICE]
    assert units[TEST].replace(" --test", "").splitlines()[3:] == units[SERVICE].splitlines()[3:]


# --- install ----------------------------------------------------------------------------------


def test_the_plan_names_the_alerts() -> None:
    text = render_plan(plan_install(answers()))
    assert "alerts" in text and "ntfy.sh" in text and "10 minutes" in text


def test_without_the_topic_the_files_are_written_and_the_timer_is_left_off() -> None:
    host, config = FakeHost(), answers()
    outcomes = {o.step: o for o in run_install(host, config)}
    assert "not started" in outcomes["watchdog"].detail and outcomes["watchdog"].changed
    for path in (names.ALERTS_CONFIG, SERVICE, TIMER, TEST):
        assert path in host.files
    assert managed.WATCHDOG_TIMER not in host.enabled and managed.WATCHDOG_TIMER not in host.active


def test_with_the_topic_the_timer_is_enabled_and_started() -> None:
    host, config = FakeHost(), answers()
    host.files[TOPIC_FILE] = "encrypted"
    outcomes = {o.step: o for o in run_install(host, config)}
    assert outcomes["watchdog"].detail.startswith("timer started")
    assert managed.WATCHDOG_TIMER in host.enabled and managed.WATCHDOG_TIMER in host.active


def test_a_topic_stored_later_starts_the_timer_on_the_next_install() -> None:
    host, config = installed()
    host.files[TOPIC_FILE] = "encrypted"
    outcomes = {o.step: o for o in run_install(host, config)}
    assert outcomes["watchdog"].detail.startswith("timer started")


def test_a_second_run_changes_nothing_and_runs_no_changing_command() -> None:
    host, config = installed(topic=True)
    host.log.clear()
    outcomes = run_install(host, config)
    assert [o.step for o in outcomes if o.changed] == []
    assert [c for c in host.mutating() if "showhold" not in c] == []
    assert len(outcomes) == 13  # the 12 with the ingest, and the watchdog


def test_a_hand_edited_unit_is_put_back_and_systemd_reloaded() -> None:
    host, config = installed(topic=True)
    host.files[SERVICE] = "# edited by hand"
    host.log.clear()
    outcomes = {o.step: o for o in run_install(host, config)}
    assert outcomes["watchdog"].changed and host.files[SERVICE] == managed.watchdog_units()[SERVICE]
    assert "systemctl daemon-reload" in host.log


def test_a_timer_that_stopped_is_started_again() -> None:
    host, config = installed(topic=True)
    host.active.discard(managed.WATCHDOG_TIMER)
    outcomes = {o.step: o for o in run_install(host, config)}
    assert outcomes["watchdog"].changed and managed.WATCHDOG_TIMER in host.active


def test_with_alerts_off_nothing_is_written() -> None:
    host = FakeHost()
    run_install(host, parse_answers(BASE.split("[database_host.alerts]")[0]))
    assert names.ALERTS_CONFIG not in host.files and SERVICE not in host.files


# --- verify -----------------------------------------------------------------------------------


def test_a_fresh_install_without_the_topic_verifies_with_a_warning_and_no_failure() -> None:
    host, config = installed()
    checks = verified(host, config)
    assert checks["alert topic"].status == "warn"
    assert not [c for c in checks.values() if c.status == "fail"]


def test_with_the_topic_and_the_timer_running_every_alert_check_passes() -> None:
    host, config = installed(topic=True)
    checks = verified(host, config)
    for name in ("alert settings", "alert topic", "alert timer"):
        assert checks[name].status == "ok", name
    assert not [c for c in checks.values() if c.status == "fail"]


def test_a_stored_topic_with_the_timer_down_is_a_failure() -> None:
    host, config = installed(topic=True)
    host.active.discard(managed.WATCHDOG_TIMER)
    assert verified(host, config)["alert timer"].status == "fail"


@pytest.mark.parametrize("path", [names.ALERTS_CONFIG, SERVICE, TIMER, TEST])
def test_a_hand_edited_alert_file_is_a_failure(path: str) -> None:
    host, config = installed(topic=True)
    host.files[path] += "# edited"
    failed = [c for c in verified(host, config).values() if c.status == "fail"]
    assert len(failed) == 1 and path in failed[0].detail


def test_a_last_run_that_failed_is_a_failure_that_says_how() -> None:
    host, config = installed(topic=True)
    host.unit_result = "exit-code"
    check = verified(host, config)["alert last run"]
    assert check.status == "fail" and "exit-code" in check.detail


def test_with_alerts_off_verify_says_nothing_about_them() -> None:
    plain = parse_answers(BASE.split("[database_host.alerts]")[0])
    host = FakeHost()
    run_install(host, plain)
    assert not [n for n in verified(host, plain) if n.startswith("alert")]


# --- alert-test -------------------------------------------------------------------------------


def test_the_test_message_starts_the_test_unit_and_says_where_to_look() -> None:
    host, config = installed(topic=True)
    host.log.clear()
    line = run_alert_test(host, config.installable_host())
    assert f"systemctl start {managed.ALERT_TEST_SERVICE}" in host.log
    assert "check your phone" in line and "ntfy.sh" in line


@pytest.mark.parametrize(
    ("prepare", "reason"),
    [
        (lambda h: setattr(h, "root", False), "must run as root"),
        (lambda h: h.files.pop(TOPIC_FILE), "topic is not stored"),
        (lambda h: h.files.pop(TEST), "test unit is not installed"),
    ],
)
def test_the_test_refuses_with_a_reason(prepare: object, reason: str) -> None:
    host, config = installed(topic=True)
    prepare(host)  # type: ignore[operator]
    with pytest.raises(AlertTestRefused, match=reason):
        run_alert_test(host, config.installable_host())


def test_the_test_refuses_when_alerts_are_off() -> None:
    plain = parse_answers(BASE.split("[database_host.alerts]")[0])
    with pytest.raises(AlertTestRefused, match="not enabled"):
        run_alert_test(FakeHost(), plain.installable_host())


def test_a_test_message_that_fails_points_at_the_journal() -> None:
    host, config = installed(topic=True)
    host.fail_on = "systemctl start sparq-alert-test"
    with pytest.raises(AlertTestRefused, match="journalctl -u sparq-alert-test"):
        run_alert_test(host, config.installable_host())


def test_the_command_prints_the_result_and_exits_by_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    answers_file = tmp_path / "install.toml"
    answers_file.write_text(BASE, encoding="utf-8")
    host, _ = installed(topic=True)
    assert main(["alert-test", "--answers", str(answers_file)], system=host) == 0
    assert "check your phone" in capsys.readouterr().out
    host.files.pop(TOPIC_FILE)
    assert main(["alert-test", "--answers", str(answers_file)], system=host) == 2
    assert "REFUSING" in capsys.readouterr().err
