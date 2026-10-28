"""S2.2: how often trades crossed a bound after the report counted, and what taking them was worth.

Run on the fit or the validate part only (W6); the test part is refused, as it is opened once in
S2.3.

* **Timeline.** A report counts ``DELAY_MS`` after its observation time (W4). The climate day is
  local standard time, with the clock's day as a variant (``stations``). A bound counts only where
  both days allow it: the lower of the two running bounds, and rain once both have it, so a report
  near midnight never settles the wrong day.
* **The call.** A market is called at the first counted moment its bound reaches
  ``check.threshold``; a market never reached is not called.
* **Ours.** A trade after the call whose taker bought the side the call made certain is one we
  could have taken instead (a taker of the other side met a maker who already held the certain
  side). We pay its price one cent worse (W5) and the taker fee rounded up to a cent, each trade
  one order, and receive Kalshi's result (W2), so a wrong call costs its whole price.
* **Capacity.** The upper bound takes every such trade; the lower bound only the first in each
  market that was worth taking when it came (its price and fee below a dollar). W7's floor is
  judged on the lower.
* **Repricing (W8).** For each called market, how long after the call the first and the last
  profitable trade of ours came; how much traded on the ruled-out side after the call, and how
  much of it at 99 cents or more for the certain side; and how long *before* the call the certain
  side last traded below 95 cents (the book's lead on the reports).
* **The gap.** For each city-day of highs, the settled high (at least the winning bracket's lowest
  degree) against the highest hourly temperature reported that day, and how many hours after the
  hourly peak the bound (with the six-hour maximum) reached it (0 when it was no later).

The trade files read run from the day before the part to two days after it, since a market trades
from the day before its day to about 01:00 local after it. For the validate part that opens the
test part's first two days, of which only the validate markets' rows are kept.

Money stays in integer millionths of a dollar; only the summaries are shown in cents and dollars.
"""

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from sparq_core.fees import QUADRATIC_FAMILY
from sparq_core.fixedpoint import E6

from sparq_analytics.study.bootstrap import Estimate, cluster_bootstrap
from sparq_analytics.study.dataset import with_fees
from sparq_analytics.study.feecalc import check_bounds, taker_fee_expr
from sparq_analytics.weather.check import settled_low, strike_value, threshold
from sparq_analytics.weather.stations import (
    BY_STATION,
    FIT_END,
    HIGH_SERIES,
    RAIN_SERIES,
    SITES,
    TEST_START,
    WeatherDataError,
)

DELAY_MS = 10 * 60 * 1000  # W4, set 2026-10-28
WORSE_E6 = 10_000  # W5: one cent worse for us
FEE_UNIT_E6 = 10_000  # the taker fee rounded up to a cent per order
TAIL, RESAMPLES, SEED = 0.025, 2_000, 20261028
CHEAP_E6 = 950_000  # the lead: the certain side's last trade below 95 cents
NINETY_NINE_E6 = 990_000
PARTS = {
    "fit": (date(2026, 8, 7), FIT_END),
    "validate": (FIT_END + timedelta(days=1), TEST_START - timedelta(days=1)),
}
# one city, two stations (Chicago's rain is O'Hare, its high Midway; Houston's rain moved): one
# cluster, since the same weather drives both
CITY = {"CLIORD": "CLIMDW", "CLIIAH": "CLIHOU"}
KEYS = ["station", "day"]


def part_days(part: str) -> tuple[date, date]:
    """The first and last day of an exploration part; the test part is refused."""
    if part not in PARTS:
        raise WeatherDataError(f"S2.2 explores only {', '.join(PARTS)}; the test part is S2.3's")
    first, last = PARTS[part]
    if last >= TEST_START:  # pragma: no cover - a guard on the constants above
        raise WeatherDataError(f"{part} reaches the test part")
    return first, last


