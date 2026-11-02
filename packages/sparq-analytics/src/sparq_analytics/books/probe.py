"""S6.1: one live hour of Kalshi's order-book channel, measured before any recorder is built
(PLAN §6.6, B2 and B6).

**The markets** (B2, from the live copies): every market open at the end of the newest copied hour,
not a combo, whose latest quote in the day before (the mid, or the one standing price) lies above 3
and below 97 cents, and that traded under 1,000 contracts a day over the seven days before, or over
its days open so far when it is younger (whole days, at least one).

**The probe** subscribes ``orderbook_delta`` and ``trade`` for those markets in batches that grow
(``BATCHES``) until Kalshi refuses one with an error code (its per-subscription limit, B6), then
goes on at the largest size it accepted. It counts every message by type, its bytes as JSON, the
book messages that carry ``clock``, the snapshots and how long each market waited for its first,
the gaps in ``seq``, and the messages in each second; halfway it drops the link on purpose, so the
reconnect, the restored subscriptions, the snapshots sent again and the ``seq`` that follows are
seen. The first minutes' messages are kept as a sample (``sample.parquet``, on the analytics
node's data drive, never committed) for S6.2's parsers and to measure a stored message's size.
Nothing else is kept. Subscriptions only: no order, no account channel.
"""

import asyncio
import json
import math
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import polars as pl
from sparq_core.fixedpoint import E6
from sparq_core.markets import COMBO_PREFIX
from sparq_core.ws import Data, FeedError, FeedEvent, Reconnected, ResubscribeFailed, SequenceGap
from sparq_core.ws_models import ORDERBOOK, TRADE

from sparq_analytics.live.source import HOUR, LiveSourceError, hour_files, last_hour_end
from sparq_analytics.survey.dump import MS_PER_DAY
from sparq_analytics.survey.lag import EXTREME_E6, TIERS, frames_of

CHANNELS = (ORDERBOOK, TRADE)
BATCHES = (1_000, 5_000, 10_000, 20_000, 50_000)  # markets in one subscription, growing
MAX_ATTEMPTS = 40  # subscription attempts per channel before the probe gives up
THIN = TIERS[1][0]  # under 1,000 contracts a day (S3.3's thin tiers)
LOOKBACK_DAYS = 7
QUOTE_HOURS = 24  # how far back a market's latest quote is looked for
SNAPSHOT = "orderbook_snapshot"
BOOK_TYPES = frozenset({SNAPSHOT, ORDERBOOK})


class Feed(Protocol):
    """What the probe needs of a feed (``sparq_core.ws.KalshiFeed`` in a real run)."""

    async def subscribe(self, channel: str, market_tickers: list[str] | None = None) -> int: ...

    async def drop_link(self) -> None: ...

    def events(self) -> Any: ...

    def stats(self) -> dict[str, Any]: ...


# -- the markets ----------------------------------------------------------------------------------


