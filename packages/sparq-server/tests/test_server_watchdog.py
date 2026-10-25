"""The ingest watchdog: what counts as a problem, when it speaks, what it says, and that it never
leaks the topic (no database needed)."""

import logging
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sparq_server import watchdog
from sparq_server.watchdog import (
    EXIT_REFUSED,
    Message,
    Notifier,
    Observation,
    Problem,
    State,
    WatchdogConfig,
    WatchdogError,
    check,
    committed,
    evaluate,
    load_state,
    parse_config,
    plan,
    save_state,
)

TOPIC = "a-long-random-secret-topic-name"
GOOD = """
server = "https://ntfy.example.net"
socket_directory = "/var/run/postgresql"
port = 5432
database = "sparq"
role = "sparq_ingest"
data_directory = "/var/lib/postgresql"
"""
CONFIG = parse_config(GOOD)
HOUR = 3600.0


def obs(**changes: Any) -> Observation:
    base: dict[str, Any] = {
        "active": True, "state_text": "active", "restarts": 0, "db_ok": True,
        "heartbeat_age": 2.0, "stopped_reason": "running", "failed_gaps": 0, "stuck_gaps": 0,
        "disk_percent": 10.0,
    }  # fmt: skip
    return Observation(**{**base, **changes})


def names(problems: list[Problem]) -> list[str]:
    return [p.name for p in problems]


# --- configuration --------------------------------------------------------------------------------


def test_the_defaults_are_the_agreed_thresholds() -> None:
    assert (CONFIG.silence_seconds, CONFIG.disk_warn_percent) == (600, 80)
    assert (CONFIG.reminder_hours, CONFIG.confirm_checks, CONFIG.stuck_gap_minutes) == (6, 2, 60)


@pytest.mark.parametrize(
    "change",
    [
        ('server = "https://ntfy.example.net"', 'server = "ftp://ntfy.example.net"'),
        ('server = "https://ntfy.example.net"', 'server = "https://ntfy.example.net/topic"'),
        ("port = 5432", "port = 80"),
        ('role = "sparq_ingest"', 'role = "x; drop table trade"'),
        ("port = 5432", "port = 5432\nsilence_seconds = 5"),
        ("port = 5432", "port = 5432\nsurprise = 1"),
    ],
)
def test_a_configuration_that_is_wrong_or_misspelt_is_refused(change: tuple[str, str]) -> None:
    with pytest.raises(WatchdogError, match="not valid"):
        parse_config(GOOD.replace(*change))


# --- what counts as a problem -----------------------------------------------------------------


def test_a_healthy_ingest_shows_no_problem() -> None:
    assert evaluate(obs(), CONFIG, [0, 0, 0]) == []


def test_a_stopped_service_is_urgent_and_says_why_when_it_stopped_for_good() -> None:
    found = evaluate(
        obs(active=False, state_text="failed", stopped_reason="disk full: x is 91% full"),
        CONFIG,
        [],
    )
    assert names(found) == ["service"] and found[0].urgent
    assert "failed" in found[0].text and "disk full" in found[0].text


def test_a_stop_for_an_ordinary_reason_does_not_quote_it() -> None:
    found = evaluate(
        obs(active=False, state_text="inactive", stopped_reason="stopped cleanly"), CONFIG, []
    )
    assert "stopped cleanly" not in found[0].text


@pytest.mark.parametrize(("age", "expected"), [(599, []), (600, []), (601, ["silent"])])
def test_silence_is_judged_at_ten_minutes(age: float, expected: list[str]) -> None:
    assert names(evaluate(obs(heartbeat_age=age), CONFIG, [])) == expected


def test_silence_is_not_added_to_a_service_that_is_down() -> None:
    found = evaluate(obs(active=False, state_text="failed", heartbeat_age=9999), CONFIG, [])
    assert names(found) == ["service"]


def test_no_heartbeat_ever_recorded_is_not_silence() -> None:
    assert evaluate(obs(heartbeat_age=None), CONFIG, []) == []


@pytest.mark.parametrize(
    ("restarts", "expected"), [(2, []), (3, ["restarting"]), (9, ["restarting"])]
)
def test_restarting_over_and_over_is_seen_though_each_start_looks_fine(
    restarts: int, expected: list[str]
) -> None:
    assert names(evaluate(obs(restarts=restarts), CONFIG, [0, 0, 1])) == expected


