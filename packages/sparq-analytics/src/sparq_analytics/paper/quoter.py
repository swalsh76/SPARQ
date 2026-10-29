"""S4.1: a paper quoter in the thin tail, on the fit part (PLAN §6.4, T1 to T6).

Nothing here sends an order. The quoter replays the ingest's live frames and trades copied to the
analytics node and asks what a maker standing inside the thin tail's wide spreads would have
filled and kept:

* **Scope** (T2): markets under 1,000 contracts a day over their days open in the span (S3.3's
  tiers), in every category, while the mid (or the one standing price) is between 3 and 97 cents,
  at frames whose book is **uncontested** (a spread wider than 20 cents) or **one-sided**.
* **The paper quote** (T3): on each side, ``k`` cents inside the standing quote (5 and 10); on an
  empty side, 20 cents from the standing one; ``S`` contracts (10 and 50). It is placed at the frame
  and dies at the market's next frame, or at the market's close, so no stale quote stands.
* **Fills** (T4): a taker trade strictly after the frame and before the quote dies fills it when its
  price is strictly beyond the paper quote (a yes taker above the paper ask, a no taker below the
  paper bid), for the smaller of ``S`` and the trade's size; one fill a paper quote and side at
  most, across hours too; block trades never.
* **Marks** (T5): the fill against the mid 10 minutes and 1 hour later (null past the copied
  frames, the market's close or a dropped day) and against the market's last two-sided mid in the
  kept hours at or after the fill (its pre-close mid when it closed in the span, else **censored**;
  null when no two-sided frame followed the fill), less the maker fee from the series' terms
  (unknown without them). Whether the market traded again after the fill is noted.
* **Days** (T6): a UTC day with a logged outage over ten minutes leaves the fit part, the book is
  not carried across it, and the figures a day scale by the hours kept.

Money is integer millionths of a dollar; sizes are hundredths of a contract; the bootstrap
clusters are (event, day).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from sparq_core.fixedpoint import E6

from sparq_analytics.live.source import HOUR, hour_files, hour_name, hour_of
from sparq_analytics.study.bootstrap import cluster_bootstrap
from sparq_analytics.study.feecalc import fee_terms_known, maker_fee_expr, maker_share_expr
from sparq_analytics.survey.dump import (
    CENTS,
    MS_PER_HOUR,
    RESAMPLES,
    TAIL,
    UNKNOWN,
    SurveyDataError,
)
from sparq_analytics.survey.lag import EXTREME_E6, a_day, empty_frames, frames_of, trades_of
from sparq_analytics.survey.queue import gaps_of, span
from sparq_analytics.survey.tail import COMPETING_E6, THIN_TIERS, outage_overlap, tiered

INSIDE_E6 = (50_000, 100_000)  # k: 5 and 10 cents inside the standing quote
SIZES_E2 = (1_000, 5_000)  # S: 10 and 50 contracts
EMPTY_SIDE_E6 = COMPETING_E6  # on an empty side, 20 cents from the standing quote (T3)
MARKS_MS = {"10m": 600_000, "1h": 3_600_000}
MARKS = (*MARKS_MS, "close")
OUTAGE_DAY_MS = 600_000  # T6: a day with an outage over ten minutes leaves the parts
SEED = 34
TRADE_COLUMNS = ("ts_ms", "ticker", "yes_price_e6", "count_e2", "taker_side")
FILL_KEYS = ["ticker", "frame_ms", "k_e6", "side"]
CELL_KEYS = ("state", "k_e6", "tier", "category")
Gaps = list[tuple[int, int]]


# -- the days and the markets ---------------------------------------------------------------------


def outage_days(gaps: pl.DataFrame) -> list[date]:
    """The UTC days touched by a logged outage longer than ten minutes (T6)."""
    days: set[date] = set()
    for row in gaps.filter(pl.col("end_ms") - pl.col("start_ms") > OUTAGE_DAY_MS).to_dicts():
        day = datetime.fromtimestamp(row["start_ms"] / 1000, tz=UTC).date()
        last = datetime.fromtimestamp((row["end_ms"] - 1) / 1000, tz=UTC).date()
        while day <= last:
            days.add(day)
            day += timedelta(days=1)
    return sorted(days)


def kept_hours(live: Path, start: datetime, end: datetime, dropped: list[date]) -> list[Path]:
    """The span's ticker hour files outside the dropped days."""
    gone = set(dropped)
    return [p for p in hour_files(live, "ticker", start, end) if hour_of(p.stem).date() not in gone]


