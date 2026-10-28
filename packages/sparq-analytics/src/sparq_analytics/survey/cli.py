"""Command line for S3, the three measurements (PLAN §6.3).

    python -m sparq_analytics.survey dump --kalshi DIR --live DIR --out DIR [--cut DAY]
                                          [--only history|live]

Run it on the analytics node. ``--kalshi`` is the history download's ``kalshi`` folder, ``--live``
the live copies' folder (``live extract``), ``--out`` where the results go. ``dump`` (S3.1) reads
the certainty dump and the 99 regimes from the history (markets closing on or before ``--cut``,
never after 2026-10-10), the queue at 99 cents from the live frames, and combines them into the
dollars a day a 99-cent bid would make, writing ``dump-history.json``, ``dump-regimes.parquet``,
``dump-live.json``, ``dump-episodes.parquet`` and ``dump.json`` (printed). ``--only history`` or
``--only live`` does one reading. Exit codes: 0 fine, 1 bad command line or data.
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
from sparq_analytics.survey.dump import CUT, SurveyDataError, history
from sparq_analytics.survey.implied import combine
from sparq_analytics.survey.queue import fee_terms, live_reading


class UsageError(Exception):
    """A bad or incomplete command line."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sparq_analytics.survey", description=__doc__.split("\n")[0]
    )
    parser.add_argument("command", choices=("dump",))
    parser.add_argument("--kalshi", type=Path, help="the history download's kalshi folder")
    parser.add_argument("--live", type=Path, help="the live copies' folder")
    parser.add_argument("--out", type=Path, help="where the results go")
    parser.add_argument("--cut", type=date.fromisoformat, default=CUT)
    parser.add_argument("--only", choices=("history", "live"))
    return parser


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
        past, regimes = history(kalshi, args.cut)
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


HANDLERS = {"dump": _dump}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
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
