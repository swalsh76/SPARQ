"""S3.3: the thin tail and the surprise rate (PLAN §6.3, M2 to M4).

Three readings, none building anything:

* **Volume**, from the history's trade files (any day, M3: no outcome): each traded market's
  contracts and dollars a day over the days it was open inside the window, placed in the activity
  tiers S3.2 used; by tier and category the markets, trades, contracts and dollars a day; and the
  markets open in the window that never traded (the silent tail).
* **Spreads and the half-spread**, from the live frames and trades: each market's time-weighted
  mean quoted spread while two-sided and the share of time a side was missing, by tier (the live
  span's own contracts a day) and category; and, for every live trade, half the quoted spread just
  before it times its contracts, which is what a maker at the touch earns against the mid, summed
  by tier, category and whether a competing quote stood within 10 cents of the mid at that moment
  (a spread of 20 cents or less: **contested**), none did (**uncontested**) or a side was missing
  (**one-sided**, no half-spread to read); gross and net of the maker fee where the series charges
  one. M4's bar is $250 a day of half-spread in thin markets' uncontested trades, a tenth captured
  being the $25 floor.
* **The surprise rate**, from the history to the cut (M3): per settled binary market the last trade
  from 36 to 12 hours before its close; where a side then stood at 90 cents or more, whether the
  market settled against it. The rate by category, by price bucket and by series with enough
  markets, each with a cluster-bootstrap interval over events, against the price-implied rate (the
  mean of one minus the price); the series whose interval lies wholly above the implied rate are
  listed with their title and rules text, beside the number that would clear by chance alone.

Money is integer millionths of a dollar; sizes are hundredths of a contract; a trade's dollars are
its yes price times its contracts, as in S3.1.
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
    CUT,
    HISTORY_START,
    MS_PER_HOUR,
    SurveyDataError,
    _scan,
    cut_markets,
    trade_files,
)
from sparq_analytics.survey.lag import TIERS, _bucket, frames_of, tiers_of
from sparq_analytics.survey.queue import span

MS_PER_DAY = 24 * MS_PER_HOUR
THIN_TIERS = (TIERS[0][1], TIERS[1][1])  # under 1,000 contracts a day
COMPETING_E6 = 200_000  # a spread of 20 cents: a quote within 10 cents of the mid on each side
TAIL_E6 = 900_000  # a side at 90 cents or more the day before close
DAY_BEFORE_FROM_MS, DAY_BEFORE_TO_MS = 36 * MS_PER_HOUR, 12 * MS_PER_HOUR
MIN_SERIES_MARKETS = 50  # M4: a series needs this many settled markets in the tail
BUCKETS: tuple[tuple[int | None, str], ...] = (
    (950_000, "90 to 95"),
    (990_000, "95 to 99"),
    (None, "99 and more"),
)
HALF_SPREAD_BAR_E6 = 250 * E6
RESAMPLES, TAIL, SEED = 2_000, 0.025, 33
CENTS_E6 = 10_000


def _a_day(value: int | float, days: float) -> float:
    return value / days


# -- volume -------------------------------------------------------------------------------------


def trade_days(kalshi: Path) -> list[date]:
    """The days with a trade file, from the folder names."""
    folder = kalshi / "trades"
    found = [p for p in folder.glob("date=*") if (p / "trades.parquet").exists()]
    days = sorted(date.fromisoformat(p.name[5:]) for p in found)
    if not days:
        raise SurveyDataError(f"no trade files under {folder}")
    return days


def _series(kalshi: Path) -> pl.DataFrame:
    columns = ["ticker", "category", "title"]
    series = pl.read_parquet(kalshi / "series.parquet", columns=columns)
    return series.rename({"ticker": "series_ticker"})


def volume(kalshi: Path, days: list[date]) -> dict[str, Any]:
    """The traded markets of the window by tier and category, and the silent ones."""
    first, last = days[0], days[-1]
    window_start_ms = int(
        datetime.combine(first, datetime.min.time(), tzinfo=UTC).timestamp() * 1000
    )
    window_end_ms = int(
        datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=UTC).timestamp()
        * 1000
    )  # fmt: skip
    window_days = (window_end_ms - window_start_ms) / MS_PER_DAY
    traded = (
        _scan(trade_files(kalshi, first, last))
        .group_by("ticker")
        .agg(
            pl.len().alias("trades"),
            pl.col("count_e2").sum().alias("contracts_e2"),
            (pl.col("yes_price_e6") * pl.col("count_e2") // 100).sum().alias("dollars_e6"),
        )
        .collect()
    )
    markets = (
        scan_markets(kalshi, ["ticker", "series_ticker", "open_time", "close_time"])
        .with_columns(
            open_ms=pl.col("open_time").dt.epoch("ms"), close_ms=pl.col("close_time").dt.epoch("ms")
        )
        .filter((pl.col("open_ms") < window_end_ms) & (pl.col("close_ms") > window_start_ms))
        .select("ticker", "series_ticker", "open_ms", "close_ms")
        .collect()
        .join(_series(kalshi).select("series_ticker", "category"), on="series_ticker", how="left")
        .with_columns(pl.col("category").fill_null("unknown"))
    )
    life_ms = pl.min_horizontal("close_ms", window_end_ms) - pl.max_horizontal(
        "open_ms", window_start_ms
    )
    life_days = (life_ms / MS_PER_DAY).ceil().clip(lower_bound=1)
    joined = markets.join(traded, on="ticker", how="left").with_columns(life_days=life_days)
    active = joined.filter(pl.col("trades").is_not_null()).with_columns(
        contracts_a_day=pl.col("contracts_e2") / 100 / pl.col("life_days")
    )
    active = active.with_columns(tier=_bucket(pl.col("contracts_a_day"), TIERS))
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
                "trades_a_day": _a_day(row["trades"], window_days),
                "contracts_a_day": _a_day(row["contracts_e2"] / 100, window_days),
                "dollars_e6_a_day": int(_a_day(row["dollars_e6"], window_days)),
            }
        return out

    silent = joined.filter(pl.col("trades").is_null())
    return {
        "first_day": first.isoformat(),
        "last_day": last.isoformat(),
        "days": window_days,
        "trade_files": len(days),
        "open_markets": joined.height,
        "traded_markets": active.height,
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


def _hour_spreads(book: pl.DataFrame, hour_start_ms: int, hour_end_ms: int) -> pl.DataFrame:
    """Per market in one hour: the time two-sided, the time a side was missing, and the spread
    integrated over the two-sided time; ``book`` holds the hour's frames with each market's
    carried frame first."""
    ordered = book.sort("ticker", "ts_ms")
    spread = pl.col("yes_ask_e6") - pl.col("yes_bid_e6")
    timed = ordered.with_columns(
        from_ms=pl.max_horizontal("ts_ms", hour_start_ms),
        to_ms=pl.col("ts_ms").shift(-1).over("ticker").fill_null(hour_end_ms),
        spread_e6=spread,
    ).with_columns(dur_ms=(pl.col("to_ms") - pl.col("from_ms")).clip(lower_bound=0))
    two_sided = pl.col("spread_e6").is_not_null()
    return timed.group_by("ticker").agg(
        pl.col("dur_ms").sum().alias("dur_ms"),
        pl.col("dur_ms").filter(two_sided).sum().alias("two_sided_ms"),
        (pl.col("dur_ms") * pl.col("spread_e6")).filter(two_sided).sum().alias("spread_dur"),
    )


def _trades(live: Path, hour: datetime) -> pl.DataFrame:
    """One hour's trades with their price, block trades left out (they are negotiated, not taken
    at the quote); an empty frame when the hour has no file."""
    path = live / "trade" / f"{hour.strftime('%Y-%m-%dT%H')}.parquet"
    columns = {
        "ts_ms": pl.Int64,
        "ticker": pl.String,
        "yes_price_e6": pl.Int64,
        "count_e2": pl.Int64,
    }
    if not path.exists():
        return pl.DataFrame(schema=columns)
    return (
        pl.read_parquet(path, columns=[*columns, "is_block_trade"])
        .filter(pl.col("is_block_trade") == 0)
        .drop("is_block_trade")
        .with_columns(pl.col("yes_price_e6").cast(pl.Int64))
    )


def _hour_trades(trades: pl.DataFrame, book: pl.DataFrame, info: pl.DataFrame) -> pl.DataFrame:
    """Each trade of the hour with the quoted spread just before it, its half-spread dollars and
    its maker fee."""
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
        .drop("k", "frame_ms")
    )
    spread = pl.col("ask0") - pl.col("bid0")
    joined = joined.with_columns(spread_e6=spread).with_columns(
        state=pl.when(pl.col("spread_e6").is_null())
        .then(pl.lit("one_sided"))
        .when(pl.col("spread_e6") <= COMPETING_E6)
        .then(pl.lit("contested"))
        .otherwise(pl.lit("uncontested")),
        half_e6=(pl.col("count_e2") * (pl.col("spread_e6") // 2)) // 100,
    )
    share = pl.col("fee_type").replace_strict(MAKER_SHARE_E2, default=None, return_dtype=pl.Int64)
    joined = joined.join(info, on="ticker", how="left").with_columns(share=share)
    fee = maker_fee_expr(
        pl.col("multiplier_e6"), pl.col("share"), pl.col("count_e2"), pl.col("yes_price_e6")
    )
    return joined.with_columns(fee_e6=pl.when(pl.col("share").is_not_null()).then(fee)).select(
        "ticker", "state", "count_e2", "half_e6", "fee_e6"
    )


def live_reading(live: Path, fees: pl.DataFrame) -> dict[str, Any]:
    """The spreads by tier and category, and the half-spread dollars a day by tier, category and
    the competition at the trade."""
    start, end = span(live)
    hours = int((end - start) / timedelta(hours=1))  # at least one: span() saw to that
    days = hours / 24
    markets = pl.read_parquet(
        live / "markets.parquet", columns=["ticker", "series_ticker", "category"]
    )
    info = (
        markets.join(tiers_of(live, start, end), on="ticker", how="left")
        .with_columns(
            pl.col("tier").fill_null(TIERS[0][1]), pl.col("category").fill_null("unknown")
        )
        .join(fees, on="series_ticker", how="left")
    )
    spread_sums: list[pl.DataFrame] = []
    trade_sums: list[pl.DataFrame] = []
    carried: pl.DataFrame | None = None
    for path in hour_files(live, "ticker", start, end):
        hour = hour_of(path.stem)
        hour_start_ms = int(hour.timestamp() * 1000)
        frames = frames_of(path).drop("carried", "mid_e6")
        book = pl.concat([carried, frames]) if carried is not None else frames
        spread_sums.append(_hour_spreads(book, hour_start_ms, hour_start_ms + MS_PER_HOUR))
        trades = _trades(live, hour)
        if trades.height:
            trade_sums.append(_hour_trades(trades, book, info))
        carried = (
            book.sort("ticker", "ts_ms")
            .group_by("ticker", maintain_order=True)
            .last()
            .select(frames.columns)
        )
    spreads = (
        pl.concat(spread_sums)
        .group_by("ticker")
        .agg(pl.col("dur_ms").sum(), pl.col("two_sided_ms").sum(), pl.col("spread_dur").sum())
        .with_columns(
            mean_spread_e6=pl.when(pl.col("two_sided_ms") > 0).then(
                pl.col("spread_dur") / pl.col("two_sided_ms")
            ),
            two_sided_share=pl.col("two_sided_ms") / pl.col("dur_ms"),
        )
        .join(info.select("ticker", "tier", "category"), on="ticker", how="left")
    )

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
                else r["median_spread_e6"] / CENTS_E6,
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
    }
    through = pl.concat(trade_sums) if trade_sums else pl.DataFrame(schema=empty)
    per_market = (
        through.group_by("ticker", "state")
        .agg(
            pl.len().alias("trades"),
            pl.col("count_e2").sum().alias("contracts_e2"),
            pl.col("half_e6").sum().alias("half_e6"),
            pl.col("fee_e6").sum().alias("fee_e6"),
            pl.col("fee_e6").is_null().sum().alias("fee_unknown"),
        )
        .join(info.select("ticker", "tier", "category"), on="ticker", how="left")
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
            gross = int(_a_day(r["half_e6"], days))
            fee = int(_a_day(r["fee_e6"], days))
            out[" | ".join(str(r[k]) for k in keys)] = {
                "markets": r["markets"],
                "trades_a_day": _a_day(r["trades"], days),
                "contracts_a_day": _a_day(r["contracts_e2"] / 100, days),
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
    """Each cut market's last trade from 36 to 12 hours before its close, with its side in the
    tail (90 cents or more) where there is one, and the counts of markets left out."""
    markets = cut_markets(kalshi, cut)
    files = trade_files(kalshi, HISTORY_START, cut)
    t = pl.col("t_ms")
    window_from = pl.col("close_ms") - DAY_BEFORE_FROM_MS
    window_to = pl.col("close_ms") - DAY_BEFORE_TO_MS
    priced = (
        _scan(files)
        .join(markets.select("ticker", "close_ms").lazy(), on="ticker")
        .filter((t >= window_from) & (t < window_to))
        .group_by("ticker")
        .agg(
            pl.col("yes_price_e6").sort_by("t_ms").last().alias("price_e6"),
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
            bucket=_bucket(pl.col("p_e6"), BUCKETS),
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
    """A cell's surprise rate with its interval over events, against the implied rate."""
    by_event = frame.group_by("event_ticker").agg(
        pl.col("surprise").sum().cast(pl.Float64).alias("num"),
        pl.len().cast(pl.Float64).alias("den"),
    )
    estimate: Estimate = cluster_bootstrap(
        by_event["num"].to_numpy(),
        by_event["den"].to_numpy(),
        tail=TAIL,
        resamples=RESAMPLES,
        seed=SEED,
    )
    implied = float(np.mean(frame["implied"].to_numpy()))
    return {
        "markets": frame.height,
        "events": by_event.height,
        "surprises": int(frame["surprise"].sum()),
        "observed": estimate.point_e6,
        "lower": estimate.lower_e6,
        "upper": estimate.upper_e6,
        "implied": implied,
        "clears": bool(estimate.lower_e6 > implied),
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
    return {
        "cut": cut.isoformat(),
        **counts,
        "total": _rate(tail),
        "by_bucket": cells("bucket"),
        "by_category": cells("category"),
        "by_series": by_series,
        "series_tested": len(by_series),
        "expected_by_chance": len(by_series) * TAIL,
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
    }  # fmt: skip
