"""Command line for S1.2a, what the live book shows (D11).

    python -m sparq_analytics.live extract --data DIR --start 2026-10-24T21 [--end TIME]
    python -m sparq_analytics.live measure --data DIR --rule FILE --busy FILE --start DAY --end DAY
    python -m sparq_analytics.live report  --data DIR [--out FILE]

Run it on the analytics node. ``extract`` copies whole UTC hours of the ingest's ticker frames and
trades, and the market list, from the production database with the read-only role (settings in
``SPARQ_LIVE_CONFIG``; see ``source.py``) into ``DIR``, and resumes where it stopped. ``measure``
works one UTC day at a time from ``--start`` to ``--end`` (exclusive) with the forward rule and its
frozen activity file, writes the per-trade and per-order measures to ``DIR/results`` and the
summary to ``DIR/results/book.json``; ``report`` prints that as markdown. Times are UTC. Exit codes:
0 fine, 1 bad command line or data.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from sparq_analytics.live.book import (
    BookConfig,
    book_frames,
    burst_measures,
    near_gaps,
    tag_trades,
    trade_measures,
)
from sparq_analytics.live.source import (
    HOUR,
    LiveSourceError,
    config_path,
    connect,
    extract,
    hour_files,
    hour_name,
    last_hour_end,
    load_source,
)
from sparq_analytics.live.summary import in_any_population, render, summarize
from sparq_analytics.study.forward import busy_digest, read_busy
from sparq_analytics.study.rules import Rule, rules_from_json

COMMANDS = ("extract", "measure", "report")
DAY = timedelta(days=1)


class UsageError(Exception):
    """A bad or incomplete command line."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sparq_analytics.live", description=__doc__.split("\n")[0]
    )
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--data", type=Path, required=True, help="the folder for the copies")
    parser.add_argument("--start", help="extract: a UTC time; measure: the first UTC day")
    parser.add_argument("--end", help="extract: a UTC time (default now); measure: the day after")
    parser.add_argument("--rule", type=Path, help="the forward rule file (one rule)")
    parser.add_argument("--busy", type=Path, help="the frozen activity file")
    parser.add_argument("--resamples", type=int, default=2_000)
    parser.add_argument("--out", type=Path, help="report: write to this file instead of stdout")
    return parser


def _when(text: str | None, name: str) -> datetime:
    if text is None:
        raise UsageError(f"{name} is required")
    value = datetime.fromisoformat(text)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _day(text: str | None, name: str) -> date:
    when = _when(text, name)
    if when != when.replace(hour=0, minute=0, second=0, microsecond=0):
        raise UsageError(f"{name} is a day, not a time")
    return when.date()


def _one_rule(path: Path | None) -> Rule:
    if path is None:
        raise UsageError("--rule is required")
    rules = rules_from_json(json.loads(path.read_text(encoding="utf-8")))
    if len(rules) != 1:
        raise UsageError(f"{path} names {len(rules)} rules; S1.2a measures exactly one")
    return rules[0]


