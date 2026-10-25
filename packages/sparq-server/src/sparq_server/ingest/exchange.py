"""The exchange's state and schedule, recorded when they change.

Kalshi pauses trading for maintenance; a pause is not an outage, and an outage must not be hidden
inside one, so both facts are stored. The status is polled often and a row is written only when a
shard's flags differ from the last row stored for it (and the first time), so the table is a
history of changes, not a log of polls. The schedule is polled rarely and stored when it differs.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import asyncpg
from sparq_core.endpoints import MarketData

log = logging.getLogger("sparq_server")

LATEST_STATUS = (
    "SELECT DISTINCT ON (exchange_index) exchange_index, exchange_active, trading_active "
    "FROM exchange_status ORDER BY exchange_index, observed_at DESC, id DESC"
)


class ExchangePoller:
    def __init__(
        self,
        data: MarketData,
        connect: Callable[[], Awaitable[asyncpg.Connection]],
        *,
        status_every: float = 60.0,
        schedule_every: float = 3600.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._data = data
        self._connect = connect
        self._status_every = status_every
        self._schedule_every = schedule_every
        self._clock = clock
        self._monotonic = monotonic
        self.polls = 0
        self.failures = 0
        self.changes = 0
        self.last_error: str | None = None

    async def poll_status(self) -> int:
        """Store the shards (and the exchange as a whole) whose flags changed; returns the rows."""
        status = await self._data.exchange_status()
        current: dict[int | None, tuple[bool, bool, dict[str, Any]]] = {
            None: (status.exchange_active, status.trading_active, {})
        }
        for shard in status.shards:
            current[shard.exchange_index] = (
                shard.exchange_active,
                shard.trading_active,
                shard.model_dump(mode="json"),
            )
        conn = await self._connect()
        try:
            known = {
                r["exchange_index"]: (r["exchange_active"], r["trading_active"])
                for r in await conn.fetch(LATEST_STATUS)
            }
            at = self._clock()
            new = [
                (at, index, active, trading, json.dumps(raw) if raw else None)
                for index, (active, trading, raw) in current.items()
                if known.get(index) != (active, trading)
            ]
            if new:
                await conn.copy_records_to_table(
                    "exchange_status",
                    records=new,
                    columns=[
                        "observed_at",
                        "exchange_index",
                        "exchange_active",
                        "trading_active",
                        "raw",
                    ],
                )
            return len(new)
        finally:
            await conn.close()

    async def poll_schedule(self) -> bool:
        """Store the published schedule if it differs from the last one stored."""
        schedule = (await self._data.exchange_schedule()).model_dump(mode="json")
        conn = await self._connect()
        try:
            last = await conn.fetchval(
                "SELECT schedule FROM exchange_schedule ORDER BY fetched_at DESC LIMIT 1"
            )
            if last is not None and json.loads(last) == schedule:
                return False
            await conn.execute(
                "INSERT INTO exchange_schedule (fetched_at, schedule) VALUES ($1, $2::jsonb)",
                self._clock(),
                json.dumps(schedule),
            )
            return True
        finally:
            await conn.close()

    async def run(self) -> None:
        """Poll for as long as the process lives; a failed poll is logged and tried again."""
        next_schedule = 0.0
        while True:
            try:
                self.changes += await self.poll_status()
                if self._monotonic() >= next_schedule:
                    self.changes += int(await self.poll_schedule())
                    next_schedule = self._monotonic() + self._schedule_every
                self.polls += 1
                self.last_error = None
            except Exception as exc:  # a failed poll must never stop the loop
                self.failures += 1
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("exchange poll failed: %s", self.last_error)
            await asyncio.sleep(self._status_every)

    def stats(self) -> dict[str, Any]:
        return {
            "polls": self.polls,
            "failures": self.failures,
            "changes_stored": self.changes,
            "last_error": self.last_error,
        }