def timeline(bounds: pl.DataFrame, delay_ms: int = DELAY_MS) -> pl.DataFrame:
    """For each station and day, every moment a report counts, with what both kinds of day
    allow: ``bound_f`` (with the six-hour maximum), ``hourly_f`` (hourly temperatures only) and
    ``rain``."""
    per_mode = bounds.with_columns(
        counted_ms=pl.col("valid_ms") + delay_ms,
        hourly_f=pl.col("temp_f").cum_max().forward_fill().over("station", "mode", "day"),
    )
    times = per_mode.select(*KEYS, "counted_ms").unique().sort("counted_ms")
    line = times
    for mode, tag in (("standard", "s"), ("civil", "c")):
        side = (
            per_mode.filter(pl.col("mode") == mode)
            .group_by(*KEYS, "counted_ms")  # running values only grow, so the most is the latest
            .agg(
                pl.col("bound_f").max().alias(f"bound_{tag}"),
                pl.col("hourly_f").max().alias(f"hourly_{tag}"),
                pl.col("rain_seen").max().alias(f"rain_{tag}"),
            )
            .sort("counted_ms")
        )
        # both sides are sorted on the time just above
        line = line.join_asof(
            side, on="counted_ms", by=KEYS, strategy="backward", check_sortedness=False
        )

    def lower(name: str) -> pl.Expr:
        both = pl.col(f"{name}_s").is_not_null() & pl.col(f"{name}_c").is_not_null()
        return pl.when(both).then(pl.min_horizontal(f"{name}_s", f"{name}_c")).alias(f"{name}_f")

    rain = pl.col("rain_s").fill_null(False) & pl.col("rain_c").fill_null(False)
    return line.select(
        *KEYS, "counted_ms", lower("bound"), lower("hourly"), rain.alias("rain")
    ).sort(*KEYS, "counted_ms")


def calls(markets: pl.DataFrame, line: pl.DataFrame) -> pl.DataFrame:
    """The markets with a threshold, each with its ``call``, ``lowest`` bound and ``call_ms``, the
    first counted moment that settles it (null when none does)."""
    rows = []
    for row in markets.iter_rows(named=True):
        settles = threshold(
            row["series_ticker"],
            row["strike_type"],
            strike_value(row["floor_strike"]),
            strike_value(row["cap_strike"]),
        )
        if settles is not None:
            rows.append({"ticker": row["ticker"], "call": settles[0], "lowest": settles[1]})
    wanted = pl.DataFrame(rows, schema={"ticker": pl.String, "call": pl.String, "lowest": pl.Int32})
    found = markets.join(wanted, on="ticker")
    reached = (
        found.select("ticker", *KEYS, "lowest")
        .join(line, on=KEYS)
        .filter(
            pl.when(pl.col("lowest").is_null())
            .then(pl.col("rain"))
            .otherwise(pl.col("bound_f") >= pl.col("lowest"))
            .fill_null(False)
        )
        .group_by("ticker")
        .agg(pl.col("counted_ms").min().alias("call_ms"))
    )
    return found.join(reached, on="ticker", how="left")


def _local_hour(zones: list[str]) -> pl.Expr:
    hour = pl.lit(None, dtype=pl.Int8)
    for zone in zones:
        hour = (
            pl.when(pl.col("tz") == zone)
            .then(pl.col("t").dt.convert_time_zone(zone).dt.hour().cast(pl.Int8))
            .otherwise(hour)
        )
    return hour


