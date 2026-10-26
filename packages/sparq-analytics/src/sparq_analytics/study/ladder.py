"""The benchmark ladder (CLAUDE.md, Research): what each step of sophistication buys.

1. the raw price taken as the probability of yes;
2. a bias correction by price band (the band's average miss added to the price), fitted on the
   fit part only;
3. a naive maker strategy: rest at the price of every trade, under both fill rules.

Steps 1 and 2 are scored by Brier score and log loss, weighted by contracts. Step 3 is the maker
edge per contract (millionths of a dollar) with the conservative maker fee. The intervals for step
3 come from the segment evaluation; the ladder gives the whole-test point values.
"""

from dataclasses import dataclass

import polars as pl
from sparq_core.fixedpoint import E6

from sparq_analytics.study.calibration import weight

_EPSILON = 1e-6  # probabilities are kept this far from 0 and 1 so the log loss is finite


@dataclass(frozen=True, slots=True)
class Scores:
    brier: float
    log_loss: float
    contracts_e2: int


@dataclass(frozen=True, slots=True)
class Ladder:
    raw: Scores
    band_corrected: Scores
    maker_optimistic_e6: float
    maker_pessimistic_e6: float


def scores(frame: pl.LazyFrame, probability: pl.Expr) -> Scores:
    q = probability.clip(_EPSILON, 1 - _EPSILON)
    y = pl.col("y").cast(pl.Float64)
    weight_ = pl.col("c").cast(pl.Float64) * weight(frame)
    row = (
        frame.select(
            (weight_ * (q - y) ** 2).sum().alias("brier"),
            (-weight_ * (y * q.log() + (1 - y) * (1 - q).log())).sum().alias("loss"),
            weight_.sum().alias("weight"),
            pl.col("c").sum().alias("contracts"),
        )
        .collect()
        .row(0, named=True)
    )
    if not row["weight"]:
        return Scores(float("nan"), float("nan"), 0)
    return Scores(row["brier"] / row["weight"], row["loss"] / row["weight"], int(row["contracts"]))


def fit_band_gaps(fit: pl.LazyFrame) -> dict[int, float]:
    """Each price band's average miss on the fit part: the weighted yes rate minus the weighted
    mean price (weights are contracts times the sampling weight). Adding it to a price keeps the
    resolution inside the band."""
    scaled = pl.col("c").cast(pl.Float64) * weight(fit)
    table = (
        fit.group_by("price_band")
        .agg(
            (pl.col("y").cast(pl.Float64) * scaled).sum().alias("yes"),
            (pl.col("p").cast(pl.Float64) / E6 * scaled).sum().alias("price"),
            scaled.sum().alias("c"),
        )
        .collect()
    )
    return {int(b): float((y - p) / c) for b, y, p, c in table.iter_rows() if c > 0}


def ladder(fit: pl.LazyFrame, test: pl.LazyFrame) -> Ladder:
    price = pl.col("p").cast(pl.Float64) / E6
    gap = pl.col("price_band").replace_strict(
        fit_band_gaps(fit), default=0.0, return_dtype=pl.Float64
    )
    w = weight(test)
    sums = (
        test.select(
            (pl.col("e_mk_opt_cons").cast(pl.Float64) * w).sum().alias("opt"),
            (pl.col("c").cast(pl.Float64) * w).sum().alias("c"),
            (pl.col("e_mk_pess_cons").cast(pl.Float64) * w).sum().alias("pess"),
            (pl.col("c_pess").cast(pl.Float64) * w).sum().alias("c_pess"),
        )
        .collect()
        .row(0, named=True)
    )
    nan = float("nan")
    return Ladder(
        raw=scores(test, price),
        band_corrected=scores(test, price + gap),
        maker_optimistic_e6=sums["opt"] / sums["c"] if sums["c"] else nan,
        maker_pessimistic_e6=sums["pess"] / sums["c_pess"] if sums["c_pess"] else nan,
    )