def market_info(live: Path, fees: pl.DataFrame, start: datetime, end: datetime) -> pl.DataFrame:
    """Each listed market's event, category, tier (S3.3's rule over the span), close and fee
    terms; the thin ones are the quoter's scope."""
    markets = pl.read_parquet(
        live / "markets.parquet",
        columns=["ticker", "series_ticker", "event_ticker", "category", "open_ms", "close_ms"],
    )
    contracts = (
        pl.scan_parquet(hour_files(live, "trade", start, end))
        .group_by("ticker")
        .agg(pl.col("count_e2").sum().alias("contracts_e2"))
        .collect()
    )
    span_ms = (int(start.timestamp() * 1000), int(end.timestamp() * 1000))
    return (
        tiered(markets.join(contracts, on="ticker", how="left"), *span_ms)
        .with_columns(
            pl.col("category").fill_null(UNKNOWN),
            pl.col("event_ticker").fill_null(pl.col("ticker")),
        )
        .join(fees, on="series_ticker", how="left")
        .with_columns(share=maker_share_expr(pl.col("fee_type"), pl.col("multiplier_e6")))
        .select(
            "ticker", "series_ticker", "event_ticker", "category", "tier", "close_ms",
            "multiplier_e6", "share",
        )
    )  # fmt: skip


# -- the paper quotes and their fills ------------------------------------------------------------


@dataclass(frozen=True)
class Scope:
    """What the quoter quotes: the values of ``k`` (cents inside, in millionths) and, for the
    registered test, only markets closing within ``close_within_ms`` of the quote."""

    inside: tuple[int, ...] = INSIDE_E6
    close_within_ms: int | None = None


FIT = Scope()


def paper_quotes(
    book: pl.DataFrame,
    info: pl.DataFrame,
    hour_start_ms: int,
    hour_end_ms: int,
    scope: Scope = FIT,
) -> pl.DataFrame:
    """The paper quotes standing in one hour: for every frame of a thin market whose book is
    uncontested or one-sided (and not in the certainty regime), one row a value of ``k`` with the
    paper bid and ask (null where a side cannot be quoted) and the window the quote stands in."""
    ordered = book.join(
        info.filter(pl.col("tier").is_in(THIN_TIERS)), on="ticker", how="inner"
    ).sort("ticker", "ts_ms", maintain_order=True)
    bid, ask = pl.col("yes_bid_e6"), pl.col("yes_ask_e6")
    next_ms = pl.col("ts_ms").shift(-1).over("ticker").fill_null(hour_end_ms)
    state = (
        pl.when(bid.is_not_null() & ask.is_not_null())
        .then(pl.when(ask - bid > COMPETING_E6).then(pl.lit("uncontested")))
        .when(bid.is_not_null() | ask.is_not_null())
        .then(pl.lit("one_sided"))
    )
    regime = pl.coalesce(pl.col("mid_e6"), bid, ask)  # the mid, or the one standing price
    timed = ordered.with_columns(
        frame_ms=pl.col("ts_ms"),
        valid_from=pl.max_horizontal("ts_ms", hour_start_ms),
        valid_to=pl.min_horizontal(next_ms, pl.col("close_ms").fill_null(hour_end_ms)),
        state=state,
        regime=regime,
    )
    closing_soon = (
        pl.lit(True)
        if scope.close_within_ms is None
        else (pl.col("close_ms") - pl.col("frame_ms") <= scope.close_within_ms).fill_null(False)
    )
    kept = timed.filter(
        pl.col("state").is_not_null()
        & (pl.col("valid_to") > pl.col("valid_from"))
        & (pl.col("regime") > EXTREME_E6)
        & (pl.col("regime") < E6 - EXTREME_E6)
        & closing_soon
    )
    quoted = kept.join(pl.DataFrame({"k_e6": list(scope.inside)}), how="cross")
    k = pl.col("k_e6")
    pbid = pl.when(bid.is_not_null()).then(bid + k).otherwise(ask - EMPTY_SIDE_E6)
    pask = pl.when(ask.is_not_null()).then(ask - k).otherwise(bid + EMPTY_SIDE_E6)
    return quoted.with_columns(
        pbid_e6=pl.when((pbid > 0) & (pbid < pask)).then(pbid),
        pask_e6=pl.when((pask < E6) & (pbid < pask)).then(pask),
    ).select(
        "ticker", "frame_ms", "valid_from", "valid_to", "state", "k_e6", "pbid_e6", "pask_e6",
        "event_ticker", "category", "tier", "close_ms", "multiplier_e6", "share",
    )  # fmt: skip


