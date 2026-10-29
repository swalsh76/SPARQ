"""Command line for S3, the three measurements (PLAN §6.3).

    python -m sparq_analytics.survey dump --kalshi DIR --live DIR --out DIR [--cut DAY]
                                          [--only history|live]
    python -m sparq_analytics.survey lag  --kalshi DIR --live DIR --out DIR [--hours N]
    python -m sparq_analytics.survey tail --kalshi DIR --live DIR --out DIR [--cut DAY]

Run it on the analytics node. ``--kalshi`` is the history download's ``kalshi`` folder, ``--live``
the live copies' folder (``live extract``), ``--out`` where the results go. ``dump`` (S3.1) reads
the certainty dump and the 99 regimes from the history (markets closing on or before ``--cut``,
never after 2026-10-10), the queue at 99 cents from the live frames, and combines them into the
dollars a day a 99-cent bid would make, writing ``dump-history.json``, ``dump-regimes.parquet``,
``dump-live.json``, ``dump-episodes.parquet`` and ``dump.json`` (printed). ``--only history`` or
``--only live`` does one reading. ``lag`` (S3.2) builds the relation pairs from the live market
list, runs the event study over the live frames (the first ``--hours`` of the span when asked) and
writes ``lag-pairs.parquet``, ``lag-coverage.json``, an hour file of events each under
``lag-events`` and ``lag.json`` (its figures by kind printed); ``--kalshi`` supplies the series'
fee terms. ``tail`` (S3.3) reads the volume tiers from the history's trade files, the spreads and
the half-spread dollars from the live frames and trades, and the surprise rate from the history to
``--cut``, writing ``tail-volume.json``, ``tail-live.json``, ``tail-surprise.json`` and
``tail.json`` (its verdicts printed). A flag the command does not use is refused. Exit codes: 0
fine, 1 bad command line or data.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from sparq_analytics.live.source import LiveSourceError
from sparq_analytics.study.dataset import StudyDataError
from sparq_analytics.survey import lag, tail
from sparq_analytics.survey.dump import CUT, SurveyDataError, history
from sparq_analytics.survey.implied import combine
from sparq_analytics.survey.queue import fee_terms, live_reading
from sparq_analytics.survey.relations import pairs


class UsageError(Exception):
    """A bad or incomplete command line."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sparq_analytics.survey", description=__doc__.split("\n")[0]
    )
    parser.add_argument("command", choices=("dump", "lag", "tail"))
    parser.add_argument("--kalshi", type=Path, help="the history download's kalshi folder")
    parser.add_argument("--live", type=Path, help="the live copies' folder")
    parser.add_argument("--out", type=Path, help="where the results go")
    parser.add_argument(
        "--cut", type=date.fromisoformat, help=f"dump, tail: the last day read for outcomes ({CUT})"
    )
    parser.add_argument("--only", choices=("history", "live"), help="dump: one reading alone")
    parser.add_argument("--hours", type=int, help="lag: read only the span's first N hours")
    return parser


APPLIES = {"cut": ("dump", "tail"), "only": ("dump",), "hours": ("lag",)}


def _check(args: argparse.Namespace) -> None:
    """A flag given to a command it means nothing to is refused, not ignored."""
    for flag, commands in APPLIES.items():
        if getattr(args, flag) is not None and args.command not in commands:
            raise UsageError(f"--{flag} does not apply to {args.command}")


def _need(value: Path | None, name: str) -> Path:
    if value is None:
        raise UsageError(f"{name} is required")
    return value


def _write(out: Path, name: str, data: dict[str, Any]) -> None:
    (out / name).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _dump(args: argparse.Namespace) -> int:
    kalshi, out = _need(args.kalshi, "--kalshi"), _need(args.out, "--out")
    out.mkdir(parents=True, exist_ok=True)
    past: dict[str, Any] | None = None
    frames: dict[str, Any] | None = None
    if args.only != "live":
        past, regimes = history(kalshi, CUT if args.cut is None else args.cut)
        _write(out, "dump-history.json", past)
        regimes.write_parquet(out / "dump-regimes.parquet")
    if args.only != "history":
        frames, episodes = live_reading(_need(args.live, "--live"), fee_terms(kalshi), past)
        _write(out, "dump-live.json", frames)
        episodes.write_parquet(out / "dump-episodes.parquet")
    if past is not None and frames is not None:
        result = {"history": past, "live": frames, "implied": combine(past, frames)}
        _write(out, "dump.json", result)
        print(json.dumps(result["implied"], indent=2))
    else:
        print(json.dumps(past if past is not None else frames, indent=2, default=str))
    return 0


def _lag(args: argparse.Namespace) -> int:
    kalshi, live = _need(args.kalshi, "--kalshi"), _need(args.live, "--live")
    out = _need(args.out, "--out")
    out.mkdir(parents=True, exist_ok=True)
    start, end = lag.study_span(live, args.hours)
    markets = pl.read_parquet(live / "markets.parquet")
    built, coverage = pairs(markets, lag.active_tickers(live, start, end))
    built.write_parquet(out / "lag-pairs.parquet")
    _write(out, "lag-coverage.json", coverage)
    _, _, found = lag.run(
        live, built, markets, fee_terms(kalshi), out, hours=args.hours,
        log=lambda line: print(line, file=sys.stderr),
    )  # fmt: skip
    result = lag.summarize(out, start, end, found)
    result["coverage"] = coverage
    _write(out, "lag.json", result)
    shown = {k: result[k] for k in ("by_kind", "cleared", "cleared_reachable")}
    print(json.dumps(shown, indent=2))
    return 0


def _tail(args: argparse.Namespace) -> int:
    kalshi, live = _need(args.kalshi, "--kalshi"), _need(args.live, "--live")
    out = _need(args.out, "--out")
    out.mkdir(parents=True, exist_ok=True)
    volume = tail.volume(kalshi, tail.trade_days(kalshi))
    _write(out, "tail-volume.json", volume)
    live_reading = tail.live_reading(live, fee_terms(kalshi))
    _write(out, "tail-live.json", live_reading)
    surprise = tail.surprise(kalshi, CUT if args.cut is None else args.cut)
    _write(out, "tail-surprise.json", surprise)
    result = tail.combine(volume, live_reading, surprise)
    _write(out, "tail.json", result)
    thin = live_reading["thin_uncontested_half_spread_e6_a_day"]
    shown = {
        "cleared": result["cleared"],
        "thin_uncontested_half_spread_e6_a_day": thin,
        "surprise_total": surprise["total"],
        "surprise_by_category": surprise["by_category"],
        "clearing_series": surprise["clearing_series"],
    }  # fmt: skip
    print(json.dumps(shown, indent=2, default=str))
    return 0


HANDLERS = {"dump": _dump, "lag": _lag, "tail": _tail}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _check(args)
        return HANDLERS[args.command](args)
    except (
        UsageError,
        SurveyDataError,
        StudyDataError,
        LiveSourceError,
        ValueError,
        OSError,
        pl.exceptions.PolarsError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
