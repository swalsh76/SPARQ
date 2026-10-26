"""The history downloader: Kalshi's settled markets, trades and series, into Parquet files.

What it does, and why it is built this way (PLAN.md section 6.9, decisions D1 to D8):

* **Public endpoints, no key, GET only** (the client refuses anything else).
* **Per-endpoint rates** measured live: historical trades at 10 requests a second, the live trades
  endpoint and the markets listings at 3.5. After a 429 the rate halves and creeps back up
  (:class:`RateController`); persistent 429s stop the run.
* **Trades, newest day first.** One UTC day at a time. A day that straddles the historical cutoff
  is fetched in two segments, each from the endpoint that serves it. ``min_ts`` is inclusive and
  ``max_ts`` exclusive on both (measured live), so segments never overlap or leave a gap.
  Combo-market trades are dropped as they arrive and counted.
* **Resumable.** A segment is saved as part files; the cursor for the next page is written to the
  state file only after the part file is in place, so an interruption repeats at most the rows
  not yet saved and never duplicates one. A finished day is merged, sorted and written as one
  file, atomically. A day that is not yet over (or ended less than an hour ago) is left for a
  later catch-up run.
* **Capped.** It stops before starting a new day once the folder holds ``max_bytes``.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from sparq_core.config import Environment, KalshiConfig
from sparq_core.endpoints import MarketData, TypedPage
from sparq_core.markets import is_combo
from sparq_core.models import Market, Trade
from sparq_core.rest import KalshiAPIError, KalshiPublicClient

from sparq_analytics.history.datadir import DataDir, State, write_table_atomic
from sparq_analytics.history.rates import RateController
from sparq_analytics.history.schemas import (
    TRADES_SCHEMA,
    fee_changes_table,
    markets_table,
    series_table,
    trades_table,
)

DAY = 86_400
HISTORICAL = "historical"
LIVE = "live"


class DuplicateTradesError(Exception):
    """Two rows of one day share a trade id: the window arithmetic or the cursor is wrong."""


class OutsideWindowError(Exception):
    """A trade came back outside the window it was requested for."""


@dataclass(frozen=True, slots=True)
class DownloadConfig:
    data_dir: Path
    max_bytes: int = 50 * 10**9
    since: date = date(2021, 1, 1)
    empty_days_to_stop: int = 60  # this many empty days in a row: the data has not started yet
    trades_rate: float = 10.0  # historical trades, requests a second
    live_rate: float = 3.5  # live trades and the markets listings
    part_rows: int = 200_000  # rows held in memory before a part file is written
    market_pages_per_file: int = 20
    day_margin_seconds: int = 3600  # a day is fetched only once it ended this long ago
    page_size: int = 1000


@dataclass(frozen=True, slots=True)
class Segment:
    side: str  # "historical" or "live": which endpoint serves this part of the day
    start: int  # Unix seconds, inclusive
    end: int  # Unix seconds, exclusive

    @property
    def key(self) -> str:
        return f"{self.side}:{self.start}-{self.end}"


def day_start(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


def day_segments(day: date, cutoff: int) -> list[Segment]:
    """The endpoint-specific pieces of one UTC day: before the cutoff is historical, after live."""
    start, end = day_start(day), day_start(day) + DAY
    if end <= cutoff:
        return [Segment(HISTORICAL, start, end)]
    if start >= cutoff:
        return [Segment(LIVE, start, end)]
    return [Segment(HISTORICAL, start, cutoff), Segment(LIVE, cutoff, end)]


def newest_complete_day(now: float, margin: int) -> date:
    """The latest UTC day that ended at least ``margin`` seconds before ``now``."""
    return datetime.fromtimestamp(now - margin, UTC).date() - timedelta(days=1)


@dataclass(slots=True)
class TradesReport:
    days_done: int = 0
    days_skipped: int = 0
    rows: int = 0
    combos_skipped: int = 0
    stopped: str = ""
    newest_day: str = ""
    oldest_day: str = ""


class Downloader:
    def __init__(
        self,
        config: DownloadConfig,
        *,
        trades_client: KalshiPublicClient,
        live_client: KalshiPublicClient,
        clock: Callable[[], float] = time.time,
        log: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        self.dir = DataDir(config.data_dir)
        self.dir.remove_stale_temporaries()
        self.state = State(self.dir.state_path)
        self._clock = clock
        self._log = log
        self.trades_data = MarketData(trades_client)
        self.live_data = MarketData(live_client)
        self.trades_rate = RateController(trades_client, config.trades_rate)
        self.live_rate = RateController(live_client, config.live_rate)
        self._clients = (trades_client, live_client)

    @classmethod
    def create(
        cls,
        config: DownloadConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        log: Callable[[str], None] = print,
    ) -> "Downloader":
        """Build the two clients (production, public data) at the configured rates."""

        def client(rate: float) -> KalshiPublicClient:
            kalshi = KalshiConfig(
                environment=Environment.PRODUCTION,
                rate_limit_per_second=rate,
                burst_requests=max(rate, 1.0),
            )
            return KalshiPublicClient(kalshi, transport=transport)

        return cls(
            config,
            trades_client=client(config.trades_rate),
            live_client=client(config.live_rate),
            log=log,
        )

    def say(self, message: str) -> None:
        self._log(message)

    async def aclose(self) -> None:
        for client in self._clients:
            await client.aclose()

    async def __aenter__(self) -> "Downloader":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _guarded[T](
        self, controller: RateController, action: Callable[[], Awaitable[T]]
    ) -> T:
        """Run a restartable step; if Kalshi rate-limits it past the client's own retries, let the
        controller slow down (or give up with :class:`RateLimitPersisting`) and run it again from
        the saved state."""
        while True:
            try:
                return await action()
            except KalshiAPIError as exc:
                if exc.status != 429:
                    raise
                controller.update()

    # --- the cutoff -----------------------------------------------------------------------

    async def cutoffs(self) -> tuple[int, int]:
        """(trades cutoff, markets cutoff) in Unix seconds."""
        cutoff = await self.live_data.historical_cutoff()
        return int(cutoff.trades_created_ts.timestamp()), int(cutoff.market_settled_ts.timestamp())

    # --- series ---------------------------------------------------------------------------

    async def download_series(self) -> tuple[int, int]:
        """The series list (with categories and fee terms) and the fee-change history."""
        series = await self._guarded(self.live_rate, self.live_data.series_list)
        changes = await self._guarded(self.live_rate, self.live_data.series_fee_changes)
        self.live_rate.update()
        fetched = datetime.fromtimestamp(self._clock(), UTC)
        write_table_atomic(series_table(series, fetched), self.dir.root / "series.parquet")
        write_table_atomic(
            fee_changes_table(changes, fetched), self.dir.root / "fee_changes.parquet"
        )
        self.state.section("series").update(
            fetched_at=fetched.isoformat(), series=len(series), fee_changes=len(changes)
        )
        self.state.save()
        self._log(f"series: {len(series):,} series and {len(changes):,} fee changes saved")
        return len(series), len(changes)

    # --- markets --------------------------------------------------------------------------

    async def download_historical_markets(self) -> int:
        """Every market that settled before the cutoff (combos excluded), in part files."""
        st = self.state.section("markets_historical")
        if st.get("done"):
            self._log(f"historical markets: already done ({st.get('rows', 0):,} rows)")
            return int(st.get("rows", 0))
        try:
            return await self._guarded(
                self.live_rate, lambda: self._historical_markets_from(st.get("cursor"), st)
            )
        except KalshiAPIError as exc:
            if exc.status != 400 or not st.get("cursor"):
                raise
            self._log(
                "historical markets: the saved cursor was refused; starting that listing again"
            )
            for path in (self.dir.root / "markets" / "historical").glob("part-*.parquet"):
                path.unlink()
            st.clear()
            self.state.save()
            return await self._guarded(
                self.live_rate, lambda: self._historical_markets_from(None, st)
            )

    async def _historical_markets_from(self, cursor: str | None, st: dict[str, Any]) -> int:
        files = int(st.get("files", 0))
        rows = int(st.get("rows", 0))
        markets: list[Market] = []
        raw: list[dict[str, Any]] = []
        pages = 0
        async for page in self.live_data.historical_markets(
            mve_filter="exclude", limit=self.config.page_size, cursor=cursor
        ):
            self.live_rate.update()
            markets += page.items
            raw += page.raw
            pages += 1
            if pages >= self.config.market_pages_per_file or page.next_cursor is None:
                files += 1
                write_table_atomic(
                    markets_table(markets, raw, HISTORICAL), self.dir.historical_markets_file(files)
                )
                rows += len(markets)
                st.update(
                    cursor=page.next_cursor, files=files, rows=rows, done=page.next_cursor is None
                )
                self.state.save()
                self._log(f"historical markets: {rows:,} saved in {files} files")
                markets, raw, pages = [], [], 0
        return rows

    async def download_live_markets(self) -> int:
        """Markets that settled since the cutoff, one settlement day at a time."""
        _, markets_cutoff = await self.cutoffs()
        days = self.state.section("markets_live")
        first = datetime.fromtimestamp(markets_cutoff, UTC).date()
        last = newest_complete_day(self._clock(), self.config.day_margin_seconds)
        total = 0
        day = first
        while day <= last:
            key = day.isoformat()
            if days.get(key, {}).get("done"):
                total += int(days[key]["rows"])
                day += timedelta(days=1)
                continue
            start, end = max(day_start(day), markets_cutoff), day_start(day) + DAY
            markets: list[Market] = []
            raw: list[dict[str, Any]] = []

            async def fetch_day(
                start: int = start,
                end: int = end,
                markets: list[Market] = markets,
                raw: list[dict[str, Any]] = raw,
            ) -> None:
                markets.clear()
                raw.clear()
                async for page in self.live_data.markets(
                    status="settled",
                    mve_filter="exclude",
                    limit=self.config.page_size,
                    min_settled_ts=start,
                    max_settled_ts=end,
                ):
                    self.live_rate.update()
                    markets += page.items
                    raw += page.raw

            await self._guarded(self.live_rate, fetch_day)
            write_table_atomic(markets_table(markets, raw, LIVE), self.dir.live_markets_file(key))
            days[key] = {"done": True, "rows": len(markets)}
            self.state.save()
            total += len(markets)
            self._log(f"live markets {key}: {len(markets):,} settled")
            day += timedelta(days=1)
        return total

    # --- trades ---------------------------------------------------------------------------

    def _trade_days(self) -> dict[str, dict[str, Any]]:
        days: dict[str, dict[str, Any]] = self.state.section("trades")
        return days

    def over_cap(self) -> bool:
        return self.dir.bytes_used() >= self.config.max_bytes

    async def download_trades(self, *, limit_days: int | None = None) -> TradesReport:
        """Newest complete day first, back to ``since``, until the size cap or the data runs out."""
        report = TradesReport()
        trades_cutoff, _ = await self.cutoffs()
        days = self._trade_days()
        day = newest_complete_day(self._clock(), self.config.day_margin_seconds)
        empty_streak = 0
        while day >= self.config.since:
            key = day.isoformat()
            done = days.get(key, {})
            if done.get("status") == "done":
                report.days_skipped += 1
                empty_streak = empty_streak + 1 if done.get("rows") == 0 else 0
            else:
                if self.over_cap():
                    report.stopped = f"size cap reached ({self.dir.bytes_used() / 1e9:.1f} GB)"
                    return report
                if limit_days is not None and report.days_done >= limit_days:
                    report.stopped = f"stopped after {limit_days} day(s) as asked"
                    return report
                result = await self._download_day(day, trades_cutoff)
                report.days_done += 1
                report.rows += int(result["rows"])
                report.combos_skipped += int(result["combos_skipped"])
                report.newest_day = report.newest_day or key
                report.oldest_day = key
                empty_streak = empty_streak + 1 if result["rows"] == 0 else 0
            if empty_streak >= self.config.empty_days_to_stop:
                report.stopped = f"{empty_streak} empty days in a row: the data starts after {key}"
                return report
            day -= timedelta(days=1)
        report.stopped = f"reached the start date {self.config.since.isoformat()}"
        return report

    async def _download_day(self, day: date, cutoff: int) -> dict[str, Any]:
        key = day.isoformat()
        started = time.monotonic()
        entry = self._trade_days().setdefault(key, {"status": "partial", "segments": {}})
        segments = day_segments(day, cutoff)
        segment_states: dict[str, dict[str, Any]] = entry["segments"]
        # a cutoff that moved since an earlier partial attempt changes the segments: start over
        if set(segment_states) - {s.key for s in segments}:
            self.dir.clear_work("trades", key)
            segment_states.clear()
        for segment in segments:
            seg_state = segment_states.setdefault(
                segment.key, {"cursor": None, "parts": [], "rows": 0, "combos": 0, "done": False}
            )
            if not seg_state["done"]:
                controller = self.trades_rate if segment.side == HISTORICAL else self.live_rate
                await self._guarded(
                    controller,
                    lambda s=segment, st=seg_state: self._fetch_segment(key, s, st),  # type: ignore[misc]
                )
        result = self._finish_day(day, segments, segment_states)
        self._trade_days()[key] = {"status": "done", **result}
        self.state.save()
        self.dir.clear_work("trades", key)
        self._log(
            f"{key}: {result['rows']:,} trades kept, {result['combos_skipped']:,} combo trades "
            f"skipped, {time.monotonic() - started:.0f} s; "
            f"folder {self.dir.bytes_used() / 1e9:.2f} GB"
        )
        return result

    async def _fetch_segment(self, key: str, segment: Segment, seg_state: dict[str, Any]) -> None:
        historical = segment.side == HISTORICAL
        data = self.trades_data if historical else self.live_data
        controller = self.trades_rate if historical else self.live_rate
        fetch = data.historical_trades if historical else data.trades
        cursor = seg_state["cursor"]
        work = self.dir.work_dir("trades", key)
        buffer: list[Trade] = []
        combos = 0
        try:
            async for page in fetch(
                min_ts=segment.start, max_ts=segment.end, limit=self.config.page_size, cursor=cursor
            ):
                controller.update()
                kept = [t for t in page.items if not is_combo(t.ticker)]
                combos += len(page.items) - len(kept)
                buffer += kept
                last_page = page.next_cursor is None
                if len(buffer) >= self.config.part_rows or last_page:
                    self._save_part(work, segment, seg_state, buffer, combos, page)
                    buffer, combos = [], 0
        except KalshiAPIError as exc:
            if exc.status != 400 or cursor is None:
                raise
            self._log(
                f"{key} {segment.side}: the saved cursor was refused; fetching that part again"
            )
            for path in work.glob(f"{segment.side}-*.parquet"):
                path.unlink()
            seg_state.update(cursor=None, parts=[], rows=0, combos=0, done=False)
            self.state.save()
            await self._fetch_segment(key, segment, seg_state)

    def _save_part(
        self,
        work: Path,
        segment: Segment,
        seg_state: dict[str, Any],
        buffer: list[Trade],
        combos: int,
        page: TypedPage[Trade],
    ) -> None:
        parts: list[str] = seg_state["parts"]
        if buffer:
            name = f"{segment.side}-{len(parts):05d}.parquet"
            write_table_atomic(trades_table(buffer), work / name)
            parts.append(name)
        seg_state["rows"] = int(seg_state["rows"]) + len(buffer)
        seg_state["combos"] = int(seg_state["combos"]) + combos
        seg_state["cursor"] = page.next_cursor
        seg_state["done"] = page.next_cursor is None
        self.state.save()  # only after the part file is in place

    def _finish_day(
        self, day: date, segments: list[Segment], states: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        key = day.isoformat()
        work = self.dir.work_dir("trades", key)
        tables: list[pa.Table] = []
        for segment in segments:
            for name in states[segment.key]["parts"]:
                tables.append(pq.read_table(work / name, schema=TRADES_SCHEMA))
        table = pa.concat_tables(tables) if tables else TRADES_SCHEMA.empty_table()
        rows = table.num_rows
        if rows:
            distinct = pc.count_distinct(table["trade_id"]).as_py()
            if distinct != rows:
                raise DuplicateTradesError(f"{key}: {rows - distinct} duplicate trade ids")
            low = pc.min(table["created_time"]).value // 1_000_000  # whole seconds since 1970
            high = pc.max(table["created_time"]).value // 1_000_000
            start, end = day_start(day), day_start(day) + DAY
            if low < start or high >= end:
                raise OutsideWindowError(f"{key}: trades from {low} to {high} fall outside the day")
            table = table.sort_by(
                [("ticker", "ascending"), ("created_time", "ascending"), ("trade_id", "ascending")]
            )
        write_table_atomic(table, self.dir.trades_file(key))
        return {
            "rows": rows,
            "combos_skipped": sum(int(s["combos"]) for s in states.values()),
            "segments": sorted(states),
        }

    # --- reporting ------------------------------------------------------------------------

    def now(self) -> float:
        return self._clock()

    def status(self) -> dict[str, Any]:
        return status_of(self.dir, self.state, self.config.max_bytes)


def status_of(directory: DataDir, state: State, max_bytes: int) -> dict[str, Any]:
    """What has been downloaded, from the state file and the folder alone (no network)."""
    days: dict[str, dict[str, Any]] = state.section("trades")
    done = {k: v for k, v in days.items() if v.get("status") == "done"}
    historical = state.section("markets_historical")
    live = state.section("markets_live")
    return {
        "folder": str(directory.root),
        "gigabytes": round(directory.bytes_used() / 1e9, 3),
        "cap_gigabytes": max_bytes / 1e9,
        "trade_days_done": len(done),
        "trade_days_partial": len(days) - len(done),
        "trade_rows": sum(int(v["rows"]) for v in done.values()),
        "combo_trades_skipped": sum(int(v["combos_skipped"]) for v in done.values()),
        "newest_day": max(done) if done else None,
        "oldest_day": min(done) if done else None,
        "historical_markets": {
            "done": bool(historical.get("done")),
            "rows": historical.get("rows", 0),
        },
        "live_market_days": len(live),
        "live_markets": sum(int(v["rows"]) for v in live.values()),
        "series": state.section("series") or None,
    }