def fills_of(trades: pl.DataFrame, quotes: pl.DataFrame, filled: pl.DataFrame) -> pl.DataFrame:
    """The fills: each trade is matched to the paper quotes placed at its market's last frame
    strictly before it; a yes taker above the paper ask fills the ask, a no taker below the paper
    bid fills the bid; the first such trade a quote and side is the fill, and a quote in
    ``filled`` (filled in an earlier hour) is not filled again."""
    frames = quotes.select("ticker", "frame_ms").unique().sort("frame_ms", maintain_order=True)
    hit = (
        trades.with_columns(key=pl.col("ts_ms") - 1)
        .sort("key", maintain_order=True)
        .join_asof(
            frames,
            left_on="key",
            right_on="frame_ms",
            by="ticker",
            strategy="backward",
            check_sortedness=False,
        )  # fmt: skip
        .drop("key")
        .filter(pl.col("frame_ms").is_not_null())
        .join(quotes, on=["ticker", "frame_ms"], how="inner")
        .filter(pl.col("ts_ms") < pl.col("valid_to"))
    )
    price, taker = pl.col("yes_price_e6"), pl.col("taker_side")
    bids = hit.filter((taker == "no") & (price < pl.col("pbid_e6"))).with_columns(
        side=pl.lit("bid"), paper_price_e6=pl.col("pbid_e6")
    )
    asks = hit.filter((taker == "yes") & (price > pl.col("pask_e6"))).with_columns(
        side=pl.lit("ask"), paper_price_e6=pl.col("pask_e6")
    )
    first = (
        pl.concat([bids, asks])
        .sort("ts_ms", maintain_order=True)
        .group_by(FILL_KEYS, maintain_order=True)
        .first()
        .join(filled, on=FILL_KEYS, how="anti")
    )
    return (
        first.rename(
            {"ts_ms": "fill_ms", "yes_price_e6": "trade_price_e6", "count_e2": "trade_count_e2"}
        )
        .with_columns(
            [pl.min_horizontal("trade_count_e2", s).alias(f"fill_e2_{s}") for s in SIZES_E2]
        )
        .drop("taker_side", "pbid_e6", "pask_e6", "valid_from", "valid_to")
    )


def with_marks(fills: pl.DataFrame, two_sided: pl.DataFrame, loaded_end_ms: int) -> pl.DataFrame:
    """The mid 10 minutes and 1 hour after each fill (null past the copied frames or the market's
    close)."""
    right = two_sided.select(
        ts_ms=pl.col("ts_ms"), ticker=pl.col("ticker"), mid_k=pl.col("mid_e6")
    ).sort("ts_ms", maintain_order=True)
    out = fills
    for name, delta in MARKS_MS.items():
        k = pl.col("k")
        known = (k < loaded_end_ms) & (k < pl.col("close_ms")).fill_null(True)
        out = (
            out.with_columns(k=pl.col("fill_ms") + delta)
            .sort("k", maintain_order=True)
            .join_asof(
                right,
                left_on="k",
                right_on="ts_ms",
                by="ticker",
                strategy="backward",
                check_sortedness=False,
            )  # fmt: skip
            .with_columns(pl.when(known).then(pl.col("mid_k")).alias(f"mid_{name}"))
            .drop("k", "ts_ms", "mid_k")
        )
    return out


