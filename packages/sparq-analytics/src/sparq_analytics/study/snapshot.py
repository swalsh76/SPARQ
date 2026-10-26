"""What the downloaded data holds, and the window and sample the run should use (D16, D17).

The snapshot is the manifest of a run: the days of trades present, how many trades have no market
record yet (markets that had not settled when the market listing ran), and the exclusion counts.
The window ends at the newest day whose share of trades lacking a market record is no more than
0.5 percentage points above the baseline share (D17 as amended), and the
sample share keeps about ``ceiling`` trades.
"""

import datetime as dt
from pathlib import Path
from typing import Any, cast

import polars as pl

from sparq_analytics.study.config import StudyConfig
from sparq_analytics.study.dataset import (
    StudyDataError,
    scan_markets,
    tag_trades,
    used,
)
from sparq_analytics.study.sample import DEFAULT_CEILING, CategoryStat, plan_shares

EDGE_MARGIN = (
    0.005  # D17, amended 2026-10-25: points above the baseline share still counted as settled
)
RECENT_DAYS = 14  # the newest days, left out of the baseline
MIN_DAYS = 4  # fewer days than this cannot tell a baseline from an edge
MAX_BASELINE = 0.05  # a higher usual share means missing market files, not open markets


def day_exclusions(root: Path) -> pl.DataFrame:
    """Trades per day and exclusion reason (``used`` for the kept ones), in one pass."""
    return (
        tag_trades(root, StudyConfig())
        .group_by(pl.col("t").dt.date().alias("day"), pl.col("exclusion").fill_null("used"))
        .agg(pl.len().alias("trades"))
        .collect()
    )


def days_table(root: Path, counts: pl.DataFrame | None = None) -> pl.DataFrame:
    """Per day of trades: trades, trades with no market record, and that share."""
    counts = day_exclusions(root) if counts is None else counts
    return (
        counts.group_by("day")
        .agg(
            pl.col("trades").sum().cast(pl.UInt32).alias("trades"),
            pl.col("trades")
            .filter(pl.col("exclusion") == "market_missing")
            .sum()
            .cast(pl.UInt32)
            .alias("market_missing"),
        )
        .with_columns((pl.col("market_missing") / pl.col("trades")).alias("missing_share"))
        .sort("day")
    )


