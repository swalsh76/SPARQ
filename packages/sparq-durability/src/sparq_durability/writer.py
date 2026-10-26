"""The writer: commits rows as fast as the database allows and tells the ledger about each one.

Every row is ``(id, SHA-256 of the id, filler)``. A commit is one row or a hundred, chosen at
random, so both small and batched flushes are exercised, and a checkpoint runs every few seconds
so the cut can fall during one. The ledger is told only after a commit returns, so the ledger
never claims more than the database acknowledged.
"""

import asyncio
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import asyncpg
from sparq_server.stats import percentile

from sparq_durability.connection import connect
from sparq_durability.ledger import ID_SPACE, first_id

BATCH = 100
REPORT_SECONDS = 5.0
CREATE = (
    "CREATE TABLE IF NOT EXISTS durability_rows "
    "(id bigint PRIMARY KEY, digest bytea NOT NULL, filler text NOT NULL)"
)
INSERT = (
    "INSERT INTO durability_rows SELECT g, sha256(convert_to(g::text, 'UTF8')), repeat('x', 200) "
    "FROM generate_series($1::bigint, $2::bigint) g"
)


class WriterRefused(Exception):
    """The writer will not start, or cannot go on."""


@dataclass
class Stats:
    commits: int = 0
    rows: int = 0
    latencies_ms: list[float] = field(default_factory=list)

    def percentile(self, fraction: float) -> float:
        return percentile(self.latencies_ms, fraction)

    def summary(self) -> str:
        return (
            f"{self.commits} commits, {self.rows} rows; commit latency ms: "
            f"median {self.percentile(0.5):.2f}, 99th {self.percentile(0.99):.2f}, "
            f"max {self.percentile(1.0):.2f}"
        )


async def _checkpoints(url: str, every: float) -> None:
    conn = await connect(url)
    try:
        while True:
            await asyncio.sleep(every)
            await conn.execute("CHECKPOINT")
    finally:
        await conn.close()


async def run_writer(
    url: str,
    run: int,
    ledger_host: str,
    ledger_port: int,
    seconds: float | None = None,
    checkpoint_every: float = 5.0,
    seed: int | None = None,
    say: Callable[[str], None] = print,
) -> Stats:
    """Write until ``seconds`` pass (or forever), and return what was written."""
    rng = random.Random(seed)  # noqa: S311 (a mix of batch sizes, not a secret)
    conn = await connect(url)
    stats = Stats()
    try:
        await conn.execute(CREATE)
        low, high = first_id(run), (run + 1) * ID_SPACE - 1
        used = await conn.fetchval(
            "SELECT count(*) FROM durability_rows WHERE id BETWEEN $1 AND $2", low, high
        )
        if used:
            raise WriterRefused(f"run {run} was already used ({used} rows); use a new run number")
        try:
            _, ledger = await asyncio.open_connection(ledger_host, ledger_port)
        except OSError as error:
            raise WriterRefused(f"the ledger is not reachable: {error}") from error
        ledger.write(f"hello {run}\n".encode())
        await ledger.drain()
        checkpoints = asyncio.create_task(_checkpoints(url, checkpoint_every))
        try:
            await _write_loop(conn, ledger, run, low, seconds, rng, stats, say)
        finally:
            checkpoints.cancel()
            await asyncio.gather(checkpoints, return_exceptions=True)
            ledger.close()
    finally:
        await conn.close()
    return stats


async def _write_loop(
    conn: asyncpg.Connection,
    ledger: asyncio.StreamWriter,
    run: int,
    next_id: int,
    seconds: float | None,
    rng: random.Random,
    stats: Stats,
    say: Callable[[str], None],
) -> None:
    started = reported = time.monotonic()
    while seconds is None or time.monotonic() - started < seconds:
        count = 1 if rng.random() < 0.5 else BATCH
        first, last = next_id, next_id + count - 1
        begun = time.monotonic()
        await conn.execute(INSERT, first, last)
        stats.latencies_ms.append((time.monotonic() - begun) * 1000)
        ledger.write(f"{run} {first} {last}\n".encode())  # only after the commit returned
        if ledger.is_closing():
            raise WriterRefused("the ledger connection closed; acknowledgements cannot be recorded")
        next_id = last + 1
        stats.commits += 1
        stats.rows += count
        if stats.commits % 64 == 0:
            await ledger.drain()
        if time.monotonic() - reported >= REPORT_SECONDS:
            reported = time.monotonic()
            say(f"run {run}: {stats.summary()}")
