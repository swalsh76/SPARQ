"""One row per trade, with its outcome, fee, time to expiry and event, from the downloaded files.

Every trade is kept in the tagged frame with an ``exclusion`` reason (or null for a trade the
study uses), so the counts of what was left out are exact and nothing is dropped silently.

The fee in force at a trade is the series' fee type and multiplier. For a series with recorded fee
changes it is the latest change at or before the trade; a trade before the first recorded change
has no known fee and is excluded as ``fee_unknown``. A series with no recorded change keeps its
current fee, on the assumption (checked in K0.5.6) that the change history is complete.
"""

from collections.abc import Sequence
from pathlib import Path

import polars as pl
from sparq_core.fees import MAKER_SHARE_E2

from sparq_analytics.study.config import StudyConfig
from sparq_analytics.study.feecalc import maker_fee_expr, taker_fee_expr
from sparq_analytics.study.sample import chosen_events_by_category

MARKET_TIME_FIELDS = (
    "expected_expiration_time",
    "close_time",
    "occurrence_datetime",
    "expiration_time",
    "latest_expiration_time",
)
REASONS = (
    "bad_side",
    "market_missing",
    "not_binary",
    "fee_unknown",
    "time_unknown",
    "event_time_unknown",
    "outside_window",
    "not_sampled",
)


class StudyDataError(Exception):
    """The data folder lacks something the study needs."""


def scan_markets(
    root: Path, columns: Sequence[str], traded: pl.LazyFrame | None = None
) -> pl.LazyFrame:
    """Every downloaded market, once each (the historical row wins), with ``columns`` (which must
    include ``ticker``); with ``traded``, only the markets in it, kept before the de-duplication
    because that is far cheaper (a day touches about 20 thousand of 18 million markets)."""
    parts = []
    historical = root / "markets" / "historical"
    if historical.exists() and any(historical.glob("part-*.parquet")):
        parts.append(
            pl.scan_parquet(historical / "part-*.parquet")
            .select(columns)
            .with_columns(rank=pl.lit(0))
        )
    live = root / "markets" / "live"
    if live.exists() and any(live.glob("date=*/markets.parquet")):
        parts.append(
            pl.scan_parquet(live / "date=*" / "markets.parquet")
            .select(columns)
            .with_columns(rank=pl.lit(1))
        )
    if not parts:
        raise StudyDataError(f"no markets found under {root / 'markets'}")
    markets = pl.concat(parts)
    if traded is not None:
        markets = markets.join(traded, on="ticker", how="semi")
    return (
        markets.sort("rank", maintain_order=True)  # a join does not keep order: rank says who wins
        .unique(subset="ticker", keep="first", maintain_order=True)
        .drop("rank")
    )


def _reference_expr(config: StudyConfig, categories: list[str]) -> pl.Expr:
    """The market time the time to expiry is measured to: the first present field in the
    category's order (D10)."""
    default = pl.coalesce([pl.col(f) for f in config.reference_for("")])
    expr = default
    for category in categories:
        fields = config.reference_for(category)
        if fields != config.reference_for(""):
            expr = (
                pl.when(pl.col("category") == category)
                .then(pl.coalesce([pl.col(f) for f in fields]))
                .otherwise(expr)
            )
    return expr


