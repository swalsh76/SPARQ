"""The archived station reports, from the Iowa Environmental Mesonet (W3; GET only).

Routine and special reports only: the archive also keeps five-minute data, but that is not freely
available live, so a history built on it would assume what the live run cannot know. One station
at a time, routine then special, each asked once and paced to the archive's one request a second;
a station's file is written under a temporary name and renamed, so a stopped run resumes at the
first station without a file. Times are UTC.
"""

import csv
import io
import time
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
from sparq_core.config import WEATHER_ARCHIVE_URL, WEATHER_USER_AGENT
from sparq_core.ratelimit import backoff

from sparq_analytics.weather.bounds import REPORT_SCHEMA, ROUTINE, SPECIAL
from sparq_analytics.weather.stations import WeatherDataError

REPORT_TYPES = {ROUTINE: "3", SPECIAL: "4"}  # the archive's codes for routine and special reports
PACE_SECONDS = 1.1  # the archive throttles to one request a second per address
RETRIES = 6
RETRY_STATUS = {429, 500, 502, 503, 504}
HEADER = ["station", "valid", "metar"]


def archive_path(folder: Path, station: str, start: date, end: date) -> Path:
    return folder / "archive" / f"{station}-{start:%Y%m%d}-{end:%Y%m%d}.parquet"


def parse_csv(text: str, station: str, kind: str) -> pl.DataFrame:
    """The archive's CSV (``station,valid,metar``, times as ``YYYY-MM-DD HH:MM`` UTC)."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or rows[0] != HEADER:
        raise WeatherDataError(f"the archive's answer for {station} is not its CSV: {text[:120]!r}")
    out = []
    for row in rows[1:]:
        if not row:
            continue
        valid = datetime.strptime(row[1], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        out.append(
            {
                "station": station,
                "valid_ms": int(valid.timestamp() * 1000),
                "kind": kind,
                "metar": row[2],
            }
        )
    return pl.DataFrame(out, schema=REPORT_SCHEMA)


def fetch(
    client: httpx.Client,
    params: dict[str, str],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """One GET, retried with a capped backoff on the archive's "slow down" and server errors."""
    for attempt in range(RETRIES):
        try:
            response = client.get(WEATHER_ARCHIVE_URL, params=params)
        except httpx.TransportError:
            if attempt == RETRIES - 1:
                raise
        else:
            if response.status_code not in RETRY_STATUS:
                response.raise_for_status()
                return response.text
        if attempt < RETRIES - 1:
            sleep(backoff(attempt, 2.0, 60.0))
    raise WeatherDataError(f"the archive kept refusing after {RETRIES} tries")


def download(
    folder: Path,
    stations: Iterable[str],
    start: date,
    end: date,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """Each station's reports from ``start`` to ``end`` (exclusive); rows per station written."""
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": WEATHER_USER_AGENT}, timeout=120)
    written: dict[str, int] = {}
    try:
        for station in sorted(set(stations)):
            path = archive_path(folder, station, start, end)
            if path.exists():
                continue
            parts = []
            for kind, code in REPORT_TYPES.items():
                params = {
                    "station": station,
                    "data": "metar",
                    "tz": "Etc/UTC",
                    "format": "onlycomma",
                    "missing": "empty",
                    "report_type": code,
                    "sts": f"{start:%Y-%m-%d}T00:00Z",
                    "ets": f"{end:%Y-%m-%d}T00:00Z",
                }
                parts.append(parse_csv(fetch(client, params, sleep=sleep), station, kind))
                sleep(PACE_SECONDS)
            frame = pl.concat(parts).sort("valid_ms", "kind")
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(".partial")
            frame.write_parquet(partial)
            partial.replace(path)
            written[station] = frame.height
            log(f"{station}: {frame.height:,} reports")
    finally:
        if own:
            client.close()
    return written


def load_reports(folder: Path) -> pl.DataFrame:
    files = sorted((folder / "archive").glob("*.parquet"))
    if not files:
        raise WeatherDataError(f"no archived reports under {folder / 'archive'}; run download")
    return pl.read_parquet(files).unique(maintain_order=True).sort("station", "valid_ms", "kind")
