"""S3.2's event study: how the watched market of a pair answers a move of the other, on the
ingest's frames (PLAN §6.3, M2; no outcome is read).

An **event** is a move of market ``a``: its mid (from a two-sided frame) changes by 2 cents or
more from its previous two-sided frame (one-sided frames in between are skipped), with the bid
and the ask not moving against each other (a pure widening is not a move). For each pair valid
at that moment, the watched market ``b`` is read:

* its quote just before the move (its last frame strictly before; **unknown** when none was seen,
  **one-sided** when a side is missing, which the feed shows as a 0-cent bid or a 100-cent ask
  with no size, **extreme** when the mid is at 3 cents or less or 97 or more, which is S3.1's
  certainty regime: those events take no further part);
* its **response** at 1 s, 10 s, 1 min, 10 min and 1 h: the change of its mid, counted positive
  in the direction the pair expects (``a``'s direction times the pair's sign; ``a``'s direction
  alone for an unsigned kind); null past ``b``'s close, past the copied frames, or where a logged
  outage of the ticker feed overlaps the hour;
* its **first move**: the first frame at or after the move at which its mid differs from the mid
  before, up to an hour (later counts as not moved; the lag is **unknown** when the hour runs
  past the copied frames or through an outage and no move was seen), and whether that move
  agreed with the expectation;
* the trades in ``b`` inside that lag (block trades left out): the takers who hit ``b``'s stale
  quote on the expected side (someone already taking it: **with**) and on the other side
  (**against**); while the quote stands every trade is at it, so the taker's side says which
  side was hit;
* for a signed kind, the **capacity** of the stale quote: the size shown on the side a taker would
  hit, times the 10-minute response, less the taker fee on that size at that price from the
  series' terms. Each stale quote is counted once, at the first move that left it stale (the same
  ``b``, expected side and first move, or, when no move came within the hour, once an hour it
  stood), provided its 10-minute response and its fee are known; the trades through it are
  counted once the same way. The capacity is given for every stale quote, as M4 words it, and
  for the **reachable** ones, which stood at least a second before ``b`` moved (SPARQ's scale)
  with that lag known, since a quote that moved in the same second as ``a`` was never there to
  hit.

Events whose first ten minutes overlap a logged outage are excluded. The figures a day scale the
span's sums by its hours; they are also broken down by ``b``'s activity tier (contracts a day in
the span), by ``b``'s category and by the size of ``a``'s move. Money is integer millionths of a
dollar; sizes are hundredths of a contract; responses are millionths per contract (10,000 to a
cent) and shown in cents.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from sparq_core.fees import QUADRATIC_FAMILY
from sparq_core.fixedpoint import E6

from sparq_analytics.live.source import HOUR, hour_files, hour_name, hour_of
from sparq_analytics.study.bootstrap import Estimate, cluster_bootstrap
from sparq_analytics.study.feecalc import taker_fee_expr
from sparq_analytics.survey.dump import MS_PER_HOUR, SurveyDataError
from sparq_analytics.survey.queue import FRAME_COLUMNS, span
from sparq_analytics.survey.relations import SIGNED

HORIZONS_MS = {"1s": 1_000, "10s": 10_000, "1m": 60_000, "10m": 600_000, "1h": 3_600_000}
HEADLINE = "10m"
LONGEST = "1h"
MOVE_E6 = 20_000  # two cents of mid
EXTREME_E6 = 30_000  # a mid at 3 cents or less (or 97 or more) is the certainty regime
TIERS: tuple[tuple[int | None, str], ...] = (
    (100, "under 100"),
    (1_000, "100 to 1,000"),
    (10_000, "1,000 to 10,000"),
    (None, "10,000 and more"),
)
BAR_E6 = 25 * E6  # M4: $25 a day for a relation type
REACH_MS = 1_000  # a quote that stood this long before b moved could have been hit
MOVE_BUCKETS: tuple[tuple[int | None, str], ...] = (  # a's move, in millionths
    (30_000, "2 to 3"),
    (50_000, "3 to 5"),
    (100_000, "5 to 10"),
    (None, "10 and more"),
)
CENTS_E6 = 10_000
MS_PER_DAY = 24 * MS_PER_HOUR
RESAMPLES, TAIL, SEED = 2_000, 0.025, 32
SPOTS = 20
EVENT_FOLDER = "lag-events"
_STATE_FRAME = ("yes_bid_e6", "yes_ask_e6", "yes_bid_size_e2", "yes_ask_size_e2", "mid_e6")


def _with_mid(frames: pl.DataFrame) -> pl.DataFrame:
    """The frames with an empty side read as missing (the feed shows it as a 0-cent bid or a
    100-cent ask with no size, never as a null) and the mid where both sides stand."""
    bid = pl.when(pl.col("yes_bid_size_e2") > 0).then(pl.col("yes_bid_e6"))
    ask = pl.when(pl.col("yes_ask_size_e2") > 0).then(pl.col("yes_ask_e6"))
    return frames.with_columns(yes_bid_e6=bid, yes_ask_e6=ask).with_columns(
        mid_e6=pl.when(
            pl.col("yes_bid_e6").is_not_null() & pl.col("yes_ask_e6").is_not_null()
        ).then((pl.col("yes_bid_e6") + pl.col("yes_ask_e6")) // 2)
    )


def frames_of(path: Path, carried: bool = False) -> pl.DataFrame:
    """One hour file's frames with their mid (null unless two-sided), flagged as carried over from
    an earlier hour or not."""
    return _with_mid(pl.read_parquet(path, columns=FRAME_COLUMNS)).with_columns(
        carried=pl.lit(carried)
    )


def empty_frames() -> pl.DataFrame:
    schema = {c: pl.Int64 if c != "ticker" else pl.String for c in FRAME_COLUMNS}
    return _with_mid(pl.DataFrame(schema=schema)).with_columns(carried=pl.lit(True))


def moves(frames: pl.DataFrame) -> pl.DataFrame:
    """Every move among ``frames``: each two-sided frame against the market's previous two-sided
    frame (each market's carried frames come first, so a move across the hour boundary is seen
    and a carried frame itself is never a move)."""

    def change(column: str) -> pl.Expr:
        return pl.col(column) - pl.col(column).shift(1).over("ticker")

    # the differences are taken before the move filter, so each row sees its previous frame
    ordered = (
        frames.filter(pl.col("mid_e6").is_not_null())
        .sort("ticker", "ts_ms")
        .with_columns(
            d_mid=change("mid_e6"), d_bid=change("yes_bid_e6"), d_ask=change("yes_ask_e6")
        )
    )
    d_mid = pl.col("d_mid")
    move = (d_mid.abs() >= MOVE_E6) & ((pl.col("d_bid") * pl.col("d_ask")) >= 0)
    return ordered.filter((move & ~pl.col("carried")).fill_null(False)).select(
        a=pl.col("ticker"),
        t=pl.col("ts_ms"),
        direction=d_mid.sign().cast(pl.Int8),
        a_move_e6=d_mid.abs(),
    )


def events_of(found: pl.DataFrame, pairs: pl.DataFrame, info: pl.DataFrame) -> pl.DataFrame:
    """Each move with every pair valid at its moment, and ``b``'s series, category, close, tier
    and fee terms (``info``)."""
    valid = (pl.col("valid_from_ms") <= pl.col("t")) & (pl.col("t") < pl.col("valid_to_ms"))
    events = (
        found.join(pairs, on="a", how="inner")
        .filter(valid)
        .drop("valid_from_ms", "valid_to_ms")
        .join(
            info.select(a=pl.col("ticker"), series_a=pl.col("series_ticker")), on="a", how="left"
        )
        .join(
            info.rename(
                {
                    "ticker": "b",
                    "series_ticker": "series_b",
                    "category": "category_b",
                    "close_ms": "close_b",
                    "tier": "tier_b",
                }
            ),
            on="b",
            how="left",
        )
    )  # fmt: skip
    return events.with_columns(
        signed=pl.col("kind").is_in(SIGNED),
        expected=(pl.col("direction") * pl.col("sign").fill_null(1)).cast(pl.Int8),
    )


def with_gaps(events: pl.DataFrame, gaps: pl.DataFrame) -> pl.DataFrame:
    """``in_gap``: an outage overlaps the event's first ten minutes; ``gap_1h``: its hour."""
    if gaps.is_empty():
        return events.with_columns(in_gap=pl.lit(False), gap_1h=pl.lit(False))
    t = pl.col("t")

    def overlap(window_ms: int) -> pl.Expr:
        return pl.any_horizontal(
            [
                (pl.lit(row["start_ms"]) < t + window_ms) & (pl.lit(row["end_ms"]) > t)
                for row in gaps.to_dicts()
            ]
        )

    return events.with_columns(
        in_gap=overlap(HORIZONS_MS[HEADLINE]), gap_1h=overlap(HORIZONS_MS[LONGEST])
    )


