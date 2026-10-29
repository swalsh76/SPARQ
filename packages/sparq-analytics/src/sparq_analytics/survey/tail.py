"""S3.3: the thin tail and the surprise rate (PLAN §6.3, M2 to M4).

Three readings, none building anything:

* **Volume**, from the history's trade files (any day, M3: no outcome): each traded market's
  contracts and dollars a day over the days it was open inside the window (whole days, at least
  one), placed in the activity tiers S3.2 used; by tier and category the markets, trades,
  contracts and dollars a day; the markets open in the window that never traded (the silent
  tail); and the traded markets the market list lacks or gives no times for (tier ``unknown``).
* **Spreads and the half-spread**, from the live frames and trades: each market's time-weighted
  mean quoted spread while two-sided and the share of time a side was missing, by tier (the live
  span's own contracts a day over the days open, the same rule) and category; and, for every live
  trade, half the quoted spread just before it times its contracts, which is what a maker at the
  touch earns against the mid, summed by tier, category and whether a competing quote stood within
  10 cents of the mid at that moment (a spread of 20 cents or less: **contested**), none did
  (**uncontested**) or a side was missing (**one-sided**, no half-spread to read); gross and net of
  the maker fee where the series charges one. An empty side (the feed shows it as a 0-cent bid or
  a 100-cent ask with no size) is missing. A market's time ends at its close, the ingest's logged
  outages count for no one, and a trade whose last frame lies before an outage reads an **unknown**
  book. M4's bar is $250 a day of half-spread in thin markets' uncontested trades, a tenth captured
  being the $25 floor.
* **The surprise rate**, from the history to the cut (M3): per settled binary market the last trade
  from 36 to 12 hours before its close (among trades in one millisecond, the highest price); where
  a side then stood at 90 cents or more, whether the market settled against it. The rate by
  category, by price bucket and by series with enough markets, each with a cluster-bootstrap
  interval over events, against the price-implied rate (the mean of one minus the price); a cell
  is judged only with ten events or more; the series whose interval lies wholly above the implied
  rate are listed with their title and rules text, beside the number that would clear by chance
  alone.

Money is integer millionths of a dollar, scaled to a day by integer arithmetic; sizes are
hundredths of a contract; a trade's dollars are its yes price times its contracts, as in S3.1.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from sparq_core.fees import MAKER_SHARE_E2
from sparq_core.fixedpoint import E6

from sparq_analytics.live.source import hour_files, hour_of
from sparq_analytics.study.bootstrap import Estimate, cluster_bootstrap
from sparq_analytics.study.dataset import scan_markets
from sparq_analytics.study.feecalc import maker_fee_expr
from sparq_analytics.survey.dump import (
    CENTS,
    CUT,
    HISTORY_START,
    MS_PER_DAY,
    MS_PER_HOUR,
    RESAMPLES,
    TAIL,
    SurveyDataError,
    cut_markets,
    scan_trades,
    series_table,
    trade_files,
)
from sparq_analytics.survey.lag import TIERS, a_day, bucket, frames_of, trades_of
from sparq_analytics.survey.queue import gaps_of, span

THIN_TIERS = (TIERS[0][1], TIERS[1][1])  # under 1,000 contracts a day
UNKNOWN = "unknown"
COMPETING_E6 = 200_000  # a spread of 20 cents: a quote within 10 cents of the mid on each side
TAIL_E6 = 900_000  # a side at 90 cents or more the day before close
DAY_BEFORE_FROM_MS, DAY_BEFORE_TO_MS = 36 * MS_PER_HOUR, 12 * MS_PER_HOUR
MIN_SERIES_MARKETS = 50  # M4: a series needs this many settled markets in the tail
MIN_EVENTS = 10  # and this many events, or its interval means nothing
BUCKETS: tuple[tuple[int | None, str], ...] = (
    (950_000, "90 to 95"),
    (990_000, "95 to 99"),
    (None, "99 and more"),
)
HALF_SPREAD_BAR_E6 = 250 * E6
SEED = 33
TRADE_COLUMNS = ("ts_ms", "ticker", "yes_price_e6", "count_e2")
Gaps = list[tuple[int, int]]


# -- volume -------------------------------------------------------------------------------------


def trade_days(kalshi: Path) -> list[date]:
    """The days with a trade file, from the folder names; refuses a gap, because a missing day
    would read as a day without trades."""
    folder = kalshi / "trades"
    found = [p for p in folder.glob("date=*") if (p / "trades.parquet").exists()]
    days = sorted(date.fromisoformat(p.name[5:]) for p in found)
    if not days:
        raise SurveyDataError(f"no trade files under {folder}")
    expected = (days[-1] - days[0]).days + 1
    if len(days) != expected:
        raise SurveyDataError(
            f"{expected - len(days)} trade day(s) missing between {days[0]} and {days[-1]}"
        )
    return days


def _series(kalshi: Path) -> pl.DataFrame:
    return series_table(kalshi, ["category", "title"])


def tiered(markets: pl.DataFrame, window_start_ms: int, window_end_ms: int) -> pl.DataFrame:
    """Each market's contracts a day over its days open inside the window (whole days, at least
    one) and its tier; ``markets`` carries ``open_ms``, ``close_ms`` and ``contracts_e2`` (null
    for a market that never traded, which is in the lowest tier). A market without an open or
    close time has no life and the tier ``unknown``."""
    # min_horizontal and max_horizontal skip nulls, so a missing time is caught first
    known = pl.col("open_ms").is_not_null() & pl.col("close_ms").is_not_null()
    life_ms = pl.when(known).then(
        pl.min_horizontal("close_ms", window_end_ms) - pl.max_horizontal("open_ms", window_start_ms)
    )
    life_days = (-((-life_ms) // MS_PER_DAY)).clip(lower_bound=1)  # an exact ceiling
    rate = pl.col("contracts_e2").fill_null(0) / 100 / life_days
    tier = pl.when(rate.is_null()).then(pl.lit(UNKNOWN)).otherwise(bucket(rate, TIERS))
    return markets.with_columns(contracts_a_day=rate, tier=tier)


def volume(kalshi: Path, days: list[date]) -> dict[str, Any]:
    """The traded markets of the window by tier and category, the silent ones, and the traded
    ones the market list lacks."""
    first, last = days[0], days[-1]
    midnight = datetime.min.time()
    window_start_ms = int(datetime.combine(first, midnight, tzinfo=UTC).timestamp() * 1000)
    window_end_ms = int(
        datetime.combine(last + timedelta(days=1), midnight, tzinfo=UTC).timestamp() * 1000
    )
    window_days = len(days)  # contiguous: trade_days saw to that
    traded = (
        scan_trades(trade_files(kalshi, first, last))
        .group_by("ticker")
        .agg(
            pl.len().alias("trades"),
            pl.col("count_e2").sum().alias("contracts_e2"),
            (pl.col("yes_price_e6") * pl.col("count_e2") // 100).sum().alias("dollars_e6"),
        )
        .collect()
    )
    times_known = pl.col("open_ms").is_not_null() & pl.col("close_ms").is_not_null()
    in_window = (pl.col("open_ms") < window_end_ms) & (pl.col("close_ms") > window_start_ms)
    listed = (
        scan_markets(kalshi, ["ticker", "series_ticker", "open_time", "close_time"])
        .with_columns(
            open_ms=pl.col("open_time").dt.epoch("ms"), close_ms=pl.col("close_time").dt.epoch("ms")
        )
        .filter(in_window | ~times_known)
        .select("ticker", "series_ticker", "open_ms", "close_ms")
        .collect()
        .join(_series(kalshi).select("series_ticker", "category"), on="series_ticker", how="left")
    )
    joined = tiered(
        listed.join(traded, on="ticker", how="full", coalesce=True), window_start_ms, window_end_ms
    ).with_columns(pl.col("category").fill_null(UNKNOWN))
    active = joined.filter(pl.col("trades").is_not_null())
    silent = joined.filter(pl.col("trades").is_null() & times_known)
    aggs = [
        pl.len().alias("markets"),
        pl.col("trades").sum().alias("trades"),
        pl.col("contracts_e2").sum().alias("contracts_e2"),
        pl.col("dollars_e6").sum().alias("dollars_e6"),
    ]

    def cells(keys: list[str]) -> dict[str, dict[str, Any]]:
        out = {}
        for row in active.group_by(keys).agg(aggs).sort(keys).to_dicts():
            key = " | ".join(str(row[k]) for k in keys)
            out[key] = {
                "markets": row["markets"],
                "trades_a_day": row["trades"] / window_days,
                "contracts_a_day": row["contracts_e2"] / 100 / window_days,
                "dollars_e6_a_day": int(row["dollars_e6"] // window_days),
            }
        return out

    return {
        "first_day": first.isoformat(),
        "last_day": last.isoformat(),
        "days": window_days,
        "open_markets": joined.filter(times_known).height,
        "traded_markets": active.height,
        "unlisted_traded_markets": active.filter(pl.col("series_ticker").is_null()).height,
        "unknown_life_markets": active.filter(pl.col("tier") == UNKNOWN).height,
        "silent_markets": silent.height,
        "silent_by_category": {
            r["category"]: r["n"]
            for r in silent.group_by("category")
            .agg(pl.len().alias("n"))
            .sort("category")
            .to_dicts()
        },
        "by_tier": cells(["tier"]),
        "by_tier_and_category": cells(["tier", "category"]),
    }


# -- spreads and the half-spread ----------------------------------------------------------------


def _outage_overlap(start: pl.Expr, end: pl.Expr, gaps: Gaps) -> pl.Expr:
    """The time of ``[start, end)`` that falls inside a logged outage."""
    if not gaps:
        return pl.lit(0, dtype=pl.Int64)
    return pl.sum_horizontal(
        [
            (pl.min_horizontal(end, g_end) - pl.max_horizontal(start, g_start)).clip(lower_bound=0)
            for g_start, g_end in gaps
        ]
    )


def _hour_spreads(
    book: pl.DataFrame, hour_start_ms: int, hour_end_ms: int, closes: pl.DataFrame, gaps: Gaps
) -> pl.DataFrame:
    """Per market in one hour: the time two-sided, the time in all, and the spread integrated
    over the two-sided time; ``book`` holds the hour's frames with each market's carried frame
    first. A market's time ends at its close, and outage time counts for no one."""
    ordered = book.join(closes, on="ticker", how="left").sort("ticker", "ts_ms")
    next_ms = pl.col("ts_ms").shift(-1).over("ticker").fill_null(hour_end_ms)
    timed = ordered.with_columns(
        from_ms=pl.max_horizontal("ts_ms", hour_start_ms),
        to_ms=pl.min_horizontal(next_ms, pl.col("close_ms").fill_null(hour_end_ms)),
        spread_e6=pl.col("yes_ask_e6") - pl.col("yes_bid_e6"),
    ).with_columns(
        dur_ms=(
            (pl.col("to_ms") - pl.col("from_ms")).clip(lower_bound=0)
            - _outage_overlap(pl.col("from_ms"), pl.col("to_ms"), gaps)
        ).clip(lower_bound=0)
    )
    two_sided = pl.col("spread_e6").is_not_null()
    return timed.group_by("ticker").agg(
        pl.col("dur_ms").sum().alias("dur_ms"),
        pl.col("dur_ms").filter(two_sided).sum().alias("two_sided_ms"),
        (pl.col("dur_ms") * pl.col("spread_e6")).filter(two_sided).sum().alias("spread_dur"),
    )


