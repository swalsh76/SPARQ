"""S3.1's history reading: the certainty dump, the 99 regime and the price-only risk (PLAN §6.3).

A market closing on or before the cut (M3) is read with its result; its **end** is the earlier of
its close and its settlement, since some settle early. Every trade at 97 cents or more for either
side is kept with that side's price, whether that side won, and who took: the taker bought the
**dead side** (the other side, so a bid resting on the near-certain side was filled: a holder
exiting, or a lottery buyer, which the tape cannot tell apart) or **lifted** the near-certain side.
A trade without a taker side is counted and takes part in neither.

A side's **99 regime** starts at its first trade at 99 cents after which that side never printed
below 97 again before the end. In the winning side's regime a 99-cent bid collects the dump; the
losing side's is a **false regime**, where that bid loses 99 cents a contract. The **payout rate**
is true regimes over all regimes, and a 99-cent bid breaks even at a reversal rate of 1.0%. The
**any-time reversal** is looser: the losing side traded at 99 at any moment.

Money is integer millionths of a dollar; sizes are hundredths of a contract.
"""

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from sparq_core.fixedpoint import E6

from sparq_analytics.study.dataset import scan_markets

CUT = date(2026, 10, 10)  # M3: markets closing after this day are held out
HISTORY_START = date(2026, 8, 7)
CENTS = 10_000
NEAR_E6 = 97 * CENTS  # the near-certain band
CERTAIN_E6 = 99 * CENTS
MS_PER_HOUR = 3_600_000