def with_state(events: pl.DataFrame, book: pl.DataFrame) -> pl.DataFrame:
    """``b``'s quote from its last frame strictly before the move, and its state."""
    right = book.select(
        b_ts0=pl.col("ts_ms"),
        b=pl.col("ticker"),
        bid0=pl.col("yes_bid_e6"),
        ask0=pl.col("yes_ask_e6"),
        bsz0=pl.col("yes_bid_size_e2"),
        asz0=pl.col("yes_ask_size_e2"),
        mid0=pl.col("mid_e6"),
    ).sort("b_ts0")
    joined = (
        events.with_columns(k=pl.col("t") - 1)
        .sort("k")
        .join_asof(
            right,
            left_on="k",
            right_on="b_ts0",
            by="b",
            strategy="backward",
            check_sortedness=False,
        )  # fmt: skip
        .drop("k")
    )
    mid0 = pl.col("mid0")
    state = (
        pl.when(pl.col("b_ts0").is_null())
        .then(pl.lit("unknown"))
        .when(mid0.is_null())
        .then(pl.lit("onesided"))
        .when((mid0 <= EXTREME_E6) | (mid0 >= E6 - EXTREME_E6))
        .then(pl.lit("extreme"))
        .otherwise(pl.lit("ok"))
    )
    return joined.with_columns(state=state)


