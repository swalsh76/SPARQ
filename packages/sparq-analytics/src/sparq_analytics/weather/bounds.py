"""From one station's reports to what each moment of a climate day already settles (W4).

For every report and both kinds of day (``stations.DAY_MODES``):

* ``temp_f``: the lowest whole °F its temperature can be; ``max6_f`` the same for its six-hour
  maximum, counted only when those six hours lie wholly inside the day;
* ``rain``: at least 0.01 inch since the last routine report, counted only when that report was
  also inside the day (rain just before midnight must not count for the new day);
* ``bound_f`` and ``rain_seen``: the running maximum and the running "it has rained" over the
  day's reports so far, in the order the reports were made.

The day's last row is what the whole day settled; S2.2 adds the publication delay to each row's
time to know when the market could have known it.
"""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import polars as pl

from sparq_analytics.weather.metar import lowest_f, parse
from sparq_analytics.weather.stations import BY_STATION, DAY_MODES, SITES, climate_day

SIX_HOURS = timedelta(hours=6)
RAIN_E2 = 1  # W4: a report of at least 0.01 inch
ROUTINE, SPECIAL = "routine", "special"

REPORT_SCHEMA = {"station": pl.String, "valid_ms": pl.Int64, "kind": pl.String, "metar": pl.String}
BOUND_SCHEMA = {
    "station": pl.String,
    "valid_ms": pl.Int64,
    "kind": pl.String,
    "mode": pl.String,
    "day": pl.Date,
    "temp_f": pl.Int32,
    "max6_f": pl.Int32,
    "precip_e2": pl.Int32,
    "rain": pl.Boolean,
}


def _rows(station: str, reports: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    if station not in BY_STATION:
        raise ValueError(f"no climate site uses the station {station}")
    tz = ZoneInfo(SITES[BY_STATION[station]].tz)
    out: list[dict[str, object]] = []
    last_routine: datetime | None = None
    for report in sorted(reports, key=lambda r: (int(str(r["valid_ms"])), str(r["kind"]))):
        valid_ms = int(str(report["valid_ms"]))
        valid = datetime.fromtimestamp(valid_ms / 1000, UTC)
        parsed = parse(str(report["metar"]))
        since = last_routine  # a report's rain is counted from the routine report before it
        for mode in DAY_MODES:
            day = climate_day(valid, tz, mode)
            temp_f = None if parsed.temp_c10 is None else lowest_f(parsed.temp_c10)
            max6_f = None
            if parsed.max6_c10 is not None and climate_day(valid - SIX_HOURS, tz, mode) == day:
                max6_f = lowest_f(parsed.max6_c10)
            rain = (
                parsed.precip_e2 is not None
                and parsed.precip_e2 >= RAIN_E2
                and since is not None
                and climate_day(since, tz, mode) == day
            )
            out.append(
                {
                    "station": station,
                    "valid_ms": valid_ms,
                    "kind": report["kind"],
                    "mode": mode,
                    "day": day,
                    "temp_f": temp_f,
                    "max6_f": max6_f,
                    "precip_e2": parsed.precip_e2,
                    "rain": rain,
                }
            )
        if report["kind"] == ROUTINE:
            last_routine = valid
    return out


def report_bounds(reports: pl.DataFrame) -> pl.DataFrame:
    """Every report once per kind of day, with its own readings and the day's running bounds."""
    rows: list[dict[str, object]] = []
    for (station,), group in reports.group_by(["station"], maintain_order=True):
        rows.extend(_rows(str(station), group.iter_rows(named=True)))
    frame = pl.DataFrame(rows, schema=BOUND_SCHEMA)
    day = ["station", "mode", "day"]
    return frame.sort(*day, "valid_ms", "kind").with_columns(
        # a report without a temperature keeps the bound so far (cum_max leaves its null)
        bound_f=pl.max_horizontal("temp_f", "max6_f").cum_max().forward_fill().over(day),
        rain_seen=pl.col("rain").cast(pl.Int8).cum_max().cast(pl.Boolean).over(day),
    )


def daily_bounds(bounds: pl.DataFrame) -> pl.DataFrame:
    """What each whole climate day settled, and how many reports it had."""
    return (
        bounds.group_by("station", "mode", "day")
        .agg(
            pl.col("bound_f").last(),
            pl.col("rain_seen").last(),
            pl.len().alias("reports"),
            (pl.col("kind") == ROUTINE).sum().alias("routine"),
        )
        .sort("station", "mode", "day")
    )
