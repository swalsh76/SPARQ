"""The edge of every cell of the grid, for choosing segments on the validate part (D17).

A cell is a price band × time band × category × taker side. For each measure of
:data:`~sparq_analytics.study.edge.MEASURES` the table gives the edge per contract (millionths of a
dollar), its cluster-robust standard error over events (the same one the studentized interval is
built on) and their ratio. There is no bootstrap here: this is for looking, on the part that may
be looked at. The verdict itself uses the intervals in :mod:`verdict`.

With some thousand cells, the biggest ratios are partly luck. That is why the pre-registered
segments are tested at the corrected tail, on the test part, which none of this saw.
"""

import polars as pl

from sparq_analytics.study.bootstrap import ratio_and_se_exprs
from sparq_analytics.study.edge import MEASURES

CELL = ["price_band", "tleft_band", "category", "taker_yes"]
SHOWN = ("taker", "mk_opt_cons", "mk_pess_cons")


def cell_table(frame: pl.LazyFrame, minimum_events: int = 30) -> pl.DataFrame:
    """One row per cell with at least ``minimum_events`` events, with the edge, standard error and
    ratio of the measures in :data:`SHOWN`, largest absolute ratio of the taker first."""
    columns = sorted({name for measure in SHOWN for name in MEASURES[measure]})
    per_event = (
        frame.group_by([*CELL, "event_ticker"])
        .agg(
            pl.len().alias("trades"),
            *[pl.col(c).cast(pl.Int128).sum().cast(pl.Float64).alias(c) for c in columns],
        )
        .collect()
    )
    outputs: list[pl.Expr] = [pl.len().alias("events"), pl.col("trades").sum().alias("trades")]
    for measure in SHOWN:
        numerator, denominator = MEASURES[measure]
        ratio, se = ratio_and_se_exprs(pl.col(numerator), pl.col(denominator))
        outputs += [ratio.alias(f"{measure}_e6"), se.alias(f"{measure}_se_e6")]
    table = per_event.group_by(CELL).agg(outputs).filter(pl.col("events") >= minimum_events)
    for measure in SHOWN:
        table = table.with_columns(
            (pl.col(f"{measure}_e6") / pl.col(f"{measure}_se_e6")).alias(f"{measure}_z")
        )
    return table.sort("taker_z", descending=True, nulls_last=True)
