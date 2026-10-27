"""Opt-in live check of the downloader against the real Kalshi API (GET only, no key).

    $env:SPARQ_LIVE = '1'; $env:SPARQ_LIVE_SUMMARY = "$env:TEMP\\sparq-live-summary.txt"
    python -m uv run pytest packages/sparq-analytics/tests/test_live_history.py -q

A full day of trades is millions of rows, so this fetches two 20-minute windows through the same
segment code the day crawl uses: one just before the historical cutoff (historical endpoint) and
one an hour ago (live endpoint). It checks what a fake cannot: that real pages, cursors and part
files give the same data after an interruption and a resume, that combo trades are skipped, and
that the **volume of every market that opened and settled inside the window equals the sum of
its trades**. About 500 requests. Only counts are written to the summary.
"""

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest
from sparq_analytics.history.downloader import DownloadConfig, Downloader, Segment
from sparq_analytics.history.schemas import TRADES_SCHEMA
from sparq_core.models import Market

pytestmark = pytest.mark.integration

LINES: list[str] = []
WINDOW = 1200  # seconds: long enough to hold a whole 15-minute market
PART_ROWS = 3000


@pytest.fixture(scope="module", autouse=True)
def summary() -> Iterator[None]:
    yield
    target = os.environ.get("SPARQ_LIVE_SUMMARY")
    if target:
        Path(target).write_text("\n".join(LINES) + "\n", encoding="utf-8")


@pytest.fixture
async def downloader(tmp_path: Path) -> AsyncIterator[Downloader]:
    config = DownloadConfig(data_dir=tmp_path / "kalshi", part_rows=PART_ROWS)
    async with Downloader.create(config, log=lambda line: None) as d:
        yield d
        trades = d.trades_data._client.stats  # noqa: SLF001
        live = d.live_data._client.stats  # noqa: SLF001
        LINES.append(
            f"  requests: trades client {trades.requests}, live client {live.requests}; "
            f"429s {trades.rate_limited + live.rate_limited}"
        )


async def fetch(
    d: Downloader, segment: Segment, name: str, interrupt_after_parts: int | None = None
) -> tuple[pa.Table, int, bool]:
    """Fetch one window through the real segment code: (rows, combos skipped, was interrupted)."""
    state: dict[str, Any] = {"cursor": None, "parts": [], "rows": 0, "combos": 0, "done": False}
    interrupted = False
    if interrupt_after_parts is not None:
        task = asyncio.create_task(d._fetch_segment(name, segment, state))  # noqa: SLF001
        while not task.done() and len(state["parts"]) < interrupt_after_parts:  # noqa: ASYNC110
            await asyncio.sleep(0.02)  # polling is the point: stop it after N saved parts
        if not task.done():
            task.cancel()
            interrupted = True
            with contextlib.suppress(asyncio.CancelledError):
                await task
        else:
            await task
    if not state["done"]:
        await d._fetch_segment(name, segment, state)  # noqa: SLF001 - resumes from the saved cursor
    work = d.dir.work_dir("trades", name)
    tables = [pq.read_table(work / part, schema=TRADES_SCHEMA) for part in state["parts"]]
    return pa.concat_tables(tables), int(state["combos"]), interrupted


def reconcile(
    table: pa.Table, markets: list[Market], start: int, end: int
) -> tuple[int, int, list[tuple[str, int, int]]]:
    """(markets inside the window, those whose volume equals their trades, the ones that differ)."""
    grouped = table.group_by("ticker").aggregate([("count_e2", "sum")])
    sums = dict(
        zip(grouped["ticker"].to_pylist(), grouped["count_e2_sum"].to_pylist(), strict=True)
    )
    checked = matched = 0
    differ: list[tuple[str, int, int]] = []
    for m in markets:
        if m.open_time is None or m.settlement_ts is None or m.volume_e2 is None:
            continue
        if m.open_time.timestamp() < start or m.settlement_ts.timestamp() >= end:
            continue
        checked += 1
        total = int(sums.get(m.ticker, 0))
        if total == m.volume_e2:
            matched += 1
        else:
            differ.append((m.ticker, m.volume_e2, total))
    return checked, matched, differ