def test_restarts_are_not_judged_without_history() -> None:
    assert evaluate(obs(restarts=50), CONFIG, []) == []


@pytest.mark.parametrize(("percent", "expected"), [(79.9, []), (80.0, ["disk"]), (95, ["disk"])])
def test_the_drive_warns_at_eighty_percent(percent: float, expected: list[str]) -> None:
    assert names(evaluate(obs(disk_percent=percent), CONFIG, [])) == expected


def test_an_unreadable_drive_is_not_a_problem_by_itself() -> None:
    assert evaluate(obs(disk_percent=None), CONFIG, []) == []


def test_an_unreachable_database_is_urgent() -> None:
    found = evaluate(obs(db_ok=False, heartbeat_age=None), CONFIG, [])
    assert names(found) == ["database"] and found[0].urgent


def test_failed_and_stuck_gaps_are_reported_with_their_counts() -> None:
    found = evaluate(obs(failed_gaps=2, stuck_gaps=1), CONFIG, [])
    assert names(found) == ["gap-failed", "gap-stuck"]
    assert (
        "2 trades gap" in found[0].text and "1 gap" in found[1].text and "60 min" in found[1].text
    )


# --- when it speaks ---------------------------------------------------------------------------


def due(state: State, problems: list[Problem], now: float) -> list[Message]:
    messages = plan(state, problems, now, CONFIG)
    for message in messages:
        committed(state, message, now)
    return messages


DOWN = [Problem("service", "the ingest is not running", urgent=True)]


def test_one_bad_check_is_not_an_alert_two_in_a_row_are() -> None:
    state = State()
    assert due(state, DOWN, 0) == []
    first = due(state, DOWN, 60)
    assert [(m.kind, m.name, m.urgent) for m in first] == [("problem", "service", True)]


def test_a_blip_that_clears_before_confirming_is_forgotten_without_a_word() -> None:
    state = State()
    due(state, DOWN, 0)
    assert due(state, [], 60) == [] and state.problems == {}
    due(state, DOWN, 120)
    assert due(state, DOWN, 180) != []  # the count started again from one


def test_a_problem_is_not_repeated_until_the_reminder_is_due() -> None:
    state = State()
    due(state, DOWN, 0)
    due(state, DOWN, 60)  # alerted
    assert due(state, DOWN, 60 + 5 * HOUR) == []
    reminder = due(state, DOWN, 60 + 6 * HOUR)
    assert [m.kind for m in reminder] == ["reminder"]
    assert due(state, DOWN, 61 + 6 * HOUR) == []  # and the clock restarts from the reminder


def test_recovery_is_announced_once_after_an_alert() -> None:
    state = State()
    due(state, DOWN, 0)
    due(state, DOWN, 60)
    gone = due(state, [], 120)
    assert [(m.kind, m.text) for m in gone] == [
        ("recovered", "recovered: the ingest is not running")
    ]
    assert due(state, [], 180) == [] and state.problems == {}


def test_a_problem_that_returns_after_recovery_is_a_new_alert() -> None:
    state = State()
    for now in (0, 60, 120):
        due(state, DOWN if now < 120 else [], now)
    due(state, DOWN, 180)
    assert [m.kind for m in due(state, DOWN, 240)] == ["problem"]


def test_a_message_that_could_not_be_delivered_is_due_again_at_the_next_check() -> None:
    state = State()
    plan(state, DOWN, 0, CONFIG)
    first = plan(state, DOWN, 60, CONFIG)  # due, but never committed: the send failed
    assert [m.kind for m in first] == ["problem"]
    assert [m.kind for m in plan(state, DOWN, 120, CONFIG)] == ["problem"]


def test_a_recovery_that_could_not_be_delivered_is_due_again() -> None:
    state = State()
    due(state, DOWN, 0)
    due(state, DOWN, 60)
    assert [m.kind for m in plan(state, [], 120, CONFIG)] == ["recovered"]  # not committed
    assert [m.kind for m in plan(state, [], 180, CONFIG)] == ["recovered"]