def quote_time(quotes: pl.DataFrame, gaps: Gaps) -> pl.DataFrame:
    """The paper quotes' standing time in the hour, outages taken out, by state, ``k``, tier and
    category, with the number of quote rows."""
    dur = (
        pl.col("valid_to") - pl.col("valid_from")
        - outage_overlap(pl.col("valid_from"), pl.col("valid_to"), gaps)
    ).clip(lower_bound=0)  # fmt: skip
    return (
        quotes.with_columns(dur_ms=dur)
        .group_by(*CELL_KEYS)
        .agg(pl.len().alias("quotes"), pl.col("dur_ms").sum().alias("quote_ms"))
    )


@dataclass
class Carry:
    """What one hour hands the next: each market's last frame (the standing book), the paper
    quotes filled so far (a quote carried across the hour is not filled again), each market's
    last two-sided mid and its time (the close mark), and its last trade time."""

    frames: pl.DataFrame
    filled: pl.DataFrame
    last_mid: pl.DataFrame
    last_trade: pl.DataFrame


def empty_carry() -> Carry:
    return Carry(
        frames=empty_frames(),
        filled=pl.DataFrame(
            schema={"ticker": pl.String, "frame_ms": pl.Int64, "k_e6": pl.Int64, "side": pl.String}
        ),
        last_mid=pl.DataFrame(
            schema={"ticker": pl.String, "mid_close": pl.Int64, "last_frame_ms": pl.Int64}
        ),
        last_trade=pl.DataFrame(schema={"ticker": pl.String, "last_trade_ms": pl.Int64}),
    )


def _last(frame: pl.DataFrame, by_ms: str) -> pl.DataFrame:
    """Each market's last row of ``frame`` by ``by_ms``."""
    return (
        frame.sort("ticker", by_ms, maintain_order=True)
        .group_by("ticker", maintain_order=True)
        .last()
    )


def study_hour(
    hour: datetime,
    live: Path,
    info: pl.DataFrame,
    gaps: Gaps,
    carry: Carry,
    span_end: datetime,
    current: pl.DataFrame | None = None,
    read_following: bool = True,
    scope: Scope = FIT,
) -> tuple[pl.DataFrame, pl.DataFrame, Carry, pl.DataFrame | None]:
    """One hour's fills with their 10-minute and 1-hour marks, its quote time, what the hour hands
    the next, and the following hour's frames (read for the marks when asked and copied before
    ``span_end``)."""
    if current is None:
        current = frames_of(live / "ticker" / f"{hour_name(hour)}.parquet")
    next_path = live / "ticker" / f"{hour_name(hour + HOUR)}.parquet"
    following = (
        frames_of(next_path)
        if read_following and next_path.exists() and hour + HOUR < span_end
        else None
    )
    loaded_end = hour + (2 * HOUR if following is not None else HOUR)
    hour_start_ms, hour_end_ms = int(hour.timestamp() * 1000), int((hour + HOUR).timestamp() * 1000)
    book = pl.concat([carry.frames, current])
    quotes = paper_quotes(book, info, hour_start_ms, hour_end_ms, scope)
    trades = trades_of(live, [hour], TRADE_COLUMNS)
    found = fills_of(trades, quotes, carry.filled)
    two_sided = pl.concat([book] + ([following] if following is not None else [])).filter(
        pl.col("mid_e6").is_not_null()
    )
    found = with_marks(found, two_sided, int(loaded_end.timestamp() * 1000))
    seen = book.filter(pl.col("mid_e6").is_not_null()).select(
        "ticker", mid_close=pl.col("mid_e6"), last_frame_ms=pl.col("ts_ms")
    )
    traded = trades.select("ticker", last_trade_ms=pl.col("ts_ms"))
    handed = Carry(
        frames=_last(book, "ts_ms").select(book.columns).with_columns(carried=pl.lit(True)),
        filled=pl.concat([carry.filled, found.select(FILL_KEYS)]),
        last_mid=_last(pl.concat([carry.last_mid, seen]), "last_frame_ms"),
        last_trade=_last(pl.concat([carry.last_trade, traded]), "last_trade_ms"),
    )
    return found, quote_time(quotes, gaps), handed, following


