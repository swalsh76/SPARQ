"""Command line for S4, the paper quoter (PLAN §6.4).

    python -m sparq_analytics.paper fit --kalshi DIR --live DIR --out DIR [--hours N]

Run it on the analytics node. ``--kalshi`` is the history download's ``kalshi`` folder (the
series' fee terms), ``--live`` the live copies' folder (``live extract``), ``--out`` where the
results go. ``fit`` (S4.1) replays the paper quoter over the live frames and trades (the first
``--hours`` of the span when asked) and writes ``paper-fills.parquet`` and ``paper.json`` (its
figures by rule printed). It sends no order and makes no API call. Exit codes: 0 fine, 1 bad command
line or data.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from sparq_analytics.paper import quoter
from sparq_analytics.survey.cli import ERRORS, need
from sparq_analytics.survey.queue import fee_terms


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sparq_analytics.paper", description=__doc__.split("\n")[0]
    )
    parser.add_argument("command", choices=("fit",))
    parser.add_argument("--kalshi", type=Path, help="the history download's kalshi folder")
    parser.add_argument("--live", type=Path, help="the live copies' folder")
    parser.add_argument("--out", type=Path, help="where the results go")
    parser.add_argument("--hours", type=int, help="read only the span's first N hours")
    return parser


def _fit(args: argparse.Namespace) -> int:
    kalshi, live = need(args.kalshi, "--kalshi"), need(args.live, "--live")
    out = need(args.out, "--out")
    result = quoter.run(
        live, fee_terms(kalshi), out, hours=args.hours,
        log=lambda line: print(line, file=sys.stderr),
    )  # fmt: skip
    (out / "paper.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    shown = {k: result[k] for k in ("hours_kept", "dropped_days", "fills", "by_rule")}
    print(json.dumps(shown, indent=2, default=str))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _fit(args)
    except ERRORS as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
