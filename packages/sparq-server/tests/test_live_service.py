"""Opt-in: the whole ingest service against the real Kalshi, restarted in the middle.

    $env:KALSHI_INTEGRATION = '1'            # and KALSHI_KEY_ID, KALSHI_PRIVATE_KEY_PATH
    $env:SPARQ_TEST_DATABASE_URL = '...'      # a test database (the guard refuses any other)
    pytest packages/sparq-server/tests/test_live_service.py -q

Read-only toward Kalshi: GET requests and a subscription to the public ticker, trade and lifecycle
channels of every market. It writes only into the test database, whose tables it empties.

The question a fake cannot answer: run as the service runs (``supervise``, everything wired as in
production, every market subscribed), stop it, leave the market alone for a while, start it again,
and is what the database holds what Kalshi says happened, with the outage written down?

* The first run records live frames and completes a discovery cycle.
* The service is stopped (the way ``systemctl stop`` stops it), and nothing is recorded for a while.
* The second run finds the heartbeat, writes the restart as gaps and backfills the trades.
* Kalshi's own trade list for the busiest markets, over a window clear of both edges, is compared
  with what was stored: nothing missing, nothing stored twice.

A summary of counts goes to the file named by ``SPARQ_LIVE_SUMMARY``.
"""

import asyncio
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from dbhelpers import dsn
from sparq_core.auth import AuthError, DataSigner
from sparq_core.config import Environment, KalshiConfig
from sparq_core.endpoints import MarketData
from sparq_core.rest import KalshiPublicClient
from sparq_server.ingest.service import STOPPED_REASON, ServiceConfig, parse_config, supervise

pytestmark = [pytest.mark.integration, pytest.mark.db]

BASE = "https://external-api.kalshi.com/trade-api/v2"
LINES: list[str] = []
say = LINES.append
CONFIG: ServiceConfig = parse_config(
    'environment = "production"\nsocket_directory = "/unused"\nport = 5432\n'
    'database = "sparq"\nrole = "sparq_ingest"\ndata_directory = "/"\ndisk_stop_percent = 99\n'
)
FIRST_RUN, OUTAGE, SECOND_RUN = 75, 20, 75  # seconds


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


def connector(url: str) -> Any:
    async def connect() -> asyncpg.Connection:
        return await asyncpg.connect(dsn(url))

    return connect


async def run_for(url: str, signer: DataSigner, seconds: float) -> int:
    """The service, as ``main`` runs it, for ``seconds`` and then stopped by a stop request."""
    stop = asyncio.Event()
    task = asyncio.create_task(
        supervise(CONFIG, signer, stop, connect=connector(url), guard=_never)
    )
    await asyncio.sleep(seconds)
    stop.set()  # what SIGTERM does
    began = time.monotonic()
    code = await asyncio.wait_for(task, 120)
    took = time.monotonic() - began
    say(f"stopped {took:.1f} s after the request, exit status {code}")
    assert took < 45, f"the stop took {took:.0f} s; the unit allows 90 s"
    return code


async def _never() -> None:
    await asyncio.Event().wait()


async def test_the_service_restarted_in_the_middle_keeps_every_trade_and_writes_the_outage_down(
    migrated: str,
) -> None:
    try:
        signer = DataSigner.from_environment()
    except AuthError:
        pytest.skip("no usable data key in KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH")
    tickers = busiest(5)
    started = time.time()

    first = await run_for(migrated, signer, FIRST_RUN)
    between = (time.time(), time.time() + OUTAGE)
    await asyncio.sleep(OUTAGE)  # nothing runs: this is the outage
    second = await run_for(migrated, signer, SECOND_RUN)
    ended = time.time()

    # the truth: every trade in an interior window, for the same markets, from REST
    low, high = int(started) + 5, int(ended) - 5
    truth: dict[str, Any] = {}
    async with KalshiPublicClient(
        KalshiConfig(
            environment=Environment.PRODUCTION, rate_limit_per_second=2.0, burst_requests=2.0
        )
    ) as client:
        for ticker in tickers:
            async for page in MarketData(client).trades(ticker=ticker, min_ts=low, max_ts=high):
                for trade in page.items:
                    truth[trade.trade_id] = trade
    stored = await fetch(
        migrated,
        "SELECT trade_id FROM trade WHERE ticker = ANY($1::text[]) "
        "AND created_time >= $2 AND created_time < $3",
        tickers, datetime.fromtimestamp(low, UTC), datetime.fromtimestamp(high, UTC),
    )  # fmt: skip
    have = {r["trade_id"] for r in stored}
    missing, extra = set(truth) - have, have - set(truth)
    twice = (
        await fetch(migrated, "SELECT count(*) - count(DISTINCT (ticker, trade_id)) FROM trade")
    )[0][0]
    counts = {
        t: (await fetch(migrated, f"SELECT count(*) FROM {t}"))[0][0]  # noqa: S608 (fixed names)
        for t in ("ticker", "trade", "market", "market_lifecycle")
    }
    gaps = await fetch(
        migrated,
        "SELECT kind, status, reason, started_at, ended_at, trades_found, trades_added "
        "FROM gap_log ORDER BY id",
    )
    reason = await fetch(migrated, "SELECT value FROM ingest_state WHERE key = $1", STOPPED_REASON)
    beat = await fetch(migrated, "SELECT value FROM ingest_state WHERE key = 'feed.heartbeat_ms'")
    say(f"markets {tickers}")
    say(f"rows: {counts}")
    say(
        f"rest says {len(truth)} trades in the window; stored {len(have)}; "
        f"missing {len(missing)}; extra {len(extra)}; stored twice {twice}"
    )
    for row in gaps:
        span = (row["ended_at"] - row["started_at"]).total_seconds() if row["ended_at"] else None
        say(
            f"gap {row['kind']:9} {row['status']:13} {row['reason']:10} "
            f"span {span if span is None else round(span, 1)} s found {row['trades_found']} "
            f"added {row['trades_added']}"
        )
    say(f"last stop: {reason[0]['value'] if reason else None}")

    assert (first, second) == (0, 0), "a requested stop must exit 0"
    assert reason and reason[0]["value"] == "stopped cleanly" and beat
    assert counts["ticker"] > 1_000 and counts["trade"] > 100 and counts["market"] > 1_000
    assert truth, "no trades in the window to compare"
    assert not missing, f"{len(missing)} trades Kalshi reports are not stored"
    assert not extra, f"{len(extra)} stored trades are not in Kalshi's list"
    assert twice == 0
    restart = [g for g in gaps if g["reason"] == "restart"]
    assert {g["kind"] for g in restart} == {"trades", "ticker", "lifecycle"}, [
        (g["kind"], g["reason"]) for g in gaps
    ]
    by_kind = {g["kind"]: g for g in restart}
    assert by_kind["trades"]["status"] == "done" and by_kind["ticker"]["status"] == "unrecoverable"
    outage = (by_kind["trades"]["ended_at"] - by_kind["trades"]["started_at"]).total_seconds()
    assert outage >= OUTAGE * 0.8, f"the restart gap is {outage:.1f} s, the outage was {OUTAGE} s"
    inside = await fetch(
        migrated,
        "SELECT count(*) FROM trade WHERE created_time >= $1 AND created_time < $2",
        datetime.fromtimestamp(between[0] + 2, UTC), datetime.fromtimestamp(between[1] - 2, UTC),
    )  # fmt: skip
    say(f"trades stored inside the outage (all from the backfill): {inside[0][0]}")
    assert inside[0][0] > 0
