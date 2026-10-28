"""Do the stations' reports agree with Kalshi's results? (S2.1's gate, on the fit part only.)

For each settled market in scope, its climate site comes from its rules (``stations.code_in``, or
the one code its series and city use), its day from its event ticker, and what the whole day of
reports settled from ``bounds.daily_bounds``. A market is **called** when the day's bound settles
it under W4's margins, and **wrong** when the call differs from Kalshi's result: S2.1's gate wants
none on the fit part. The same is counted without the 1 °F margin, for information only (W4 lets
the margin be widened after S2.2, never narrowed).

A market's strike is a number of degrees (or 0 inches for rain); its climate value is whole °F:

* ``greater`` X is YES when the value is above X, so a bound at or above ``floor(X) + 1`` settles
  it YES;
* ``less`` X is YES when the value is below X, so a bound at or above ``ceil(X)`` settles it NO;
* ``between`` A and B is YES from A to B, so a bound at or above ``floor(B) + 1`` settles it NO.

The margin adds to each threshold. Rain is YES once any report counted rain on the day.
"""

import math
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from sparq_analytics.study.dataset import scan_markets
from sparq_analytics.weather.stations import (
    DAY_MODES,
    RAIN_SERIES,
    SCOPE,
    SITES,
    TEST_START,
    WeatherDataError,
    city_of,
    code_in,
    market_day,
)

MARGIN_F = 1  # W4
MARKET_COLUMNS = [
    "ticker",
    "series_ticker",
    "event_ticker",
    "result",
    "strike_type",
    "floor_strike",
    "cap_strike",
    "rules_primary",
]


def threshold(
    series: str,
    strike_type: str | None,
    floor: float | None,
    cap: float | None,
    margin_f: int = MARGIN_F,
) -> tuple[str, int | None] | None:
    """What a market's bound can settle: the result ("yes" or "no") and the lowest bound in whole
    °F that settles it (None for rain, settled by any rain), or None when nothing can."""
    if series == RAIN_SERIES:
        # rain settles only "more than 0 inches", the one strike the history holds; any other
        # threshold is left uncalled rather than settled by 0.01 inch
        return ("yes", None) if strike_type == "greater" and floor == 0 else None
    if strike_type == "greater" and floor is not None:
        return "yes", math.floor(floor) + 1 + margin_f
    if strike_type == "less" and cap is not None:
        return "no", math.ceil(cap) + margin_f
    if strike_type == "between" and cap is not None:
        return "no", math.floor(cap) + 1 + margin_f
    return None


def settled_low(strike_type: str | None, floor: float | None) -> int | None:
    """The lowest whole degree F a winning market allows the day's high to be (the counterpart of
    :func:`threshold`): ``between`` A and B from A, ``greater`` X from ``floor(X) + 1``; ``less``
    has no lower edge."""
    if floor is None:
        return None
    if strike_type == "between":
        return math.ceil(floor)
    if strike_type == "greater":
        return math.floor(floor) + 1
    return None


def implied(
    series: str,
    strike_type: str | None,
    floor: float | None,
    cap: float | None,
    bound_f: int | None,
    rain: bool | None,
    margin_f: int = MARGIN_F,
) -> str | None:
    """The result a day's bound settles ("yes" or "no"), or None while it settles nothing."""
    settles = threshold(series, strike_type, floor, cap, margin_f)
    if settles is None:
        return None
    call, lowest = settles
    if lowest is None:
        return call if rain else None
    return call if bound_f is not None and bound_f >= lowest else None


def strike_value(value: Any) -> float | None:
    return None if value is None or value == "" else float(value)


