"""The writer: frames in, atomic batches out, with a bounded buffer.

``add`` converts a frame (see :mod:`sparq_server.ingest.rows`) and buffers its row. A background
task writes the buffer every ``flush_interval`` seconds, or sooner when it holds ``batch_rows``. One
batch is one transaction, so a write either lands whole or not at all.

* **A lost database connection** is retried with a capped backoff on a fresh connection; the batch
  is kept, so nothing is lost and nothing is written twice (a trade that did land is skipped by the
  unique key).
* **A row the database refuses** (the rows are checked first, so this is rare) does not stop the
  rest: the batch is halved until the one bad row is alone, and that row goes to ``ingest_reject``
  with the reason, where it can be looked at.
* **A stalled database** fills the buffer to ``max_pending`` rows (counting the batch being written)
  and then ``add`` waits. The caller then reads the feed more slowly, the feed's own bounded queue
  fills, and the feed drops the link and reports a gap to backfill. Memory stays bounded all the way
  back to the socket.
* **Trades** go through a staging table with ``ON CONFLICT DO NOTHING``, so a trade that arrives
  twice (live, then backfilled) is stored once and the duplicate is counted.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import asyncpg
from sparq_core.ws import Data

from sparq_server.ingest.rows import (
    LIFECYCLE_COLUMNS,
    TICKER_COLUMNS,
    TRADE_COLUMNS,
    LifecycleRow,
    Reject,
    Skip,
    TickerRow,
    TradeRow,
    convert,
)

log = logging.getLogger("sparq_server")

# a problem with the link or the server's state: wait and try again on a fresh connection
RETRYABLE = (
    OSError,
    TimeoutError,
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    asyncpg.TooManyConnectionsError,
    asyncpg.CannotConnectNowError,
    asyncpg.AdminShutdownError,
    asyncpg.CrashShutdownError,
    asyncpg.DeadlockDetectedError,
    asyncpg.SerializationError,
)
# a problem with the row: no retry will help, so isolate it
ROW_ERRORS = (
    asyncpg.DataError,
    asyncpg.IntegrityConstraintViolationError,
    ValueError,
    TypeError,
    OverflowError,
)
KIND_TABLES = {"ticker": "ticker", "lifecycle": "market_lifecycle"}
REJECT_COLUMNS = ("kind", "reason", "payload")
STAGE = (
    "CREATE TEMP TABLE IF NOT EXISTS trade_stage (LIKE trade INCLUDING DEFAULTS) "
    "ON COMMIT DELETE ROWS"
)
SAMPLES = 20_000


class WriterError(Exception):
    """The writer could not do what was asked (for example, flush before closing)."""


@dataclass(frozen=True, slots=True)
class WriterConfig:
    batch_rows: int = 5_000
    flush_interval: float = 1.0
    max_pending: int = 100_000
    retry_base: float = 0.5
    retry_max: float = 30.0
    close_timeout: float = 60.0

    def __post_init__(self) -> None:
        if self.batch_rows < 1 or self.max_pending < self.batch_rows:
            raise ValueError("batch_rows must be at least 1 and max_pending at least batch_rows")
        if not (self.flush_interval > 0 and self.retry_base > 0 and self.close_timeout > 0):
            raise ValueError("flush_interval, retry_base and close_timeout must be positive")
        if self.retry_max < self.retry_base:
            raise ValueError("retry_max must be at least retry_base")


Item = tuple[str, Any]  # ("ticker" | "trade" | "lifecycle" | "reject", the row)


def scrub(value: Any) -> Any:
    """The value with every NUL replaced: Postgres refuses it in text and in JSON, so a reject
    holding one would be refused too. Works through lists and dictionaries."""
    if isinstance(value, str):
        return value.replace("\x00", "\ufffd")
    if isinstance(value, dict):
        return {scrub(k): scrub(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [scrub(v) for v in value]
    return value


def _record(item: Item) -> dict[str, Any]:
    kind, row = item
    return {"kind": kind, "row": row._asdict() if hasattr(row, "_asdict") else str(row)}


class IngestWriter:
    def __init__(
        self,
        connect: Callable[[], Awaitable[asyncpg.Connection]],
        config: WriterConfig | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connect = connect
        self._config = config or WriterConfig()
        self._sleep = sleep
        self._monotonic = monotonic
        self._items: list[Item] = []
        self._pending = 0  # buffered rows plus the batch being written
        self._conn: asyncpg.Connection | None = None
        self._wake = asyncio.Event()
        self._room = asyncio.Event()
        self._room.set()
        self._flush_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._counts: Counter[str] = Counter()
        self._skipped: Counter[str] = Counter()
        self._rejected: Counter[str] = Counter()
        self._flush_ms: list[float] = []
        self._batch_sizes: list[int] = []
        self._lag_ms: list[float] = []
        self._high_water = 0

    # --- using the writer ------------------------------------------------------------------

    async def __aenter__(self) -> "IngestWriter":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def add(self, data: Data) -> None:
        """Buffer the row for a frame. Waits (and so slows the caller) while the buffer is full."""
        converted = convert(data)
        outcome = converted.result
        if isinstance(outcome, Skip):
            self._skipped[outcome.reason] += 1
            return
        if converted.doubtful_time:
            self._counts["doubtful_times"] += 1
        while self._pending >= self._config.max_pending and not self._closing:
            self._counts["waits_for_room"] += 1
            self._room.clear()
            await self._room.wait()
        if isinstance(outcome, Reject):
            self._items.append(("reject", outcome))
        else:
            kind = {TickerRow: "ticker", TradeRow: "trade", LifecycleRow: "lifecycle"}[
                type(outcome)
            ]
            self._items.append((kind, outcome))
        self._pending += 1
        self._high_water = max(self._high_water, self._pending)
        if len(self._items) >= self._config.batch_rows:
            self._wake.set()

    async def flush(self) -> None:
        """Write everything buffered now; retries until it lands (``close`` is the bounded one)."""
        await self._flush_once()

    async def close(self) -> None:
        """Stop, write what is left, and release the connection. Bounded by ``close_timeout``."""
        self._closing = True
        self._room.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        owed = self._pending
        try:
            await asyncio.wait_for(self._flush_once(), self._config.close_timeout)
        except TimeoutError:
            raise WriterError(f"{owed} rows could not be written before closing") from None
        finally:
            if self._conn is not None:
                with contextlib.suppress(Exception):
                    await self._conn.close()
                self._conn = None

    def stats(self) -> dict[str, Any]:
        def percentile(values: list[float] | list[int], fraction: float) -> float:
            if not values:
                return 0.0
            ordered = sorted(values)
            return float(ordered[min(len(ordered) - 1, int(fraction * len(ordered)))])

        return {
            "pending": self._pending,
            "pending_high_water": self._high_water,
            "pending_limit": self._config.max_pending,
            "written": {k: self._counts[f"written_{k}"] for k in ("ticker", "trade", "lifecycle")},
            "trade_duplicates": self._counts["trade_duplicates"],
            "rejected": dict(self._rejected),
            "skipped": dict(self._skipped),
            "doubtful_times": self._counts["doubtful_times"],
            "rejects_lost": self._counts["rejects_lost"],
            "flushes": self._counts["flushes"],
            "retries": self._counts["retries"],
            "reconnects": max(0, self._counts["connections"] - 1),
            "waits_for_room": self._counts["waits_for_room"],
            "flush_ms": {
                "median": percentile(self._flush_ms, 0.5),
                "p99": percentile(self._flush_ms, 0.99),
            },
            "batch_rows": {
                "median": percentile(self._batch_sizes, 0.5),
                "max": max(self._batch_sizes, default=0),
            },
            "lag_ms": {
                "median": percentile(self._lag_ms, 0.5),
                "p99": percentile(self._lag_ms, 0.99),
            },
        }

    # --- the background flusher ------------------------------------------------------------

    async def _run(self) -> None:
        while not self._closing:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._config.flush_interval)
            self._wake.clear()
            await self._flush_once()

    async def _flush_once(self) -> None:
        async with self._flush_lock:
            batch, self._items = self._items, []
            if not batch:
                return
            started = self._monotonic()
            try:
                await self._write_batch(batch)
            finally:
                self._pending -= len(batch)
                self._room.set()
            self._counts["flushes"] += 1
            self._flush_ms.append((self._monotonic() - started) * 1000)
            self._batch_sizes.append(len(batch))
            for samples in (self._flush_ms, self._batch_sizes):
                del samples[:-SAMPLES]

    # --- writing ---------------------------------------------------------------------------

    async def _write_batch(self, batch: list[Item]) -> None:
        """Write the batch whole, retrying a lost connection; halve it to find a refused row."""
        attempt = 0
        while True:
            try:
                await self._attempt(batch)
                return
            except RETRYABLE as exc:
                attempt += 1
                self._counts["retries"] += 1
                await self._drop_connection()
                delay = min(self._config.retry_max, self._config.retry_base * 2.0 ** (attempt - 1))
                log.warning(
                    "ingest write failed (%s); retrying in %.1f s", type(exc).__name__, delay
                )
                await self._sleep(delay)
            except ROW_ERRORS as exc:
                if len(batch) == 1:
                    if batch[0][0] == "reject":  # a reject that cannot be stored: do not recurse
                        self._counts["rejects_lost"] += 1
                        log.error("a rejected row could not itself be stored: %s", exc)
                        return
                    await self._reject_alone(batch[0], exc)
                    return
                middle = len(batch) // 2
                await self._write_batch(batch[:middle])
                await self._write_batch(batch[middle:])
                return

    async def _connection(self) -> asyncpg.Connection:
        if self._conn is None or self._conn.is_closed():
            self._conn = await self._connect()
            self._counts["connections"] += 1
        return self._conn

    async def _drop_connection(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close(timeout=2)
            self._conn = None

    async def _attempt(self, batch: list[Item]) -> None:
        conn = await self._connection()
        by_kind: dict[str, list[Any]] = {}
        for kind, row in batch:
            by_kind.setdefault(kind, []).append(row)
        added = 0
        async with conn.transaction():
            for kind, columns in (("ticker", TICKER_COLUMNS), ("lifecycle", LIFECYCLE_COLUMNS)):
                if kind in by_kind:
                    await conn.copy_records_to_table(
                        KIND_TABLES[kind], records=by_kind[kind], columns=columns
                    )
            if "trade" in by_kind:
                added = await self._stage_trades(conn, by_kind["trade"])
            if "reject" in by_kind:
                records = [
                    (r.kind, scrub(r.reason)[:500], json.dumps(scrub(r.payload), default=str))
                    for r in by_kind["reject"]
                ]
                await conn.copy_records_to_table(
                    "ingest_reject", records=records, columns=REJECT_COLUMNS
                )
        # counted only after the commit: a retried batch is not counted twice
        for kind, rows in by_kind.items():
            if kind == "reject":
                for r in rows:
                    self._rejected[f"{r.kind}: {r.reason[:60]}"] += 1
            elif kind == "trade":
                self._counts["written_trade"] += added
                self._counts["trade_duplicates"] += len(rows) - added
            else:
                self._counts[f"written_{kind}"] += len(rows)
        for kind, rows in by_kind.items():
            if kind in ("ticker", "trade"):
                moments = [(r.received_at - r.ts) if kind == "ticker" else None for r in rows]
                self._lag_ms.extend(m.total_seconds() * 1000 for m in moments if m is not None)
        del self._lag_ms[:-SAMPLES]

    @staticmethod
    async def _stage_trades(conn: asyncpg.Connection, trades: list[Any]) -> int:
        """Insert trades through a staging table; returns how many were new (the rest existed)."""
        await conn.execute(STAGE)
        await conn.copy_records_to_table("trade_stage", records=trades, columns=TRADE_COLUMNS)
        status = await conn.execute(
            "INSERT INTO trade SELECT * FROM trade_stage ON CONFLICT DO NOTHING"
        )
        return int(status.rsplit(" ", 1)[1])

    async def insert_trades(self, trades: list[TradeRow]) -> tuple[int, int]:
        """Write trades now, apart from the stream's buffer; returns (added, already stored).

        For the backfill. The unique key makes it safe to repeat, and the same retry on a lost
        connection applies. A trade the database refuses raises (the caller's gap fails with it).
        """
        if not trades:
            return 0, 0
        attempt = 0
        async with self._flush_lock:
            while True:
                try:
                    conn = await self._connection()
                    async with conn.transaction():
                        added = await self._stage_trades(conn, trades)
                    return added, len(trades) - added
                except RETRYABLE as exc:
                    attempt += 1
                    self._counts["retries"] += 1
                    await self._drop_connection()
                    delay = min(
                        self._config.retry_max, self._config.retry_base * 2.0 ** (attempt - 1)
                    )
                    log.warning(
                        "trade insert failed (%s); retrying in %.1f s", type(exc).__name__, delay
                    )
                    await self._sleep(delay)

    async def _reject_alone(self, item: Item, error: Exception) -> None:
        """A single row the database refuses: keep it, with the reason, in ``ingest_reject``."""
        kind, row = item
        reason = f"the database refused it: {type(error).__name__}: {str(error)[:200]}"
        payload = row.payload if isinstance(row, Reject) else _record(item)
        log.error("ingest row rejected (%s): %s", kind, reason)
        await self._write_batch([("reject", Reject(kind, reason, payload))])