def choose(live: Path) -> tuple[list[str], dict[str, Any]]:
    """B2's markets at the end of the newest copied hour, and the counts of what was left out."""
    end = last_hour_end(live)
    if end is None:
        raise LiveSourceError(f"no hours copied under {live}; run extract")
    end_ms = int(end.timestamp() * 1000)
    quotes = pl.concat(
        [frames_of(p) for p in hour_files(live, "ticker", end - QUOTE_HOURS * HOUR, end)]
    )
    price = pl.coalesce("mid_e6", "yes_bid_e6", "yes_ask_e6")  # the mid, or the one standing
    latest = (
        quotes.with_columns(price_e6=price)
        .filter(pl.col("price_e6").is_not_null())
        .sort("ts_ms")
        .group_by("ticker")
        .agg(pl.col("price_e6").last())
    )
    since = end - timedelta(days=LOOKBACK_DAYS)
    traded = (
        pl.scan_parquet(hour_files(live, "trade", since, end))
        .group_by("ticker")
        .agg(pl.col("count_e2").sum())
        .collect()
    )
    since_ms = int(since.timestamp() * 1000)
    markets = pl.read_parquet(live / "markets.parquet", columns=["ticker", "open_ms", "close_ms"])
    open_now = markets.filter((pl.col("open_ms") <= end_ms) & (pl.col("close_ms") > end_ms))
    combo = pl.col("ticker").str.starts_with(COMBO_PREFIX)
    life_ms = end_ms - pl.max_horizontal("open_ms", pl.lit(since_ms))
    days = (-((-life_ms) // MS_PER_DAY)).clip(lower_bound=1)  # whole days, at least one
    rated = (
        open_now.filter(~combo)
        .join(latest, on="ticker", how="left")
        .join(traded, on="ticker", how="left")
        .with_columns(per_day=pl.col("count_e2").fill_null(0) / 100 / days)
    )
    priced = rated.filter(pl.col("price_e6").is_not_null())
    in_band = priced.filter(
        (pl.col("price_e6") > EXTREME_E6) & (pl.col("price_e6") < E6 - EXTREME_E6)
    )
    chosen = in_band.filter(pl.col("per_day") < THIN)
    counts = {
        "at": end.isoformat(),
        "open": open_now.height,
        "combos_skipped": open_now.filter(combo).height,
        "quoted_in_the_day_before": priced.height,
        "in_the_band": in_band.height,
        "chosen": chosen.height,
    }
    return sorted(chosen["ticker"].to_list()), counts


# -- subscribing ----------------------------------------------------------------------------------


async def subscribe_all(
    feed: Feed, channel: str, tickers: list[str], batches: tuple[int, ...] = BATCHES
) -> dict[str, Any]:
    """Subscribe ``channel`` for every ticker, each subscription larger than the last until one is
    refused with an error code; then on at the largest size accepted (half the refused one when
    none was). A refusal without a code is the link failing, which ends the probe."""
    sizes, step, done, largest = list(batches), 0, 0, 0
    attempts: list[dict[str, Any]] = []
    while done < len(tickers):
        if len(attempts) >= MAX_ATTEMPTS:
            raise FeedError(f"{channel}: still refused after {MAX_ATTEMPTS} attempts")
        chunk = tickers[done : done + sizes[min(step, len(sizes) - 1)]]
        try:
            sid = await feed.subscribe(channel, chunk)
        except FeedError as error:
            if error.code is None:
                raise
            attempts.append({"markets": len(chunk), "refused": str(error), "code": error.code})
            smaller = largest if 0 < largest < len(chunk) else len(chunk) // 2
            if smaller < 1:
                raise
            sizes, step = [smaller], 0
            continue
        attempts.append({"markets": len(chunk), "sid": sid})
        done += len(chunk)
        largest = max(largest, len(chunk))
        step += 1
    refused = [a for a in attempts if "refused" in a]
    return {
        "channel": channel,
        "markets": len(tickers),
        "subscriptions": len(attempts) - len(refused),
        "largest_accepted": largest,
        "refused": refused,
    }


# -- counting -------------------------------------------------------------------------------------


@dataclass
class Tally:
    """Everything the probe counts as the events arrive."""

    started_ns: int
    sample_ns: int
    by_type: Counter[str] = field(default_factory=Counter)
    bytes_by_type: Counter[str] = field(default_factory=Counter)
    book_messages: int = 0
    with_clock: int = 0
    first_snapshot_ns: dict[str, int] = field(default_factory=dict)
    snapshots_after_reconnect: int = 0
    per_second: Counter[int] = field(default_factory=Counter)
    gaps: int = 0
    reconnects: list[dict[str, Any]] = field(default_factory=list)
    resubscribe_failures: list[str] = field(default_factory=list)
    first_seq_after_reconnect: dict[int, int] = field(default_factory=dict)
    sample: list[dict[str, Any]] = field(default_factory=list)

    def add(self, event: FeedEvent) -> None:
        if isinstance(event, Reconnected):
            self.reconnects.append(
                {"generation": event.generation, "reason": event.reason, "downtime": event.downtime}
            )
            return
        if isinstance(event, SequenceGap):
            self.gaps += 1
            return
        if isinstance(event, ResubscribeFailed):
            self.resubscribe_failures.append(f"{event.channel}: {event.error}")
            return
        self._data(event)

    def _data(self, event: Data) -> None:
        frame = event.frame
        text = json.dumps(frame.raw, separators=(",", ":"))
        self.by_type[frame.type] += 1
        self.bytes_by_type[frame.type] += len(text)
        self.per_second[(event.mono_ns - self.started_ns) // 1_000_000_000] += 1
        message = frame.raw.get("msg")
        ticker = message.get("market_ticker") if isinstance(message, dict) else None
        if frame.type in BOOK_TYPES:
            self.book_messages += 1
            self.with_clock += isinstance(message, dict) and "clock" in message
        if frame.type == SNAPSHOT and isinstance(ticker, str):
            if event.generation == 1:
                self.first_snapshot_ns.setdefault(ticker, event.mono_ns)
            else:
                self.snapshots_after_reconnect += 1
        if event.generation > 1 and frame.sid is not None and frame.seq is not None:
            self.first_seq_after_reconnect.setdefault(frame.sid, frame.seq)
        if event.mono_ns < self.sample_ns:
            self.sample.append(
                {
                    "received_ns": event.received_ns,
                    "generation": event.generation,
                    "type": frame.type,
                    "sid": frame.sid,
                    "seq": frame.seq,
                    "market_ticker": ticker,
                    "raw": text,
                }
            )


SAMPLE_SCHEMA = {
    "received_ns": pl.Int64,
    "generation": pl.Int64,
    "type": pl.String,
    "sid": pl.Int64,
    "seq": pl.Int64,
    "market_ticker": pl.String,
    "raw": pl.String,
}


async def consume(feed: Feed, tally: Tally) -> None:
    async for event in feed.events():
        tally.add(event)


# -- the run --------------------------------------------------------------------------------------


async def run_probe(
    feed: Feed,
    tickers: list[str],
    seconds: float,
    reconnect_after: float | None,
    sample_seconds: float,
    out: Path,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """Subscribe, listen for ``seconds`` (dropping the link once after ``reconnect_after``), keep
    the first ``sample_seconds`` of messages in ``out``, and return the measurements."""
    started = monotonic_ns()
    tally = Tally(started_ns=started, sample_ns=started + int(sample_seconds * 1e9))
    consumer = asyncio.create_task(consume(feed, tally))
    try:
        subscriptions = [await subscribe_all(feed, channel, tickers) for channel in CHANNELS]
        subscribed_s = (monotonic_ns() - started) / 1e9

        def left(until: float) -> float:  # every wait is measured from the start
            return max(0.0, until - (monotonic_ns() - started) / 1e9)

        if reconnect_after is not None:
            await sleep(left(reconnect_after))
            await feed.drop_link()
        await sleep(left(seconds))
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
    elapsed_s = (monotonic_ns() - started) / 1e9
    return await asyncio.to_thread(
        finish, tally, tickers, subscriptions, subscribed_s, elapsed_s, out
    )


def finish(
    tally: Tally,
    tickers: list[str],
    subscriptions: list[dict[str, Any]],
    subscribed_s: float,
    elapsed_s: float,
    out: Path,
) -> dict[str, Any]:
    """Write the sample and return the measurements (off the event loop: it touches the disk)."""
    sample = pl.DataFrame(tally.sample, schema=SAMPLE_SCHEMA)
    out.mkdir(parents=True, exist_ok=True)
    sample_path = out / "sample.parquet"
    sample.write_parquet(sample_path, compression="zstd")
    return summarize(tally, tickers, subscriptions, subscribed_s, elapsed_s, sample, sample_path)


def summarize(
    tally: Tally,
    tickers: list[str],
    subscriptions: list[dict[str, Any]],
    subscribed_s: float,
    elapsed_s: float,
    sample: pl.DataFrame,
    sample_path: Path,
) -> dict[str, Any]:
    waits = np.array(
        [(ns - tally.started_ns) / 1e9 for ns in tally.first_snapshot_ns.values()], dtype=float
    )
    seconds = np.zeros(max(1, math.ceil(elapsed_s)))  # a silent second counts as zero
    for second, count in tally.per_second.items():
        seconds[min(max(second, 0), seconds.size - 1)] += count
    steady = sum(n for kind, n in tally.by_type.items() if kind != SNAPSHOT)
    steady_bytes = sum(b for kind, b in tally.bytes_by_type.items() if kind != SNAPSHOT)
    raw_bytes = int(sample["raw"].str.len_bytes().sum()) if sample.height else 0
    stored_ratio = sample_path.stat().st_size / raw_bytes if raw_bytes else None
    json_a_day = (
        steady_bytes / elapsed_s * 86_400 + tally.bytes_by_type[SNAPSHOT] if elapsed_s else None
    )
    return {
        "markets": len(tickers),
        "subscriptions": subscriptions,
        "subscribed_in_seconds": subscribed_s,
        "seconds": elapsed_s,
        "messages_by_type": dict(tally.by_type),
        "json_bytes_by_type": dict(tally.bytes_by_type),
        "book_messages": tally.book_messages,
        "book_messages_with_clock": tally.with_clock,
        "snapshots": {
            "markets_with_one": len(tally.first_snapshot_ns),
            "markets_without": len(tickers) - len(tally.first_snapshot_ns),
            "wait_seconds": _quantiles(waits),
            "sent_again_after_reconnect": tally.snapshots_after_reconnect,
        },
        "per_second": {
            "mean": float(seconds.mean()),
            "p99": float(np.quantile(seconds, 0.99)),
            "max": float(seconds.max()),
        },
        "steady_messages_a_second": steady / elapsed_s if elapsed_s else None,
        "sequence_gaps": tally.gaps,
        "reconnects": tally.reconnects,
        "resubscribe_failures": tally.resubscribe_failures,
        "first_seq_after_reconnect": sorted(set(tally.first_seq_after_reconnect.values())),
        "sample": {
            "messages": sample.height,
            "json_bytes": raw_bytes,
            "stored_bytes": sample_path.stat().st_size,
        },
        "day": {
            "json_bytes": json_a_day,
            "stored_bytes": json_a_day * stored_ratio
            if json_a_day is not None and stored_ratio is not None
            else None,
        },
    }


def _quantiles(values: np.ndarray[Any, np.dtype[np.float64]]) -> dict[str, float] | None:
    if values.size == 0:
        return None
    p50, p95, top = np.quantile(values, [0.5, 0.95, 1.0])
    return {"p50": float(p50), "p95": float(p95), "max": float(top)}
