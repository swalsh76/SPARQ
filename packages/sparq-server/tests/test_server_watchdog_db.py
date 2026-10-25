"""What the watchdog reads from the real tables: the heartbeat, why the service last stopped, and
the gaps that are failed or stuck."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
from dbhelpers import dsn
from sparq_server import watchdog
from sparq_server.ingest import gaps, state
from sparq_server.watchdog import observe, parse_config

pytestmark = pytest.mark.db

CONFIG = parse_config(
    'server = "https://ntfy.example.net"\nsocket_directory = "/unused"\nport = 5432\n'
    'database = "sparq"\nrole = "sparq_ingest"\ndata_directory = "/"\n'
)


async def systemd(argv: list[str]) -> str:
    return "ActiveState=active\nNRestarts=4\n"


def drive(path: str) -> SimpleNamespace:
    return SimpleNamespace(used=250, total=1000)


def connector(url: str) -> Any:
    async def connect() -> asyncpg.Connection:
        return await asyncpg.connect(dsn(url))

    return connect


async def seen(url: str) -> Any:
    return await observe(CONFIG, connector(url), systemd, drive)


async def put(url: str, action: Any) -> None:
    conn = await asyncpg.connect(dsn(url))
    try:
        await action(conn)
    finally:
        await conn.close()


NOW = datetime.now(UTC)


async def test_an_empty_database_shows_no_heartbeat_and_no_gaps(migrated: str) -> None:
    look = await seen(migrated)
    assert (look.db_ok, look.heartbeat_age, look.stopped_reason) == (True, None, None)
    assert (look.failed_gaps, look.stuck_gaps) == (0, 0)


async def test_the_systemd_and_drive_readings_come_through(migrated: str) -> None:
    look = await seen(migrated)
    assert (look.active, look.state_text, look.restarts, look.disk_percent) == (
        True,
        "active",
        4,
        25.0,
    )


async def test_the_heartbeat_age_is_how_long_ago_the_last_frame_arrived(migrated: str) -> None:
    async def act(conn: asyncpg.Connection) -> None:
        await conn.execute(
            "INSERT INTO ingest_state (key, value) VALUES ($1, "
            "(extract(epoch FROM now()) * 1000 - 125000)::bigint::text)",
            state.HEARTBEAT,
        )
        await state.put(conn, "service.stopped_reason", "disk full: the drive is 91% full")

    await put(migrated, act)
    look = await seen(migrated)
    assert look.heartbeat_age is not None and 122 < look.heartbeat_age < 135
    assert look.stopped_reason == "disk full: the drive is 91% full"


async def test_only_recent_failed_trades_gaps_count_as_failed(migrated: str) -> None:
    async def act(conn: asyncpg.Connection) -> None:
        end = NOW - timedelta(minutes=1)
        for kind, status, ago in (
            ("trades", gaps.FAILED, timedelta(minutes=30)),  # counts
            ("trades", gaps.FAILED, timedelta(hours=5)),  # counts
            ("trades", gaps.FAILED, timedelta(hours=7)),  # too old
            ("ticker", gaps.FAILED, timedelta(minutes=30)),  # not a trades gap
            ("trades", gaps.DONE, timedelta(minutes=30)),  # fine
        ):
            await gaps.open_gap(conn, kind, NOW - ago, end, "test", status=status)

    await put(migrated, act)
    assert (await seen(migrated)).failed_gaps == 2


async def test_a_gap_is_stuck_when_unfinished_for_long_and_not_a_lifecycle_gap(
    migrated: str,
) -> None:
    async def act(conn: asyncpg.Connection) -> None:
        old, young = NOW - timedelta(minutes=90), NOW - timedelta(minutes=10)
        await gaps.open_gap(conn, "trades", old, None, "t", status=gaps.OPEN)  # stuck
        await gaps.open_gap(conn, "trades", old, None, "t", status=gaps.BACKFILLING)  # stuck
        await gaps.open_gap(conn, "trades", young, None, "t", status=gaps.OPEN)  # young
        await gaps.open_gap(conn, "lifecycle", old, None, "t", status=gaps.OPEN)  # may wait
        await gaps.open_gap(conn, "trades", old, None, "t", status=gaps.DONE)  # finished
        await gaps.open_gap(conn, "ticker", old, None, "t", status=gaps.UNRECOVERABLE)  # final

    await put(migrated, act)
    assert (await seen(migrated)).stuck_gaps == 2


async def test_a_database_that_refuses_is_reported_not_raised(migrated: str) -> None:
    async def refuses() -> asyncpg.Connection:
        raise asyncpg.CannotConnectNowError("the database system is shutting down")

    look = await observe(CONFIG, refuses, systemd, drive)
    assert not look.db_ok and look.heartbeat_age is None and look.active


async def test_a_database_that_never_answers_is_reported_after_the_probe_timeout(
    migrated: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hangs() -> asyncpg.Connection:
        await asyncio.sleep(60)
        raise AssertionError("unreachable")

    monkeypatch.setattr(watchdog, "PROBE_TIMEOUT", 0.05)
    assert not (await observe(CONFIG, hangs, systemd, drive)).db_ok
