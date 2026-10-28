from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from sparq_analytics.weather.stations import (
    BY_STATION,
    SCOPE,
    SITES,
    WeatherDataError,
    city_of,
    climate_day,
    code_in,
    day_bounds,
    market_day,
)

NY = ZoneInfo("America/New_York")


def test_every_site_has_a_real_zone_and_its_own_station() -> None:
    for site in SITES.values():
        ZoneInfo(site.tz)
    assert len(BY_STATION) == len(SITES)


def test_the_code_comes_from_the_rules_by_name_or_by_station() -> None:
    assert (
        code_in("the maximum temperature recorded at New York City (CLINYC) for Oct 23") == "CLINYC"
    )
    assert code_in("If the total precipitation at KMKE in Milwaukee in Sep 16") == "CLIMKE"
    assert code_in("recorded at Austin for Aug 14, 2026") is None
    assert code_in("at KZZZ somewhere") is None
    assert code_in(None) is None and code_in("") is None


def test_the_city_and_the_day_of_a_market() -> None:
    assert city_of("KXRAIN", "KXRAIN-26OCT15-NOLA") == "NOLA"
    assert city_of("KXHIGHNY", "KXHIGHNY-26OCT15-T80") == "KXHIGHNY"
    assert market_day("KXHIGHNY-26OCT15") == date(2026, 10, 15)
    assert market_day("KXRAIN-26AUG07") == date(2026, 8, 7)


@pytest.mark.parametrize("ticker", ["KXHIGHNY", "KXHIGHNY-26XYZ15", "KXHIGHNY-2610-15"])
def test_an_event_ticker_without_a_day_is_refused(ticker: str) -> None:
    with pytest.raises(WeatherDataError):
        market_day(ticker)


def test_the_scope_is_the_daily_highs_and_rain() -> None:
    assert {"KXRAIN", "KXHIGHTSEA"} <= SCOPE
    assert not {"KXHIGHINFLATION", "KXTEMPNYCH", "KXLOWTAUS", "KXRAINNYCM"} & SCOPE


def test_in_summer_the_standard_day_starts_at_one_on_the_clock() -> None:
    half_past_midnight_edt = datetime(2026, 10, 20, 4, 30, tzinfo=UTC)
    assert climate_day(half_past_midnight_edt, NY, "standard") == date(2026, 10, 19)
    assert climate_day(half_past_midnight_edt, NY, "civil") == date(2026, 10, 20)
    assert day_bounds(date(2026, 10, 15), NY, "standard") == (
        datetime(2026, 10, 15, 5, tzinfo=UTC),
        datetime(2026, 10, 16, 5, tzinfo=UTC),
    )
    assert day_bounds(date(2026, 10, 15), NY, "civil") == (
        datetime(2026, 10, 15, 4, tzinfo=UTC),
        datetime(2026, 10, 16, 4, tzinfo=UTC),
    )


def test_the_civil_day_follows_the_clock_across_the_autumn_change() -> None:
    # 2026-11-01 is 25 hours long in New York; its standard day stays 24
    start, end = day_bounds(date(2026, 11, 1), NY, "civil")
    assert (end - start).total_seconds() == 25 * 3600
    start, end = day_bounds(date(2026, 11, 1), NY, "standard")
    assert (end - start).total_seconds() == 24 * 3600


def test_phoenix_keeps_one_day_all_year() -> None:
    phoenix = ZoneInfo("America/Phoenix")
    july = date(2026, 7, 1)
    assert day_bounds(july, phoenix, "standard") == day_bounds(july, phoenix, "civil")
