"""Command line for the history downloader.

    python -m sparq_analytics.history --production plan
    python -m sparq_analytics.history --production series
    python -m sparq_analytics.history --production markets
    python -m sparq_analytics.history --production trades [--days N]
    python -m sparq_analytics.history --production candles --series KXBTC --sample 500
    python -m sparq_analytics.history status
    python -m sparq_analytics.history verify

Run it from the repository root: the data goes in ``data/kalshi`` (git-ignored) unless
``--data-dir`` or ``SPARQ_DATA_DIR`` says otherwise. Commands that talk to Kalshi need
``--production``, because the demo site has no history; they read public data and send only GETs.
Ctrl+C is safe: progress is saved after every part, and the next run resumes. Output is counts
and file names only. Exit codes: 0 fine, 1 bad command line or error, 2 a check failed or the
run stopped because Kalshi kept rate-limiting it.
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from sparq_core.config import DEFAULT_LIVE_RATE_LIMIT_PER_SECOND, DEFAULT_RATE_LIMIT_PER_SECOND
from sparq_core.rest import KalshiError

from sparq_analytics.history.candles import download_candles
from sparq_analytics.history.datadir import DataDir, State
from sparq_analytics.history.downloader import (
    DownloadConfig,
    Downloader,
    day_segments,
    newest_complete_day,
    status_of,
)
from sparq_analytics.history.rates import RateLimitPersisting
from sparq_analytics.history.verify import verify

NETWORK_COMMANDS = {"plan", "series", "markets", "trades", "candles"}


def _rate(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sparq_analytics.history", description=__doc__.split("\n")[0]
    )
    parser.add_argument(
        "--data-dir", type=Path, default=Path(os.environ.get("SPARQ_DATA_DIR", "data"))
    )
    parser.add_argument(
        "--max-gb", type=float, default=50.0, help="stop starting new days past this size"
    )
    parser.add_argument("--since", type=date.fromisoformat, default=date(2021, 1, 1))
    parser.add_argument(
        "--trades-rate",
        type=float,
        default=_rate("SPARQ_RATE_LIMIT_PER_SECOND", DEFAULT_RATE_LIMIT_PER_SECOND),
    )
    parser.add_argument(
        "--live-rate",
        type=float,
        default=_rate("SPARQ_LIVE_RATE_LIMIT_PER_SECOND", DEFAULT_LIVE_RATE_LIMIT_PER_SECOND),
    )
    parser.add_argument(
        "--production", action="store_true", help="use Kalshi production (needed to download)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="show what is done and what a run would do (one request)")
    sub.add_parser(
        "series", help="the series list with categories and fee terms, and the fee history"
    )
    sub.add_parser("markets", help="every settled non-combo market, historical and live")
    trades = sub.add_parser("trades", help="trades, newest complete day first, until the size cap")
    trades.add_argument("--days", type=int, default=None, help="stop after this many new days")
    candles = sub.add_parser("candles", help="hourly candles for a chosen set of markets")
    candles.add_argument(
        "--series", default=None, help="only markets whose series starts with this"
    )
    candles.add_argument(
        "--sample", type=int, default=None, help="a reproducible random sample of this size"
    )
    sub.add_parser("status", help="what has been downloaded (no network)")
    sub.add_parser("verify", help="integrity checks and the volume reconciliation (no network)")
    return parser


def _config(args: argparse.Namespace) -> DownloadConfig:
    return DownloadConfig(
        data_dir=args.data_dir / "kalshi",
        max_bytes=int(args.max_gb * 1e9),
        since=args.since,
        trades_rate=args.trades_rate,
        live_rate=args.live_rate,
    )


async def _run_network(args: argparse.Namespace) -> int:
    async with Downloader.create(_config(args)) as downloader:
        command = args.command
        if command == "plan":
            trades_cutoff, markets_cutoff = await downloader.cutoffs()
            newest = newest_complete_day(downloader.now(), downloader.config.day_margin_seconds)
            total = (newest - args.since).days + 1
            status = downloader.status()
            print(f"trade cutoff {trades_cutoff}, markets cutoff {markets_cutoff} (Unix seconds)")
            print(f"newest complete day {newest}; days back to {args.since}: {total}")
            segments = [f"{s.side} {s.start}-{s.end}" for s in day_segments(newest, trades_cutoff)]
            print(f"that day is fetched as: {', '.join(segments)}")
            print(
                f"already done: {status['trade_days_done']} trade days, "
                f"{status['trade_rows']:,} rows, "
                f"{status['gigabytes']} GB of {status['cap_gigabytes']:.0f} GB"
            )
            print(f"rates: historical trades {args.trades_rate}/s, others {args.live_rate}/s")
        elif command == "series":
            await downloader.download_series()
        elif command == "markets":
            await downloader.download_historical_markets()
            await downloader.download_live_markets()
        elif command == "trades":
            report = await downloader.download_trades(limit_days=args.days)
            print(
                f"trades: {report.days_done} days downloaded ({report.rows:,} rows, "
                f"{report.combos_skipped:,} combo trades skipped), "
                f"{report.days_skipped} already done; stopped: {report.stopped}"
            )
        else:  # candles: the parser allows no other command here
            fetched = await download_candles(
                downloader, series_prefix=args.series, sample=args.sample
            )
            print(f"candles: {fetched:,} markets fetched")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in NETWORK_COMMANDS and not args.production:
        print(
            "This talks to Kalshi production (public data, GET only). Add --production.",
            file=sys.stderr,
        )
        return 1
    try:
        if args.command == "status":
            downloader_dir = DataDir(args.data_dir / "kalshi")
            state = State(downloader_dir.state_path)
            for key, value in status_of(downloader_dir, state, int(args.max_gb * 1e9)).items():
                print(f"{key}: {value}")
            return 0
        if args.command == "verify":
            directory = DataDir(args.data_dir / "kalshi")
            report = verify(directory, State(directory.state_path))
            print("\n".join(report.lines()))
            return 0 if report.ok else 2
        return asyncio.run(_run_network(args))
    except KeyboardInterrupt:
        print("\nstopped; progress is saved, run the same command again to resume", file=sys.stderr)
        return 130
    except RateLimitPersisting as exc:
        print(
            f"stopped: Kalshi kept rate-limiting the run ({exc}); "
            "try again later or lower the rates",
            file=sys.stderr,
        )
        return 2
    except KalshiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
