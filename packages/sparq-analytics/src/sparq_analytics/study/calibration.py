"""Calibration: how often the yes outcome happened at each price, by band, time and category.

Weighted by contracts, so a cell reads as "of the contracts bought at about this price, this share
settled yes". ``gap`` is the observed yes rate minus the mean price: positive means the market
under-priced yes. The columns that cluster the intervals by event live in :mod:`bootstrap`.
"""

import polars as pl

from sparq_analytics.study.config import StudyConfig

E6 = 1_000_000


def weight(frame: pl.LazyFrame) -> pl.Expr:
    """The sampling weight, or 1.0 for a frame that has none."""
    return pl.col("w") if "w" in frame.collect_schema().names() else pl.lit(1.0)


def calibration_table(frame: pl.LazyFrame, by: list[str]) -> pl.DataFrame:
    """One row per cell of ``by`` (columns of the banded frame, such as ``price_band``).

    The rates are weighted by contracts and by the sampling weight, so a table pooled over
    categories sampled at different shares is still unbiased; ``trades``, ``events`` and
    ``contracts_e2`` are the counts actually loaded."""
    w = weight(frame)
    scaled = pl.col("c").cast(pl.Float64) * w
    return (
        frame.group_by(by)
        .agg(
            pl.len().alias("trades"),
            pl.col("event_ticker").n_unique().alias("events"),
            pl.col("c").sum().alias("contracts_e2"),
            scaled.sum().alias("_weight"),
            (pl.col("p").cast(pl.Float64) * scaled).sum().alias("_price_weight"),
            (pl.col("y").cast(pl.Float64) * scaled).sum().alias("_yes_weight"),
        )
        .with_columns(
            (pl.col("_price_weight") / pl.col("_weight") / E6).alias("mean_price"),
            (pl.col("_yes_weight") / pl.col("_weight")).alias("yes_rate"),
        )
        .with_columns((pl.col("yes_rate") - pl.col("mean_price")).alias("gap"))
        .drop("_weight", "_price_weight", "_yes_weight")
        .sort(by)
        .collect()
    )


def band_labels(config: StudyConfig) -> list[str]:
    """Readable names of the price bands, in cents, in band-number order."""
    edges = [e // 10_000 for e in config.price_edges_e6]
    return [f"{lo}-{hi}c" for lo, hi in zip(edges, edges[1:], strict=False)]
