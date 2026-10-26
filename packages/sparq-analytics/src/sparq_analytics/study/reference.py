"""D10: which market time is nearest to the market's last trade, by category.

For every market that closed inside the downloaded days, the gap in seconds between its last trade
and each candidate end time. The field with the smaller and steadier gap marks better when the
market really ended. ``close_time`` won in all 11 categories on the first real day (K0.5.5); the
run repeats the measurement on everything downloaded.
"""

from pathlib import Path

import polars as pl

from sparq_analytics.study.config import StudyConfig
from sparq_analytics.study.dataset import scan_markets, tag_trades

FIELDS = ("close_time", "expected_expiration_time")


def measure_reference(root: Path) -> pl.DataFrame:
    tagged = tag_trades(root, StudyConfig())
    last_trade = tagged.group_by("ticker").agg(
        pl.col("category").first(), pl.col("t").max().alias("last_trade")
    )
    markets = scan_markets(root, ["ticker", *FIELDS])
    bounds = tagged.select(pl.col("t").min().alias("first"), pl.col("t").max().alias("last"))
    gaps = (
        last_trade.join(markets, on="ticker")
        .join(bounds, how="cross")
        .filter(pl.col("close_time").is_between(pl.col("first"), pl.col("last")))
    )
    aggregates = []
    for field in FIELDS:
        gap = (pl.col(field) - pl.col("last_trade")).dt.total_seconds()
        short = field.split("_")[0]
        aggregates += [
            gap.quantile(0.1).alias(f"{short}_p10"),
            gap.median().alias(f"{short}_median"),
            gap.quantile(0.9).alias(f"{short}_p90"),
            pl.col(field).is_null().mean().alias(f"{short}_null"),
        ]
    return (
        gaps.group_by("category")
        .agg(pl.len().alias("markets"), *aggregates)
        .with_columns(
            pl.when(pl.col("close_median").abs() <= pl.col("expected_median").abs())
            .then(pl.lit("close_time"))
            .otherwise(pl.lit("expected_expiration_time"))
            .alias("nearer"),
        )
        .sort("markets", descending=True)
        .collect()
    )
