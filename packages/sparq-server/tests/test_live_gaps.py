"""Opt-in: the whole ingest against the real Kalshi, into a test database.

    $env:KALSHI_INTEGRATION = '1'            # and KALSHI_KEY_ID, KALSHI_PRIVATE_KEY_PATH
    $env:SPARQ_TEST_DATABASE_URL = '...'      # a test database (the guard refuses any other)
    pytest packages/sparq-server/tests/test_live_gaps.py -q

Read-only toward Kalshi: GET requests, and a short subscription to the public trade and lifecycle
channels of five busy markets. It writes only into the test database, whose tables it empties.

* **The drop test** is the one a fake cannot stand in for. It reads the live trades of five markets
  for a while, cuts the link, lets the gap log and the backfill repair the hole, and then compares
  what was stored with what Kalshi's REST API says happened in the same window: no trade missing,
  none stored twice.
* **Discovery** runs one full and one incremental cycle against the real listings.
* **The exchange poller** reads the real status and schedule once.

A summary of counts goes to the file named by ``SPARQ_LIVE_SUMMARY``.
"""

import asyncio
import os
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from dbhelpers import dsn
from sparq_core.auth import AuthError, DataSigner
from sparq_core.config import WS_URLS, Environment, KalshiConfig
from sparq_core.endpoints import MarketData
from sparq_core.rest import KalshiPublicClient
from sparq_core.ws import FeedConfig, KalshiFeed
from sparq_server.ingest.backfill import Backfiller
from sparq_server.ingest.discovery import Discovery, DiscoveryConfig
from sparq_server.ingest.exchange import ExchangePoller
from sparq_server.ingest.pump import EventPump
from sparq_server.ingest.writer import IngestWriter, WriterConfig

pytestmark = [pytest.mark.integration, pytest.mark.db]

BASE = "https://external-api.kalshi.com/trade-api/v2"
LINES: list[str] = []
say = LINES.append
PRODUCTION = KalshiConfig(
    environment=Environment.PRODUCTION, rate_limit_per_second=2.0, burst_requests=2.0
)


@pytest.fixture(scope="module", autouse=True)
def summary() -> Any:
    yield
    target = os.environ.get("SPARQ_LIVE_SUMMARY")
    if target:
        Path(target).write_text("\n".join(LINES) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def opted_in() -> None:
    if os.environ.get("KALSHI_INTEGRATION") != "1":
        pytest.skip("live test: set KALSHI_INTEGRATION=1 to run")


def connector(url: str) -> Any:
    async def connect() -> asyncpg.Connection:
        return await asyncpg.connect(dsn(url))

    return connect


async def fetch(url: str, statement: str, *args: Any) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn(url))
    try:
        return list(await conn.fetch(statement, *args))
    finally:
        await conn.close()


def busiest(count: int) -> list[str]:
    trades = httpx.get(f"{BASE}/markets/trades", params={"limit": 1000}, timeout=30).json()[
        "trades"
    ]
    counts = Counter(t["ticker"] for t in trades if "KXMVE" not in t["ticker"])
    return [name for name, _ in counts.most_common(count)]


class Immediately:
    """Stands in for the discovery runner here: the drop test is about trades."""

    def request(self) -> "asyncio.Future[None]":
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        future.set_result(None)
        return future