def baseline_share(days: pl.DataFrame) -> float | None:
    """The usual share of trades with no market record, from the days before the newest ones.

    A steady share of trades (about 1% on the real download) belongs to markets that are still open
    and so have no outcome, whatever the day; that is not a gap in the data. The newest days are
    left out of the baseline because they are the ones the unsettled edge distorts. None when there
    are too few days to tell a baseline from an edge.
    """
    count = days.height
    if count < MIN_DAYS:
        return None
    older = days.sort("day").head(count - min(RECENT_DAYS, count // 2))
    return float(cast(float, older["missing_share"].median()))


def window_for(days: pl.DataFrame) -> tuple[str, str] | None:
    """The first and one past the last day of the window, or None when no day qualifies.

    The window starts at the oldest day present (an event that opened on or after it has all its
    trades) and ends after the newest day whose missing share is no more than ``EDGE_MARGIN`` above
    the baseline (D17 as amended on 2026-10-25).
    """
    baseline = baseline_share(days)
    if baseline is None or baseline > MAX_BASELINE:
        return None
    ready = days.filter(pl.col("missing_share") <= baseline + EDGE_MARGIN)
    if ready.is_empty():
        return None
    first = cast(dt.date, days["day"].min())  # the column is dates, so these are dates
    last = cast(dt.date, ready["day"].max())
    return first.isoformat(), (last + dt.timedelta(days=1)).isoformat()


def propose_split(
    root: Path, window_start: str, window_end: str, fractions: tuple[float, float, float]
) -> tuple[str, str] | None:
    """Fixed fit and validate boundary dates (D12), from the downloaded markets.

    The markets are complete for the whole window long before its trades are, so the boundaries
    taken from them are final today and do not move as trade days arrive. They are the close-time
    quantiles, by event count, of the settled events that traded and lie inside the window, rounded
    down to a UTC date. None when the events are too few to give two distinct dates.
    """
    columns = ["ticker", "event_ticker", "label", "volume_e2", "open_time", "created_time"]
    columns += ["close_time", "expected_expiration_time"]
    try:
        markets = scan_markets(root, columns)
    except StudyDataError:
        return None
    start = dt.datetime.fromisoformat(window_start).replace(tzinfo=dt.UTC)
    end = dt.datetime.fromisoformat(window_end).replace(tzinfo=dt.UTC)
    closes = (
        markets.filter(pl.col("label").is_in(["yes", "no"]) & (pl.col("volume_e2") > 0))
        .group_by("event_ticker")
        .agg(
            pl.coalesce("open_time", "created_time").min().alias("ev_open"),
            pl.coalesce("close_time", "expected_expiration_time").max().alias("ev_close"),
        )
        .filter((pl.col("ev_open") >= start) & (pl.col("ev_close") < end))
        .sort("ev_close")
        .select("ev_close")
        .collect()["ev_close"]
    )
    n = closes.len()
    if n < 3:
        return None
    first, second, _ = fractions
    fit = cast(dt.datetime, closes[min(n - 1, int(first * n))]).date()
    validate = cast(dt.datetime, closes[min(n - 1, int((first + second) * n))]).date()
    if fit >= validate:
        return None
    return fit.isoformat(), validate.isoformat()


def category_stats(root: Path, config: StudyConfig) -> list[CategoryStat]:
    """Per category: the events and trades the window would use, before any sampling."""
    # one row per event first (a few hundred thousand), then the totals: counting distinct events
    # over every trade at once makes the engine hold far more than this does
    per_event = (
        used(tag_trades(root, config))
        .group_by("category", "event_ticker")
        .agg(pl.len().alias("trades"))
        .collect()
    )
    table = per_event.group_by("category").agg(
        pl.len().alias("events"), pl.col("trades").sum().alias("trades")
    )
    return [CategoryStat(str(c), int(e), int(t)) for c, e, t in table.iter_rows()]


def build_snapshot(
    root: Path,
    ceiling: int = DEFAULT_CEILING,
    window_start: str | None = None,
    window_end: str | None = None,
) -> dict[str, Any]:
    """The manifest; a window given by the caller is used as it stands, else the one proposed."""
    counts = day_exclusions(root)
    days = days_table(root, counts)
    suggestion: dict[str, Any] = {"window_start": None, "window_end": None}
    window = (window_start, window_end) if window_start and window_end else window_for(days)
    baseline = baseline_share(days)
    if window is not None:
        config = StudyConfig(window_start=window[0], window_end=window[1])
        stats = category_stats(root, config)
        trades = sum(stat.trades for stat in stats)
        split = propose_split(root, window[0], window[1], config.fractions)
        base, overrides = plan_shares(stats, ceiling)
        expected = sum(stat.trades * overrides.get(stat.category, base) for stat in stats)
        suggestion = {
            "baseline_missing_share": baseline,
            "missing_limit": None if baseline is None else baseline + EDGE_MARGIN,
            "window_start": window[0],
            "window_end": window[1],
            "fit_end": split[0] if split else None,
            "validate_end": split[1] if split else None,
            "used_trades": trades,
            "ceiling": ceiling,
            "sample_share": base,
            "category_shares": overrides,
            "expected_trades": int(expected),
            "categories": [
                {"category": stat.category, "events": stat.events, "trades": stat.trades}
                for stat in sorted(stats, key=lambda stat: -stat.trades)
            ],
        }
    return {
        "root_name": root.name,
        "polars_version": pl.__version__,
        "trade_days": days.to_dicts(),
        "exclusions": {
            str(reason): int(n)
            for reason, n in counts.group_by("exclusion").agg(pl.col("trades").sum()).iter_rows()
        },
        "suggested": suggestion,
    }