def test_problems_are_tracked_separately() -> None:
    state = State()
    other = Problem("disk", "the data drive is 85% full")
    due(state, [*DOWN, other], 0)
    sent = due(state, [*DOWN, other], 60)
    assert sorted(m.name for m in sent) == ["disk", "service"]
    gone = due(state, DOWN, 120)
    assert [(m.kind, m.name) for m in gone] == [("recovered", "disk")]


# --- the state file ---------------------------------------------------------------------------


def test_the_state_survives_a_restart_of_the_watchdog(tmp_path: Path) -> None:
    state = State()
    due(state, DOWN, 0)
    due(state, DOWN, 60)
    state.restarts = [1, 2, 3]
    path = tmp_path / "state.json"
    save_state(path, state)
    again = load_state(path)
    assert again == state
    assert due(again, DOWN, 120) == []  # it does not tell the owner a second time


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX")
def test_the_state_file_is_private_and_replaced_whole(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(path, State())
    save_state(path, State(restarts=[1]))
    assert path.stat().st_mode & 0o777 == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]  # no half-written leftovers


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        "{}",
        '{"problems": 3, "restarts": []}',
        '{"problems": {"a": {"nope": 1}}, "restarts": []}',
    ],
)
def test_a_damaged_state_file_gives_a_fresh_state_not_an_error(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "state.json"
    path.write_text(content, encoding="utf-8")
    assert load_state(path) == State()
    assert load_state(tmp_path / "absent.json") == State()


# --- delivery ---------------------------------------------------------------------------------


def notifier_with(handler: Any) -> tuple[Notifier, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Notifier("https://ntfy.example.net", TOPIC, client), client


async def test_a_message_is_posted_to_the_topic_with_a_title_and_a_priority() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    notifier, client = notifier_with(handler)
    await notifier.send(Message("problem", "service", "the ingest is not running", urgent=True))
    await notifier.send(Message("recovered", "service", "recovered: the ingest is not running"))
    await client.aclose()
    first, second = seen
    assert str(first.url) == f"https://ntfy.example.net/{TOPIC}" and first.method == "POST"
    assert first.content == b"the ingest is not running"
    assert first.headers["Title"] == "SPARQ ingest: problem" and first.headers["Priority"] == "high"
    assert (
        second.headers["Title"] == "SPARQ ingest: recovered"
        and second.headers["Priority"] == "default"
    )


async def test_a_refusal_by_the_server_is_an_error_without_the_topic() -> None:
    notifier, client = notifier_with(lambda request: httpx.Response(403))
    with pytest.raises(WatchdogError) as caught:
        await notifier.send(Message("problem", "service", "down"))
    await client.aclose()
    assert "403" in str(caught.value) and TOPIC not in str(caught.value)
    assert caught.value.__cause__ is None


async def test_an_unreachable_server_is_an_error_without_the_topic() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    notifier, client = notifier_with(handler)
    with pytest.raises(WatchdogError) as caught:
        await notifier.send(Message("problem", "service", "down"))
    await client.aclose()
    assert "ConnectError" in str(caught.value) and TOPIC not in str(caught.value)
    assert caught.value.__cause__ is None


# --- a whole check ----------------------------------------------------------------------------


class Machine:
    """A stand-in for the host: systemd's answers, a drive, and a database that may be away."""

    def __init__(self) -> None:
        self.active_state = "active"
        self.restarts = 0
        self.percent = 10.0
        self.sent: list[Message] = []
        self.refuse = False

    async def run(self, argv: list[str]) -> str:
        return f"ActiveState={self.active_state}\nNRestarts={self.restarts}\n"

    def usage(self, path: str) -> SimpleNamespace:
        return SimpleNamespace(used=int(self.percent * 10), total=1000)

    async def connect(self) -> Any:
        raise OSError("the database is away")


async def one_check(
    machine: Machine, path: Path, now: float, caplog: pytest.LogCaptureFixture | None = None
) -> int:
    def handler(request: httpx.Request) -> httpx.Response:
        if machine.refuse:
            return httpx.Response(500)
        machine.sent.append(Message("seen", "", request.content.decode()))
        return httpx.Response(200)

    notifier, client = notifier_with(handler)
    try:
        return await check(
            CONFIG, path, notifier, connect=machine.connect, run=machine.run,
            usage=machine.usage, clock=lambda: now,
        )  # fmt: skip
    finally:
        await client.aclose()


async def test_a_database_that_stays_away_is_reported_once_confirmed(tmp_path: Path) -> None:
    machine, path = Machine(), tmp_path / "state.json"
    assert await one_check(machine, path, 0) == 0 and machine.sent == []
    assert await one_check(machine, path, 60) == 0
    assert [m.text for m in machine.sent] == ["PostgreSQL cannot be reached"]
    await one_check(machine, path, 120)
    assert len(machine.sent) == 1  # not again every minute


async def test_a_service_that_stops_and_comes_back_gives_an_alert_and_a_recovery(
    tmp_path: Path,
) -> None:
    machine, path = Machine(), tmp_path / "state.json"
    machine.active_state = "failed"
    for now in (0, 60, 120):
        await one_check(machine, path, now)
    machine.active_state = "active"
    await one_check(machine, path, 180)
    texts = [m.text for m in machine.sent]
    assert texts[0].startswith("PostgreSQL") or "not running" in texts[0]
    assert any("not running (failed)" in t for t in texts)
    assert texts[-1].startswith("recovered:")


async def test_an_undelivered_alert_makes_the_check_fail_and_is_retried(tmp_path: Path) -> None:
    machine, path = Machine(), tmp_path / "state.json"
    machine.active_state = "failed"
    machine.refuse = True
    await one_check(machine, path, 0)
    assert await one_check(machine, path, 60) == 1  # due, refused
    machine.refuse = False
    assert await one_check(machine, path, 120) == 0
    assert any("not running" in m.text for m in machine.sent)


async def test_a_service_restarting_in_a_loop_is_caught_across_checks(tmp_path: Path) -> None:
    machine, path = Machine(), tmp_path / "state.json"
    for step in range(5):
        machine.restarts = step * 2  # active every time, but 2 more restarts each minute
        await one_check(machine, path, step * 60)
    assert any("restarted" in m.text for m in machine.sent)


async def test_the_drive_filling_up_is_reported(tmp_path: Path) -> None:
    machine, path = Machine(), tmp_path / "state.json"
    machine.percent = 83
    await one_check(machine, path, 0)
    await one_check(machine, path, 60)
    assert any("83% full" in m.text for m in machine.sent)


async def test_nothing_the_watchdog_logs_contains_the_topic(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    machine, path = Machine(), tmp_path / "state.json"
    machine.active_state = "failed"
    machine.refuse = True
    with caplog.at_level(logging.DEBUG):
        for now in (0, 60, 120):
            await one_check(machine, path, now)
    assert TOPIC not in caplog.text and "could not deliver" in caplog.text


# --- starting up ------------------------------------------------------------------------------


def test_without_its_unit_the_watchdog_will_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "alerts.toml"
    config.write_text(GOOD, encoding="utf-8")
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.delenv("STATE_DIRECTORY", raising=False)
    assert watchdog.main([str(config)]) == EXIT_REFUSED


def test_a_missing_or_empty_topic_is_refused_without_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    config = tmp_path / "alerts.toml"
    config.write_text(GOOD, encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("STATE_DIRECTORY", str(tmp_path))
    assert watchdog.main([str(config)]) == EXIT_REFUSED
    (tmp_path / "ntfy-topic").write_text("  \n", encoding="utf-8")
    assert watchdog.main([str(config)]) == EXIT_REFUSED
    assert str(tmp_path) not in caplog.text


def test_the_test_flag_sends_one_test_message_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "alerts.toml"
    config.write_text(GOOD, encoding="utf-8")
    (tmp_path / "ntfy-topic").write_text(TOPIC + "\n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("STATE_DIRECTORY", str(tmp_path))
    seen: list[httpx.Request] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    assert watchdog.main([str(config), "--test"]) == 0
    assert len(seen) == 1 and str(seen[0].url).endswith("/" + TOPIC)
    assert seen[0].headers["Title"] == "SPARQ ingest: test message"
    assert not (tmp_path / "state.json").exists()  # a test touches no state


def test_the_config_type_is_the_one_install_renders() -> None:
    assert isinstance(CONFIG, WatchdogConfig)