def market_groups(trades: pl.DataFrame, groups: int) -> list[list[str]]:
    """The traded markets in ``groups`` runs of about equal trade counts (at least one group)."""
    counts = trades.group_by("ticker").len().sort("ticker")
    if counts.is_empty():
        return [[]]
    total = int(counts["len"].sum())
    numbered = counts.with_columns(group=(pl.col("len").cum_sum() - 1) * groups // total)
    return [
        numbered.filter(pl.col("group") == g)["ticker"].to_list()
        for g in numbered["group"].unique().sort()
    ]


def measure_day(
    folder: Path,
    day: date,
    rule: Rule,
    busy: pl.DataFrame,
    config: BookConfig,
    *,
    groups: int = 4,
) -> tuple[pl.DataFrame, pl.DataFrame, datetime, int]:
    """One UTC day's per-trade and per-order measures, where its data ended, and how many of its
    trades were left out for being near an outage of the ticker feed.

    Every measure looks at one market at a time, so the day is measured in ``groups`` groups of
    markets, one after another, to keep the book and the windows of only one group in memory; the
    results are the same for any number of groups."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    end = start + DAY
    available = last_hour_end(folder)
    if available is None or available < end:
        raise LiveSourceError(f"{day} is not fully copied yet; run extract")
    data_end = min(end + timedelta(milliseconds=config.lookahead_ms), available)
    load_end = data_end.replace(minute=0, second=0, microsecond=0)
    if load_end < data_end:
        load_end += HOUR
    before = start - HOUR
    load_start = before if (folder / "trade" / f"{hour_name(before)}.parquet").exists() else start
    trades = pl.scan_parquet(hour_files(folder, "trade", load_start, load_end))
    frames = pl.scan_parquet(hour_files(folder, "ticker", load_start, load_end))
    markets = pl.scan_parquet(folder / "markets.parquet")
    gap_file = folder / "gaps.parquet"
    if not gap_file.exists():
        raise LiveSourceError(f"no list of feed outages at {gap_file}; run extract")
    tagged = tag_trades(trades, markets, busy, rule).collect()
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    data_end_ms = int(data_end.timestamp() * 1000)
    day_trades = tagged.filter((pl.col("ts_ms") >= start_ms) & (pl.col("ts_ms") < end_ms))
    near = near_gaps(pl.read_parquet(gap_file), config, data_end_ms)
    evaluated = day_trades.filter(~near)
    dropped = day_trades.height - evaluated.height
    measured_parts, burst_parts = [], []
    for markets_in_group in market_groups(evaluated, groups):
        mine = pl.col("ticker").is_in(markets_in_group)
        book = book_frames(frames.filter(mine)).collect().lazy()
        measured = trade_measures(
            evaluated.filter(mine).lazy(), tagged.filter(mine).lazy(), book, config, data_end_ms
        ).collect()
        burst_parts.append(burst_measures(measured.lazy(), book, config, data_end_ms).collect())
        measured_parts.append(measured)
        del book
    measured = pl.concat(measured_parts).sort("ticker", "ts_ms", maintain_order=True)
    bursts = pl.concat(burst_parts).sort("ticker", "ts_ms", "taker_yes")
    return measured, bursts, data_end, dropped


def _extract(args: argparse.Namespace) -> int:
    start = _when(args.start, "--start")
    end = _when(args.end, "--end") if args.end else datetime.now(UTC)
    source = load_source(config_path())

    async def run() -> Any:
        conn = await connect(source)
        try:
            return await extract(conn, args.data, start, end, now=datetime.now(UTC), log=print)
        finally:
            await conn.close()

    report = asyncio.run(run())
    print(
        f"copied {report.hours_copied} hour files, {report.hours_present} already there; "
        f"rows {report.rows}; markets {report.markets:,}; ticker gaps {report.gaps:,}"
    )
    return 0


def _measure(args: argparse.Namespace) -> int:
    first, last = _day(args.start, "--start"), _day(args.end, "--end")
    if last <= first:
        raise UsageError("--end must be after --start")
    if args.busy is None:
        raise UsageError("--busy is required")
    rule, busy = _one_rule(args.rule), read_busy(args.busy)
    config = BookConfig()
    results = args.data / "results"
    results.mkdir(parents=True, exist_ok=True)
    days: list[str] = []
    data_end: datetime | None = None
    dropped = 0
    day = first
    while day < last:
        # one day in memory at a time: it is written, then let go
        measured, bursts, data_end, near = measure_day(args.data, day, rule, busy, config)
        measured.write_parquet(results / f"trades-{day}.parquet")
        bursts.write_parquet(results / f"bursts-{day}.parquet")
        print(
            f"{day}: {measured.height:,} trades, {bursts.height:,} taker orders; "
            f"{near:,} trades left out near feed outages"
        )
        del measured, bursts
        days.append(str(day))
        dropped += near
        day += timedelta(days=1)
    # read back only the rows a population summarises (most trades are in none of them)
    keep = in_any_population(rule)
    trades = pl.scan_parquet([results / f"trades-{d}.parquet" for d in days]).filter(keep)
    orders = pl.scan_parquet([results / f"bursts-{d}.parquet" for d in days]).filter(keep)
    summary = summarize(trades.collect(), orders.collect(), rule, config, resamples=args.resamples)
    summary |= {
        "days": days,
        "data_end": str(data_end),
        "near_gaps": dropped,
        "busy_hash": busy_digest(args.busy),
        "rule_file": str(args.rule),
    }
    (results / "book.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    stop = summary["stop"]
    print(f"stop rule: {stop['outcome']} ({stop['share']} of {stop['orders']:,} cleared levels)")
    return 0


def _report(args: argparse.Namespace) -> int:
    path = args.data / "results" / "book.json"
    if not path.exists():
        raise UsageError(f"no summary at {path}; run measure first")
    text = render(json.loads(path.read_text(encoding="utf-8")))
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"extract": _extract, "measure": _measure, "report": _report}
    try:
        return handlers[args.command](args)
    except (UsageError, LiveSourceError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
