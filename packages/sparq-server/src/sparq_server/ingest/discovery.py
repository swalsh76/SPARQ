"""Discovery: keep the series, fee changes and markets in step with Kalshi.

Two kinds of cycle. A **full** one (the first, then at most daily) lists every unopened, open and
closed market and the markets settled since the bookmark, and refreshes the series and their fee
changes. An **incremental** one asks only for markets updated since the bookmark
(``min_updated_ts``), which covers new markets, status changes and settlements, and takes seconds.
Combo (``KXMVE``) markets are never listed.

The bookmark advances only when a whole cycle has succeeded, and it is set a couple of minutes back
so that clock and ordering edges cannot hide a change; a failed cycle is simply retried from the
same point. A market Kalshi sends that does not fit the model is set aside in ``ingest_reject`` and
counted: it must not fail the cycle, or the cycle would fail forever on the same page.

The series list is one 20 MB response, so it is fetched in full cycles only.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from sparq_core.endpoints import MarketData
from sparq_core.markets import is_combo, market_values

from sparq_server.ingest import records, state
from sparq_server.ingest.writer import scrub

log = logging.getLogger("sparq_server")

STATUSES = ("unopened", "open", "closed")
SERIES_CHUNK = 5_000


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    chunk: int = 1_000  # markets per request and per transaction
    first_run_lookback: timedelta = timedelta(days=7)  # settled markets to fetch on the first run
    full_every: timedelta = timedelta(hours=24)
    overlap: timedelta = timedelta(minutes=2)


@dataclass
class Counts:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    rejected: int = 0

    @property
    def unchanged(self) -> int:
        return self.fetched - self.inserted - self.updated


@dataclass
class DiscoveryReport:
    mode: str
    series: Counts = field(default_factory=Counts)
    fee_changes: Counts = field(default_factory=Counts)
    markets: Counts = field(default_factory=Counts)
    history_rows: int = 0
    seconds: float = 0.0

    def line(self) -> str:
        m = self.markets
        return (
            f"{self.mode}: markets {m.fetched} fetched, {m.inserted} new, {m.updated} changed, "
            f"{m.rejected} rejected; {self.history_rows} history rows; {self.seconds:.1f} s"
        )


def epoch(moment: datetime) -> int:
    return int(moment.timestamp())


async def store_rejects(
    conn: asyncpg.Connection, kind: str, items: list[tuple[dict[str, Any], str]]
) -> None:
    if items:
        await conn.copy_records_to_table(
            "ingest_reject",
            records=[
                (kind, scrub(why)[:500], json.dumps(scrub(raw), default=str)) for raw, why in items
            ],
            columns=["kind", "reason", "payload"],
        )


class Discovery:
    def __init__(
        self,
        data: MarketData,
        connect: Callable[[], Awaitable[asyncpg.Connection]],
        config: DiscoveryConfig | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._data = data
        self._connect = connect
        self._config = config or DiscoveryConfig()
        self._clock = clock
        self._monotonic = monotonic

    async def cycle(self, full: bool = False) -> DiscoveryReport:
        """One cycle; raises if anything failed, leaving the bookmarks where they were."""
        started = self._monotonic()
        conn = await self._connect()
        try:
            at = self._clock()
            since = await state.get_int(conn, state.UPDATED_SINCE)
            last_full = await state.get_int(conn, state.LAST_FULL)
            do_full = (
                full
                or since is None
                or last_full is None
                or at - datetime.fromtimestamp(last_full, UTC) >= self._config.full_every
            )
            report = DiscoveryReport("full" if do_full else "incremental")
            if do_full:
                await self._series(conn, at, report)
                for status in STATUSES:
                    await self._markets(conn, at, report, status=status)
                settled = since or epoch(at - self._config.first_run_lookback)
                await self._markets(conn, at, report, status="settled", min_settled_ts=settled)
            else:
                await self._markets(conn, at, report, min_updated_ts=since)
            # only now, with everything written, move the bookmarks
            await state.put(conn, state.UPDATED_SINCE, epoch(at - self._config.overlap))
            if do_full:
                await state.put(conn, state.LAST_FULL, epoch(at))
            report.seconds = self._monotonic() - started
            return report
        finally:
            await conn.close()

    async def _markets(
        self,
        conn: asyncpg.Connection,
        at: datetime,
        report: DiscoveryReport,
        **filters: Any,
    ) -> None:
        pages = self._data.markets(
            mve_filter="exclude", limit=self._config.chunk, tolerant=True, **filters
        )
        async for page in pages:
            rows = [
                market_values(market, raw, "live")
                for market, raw in zip(page.items, page.raw, strict=True)
                if not is_combo(market.ticker)
            ]
            await store_rejects(conn, "market", page.invalid)
            inserted, updated, history = await records.upsert_markets(conn, rows, at)
            report.markets.fetched += len(rows)
            report.markets.inserted += inserted
            report.markets.updated += updated
            report.markets.rejected += len(page.invalid)
            report.history_rows += history

    async def _series(
        self, conn: asyncpg.Connection, at: datetime, report: DiscoveryReport
    ) -> None:
        series = await self._data.series_list()
        rows = [records.series_record(s, at) for s in series]
        for start in range(0, len(rows), SERIES_CHUNK):
            chunk = rows[start : start + SERIES_CHUNK]
            inserted, updated = await records.upsert(
                conn, "series", records.SERIES_COLUMNS, "ticker", chunk, ignore=("fetched_at",)
            )
            report.series.inserted += inserted
            report.series.updated += updated
        report.series.fetched = len(rows)
        changes = await self._data.series_fee_changes()
        fee_rows = [records.fee_change_record(c, at) for c in changes]
        inserted, updated = await records.upsert(
            conn, "fee_change", records.FEE_CHANGE_COLUMNS, "id", fee_rows, ignore=("fetched_at",)
        )
        report.fee_changes = Counts(len(fee_rows), inserted, updated)


class DiscoveryRunner:
    """Run a cycle every ``interval`` seconds, or sooner when asked; a failed cycle is retried."""

    def __init__(
        self,
        discovery: Discovery,
        interval: float = 60.0,
        *,
        retry_after: float = 15.0,
    ) -> None:
        self._discovery = discovery
        self._interval = interval
        self._retry_after = retry_after
        self._wake = asyncio.Event()
        self._starts = 0  # cycles begun
        self._waiters: list[tuple[int, asyncio.Future[None]]] = []
        self.cycles = 0
        self.failures = 0
        self.last_report: DiscoveryReport | None = None
        self.last_error: str | None = None
        self.last_success: datetime | None = None

    def request(self) -> asyncio.Future[None]:
        """Run a cycle now; the future completes when a cycle that BEGAN after this call succeeds.

        A cycle already under way may have read the markets before the change that prompted the
        request, so it does not count.
        """
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append((self._starts + 1, future))
        self._wake.set()
        return future

    async def run(self) -> None:
        wait = 0.0
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), wait)
            self._wake.clear()
            self._starts += 1
            began = self._starts
            try:
                self.last_report = await self._discovery.cycle()
                self.cycles += 1
                self.last_error = None
                self.last_success = datetime.now(UTC)
                wait = self._interval
                self._resolve(began)
                log.info("discovery: %s", self.last_report.line())
            except Exception as exc:  # a cycle must never stop the loop
                self.failures += 1
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                wait = self._retry_after
                log.exception("discovery cycle failed; retrying in %.0f s", wait)

    def _resolve(self, began: int) -> None:
        """Release the requests that a cycle begun at or after their time has now satisfied."""
        waiting = []
        for target, future in self._waiters:
            if target <= began and not future.done():
                future.set_result(None)
            elif not future.done():
                waiting.append((target, future))
        self._waiters = waiting

    def stats(self) -> dict[str, Any]:
        return {
            "cycles": self.cycles,
            "failures": self.failures,
            "last_error": self.last_error,
            "last_success": None if self.last_success is None else self.last_success.isoformat(),
        }