def hours(ms: pl.Expr) -> pl.Expr:
    """Whole seconds over 3,600. Polars divides a column by a literal through the reciprocal, so
    the millisecond count over 3,600,000 gave 1.9999999999999998 for two hours; whole seconds
    over 3,600 give whole hours exactly, and fractions to a second."""
    return (ms // 1000) / 3600


MARKET_COLUMNS = ["ticker", "series_ticker", "result", "close_time", "settlement_ts"]
TOP_SERIES = 15


class SurveyDataError(Exception):
    """The data folders lack something the survey needs, or the request reads held-out days."""


def cut_markets(kalshi: Path, cut: date = CUT) -> pl.DataFrame:
    """The settled binary markets closing on or before ``cut`` (UTC), with their end (the earlier
    of close and settlement), category and fee terms."""
    if cut > CUT:
        raise SurveyDataError(
            f"{cut} is past the cut {CUT}: the days from {CUT + timedelta(days=1)} are held out"
            " (M3)"
        )
    end_of_cut = datetime.combine(cut + timedelta(days=1), time.min, tzinfo=UTC)
    markets = (
        scan_markets(kalshi, MARKET_COLUMNS)
        .filter(pl.col("result").is_in(["yes", "no"]))
        .filter(pl.col("close_time") < end_of_cut)
        .collect()
    )
    if markets.is_empty():
        raise SurveyDataError(f"no settled market closes on or before {cut}")
    series = pl.read_parquet(
        kalshi / "series.parquet", columns=["ticker", "category", "fee_type", "fee_multiplier_e6"]
    ).rename({"ticker": "series_ticker", "fee_multiplier_e6": "multiplier_e6"})
    return (
        markets.join(series, on="series_ticker", how="left")
        .with_columns(
            pl.col("category").fill_null("unknown"),
            close_ms=pl.col("close_time").dt.epoch("ms"),
            settlement_ms=pl.col("settlement_ts").dt.epoch("ms"),
        )
        .with_columns(
            end_ms=pl.min_horizontal(
                "close_ms", pl.col("settlement_ms").fill_null(pl.col("close_ms"))
            )
        )
        .select(
            "ticker",
            "series_ticker",
            "category",
            "result",
            "close_ms",
            "settlement_ms",
            "end_ms",
            "fee_type",
            "multiplier_e6",
        )
    )


def trade_files(kalshi: Path, first: date, last: date) -> list[Path]:
    """The trade files of the days from ``first`` to ``last`` (inclusive) that exist."""
    paths = []
    day = first
    while day <= last:
        path = kalshi / "trades" / f"date={day}" / "trades.parquet"
        if path.exists():
            paths.append(path)
        day += timedelta(days=1)
    if not paths:
        raise SurveyDataError(f"no trades from {first} to {last} under {kalshi / 'trades'}")
    return paths


def _scan(files: list[Path]) -> pl.LazyFrame:
    return pl.scan_parquet(files, hive_partitioning=False).select(
        pl.col("ticker"),
        pl.col("created_time").dt.epoch("ms").alias("t_ms"),
        pl.col("yes_price_e6").cast(pl.Int64),
        pl.col("count_e2"),
        pl.col("taker_side"),
    )


def side_extremes(files: list[Path], markets: pl.DataFrame) -> pl.DataFrame:
    """Per market: the last moment each side printed below 97 cents before the market's end
    (null when it never did)."""
    yes = pl.col("yes_price_e6")
    return (
        _scan(files)
        .join(markets.select("ticker", "end_ms").lazy(), on="ticker")
        .filter(pl.col("t_ms") <= pl.col("end_ms"))
        .group_by("ticker")
        .agg(
            pl.col("t_ms").filter(yes < NEAR_E6).max().alias("last_yes_below_ms"),
            pl.col("t_ms").filter(yes > E6 - NEAR_E6).max().alias("last_no_below_ms"),
            pl.len().alias("trades"),
        )
        .collect()
    )


def near_certain_trades(files: list[Path], markets: pl.DataFrame) -> pl.DataFrame:
    """Every trade at 97 cents or more for either side of a cut market, before its end, priced on
    that side."""
    yes = pl.col("yes_price_e6")
    side_price = pl.when(pl.col("side") == "yes").then(yes).otherwise(E6 - yes)
    return (
        _scan(files)
        .filter((yes >= NEAR_E6) | (yes <= E6 - NEAR_E6))
        .join(
            markets.select(
                "ticker", "series_ticker", "category", "result", "end_ms", "settlement_ms"
            ).lazy(),
            on="ticker",
        )
        .filter(pl.col("t_ms") <= pl.col("end_ms"))
        .with_columns(side=pl.when(yes >= NEAR_E6).then(pl.lit("yes")).otherwise(pl.lit("no")))
        .with_columns(side_price_e6=side_price)
        .with_columns(
            cents=(pl.col("side_price_e6") // CENTS).cast(pl.Int16),
            won=pl.col("side") == pl.col("result"),
            # null when the taker side is unknown: in neither count
            taker_bought_dead=pl.when(pl.col("taker_side").is_in(["yes", "no"])).then(
                pl.col("taker_side") != pl.col("side")
            ),
            dollars_e6=pl.col("side_price_e6") * pl.col("count_e2") // 100,
        )
        .collect()
    )


def regimes(near: pl.DataFrame, extremes: pl.DataFrame) -> pl.DataFrame:
    """Per market and side with a 99 regime: its start, whether it is true (that side won), the
    dump into resting 99-cent bids inside it, and its hours to the end and to settlement."""
    last_below = (
        pl.when(pl.col("side") == "yes")
        .then(pl.col("last_yes_below_ms"))
        .otherwise(pl.col("last_no_below_ms"))
    )
    at_99 = near.filter(pl.col("cents") >= 99)
    starts = (
        at_99.join(
            extremes.select("ticker", "last_yes_below_ms", "last_no_below_ms"),
            on="ticker",
            how="left",
        )
        .filter(pl.col("t_ms") > last_below.fill_null(-1))
        .group_by("ticker", "side")
        .agg(
            pl.col("t_ms").min().alias("start_ms"),
            pl.col("won").first(),
            pl.col("end_ms").first(),
            pl.col("settlement_ms").first(),
            pl.col("category").first(),
            pl.col("series_ticker").first(),
        )
    )
    dead = pl.col("taker_bought_dead")
    inside = (
        at_99.join(starts.select("ticker", "side", "start_ms"), on=["ticker", "side"])
        .filter(pl.col("t_ms") >= pl.col("start_ms"))
        .group_by("ticker", "side")
        .agg(
            pl.col("count_e2").filter(dead).sum().alias("dump_e2"),
            pl.col("dollars_e6").filter(dead).sum().alias("dump_e6"),
            pl.col("count_e2").filter(~dead).sum().alias("lifted_e2"),
        )
    )
    return (
        starts.join(inside, on=["ticker", "side"], how="left")
        .with_columns(pl.col("dump_e2", "dump_e6", "lifted_e2").fill_null(0))
        .with_columns(
            hours_to_end=hours(pl.col("end_ms") - pl.col("start_ms")),
            settle_ms=pl.col("settlement_ms").fill_null(pl.col("end_ms")) - pl.col("start_ms"),
        )
        .sort("ticker", "side")
    )


def _rows(frame: pl.DataFrame) -> list[dict[str, Any]]:
    return frame.to_dicts()


def summarize(
    markets: pl.DataFrame,
    near: pl.DataFrame,
    regs: pl.DataFrame,
    first: date,
    cut: date,
    trade_days: int,
) -> dict[str, Any]:
    """S3.1's history figures: per category the regimes, the payout rate, the dump and the
    reversals; the winning side's trades by level; the series with the biggest dump."""
    true = pl.col("won")
    by_category = (
        regs.group_by("category")
        .agg(
            true.sum().alias("true_regimes"),
            (~true).sum().alias("false_regimes"),
            pl.col("dump_e2").filter(true).sum().alias("dump_e2"),
            pl.col("dump_e6").filter(true).sum().alias("dump_e6"),
            pl.col("lifted_e2").filter(true).sum().alias("lifted_e2"),
            pl.col("dump_e2").filter(~true).sum().alias("false_dump_e2"),
            pl.col("hours_to_end").filter(true).median().alias("hours_to_end_median"),
            pl.col("hours_to_end").filter(true).quantile(0.9).alias("hours_to_end_p90"),
            pl.col("settle_ms").filter(true).median().cast(pl.Int64).alias("settle_ms_median"),
        )
        .with_columns(
            payout=pl.col("true_regimes") / (pl.col("true_regimes") + pl.col("false_regimes")),
            # one cent a contract to every maker in the queue, over the days of trades read
            makers_gain_a_day_e6=pl.col("dump_e2") * 100 // trade_days,
        )
    )
    dead = pl.col("taker_bought_dead")
    any_time = (
        near.filter((pl.col("cents") >= 99) & ~pl.col("won") & dead)
        .group_by("category")
        .agg(
            pl.col("ticker").n_unique().alias("reversal_markets"),
            pl.col("count_e2").sum().alias("reversal_e2"),
        )
    )
    # the reactive bidder's base: every winning side that printed 99 to a resting bid (a market
    # whose both sides did counts once on each side, as the bidder would have bid on both)
    winners = (
        near.filter((pl.col("cents") >= 99) & pl.col("won") & dead)
        .group_by("category")
        .agg(pl.col("ticker").n_unique().alias("winning_99_markets"))
    )
    counts = markets.group_by("category").agg(pl.len().alias("markets"))
    categories = (
        counts.join(by_category, on="category", how="left")
        .join(any_time, on="category", how="left")
        .join(winners, on="category", how="left")
        .with_columns(
            pl.exclude(
                "category",
                "payout",
                "hours_to_end_median",
                "hours_to_end_p90",
                "settle_ms_median",
                "reactive_payout",
            ).fill_null(0)
        )
        .with_columns(
            # null, not 0/0, where no side ever printed 99 to a resting bid
            reactive_payout=pl.when(
                pl.col("winning_99_markets") + pl.col("reversal_markets") > 0
            ).then(
                pl.col("winning_99_markets")
                / (pl.col("winning_99_markets") + pl.col("reversal_markets"))
            )
        )
        .sort("category")
    )
    # what a reversal dumps into the losing side's 99-cent bids, per market; and what a false
    # regime dumps: the loss side's counterparts of the live fills
    reversal_dumps = (
        near.filter((pl.col("cents") >= 99) & ~pl.col("won") & dead)
        .group_by("category", "ticker")
        .agg(pl.col("count_e2").sum().alias("e2"))
        .group_by("category")
        .agg(pl.col("e2").sort().alias("reversal_dumps_e2"))
    )
    false_dumps = (
        regs.filter(~true)
        .group_by("category")
        .agg(pl.col("dump_e2").sort().alias("false_regime_dumps_e2"))
    )
    rows = _rows(
        categories.join(reversal_dumps, on="category", how="left").join(
            false_dumps, on="category", how="left"
        )
    )
    for row in rows:
        for key in ("reversal_dumps_e2", "false_regime_dumps_e2"):
            row[key] = row[key] or []
    levels = (
        near.filter(pl.col("won") & dead.is_not_null())
        .join(regs.select("ticker", "side", "start_ms"), on=["ticker", "side"], how="left")
        .with_columns(
            cents=pl.col("cents").clip(upper_bound=99),
            in_regime=(pl.col("t_ms") >= pl.col("start_ms")).fill_null(False),
        )
        .group_by("category", "cents", "taker_bought_dead", "in_regime")
        .agg(
            pl.len().alias("trades"),
            pl.col("count_e2").sum().alias("contracts_e2"),
            pl.col("dollars_e6").sum().alias("dollars_e6"),
        )
        .sort("category", "cents", "taker_bought_dead", "in_regime")
    )
    top = (
        regs.filter(true)
        .group_by("series_ticker", "category")
        .agg(
            pl.len().alias("regimes"),
            pl.col("dump_e2").sum().alias("dump_e2"),
            pl.col("dump_e6").sum().alias("dump_e6"),
        )
        .sort("dump_e2", descending=True)
        .head(TOP_SERIES)
    )
    return {
        "first": str(first),
        "cut": str(cut),
        "days": (cut - first).days + 1,
        "trade_days": trade_days,
        "markets": markets.height,
        "trades_near_certain": near.height,
        "side_unknown_trades": near.filter(dead.is_null()).height,
        "regimes": regs.height,
        "by_category": {row["category"]: row for row in rows},
        "levels": _rows(levels),
        "top_series": _rows(top),
    }


def history(
    kalshi: Path, cut: date = CUT, first: date = HISTORY_START
) -> tuple[dict[str, Any], pl.DataFrame]:
    """The whole history reading: the summary and the regimes."""
    markets = cut_markets(kalshi, cut)
    files = trade_files(kalshi, first, cut)
    extremes = side_extremes(files, markets)
    near = near_certain_trades(files, markets)
    regs = regimes(near, extremes)
    return summarize(markets, near, regs, first, cut, len(files)), regs
