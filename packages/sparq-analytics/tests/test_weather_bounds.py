from datetime import UTC, date, datetime

import polars as pl
import pytest
from sparq_analytics.weather.bounds import REPORT_SCHEMA, daily_bounds, report_bounds

OCT14, OCT15 = date(2026, 10, 14), date(2026, 10, 15)


def ms(hour: int, minute: int, day: int = 15) -> int:
    return int(datetime(2026, 10, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


# Central Park on 15 October 2026 (daylight time: the standard day starts 05:00 UTC, the civil
# day 04:00 UTC), in the order made
REPORTS = [
    # 00:51 EDT: 20.0 C (68 F), 0.05 in since a report before the run
    ("routine", ms(4, 51), "KNYC 150451Z AUTO 10SM 20/10 RMK AO2 T02000100 P0005"),
    # 21.1 C (69.98, so 69 F); rain since 04:51, which is the previous standard day
    ("routine", ms(5, 51), "KNYC 150551Z AUTO 10SM 21/10 RMK AO2 T02110100 P0002"),
    # a special without the tenths: 22 C is at least 21.5 C, 70.7, so 70 F; rain since 05:51
    ("special", ms(6, 10), "KNYC 150610Z AUTO 10SM 22/10 RMK AO2 P0001"),
    # 16.0 C (60 F) and a six-hour maximum of 30.0 C whose hours began on the 14th: not counted
    ("routine", ms(8, 51), "KNYC 150851Z AUTO 10SM 16/10 RMK AO2 T01600100 10300"),
    # 15.0 C (59 F) and a six-hour maximum of 25.0 C (77 F) for 05:51 to 11:51: counted
    ("routine", ms(11, 51), "KNYC 151151Z AUTO 10SM 15/10 RMK AO2 T01500100 10250"),
    # no temperature at all: the bound stays
    ("routine", ms(12, 51), "KNYC 151251Z AUTO 10SM A3018 RMK AO2"),
]


def frame(rows: list[tuple[str, int, str]], station: str = "KNYC") -> pl.DataFrame:
    return pl.DataFrame(
        [{"station": station, "valid_ms": v, "kind": k, "metar": m} for k, v, m in rows],
        schema=REPORT_SCHEMA,
    )


def test_the_standard_day_keeps_its_running_bound_and_rain() -> None:
    bounds = report_bounds(frame(list(reversed(REPORTS))))  # any input order
    day = bounds.filter((pl.col("mode") == "standard") & (pl.col("day") == OCT15))
    assert day["temp_f"].to_list() == [69, 70, 60, 59, None]
    assert day["max6_f"].to_list() == [None, None, None, 77, None]
    assert day["bound_f"].to_list() == [69, 70, 70, 77, 77]
    assert day["rain"].to_list() == [False, True, False, False, False]
    assert day["rain_seen"].to_list() == [False, True, True, True, True]
    earlier = bounds.filter((pl.col("mode") == "standard") & (pl.col("day") == OCT14))
    assert earlier["bound_f"].to_list() == [68]
    assert earlier["rain"].to_list() == [False]  # no routine report before it in the data


def test_the_civil_day_takes_the_hour_after_midnight_and_its_rain() -> None:
    bounds = report_bounds(frame(REPORTS))
    day = bounds.filter((pl.col("mode") == "civil") & (pl.col("day") == OCT15))
    assert day["bound_f"].to_list() == [68, 69, 70, 70, 77, 77]
    assert day["rain"].to_list() == [False, True, True, False, False, False]


def test_the_daily_rows_are_the_last_of_each_day() -> None:
    daily = daily_bounds(report_bounds(frame(REPORTS)))
    standard = daily.filter(pl.col("mode") == "standard").to_dicts()
    assert [
        (r["day"], r["bound_f"], r["rain_seen"], r["reports"], r["routine"]) for r in standard
    ] == [
        (OCT14, 68, False, 1, 1),
        (OCT15, 77, True, 5, 4),
    ]
    civil = daily.filter(pl.col("mode") == "civil").to_dicts()
    assert [(r["day"], r["bound_f"], r["reports"]) for r in civil] == [(OCT15, 77, 6)]


def test_stations_are_kept_apart() -> None:
    other = frame([("routine", ms(12, 51), "KMDW 151251Z 10SM 30/10 RMK AO2 T03000100")], "KMDW")
    daily = daily_bounds(report_bounds(pl.concat([frame(REPORTS), other])))
    chicago = daily.filter((pl.col("station") == "KMDW") & (pl.col("mode") == "standard"))
    assert chicago["bound_f"].to_list() == [86]
    new_york = daily.filter((pl.col("station") == "KNYC") & (pl.col("day") == OCT15))
    assert new_york["bound_f"].to_list() == [77, 77]


def test_a_special_report_does_not_restart_the_rain_window() -> None:
    # the routine report at 05:51 counts its rain from 04:51, the day before, even though a special
    # came at 05:10, inside the new standard day
    rows = [
        ("routine", ms(4, 51), "KNYC 150451Z AUTO 10SM 20/10 RMK AO2 T02000100"),
        ("special", ms(5, 10), "KNYC 150510Z AUTO 10SM 20/10 RMK AO2"),
        ("routine", ms(5, 51), "KNYC 150551Z AUTO 10SM 20/10 RMK AO2 T02000100 P0002"),
    ]
    bounds = report_bounds(frame(rows))
    day = bounds.filter((pl.col("mode") == "standard") & (pl.col("day") == OCT15))
    assert day["rain"].to_list() == [False, False]


def test_a_station_no_site_uses_is_refused() -> None:
    with pytest.raises(ValueError, match="KZZZ"):
        report_bounds(frame(REPORTS, "KZZZ"))