def with_fees(trades: pl.LazyFrame, root: Path) -> pl.LazyFrame:
    """Add ``fee_type`` and ``multiplier_e6`` as in force at each trade (null when unknown).

    A series' own fee applies, except that a series with scheduled fee changes takes the fee of
    its latest change at or before the trade (and none before its first). There are only about a
    hundred changes, so they become one chain of conditions, applied in a single pass; splitting
    the trades by whether their series ever changed, and joining the changed ones in time order,
    made the engine buffer and sort the whole history (over 100 GB on the real download).
    """
    series = pl.scan_parquet(root / "series.parquet").select(
        pl.col("ticker").alias("series_ticker"),
        pl.col("fee_type"),
        pl.col("fee_multiplier_e6").alias("multiplier_e6"),
    )
    frame = trades.join(series, on="series_ticker", how="left")
    changes_path = root / "fee_changes.parquet"
    if not changes_path.exists():
        return frame
    changes = (
        pl.scan_parquet(changes_path)
        .select(
            "series_ticker",
            pl.col("scheduled_ts"),
            pl.col("fee_type"),
            pl.col("fee_multiplier_e6").alias("multiplier_e6"),
        )
        .sort("series_ticker", "scheduled_ts", maintain_order=True)
        .collect()
    )
    if changes.is_empty():
        return frame
    fee_type, multiplier = pl.col("fee_type"), pl.col("multiplier_e6")
    for (name,), group in changes.group_by("series_ticker", maintain_order=True):
        type_chain = pl.lit(None, dtype=changes.schema["fee_type"])
        multiplier_chain = pl.lit(None, dtype=changes.schema["multiplier_e6"])
        for row in group.iter_rows(named=True):  # oldest first, so the newest ends outermost
            reached = pl.col("t") >= row["scheduled_ts"]
            type_chain = (
                pl.when(reached)
                .then(pl.lit(row["fee_type"], dtype=changes.schema["fee_type"]))
                .otherwise(type_chain)
            )
            multiplier_chain = (
                pl.when(reached)
                .then(pl.lit(row["multiplier_e6"], dtype=changes.schema["multiplier_e6"]))
                .otherwise(multiplier_chain)
            )
        # a trade in an unknown market has a null series: the condition is null, so it falls through
        mine = pl.col("series_ticker") == name
        fee_type = pl.when(mine).then(type_chain).otherwise(fee_type)
        multiplier = pl.when(mine).then(multiplier_chain).otherwise(multiplier)
    return frame.with_columns(fee_type.alias("fee_type"), multiplier.alias("multiplier_e6"))


def _weight(config: StudyConfig) -> pl.Expr:
    """One over the share of the trade's category: what one sampled event stands for. It is 1.0 for
    a full run and the same everywhere for one share, where it cancels in every ratio; with shares
    per category it is what keeps a figure pooled over categories (the calibration table, the
    ladder) from over-counting the categories that were sampled more heavily."""
    base = 1.0 / config.sample_share
    if not config.category_shares:
        return pl.lit(base)
    mapping = {name: 1.0 / share for name, share in config.category_shares}
    return pl.col("category").replace_strict(mapping, default=base, return_dtype=pl.Float64)