async def check_side(
    d: Downloader, side: str, start: int, end: int, markets: list[Market], minimum: int = 3
) -> None:
    segment = Segment(side, start, end)
    straight, combos, _ = await fetch(d, segment, f"straight-{side}")
    resumed, resumed_combos, interrupted = await fetch(
        d, segment, f"resumed-{side}", interrupt_after_parts=2
    )
    ids = straight["trade_id"].to_pylist()
    resumed_ids = resumed["trade_id"].to_pylist()
    duplicates = len(ids) - len(set(ids))
    same = sorted(ids) == sorted(resumed_ids)
    checked, matched, differ = reconcile(straight, markets, start, end)
    seconds = pc.min_max(straight["created_time"])
    span = (seconds["max"].value - seconds["min"].value) // 1_000_000
    LINES.append(
        f"{side} window of {(end - start) // 60} minutes: {len(ids):,} trades kept, "
        f"{combos:,} combo trades skipped, duplicates {duplicates}, trade times span {span} s"
    )
    LINES.append(
        f"{side} interrupted mid-window {interrupted}; same trades after the resume {same}; "
        f"same combo count {combos == resumed_combos}"
    )
    LINES.append(
        f"{side} volume reconciliation: {checked} markets inside the window, {matched} equal "
        f"the sum of their trades, {len(differ)} differ {differ[:3]}"
    )
    assert len(ids) > 1000
    assert duplicates == 0
    assert interrupted, "the run finished before it could be interrupted: use a longer window"
    assert same
    assert combos == resumed_combos
    assert combos > 0
    assert checked >= minimum
    assert not differ


async def test_the_historical_side_far_from_the_cutoff(downloader: Downloader) -> None:
    """A window a day and a half before the newest 15-minute markets, with their own listing.

    The global historical listing is only roughly in time order and mixes every series, so the
    markets come from the per-series listing, which is where the short-lived ones are.
    """
    trades_cutoff, markets_cutoff = await downloader.cutoffs()
    markets: list[Market] = []
    for series in ("KXBTC15M", "KXETH15M"):
        pages = 0
        async for page in downloader.live_data.historical_markets(series_ticker=series, limit=1000):
            markets += page.items
            pages += 1
            if pages >= 2:
                break
    newest = max(int(m.settlement_ts.timestamp()) for m in markets if m.settlement_ts)
    target = newest - 36 * 3600
    start = target - (target % 900)  # on a quarter hour, where 15-minute markets open
    LINES.append(
        f"historical cutoff {datetime.fromtimestamp(trades_cutoff, UTC):%Y-%m-%d %H:%M}Z "
        f"(markets {datetime.fromtimestamp(markets_cutoff, UTC):%Y-%m-%d %H:%M}Z); "
        f"{len(markets):,} historical 15-minute markets listed; window starts "
        f"{datetime.fromtimestamp(start, UTC):%Y-%m-%d %H:%M}Z"
    )
    # 35 minutes hold two quarter-hours of two series: four whole 15-minute markets
    await check_side(downloader, "historical", start, start + 2100, markets, minimum=4)


async def test_the_historical_side_just_before_the_cutoff(downloader: Downloader) -> None:
    """Trades from the historical endpoint three hours before the cutoff, checked against the
    markets the live endpoint still serves for that time."""
    trades_cutoff, _ = await downloader.cutoffs()
    start = trades_cutoff - 3 * 3600  # a whole hour, so also on a quarter hour
    markets: list[Market] = []
    async for page in downloader.live_data.markets(
        status="settled",
        mve_filter="exclude",
        min_settled_ts=start,
        max_settled_ts=start + WINDOW + 900,
        limit=1000,
    ):
        markets += page.items
    await check_side(downloader, "historical", start, start + WINDOW, markets)


async def test_the_live_side(downloader: Downloader) -> None:
    now = int(time.time())
    start = now - 3600 - (now % 900)  # a quarter-hour boundary about an hour ago
    end = start + WINDOW
    markets: list[Market] = []
    async for page in downloader.live_data.markets(
        status="settled",
        mve_filter="exclude",
        min_settled_ts=start,
        max_settled_ts=end + 900,
        limit=1000,
    ):
        markets += page.items
    await check_side(downloader, "live", start, end, markets)
