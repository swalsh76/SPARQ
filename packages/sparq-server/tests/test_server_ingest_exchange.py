"""The exchange poller and the gap log against a real PostgreSQL."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from dbhelpers import dsn
from sparq_core.models import ExchangeSchedule, ExchangeStatus
from sparq_server.ingest import gaps
from sparq_server.ingest.exchange import ExchangePoller

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def status(
    *shards: tuple[int, bool, bool], active: bool = True, trading: bool = True
) -> ExchangeStatus:
    return ExchangeStatus.model_validate(
        {
            "exchange_active": active,
            "trading_active": trading,
            "exchange_index_statuses": [
                {
                    "exchange_index": i,
                    "description": f"shard {i}",
                    "exchange_active": a,
                    "trading_active": t,
                }
                for i, a, t in shards
            ],
        }
    )


def schedule(start: str = "2026-01-01T00:00:00Z") -> ExchangeSchedule:
    return ExchangeSchedule.model_validate(
        {
            "standard_hours": [
                {
                    "start_time": start,
                    "end_time": "2027-01-01T00:00:00Z",
                    "thursday": [{"open_time": "05:00", "close_time": "03:00"}],
                }
            ],
            "maintenance_windows": [],
        }
    )


class FakeExchange:
    def __init__(self) -> None:
        self.current = status((0, True, True), (1, True, True))
        self.sched = schedule()
        self.fail = False

    async def exchange_status(self) -> ExchangeStatus:
        if self.fail:
            raise RuntimeError("Kalshi is unavailable")
        return self.current

    async def exchange_schedule(self) -> ExchangeSchedule:
        if self.fail:
            raise RuntimeError("Kalshi is unavailable")
        return self.sched


def connector(url: str) -> Any:
    async def connect() -> asyncpg.Connection:
        return await asyncpg.connect(dsn(url))

    return connect


async def rows(url: str, statement: str, *args: Any) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn(url))
    try:
        return list(await conn.fetch(statement, *args))
    finally:
        await conn.close()


def poller(
    url: str, fake: FakeExchange, clock: list[datetime] | None = None, **kwargs: Any
) -> ExchangePoller:
    moment = clock if clock is not None else [NOW]
    exchange: Any = fake
    return ExchangePoller(exchange, connector(url), clock=lambda: moment[0], **kwargs)


# --- status ---------------------------------------------------------------------------------------


async def test_the_first_poll_stores_the_exchange_and_every_shard(migrated: str) -> None:
    stored = await poller(migrated, FakeExchange()).poll_status()
    assert stored == 3
    log = await rows(
        migrated,
        "SELECT exchange_index, exchange_active, trading_active, raw FROM exchange_status "
        "ORDER BY exchange_index NULLS FIRST",
    )
    assert [r["exchange_index"] for r in log] == [None, 0, 1]
    assert json.loads(log[1]["raw"])["description"] == "shard 0" and log[0]["raw"] is None


async def test_an_unchanged_status_stores_nothing(migrated: str) -> None:
    p = poller(migrated, FakeExchange())
    await p.poll_status()
    assert await p.poll_status() == 0
    assert (await rows(migrated, "SELECT count(*) FROM exchange_status"))[0][0] == 3


async def test_a_pause_on_one_shard_stores_only_that_shard_and_so_does_its_end(
    migrated: str,
) -> None:
    fake, clock = FakeExchange(), [NOW]
    p = poller(migrated, fake, clock)
    await p.poll_status()
    clock[0] = NOW + timedelta(hours=1)
    fake.current = status((0, True, False), (1, True, True))  # shard 0 stops trading
    assert await p.poll_status() == 1
    clock[0] = NOW + timedelta(hours=2)
    fake.current = status((0, True, True), (1, True, True))  # and starts again
    assert await p.poll_status() == 1
    history = await rows(
        migrated,
        "SELECT observed_at, trading_active FROM exchange_status "
        "WHERE exchange_index = 0 ORDER BY observed_at",
    )
    assert [(r["observed_at"], r["trading_active"]) for r in history] == [
        (NOW, True), (NOW + timedelta(hours=1), False), (NOW + timedelta(hours=2), True),
    ]  # fmt: skip


async def test_the_whole_exchange_going_inactive_is_recorded(migrated: str) -> None:
    fake = FakeExchange()
    p = poller(migrated, fake)
    await p.poll_status()
    fake.current = status((0, True, True), (1, True, True), active=False, trading=False)
    assert await p.poll_status() == 1
    latest = await rows(
        migrated,
        "SELECT exchange_active, trading_active FROM exchange_status "
        "WHERE exchange_index IS NULL ORDER BY id DESC LIMIT 1",
    )
    assert (latest[0][0], latest[0][1]) == (False, False)


# --- schedule -------------------------------------------------------------------------------------


async def test_the_schedule_is_stored_once_and_again_only_when_it_changes(migrated: str) -> None:
    fake, clock = FakeExchange(), [NOW]
    p = poller(migrated, fake, clock)
    assert await p.poll_schedule() is True
    clock[0] = NOW + timedelta(hours=1)
    assert await p.poll_schedule() is False
    clock[0] = NOW + timedelta(hours=2)
    fake.sched = schedule(start="2026-06-01T00:00:00Z")
    assert await p.poll_schedule() is True
    stored = await rows(migrated, "SELECT schedule FROM exchange_schedule ORDER BY fetched_at")
    assert len(stored) == 2 and json.loads(stored[0]["schedule"])["standard_hours"][0]["thursday"]


# --- the loop -------------------------------------------------------------------------------------


async def test_the_loop_survives_a_failed_poll_and_carries_on(migrated: str) -> None:
    fake = FakeExchange()
    fake.fail = True
    p = poller(migrated, fake, status_every=0.03)
    task = asyncio.create_task(p.run())
    for _ in range(100):
        if p.failures >= 1:
            break
        await asyncio.sleep(0.02)
    assert p.failures >= 1 and "unavailable" in (p.last_error or "")
    fake.fail = False
    for _ in range(200):
        if p.polls >= 1:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):  # a poller that swallowed this could not be stopped
        await asyncio.wait_for(task, 5)
    assert p.polls >= 1 and p.last_error is None and p.stats()["changes_stored"] >= 4


# --- the gap log ----------------------------------------------------------------------------------


async def test_the_gap_log_opens_marks_and_finishes_a_gap(migrated: str) -> None:
    conn = await asyncpg.connect(dsn(migrated))
    try:
        gap_id = await gaps.open_gap(
            conn, "trades", NOW, NOW + timedelta(minutes=1), "overflow", generation=3
        )
        gap = await gaps.get(conn, gap_id)
        assert gap is not None and (gap.kind, gap.status, gap.reason, gap.source) == (
            "trades",
            "open",
            "overflow",
            "ws",
        )
        await gaps.mark(conn, gap_id, gaps.BACKFILLING)
        await gaps.finish(conn, gap_id, gaps.DONE, found=10, added=7, duplicate=3, note="ok")
        await gaps.finish(conn, gap_id, gaps.DONE, note=None)  # a later call keeps what is there
        row = await conn.fetchrow("SELECT * FROM gap_log WHERE id = $1", gap_id)
        assert (
            row["status"],
            row["trades_found"],
            row["trades_added"],
            row["trades_duplicate"],
            row["note"],
        ) == ("done", 10, 7, 3, "ok")
        assert await gaps.get(conn, 999_999) is None
    finally:
        await conn.close()


async def test_unfinished_gaps_are_the_open_and_half_done_ones_oldest_first(migrated: str) -> None:
    conn = await asyncpg.connect(dsn(migrated))
    try:
        later = await gaps.open_gap(conn, "trades", NOW + timedelta(hours=1), None, "x")
        earlier = await gaps.open_gap(conn, "trades", NOW, None, "x")
        half = await gaps.open_gap(conn, "lifecycle", NOW + timedelta(minutes=30), None, "x")
        await gaps.mark(conn, half, gaps.BACKFILLING)
        await gaps.open_gap(conn, "trades", NOW, None, "x", status=gaps.DONE)
        await gaps.open_gap(conn, "ticker", NOW, None, "x", status=gaps.UNRECOVERABLE)
        await gaps.open_gap(conn, "trades", NOW, None, "x", status=gaps.FAILED)
        assert [g.id for g in await gaps.unfinished(conn)] == [earlier, half, later]
    finally:
        await conn.close()