def with_responses(
    events: pl.DataFrame, two_sided: pl.DataFrame, loaded_end_ms: int
) -> pl.DataFrame:
    """``b``'s mid change at each horizon, in the expected direction."""
    right = two_sided.select(
        ts_ms=pl.col("ts_ms"), b=pl.col("ticker"), mid_k=pl.col("mid_e6")
    ).sort("ts_ms")
    out = events
    for name, delta in HORIZONS_MS.items():
        k = pl.col("k")
        known = (k < loaded_end_ms) & (k < pl.col("close_b"))
        if name == LONGEST:
            known = known & ~pl.col("gap_1h")
        response = pl.when(known).then(pl.col("expected") * (pl.col("mid_k") - pl.col("mid0")))
        out = (
            out.with_columns(k=pl.col("t") + delta)
            .sort("k")
            .join_asof(
                right,
                left_on="k",
                right_on="ts_ms",
                by="b",
                strategy="backward",
                check_sortedness=False,
            )  # fmt: skip
            .with_columns(response.cast(pl.Int32).alias(f"r_{name}"))
            .drop("k", "ts_ms", "mid_k")
        )
    return out  # fmt: skip


def with_first_move(
    events: pl.DataFrame, changes: pl.DataFrame, loaded_end_ms: int
) -> pl.DataFrame:
    """``b``'s first mid change at or after the move, within an hour, and whether it agreed;
    ``lag_known`` is false where none was seen and the hour ran past the copied frames or
    through an outage."""
    right = changes.select(
        first_ms=pl.col("ts_ms"), b=pl.col("ticker"), mid_first=pl.col("mid_e6")
    ).sort("first_ms")
    joined = events.sort("t").join_asof(
        right, left_on="t", right_on="first_ms", by="b", strategy="forward", check_sortedness=False
    )  # fmt: skip
    lag = pl.col("first_ms") - pl.col("t")
    within = lag <= HORIZONS_MS[LONGEST]
    agreement = (pl.col("mid_first") - pl.col("mid0")).sign() * pl.col("expected")
    whole_hour = (pl.col("t") + HORIZONS_MS[LONGEST] <= loaded_end_ms) & ~pl.col("gap_1h")
    return joined.with_columns(
        first_ms=pl.when(within).then(pl.col("first_ms")),
        lag_ms=pl.when(within).then(lag).cast(pl.Int32),
        agreement=pl.when(within).then(agreement).cast(pl.Int8),
        lag_known=within.fill_null(False) | whole_hour,
    ).drop("mid_first")


