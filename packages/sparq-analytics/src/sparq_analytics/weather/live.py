"""How late a station report becomes public (S2.1; W4 sets the history's delay from this).

``record`` asks NOAA's aviation weather service once a minute for the stations' recent reports (one
request for all of them, against its limit of 100 a minute) and appends each report it has not
seen to a CSV with the time it first saw it; the reports already there at the first request are
marked ``baseline``, since when they appeared is unknown. A failed request is logged and the next
minute tries again. ``summarize`` gives the delays (first seen and the service's receipt, after
the observation) and compares the live reports with the archive's.
"""

import csv
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl
from sparq_core.config import WEATHER_LIVE_URL, WEATHER_USER_AGENT

from sparq_analytics.weather.metar import parse

POLL_SECONDS = 60.0
FIELDS = [
    "station",
    "kind",
    "obs_ms",
    "report_ms",
    "receipt_ms",
    "first_seen_ms",
    "baseline",
    "raw",
]
TEXT_FIELDS = ("station", "kind", "raw")
CSV_SCHEMA = {f: pl.String if f in TEXT_FIELDS else pl.Int64 for f in FIELDS}


def _iso_ms(text: str | None) -> int | None:
    if not text:
        return None
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def poll(client: httpx.Client, stations: Iterable[str]) -> list[dict[str, Any]]:
    """The service's reports of the last two hours for ``stations``."""
    response = client.get(
        WEATHER_LIVE_URL, params={"ids": ",".join(sorted(stations)), "format": "json", "hours": "2"}
    )
    response.raise_for_status()
    reports = []
    for item in response.json():
        reports.append(
            {
                "station": item["icaoId"],
                "kind": item.get("metarType", "METAR"),
                "obs_ms": int(item["obsTime"]) * 1000,
                "report_ms": _iso_ms(item.get("reportTime")),
                "receipt_ms": _iso_ms(item.get("receiptTime")),
                "raw": item["rawOb"],
            }
        )
    return reports


def record(
    path: Path,
    stations: Iterable[str],
    hours: float,
    *,
    client: httpx.Client | None = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> int:
    """Poll for ``hours``; returns how many new reports were written."""
    stations = sorted(set(stations))
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": WEATHER_USER_AGENT}, timeout=30)
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    seen: set[tuple[str, int, str]] = set()
    if not fresh:
        # a restart appends: the reports already written are not written again
        with path.open(newline="", encoding="utf-8") as existing:
            seen = {(r["station"], int(r["obs_ms"]), r["raw"]) for r in csv.DictReader(existing)}
    written, first = 0, True
    end = clock() + hours * 3600
    try:
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            if fresh:
                writer.writeheader()
            while True:
                started = clock()
                try:
                    reports = poll(client, stations)
                except (httpx.HTTPError, ValueError, KeyError) as error:
                    log(f"poll failed ({type(error).__name__}); trying again next minute")
                    reports = []
                else:
                    now_ms = int(clock() * 1000)
                    for report in reports:
                        key = (report["station"], report["obs_ms"], report["raw"])
                        if key in seen:
                            continue
                        seen.add(key)
                        writer.writerow(report | {"first_seen_ms": now_ms, "baseline": int(first)})
                        written += 0 if first else 1
                    handle.flush()
                    first = False
                if started + POLL_SECONDS >= end:
                    break
                sleep(max(0.0, started + POLL_SECONDS - clock()))
    finally:
        if own:
            client.close()
    log(f"{written:,} new reports recorded")
    return written


def _norm(raw: str) -> str:
    words = raw.split()
    if words and words[0] in ("METAR", "SPECI"):
        words = words[1:]
    return " ".join(words)


def _quantiles(values: pl.Series) -> dict[str, float | None]:
    if values.is_empty():
        return {"median": None, "p95": None, "max": None}
    return {
        "median": float(values.median() or 0.0),  # type: ignore[arg-type]
        "p95": float(values.quantile(0.95, "nearest") or 0.0),
        "max": float(values.max() or 0.0),  # type: ignore[arg-type]
    }


def summarize(path: Path, archive: pl.DataFrame | None = None) -> dict[str, Any]:
    """The delays in seconds after the observation, and, with ``archive``, how many live reports
    the archive holds with the same text and the same parsed readings."""
    live = pl.read_csv(path, schema=CSV_SCHEMA)
    timed = live.filter(pl.col("baseline") == 0)
    seen_s = ((timed["first_seen_ms"] - timed["obs_ms"]) / 1000).drop_nulls()
    receipt_s = ((timed["receipt_ms"] - timed["obs_ms"]) / 1000).drop_nulls()
    out: dict[str, Any] = {
        "reports": live.height,
        "timed": timed.height,
        "stations": live["station"].n_unique(),
        "first_seen_after_obs_s": _quantiles(seen_s),
        "receipt_after_obs_s": _quantiles(receipt_s),
    }
    if archive is not None:
        # the archive can hold two reports in one minute (routine and special), so each live
        # report is matched once, against any of them
        archived = archive.group_by("station", pl.col("valid_ms").alias("obs_ms")).agg(
            pl.col("metar").alias("archived")
        )
        joined = live.join(archived, on=["station", "obs_ms"], how="left")
        same_text = same_reading = found = 0
        for row in joined.iter_rows(named=True):
            if row["archived"] is None:
                continue
            found += 1
            same_text += any(_norm(row["raw"]) == _norm(text) for text in row["archived"])
            same_reading += any(parse(row["raw"]) == parse(text) for text in row["archived"])
        out["archive"] = {"found": found, "same_text": same_text, "same_reading": same_reading}
    return out
