"""The trade backfill: fetch what the feed missed from REST and store it, safely and idempotently.

A gap of kind ``trades`` names a window. The backfill reads that window (padded a little) from
Kalshi in slices of an hour, from the live endpoint or, for the part older than the historical
cutoff, from the historical one, and stores the trades through the writer's de-duplicating insert.

* **Safe to repeat.** A trade is stored once whatever delivers it and however often: the REST time
  is cut to the millisecond like the WebSocket's, so the unique key matches, and the database drops
  the duplicate. A retry, a restart in the middle, or an overlap with the live stream cannot double
  anything, and no stored-id lookup is needed.
* **Never truncated.** A long window is sliced, not cut short. Any gap a previous run left open or
  half done is run again at startup.
* **Honest counts.** ``trades_found`` is what the last complete pass read, ``trades_added`` is every
  trade actually inserted across all passes, and the difference is the duplicates.
"""

import asyncio
import contextlib
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from sparq_core.endpoints import MarketData
from sparq_core.markets import is_combo
from sparq_core.models import Trade

from sparq_server.ingest import gaps
from sparq_server.ingest.discovery import store_rejects
from sparq_server.ingest.rows import TradeRow, check_trade, floor_ms
from sparq_server.ingest.writer import IngestWriter

log = logging.getLogger("sparq_server")


@dataclass(frozen=True, slots=True)
class BackfillConfig:
    slice: timedelta = timedelta(hours=1)
    margin: timedelta = timedelta(seconds=10)  # read a little each side of the gap
    attempts: int = 3
    retry_base: float = 2.0
    rescan_every: float = 300.0  # how often unfinished gaps are looked for again


@dataclass
class Progress:
    found: int = 0  # valid, non-combo trades read in the current pass
    added: int = 0  # trades actually inserted, over every pass
    rejected: int = 0
    combos: int = 0


def rest_trade_row(trade: Trade) -> TradeRow:
    """A REST trade as the row the stream would have written for it."""
    return TradeRow(
        trade.trade_id, trade.ticker, floor_ms(trade.created_time), trade.yes_price_e6,
        trade.count_e2, trade.taker_side or "", trade.is_block_trade,
    )  # fmt: skip


class Backfiller:
    def __init__(
        self,
        data: MarketData,
        writer: IngestWriter,
        connect: Callable[[], Awaitable[asyncpg.Connection]],
        config: BackfillConfig | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._data = data
        self._writer = writer
        self._connect = connect
        self._config = config or BackfillConfig()
        self._sleep = sleep
        self._clock = clock
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._known: set[int] = set()  # queued or running
        self.processed = 0
        self.failures = 0

    def submit(self, gap_id: int) -> None:
        if gap_id not in self._known:
            self._known.add(gap_id)
            self._queue.put_nowait(gap_id)

    async def requeue_unfinished(self) -> int:
        """Run again every trades gap left open or half done; returns how many."""
        conn = await self._connect()
        try:
            pending = [g for g in await gaps.unfinished(conn) if g.kind == gaps.TRADES]
        finally:
            await conn.close()
        for gap in pending:
            self.submit(gap.id)
        return len(pending)

    async def run(self) -> None:
        """Work through the queue for as long as the process lives."""
        async with asyncio.TaskGroup() as group:
            group.create_task(self._rescan())
            while True:
                gap_id = await self._queue.get()
                try:
                    await self.process(gap_id)
                except Exception:  # one gap must not stop the others
                    self.failures += 1
                    log.exception("backfill of gap %d crashed", gap_id)
                finally:
                    self._known.discard(gap_id)
                    self.processed += 1

    async def _rescan(self) -> None:
        while True:
            await self._sleep(self._config.rescan_every)
            with contextlib.suppress(Exception):
                await self.requeue_unfinished()

    async def process(self, gap_id: int) -> None:
        conn = await self._connect()
        try:
            gap = await gaps.get(conn, gap_id)
            if gap is None or gap.kind != gaps.TRADES:
                return
            await gaps.mark(conn, gap_id, gaps.BACKFILLING)
            start = gap.started_at - self._config.margin
            end = (gap.ended_at or self._clock()) + self._config.margin
            progress, error = Progress(), ""
            for attempt in range(1, self._config.attempts + 1):
                progress.found = 0  # the last complete pass is what "found" reports
                try:
                    await self._read_window(conn, start, end, progress)
                    error = ""
                    break
                except Exception as exc:  # re-reading is safe, so try the whole window again
                    error = f"{type(exc).__name__}: {exc}"[:300]
                    log.warning("gap %d backfill attempt %d failed: %s", gap_id, attempt, error)
                    if attempt < self._config.attempts:
                        await self._sleep(self._config.retry_base * 2 ** (attempt - 1))
            note = f"{progress.combos} combo trades skipped, {progress.rejected} rejected"
            if error:
                await gaps.finish(
                    conn, gap_id, gaps.FAILED, added=progress.added, note=f"{error}; {note}"
                )
            else:
                await gaps.finish(
                    conn, gap_id, gaps.DONE, found=progress.found, added=progress.added,
                    duplicate=max(progress.found - progress.added, 0), note=note,
                )  # fmt: skip
                log.info("gap %d done: found %d, added %d", gap_id, progress.found, progress.added)
        finally:
            await conn.close()

    async def _read_window(
        self, conn: asyncpg.Connection, start: datetime, end: datetime, progress: Progress
    ) -> None:
        cutoff = int((await self._data.historical_cutoff()).trades_created_ts.timestamp())
        low, high = math.floor(start.timestamp()), math.ceil(end.timestamp())
        parts: list[tuple[int, int, bool]] = []  # (from, to, historical)
        if low < cutoff:
            parts.append((low, min(high, cutoff), True))
        if high > cutoff:
            parts.append((max(low, cutoff), high, False))
        step = int(self._config.slice.total_seconds())
        for part_low, part_high, historical in parts:
            for first in range(part_low, part_high, step):
                last = min(first + step, part_high)
                source: Any = self._data.historical_trades if historical else self._data.trades
                async for page in source(min_ts=first, max_ts=last, limit=1000):
                    await self._store(conn, page.items, progress)

    async def _store(
        self, conn: asyncpg.Connection, trades: list[Trade], progress: Progress
    ) -> None:
        rows: list[TradeRow] = []
        rejects: list[tuple[dict[str, Any], str]] = []
        for trade in trades:
            if is_combo(trade.ticker):
                progress.combos += 1
                continue
            row = rest_trade_row(trade)
            problem = check_trade(row)
            if problem:
                rejects.append((row._asdict(), problem))
            else:
                rows.append(row)
        await store_rejects(conn, "trade", rejects)
        progress.rejected += len(rejects)
        added, _ = await self._writer.insert_trades(rows)
        progress.found += len(rows)
        progress.added += added