def with_through(events: pl.DataFrame, trades: pl.DataFrame) -> pl.DataFrame:
    """The trades in ``b`` while its quote stood (from the move to its first move, at most an
    hour), by the taker's side: ``with`` the expectation (the stale quote was hit) or against."""
    yes = (pl.col("taker_side") == "yes").cast(pl.Int64)
    no = (pl.col("taker_side") == "no").cast(pl.Int64)
    cumulative = (
        trades.sort("ticker", "ts_ms")
        .with_columns(
            yes_e2=(pl.col("count_e2") * yes).cum_sum().over("ticker"),
            no_e2=(pl.col("count_e2") * no).cum_sum().over("ticker"),
            yes_n=yes.cum_sum().over("ticker"),
            no_n=no.cum_sum().over("ticker"),
        )
        .select("ts_ms", pl.col("ticker").alias("b"), "yes_e2", "no_e2", "yes_n", "no_n")
        .sort("ts_ms")
    )
    sums = ["yes_e2", "no_e2", "yes_n", "no_n"]

    def at(frame: pl.DataFrame, key: pl.Expr, suffix: str) -> pl.DataFrame:
        joined = (
            frame.with_columns(k=key)
            .sort("k")
            .join_asof(
                cumulative,
                left_on="k",
                right_on="ts_ms",
                by="b",
                strategy="backward",
                check_sortedness=False,
            )  # fmt: skip
        )
        return joined.with_columns(
            [pl.col(s).fill_null(0).alias(f"{s}{suffix}") for s in sums]
        ).drop("k", "ts_ms", *sums)

    hour = HORIZONS_MS[LONGEST]
    end = pl.min_horizontal(pl.col("first_ms").fill_null(pl.col("t") + hour), pl.col("t") + hour)
    out = at(at(events, pl.col("t") - 1, "_0"), end - 1, "_1")
    up = pl.col("expected") > 0

    def taken(side: str, measure: str) -> pl.Expr:
        return pl.col(f"{side}_{measure}_1") - pl.col(f"{side}_{measure}_0")

    return out.with_columns(
        with_e2=pl.when(up).then(taken("yes", "e2")).otherwise(taken("no", "e2")),
        with_n=pl.when(up).then(taken("yes", "n")).otherwise(taken("no", "n")),
        against_e2=pl.when(up).then(taken("no", "e2")).otherwise(taken("yes", "e2")),
        against_n=pl.when(up).then(taken("no", "n")).otherwise(taken("yes", "n")),
    ).drop([f"{s}_{i}" for s in sums for i in (0, 1)])


def with_capacity(events: pl.DataFrame) -> pl.DataFrame:
    """The stale quote's capacity for a signed kind: its size on the side a taker would hit, times
    the 10-minute response (floored to a millionth), less the taker fee on that size at that price;
    the fee is unknown outside the quadratic family."""
    up = pl.col("expected") > 0
    live = pl.col("signed") & (pl.col("state") == "ok")
    size = pl.when(live).then(pl.when(up).then(pl.col("asz0")).otherwise(pl.col("bsz0")))
    price = pl.when(live).then(pl.when(up).then(pl.col("ask0")).otherwise(pl.col("bid0")))
    frame = events.with_columns(stale_size_e2=size.fill_null(0), stale_price_e6=price)
    fee = pl.when(pl.col("fee_type").is_in(QUADRATIC_FAMILY)).then(
        taker_fee_expr(pl.col("multiplier_e6"), pl.col("stale_size_e2"), pl.col("stale_price_e6"))
    )
    gross = (pl.col("stale_size_e2") * pl.col(f"r_{HEADLINE}").cast(pl.Int64)) // 100
    return (
        frame.with_columns(
            stale_size_e2=pl.when(live).then(pl.col("stale_size_e2")),
            gross_e6=pl.when(live).then(gross),
            fee_e6=pl.when(live).then(fee),
        )
        .with_columns(net_e6=pl.col("gross_e6") - pl.col("fee_e6"))
        .drop("bid0", "ask0", "bsz0", "asz0", "fee_type", "multiplier_e6")
    )


