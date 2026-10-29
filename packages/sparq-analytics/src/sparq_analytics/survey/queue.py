"""S3.1's live reading: the queue at 99 cents and the dump into it, from the ingest's frames
(PLAN §6.3, M2). No outcome is read.

A market is in the **yes regime** while its best yes bid is 99 cents, and in the **no regime**
while its best yes ask is 1 cent (a 99-cent bid for no). The size shown at that best is the
**queue proxy**; what it misses, the depth behind 99 and cancellations ahead, is in PLAN §9. An
**episode** runs from the first frame in the regime (the onset) to the first frame out of it; one
still in the regime at the span's end is right-censored, and one that begins in the span's first
hour is left-censored (its true onset may be earlier) and takes no part in the fills.

The **dump** is the trades at 99 cents whose taker bought the dead side during an episode: each
filled a resting 99-cent bid. The **fills** a bid of ``S`` contracts would have had, first in, first
out: joining at the onset behind the size then shown, or an hour earlier behind the size shown at
99 then (none when the best was not yet 99; unknown when no frame was seen in the two hours before
that moment).

The hour files are read one at a time and reduced to the frames in a regime, those right after
one, and each market's first and last frame of the hour, which is enough to find every onset and
end exactly. Money is integer millionths of a dollar; sizes are hundredths of a contract.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray
from sparq_core.fees import MAKER_SHARE_E2

from sparq_analytics.live.source import HOUR, hour_files, hour_name, hour_of, last_hour_end
from sparq_analytics.study.feecalc import maker_fee_expr
from sparq_analytics.survey.dump import CENTS, CERTAIN_E6, MS_PER_HOUR, SurveyDataError, hours

SIDES = ("yes", "no")
BID_SIZES_E2 = (10_000, 100_000)  # 100 and 1,000 contracts a market
FRAME_COLUMNS = [
    "ts_ms",
    "ticker",
    "yes_bid_e6",
    "yes_ask_e6",
    "yes_bid_size_e2",
    "yes_ask_size_e2",
]


def span(live: Path) -> tuple[datetime, datetime]:
    """The copied span: from the first ticker hour file with rows to the end of the last hour
    copied for both tables."""
    names = sorted(p.stem for p in (live / "ticker").glob("*.parquet"))
    end = last_hour_end(live)
    if not names or end is None:
        raise SurveyDataError(f"no hour files under {live}; run live extract")
    for name in names:
        if pl.scan_parquet(live / "ticker" / f"{name}.parquet").select(pl.len()).collect().item():
            start = hour_of(name)
            if start < end:
                return start, end
            break
    raise SurveyDataError(f"no ticker frames under {live}")


def _reduce(path: Path) -> pl.DataFrame:
    """One hour's frames cut down to what finds every regime's onset and end."""
    frames = pl.read_parquet(path, columns=FRAME_COLUMNS).sort("ticker", "ts_ms")
    in_yes = (pl.col("yes_bid_e6") >= CERTAIN_E6).fill_null(False)
    in_no = (pl.col("yes_ask_e6") <= CENTS).fill_null(False)
    frames = frames.with_columns(in_yes=in_yes, in_no=in_no)
    any_in = pl.col("in_yes") | pl.col("in_no")
    ticker = pl.col("ticker")
    keep = (
        any_in
        | any_in.shift(1).over("ticker").fill_null(False)
        | (ticker != ticker.shift(1)).fill_null(True)
        | (ticker != ticker.shift(-1)).fill_null(True)
    )
    return frames.filter(keep).select(
        "ts_ms", "ticker", "in_yes", "in_no", "yes_bid_size_e2", "yes_ask_size_e2"
    )


def episodes(reduced: pl.DataFrame, start: datetime, end: datetime) -> pl.DataFrame:
    """Every episode of both regimes in the reduced frames of the span."""
    span_start_ms = int(start.timestamp() * 1000)
    span_end_ms = int(end.timestamp() * 1000)
    out = []
    for side in SIDES:
        size = "yes_bid_size_e2" if side == "yes" else "yes_ask_size_e2"
        rows = reduced.select(
            "ts_ms",
            "ticker",
            pl.col(f"in_{side}").alias("inr"),
            pl.col(size).fill_null(0).alias("size_e2"),
        ).sort("ticker", "ts_ms")
        rows = rows.with_columns(
            onset=pl.col("inr") & ~pl.col("inr").shift(1).over("ticker").fill_null(False)
        ).with_columns(episode=pl.col("onset").cast(pl.Int32).cum_sum().over("ticker"))
        inr = pl.col("inr")
        found = (
            rows.filter(pl.col("episode") > 0)
            .group_by("ticker", "episode")
            .agg(
                pl.col("ts_ms").filter(pl.col("onset")).min().alias("start_ms"),
                pl.col("size_e2").filter(pl.col("onset")).first().alias("onset_size_e2"),
                pl.col("ts_ms").filter(~inr).min().alias("end_ms"),
                inr.sum().alias("frames"),
                pl.col("size_e2").filter(inr).mean().alias("size_mean_e2"),
            )
            .with_columns(side=pl.lit(side))
        )
        out.append(found)
    found = pl.concat(out).with_columns(
        censored=pl.col("end_ms").is_null(),
        left_censored=pl.col("start_ms") < span_start_ms + MS_PER_HOUR,
        hours=hours(pl.col("end_ms").fill_null(span_end_ms) - pl.col("start_ms")),
    )
    return found.sort("ticker", "side", "start_ms").drop("episode")


def hour_before(live: Path, found: pl.DataFrame) -> pl.DataFrame:
    """Each episode's queue an hour before its onset: the size at 99 shown by the last frame in
    the two hours before that moment (0 when the best was not 99; null when no frame)."""
    targets = found.select("ticker", "side", "start_ms").with_columns(
        target_ms=pl.col("start_ms") - MS_PER_HOUR
    )
    target_hours = (
        targets.select((pl.col("target_ms") // MS_PER_HOUR * MS_PER_HOUR).alias("hour_ms"))
        .unique()
        .sort("hour_ms")["hour_ms"]
        .to_list()
    )
    pieces = []
    loaded: dict[datetime, pl.DataFrame | None] = {}  # this hour and the one before
    for hour_ms in target_hours:
        hour = datetime.fromtimestamp(hour_ms / 1000, tz=UTC)
        frames = []
        for when in (hour - HOUR, hour):
            if when not in loaded:
                path = live / "ticker" / f"{hour_name(when)}.parquet"
                loaded[when] = (
                    pl.read_parquet(path, columns=FRAME_COLUMNS) if path.exists() else None
                )
            frame = loaded[when]
            if frame is not None:
                frames.append(frame)
        for when in [w for w in loaded if w < hour - HOUR]:
            del loaded[when]
        mine = targets.filter(pl.col("target_ms") // MS_PER_HOUR * MS_PER_HOUR == hour_ms)
        if not frames:
            pieces.append(
                mine.with_columns(hour_before_size_e2=pl.lit(None, dtype=pl.Int64)).select(
                    "ticker", "side", "start_ms", "hour_before_size_e2"
                )
            )
            continue
        book = pl.concat(frames).sort("ts_ms")
        joined = mine.sort("target_ms").join_asof(
            book, left_on="target_ms", right_on="ts_ms", by="ticker", strategy="backward",
            check_sortedness=False,
        )  # fmt: skip
        at_99 = (
            pl.when(pl.col("side") == "yes")
            .then(pl.col("yes_bid_e6") >= CERTAIN_E6)
            .otherwise(pl.col("yes_ask_e6") <= CENTS)
        )
        size = (
            pl.when(pl.col("side") == "yes")
            .then(pl.col("yes_bid_size_e2"))
            .otherwise(pl.col("yes_ask_size_e2"))
        )
        pieces.append(
            joined.with_columns(
                hour_before_size_e2=pl.when(pl.col("ts_ms").is_null())
                .then(pl.lit(None, dtype=pl.Int64))
                .when(at_99.fill_null(False))
                .then(size.fill_null(0))
                .otherwise(0)
            ).select("ticker", "side", "start_ms", "hour_before_size_e2")
        )
    return found.join(pl.concat(pieces), on=["ticker", "side", "start_ms"], how="left")


def mark_gaps(found: pl.DataFrame, live: Path, end: datetime) -> pl.DataFrame:
    """Each episode with ``in_gap``: whether an outage of the ticker feed (the ingest's gap log)
    overlaps it, in which case its frames may miss an end or a re-entry and its trades were
    backfilled; such episodes take no part in the figures."""
    path = live / "gaps.parquet"
    span_end_ms = int(end.timestamp() * 1000)
    if not path.exists():
        return found.with_columns(in_gap=pl.lit(False))
    gaps = (
        pl.read_parquet(path)
        .filter(pl.col("kind").is_in(["ticker", "all"]))
        .select(
            pl.col("start_ms").alias("gap_start_ms"),
            pl.col("end_ms").fill_null(span_end_ms).alias("gap_end_ms"),
        )
    )
    if gaps.is_empty():
        return found.with_columns(in_gap=pl.lit(False))
    overlapping = (
        found.select("ticker", "side", "start_ms", "end_ms")
        .join(gaps, how="cross")
        .filter(
            (pl.col("gap_start_ms") < pl.col("end_ms").fill_null(span_end_ms))
            & (pl.col("gap_end_ms") > pl.col("start_ms"))
        )
        .select("ticker", "side", "start_ms")
        .unique()
        .with_columns(in_gap=pl.lit(True))
    )
    return found.join(overlapping, on=["ticker", "side", "start_ms"], how="left").with_columns(
        pl.col("in_gap").fill_null(False)
    )


def dump_trades(live: Path, found: pl.DataFrame, start: datetime, end: datetime) -> pl.DataFrame:
    """Each episode with the contracts dumped into its 99-cent bids (the taker bought the dead
    side) and lifted from the certain side, from the span's trades."""
    files = hour_files(live, "trade", start, end)
    price = pl.col("yes_price_e6")
    trades = (
        pl.scan_parquet(files)
        .filter((price == CERTAIN_E6) | (price == CENTS))
        .with_columns(side=pl.when(price == CERTAIN_E6).then(pl.lit("yes")).otherwise(pl.lit("no")))
        .with_columns(dead=pl.col("taker_side") != pl.col("side"))
        .select("ticker", "side", "ts_ms", "count_e2", "dead")
        .collect()
        .sort("ts_ms")
    )
    starts = found.select("ticker", "side", "start_ms", "end_ms").sort("start_ms")
    inside = (
        trades.join_asof(
            starts,
            left_on="ts_ms",
            right_on="start_ms",
            by=["ticker", "side"],
            strategy="backward",
            check_sortedness=False,
        )  # fmt: skip
        .filter(pl.col("start_ms").is_not_null())
        .filter(pl.col("end_ms").is_null() | (pl.col("ts_ms") < pl.col("end_ms")))
        .group_by("ticker", "side", "start_ms")
        .agg(
            pl.col("count_e2").filter(pl.col("dead")).sum().alias("dump_e2"),
            pl.col("count_e2").filter(~pl.col("dead")).sum().alias("lifted_e2"),
        )
    )
    return found.join(inside, on=["ticker", "side", "start_ms"], how="left").with_columns(
        pl.col("dump_e2", "lifted_e2").fill_null(0)
    )


def expected_fill(
    queues: NDArray[np.int64], dumps: NDArray[np.int64], size_e2: int
) -> NDArray[np.int64]:
    """For each queue, the mean over ``dumps`` of what a bid of ``size_e2`` behind it would
    fill, first in, first out (``clamp(dump - queue, 0, size)``), in whole hundredths; zero
    when there is no dump to draw from."""
    if dumps.size == 0:
        return np.zeros(queues.shape, dtype=np.int64)
    ordered = np.sort(dumps)
    sums = np.concatenate([[0], np.cumsum(ordered)])
    low = np.searchsorted(ordered, queues, side="right")  # dumps at or below the queue: 0
    high = np.searchsorted(ordered, queues + size_e2, side="left")  # from here on: the bid
    partial = (sums[high] - sums[low]) - queues * (high - low)
    full = (ordered.size - high) * size_e2
    return np.asarray((partial + full) // ordered.size, dtype=np.int64)


def _reversal_fills(
    frame: pl.DataFrame, queue: str, dumps: dict[str, list[int]], size_e2: int
) -> pl.Series:
    """Per episode, the expected fill a reversal would give the bid behind its queue, from the
    history's dumps of its category (null where the queue is unknown, or where the history
    never saw the category; an empty list is a category that never reversed)."""
    categories = frame["category"].to_list()
    queues = frame[queue].to_list()
    out: list[int | None] = [None] * frame.height
    for category in set(categories) & set(dumps):
        rows = [i for i, c in enumerate(categories) if c == category and queues[i] is not None]
        fills = expected_fill(
            np.asarray([queues[i] for i in rows], dtype=np.int64),
            np.asarray(dumps[category], dtype=np.int64),
            size_e2,
        )
        for i, fill in zip(rows, fills.tolist(), strict=True):
            out[i] = fill
    return pl.Series(out, dtype=pl.Int64)


def with_fills(
    found: pl.DataFrame,
    markets: pl.DataFrame,
    fees: pl.DataFrame,
    reversal_dumps: dict[str, list[int]] | None = None,
    false_dumps: dict[str, list[int]] | None = None,
) -> pl.DataFrame:
    """Each episode with its category and fee terms and, for each bid size, the fills of a bid
    joining at the onset and an hour before, with the maker fee on them; and, given the
    history's reversal dumps (for the onset) and false-regime dumps (for the hour before), the
    fill a reversal would have given each."""
    # the series' current terms: the live span is days old, so they are the terms in force; a
    # fee type outside the quadratic family, or a series without terms, leaves the fee unknown
    share = pl.col("fee_type").replace_strict(MAKER_SHARE_E2, default=None, return_dtype=pl.Int64)
    frame = (
        found.join(markets.select("ticker", "series_ticker", "category"), on="ticker", how="left")
        .join(fees, on="series_ticker", how="left")
        .with_columns(pl.col("category").fill_null("unknown"), share=share)
    )
    columns = []
    for size_e2 in BID_SIZES_E2:
        for position, queue in (("onset", "onset_size_e2"), ("hour_before", "hour_before_size_e2")):
            fill = (pl.col("dump_e2") - pl.col(queue)).clip(lower_bound=0, upper_bound=size_e2)
            name = f"fill_{position}_{size_e2}"
            columns.append(fill.alias(name))
            columns.append(
                maker_fee_expr(
                    pl.col("multiplier_e6"), pl.col("share"), fill, pl.lit(CERTAIN_E6)
                ).alias(f"fee_{position}_{size_e2}")
            )
    frame = frame.with_columns(columns)
    for size_e2 in BID_SIZES_E2:
        for position, queue, dumps in (
            ("onset", "onset_size_e2", reversal_dumps),
            ("hour_before", "hour_before_size_e2", false_dumps),
        ):
            if dumps is not None:
                frame = frame.with_columns(
                    _reversal_fills(frame, queue, dumps, size_e2).alias(
                        f"rev_fill_{position}_{size_e2}"
                    )
                )
    return frame


def summarize(found: pl.DataFrame, start: datetime, end: datetime) -> dict[str, Any]:
    """S3.1's live figures per category: the episodes, the queue, the dump rate and the fills a
    day; left-censored episodes and those overlapping a ticker gap take no part in the queue
    and the fills."""
    span_hours = int((end - start) / timedelta(hours=1))
    if span_hours < 1:
        raise SurveyDataError("the span holds no whole hour")
    known = found.filter(~pl.col("left_censored") & ~pl.col("in_gap"))
    closed = known.filter(~pl.col("censored"))
    aggs = [
        pl.len().alias("episodes"),
        (pl.len() * 24 // span_hours).alias("episodes_a_day"),
        pl.col("censored").sum().alias("censored"),
        pl.col("ticker").n_unique().alias("markets"),
        pl.col("onset_size_e2").median().alias("onset_size_median_e2"),
        pl.col("hour_before_size_e2").median().alias("hour_before_size_median_e2"),
        pl.col("hour_before_size_e2").is_null().sum().alias("hour_before_unknown"),
        pl.col("dump_e2").sum().alias("dump_e2"),
        pl.col("lifted_e2").sum().alias("lifted_e2"),
        pl.col("share").is_null().sum().alias("fee_unknown_episodes"),
    ]
    for size_e2 in BID_SIZES_E2:
        for position in ("onset", "hour_before"):
            name = f"{position}_{size_e2}"
            aggs.append(
                (pl.col(f"fill_{name}").sum() * 24 // span_hours).alias(f"fills_a_day_e2_{name}")
            )
            aggs.append(
                (pl.col(f"fee_{name}").sum() * 24 // span_hours).alias(f"fee_a_day_e6_{name}")
            )
            if f"rev_fill_{name}" in known.columns:
                known_fill = pl.col(f"rev_fill_{name}")
                aggs.append(  # null, not 0, where no episode's reversal fill is known
                    pl.when(known_fill.is_not_null().any())
                    .then(known_fill.sum() * 24 // span_hours)
                    .alias(f"rev_fills_a_day_e2_{name}")
                )
    by_category = known.group_by("category").agg(aggs)
    rate = closed.group_by("category").agg(
        pl.col("dump_e2").sum().alias("closed_dump_e2"),
        pl.col("hours").sum().alias("closed_hours"),
        pl.col("hours").median().alias("closed_hours_median"),
    )
    by_category = (
        by_category.join(rate, on="category", how="left")
        .with_columns(  # null, not infinity, where the closed episodes lasted under a second
            dump_e2_a_regime_hour=pl.when(pl.col("closed_hours") > 0).then(
                pl.col("closed_dump_e2") / pl.col("closed_hours")
            )
        )
        .sort("category")
    )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "hours": span_hours,
        "episodes": found.height,
        "left_censored": int(found["left_censored"].sum()),
        "gap_episodes": int(found["in_gap"].sum()),
        "by_category": {row["category"]: row for row in by_category.to_dicts()},
    }


def live_reading(
    live: Path, fees: pl.DataFrame, history: dict[str, Any] | None = None
) -> tuple[dict[str, Any], pl.DataFrame]:
    """The whole live reading: the summary and the episodes; with the history's summary, the
    fills a reversal would give too."""
    start, end = span(live)
    reduced = pl.concat([_reduce(path) for path in hour_files(live, "ticker", start, end)])
    found = mark_gaps(episodes(reduced, start, end), live, end)
    found = dump_trades(live, hour_before(live, found), start, end)
    markets = pl.read_parquet(
        live / "markets.parquet", columns=["ticker", "series_ticker", "category"]
    )
    past = (history or {}).get("by_category")
    found = with_fills(
        found,
        markets,
        fees,
        None if past is None else {c: r["reversal_dumps_e2"] for c, r in past.items()},
        None if past is None else {c: r["false_regime_dumps_e2"] for c, r in past.items()},
    )
    return summarize(found, start, end), found


def fee_terms(kalshi: Path) -> pl.DataFrame:
    """Each series' fee type and multiplier, from the history's series table."""
    return pl.read_parquet(
        kalshi / "series.parquet", columns=["ticker", "fee_type", "fee_multiplier_e6"]
    ).rename({"ticker": "series_ticker", "fee_multiplier_e6": "multiplier_e6"})
