"""Command line for S6, the order-book recorder (PLAN §6.6).

    python -m sparq_analytics.books probe --live DIR --out DIR --production
                                          [--minutes 60] [--reconnect-at 30] [--sample-minutes 10]

Run it on the analytics node. ``probe`` (S6.1) chooses B2's markets from the live copies in
``--live`` (made by ``python -m sparq_analytics.live extract``), subscribes Kalshi's order-book and
trade channels for them with the read-only key (``SPARQ_KALSHI_KEY_ID`` and
``SPARQ_KALSHI_PRIVATE_KEY_PATH``, loaded into this process by its run script), listens for
``--minutes``, drops the link once at ``--reconnect-at`` minutes, and writes ``probe.json`` and
the first ``--sample-minutes`` of messages as ``sample.parquet`` in ``--out``. It needs
``--production``: the demo site has no thin tail to measure. Subscriptions only, never an order.
Exit codes: 0 fine, 1 bad command line, settings, data or feed.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sparq_core.auth import AuthError, DataSigner
from sparq_core.config import Environment, KalshiConfig
from sparq_core.ws import FeedConfig, FeedError, KalshiFeed

from sparq_analytics.books import probe
from sparq_analytics.live.source import LiveSourceError
from sparq_analytics.scratch import ready
from sparq_analytics.survey.cli import UsageError, need


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sparq_analytics.books", description=__doc__.split("\n")[0]
    )
    parser.add_argument("command", choices=("probe",))
    parser.add_argument("--live", type=Path, help="the live copies (live extract's --data)")
    parser.add_argument("--out", type=Path, help="where probe.json and the sample go")
    parser.add_argument("--minutes", type=float, default=60.0, help="how long to listen")
    parser.add_argument(
        "--reconnect-at", type=float, default=30.0, help="minutes in, drop the link once"
    )
    parser.add_argument("--sample-minutes", type=float, default=10.0, help="minutes kept")
    parser.add_argument("--production", action="store_true", help="talk to Kalshi production")
    return parser


def make_feed(signer: DataSigner) -> KalshiFeed:
    """The production feed (tests put a fake in its place)."""
    url = KalshiConfig(environment=Environment.PRODUCTION).ws_url
    return KalshiFeed(signer, FeedConfig(url=url))


async def _probe(args: argparse.Namespace) -> int:
    live, out = need(args.live, "--live"), need(args.out, "--out")
    if not args.production:
        raise UsageError(
            "the probe talks to Kalshi production (subscriptions only): add --production"
        )
    if not 0 < args.reconnect_at < args.minutes or args.sample_minutes < 0:
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
            seconds=args.minutes * 60,
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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not ready():  # the scratch settings, before any data is read
        return 1
    try:
        return asyncio.run(_probe(args))
    except (UsageError, LiveSourceError, AuthError, FeedError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