def priced_trades(kalshi: Path, called: pl.DataFrame, first: date, last: date) -> pl.DataFrame:
    """Every trade of a called market, priced for us: ``after`` when at or after the call,
    ``ours`` when its taker bought the certain side, ``certain_e6`` the certain side's price,
    ``profit_e6`` what taking it would have made after the fee (null when the fee is unknown) and
    ``takeable`` when that would have been positive had the call been right."""
    paths = []
    day = first - timedelta(days=1)  # a market opens the day before its day
    while day <= last + timedelta(days=2):  # and closes about 01:00 local after it
        path = kalshi / "trades" / f"date={day}" / "trades.parquet"
        if path.exists():
            paths.append(path)
        day += timedelta(days=1)
    if not paths:
        raise WeatherDataError(f"no trades from {first} to {last} under {kalshi / 'trades'}")
    markets = called.filter(pl.col("call_ms").is_not_null()).with_columns(
        tz=pl.col("station").replace_strict(
            {station: SITES[code].tz for station, code in BY_STATION.items()}, default=None
        ),
        place=pl.col("code").replace(CITY),
    )
    tickers = markets["ticker"].to_list()
    trades = (
        pl.scan_parquet(paths)
        .filter(pl.col("ticker").is_in(tickers))
        .select(
            pl.col("ticker"),
            pl.col("created_time").alias("t"),
            pl.col("yes_price_e6").cast(pl.Int64).alias("p"),
            pl.col("count_e2").alias("c"),
            pl.col("taker_side"),
        )
    )
    joined = trades.join(
        markets.select(
            "ticker", "series_ticker", "place", "day", "tz", "result", "call", "call_ms"
        ).lazy(),
        on="ticker",
    )
    frame = with_fees(joined, kalshi).with_columns(t_ms=pl.col("t").dt.epoch("ms")).collect()
    check_bounds(frame, multiplier="multiplier_e6", count="c", price="p")
    certain = pl.when(pl.col("call") == "yes").then(pl.col("p")).otherwise(E6 - pl.col("p"))
    price = pl.min_horizontal(certain + WORSE_E6, pl.lit(E6, dtype=pl.Int64))
    payoff = pl.when(pl.col("result") == pl.col("call")).then(E6).otherwise(0)
    fee = taker_fee_expr(pl.col("multiplier_e6"), pl.col("c"), pl.col("price_e6"))
    known = (
        pl.col("fee_type").is_in(sorted(QUADRATIC_FAMILY)) & pl.col("multiplier_e6").is_not_null()
    )
    return (
        frame.with_columns(
            after=pl.col("t_ms") >= pl.col("call_ms"),
            ours=pl.col("taker_side") == pl.col("call"),
            certain_e6=certain,
            price_e6=price,
            hour=_local_hour(sorted(set(markets["tz"].drop_nulls()))),
            after_s=(pl.col("t_ms") - pl.col("call_ms")) / 1000,
        )
        .with_columns(
            fee_e6=pl.when(known).then((fee + FEE_UNIT_E6 - 1) // FEE_UNIT_E6 * FEE_UNIT_E6)
        )
        .with_columns(
            profit_e6=(payoff - pl.col("price_e6")) * pl.col("c") // 100 - pl.col("fee_e6"),
            takeable=(E6 - pl.col("price_e6")) * pl.col("c") // 100 - pl.col("fee_e6") > 0,
        )
        .sort("t_ms", "ticker")
    )


def _number(value: float | None) -> float | None:
    return None if value is None or math.isnan(value) else round(value, 4)


def _estimate(ours: pl.DataFrame, days: int) -> dict[str, Any]:
    """The total and per-day profit, and the profit per contract with its interval, clustered by
    city-day."""
    clusters = ours.group_by("place", "day").agg(
        pl.col("profit_e6").sum().alias("profit_e6"), (pl.col("c").sum() / 100).alias("contracts")
    )
    per_contract: Estimate = cluster_bootstrap(
        clusters["profit_e6"].cast(pl.Float64).to_numpy(),
        clusters["contracts"].cast(pl.Float64).to_numpy(),
        tail=TAIL,
        resamples=RESAMPLES,
        seed=SEED,
    )
    per_city_day = cluster_bootstrap(
        clusters["profit_e6"].cast(pl.Float64).to_numpy(),
        np.ones(clusters.height),
        tail=TAIL,
        resamples=RESAMPLES,
        seed=SEED,
    )
    total = int(ours["profit_e6"].sum())
    return {
        "trades": ours.height,
        "markets": ours["ticker"].n_unique(),
        "city_days": clusters.height,
        "contracts": round(float(ours["c"].sum()) / 100, 2),
        "profit_dollars": round(total / E6, 2),
        "fee_dollars": round(int(ours["fee_e6"].sum()) / E6, 2),
        "dollars_a_day": round(total / E6 / days, 2),
        "dollars_a_day_lower": _number(per_city_day.lower_e6 * clusters.height / E6 / days),
        "cents_per_contract": _number(per_contract.point_e6 / 10_000),
        "cents_per_contract_lower": _number(per_contract.lower_e6 / 10_000),
    }


def _quantiles(values: pl.Series) -> dict[str, float | None]:
    if values.is_empty():
        return {"median": None, "p90": None, "max": None}
    return {
        "median": _number(values.quantile(0.5)),
        "p90": _number(values.quantile(0.9)),
        "max": _number(values.quantile(1.0)),
    }


def lead(trades: pl.DataFrame) -> dict[str, Any]:
    """By kind of market: how long before the call the certain side last traded below 95 cents,
    over the markets where that last trade came before the call."""
    kind = (
        pl.when(pl.col("series_ticker") == RAIN_SERIES)
        .then(pl.lit("rain"))
        .otherwise(pl.lit("high"))
    )
    last = (
        trades.filter(pl.col("certain_e6") < CHEAP_E6)
        .group_by("ticker")
        .agg(pl.col("series_ticker").first(), pl.col("call_ms").first(), pl.col("t_ms").max())
        .with_columns(kind=kind, lead_min=(pl.col("call_ms") - pl.col("t_ms")) / 60_000)
    )
    out: dict[str, Any] = {}
    for name in ("high", "rain"):
        mine = last.filter(pl.col("kind") == name)
        before = mine.filter(pl.col("lead_min") > 0)["lead_min"]
        out[name] = {
            "markets_below_95": mine.height,
            "last_below_95_before_call": before.len(),
            "median_min": _number(before.quantile(0.5)),
            "p10_min": _number(before.quantile(0.1)),
        }
    return out


def repricing(called: pl.DataFrame, after: pl.DataFrame) -> dict[str, Any]:
    """W8: how fast ours ran out, what traded on the ruled-out side (every trade, whatever its
    fee), and the calls the result contradicted."""
    ours = after.filter(pl.col("ours") & pl.col("profit_e6").is_not_null())
    per_market = ours.group_by("ticker").agg(
        pl.col("after_s").min().alias("first_s"),
        pl.col("after_s").filter(pl.col("profit_e6") > 0).max().alias("last_profitable_s"),
    )
    ruled_out = after.filter(pl.col("taker_side").is_not_null() & ~pl.col("ours"))
    at_99 = ruled_out.filter(pl.col("certain_e6") >= NINETY_NINE_E6)
    made = called.filter(pl.col("call_ms").is_not_null())
    wrong = made.filter(pl.col("result") != pl.col("call"))
    return {
        "called_markets": made.height,
        "markets_with_ours": per_market.height,
        "first_ours_s": _quantiles(per_market["first_s"]),
        "last_profitable_s": _quantiles(per_market["last_profitable_s"].drop_nulls()),
        "ruled_out_trades": ruled_out.height,
        "ruled_out_contracts": round(float(ruled_out["c"].sum()) / 100, 2),
        "ruled_out_at_99_trades": at_99.height,
        "ruled_out_at_99_contracts": round(float(at_99["c"].sum()) / 100, 2),
        "side_unknown_trades": after.filter(pl.col("taker_side").is_null()).height,
        "wrong_calls": wrong.select("ticker", "call", "result").to_dicts(),
    }


def breakdown(ours: pl.DataFrame) -> pl.DataFrame:
    """Ours by series, the side made certain and the local hour."""
    return (
        ours.group_by("series_ticker", "call", "hour")
        .agg(
            pl.len().alias("trades"),
            (pl.col("c").sum() / 100).alias("contracts"),
            (pl.col("profit_e6").sum() / E6).round(2).alias("profit_dollars"),
            (pl.col("fee_e6").sum() / E6).round(2).alias("fee_dollars"),
            (pl.col("profit_e6") > 0).sum().alias("profitable"),
        )
        .sort("series_ticker", "call", "hour")
    )


def gap(markets: pl.DataFrame, line: pl.DataFrame) -> tuple[dict[str, Any], pl.DataFrame]:
    """How often and by how much the settled high is above the day's highest hourly report, and
    how many hours after the hourly peak the bound reached it."""
    winners = [
        {
            "event_ticker": row["event_ticker"],
            "settled_lo": settled_low(row["strike_type"], strike_value(row["floor_strike"])),
        }
        for row in markets.filter(
            pl.col("series_ticker").is_in(sorted(HIGH_SERIES)) & (pl.col("result") == "yes")
        ).iter_rows(named=True)
    ]
    events = (
        markets.filter(pl.col("series_ticker").is_in(sorted(HIGH_SERIES)))
        .select("event_ticker", *KEYS)
        .unique()
        .join(
            pl.DataFrame(winners, schema={"event_ticker": pl.String, "settled_lo": pl.Int32})
            .group_by("event_ticker")
            .agg(pl.col("settled_lo").max()),
            on="event_ticker",
            how="left",
        )
    )
    days = (
        line.group_by(*KEYS)
        .agg(pl.col("hourly_f").max().alias("hourly_f"), pl.col("bound_f").max().alias("bound_f"))
        .join(line.select(*KEYS, "counted_ms", pl.col("hourly_f").alias("h")), on=KEYS)
        .filter(pl.col("h") == pl.col("hourly_f"))
        .group_by(*KEYS, "hourly_f", "bound_f")
        .agg(pl.col("counted_ms").min().alias("peak_ms"))
    )
    joined = events.join(days, on=KEYS, how="left")
    reach = (
        joined.select("event_ticker", *KEYS, "settled_lo")
        .join(line.select(*KEYS, "counted_ms", "bound_f"), on=KEYS)
        .filter(pl.col("bound_f") >= pl.col("settled_lo"))
        .group_by("event_ticker")
        .agg(pl.col("counted_ms").min().alias("reach_ms"))
    )
    detail = (
        joined.join(reach, on="event_ticker", how="left")
        .with_columns(
            gap_f=pl.col("settled_lo") - pl.col("hourly_f"),
            # the six-hour maximum can show it before the hourly peak counts: no lag then
            hours_later=(pl.col("reach_ms") - pl.col("peak_ms")).clip(lower_bound=0) / 3_600_000,
        )
        .sort(*KEYS)
    )
    known = detail.filter(pl.col("gap_f").is_not_null())
    above = known.filter(pl.col("gap_f") > 0)
    reached = above.filter(pl.col("hours_later").is_not_null())
    sizes = above.group_by("gap_f").agg(pl.len().alias("city_days")).sort("gap_f")
    return {
        "city_days": detail.height,
        "known": known.height,
        "above_hourly": above.height,
        "by_degrees": {str(row["gap_f"]): row["city_days"] for row in sizes.iter_rows(named=True)},
        "reached_later": reached.height,
        "reached_by_the_peak": reached.filter(pl.col("hours_later") == 0).height,
        "hours_later": _quantiles(reached["hours_later"]),
        "never_reached": above.height - reached.height,
    }, detail


def explore(
    kalshi: Path, markets: pl.DataFrame, bounds: pl.DataFrame, first: date, last: date
) -> tuple[dict[str, Any], pl.DataFrame]:
    """S2.2's measures for the markets with a day from ``first`` to ``last``, and the trades
    after each call (for designing the rules)."""
    if markets.is_empty():
        raise WeatherDataError("no settled market in scope in that window")
    line = timeline(bounds)
    called = calls(markets, line)
    trades = priced_trades(kalshi, called, first, last)
    after = trades.filter(pl.col("after"))
    unknown_fee = after.filter(pl.col("fee_e6").is_null()).height
    ours = after.filter(pl.col("ours") & pl.col("fee_e6").is_not_null())
    # sorted by time: the first worth taking in each market
    lower = ours.filter(pl.col("takeable")).group_by("ticker", maintain_order=True).first()
    days = (last - first).days + 1
    gap_summary, _ = gap(markets, line)
    summary = {
        "first": str(first),
        "last": str(last),
        "days": days,
        "delay_minutes": DELAY_MS // 60_000,
        "markets": markets.height,
        "with_threshold": called.height,
        "trades_after_call": after.height,
        "trades_fee_unknown": unknown_fee,
        "upper": _estimate(ours, days),
        "lower": _estimate(lower, days),
        "repricing": repricing(called, after),
        "lead": lead(trades),
        "by_hour": breakdown(ours.with_columns(series_ticker=pl.lit("all"), call=pl.lit("all")))
        .drop("series_ticker", "call")
        .to_dicts(),
        "by_series": breakdown(ours).to_dicts(),
        "gap": gap_summary,
    }
    return summary, after