@dataclass(frozen=True)
class Replay:
    """A replay's fills (with their marks), the quote time by cell, the hours kept and the days
    dropped."""

    fills: pl.DataFrame
    times: pl.DataFrame
    hours: int
    dropped: list[date]


def replay(
    live: Path,
    fees: pl.DataFrame,
    start: datetime,
    end: datetime,
    scope: Scope = FIT,
    log: Callable[[str], None] = lambda _: None,
    marks_end: datetime | None = None,
) -> Replay:
    """The paper quoter over ``[start, end)`` of the live copies, hour by hour; the marks may read
    frames to ``marks_end`` (the copies' end, for a window inside them), else to ``end``."""
    marks_end = end if marks_end is None else marks_end
    span_end_ms = int(end.timestamp() * 1000)
    gap_frame = gaps_of(live, end)
    # only the window's days can be dropped: an outage outside it drops nothing
    last_day = (end - timedelta(milliseconds=1)).date()
    dropped = [d for d in outage_days(gap_frame) if start.date() <= d <= last_day]
    files = kept_hours(live, start, end, dropped)
    if not files:
        raise SurveyDataError("every hour of the span lies in a dropped day")
    gaps: Gaps = [(r["start_ms"], r["end_ms"]) for r in gap_frame.to_dicts()]
    info = market_info(live, fees, start, end)
    carry = empty_carry()
    current: pl.DataFrame | None = None
    fills: list[pl.DataFrame] = []
    times: list[pl.DataFrame] = []
    for i, path in enumerate(files):
        hour = hour_of(path.stem)
        next_kept = i + 1 < len(files) and hour_of(files[i + 1].stem) == hour + HOUR
        # the last kept hour's marks may read the hour after it when that hour is copied and kept
        read_following = next_kept or (
            i + 1 == len(files) and (hour + HOUR).date() not in set(dropped)
        )
        found, timed, carry, following = study_hour(
            hour, live, info, gaps, carry, marks_end, current, read_following, scope
        )
        current = following if next_kept else None
        if not next_kept:  # across a dropped day the book is not carried: a day-old quote is none
            carry = Carry(empty_frames(), carry.filled.clear(), carry.last_mid, carry.last_trade)
        fills.append(found)
        times.append(timed)
        log(f"{path.stem}: {found.height:,} fills")
    all_fills = (
        pl.concat(fills)
        .join(carry.last_mid, on="ticker", how="left")
        .join(carry.last_trade, on="ticker", how="left")
        .with_columns(
            # the close mark must follow the fill: a mid seen only before it marks nothing
            mid_close=pl.when(pl.col("last_frame_ms") >= pl.col("fill_ms")).then(
                pl.col("mid_close")
            ),
            censored=pl.col("close_ms").is_null() | (pl.col("close_ms") > span_end_ms),
            traded_again=(pl.col("last_trade_ms") > pl.col("fill_ms")).fill_null(False),
        )
        .sort("fill_ms", "ticker", "k_e6", "side", maintain_order=True)
    )
    return Replay(all_fills, pl.concat(times), len(files), dropped)


