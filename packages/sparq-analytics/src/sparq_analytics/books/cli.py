"""Command line for S6, the order-book recorder (PLAN §6.6).

    python -m sparq_analytics.books probe  --live DIR --out DIR --production
                                           [--minutes 60] [--reconnect-at 30] [--sample-minutes 10]
    python -m sparq_analytics.books record --live DIR --out DIR --production [--minutes N]
    python -m sparq_analytics.books status --out DIR
    python -m sparq_analytics.books check  --live DIR --out DIR --start HOUR --end HOUR

Run it on the analytics node. ``probe`` (S6.1) chooses B2's markets from the live copies in
``--live`` (made by ``python -m sparq_analytics.live extract``), subscribes Kalshi's order-book and
trade channels for them with the read-only key (``SPARQ_KALSHI_KEY_ID`` and
``SPARQ_KALSHI_PRIVATE_KEY_PATH``, loaded into this process by its run script), listens for
``--minutes``, drops the link once at ``--reconnect-at`` minutes, and writes ``probe.json`` and
the first ``--sample-minutes`` of messages as ``sample.parquet`` in ``--out``. It needs
``--production``: the demo site has no thin tail to measure. Subscriptions only, never an order.

``record`` (S6.2) records the order-book and trade channels of B2's markets into ``--out`` (see
``store.py``) until stopped (SIGTERM or Ctrl+C) or for ``--minutes``, with the same key and the
copies in ``--live``, which it refreshes itself each day. ``status`` reads the recording folder
alone. ``check`` compares the recorded hours from ``--start`` to ``--end`` (UTC hours such as
``2026-11-02T18``, the end exclusive) with the ingest's ticker frames and trades in ``--live``,
and writes ``check.json`` in ``--out``. Exit codes: 0 fine (``check``: the bar passed), 1 bad
command line, settings, data or feed, or ``check``'s bar missed.
"""

import argparse
import asyncio
import contextlib
import json
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sparq_core.auth import AuthError, DataSigner
from sparq_core.config import Environment, KalshiConfig
from sparq_core.ws import FeedConfig, FeedError, KalshiFeed

from sparq_analytics.books import probe
from sparq_analytics.books.check import check
from sparq_analytics.books.recorder import Recorder, RecorderError, Settings
from sparq_analytics.books.status import status
from sparq_analytics.live.source import LiveSourceError, hour_of
from sparq_analytics.scratch import ready
from sparq_analytics.survey.cli import UsageError, need

# Kalshi's reply to a joined subscribe lists the channel's every market: about 3 MB for 91,623
MAX_FRAME_BYTES = 64 * 1024 * 1024


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sparq_analytics.books", description=__doc__.split("\n")[0]
    )
    parser.add_argument("command", choices=("probe", "record", "status", "check"))
    parser.add_argument("--live", type=Path, help="the live copies (live extract's --data)")
    parser.add_argument("--out", type=Path, help="the probe's or the recording's folder")
    parser.add_argument(
        "--minutes", type=float, help="how long to listen (probe: 60; record: until stopped)"
    )
    parser.add_argument("--start", help="check: the first UTC hour, such as 2026-11-02T18")
    parser.add_argument("--end", help="check: the hour after the last")
    parser.add_argument(
        "--reconnect-at", type=float, default=30.0, help="minutes in, drop the link once"
    )
    parser.add_argument("--sample-minutes", type=float, default=10.0, help="minutes kept")
    parser.add_argument("--production", action="store_true", help="talk to Kalshi production")
    return parser


def make_feed(signer: DataSigner) -> KalshiFeed:
    """The production feed (tests put a fake in its place)."""
    url = KalshiConfig(environment=Environment.PRODUCTION).ws_url
    return KalshiFeed(signer, FeedConfig(url=url, max_frame_bytes=MAX_FRAME_BYTES))


async def _probe(args: argparse.Namespace) -> int:
    live, out = need(args.live, "--live"), need(args.out, "--out")
    if not args.production:
        raise UsageError(
            "the probe talks to Kalshi production (subscriptions only): add --production"
        )
    minutes = 60.0 if args.minutes is None else args.minutes
    if not 0 < args.reconnect_at < minutes or args.sample_minutes < 0:
        raise UsageError("--reconnect-at must lie inside --minutes, and --sample-minutes be >= 0")
    tickers, chosen = probe.choose(live)
    if not tickers:
        raise LiveSourceError(f"no market qualifies at {chosen['at']}")
    print(json.dumps({"chosen": chosen}), file=sys.stderr)
    feed = make_feed(DataSigner.from_environment())
    async with feed:
        result: dict[str, Any] = await probe.run_probe(
            feed,
            tickers,
            seconds=minutes * 60,
            reconnect_after=args.reconnect_at * 60,
            sample_seconds=args.sample_minutes * 60,
            out=out,
        )
        result["feed"] = feed.stats()
    result["chosen"] = chosen
    text = json.dumps(result, indent=2, default=str)
    (out / "probe.json").write_text(text, encoding="utf-8")
    print(text)
    return 0


async def _record(args: argparse.Namespace) -> int:
    live, out = need(args.live, "--live"), need(args.out, "--out")
    if not args.production:
        raise UsageError(
            "the recorder talks to Kalshi production (subscriptions only): add --production"
        )
    if args.minutes is not None and args.minutes <= 0:
        raise UsageError("--minutes must be above zero")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # no signal handlers off POSIX
            loop.add_signal_handler(signum, stop.set)
    feed = make_feed(DataSigner.from_environment())
    async with feed:
        recorder = Recorder(feed, Settings(out=out, live=live))
        seconds = None if args.minutes is None else args.minutes * 60
        result = await recorder.run(stop, seconds)
    text = json.dumps(result, indent=2, default=str)
    (out / "record.json").write_text(text, encoding="utf-8")
    print(text)
    return 0


def _status(args: argparse.Namespace) -> int:
    print(json.dumps(status(need(args.out, "--out")), indent=2, default=str))
    return 0


def _check(args: argparse.Namespace) -> int:
    live, out = need(args.live, "--live"), need(args.out, "--out")
    if args.start is None or args.end is None:
        raise UsageError("check needs --start and --end")
    try:
        start, end = hour_of(args.start), hour_of(args.end)
    except ValueError:
        raise UsageError("--start and --end are UTC hours such as 2026-11-02T18") from None
    if end <= start:
        raise UsageError("--end must come after --start")
    result = check(out, live, start, end)
    text = json.dumps(result, indent=2, default=str)
    (out / "check.json").write_text(text, encoding="utf-8")
    print(text)
    return 0 if result["passes"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not ready():  # the scratch settings, before any data is read
        return 1
    try:
        if args.command == "status":
            return _status(args)
        if args.command == "check":
            return _check(args)
        return asyncio.run(_record(args) if args.command == "record" else _probe(args))
    except (
        UsageError, LiveSourceError, AuthError, FeedError, RecorderError, ValueError, OSError
    ) as error:  # fmt: skip
        print(f"error: {error}", file=sys.stderr)
        return 1
