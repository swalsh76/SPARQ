"""The trade backfill against a real PostgreSQL, with a fake Kalshi that behaves like the real one:
whole-second bounds (inclusive start, exclusive end), pages, microsecond times."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
from dbhelpers import dsn
from ingestfixtures import RECEIVED_NS, TS_MS
from ingestfixtures import trade as stream_trade
from sparq_core.endpoints import TypedPage
from sparq_core.models import Trade
from sparq_server.ingest import gaps
from sparq_server.ingest.backfill import BackfillConfig, Backfiller, rest_trade_row
from sparq_server.ingest.writer import IngestWriter, WriterConfig

pytestmark = pytest.mark.db

T0 = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
FAST_WRITER = WriterConfig(batch_rows=1000, flush_interval=0.05, retry_base=0.01, retry_max=0.05)


def rest_trade(
    trade_id: str, at: datetime, ticker: str = "KXTEST-A", side: str = "yes", **changes: Any
) -> Trade:
    """A trade as REST sends it: the time to the microsecond."""
    return Trade.model_validate(
        {
            "trade_id": trade_id,
            "ticker": ticker,
            "created_time": at.isoformat().replace("+00:00", "Z"),
            "yes_price_dollars": "0.6500",
            "no_price_dollars": "0.3500",
            "count_fp": "6.15",
            "taker_side": side,
            "taker_outcome_side": side,
            "taker_book_side": "bid",
            "is_block_trade": False,
            **changes,
        }
    )


class FakeKalshi:
    """Serves scripted trades the way the real endpoints do."""

    def __init__(
        self, trades: list[Trade], cutoff: datetime = T0 - timedelta(days=90), page: int = 3
    ) -> None:
        self.trades_data = trades
        self.cutoff = cutoff
        self.page = page
        self.live_calls: list[tuple[int, int]] = []
        self.historical_calls: list[tuple[int, int]] = []
        self.fail_after: int | None = None  # fail once, after this many pages
        self.always_fail = False
        self._pages_served = 0

    async def historical_cutoff(self) -> Any:
        return SimpleNamespace(trades_created_ts=self.cutoff)

    def _serve(
        self, calls: list[tuple[int, int]], min_ts: int, max_ts: int
    ) -> AsyncIterator[TypedPage[Trade]]:
        calls.append((min_ts, max_ts))
        wanted = [t for t in self.trades_data if min_ts <= int(t.created_time.timestamp()) < max_ts]

        async def pages() -> AsyncIterator[TypedPage[Trade]]:
            for start in range(0, max(len(wanted), 1), self.page):
                self._pages_served += 1
                if self.always_fail or (
                    self.fail_after is not None and self._pages_served > self.fail_after
                ):
                    self.fail_after = None
                    raise RuntimeError("Kalshi is unavailable")
                chunk = wanted[start : start + self.page]
                yield TypedPage(chunk, [{} for _ in chunk], None, None)

        return pages()

    def trades(
        self, *, min_ts: int, max_ts: int, limit: int = 1000, **_: Any
    ) -> AsyncIterator[TypedPage[Trade]]:
        return self._serve(self.live_calls, min_ts, max_ts)

    def historical_trades(
        self, *, min_ts: int, max_ts: int, limit: int = 1000, **_: Any
    ) -> AsyncIterator[TypedPage[Trade]]:
        return self._serve(self.historical_calls, min_ts, max_ts)


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


async def new_gap(url: str, start: datetime, end: datetime | None, kind: str = "trades") -> int:
    conn = await asyncpg.connect(dsn(url))
    try:
        return await gaps.open_gap(conn, kind, start, end, "test")
    finally:
        await conn.close()


async def gap_row(url: str, gap_id: int) -> asyncpg.Record:
    return (await rows(url, "SELECT * FROM gap_log WHERE id = $1", gap_id))[0]


def backfiller_for(url: str, fake: FakeKalshi, writer: IngestWriter, **config: Any) -> Backfiller:
    async def no_wait(_: float) -> None:
        return None

    data: Any = fake  # it stands in for MarketData
    return Backfiller(
        data,
        writer,
        connector(url),
        BackfillConfig(**config),
        sleep=no_wait,
        clock=lambda: T0 + timedelta(hours=2),
    )


async def run_gap(
    url: str, fake: FakeKalshi, start: datetime, end: datetime | None, **config: Any
) -> asyncpg.Record:
    gap_id = await new_gap(url, start, end)
    async with IngestWriter(connector(url), FAST_WRITER) as writer:
        await backfiller_for(url, fake, writer, **config).process(gap_id)
    return await gap_row(url, gap_id)


# --- the point of the slice -------------------------------------------------------------------


async def test_a_trade_the_stream_wrote_and_rest_returns_again_is_one_row(migrated: str) -> None:
    """Same trade, two sources: the stream has milliseconds, REST has microseconds."""
    millis = 1_792_792_343_916
    stream = stream_trade("t-same", ts_ms=millis)
    rest = rest_trade(
        "t-same", datetime.fromtimestamp(1_792_792_343.916130, UTC), "KXTEST-26OCT24-A"
    )
    assert rest.created_time.microsecond == 916130
    async with IngestWriter(connector(migrated), FAST_WRITER) as writer:
        await writer.add(stream)
        await writer.flush()
        fake = FakeKalshi([rest])
        gap_id = await new_gap(
            migrated,
            datetime.fromtimestamp(1_792_792_340, UTC),
            datetime.fromtimestamp(1_792_792_350, UTC),
        )
        await backfiller_for(migrated, fake, writer).process(gap_id)
    stored = await rows(migrated, "SELECT trade_id, created_time FROM trade")
    assert len(stored) == 1
    assert stored[0]["created_time"] == datetime.fromtimestamp(millis / 1000, UTC)
    gap = await gap_row(migrated, gap_id)
    assert (gap["status"], gap["trades_found"], gap["trades_added"], gap["trades_duplicate"]) == (
        "done",
        1,
        0,
        1,
    )


async def test_a_trade_missed_by_the_stream_is_added_at_the_same_resolution(migrated: str) -> None:
    at = T0 + timedelta(seconds=30, microseconds=123_456)
    gap = await run_gap(
        migrated, FakeKalshi([rest_trade("t-missed", at)]), T0, T0 + timedelta(minutes=1)
    )
    stored = (await rows(migrated, "SELECT created_time FROM trade"))[0][0]
    assert stored == T0 + timedelta(seconds=30, milliseconds=123)  # cut to the millisecond
    assert (gap["trades_found"], gap["trades_added"], gap["trades_duplicate"]) == (1, 1, 0)


def test_the_rest_row_is_the_stream_row_for_the_same_trade() -> None:
    row = rest_trade_row(
        rest_trade("t-1", datetime.fromtimestamp(TS_MS / 1000 + 0.000_130, UTC), side="no")
    )
    assert row.created_time == datetime.fromtimestamp(TS_MS / 1000, UTC)
    assert (row.yes_price_e6, row.count_e2, row.taker_side) == (650_000, 615, "no")


# --- walking the window ------------------------------------------------------------------------


async def test_a_long_window_is_read_in_contiguous_slices_with_nothing_skipped(
    migrated: str,
) -> None:
    trades = [rest_trade(f"t-{n}", T0 + timedelta(minutes=17 * n, seconds=n)) for n in range(10)]
    fake = FakeKalshi(trades)
    gap = await run_gap(migrated, fake, T0, T0 + timedelta(hours=2, minutes=40))
    calls = fake.live_calls
    assert len(calls) == 3 and all(b - a <= 3600 for a, b in calls)
    assert all(calls[i][1] == calls[i + 1][0] for i in range(len(calls) - 1))  # no hole, no overlap
    assert calls[0][0] == int((T0 - timedelta(seconds=10)).timestamp())  # padded each side
    assert calls[-1][1] == int((T0 + timedelta(hours=2, minutes=40, seconds=10)).timestamp())
    assert (gap["trades_found"], gap["trades_added"]) == (10, 10)
    assert (await rows(migrated, "SELECT count(*) FROM trade"))[0][0] == 10


async def test_a_window_with_nothing_in_it_is_a_finished_gap(migrated: str) -> None:
    gap = await run_gap(migrated, FakeKalshi([]), T0, T0 + timedelta(minutes=5))
    assert (gap["status"], gap["trades_found"], gap["trades_added"], gap["trades_duplicate"]) == (
        "done",
        0,
        0,
        0,
    )


async def test_an_open_ended_gap_runs_to_now(migrated: str) -> None:
    fake = FakeKalshi([])
    await run_gap(migrated, fake, T0, None)
    assert fake.live_calls[-1][1] == int(
        (T0 + timedelta(hours=2, seconds=10)).timestamp()
    )  # the clock + margin


async def test_the_historical_endpoint_serves_the_part_older_than_the_cutoff(migrated: str) -> None:
    cutoff = T0 + timedelta(minutes=30)
    old = rest_trade("t-old", T0 + timedelta(minutes=10))
    new = rest_trade("t-new", T0 + timedelta(minutes=40))
    fake = FakeKalshi([old, new], cutoff=cutoff)
    gap = await run_gap(migrated, fake, T0, T0 + timedelta(hours=1))
    assert fake.historical_calls and fake.live_calls
    assert max(b for _, b in fake.historical_calls) == int(cutoff.timestamp())
    assert min(a for a, _ in fake.live_calls) == int(cutoff.timestamp())  # they meet exactly
    assert gap["trades_added"] == 2


async def test_a_window_wholly_after_or_before_the_cutoff_uses_one_endpoint(migrated: str) -> None:
    live = FakeKalshi([rest_trade("a", T0 + timedelta(minutes=1))], cutoff=T0 - timedelta(days=1))
    await run_gap(migrated, live, T0, T0 + timedelta(minutes=5))
    assert live.live_calls and not live.historical_calls
    old = FakeKalshi([rest_trade("b", T0 + timedelta(minutes=2))], cutoff=T0 + timedelta(days=1))
    await run_gap(migrated, old, T0, T0 + timedelta(minutes=5))
    assert old.historical_calls and not old.live_calls


async def test_pages_beyond_the_first_are_read_too(migrated: str) -> None:
    trades = [rest_trade(f"t-{n}", T0 + timedelta(seconds=n)) for n in range(11)]
    gap = await run_gap(migrated, FakeKalshi(trades, page=2), T0, T0 + timedelta(minutes=1))
    assert gap["trades_added"] == 11


# --- what is not stored -----------------------------------------------------------------------


async def test_combo_trades_are_skipped_and_a_malformed_one_is_kept_as_a_reject(
    migrated: str,
) -> None:
    trades = [
        rest_trade("good", T0 + timedelta(seconds=1)),
        rest_trade("combo", T0 + timedelta(seconds=2), ticker="KXMVECROSS-1"),
        rest_trade("odd", T0 + timedelta(seconds=3), taker_side=None, taker_outcome_side=None),
    ]
    gap = await run_gap(migrated, FakeKalshi(trades), T0, T0 + timedelta(minutes=1))
    assert (gap["trades_found"], gap["trades_added"]) == (1, 1)
    assert "1 combo trades skipped, 1 rejected" in gap["note"]
    reject = (await rows(migrated, "SELECT kind, reason, payload FROM ingest_reject"))[0]
    assert reject["kind"] == "trade" and "taker side" in reject["reason"]
    assert json.loads(reject["payload"])["trade_id"] == "odd"


# --- trying again --------------------------------------------------------------------------------


async def test_running_a_finished_gap_again_adds_nothing_and_changes_no_row(migrated: str) -> None:
    fake = FakeKalshi([rest_trade(f"t-{n}", T0 + timedelta(seconds=n)) for n in range(5)])
    gap_id = await new_gap(migrated, T0, T0 + timedelta(minutes=1))
    async with IngestWriter(connector(migrated), FAST_WRITER) as writer:
        backfiller = backfiller_for(migrated, fake, writer)
        await backfiller.process(gap_id)
        before = await rows(migrated, "SELECT * FROM trade ORDER BY trade_id")
        await backfiller.process(gap_id)
    after = await rows(migrated, "SELECT * FROM trade ORDER BY trade_id")
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    gap = await gap_row(migrated, gap_id)
    assert (gap["trades_found"], gap["trades_added"], gap["trades_duplicate"]) == (
        5,
        0,
        5,
    )  # the last pass


async def test_a_failed_pass_is_read_again_and_every_insert_is_counted_once(migrated: str) -> None:
    trades = [rest_trade(f"t-{n}", T0 + timedelta(seconds=n)) for n in range(9)]
    fake = FakeKalshi(trades, page=3)
    fake.fail_after = 1  # the first page lands, the second fails, the retry succeeds
    gap = await run_gap(migrated, fake, T0, T0 + timedelta(minutes=1))
    assert gap["status"] == "done"
    assert (gap["trades_found"], gap["trades_added"], gap["trades_duplicate"]) == (9, 9, 0)
    assert (await rows(migrated, "SELECT count(*) FROM trade"))[0][0] == 9


async def test_a_gap_that_keeps_failing_is_failed_with_the_reason_and_what_was_added(
    migrated: str,
) -> None:
    fake = FakeKalshi([rest_trade("t-1", T0 + timedelta(seconds=1))])
    fake.always_fail = True
    gap = await run_gap(migrated, fake, T0, T0 + timedelta(minutes=1), attempts=2)
    assert gap["status"] == "failed" and "Kalshi is unavailable" in gap["note"]


async def test_only_unfinished_trade_gaps_are_run_again_after_a_restart(migrated: str) -> None:
    open_trades = await new_gap(migrated, T0, T0 + timedelta(minutes=1))
    half_done = await new_gap(migrated, T0, T0 + timedelta(minutes=1))
    done = await new_gap(migrated, T0, T0 + timedelta(minutes=1))
    lifecycle = await new_gap(migrated, T0, T0 + timedelta(minutes=1), kind="lifecycle")
    conn = await asyncpg.connect(dsn(migrated))
    try:
        await gaps.mark(conn, half_done, gaps.BACKFILLING)
        await gaps.mark(conn, done, gaps.DONE)
    finally:
        await conn.close()
    async with IngestWriter(connector(migrated), FAST_WRITER) as writer:
        backfiller = backfiller_for(migrated, FakeKalshi([]), writer)
        assert await backfiller.requeue_unfinished() == 2
        backfiller.submit(open_trades)  # already queued: not queued twice
        assert backfiller._queue.qsize() == 2  # noqa: SLF001
    assert {open_trades, half_done} == {backfiller._queue.get_nowait() for _ in range(2)}  # noqa: SLF001
    assert lifecycle not in backfiller._known  # noqa: SLF001


async def test_the_worker_runs_what_is_submitted_and_survives_a_gap_that_cannot_be_read(
    migrated: str,
) -> None:
    fake = FakeKalshi([rest_trade("t-1", T0 + timedelta(seconds=1))])
    good = await new_gap(migrated, T0, T0 + timedelta(minutes=1))
    async with IngestWriter(connector(migrated), FAST_WRITER) as writer:
        backfiller = backfiller_for(migrated, fake, writer)
        task = asyncio.create_task(backfiller.run())
        backfiller.submit(999_999)  # a gap that does not exist
        backfiller.submit(good)
        for _ in range(1500):  # a slow runner needs far longer than a local machine
            if backfiller.processed >= 2:
                break
            await asyncio.sleep(0.02)
        task.cancel()
    assert backfiller.processed >= 2
    assert (await gap_row(migrated, good))["status"] == "done"


async def test_a_gap_of_another_kind_is_left_alone(migrated: str) -> None:
    gap_id = await new_gap(migrated, T0, T0 + timedelta(minutes=1), kind="ticker")
    async with IngestWriter(connector(migrated), FAST_WRITER) as writer:
        fake = FakeKalshi([])
        await backfiller_for(migrated, fake, writer).process(gap_id)
    assert not fake.live_calls and (await gap_row(migrated, gap_id))["status"] == "open"


def test_unused_names_are_the_fixtures_we_meant() -> None:
    assert RECEIVED_NS > 0