async def test_a_dropped_link_is_repaired_and_the_stored_trades_match_rest(migrated: str) -> None:
    try:
        signer = DataSigner.from_environment()
    except AuthError:
        pytest.skip("no usable data key in KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH")
    tickers = busiest(5)
    started = time.time()
    async with KalshiPublicClient(PRODUCTION) as client:
        data = MarketData(client)
        # a slow reconnect (5 to 10 s) so that the stream really misses trades
        feed = KalshiFeed(
            signer,
            FeedConfig(
                url=WS_URLS[Environment.PRODUCTION], reconnect_base=10.0, reconnect_max=10.0
            ),
        )
        async with IngestWriter(connector(migrated), WriterConfig()) as writer:
            backfiller = Backfiller(data, writer, connector(migrated))
            pump = EventPump(feed, writer, connector(migrated), backfiller, Immediately())  # type: ignore[arg-type]
            await feed.start()
            await feed.subscribe("trade", tickers)
            await feed.subscribe("market_lifecycle_v2")
            worker = asyncio.create_task(backfiller.run())
            runner = asyncio.create_task(pump.run())
            await asyncio.sleep(20)  # live trades arrive
            await feed._conn.close()  # noqa: SLF001 (cut the link as the network would)
            say("link cut after 20 s")
            await asyncio.sleep(
                25
            )  # the feed reconnects, the pump opens gaps, the backfill repairs
            for _ in range(120):  # let the backfill finish
                if backfiller.processed >= 1 and not backfiller._known:  # noqa: SLF001
                    break
                await asyncio.sleep(0.5)
            await feed.close()
            await asyncio.wait_for(runner, 30)
            await writer.flush()
            worker.cancel()
            frames, opened = pump.frames, pump.gaps_opened
        ended = time.time()
        # the truth: every trade in the window, from REST, for the same markets
        low, high = int(started) + 3, int(ended) - 3  # an interior window, clear of both edges
        truth: dict[str, Any] = {}
        for ticker in tickers:
            async for page in data.trades(ticker=ticker, min_ts=low, max_ts=high):
                for trade in page.items:
                    truth[trade.trade_id] = trade
    stored = await fetch(
        migrated,
        "SELECT trade_id, created_time FROM trade WHERE ticker = ANY($1::text[]) "
        "AND created_time >= $2 AND created_time < $3",
        tickers, datetime.fromtimestamp(low, UTC), datetime.fromtimestamp(high, UTC),
    )  # fmt: skip
    have = {r["trade_id"] for r in stored}
    missing, extra = set(truth) - have, have - set(truth)
    twice = (
        await fetch(migrated, "SELECT count(*) - count(DISTINCT (ticker, trade_id)) FROM trade")
    )[0][0]
    log = await fetch(
        migrated,
        "SELECT kind, status, trades_found, trades_added, trades_duplicate, started_at, "
        "ended_at, reason FROM gap_log ORDER BY id",
    )
    say(f"markets {tickers}")
    say(f"frames through the pump {frames}; gaps opened {opened}")
    say(
        f"rest says {len(truth)} trades in the window; stored {len(have)}; "
        f"missing {len(missing)}; extra {len(extra)}"
    )
    say(f"trades stored twice under different times: {twice}")
    for row in log:
        span = (row["ended_at"] - row["started_at"]).total_seconds() if row["ended_at"] else None
        say(
            f"gap {row['kind']:9} {row['status']:13} found {row['trades_found']} "
            f"added {row['trades_added']} dup {row['trades_duplicate']} span {span:.1f} s "
            f"({row['reason']})"
        )

    assert truth, "no trades in the window to compare"
    assert not missing, (
        f"{len(missing)} trades Kalshi reports are not stored, e.g. {sorted(missing)[:3]}"
    )
    assert not extra, f"{len(extra)} stored trades are not in Kalshi's list"
    assert twice == 0
    by_kind = {r["kind"]: r for r in log}
    outage = by_kind["trades"]
    span = (outage["ended_at"] - outage["started_at"]).total_seconds()
    assert span >= 4, f"the link was down only {span:.1f} s; the test would prove little"
    # while the link was down the stream stored nothing, so whatever sits in that stretch for these
    # markets came from the backfill, and it is all there
    inside = await fetch(
        migrated,
        "SELECT count(*) FROM trade WHERE ticker = ANY($1::text[]) AND created_time >= $2 "
        "AND created_time < $3",
        tickers, outage["started_at"], outage["ended_at"],
    )  # fmt: skip
    say(f"trades of these markets stored inside the outage (all from the backfill): {inside[0][0]}")
    assert inside[0][0] > 0
    assert by_kind["trades"]["status"] == "done" and by_kind["ticker"]["status"] == "unrecoverable"
    assert by_kind["lifecycle"]["status"] == "done"


async def test_discovery_reads_the_real_listings_and_the_next_cycle_is_incremental(
    migrated: str,
) -> None:
    async with KalshiPublicClient(PRODUCTION) as client:
        discovery = Discovery(
            MarketData(client),
            connector(migrated),
            DiscoveryConfig(first_run_lookback=timedelta(hours=2)),
        )
        first_at = time.monotonic()
        full = await discovery.cycle()
        full_seconds = time.monotonic() - first_at
        second = await discovery.cycle()
    say(
        f"discovery full: {full.line()} (series {full.series.fetched}, "
        f"fee changes {full.fee_changes.fetched})"
    )
    say(f"discovery incremental: {second.line()}")
    labels = Counter(r["label"] for r in await fetch(migrated, "SELECT label FROM market"))
    say(f"labels {dict(labels)}; wall time of the full cycle {full_seconds:.0f} s")
    assert full.mode == "full" and second.mode == "incremental"
    assert full.markets.fetched > 1_000 and full.series.fetched > 1_000
    assert full.markets.rejected == 0, "real markets were rejected"
    assert (await fetch(migrated, "SELECT count(*) FROM market WHERE ticker LIKE 'KXMVE%'"))[0][
        0
    ] == 0
    discovered = (
        await fetch(
            migrated, "SELECT count(*) FROM market_lifecycle WHERE event_type = 'discovered'"
        )
    )[0][0]
    assert discovered == full.markets.inserted + second.markets.inserted  # both cycles
    assert second.markets.fetched < full.markets.fetched / 10  # only what changed


async def test_the_exchange_poller_reads_the_real_status_and_schedule(migrated: str) -> None:
    async with KalshiPublicClient(PRODUCTION) as client:
        poller = ExchangePoller(MarketData(client), connector(migrated))
        stored = await poller.poll_status()
        schedule = await poller.poll_schedule()
        again = await poller.poll_status()
    say(
        f"exchange: {stored} status rows stored, schedule stored {schedule}, "
        f"second poll stored {again}"
    )
    assert stored >= 1 and schedule is True and again == 0
