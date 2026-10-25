"""The event pump: what the feed reports becomes gap records and recovery work."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from dbhelpers import dsn
from ingestfixtures import TRADE, data
from sparq_core.ws import Data, FeedEvent, Reconnected, ResubscribeFailed, SequenceGap
from sparq_server.ingest import gaps, state
from sparq_server.ingest.pump import EventPump

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)


def at(seconds: float) -> int:
    """A receive time, in nanoseconds, ``seconds`` before NOW."""
    return int((NOW - timedelta(seconds=seconds)).timestamp() * 1e9)


def frame(sid: int, seconds_ago: float, kind: str = "trade") -> Data:
    return data(
        kind,
        {**TRADE, "ts_ms": 5 if kind == "trade" else None},
        received_ns=at(seconds_ago),
        sid=sid,
    )


class FakeFeed:
    def __init__(self, events: list[FeedEvent], channels: dict[int, str] | None = None) -> None:
        self._events = events
        self.channels = channels or {1: "ticker", 2: "trade", 3: "market_lifecycle_v2"}

    async def events(self) -> AsyncIterator[FeedEvent]:
        for event in self._events:
            yield event

    def channel_of(self, sid: int) -> str | None:
        return self.channels.get(sid)


class FakeWriter:
    def __init__(self) -> None:
        self.added: list[Data] = []

    async def add(self, item: Data) -> None:
        self.added.append(item)


class FakeBackfiller:
    def __init__(self) -> None:
        self.submitted: list[int] = []
        self.requeued = 0

    def submit(self, gap_id: int) -> None:
        self.submitted.append(gap_id)

    async def requeue_unfinished(self) -> int:
        self.requeued += 1
        return 0


class FakeDiscovery:
    def __init__(self, outcome: str = "success") -> None:
        self.requests: list[asyncio.Future[None]] = []
        self.outcome = outcome

    def request(self) -> asyncio.Future[None]:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.requests.append(future)
        if self.outcome == "success":
            future.set_result(None)
        return future


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


def pump_for(
    url: str, events: list[FeedEvent], **kwargs: Any
) -> tuple[EventPump, FakeWriter, FakeBackfiller, FakeDiscovery]:
    writer, backfiller = FakeWriter(), FakeBackfiller()
    discovery = FakeDiscovery(kwargs.pop("outcome", "success"))

    async def no_wait(_: float) -> None:
        return None

    feed: Any = FakeFeed(events, kwargs.pop("channels", None))
    pump = EventPump(
        feed, writer, connector(url), backfiller, discovery,  # type: ignore[arg-type]
        clock=lambda: NOW, sleep=no_wait,
        heartbeat_every=kwargs.pop("heartbeat_every", 10.0), **kwargs,
    )  # fmt: skip
    return pump, writer, backfiller, discovery


async def drive(pump: EventPump, events: list[FeedEvent]) -> None:
    """Feed the events to the pump and let any recovery task it started finish (a shutdown would
    cancel them, which is right for a shutdown but is not what these tests are about)."""
    for event in events:
        await pump.handle(event)
    await settle()


async def settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0.02)


# --- frames ----------------------------------------------------------------


async def test_frames_go_to_the_writer_in_order_and_the_heartbeat_follows_the_last_one(
    migrated: str,
) -> None:
    events: list[FeedEvent] = [frame(2, 30), frame(2, 20), frame(2, 10)]
    pump, writer, _, _ = pump_for(migrated, events, heartbeat_every=0.0)
    await pump.run()
    assert writer.added == events and pump.frames == 3
    conn = await asyncpg.connect(dsn(migrated))
    try:
        beat = await state.get_int(conn, state.HEARTBEAT)
    finally:
        await conn.close()
    assert beat == int((NOW - timedelta(seconds=10)).timestamp() * 1000)  # saved again on close


async def test_the_heartbeat_is_not_written_for_every_frame(migrated: str) -> None:
    pump, _, _, _ = pump_for(migrated, [frame(2, 30), frame(2, 20)], heartbeat_every=3600.0)
    await pump.handle(frame(2, 30))  # the first is due (nothing written yet)
    await pump.handle(frame(2, 20))
    beat = (await rows(migrated, "SELECT value FROM ingest_state WHERE key = $1", state.HEARTBEAT))[
        0
    ][0]
    assert int(beat) == int((NOW - timedelta(seconds=30)).timestamp() * 1000)  # not the second one
    await pump.close()


# --- a lost link -----------------------------------------------------------


async def test_a_reconnect_records_three_gaps_from_the_last_frame_to_now(migrated: str) -> None:
    events: list[FeedEvent] = [frame(2, 90), frame(1, 60), Reconnected(2, "stalled", 61.0)]
    pump, _, backfiller, discovery = pump_for(migrated, events)
    await drive(pump, events)
    log = {r["kind"]: r for r in await rows(migrated, "SELECT * FROM gap_log")}
    assert set(log) == {"trades", "ticker", "lifecycle"}
    for row in log.values():
        assert row["started_at"] == NOW - timedelta(
            seconds=60
        )  # the last frame, not "when noticed"
        assert row["ended_at"] == NOW and row["reason"] == "stalled" and row["generation"] == 2
    assert log["trades"]["status"] == "open" and backfiller.submitted == [log["trades"]["id"]]
    assert (
        log["ticker"]["status"] == "unrecoverable" and "cannot be replayed" in log["ticker"]["note"]
    )
    assert log["lifecycle"]["status"] == "done" and "discovery" in log["lifecycle"]["note"]
    assert len(discovery.requests) == 1


async def test_a_lifecycle_gap_stays_open_until_a_fresh_discovery_cycle_succeeds(
    migrated: str,
) -> None:
    events: list[FeedEvent] = [frame(3, 5), Reconnected(2, "connection_lost", 1.0)]
    pump, _, _, discovery = pump_for(migrated, events, outcome="hold")
    await drive(pump, events)
    assert (await rows(migrated, "SELECT status FROM gap_log WHERE kind = 'lifecycle'"))[0][
        0
    ] == "open"
    discovery.requests[0].set_result(None)
    await settle()
    assert (await rows(migrated, "SELECT status FROM gap_log WHERE kind = 'lifecycle'"))[0][
        0
    ] == "done"


async def test_a_lifecycle_gap_that_discovery_never_closes_is_failed_not_left_open(
    migrated: str,
) -> None:
    events: list[FeedEvent] = [Reconnected(2, "overflow", 1.0)]
    pump, _, _, _ = pump_for(migrated, events, outcome="hold", lifecycle_wait=0.05)
    await drive(pump, events)
    await asyncio.sleep(0.3)
    row = (await rows(migrated, "SELECT status, note FROM gap_log WHERE kind = 'lifecycle'"))[0]
    assert row["status"] == "failed" and "no discovery cycle" in row["note"]


async def test_the_window_before_any_frame_has_arrived_starts_now(migrated: str) -> None:
    pump, _, _, _ = pump_for(migrated, [Reconnected(2, "connection_lost", 1.0)])
    await pump.run()
    await settle()
    row = (await rows(migrated, "SELECT started_at, ended_at FROM gap_log WHERE kind = 'trades'"))[
        0
    ]
    assert row["started_at"] == NOW == row["ended_at"]


# --- a jump in the sequence ------------------------------------------------


async def test_a_jump_on_the_trade_channel_is_a_trades_gap_from_that_subscriptions_last_frame(
    migrated: str,
) -> None:
    events: list[FeedEvent] = [frame(2, 40), frame(1, 5), SequenceGap(2, 10, 14, 1)]
    pump, _, backfiller, discovery = pump_for(migrated, events)
    await pump.run()
    log = await rows(migrated, "SELECT * FROM gap_log")
    assert [r["kind"] for r in log] == ["trades"] and backfiller.submitted == [log[0]["id"]]
    assert log[0]["started_at"] == NOW - timedelta(seconds=40)  # sid 2's last frame, not sid 1's
    assert "expected 10, got 14" in log[0]["reason"] and not discovery.requests


async def test_a_jump_on_the_lifecycle_channel_asks_discovery_and_makes_no_trades_gap(
    migrated: str,
) -> None:
    pump, _, backfiller, discovery = pump_for(migrated, [frame(3, 20), SequenceGap(3, 4, 6, 1)])
    await pump.run()
    await settle()
    assert [r["kind"] for r in await rows(migrated, "SELECT kind FROM gap_log")] == ["lifecycle"]
    assert len(discovery.requests) == 1 and not backfiller.submitted


async def test_a_jump_on_an_unknown_subscription_is_logged_and_ignored(migrated: str) -> None:
    pump, _, _, discovery = pump_for(migrated, [SequenceGap(99, 1, 3, 1)])
    await pump.run()
    assert await rows(migrated, "SELECT * FROM gap_log") == [] and not discovery.requests


# --- starting up -----------------------------------------------------------


async def test_a_restart_records_the_gap_since_the_heartbeat_and_resumes_unfinished_gaps(
    migrated: str,
) -> None:
    conn = await asyncpg.connect(dsn(migrated))
    try:
        await state.put(conn, state.HEARTBEAT, int((NOW - timedelta(minutes=7)).timestamp() * 1000))
    finally:
        await conn.close()
    pump, _, backfiller, _ = pump_for(migrated, [])
    await pump.run()
    await settle()
    log = {r["kind"]: r for r in await rows(migrated, "SELECT * FROM gap_log")}
    assert set(log) == {"trades", "ticker", "lifecycle"}
    assert all(
        r["reason"] == "restart" and r["started_at"] == NOW - timedelta(minutes=7)
        for r in log.values()
    )
    assert backfiller.requeued == 1 and backfiller.submitted == [log["trades"]["id"]]


async def test_the_very_first_start_has_no_gap(migrated: str) -> None:
    pump, _, backfiller, _ = pump_for(migrated, [])
    await pump.run()
    assert await rows(migrated, "SELECT * FROM gap_log") == [] and backfiller.requeued == 1


# --- trouble ---------------------------------------------------------------


async def test_a_subscription_kalshi_will_not_restore_is_recorded_as_a_failed_gap(
    migrated: str,
) -> None:
    pump, _, _, _ = pump_for(migrated, [ResubscribeFailed("trade", "[7] no such channel", 2)])
    await pump.run()
    row = (await rows(migrated, "SELECT kind, status, reason, note FROM gap_log"))[0]
    assert (row["kind"], row["status"]) == ("all", "failed") and "trade" in row["reason"]
    assert pump.resubscribe_failures == 1 and pump.stats()["resubscribe_failures"] == 1


async def test_a_gap_is_still_recorded_when_the_database_is_away_for_a_moment(
    migrated: str,
) -> None:
    real = connector(migrated)
    failures = {"left": 2}

    async def flaky() -> asyncpg.Connection:
        if failures["left"]:
            failures["left"] -= 1
            raise OSError("no route")
        return await real()

    pump, _, _, _ = pump_for(migrated, [Reconnected(2, "connection_lost", 1.0)])
    pump._connect = flaky  # noqa: SLF001
    await pump.run()
    await settle()
    assert failures["left"] == 0 and len(await rows(migrated, "SELECT * FROM gap_log")) == 3


async def test_the_stats_report_frames_gaps_and_the_last_frame(migrated: str) -> None:
    pump, _, _, _ = pump_for(migrated, [frame(2, 5), Reconnected(2, "stalled", 1.0)])
    await pump.run()
    await settle()
    stats = pump.stats()
    assert stats["frames"] == 1 and stats["gaps_opened"] == {
        "trades": 1,
        "ticker": 1,
        "lifecycle": 1,
    }
    assert stats["last_frame"] == (NOW - timedelta(seconds=5)).isoformat()
    assert json.dumps(stats)  # plain data, ready for a status report


async def test_a_lifecycle_gap_a_previous_run_left_open_is_made_good_at_startup(
    migrated: str,
) -> None:
    conn = await asyncpg.connect(dsn(migrated))
    try:
        gap_id = await gaps.open_gap(conn, "lifecycle", NOW - timedelta(hours=1), NOW, "restart")
    finally:
        await conn.close()
    pump, _, _, discovery = pump_for(migrated, [])
    await pump.recover_startup()
    await settle()
    assert len(discovery.requests) == 1
    assert (await rows(migrated, "SELECT status FROM gap_log WHERE id = $1", gap_id))[0][
        0
    ] == "done"


async def test_the_main_loop_and_the_recovery_tasks_never_use_the_connection_at_once(
    migrated: str,
) -> None:
    pump, _, _, _ = pump_for(migrated, [])

    async def slow_query(conn: asyncpg.Connection) -> int:
        await conn.fetchval("SELECT pg_sleep(0.02)")
        return 1

    results = await asyncio.gather(*[pump._db(slow_query) for _ in range(12)])  # noqa: SLF001
    assert results == [1] * 12  # without the lock asyncpg refuses overlapping operations
    await pump.close()


async def test_a_subscription_id_reused_on_the_new_link_does_not_inherit_the_old_links_time(
    migrated: str,
) -> None:
    events: list[FeedEvent] = [
        frame(2, 100),
        frame(1, 10),
        Reconnected(2, "connection_lost", 1.0),
        SequenceGap(2, 3, 5, 2),
    ]
    pump, _, _, _ = pump_for(migrated, events)
    await drive(pump, events)
    trades = await rows(
        migrated, "SELECT started_at, reason FROM gap_log WHERE kind = 'trades' ORDER BY id"
    )
    assert len(trades) == 2
    after_reconnect = trades[1]
    assert "sequence gap" in after_reconnect["reason"]
    # sid 2 is a different subscription now: its only history is the link's last frame, 10 s ago
    assert after_reconnect["started_at"] == NOW - timedelta(seconds=10)


async def test_the_first_heartbeat_is_saved_even_when_the_machine_has_only_just_booted(
    migrated: str,
) -> None:
    pump, _, _, _ = pump_for(migrated, [], heartbeat_every=3600.0, monotonic=lambda: 5.0)
    await pump.handle(frame(2, 30))  # a monotonic clock of 5 s is less than the interval
    beat = await rows(migrated, "SELECT value FROM ingest_state WHERE key = $1", state.HEARTBEAT)
    assert len(beat) == 1
    await pump.close()
