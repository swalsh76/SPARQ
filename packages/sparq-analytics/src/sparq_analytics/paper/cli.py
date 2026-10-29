"""Command line for S4, the paper quoter (PLAN §6.4).

    python -m sparq_analytics.paper fit  --kalshi DIR --live DIR --out DIR [--hours N]
    python -m sparq_analytics.paper test --kalshi DIR --live DIR --out DIR --rules FILE
                                         --from DAY --to DAY [--min-days N]

Run it on the analytics node. ``--kalshi`` is the history download's ``kalshi`` folder (the
series' fee terms), ``--live`` the live copies' folder (``live extract``), ``--out`` where the
results go. ``fit`` (S4.1) replays the paper quoter over the live frames and trades (the first
``--hours`` of the span when asked) and writes ``paper-fills.parquet`` and ``paper.json`` (its
figures by rule printed). ``test`` (S4.2) replays the registered rules (``--rules``, refused unless
it is the file the owner approved) over the whole UTC days ``--from`` to ``--to`` (at least
``--min-days`` kept, seven unless asked otherwise), marks the fills to settlement and writes
``paper-test-fills.parquet`` and ``paper-test.json`` (its verdicts printed). Neither sends an order
or makes an API call. A flag the command does not use is refused. Exit codes: 0 fine, 1 bad command
line or data.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from sparq_analytics.paper import quoter, trial
from sparq_analytics.survey.cli import ERRORS, UsageError, check, need
from sparq_analytics.survey.queue import fee_terms

APPLIES: dict[str, tuple[str, ...]] = {
    "hours": ("fit",),
    "rules": ("test",),
    "from": ("test",),
    "to": ("test",),
    "min-days": ("test",),
}
NAMES = {"from": "from_day", "to": "to_day", "min-days": "min_days"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sparq_analytics.paper", description=__doc__.split("\n")[0]
    )
    parser.add_argument("command", choices=("fit", "test"))
    parser.add_argument("--kalshi", type=Path, help="the history download's kalshi folder")
    parser.add_argument("--live", type=Path, help="the live copies' folder")
    parser.add_argument("--out", type=Path, help="where the results go")
    parser.add_argument("--hours", type=int, help="fit: read only the span's first N hours")
    parser.add_argument("--rules", type=Path, help="test: the registered rules file")
    parser.add_argument("--from", dest="from_day", type=date.fromisoformat, help="test: first day")
    parser.add_argument("--to", dest="to_day", type=date.fromisoformat, help="test: last day")
    parser.add_argument(
        "--min-days", dest="min_days", type=int, help=f"test: days kept at least ({trial.MIN_DAYS})"
    )
    return parser


def _write(out: Path, name: str, data: dict[str, Any]) -> None:
    (out / name).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _fit(args: argparse.Namespace) -> int:
    kalshi, live = need(args.kalshi, "--kalshi"), need(args.live, "--live")
    out = need(args.out, "--out")
    result = quoter.run(
        live, fee_terms(kalshi), out, hours=args.hours,
        log=lambda line: print(line, file=sys.stderr),
    )  # fmt: skip
    _write(out, "paper.json", result)
    shown = {k: result[k] for k in ("hours_kept", "dropped_days", "fills", "by_rule")}
    print(json.dumps(shown, indent=2, default=str))
    return 0


def _test(args: argparse.Namespace) -> int:
    kalshi, live = need(args.kalshi, "--kalshi"), need(args.live, "--live")
    out, rules = need(args.out, "--out"), need(args.rules, "--rules")
    if args.from_day is None or args.to_day is None:
        raise UsageError("--from and --to are required")
    result = trial.run_test(
        live, fee_terms(kalshi), out, rules, args.from_day, args.to_day,
        min_days=trial.MIN_DAYS if args.min_days is None else args.min_days,
        log=lambda line: print(line, file=sys.stderr),
    )  # fmt: skip
    _write(out, "paper-test.json", result)
    print(json.dumps(result, indent=2, default=str))
    return 0


HANDLERS = {"fit": _fit, "test": _test}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        check(args, APPLIES, NAMES)
        return HANDLERS[args.command](args)
    except ERRORS as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