def run(
    live: Path,
    fees: pl.DataFrame,
    out: Path,
    hours: int | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> dict[str, Any]:
    """The whole fit-part reading: the fills written to ``out/paper-fills.parquet`` and the
    figures returned (and written as ``paper.json`` by the command line)."""
    start, end = span(live)
    if hours is not None:
        end = min(end, start + hours * HOUR)
    played = replay(live, fees, start, end, FIT, log)
    out.mkdir(parents=True, exist_ok=True)
    played.fills.write_parquet(out / "paper-fills.parquet")
    return summarize(played.fills, played.times, start, end, played.hours, played.dropped)


# -- the figures -----------------------------------------------------------------------------------


def _pnl(fills: pl.DataFrame, marks: tuple[str, ...] = MARKS) -> pl.DataFrame:
    """Per fill and size: the dollars against each mark, the maker fee and the net."""
    sign = pl.when(pl.col("side") == "bid").then(1).otherwise(-1)
    terms = fee_terms_known(pl.col("share"), pl.col("multiplier_e6"))
    columns = []
    for s in SIZES_E2:
        fill = pl.col(f"fill_e2_{s}")
        fee = (
            pl.when(terms)
            .then(
                maker_fee_expr(
                    pl.col("multiplier_e6"), pl.col("share"), fill, pl.col("paper_price_e6")
                )
            )
            .cast(pl.Int64)
        )
        columns.append(fee.alias(f"fee_{s}"))
        for mark in marks:
            mid = pl.col(f"mid_{mark}")
            gross = pl.when(mid.is_not_null()).then(
                sign * (mid - pl.col("paper_price_e6")) * fill // 100
            )
            columns.append(gross.alias(f"gross_{mark}_{s}"))
            columns.append((gross - fee).alias(f"net_{mark}_{s}"))
    return fills.with_columns(columns).with_columns(
        fee_unknown=~terms,
        day=(pl.col("fill_ms") // (24 * MS_PER_HOUR)),
        hours_to_close=(pl.col("close_ms") - pl.col("fill_ms")) / MS_PER_HOUR,
    )


def _interval(frame: pl.DataFrame, column: str, hours: int) -> dict[str, Any]:
    """The net a day's cluster-bootstrap interval over (event, day) clusters."""
    cells = (
        frame.filter(pl.col(column).is_not_null())
        .group_by("event_ticker", "day")
        .agg(pl.col(column).sum().cast(pl.Float64).alias("num"))
        .sort("event_ticker", "day")
    )
    if cells.height < 2:  # one cluster has no spread to bootstrap
        total = float(cells["num"].sum()) * 24 / hours if cells.height else None
        return {"point": total, "lower": None, "upper": None, "clusters": cells.height}
    estimate = cluster_bootstrap(
        cells["num"].to_numpy(), np.ones(cells.height), tail=TAIL, resamples=RESAMPLES, seed=SEED
    )
    scale = cells.height * 24 / hours  # the mean a cluster, over the clusters, a day
    return {
        "point": estimate.point_e6 * scale,
        "lower": estimate.lower_e6 * scale,
        "upper": estimate.upper_e6 * scale,
        "clusters": cells.height,
    }


def _cells(
    fills: pl.DataFrame,
    times: pl.DataFrame,
    keys: list[str],
    hours: int,
    intervals: bool,
    marks: tuple[str, ...] = MARKS,
) -> dict[str, dict[str, Any]]:
    """The figures of each cell of ``keys`` (the rule, state and k, with a category or tier):
    one entry a size, built from the quote time, so a cell quoted but never filled shows its
    quote-hours and no fills."""
    aggs = [
        pl.len().alias("fills"),
        pl.col("ticker").n_unique().alias("markets"),
        pl.col("fee_unknown").sum().alias("fee_unknown"),
        pl.col("censored").mean().alias("censored"),
        (~pl.col("traded_again")).mean().alias("never_traded_again"),
        pl.col("hours_to_close").median().alias("hours_to_close_median"),
    ]
    for s in SIZES_E2:
        aggs.append(pl.col(f"fill_e2_{s}").sum().alias(f"contracts_{s}"))
        aggs.append(pl.col(f"fee_{s}").sum().alias(f"fee_{s}"))
        for mark in marks:
            netted = pl.col(f"net_{mark}_{s}")
            aggs.append(pl.col(f"gross_{mark}_{s}").sum().alias(f"gross_{mark}_{s}"))
            aggs.append(netted.sum().alias(f"net_{mark}_{s}"))
            aggs.append(pl.col(f"gross_{mark}_{s}").is_not_null().sum().alias(f"marked_{mark}_{s}"))
            aggs.append(netted.is_not_null().sum().alias(f"netted_fills_{mark}_{s}"))
            aggs.append(
                pl.col(f"fill_e2_{s}")
                .filter(netted.is_not_null())
                .sum()
                .alias(f"netted_{mark}_{s}")
            )
    timed = times.group_by(keys).agg(pl.col("quotes").sum(), pl.col("quote_ms").sum())
    rows = (
        timed.join(fills.group_by(keys).agg(aggs), on=keys, how="full", coalesce=True)
        .sort(keys)
        .to_dicts()
    )
    parts = fills.partition_by(keys, as_dict=True) if intervals else {}
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        sub = parts.get(tuple(r[k] for k in keys))
        n_fills, quote_hours = r["fills"] or 0, (r["quote_ms"] or 0) / MS_PER_HOUR
        for s in SIZES_E2:
            contracts = (r[f"contracts_{s}"] or 0) / 100
            cell: dict[str, Any] = {
                "fills": n_fills,
                "markets": r["markets"] or 0,
                "contracts": contracts,
                "quotes": r["quotes"] or 0,
                "quote_hours_a_day": quote_hours * 24 / hours,
                "fills_a_quote_hour": None if quote_hours == 0 else n_fills / quote_hours,
                "hours_to_close_median": r["hours_to_close_median"],
                "censored": r["censored"],
                "never_traded_again": r["never_traded_again"],
                "fee_unknown_fills": r["fee_unknown"] or 0,
                "fee_e6_a_day": a_day(r[f"fee_{s}"] or 0, hours),
                "marks": {},
            }
            for mark in marks:
                # the net, and the cents a contract, are over the marked fills whose fee is known
                net_e6: int = r[f"net_{mark}_{s}"] or 0
                over: float = (r[f"netted_{mark}_{s}"] or 0) / 100
                entry: dict[str, Any] = {
                    "marked_fills": r[f"marked_{mark}_{s}"] or 0,
                    "netted_fills": r[f"netted_fills_{mark}_{s}"] or 0,
                    "gross_e6_a_day": a_day(r[f"gross_{mark}_{s}"] or 0, hours),
                    "net_e6_a_day": a_day(net_e6, hours),
                    "netted_contracts": over,
                    "cents_a_contract": None if over == 0 else net_e6 / over / CENTS,
                }
                if sub is not None and mark != "10m":
                    entry["interval_e6_a_day"] = _interval(sub, f"net_{mark}_{s}", hours)
                cell["marks"][mark] = entry
            out[" | ".join(str(r[k]) for k in keys) + f" | {s // 100}"] = cell
    return out


def summarize(
    fills: pl.DataFrame,
    times: pl.DataFrame,
    start: datetime,
    end: datetime,
    hours: int,
    dropped: list[date],
) -> dict[str, Any]:
    """S4.1's figures: by rule (state, k, S), by rule and category, and by rule and tier."""
    priced = _pnl(fills)
    rule = ["state", "k_e6"]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "hours_kept": hours,
        "dropped_days": [d.isoformat() for d in dropped],
        "fills": fills.height,
        "markets_filled": fills["ticker"].n_unique(),
        "by_rule": _cells(priced, times, rule, hours, intervals=True),
        "by_rule_and_category": _cells(priced, times, [*rule, "category"], hours, intervals=True),
        "by_rule_and_tier": _cells(priced, times, [*rule, "tier"], hours, intervals=False),
    }
