"""The after-fee edge of every trade, for the taker and for the maker on the other side.

All edge columns are exact integers in hundredths of a millionth of a dollar (count in hundredths
× price in millionths); dividing a sum by the contracts in hundredths gives millionths of a dollar
per contract. The taker pays the fee on the whole trade, so a fee in millionths is scaled by 100.

The taker's gross edge is what the contracts it bought are worth at settlement minus what it paid.
The maker is the counterparty and has the opposite gross edge. Two fill rules say how many of the
trades a resting order would have taken part in (D6, D9):

* optimistic: every trade, as if the order were first in the queue every time;
* pessimistic: a trade fills the order only if a *later* second saw the price trade strictly
  through its level, so the queue ahead of it must have been cleared. Trades inside one second have
  no known order and never count as later.

The maker fee comes in three versions: none (an upper bound), the schedule's nominal share, and the
conservative share the verdict uses (D13).
"""

import polars as pl

MEASURES = {
    # name: (numerator column, denominator column)
    "taker": ("e_taker", "c"),
    "mk_opt_none": ("e_mk_opt_none", "c"),
    "mk_opt_nom": ("e_mk_opt_nom", "c"),
    "mk_opt_cons": ("e_mk_opt_cons", "c"),
    "mk_pess_none": ("e_mk_pess_none", "c_pess"),
    "mk_pess_nom": ("e_mk_pess_nom", "c_pess"),
    "mk_pess_cons": ("e_mk_pess_cons", "c_pess"),
}
_ONE_E6 = 1_000_000


def _later_extremes(frame: pl.LazyFrame) -> pl.LazyFrame:
    """For each (market, second): the highest and lowest yes price of any strictly later second."""
    by_second = (
        frame.group_by("ticker", pl.col("t").dt.truncate("1s").alias("sec"))
        .agg(pl.col("p").max().alias("hi"), pl.col("p").min().alias("lo"))
        .sort("ticker", "sec")
    )
    return by_second.select(
        "ticker",
        "sec",
        pl.col("hi").cum_max(reverse=True).shift(-1).over("ticker").alias("later_hi"),
        pl.col("lo").cum_min(reverse=True).shift(-1).over("ticker").alias("later_lo"),
    )


def add_edges(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Add ``filled_pess``, ``c_pess`` and the ``e_*`` columns of :data:`MEASURES`."""
    sec = pl.col("t").dt.truncate("1s").alias("sec")
    frame = frame.with_columns(sec).join(_later_extremes(frame), on=["ticker", "sec"], how="left")
    through = (pl.col("taker_yes") & (pl.col("later_hi") > pl.col("p"))) | (
        ~pl.col("taker_yes") & (pl.col("later_lo") < pl.col("p"))
    )
    paid = pl.when(pl.col("taker_yes")).then(pl.col("p")).otherwise(_ONE_E6 - pl.col("p"))
    won = pl.when(pl.col("taker_yes")).then(pl.col("y")).otherwise(1 - pl.col("y"))
    gross = pl.col("c") * (won.cast(pl.Int64) * _ONE_E6 - paid)
    frame = frame.with_columns(
        through.fill_null(False).alias("filled_pess"),
        gross.alias("gross"),
    )

    def pess(column: str) -> pl.Expr:
        return pl.when(pl.col("filled_pess")).then(pl.col(column)).otherwise(0)

    frame = frame.with_columns(
        (pl.col("gross") - 100 * pl.col("tk_fee")).alias("e_taker"),
        (-pl.col("gross")).alias("e_mk_opt_none"),
        (-pl.col("gross") - 100 * pl.col("mk_fee_nom")).alias("e_mk_opt_nom"),
        (-pl.col("gross") - 100 * pl.col("mk_fee_cons")).alias("e_mk_opt_cons"),
        pess("c").alias("c_pess"),
    )
    return frame.with_columns(
        pess("e_mk_opt_none").alias("e_mk_pess_none"),
        pess("e_mk_opt_nom").alias("e_mk_pess_nom"),
        pess("e_mk_opt_cons").alias("e_mk_pess_cons"),
    ).drop("sec", "later_hi", "later_lo", "gross")
