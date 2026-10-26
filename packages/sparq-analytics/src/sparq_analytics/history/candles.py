"""Candles for a chosen set of settled markets (not part of the default download).

One hourly request covers a market's whole life up to 208 days (Kalshi allows 5,000 candles per
request, judged by the requested range), so the cost is one request per market. All 2.2 million
markets would take days, so this runs on a sample or a series prefix chosen by the study.
"""

import random
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow as pa
from sparq_core.markets import series_ticker_of
from sparq_core.models import Candlestick

from sparq_analytics.history.datadir import write_table_atomic
from sparq_analytics.history.downloader import Downloader
from sparq_analytics.history.schemas import candles_table

PERIOD_MINUTES = 60
MAX_CANDLES = 5_000  # Kalshi's limit per request, judged by (range / interval)
SAMPLE_SEED = 1
MARKETS_PER_FILE = 200


def choose_markets(
    data_root: Path, *, series_prefix: str | None, sample: int | None
) -> list[dict[str, Any]]:
    """Yes/no markets with an open and a settlement time, optionally of one series, sampled."""
    root = data_root / "markets"
    files = sorted(root.rglob("*.parquet")) if root.exists() else []
    if not files:
        return []
    columns = ["ticker", "series_ticker", "label", "open_time", "settlement_ts"]
    markets = pl.concat([pl.read_parquet(path, columns=columns) for path in files]).filter(
        pl.col("label").is_in(["yes", "no"])
        & pl.col("open_time").is_not_null()
        & pl.col("settlement_ts").is_not_null()
    )
    if series_prefix is not None:
        markets = markets.filter(
            pl.col("series_ticker").fill_null("").str.starts_with(series_prefix)
        )
    rows = markets.sort("ticker", maintain_order=True).to_dicts()  # stable, as the sample needs
    if sample is not None and sample < len(rows):
        rows = random.Random(SAMPLE_SEED).sample(rows, sample)  # noqa: S311 - a reproducible sample
    return rows


def _slices(start: int, end: int) -> list[tuple[int, int]]:
    """Split [start, end] so that each slice asks for at most MAX_CANDLES hourly candles."""
    span = MAX_CANDLES * PERIOD_MINUTES * 60
    out = []
    while start < end:
        out.append((start, min(end, start + span)))
        start += span
    return out or [(start, end)]


async def download_candles(
    downloader: Downloader, *, series_prefix: str | None = None, sample: int | None = None
) -> int:
    """Hourly candles for the chosen markets, resumable; returns the number of markets fetched."""
    directory = downloader.dir
    done_path = directory.root / "candles" / "_done.txt"
    done = set(done_path.read_text(encoding="utf-8").split()) if done_path.exists() else set()
    markets = [
        m
        for m in choose_markets(directory.root, series_prefix=series_prefix, sample=sample)
        if m["ticker"] not in done
    ]
    _, markets_cutoff = await downloader.cutoffs()
    parts = (
        len(list((directory.root / "candles").glob("part-*.parquet")))
        if (directory.root / "candles").exists()
        else 0
    )
    tables: list[pa.Table] = []
    finished: list[str] = []
    fetched = 0

    def flush() -> None:
        nonlocal parts, tables, finished
        if not finished:
            return
        parts += 1
        if tables:
            write_table_atomic(
                pa.concat_tables(tables), directory.root / "candles" / f"part-{parts:06d}.parquet"
            )
        done_path.parent.mkdir(parents=True, exist_ok=True)
        with done_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(finished) + "\n")
        tables, finished = [], []

    for market in markets:
        ticker = market["ticker"]
        opened = int(market["open_time"].timestamp())
        settled = int(market["settlement_ts"].timestamp())
        historical = settled < markets_cutoff
        candles: list[Candlestick] = []
        for start, end in _slices(opened, settled):
            if historical:
                candles += await downloader.live_data.historical_candlesticks(
                    ticker, start_ts=start, end_ts=end, period_interval=PERIOD_MINUTES
                )
            else:
                series = market["series_ticker"] or series_ticker_of(ticker) or ticker
                candles += await downloader.live_data.candlesticks(
                    series, ticker, start_ts=start, end_ts=end, period_interval=PERIOD_MINUTES
                )
            downloader.live_rate.update()
        if candles:
            tables.append(candles_table(ticker, PERIOD_MINUTES, candles))
        finished.append(ticker)
        fetched += 1
        if len(finished) >= MARKETS_PER_FILE:
            flush()
            downloader.say(f"candles: {fetched:,} markets fetched")
    flush()
    return fetched
