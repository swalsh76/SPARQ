"""Command line for S2.1, the weather sources (PLAN §6.2).

    python -m sparq_analytics.weather stations --kalshi DIR
    python -m sparq_analytics.weather download --data DIR --start DAY --end DAY
    python -m sparq_analytics.weather check    --data DIR --kalshi DIR [--first DAY] [--last DAY]
    python -m sparq_analytics.weather delay    --data DIR --hours H
    python -m sparq_analytics.weather delay-report --data DIR

Run it on the analytics node. ``--kalshi`` is the history download's ``kalshi`` folder, ``--data``
the weather folder. ``stations`` lists the climate site and station of every market in scope (from
the history's start to the end of the validate part) and fails if one names a site the map lacks;
``download`` fetches the archived reports of every mapped station; ``check`` compares each day's
bound with Kalshi's results, on the fit part by default and never on the test part, writing
``check.json``; ``delay`` records the live reports for ``H`` hours into ``live/delay.csv`` and
``delay-report`` summarises it against the archive. Exit codes: 0 fine, 1 bad command line or
data, 2 the check found a wrong call or an unmapped site.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

import httpx
import polars as pl

from sparq_analytics.study.dataset import StudyDataError
from sparq_analytics.weather import archive, live
from sparq_analytics.weather.bounds import daily_bounds, report_bounds
from sparq_analytics.weather.check import check, coverage, scope_markets
from sparq_analytics.weather.stations import FIT_END, SITES, TEST_START, WeatherDataError

HISTORY_START = date(2026, 8, 7)
FOUND_PROBLEMS = 2


class UsageError(Exception):
    """A bad or incomplete command line."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sparq_analytics.weather", description=__doc__.split("\n")[0]
    )
    parser.add_argument(
        "command", choices=("stations", "download", "check", "delay", "delay-report")
    )
    parser.add_argument("--data", type=Path, help="the weather folder")
    parser.add_argument("--kalshi", type=Path, help="the history download's kalshi folder")
    parser.add_argument("--start", type=date.fromisoformat, help="download: the first UTC day")
    parser.add_argument("--end", type=date.fromisoformat, help="download: the day after the last")
    parser.add_argument("--first", type=date.fromisoformat, default=HISTORY_START)
    parser.add_argument("--last", type=date.fromisoformat, default=FIT_END)
    parser.add_argument("--hours", type=float, default=24.0)
    return parser


def _need(value: Path | None, name: str) -> Path:
    if value is None:
        raise UsageError(f"{name} is required")
    return value


def _stations(args: argparse.Namespace) -> int:
    markets = scope_markets(
        _need(args.kalshi, "--kalshi"), HISTORY_START, TEST_START - timedelta(days=1)
    )
    table = (
        markets.group_by("series_ticker", "city", "code", "station")
        .agg(
            pl.len().alias("markets"),
            pl.col("day").min().alias("first"),
            pl.col("day").max().alias("last"),
        )
        .sort("series_ticker", "city", "first")
    )
    with pl.Config(tbl_rows=200, tbl_width_chars=200):
        print(table)
    missing = table.filter(pl.col("station").is_null())
    if not missing.is_empty():
        print(f"{missing['markets'].sum():,} markets name no mapped site", file=sys.stderr)
        return FOUND_PROBLEMS
    return 0


def _download(args: argparse.Namespace) -> int:
    if args.start is None or args.end is None or args.end <= args.start:
        raise UsageError("--start and a later --end are required")
    stations = [site.station for site in SITES.values()]
    written = archive.download(_need(args.data, "--data"), stations, args.start, args.end)
    print(f"downloaded {len(written)} stations, {sum(written.values()):,} reports")
    return 0


def _check(args: argparse.Namespace) -> int:
    data = _need(args.data, "--data")
    markets = scope_markets(_need(args.kalshi, "--kalshi"), args.first, args.last)
    daily = daily_bounds(report_bounds(archive.load_reports(data)))
    summary, wrong = check(markets, daily)
    covered = coverage(daily)
    with pl.Config(tbl_rows=300, tbl_width_chars=220):
        print(summary)
        print(covered)
        if not wrong.is_empty():
            print(wrong.select("mode", "ticker", "bound_f", "rain_seen", "result", "call", "call0"))
    totals = summary.group_by("mode").agg(pl.exclude("series", "city").sum()).sort("mode")
    print(totals)
    out = {
        "first": str(args.first),
        "last": str(args.last),
        "totals": totals.to_dicts(),
        "summary": summary.to_dicts(),
        "wrong": wrong.with_columns(pl.col("day").cast(pl.String)).to_dicts(),
        "coverage": covered.to_dicts(),
    }
    (data / "check.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    unmapped = markets.filter(pl.col("station").is_null()).height
    if unmapped:
        print(f"{unmapped:,} markets name no mapped site", file=sys.stderr)
    return FOUND_PROBLEMS if unmapped or not wrong.filter(pl.col("wrong")).is_empty() else 0


def _delay(args: argparse.Namespace) -> int:
    path = _need(args.data, "--data") / "live" / "delay.csv"
    live.record(path, [site.station for site in SITES.values()], args.hours)
    return 0


def _delay_report(args: argparse.Namespace) -> int:
    data = _need(args.data, "--data")
    try:
        reports = archive.load_reports(data)
    except WeatherDataError:
        reports = None
    print(json.dumps(live.summarize(data / "live" / "delay.csv", reports), indent=2))
    return 0


HANDLERS = {
    "stations": _stations,
    "download": _download,
    "check": _check,
    "delay": _delay,
    "delay-report": _delay_report,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return HANDLERS[args.command](args)
    except (
        UsageError,
        WeatherDataError,
        StudyDataError,
        ValueError,
        OSError,
        httpx.HTTPError,
        pl.exceptions.PolarsError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
