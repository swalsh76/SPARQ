"""Opt-in: the real feed, all markets, into a test database through the real writer.

    $env:KALSHI_INTEGRATION = '1'            # and KALSHI_KEY_ID, KALSHI_PRIVATE_KEY_PATH
    $env:SPARQ_TEST_DATABASE_URL = '...'      # a test database (the guard refuses any other)
    pytest packages/sparq-server/tests/test_live_ingest.py -q

Read-only toward Kalshi (a subscription to the public ticker, trade and lifecycle channels of every
market); it writes only into the test database, whose tables it empties first. It asks what a fake
cannot: the real volume, how long a flush takes, how far behind the exchange we run, whether any
real frame is rejected, and how small a row becomes once TimescaleDB compresses its chunk.

A summary of counts and sizes (never account values) goes to ``SPARQ_LIVE_SUMMARY``.
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from dbhelpers import dsn
from sparq_core.auth import AuthError, DataSigner
from sparq_core.config import WS_URLS, Environment
from sparq_core.ws import Data, FeedConfig, KalshiFeed, Reconnected, SequenceGap
from sparq_server.ingest.writer import IngestWriter, WriterConfig

pytestmark = [pytest.mark.integration, pytest.mark.db]

SECONDS = float(os.environ.get("SPARQ_LIVE_INGEST_SECONDS", "120"))
LINES: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def summary() -> Any:
    yield
    target = os.environ.get("SPARQ_LIVE_SUMMARY")
    if target:
        Path(target).write_text("\n".join(LINES) + "\n", encoding="utf-8")


async def scalar(url: str, statement: str) -> Any:
    conn = await asyncpg.connect(dsn(url))
    try:
        return await conn.fetchval(statement)
    finally:
        await conn.close()


async def test_every_market_through_the_feed_and_the_writer_into_the_database(
    migrated: str,
) -> None:
    if os.environ.get("KALSHI_INTEGRATION") != "1":
        pytest.skip("live test: set KALSHI_INTEGRATION=1 to run")
    try:
        signer = DataSigner.from_environment()
    except AuthError:
        pytest.skip("no usable data key in KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH")

    async def connect() -> asyncpg.Connection:
        return await asyncpg.connect(dsn(migrated))

    reconnects = gaps = 0
    frames = 0
    started = time.monotonic()
    async with (
        KalshiFeed(signer, FeedConfig(url=WS_URLS[Environment.PRODUCTION])) as feed,
        IngestWriter(connect, WriterConfig()) as writer,
    ):
        await feed.subscribe("ticker")
        await feed.subscribe("trade")
        await feed.subscribe("market_lifecycle_v2")
        events = feed.events()
        deadline = started + SECONDS
        while time.monotonic() < deadline:
            try:
                event = await asyncio.wait_for(anext(events), timeout=deadline - time.monotonic())
            except TimeoutError:
                break
            if isinstance(event, Data):
                frames += 1
                await writer.add(event)
            elif isinstance(event, Reconnected):
                reconnects += 1
            elif isinstance(event, SequenceGap):
                gaps += 1
        await writer.flush()
        wrote = writer.stats()
        fed = feed.stats()
    elapsed = time.monotonic() - started
    written = wrote["written"]
    total = sum(written.values())
    say = LINES.append
    say(f"{elapsed:.0f} s, {frames} frames ({frames / elapsed:.0f} a second) read")
    say(
        f"rows written: {written} ({total / elapsed:.0f} a second); "
        f"duplicates {wrote['trade_duplicates']}"
    )
    say(
        f"flush ms {wrote['flush_ms']}; batch rows {wrote['batch_rows']}; "
        f"flushes {wrote['flushes']}"
    )
    say(f"lag behind the exchange, ms: {wrote['lag_ms']}")
    say(f"rejected {wrote['rejected']}; skipped {wrote['skipped']}")
    say(f"doubtful exchange times {wrote['doubtful_times']}")
    say(
        f"writer buffer high water {wrote['pending_high_water']} of {wrote['pending_limit']}, "
        f"waits for room {wrote['waits_for_room']}, retries {wrote['retries']}"
    )
    say(
        f"feed: queue high water {fed['high_water']} of {fed['queue_limit']}, overflows "
        f"{fed['overflows']}, stalls {fed['stalls']}, reconnects {reconnects}, gaps {gaps}"
    )
    say(
        f"unknown fields {fed['unknown_fields_by_type']}; "
        f"parse errors {fed['parse_errors_by_type']}"
    )

    sizes = {
        "ticker": "SELECT hypertable_size('ticker')",
        "trade": "SELECT hypertable_size('trade')",
        "market_lifecycle": "SELECT pg_total_relation_size('market_lifecycle')",
    }
    for table, size_sql in sizes.items():
        rows = await scalar(migrated, f"SELECT count(*) FROM {table}")  # noqa: S608
        size = await scalar(migrated, size_sql)
        say(
            f"{table}: {rows} rows, {size} bytes, "
            f"{size / max(rows, 1):.0f} bytes a row uncompressed"
        )
    for table in ("ticker", "trade"):
        conn = await asyncpg.connect(dsn(migrated))
        try:
            await conn.execute(
                f"SELECT compress_chunk(c, if_not_compressed => true) FROM show_chunks('{table}') c"  # noqa: S608
            )
            row = await conn.fetchrow(
                "SELECT before_compression_total_bytes AS b, after_compression_total_bytes AS a "
                f"FROM hypertable_compression_stats('{table}')"
            )
            rows = await conn.fetchval(f"SELECT count(*) FROM {table}")  # noqa: S608
        finally:
            await conn.close()
        if row and rows:
            say(f"{table} compressed: {row['b']} -> {row['a']} bytes, {row['a'] / rows:.1f} a row")
    per_day = 86400 / elapsed / 1e6
    say(
        f"a day at this rate: {total * per_day:.0f} million rows "
        f"({written['ticker'] * per_day:.0f} million ticker)"
    )

    assert written["ticker"] > 0 and written["trade"] > 0, "nothing arrived"
    assert wrote["rejects_lost"] == 0
    assert fed["overflows"] == 0, "the writer could not keep up with the feed"
    assert not wrote["rejected"], f"real frames were rejected: {wrote['rejected']}"