def tag_trades(root: Path, config: StudyConfig, *, drop_unsampled: bool = False) -> pl.LazyFrame:
    """The study's trade frame with an ``exclusion`` column (null = used).

    With ``drop_unsampled`` the trades of events outside the sample are removed early, before the
    costly steps, instead of being marked ``not_sampled``: the used trades are exactly the same.

    Columns: ``event_ticker ticker series_ticker category t p c taker_yes y tleft_s``
    ``tk_fee mk_fee_nom mk_fee_cons ev_open ev_close w exclusion`` (``w`` the sampling weight, 1.0
    for a full run; ``p`` the yes price in
    millionths, ``c`` the count in hundredths, ``y`` 1 when the market settled yes, fees in
    millionths of a dollar for the whole trade).
    """
    trades_pattern = root / "trades" / "date=*" / "trades.parquet"
    if not any(root.glob("trades/date=*/trades.parquet")):
        raise StudyDataError(f"no trades found at {trades_pattern}")
    trades = pl.scan_parquet(trades_pattern, hive_partitioning=False).select(
        pl.col("ticker"),
        pl.col("created_time").alias("t"),
        pl.col("yes_price_e6").cast(pl.Int64).alias("p"),
        pl.col("count_e2").alias("c"),
        pl.col("taker_side"),
    )
    if not (root / "series.parquet").exists():
        raise StudyDataError(f"no series.parquet found under {root}")
    # collected now, not left lazy: a scan that fed both this list and the main join below made the
    # engine buffer (spill) the whole scan, over 100 GB, while the list was still being built
    traded = trades.select("ticker").unique().collect().lazy()
    columns = (
        "ticker", "event_ticker", "series_ticker", "label", "open_time", "created_time",
        *MARKET_TIME_FIELDS,
    )  # fmt: skip
    markets = scan_markets(root, columns, traded)
    categories = (
        pl.scan_parquet(root / "series.parquet")
        .select(pl.col("ticker").alias("series_ticker"), pl.col("category"))
        .with_columns(pl.col("category").fill_null("unknown"))
    )
    category_names = categories.select("category").unique().collect()["category"].to_list()

    events = markets.group_by("event_ticker").agg(
        pl.coalesce("open_time", "created_time").min().alias("ev_open"),
        pl.coalesce("close_time", "expected_expiration_time").max().alias("ev_close"),
    )
    market_info = (
        markets.join(categories, on="series_ticker", how="left")
        .with_columns(pl.col("category").fill_null("unknown"))
        .with_columns(_reference_expr(config, category_names).alias("ref_time"))
        .select("ticker", "event_ticker", "series_ticker", "category", "label", "ref_time")
        .join(events, on="event_ticker", how="left")
    )
    frame = trades.join(market_info, on="ticker", how="left")
    chosen: list[str] | None = None
    if config.sampled:
        listed = market_info.select("event_ticker", "category").unique(subset="event_ticker")
        listed_events = listed.collect()
        chosen = chosen_events_by_category(
            zip(listed_events["event_ticker"], listed_events["category"], strict=True),
            config.share_of,
            config.sample_seed,
        )
    if drop_unsampled and chosen is not None:
        # the fee and time arithmetic below is the expensive part (sorts and an as-of join over
        # every trade); a run that will use only the sample need not do it for the rest. The
        # unsampled trades are then absent rather than counted as ``not_sampled``.
        frame = frame.filter(pl.col("event_ticker").is_in(chosen))
    frame = with_fees(frame, root)
    nominal = pl.col("fee_type").replace_strict(MAKER_SHARE_E2, default=None, return_dtype=pl.Int64)
    conservative = (
        pl.when(nominal > 0)
        .then(pl.lit(config.verdict_maker_share_e2, dtype=pl.Int64))
        .otherwise(0)
    )
    known = nominal.is_not_null() & pl.col("multiplier_e6").is_not_null()
    frame = frame.with_columns(
        (pl.col("taker_side") == "yes").alias("taker_yes"),
        (pl.col("label") == "yes").cast(pl.Int8).alias("y"),
        ((pl.col("ref_time") - pl.col("t")).dt.total_seconds().clip(lower_bound=0)).alias(
            "tleft_s"
        ),
        pl.when(known)
        .then(taker_fee_expr(pl.col("multiplier_e6"), pl.col("c"), pl.col("p")))
        .alias("tk_fee"),
        pl.when(known)
        .then(maker_fee_expr(pl.col("multiplier_e6"), nominal, pl.col("c"), pl.col("p")))
        .alias("mk_fee_nom"),
        pl.when(known)
        .then(maker_fee_expr(pl.col("multiplier_e6"), conservative, pl.col("c"), pl.col("p")))
        .alias("mk_fee_cons"),
    )
    # the first rule that applies names the reason, so the order is the priority
    rules: list[tuple[pl.Expr, str]] = [
        (~pl.col("taker_side").is_in(["yes", "no"]), "bad_side"),
        (pl.col("label").is_null(), "market_missing"),
        (~pl.col("label").is_in(["yes", "no"]), "not_binary"),
        (pl.col("tk_fee").is_null(), "fee_unknown"),
        (pl.col("ref_time").is_null(), "time_unknown"),
        (pl.col("ev_open").is_null() | pl.col("ev_close").is_null(), "event_time_unknown"),
    ]
    window_start, window_end = config.window()
    if window_start is not None:
        rules.append((pl.col("ev_open") < window_start, "outside_window"))
    if window_end is not None:
        rules.append((pl.col("ev_close") >= window_end, "outside_window"))
    if chosen is not None:
        rules.append((~pl.col("event_ticker").is_in(chosen), "not_sampled"))
    exclusion = pl.coalesce([pl.when(rule).then(pl.lit(reason)) for rule, reason in rules])
    return frame.with_columns(exclusion.alias("exclusion"), _weight(config).alias("w")).select(
        "event_ticker",
        "ticker",
        "series_ticker",
        "category",
        "t",
        "p",
        "c",
        "taker_yes",
        "y",
        "tleft_s",
        "tk_fee",
        "mk_fee_nom",
        "mk_fee_cons",
        "ev_open",
        "ev_close",
        "w",
        "exclusion",
    )


def used(frame: pl.LazyFrame) -> pl.LazyFrame:
    return frame.filter(pl.col("exclusion").is_null()).drop("exclusion")


def exclusion_counts(frame: pl.LazyFrame) -> dict[str, int]:
    """The number of trades by exclusion reason (``used`` for the kept ones)."""
    rows = (
        frame.group_by(pl.col("exclusion").fill_null("used"))
        .agg(pl.len().alias("trades"))
        .collect()
        .iter_rows()
    )
    return {reason: int(n) for reason, n in rows}