def _hour_trades(
    trades: pl.DataFrame, book: pl.DataFrame, info: pl.DataFrame, gaps: Gaps
) -> pl.DataFrame:
    """Each trade of the hour with the quoted spread just before it, its half-spread dollars and
    its maker fee (unknown without the series' terms: a fee type in the quadratic family and a
    multiplier); a trade whose last frame lies before an outage reads an unknown book."""
    right = book.select(
        frame_ms=pl.col("ts_ms"),
        ticker=pl.col("ticker"),
        bid0=pl.col("yes_bid_e6"),
        ask0=pl.col("yes_ask_e6"),
    ).sort("frame_ms")
    joined = (
        trades.with_columns(k=pl.col("ts_ms") - 1)
        .sort("k")
        .join_asof(
            right,
            left_on="k",
            right_on="frame_ms",
            by="ticker",
            strategy="backward",
            check_sortedness=False,
        )  # fmt: skip
        .drop("k")
    )
    frame, trade = pl.col("frame_ms"), pl.col("ts_ms")
    through_outage = pl.any_horizontal(
        [pl.lit(False)] + [(frame < g_start) & (trade >= g_start) for g_start, _ in gaps]
    )
    spread = pl.col("ask0") - pl.col("bid0")
    state = (
        pl.when(frame.is_null() | through_outage)
        .then(pl.lit(UNKNOWN))
        .when(spread.is_null())
        .then(pl.lit("one_sided"))
        .when(spread <= COMPETING_E6)
        .then(pl.lit("contested"))
        .otherwise(pl.lit("uncontested"))
    )
    joined = joined.with_columns(spread_e6=spread, state=state).with_columns(
        half_e6=pl.when(pl.col("state").is_in(["contested", "uncontested"])).then(
            (pl.col("count_e2") * (pl.col("spread_e6") // 2)) // 100
        )
    )
    share = pl.col("fee_type").replace_strict(MAKER_SHARE_E2, default=None, return_dtype=pl.Int64)
    joined = joined.join(info, on="ticker", how="left").with_columns(share=share)
    readable = pl.col("half_e6").is_not_null()
    terms = pl.col("share").is_not_null() & pl.col("multiplier_e6").is_not_null()
    fee = maker_fee_expr(
        pl.col("multiplier_e6"), pl.col("share"), pl.col("count_e2"), pl.col("yes_price_e6")
    )
    return joined.with_columns(
        fee_e6=pl.when(readable & terms).then(fee),
        fee_unknown=readable & ~terms,
    ).select("ticker", "state", "count_e2", "half_e6", "fee_e6", "fee_unknown")


def live_reading(live: Path, fees: pl.DataFrame) -> dict[str, Any]:
    """The spreads by tier and category, and the half-spread dollars a day by tier, category and
    the competition at the trade."""
    start, end = span(live)
    hours = int((end - start) / timedelta(hours=1))  # at least one: span() saw to that
    markets = pl.read_parquet(
        live / "markets.parquet",
        columns=["ticker", "series_ticker", "category", "open_ms", "close_ms"],
    )
    contracts = (
        pl.scan_parquet(hour_files(live, "trade", start, end))
        .group_by("ticker")
        .agg(pl.col("count_e2").sum().alias("contracts_e2"))
        .collect()
    )
    span_ms = (int(start.timestamp() * 1000), int(end.timestamp() * 1000))
    info = (
        tiered(markets.join(contracts, on="ticker", how="left"), *span_ms)
        .with_columns(pl.col("category").fill_null(UNKNOWN))
        .select("ticker", "series_ticker", "category", "tier")
        .join(fees, on="series_ticker", how="left")
    )
    closes = markets.select("ticker", "close_ms")
    gaps: Gaps = [(r["start_ms"], r["end_ms"]) for r in gaps_of(live, end).to_dicts()]
    spread_sums: list[pl.DataFrame] = []
    trade_sums: list[pl.DataFrame] = []
    carried: pl.DataFrame | None = None
    for path in hour_files(live, "ticker", start, end):
        hour = hour_of(path.stem)
        hour_start_ms = int(hour.timestamp() * 1000)
        frames = frames_of(path).drop("carried", "mid_e6")
        book = pl.concat([carried, frames]) if carried is not None else frames
        spread_sums.append(
            _hour_spreads(book, hour_start_ms, hour_start_ms + MS_PER_HOUR, closes, gaps)
        )
        trades = trades_of(live, [hour], TRADE_COLUMNS)
        if trades.height:
            trade_sums.append(_hour_trades(trades, book.sort("ts_ms"), info, gaps))
        carried = (  # a market closed by the next hour has no time left and no trade to read
            book.sort("ticker", "ts_ms")
            .group_by("ticker", maintain_order=True)
            .last()
            .join(closes, on="ticker", how="left")
            .filter(
                pl.col("close_ms").is_null() | (pl.col("close_ms") > hour_start_ms + MS_PER_HOUR)
            )
            .select(frames.columns)
        )
    tiers = info.select("ticker", "tier", "category")
    spreads = (
        pl.concat(spread_sums)
        .group_by("ticker")
        .agg(pl.col("dur_ms").sum(), pl.col("two_sided_ms").sum(), pl.col("spread_dur").sum())
        .with_columns(
            mean_spread_e6=pl.when(pl.col("two_sided_ms") > 0).then(
                pl.col("spread_dur") / pl.col("two_sided_ms")
            ),
            two_sided_share=pl.when(pl.col("dur_ms") > 0).then(
                pl.col("two_sided_ms") / pl.col("dur_ms")
            ),
        )
        .join(tiers, on="ticker", how="left")
        .with_columns(pl.col("tier").fill_null(UNKNOWN), pl.col("category").fill_null(UNKNOWN))
    )  # a market with frames but no row in the market list has no tier and no category

    def spread_cells(keys: list[str]) -> dict[str, dict[str, Any]]:
        rows = (
            spreads.group_by(keys)
            .agg(
                pl.len().alias("markets"),
                pl.col("mean_spread_e6").median().alias("median_spread_e6"),
                pl.col("two_sided_share").mean().alias("two_sided_share"),
            )
            .sort(keys)
            .to_dicts()
        )
        return {
            " | ".join(str(r[k]) for k in keys): {
                "markets": r["markets"],
                "median_spread_cents": None
                if r["median_spread_e6"] is None
                else r["median_spread_e6"] / CENTS,
                "two_sided_share": r["two_sided_share"],
            }
            for r in rows
        }

    empty = {
        "ticker": pl.String,
        "state": pl.String,
        "count_e2": pl.Int64,
        "half_e6": pl.Int64,
        "fee_e6": pl.Int64,
        "fee_unknown": pl.Boolean,
    }
    through = pl.concat(trade_sums) if trade_sums else pl.DataFrame(schema=empty)
    per_market = (
        through.group_by("ticker", "state")
        .agg(
            pl.len().alias("trades"),
            pl.col("count_e2").sum().alias("contracts_e2"),
            pl.col("half_e6").sum().alias("half_e6"),
            pl.col("fee_e6").sum().alias("fee_e6"),
            pl.col("fee_unknown").sum().alias("fee_unknown"),
        )
        .join(tiers, on="ticker", how="left")
        .with_columns(pl.col("tier").fill_null(UNKNOWN), pl.col("category").fill_null(UNKNOWN))
    )

    def half_cells(frame: pl.DataFrame, keys: list[str]) -> dict[str, dict[str, Any]]:
        rows = (
            frame.group_by(keys)
            .agg(
                pl.col("ticker").n_unique().alias("markets"),
                pl.col("trades").sum().alias("trades"),
                pl.col("contracts_e2").sum().alias("contracts_e2"),
                pl.col("half_e6").sum().alias("half_e6"),
                pl.col("fee_e6").sum().alias("fee_e6"),
                pl.col("fee_unknown").sum().alias("fee_unknown"),
            )
            .sort(keys)
            .to_dicts()
        )
        out = {}
        for r in rows:
            gross, fee = a_day(r["half_e6"], hours), a_day(r["fee_e6"], hours)
            out[" | ".join(str(r[k]) for k in keys)] = {
                "markets": r["markets"],
                "trades_a_day": r["trades"] * 24 / hours,
                "contracts_a_day": r["contracts_e2"] / 100 * 24 / hours,
                "half_spread_e6_a_day": gross,
                "fee_e6_a_day": fee,
                "net_e6_a_day": gross - fee,
                "fee_unknown_trades": r["fee_unknown"],
            }
        return out

    thin = per_market.filter(pl.col("tier").is_in(THIN_TIERS))
    thin_by_state = half_cells(thin, ["state"])
    uncontested = thin_by_state.get("uncontested")
    thin_uncontested = 0 if uncontested is None else uncontested["half_spread_e6_a_day"]
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "hours": hours,
        "outages": len(gaps),
        "markets_with_frames": spreads.height,
        "spreads_by_tier": spread_cells(["tier"]),
        "spreads_by_tier_and_category": spread_cells(["tier", "category"]),
        "half_spread_by_state": half_cells(per_market, ["state"]),
        "half_spread_by_tier_and_state": half_cells(per_market, ["tier", "state"]),
        "half_spread_thin_by_state": thin_by_state,
        "half_spread_thin_by_category_and_state": half_cells(thin, ["category", "state"]),
        "thin_uncontested_half_spread_e6_a_day": thin_uncontested,
        "clears": thin_uncontested >= HALF_SPREAD_BAR_E6,
    }


# -- the surprise rate --------------------------------------------------------------------------


def day_before(kalshi: Path, cut: date) -> tuple[pl.DataFrame, dict[str, int]]:
    """Each cut market's last trade from 36 to 12 hours before its close (among trades in one
    millisecond, the highest price), with its side in the tail (90 cents or more) where there is
    one, and the counts of markets left out."""
    markets = cut_markets(kalshi, cut)
    files = trade_files(kalshi, HISTORY_START, cut)
    t = pl.col("t_ms")
    window_from = pl.col("close_ms") - DAY_BEFORE_FROM_MS
    window_to = pl.col("close_ms") - DAY_BEFORE_TO_MS
    priced = (
        scan_trades(files)
        .join(markets.select("ticker", "close_ms").lazy(), on="ticker")
        .filter((t >= window_from) & (t < window_to))
        .group_by("ticker")
        .agg(
            pl.col("yes_price_e6").sort_by(["t_ms", "yes_price_e6"]).last().alias("price_e6"),
            pl.col("t_ms").max().alias("priced_ms"),
        )
        .collect()
    )
    events = (
        scan_markets(kalshi, ["ticker", "event_ticker"], traded=priced.select("ticker").lazy())
        .select("ticker", "event_ticker")
        .collect()
    )
    price = pl.col("price_e6")
    frame = (
        markets.join(priced, on="ticker", how="inner")
        .join(events, on="ticker", how="left")
        .with_columns(
            side=pl.when(price >= TAIL_E6)
            .then(pl.lit("yes"))
            .when(price <= E6 - TAIL_E6)
            .then(pl.lit("no"))
        )
        .with_columns(
            p_e6=pl.when(pl.col("side") == "yes").then(price).otherwise(E6 - price),
        )
        .with_columns(
            surprise=(pl.col("result") != pl.col("side")).cast(pl.Int8),
            implied=(E6 - pl.col("p_e6")) / E6,
            bucket=bucket(pl.col("p_e6"), BUCKETS),
            event_ticker=pl.col("event_ticker").fill_null(pl.col("ticker")),
        )
    )
    in_tail = frame.filter(pl.col("side").is_not_null())
    counts = {
        "markets": markets.height,
        "priced": priced.height,
        "unpriced": markets.height - priced.height,
        "not_in_tail": frame.height - in_tail.height,
        "in_tail": in_tail.height,
    }
    return in_tail.select(
        "ticker", "series_ticker", "category", "event_ticker", "result", "side", "p_e6", "surprise",
        "implied", "bucket", "priced_ms", "close_ms",
    ), counts  # fmt: skip


def _rate(frame: pl.DataFrame) -> dict[str, Any]:
    """A cell's surprise rate with its interval over events, against the implied rate; judged
    only with enough events."""
    by_event = (
        frame.group_by("event_ticker")
        .agg(
            pl.col("surprise").sum().cast(pl.Float64).alias("num"),
            pl.len().cast(pl.Float64).alias("den"),
        )
        .sort("event_ticker")  # a group-by's order is not fixed; the draws must be
    )
    estimate: Estimate = cluster_bootstrap(
        by_event["num"].to_numpy(),
        by_event["den"].to_numpy(),
        tail=TAIL,
        resamples=RESAMPLES,
        seed=SEED,
    )
    implied = float(np.mean(frame["implied"].to_numpy()))
    judged = by_event.height >= MIN_EVENTS
    return {
        "markets": frame.height,
        "events": by_event.height,
        "surprises": int(frame["surprise"].sum()),
        "observed": estimate.point_e6,
        "lower": estimate.lower_e6,
        "upper": estimate.upper_e6,
        "implied": implied,
        "judged": judged,
        "clears": bool(judged and estimate.lower_e6 > implied),
    }


def surprise(
    kalshi: Path, cut: date = CUT, min_markets: int = MIN_SERIES_MARKETS
) -> dict[str, Any]:
    """The surprise reading: the rate by category, bucket and series, and the clearing series with
    their title and rules."""
    tail, counts = day_before(kalshi, cut)
    if tail.is_empty():
        raise SurveyDataError("no market stood at 90 cents or more the day before its close")

    def cells(key: str, minimum: int = 1) -> dict[str, dict[str, Any]]:
        out = {}
        for value, group in sorted(tail.group_by(key), key=lambda kv: str(kv[0][0])):
            if group.height >= minimum:
                out[str(value[0])] = _rate(group)
        return out

    by_series = cells("series_ticker", min_markets)
    series = _series(kalshi)
    titles = dict(zip(series["series_ticker"], series["title"], strict=True))
    clearing = sorted(k for k, v in by_series.items() if v["clears"])
    rules: dict[str, str | None] = {}
    if clearing:
        sample = (
            tail.filter(pl.col("series_ticker").is_in(clearing))
            .group_by("series_ticker")
            .agg(pl.col("ticker").first())
        )
        texts = (
            scan_markets(kalshi, ["ticker", "rules_primary"], traded=sample.select("ticker").lazy())
            .collect()
            .join(sample, on="ticker")
        )
        rules = dict(zip(texts["series_ticker"], texts["rules_primary"], strict=True))
    tested = sum(1 for v in by_series.values() if v["judged"])
    return {
        "cut": cut.isoformat(),
        **counts,
        "total": _rate(tail),
        "by_bucket": cells("bucket"),
        "by_category": cells("category"),
        "by_series": by_series,
        "series_tested": tested,
        "series_too_few_events": len(by_series) - tested,
        "expected_by_chance": tested * TAIL,
        "clearing_series": [
            {
                "series": k,
                "title": titles.get(k),
                "category": tail.filter(pl.col("series_ticker") == k)["category"][0],
                **by_series[k],
                "rules": rules.get(k),
            }
            for k in clearing
        ],
    }


def combine(
    volume_reading: dict[str, Any], live: dict[str, Any], surprise_reading: dict[str, Any]
) -> dict[str, Any]:
    """The three readings and M4's verdicts."""
    return {
        "volume": volume_reading,
        "live": live,
        "surprise": surprise_reading,
        "cleared": {
            "half_spread": live["clears"],
            "surprise_series": [s["series"] for s in surprise_reading["clearing_series"]],
        },
    }