def scope_markets(kalshi_root: Path, first: date, last: date) -> pl.DataFrame:
    """The settled markets in scope with a day from ``first`` to ``last`` (inclusive), each with
    its climate code and station; refuses any day in the test part."""
    if last >= TEST_START:
        raise WeatherDataError(
            f"{last} is in the test part (from {TEST_START}); S2.1 may not read it"
        )
    markets = (
        scan_markets(kalshi_root, MARKET_COLUMNS)
        .filter(pl.col("series_ticker").is_in(sorted(SCOPE)))  # W1's scope
        .filter(pl.col("result").is_in(["yes", "no"]))
        .collect()
    )
    rows = []
    for row in markets.iter_rows(named=True):
        day = market_day(row["event_ticker"])
        if not first <= day <= last:
            continue
        rows.append(
            row
            | {
                "day": day,
                "city": city_of(row["series_ticker"], row["ticker"]),
                "code": code_in(row["rules_primary"]),
            }
        )
    schema = dict(markets.schema) | {"day": pl.Date, "city": pl.String, "code": pl.String}
    frame = pl.DataFrame(rows, schema=schema)
    # markets whose rules name no site take the one code their series and city use
    single = (
        frame.filter(pl.col("code").is_not_null())
        .group_by("series_ticker", "city")
        .agg(pl.col("code").unique())
        .filter(pl.col("code").list.len() == 1)
        .with_columns(pl.col("code").list.first().alias("only_code"))
        .drop("code")
    )
    frame = frame.join(single, on=["series_ticker", "city"], how="left").with_columns(
        code=pl.coalesce("code", "only_code")
    )
    stations = pl.DataFrame(
        {"code": list(SITES), "station": [site.station for site in SITES.values()]}
    )
    return frame.drop("only_code", "rules_primary").join(stations, on="code", how="left")


def check(markets: pl.DataFrame, daily: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Per kind of day, series and city: markets, those with data, called, wrong; and the wrong
    ones themselves."""
    rows = []
    for mode in DAY_MODES:
        days = daily.filter(pl.col("mode") == mode).drop("mode")
        joined = markets.join(days, on=["station", "day"], how="left")
        for row in joined.iter_rows(named=True):
            floor, cap = strike_value(row["floor_strike"]), strike_value(row["cap_strike"])
            has_data = row["reports"] is not None
            call = implied(
                row["series_ticker"],
                row["strike_type"],
                floor,
                cap,
                row["bound_f"],
                row["rain_seen"],
            )
            call0 = implied(
                row["series_ticker"],
                row["strike_type"],
                floor,
                cap,
                row["bound_f"],
                row["rain_seen"],
                0,
            )
            rows.append(
                {
                    "mode": mode,
                    "series": row["series_ticker"],
                    "city": row["city"],
                    "ticker": row["ticker"],
                    "day": row["day"],
                    "station": row["station"],
                    "result": row["result"],
                    "strike_type": row["strike_type"],
                    "floor_strike": floor,
                    "cap_strike": cap,
                    "bound_f": row["bound_f"],
                    "rain_seen": row["rain_seen"],
                    "has_station": row["station"] is not None,
                    "has_data": has_data,
                    "call": call,
                    "wrong": call is not None and call != row["result"],
                    "call0": call0,
                    "wrong0": call0 is not None and call0 != row["result"],
                }
            )
    if not rows:
        raise WeatherDataError("no settled market in scope in that window")
    detail = pl.DataFrame(rows, infer_schema_length=None)
    summary = (
        detail.group_by("mode", "series", "city")
        .agg(
            pl.len().alias("markets"),
            pl.col("has_station").sum().alias("with_station"),
            pl.col("has_data").sum().alias("with_data"),
            pl.col("call").is_not_null().sum().alias("called"),
            pl.col("wrong").sum().alias("wrong"),
            pl.col("call0").is_not_null().sum().alias("called_no_margin"),
            pl.col("wrong0").sum().alias("wrong_no_margin"),
        )
        .sort("mode", "series", "city")
    )
    return summary, detail.filter(pl.col("wrong") | pl.col("wrong0"))


def coverage(daily: pl.DataFrame) -> pl.DataFrame:
    """Per station (standard days): days with reports, and days short of 24 routine reports."""
    return (
        daily.filter(pl.col("mode") == "standard")
        .group_by("station")
        .agg(
            pl.len().alias("days"),
            (pl.col("routine") < 24).sum().alias("short_days"),
            pl.col("routine").min().alias("fewest_routine"),
        )
        .sort("station")
    )
