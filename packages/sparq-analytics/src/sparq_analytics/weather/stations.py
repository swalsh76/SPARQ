"""Which station settles which market, and which hours make its day (S2.1, PLAN §6.2 W3).

Kalshi's daily-high and rain markets name an NWS climate site in their rules (``CLINYC``,
``CLIMDW``, ...); the site's reports come from one airport or city station, listed here by hand
and proved by ``check`` (a wrong station shows up as bounds on the wrong side of the results). The
code is read
from each market's own rules, not per series, because a series can move: Houston's rain market
named ``CLIHOU`` and later ``CLIIAH``. The first week of the history names only a city; those
markets take their series' one code.

An NWS climate day runs from midnight to midnight local *standard* time all year, so in summer it
is 01:00 to 01:00 on the clock; the "civil" day (the clock's midnight to midnight) is kept as a
variant because the rain markets closed an hour earlier in August.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

DayMode = Literal["standard", "civil"]
DAY_MODES: tuple[DayMode, ...] = ("standard", "civil")
RAIN_SERIES = "KXRAIN"
HIGH_PREFIX = "KXHIGH"
# the test part of W6 starts here: nothing in S2.1 or S2.2 reads a result from it
TEST_START = date(2026, 10, 11)
FIT_END = date(2026, 9, 28)


@dataclass(frozen=True)
class Site:
    station: str  # the ICAO identifier of the station whose reports make the climate value
    tz: str  # the IANA zone; the climate day uses its standard offset


SITES: dict[str, Site] = {
    "CLIABQ": Site("KABQ", "America/Denver"),
    "CLIATL": Site("KATL", "America/New_York"),
    "CLIAUS": Site("KAUS", "America/Chicago"),  # Austin-Bergstrom, not Camp Mabry (CLIATT)
    "CLIBOS": Site("KBOS", "America/New_York"),
    "CLICLL": Site("KCLL", "America/Chicago"),
    "CLICMH": Site("KCMH", "America/New_York"),
    "CLIDCA": Site("KDCA", "America/New_York"),
    "CLIDEN": Site("KDEN", "America/Denver"),
    "CLIDFW": Site("KDFW", "America/Chicago"),
    "CLIDJT": Site("KPBI", "America/New_York"),  # West Palm Beach; from 2026-10-18 only (test part)
    "CLIDTW": Site("KDTW", "America/Detroit"),
    "CLIEWR": Site("KEWR", "America/New_York"),
    "CLIHOU": Site("KHOU", "America/Chicago"),  # Hobby
    "CLIIAH": Site("KIAH", "America/Chicago"),  # Bush Intercontinental
    "CLIIND": Site("KIND", "America/Indiana/Indianapolis"),
    "CLILAS": Site("KLAS", "America/Los_Angeles"),
    "CLILAX": Site("KLAX", "America/Los_Angeles"),
    "CLILEX": Site("KLEX", "America/New_York"),
    "CLIMDW": Site("KMDW", "America/Chicago"),  # Midway: the daily high
    "CLIMIA": Site("KMIA", "America/New_York"),
    "CLIMKE": Site("KMKE", "America/Chicago"),
    "CLIMSP": Site("KMSP", "America/Chicago"),
    "CLIMSY": Site("KMSY", "America/Chicago"),
    "CLINYC": Site("KNYC", "America/New_York"),  # Central Park
    "CLIOKC": Site("KOKC", "America/Chicago"),
    "CLIORD": Site("KORD", "America/Chicago"),  # O'Hare: Chicago's rain
    "CLIPHL": Site("KPHL", "America/New_York"),
    "CLIPHX": Site("KPHX", "America/Phoenix"),
    "CLIPIT": Site("KPIT", "America/New_York"),
    "CLIPVD": Site("KPVD", "America/New_York"),
    "CLISAN": Site("KSAN", "America/Los_Angeles"),
    "CLISAT": Site("KSAT", "America/Chicago"),
    "CLISDF": Site("KSDF", "America/Kentucky/Louisville"),
    "CLISEA": Site("KSEA", "America/Los_Angeles"),
    "CLISFO": Site("KSFO", "America/Los_Angeles"),
    "CLISGF": Site("KSGF", "America/Chicago"),
    "CLISTL": Site("KSTL", "America/Chicago"),
    "CLITPA": Site("KTPA", "America/New_York"),
    "CLITTN": Site("KTTN", "America/New_York"),
}
BY_STATION = {site.station: code for code, site in SITES.items()}
CODE = re.compile(r"\b(CLI[A-Z]{3})\b")
STATION = re.compile(r"\b(K[A-Z]{3})\b")
MONTHS = {
    month: number
    for number, month in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1
    )
}
EVENT_DAY = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})$")


class WeatherDataError(Exception):
    """The station map, the reports or the markets are missing or inconsistent."""


def in_scope(series: str) -> bool:
    """W1: the daily highs and the daily rain markets."""
    return series == RAIN_SERIES or series.startswith(HIGH_PREFIX)


def code_in(rules: str | None) -> str | None:
    """The climate code a market's rules name, by its code or by its station."""
    if not rules:
        return None
    if found := CODE.search(rules):
        return found.group(1)
    for station in STATION.findall(rules):
        if station in BY_STATION:
            return BY_STATION[station]
    return None


def city_of(series: str, ticker: str) -> str:
    """A rain event holds one market per city; a high series is one city."""
    return ticker.rsplit("-", 1)[-1] if series == RAIN_SERIES else series


def market_day(event_ticker: str) -> date:
    """The day an event is about, from its ticker (``KXHIGHNY-26OCT15`` is 2026-10-15)."""
    parts = event_ticker.split("-")
    found = EVENT_DAY.match(parts[1]) if len(parts) > 1 else None
    if found is None or found.group(2) not in MONTHS:
        raise WeatherDataError(f"no day in the event ticker {event_ticker!r}")
    year, month, day = found.groups()
    return date(2000 + int(year), MONTHS[month], int(day))


def standard_offset(tz: ZoneInfo, year: int) -> timedelta:
    """The zone's offset from UTC in winter (no zone here is in the southern hemisphere)."""
    offset = datetime(year, 1, 15, 12, tzinfo=tz).utcoffset()
    if offset is None:  # pragma: no cover - a ZoneInfo always has an offset
        raise WeatherDataError(f"{tz} has no offset")
    return offset


def climate_day(when: datetime, tz: ZoneInfo, mode: DayMode) -> date:
    """The day a moment belongs to: by local standard time, or by the local clock."""
    when = when.astimezone(UTC)
    if mode == "civil":
        return when.astimezone(tz).date()
    return (when + standard_offset(tz, when.year)).date()


def day_bounds(day: date, tz: ZoneInfo, mode: DayMode) -> tuple[datetime, datetime]:
    """The UTC start and end (exclusive) of a climate day."""
    if mode == "civil":
        start = datetime(day.year, day.month, day.day, tzinfo=tz)
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        return start.astimezone(UTC), end.astimezone(UTC)
    offset = standard_offset(tz, day.year)
    start = datetime(day.year, day.month, day.day, tzinfo=UTC) - offset
    return start, start + timedelta(days=1)