def tiers_of(live: Path, start: datetime, end: datetime) -> pl.DataFrame:
    """Each traded market's activity tier: its contracts a day over the span."""
    hours = max(1, int((end - start) / timedelta(hours=1)))
    traded = (
        pl.scan_parquet(hour_files(live, "trade", start, end))
        .group_by("ticker")
        .agg((pl.col("count_e2").sum() * 24 // (hours * 100)).alias("contracts_a_day"))
        .collect()
    )
    return traded.with_columns(tier=_bucket(pl.col("contracts_a_day"), TIERS)).select(
        "ticker", "tier"
    )


def _bucket(value: pl.Expr, buckets: tuple[tuple[int | None, str], ...]) -> pl.Expr:
    """The name of the first bucket whose bound ``value`` is under; the last bucket has none."""
    name = pl.lit(buckets[-1][1])
    for bound, label in reversed(buckets[:-1]):
        name = pl.when(value < bound).then(pl.lit(label)).otherwise(name)
    return name


def market_info(markets: pl.DataFrame, tiers: pl.DataFrame, fees: pl.DataFrame) -> pl.DataFrame:
    """Each market's series, category, close, tier (the lowest when it never traded) and fee
    terms."""
    return (
        markets.select("ticker", "series_ticker", "category", "close_ms")
        .join(tiers, on="ticker", how="left")
        .with_columns(pl.col("tier").fill_null(TIERS[0][1]))
        .join(fees, on="series_ticker", how="left")
    )


def gaps_of(live: Path, end: datetime) -> pl.DataFrame:
    path = live / "gaps.parquet"
    span_end_ms = int(end.timestamp() * 1000)
    if not path.exists():
        return pl.DataFrame(schema={"start_ms": pl.Int64, "end_ms": pl.Int64})
    return (
        pl.read_parquet(path)
        .filter(pl.col("kind").is_in(["ticker", "all"]))
        .select("start_ms", pl.col("end_ms").fill_null(span_end_ms))
    )


def _trades_of(live: Path, hours: list[datetime]) -> pl.DataFrame:
    paths = [p for p in (live / "trade" / f"{hour_name(h)}.parquet" for h in hours) if p.exists()]
    columns = ["ts_ms", "ticker", "count_e2", "taker_side"]
    if not paths:
        schema = {
            "ts_ms": pl.Int64,
            "ticker": pl.String,
            "count_e2": pl.Int64,
            "taker_side": pl.String,
        }
        return pl.DataFrame(schema=schema)
    # a block trade is negotiated, not taken at the resting quote
    return pl.concat(
        [
            pl.read_parquet(p, columns=[*columns, "is_block_trade"])
            .filter(pl.col("is_block_trade") == 0)
            .drop("is_block_trade")
            for p in paths
        ]
    )


def study_hour(
    hour: datetime,
    live: Path,
    pairs: pl.DataFrame,
    info: pl.DataFrame,
    gaps: pl.DataFrame,
    carried: pl.DataFrame,
    span_end: datetime,
    current: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame, int, pl.DataFrame | None]:
    """One hour's events, fully measured; the frames to carry into the next hour (each market's
    last frame and last two-sided frame); the hour's count of moves; and the following hour's
    frames, read here for the horizons and handed on as the next hour's ``current``."""
    if current is None:
        current = frames_of(live / "ticker" / f"{hour_name(hour)}.parquet")
    next_path = live / "ticker" / f"{hour_name(hour + HOUR)}.parquet"
    following = frames_of(next_path) if next_path.exists() and hour + HOUR < span_end else None
    loaded_end = hour + (2 * HOUR if following is not None else HOUR)
    with_carried = pl.concat([carried, current])
    found = moves(with_carried)
    events = events_of(found, pairs, info)
    book = pl.concat([with_carried] + ([following] if following is not None else [])).drop(
        "carried"
    )
    two_sided = book.filter(pl.col("mid_e6").is_not_null()).sort("ticker", "ts_ms")
    changes = two_sided.filter(pl.col("mid_e6") != pl.col("mid_e6").shift(1).over("ticker"))
    trades = _trades_of(live, [hour] + ([hour + HOUR] if following is not None else []))
    events = with_gaps(events, gaps)
    events = with_state(events, book.sort("ts_ms"))
    events = with_responses(events, two_sided, int(loaded_end.timestamp() * 1000))
    events = with_first_move(events, changes, int(loaded_end.timestamp() * 1000))
    events = with_through(events, trades)
    events = with_capacity(events).sort("t", "a", "b", "kind")
    ordered = with_carried.sort("ticker", "ts_ms")
    last = ordered.group_by("ticker", maintain_order=True).last()
    last_two_sided = (
        ordered.filter(pl.col("mid_e6").is_not_null())
        .group_by("ticker", maintain_order=True)
        .last()
    )
    new_carried = pl.concat([last, last_two_sided]).unique().with_columns(carried=pl.lit(True))
    return events, new_carried.select(with_carried.columns), found.height, following


def study_span(live: Path, hours: int | None = None) -> tuple[datetime, datetime]:
    """The copied span, cut to its first ``hours`` when asked."""
    start, end = span(live)
    if hours is not None:
        end = min(end, start + hours * HOUR)
    return start, end


def active_tickers(live: Path, start: datetime, end: datetime) -> list[str]:
    """The markets with a frame in the span: the only ones a pair can be seen on."""
    files = hour_files(live, "ticker", start, end)
    found = pl.scan_parquet(files).select("ticker").unique().collect()["ticker"].to_list()
    return sorted(found)


def run(
    live: Path,
    pairs: pl.DataFrame,
    markets: pl.DataFrame,
    fees: pl.DataFrame,
    out: Path,
    hours: int | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> tuple[datetime, datetime, int]:
    """The whole study, hour by hour, each hour's events written under ``out/lag-events``; returns
    the span read and the number of moves seen."""
    start, end = study_span(live, hours)
    files = hour_files(live, "ticker", start, end)
    folder = out / EVENT_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.glob("*.parquet"):
        stale.unlink()
    info = market_info(markets, tiers_of(live, start, end), fees)
    gaps = gaps_of(live, end)
    carried = empty_frames()
    current: pl.DataFrame | None = None
    total = 0
    for path in files:
        hour = hour_of(path.stem)
        events, carried, found, current = study_hour(
            hour, live, pairs, info, gaps, carried, end, current
        )
        events.write_parquet(folder / path.name)
        total += found
        log(f"{path.stem}: {found:,} moves, {events.height:,} events")
    return start, end, total


def _a_day(value: int, hours: int) -> int:
    """A span's sum scaled to a day."""
    return int(value * 24 // hours)


def _cells(frame: pl.LazyFrame, keys: list[str], hours: int) -> list[dict[str, Any]]:
    """The figures of each cell of ``keys``: events, pairs, responses, first moves, the trades
    through each stale quote, and the counted quotes' capacity, for every quote and for the
    reachable ones."""
    counted = pl.col("counted")
    reachable = counted & pl.col("reachable")
    aggs = [
        pl.len().alias("events"),
        pl.struct("a", "b").n_unique().alias("pairs"),
        pl.col("b").n_unique().alias("markets_b"),
        pl.col("group").n_unique().alias("groups"),
        pl.col("signed").first().alias("signed"),
        pl.col("lag_ms").is_not_null().sum().alias("moved"),
        (pl.col("agreement") == 1).sum().alias("agreeing"),
        (pl.col("with_n") > 0).sum().alias("through_with_events"),
        (pl.col("against_n") > 0).sum().alias("through_against_events"),
        pl.col("with_e2").filter(counted).sum().alias("through_with_e2"),
        pl.col("against_e2").filter(counted).sum().alias("through_against_e2"),
        pl.col("stale_size_e2").median().alias("stale_size_median_e2"),
        (pl.col("first_of_spell") & pl.col("signed") & pl.col("fee_e6").is_null())
        .sum()
        .alias("fee_unknown"),
    ]
    for prefix, flag in (("", counted), ("reachable_", reachable)):
        for money in ("gross_e6", "fee_e6", "net_e6"):
            aggs.append(pl.col(money).filter(flag).sum().alias(f"{prefix}{money}"))
        aggs.append(flag.sum().alias(f"{prefix}quotes"))
    for name, delta in HORIZONS_MS.items():
        aggs.append(pl.col(f"r_{name}").mean().alias(f"response_e6_{name}"))
        aggs.append((pl.col("lag_ms") <= delta).sum().alias(f"moved_within_{name}"))
    rows = frame.group_by(keys).agg(aggs).sort(keys).collect().to_dicts()
    temporary = ["agreeing", "moved", "through_with_events", "through_against_events",
                 "through_with_e2", "through_against_e2", "fee_unknown"]  # fmt: skip
    for prefix in ("", "reachable_"):
        temporary += [f"{prefix}{k}" for k in ("gross_e6", "fee_e6", "net_e6", "quotes")]
    for row in rows:
        events = row["events"]
        row["events_a_day"] = _a_day(row["events"], hours)
        responses = {name: row[f"response_e6_{name}"] for name in HORIZONS_MS}
        row["response_cents"] = {
            name: None if r is None else r / CENTS_E6 for name, r in responses.items()
        }
        row["moved_within"] = {name: row[f"moved_within_{name}"] / events for name in HORIZONS_MS}
        row["agreement"] = None if row["moved"] == 0 else row["agreeing"] / row["moved"]
        row["through_with_share"] = row["through_with_events"] / events
        row["through_against_share"] = row["through_against_events"] / events
        row["through_with_e2_a_day"] = _a_day(row["through_with_e2"], hours)
        row["through_against_e2_a_day"] = _a_day(row["through_against_e2"], hours)
        row["stale_quotes_a_day"] = _a_day(row["quotes"], hours)
        if row["signed"]:
            row["capacity"] = {
                "gross_e6_a_day": _a_day(row["gross_e6"], hours),
                "fee_e6_a_day": _a_day(row["fee_e6"], hours),
                "net_e6_a_day": _a_day(row["net_e6"], hours),
                "quotes_a_day": _a_day(row["quotes"], hours),
                "reachable_gross_e6_a_day": _a_day(row["reachable_gross_e6"], hours),
                "reachable_fee_e6_a_day": _a_day(row["reachable_fee_e6"], hours),
                "reachable_net_e6_a_day": _a_day(row["reachable_net_e6"], hours),
                "reachable_quotes_a_day": _a_day(row["reachable_quotes"], hours),
                "fee_unknown": row["fee_unknown"],
                "clears": _a_day(row["net_e6"], hours) >= BAR_E6,
                "clears_reachable": _a_day(row["reachable_net_e6"], hours) >= BAR_E6,
            }
        else:
            row["capacity"] = None
        for name in HORIZONS_MS:
            del row[f"response_e6_{name}"], row[f"moved_within_{name}"]
        for key in temporary:
            del row[key]
    return rows


def _interval(estimate: Estimate, scale: float) -> dict[str, float | int]:
    return {
        "point": estimate.point_e6 * scale,
        "lower": estimate.lower_e6 * scale,
        "upper": estimate.upper_e6 * scale,
        "clusters": estimate.events,
    }


def _intervals(usable: pl.LazyFrame, hours: int, start_ms: int) -> dict[str, dict[str, Any]]:
    """Per kind: the 10-minute response's interval over (group, hour) clusters, in cents, and the
    counted capacity's interval (every quote, and the reachable ones) over the span's hours, in
    millionths a day."""
    headline = pl.col(f"r_{HEADLINE}")
    by_cluster = (
        usable.filter(headline.is_not_null())
        .group_by("kind", "group", "hour")
        .agg(
            headline.sum().cast(pl.Float64).alias("num"),
            headline.count().cast(pl.Float64).alias("den"),
        )
        .sort("kind", "group", "hour")  # a group-by's order is not fixed; the draws must be
        .collect()
    )
    nets = usable.filter(pl.col("counted") & pl.col("net_e6").is_not_null())
    by_hour = (
        nets.group_by("kind", "hour")
        .agg(
            pl.col("net_e6").sum().cast(pl.Float64).alias("num"),
            pl.col("net_e6").filter(pl.col("reachable")).sum().cast(pl.Float64).alias("reach"),
        )
        .collect()
    )
    first_hour = start_ms // MS_PER_HOUR  # the span starts on an hour (its hour files)
    out: dict[str, dict[str, Any]] = {}
    for kind in sorted(set(by_cluster["kind"].to_list()) | set(by_hour["kind"].to_list())):
        cells = by_cluster.filter(pl.col("kind") == kind)
        response = cluster_bootstrap(
            cells["num"].to_numpy(),
            cells["den"].to_numpy(),
            tail=TAIL,
            resamples=RESAMPLES,
            seed=SEED,
        )
        entry: dict[str, Any] = {"response_10m_cents": _interval(response, 1 / CENTS_E6)}
        hourly = by_hour.filter(pl.col("kind") == kind)
        if hourly.height:
            for column, name in (("num", "net_e6_a_day"), ("reach", "reachable_net_e6_a_day")):
                per_hour = np.zeros(hours, dtype=np.float64)
                for row in hourly.to_dicts():
                    index = int(row["hour"]) - first_hour
                    if 0 <= index < hours:  # pragma: no branch - events lie inside the span
                        per_hour[index] += row[column]
                capacity = cluster_bootstrap(
                    per_hour, np.ones(hours), tail=TAIL, resamples=RESAMPLES, seed=SEED
                )
                entry[name] = _interval(capacity, 24)
        out[kind] = entry
    return out


def summarize(out: Path, start: datetime, end: datetime, moves_total: int) -> dict[str, Any]:
    """S3.2's figures from the written events: by kind, by kind and tier, by kind and category,
    by kind and the size of a's move, the slow spots, and M4's bar."""
    hours = int((end - start) / timedelta(hours=1))
    if hours < 1:
        raise SurveyDataError("the span holds no whole hour")
    files = sorted((out / EVENT_FOLDER).glob("*.parquet"))
    if not files:
        raise SurveyDataError(f"no events under {out / EVENT_FOLDER}")
    events = pl.scan_parquet(files).with_columns(hour=pl.col("t") // MS_PER_HOUR)
    counts = events.select(
        pl.len().alias("events"),
        (pl.col("state") == "unknown").sum().alias("unknown_b"),
        (pl.col("state") == "onesided").sum().alias("onesided_b"),
        (pl.col("state") == "extreme").sum().alias("extreme_b"),
        ((pl.col("state") == "ok") & pl.col("in_gap")).sum().alias("gap_events"),
        pl.struct("a", "t").n_unique().alias("moves_with_partner"),
    ).collect().to_dicts()[0]  # fmt: skip
    usable = events.filter((pl.col("state") == "ok") & ~pl.col("in_gap"))
    # a stale quote's spell: the same watched market, side and first move; its first event counts
    # (a quote that never answered within the hour is counted once an hour it stood: that tells a
    # market that re-quotes and stands again apart, without counting every size refresh); its
    # first event counts when the 10-minute response and the fee are known
    usable = usable.with_columns(
        spell_hour=pl.when(pl.col("first_ms").is_null()).then(pl.col("hour"))
    )
    spells = ["kind", "b", "expected", "first_ms", "spell_hour"]
    firsts = usable.group_by(spells).agg(pl.col("t").min().alias("t_first"))
    first = (pl.col("t") == pl.col("t_first")).fill_null(False)
    measurable = pl.col(f"r_{HEADLINE}").is_not_null() & (
        ~pl.col("signed") | pl.col("fee_e6").is_not_null()
    )
    usable = usable.join(firsts, on=spells, how="left", nulls_equal=True).with_columns(
        first_of_spell=first,
        counted=first & measurable,
        reachable=pl.col("lag_known")
        & (pl.col("lag_ms").is_null() | (pl.col("lag_ms") >= REACH_MS)),
        move=_bucket(pl.col("a_move_e6"), MOVE_BUCKETS),
    )
    by_kind = {row["kind"]: row for row in _cells(usable, ["kind"], hours)}
    for row in by_kind.values():
        del row["kind"]
    by_tier = _cells(usable, ["kind", "tier_b"], hours)
    by_category = _cells(usable, ["kind", "category_b"], hours)
    by_move = _cells(usable, ["kind", "move"], hours)
    spots = sorted(
        (
            r
            for r in _cells(
                usable.filter(pl.col("signed")), ["kind", "series_a", "series_b"], hours
            )
            if r["capacity"] is not None and r["capacity"]["reachable_quotes_a_day"] > 0
        ),
        key=lambda r: -r["capacity"]["reachable_net_e6_a_day"],
    )[:SPOTS]
    intervals = _intervals(usable, hours, int(start.timestamp() * 1000))
    for kind, entry in intervals.items():
        by_kind[kind]["intervals"] = entry
    keep = ("events", "events_a_day", "pairs", "response_cents", "moved_within", "agreement",
            "through_with_share", "through_against_share", "stale_quotes_a_day",
            "capacity")  # fmt: skip

    def cleared(key: str) -> list[str]:
        return sorted(
            kind for kind, row in by_kind.items() if row["capacity"] and row["capacity"][key]
        )

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "hours": hours,
        "moves": moves_total,
        **counts,
        "usable_events": usable.select(pl.len()).collect().item(),
        "lag_unknown": usable.select((~pl.col("lag_known")).sum()).collect().item(),
        "by_kind": by_kind,
        "by_tier": {f"{r['kind']} | {r['tier_b']}": {k: r[k] for k in keep} for r in by_tier},
        "by_category": {
            f"{r['kind']} | {r['category_b']}": {k: r[k] for k in keep} for r in by_category
        },
        "by_move": {f"{r['kind']} | {r['move']}": {k: r[k] for k in keep} for r in by_move},
        "slow_spots": [
            {
                "kind": r["kind"],
                "series_a": r["series_a"],
                "series_b": r["series_b"],
                **{k: r[k] for k in keep},
            }
            for r in spots
        ],  # fmt: skip
        "cleared": cleared("clears"),
        "cleared_reachable": cleared("clears_reachable"),
    }
