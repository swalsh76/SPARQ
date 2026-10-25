"""The pump: the feed's events in, rows written and gaps recorded.

It reads the feed in order. A frame goes to the writer. Everything else the feed reports becomes a
row in the gap log at once, with the work to make it good:

* a **reconnect** or a **restart** loses a window of every channel: a ``trades`` gap (the backfill
  fetches it), a ``ticker`` gap (marked ``unrecoverable``: ticker data cannot be replayed) and a
  ``lifecycle`` gap (a discovery cycle that begins afterwards makes it good);
* a **sequence gap** on the trade channel is a ``trades`` gap and on the lifecycle channel a
  ``lifecycle`` gap, for the window since the last frame of that subscription.

The window starts at the last frame actually received, not at the moment the loss was noticed (a
stalled link is noticed a minute late). The time of the last frame is also saved every few seconds
as a heartbeat, so that after a crash or a restart the window is known to the second. Gaps that a
previous run left open or half done are run again.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

import asyncpg
from sparq_core.ws import Data, FeedEvent, Reconnected, ResubscribeFailed, SequenceGap

from sparq_server.ingest import gaps, state
from sparq_server.ingest.backfill import Backfiller
from sparq_server.ingest.discovery import DiscoveryRunner
from sparq_server.ingest.writer import RETRYABLE, IngestWriter

log = logging.getLogger("sparq_server")
T = TypeVar("T")
TRADE_CHANNEL = "trade"
LIFECYCLE_CHANNEL = "market_lifecycle_v2"


class Feed(Protocol):
    def events(self) -> Any: ...
    def channel_of(self, sid: int) -> str | None: ...


class EventPump:
    def __init__(
        self,
        feed: Feed,
        writer: IngestWriter,
        connect: Callable[[], Awaitable[asyncpg.Connection]],
        backfiller: Backfiller,
        discovery: DiscoveryRunner,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        heartbeat_every: float = 10.0,
        # a first full cycle takes about 14 minutes, and a restart starts it over
        lifecycle_wait: float = 3600.0,
        retry_every: float = 2.0,
    ) -> None:
        self._feed = feed
        self._writer = writer
        self._connect = connect
        self._backfiller = backfiller
        self._discovery = discovery
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._heartbeat_every = heartbeat_every
        self._lifecycle_wait = lifecycle_wait
        self._retry_every = retry_every
        self._conn: asyncpg.Connection | None = None
        self._db_lock = asyncio.Lock()  # one connection, one operation at a time
        self._last_frame: datetime | None = None
        self._last_by_sid: dict[int, datetime] = {}
        self._last_heartbeat = float(
            "-inf"
        )  # the first frame always saves one, however young the clock
        self._tasks: set[asyncio.Task[None]] = set()
        self.frames = 0
        self.gaps_opened: dict[str, int] = {}
        self.resubscribe_failures = 0

    # --- running -----------------------------------------------------------------------------

    async def run(self) -> None:
        """Recover from the last run, then pump events until the feed ends."""
        await self.recover_startup()
        try:
            async for event in self._feed.events():
                await self.handle(event)
        finally:
            await self.close()

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._last_frame is not None:
            with contextlib.suppress(Exception):
                await self._save_heartbeat()
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()
            self._conn = None

    async def recover_startup(self) -> None:
        """Resume gaps left unfinished, and record the window since the last run stopped."""
        await self._backfiller.requeue_unfinished()
        for gap in await self._db(gaps.unfinished):
            if gap.kind == "lifecycle":
                self._spawn_lifecycle_recovery(gap.id)
        beat = await self._db(lambda c: state.get_int(c, state.HEARTBEAT))
        if beat is not None:
            started = datetime.fromtimestamp(beat / 1000, UTC)
            await self._open_gaps(started, self._clock(), "restart", None)

    async def handle(self, event: FeedEvent) -> None:
        if isinstance(event, Data):
            await self._writer.add(event)
            self.frames += 1
            seen = datetime.fromtimestamp(event.received_ns / 1e9, UTC)
            self._last_frame = seen
            if event.frame.sid is not None:
                self._last_by_sid[event.frame.sid] = seen
            if self._monotonic() - self._last_heartbeat >= self._heartbeat_every:
                await self._save_heartbeat()
        elif isinstance(event, Reconnected):
            self._last_by_sid.clear()  # sids mean nothing on the new link
            start = self._last_frame or self._clock()
            await self._open_gaps(start, self._clock(), event.reason, event.generation)
        elif isinstance(event, SequenceGap):
            await self._on_sequence_gap(event)
        elif isinstance(event, ResubscribeFailed):
            self.resubscribe_failures += 1
            log.error("could not resubscribe to %s: %s", event.channel, event.error)
            await self._db(
                lambda c: gaps.open_gap(
                    c,
                    "all",
                    self._clock(),
                    None,
                    f"could not resubscribe to {event.channel}",
                    generation=event.generation,
                    status=gaps.FAILED,
                    note=event.error,
                )  # fmt: skip
            )

    # --- gaps --------------------------------------------------------------------------------

    async def _on_sequence_gap(self, event: SequenceGap) -> None:
        channel = self._feed.channel_of(event.sid)
        start = self._last_by_sid.get(event.sid) or self._last_frame or self._clock()
        end = self._clock()
        reason = f"sequence gap: expected {event.expected}, got {event.got}"
        if channel == TRADE_CHANNEL:
            await self._open_trades_gap(start, end, reason, event.generation)
        elif channel == LIFECYCLE_CHANNEL:
            await self._open_lifecycle_gap(start, end, reason, event.generation)
        else:
            log.warning("sequence gap on an unexpected channel %r (sid %d)", channel, event.sid)

    async def _open_gaps(
        self, start: datetime, end: datetime, reason: str, generation: int | None
    ) -> None:
        await self._open_trades_gap(start, end, reason, generation)
        await self._db(
            lambda c: gaps.open_gap(
                c,
                "ticker",
                start,
                end,
                reason,
                generation=generation,
                status=gaps.UNRECOVERABLE,
                note="ticker data cannot be replayed",
            )  # fmt: skip
        )
        self.gaps_opened["ticker"] = self.gaps_opened.get("ticker", 0) + 1
        await self._open_lifecycle_gap(start, end, reason, generation)

    async def _open_trades_gap(
        self, start: datetime, end: datetime, reason: str, generation: int | None
    ) -> None:
        gap_id = await self._db(
            lambda c: gaps.open_gap(c, "trades", start, end, reason, generation=generation)
        )
        self.gaps_opened["trades"] = self.gaps_opened.get("trades", 0) + 1
        self._backfiller.submit(gap_id)

    async def _open_lifecycle_gap(
        self, start: datetime, end: datetime, reason: str, generation: int | None
    ) -> None:
        gap_id = await self._db(
            lambda c: gaps.open_gap(c, "lifecycle", start, end, reason, generation=generation)
        )
        self.gaps_opened["lifecycle"] = self.gaps_opened.get("lifecycle", 0) + 1
        self._spawn_lifecycle_recovery(gap_id)

    def _spawn_lifecycle_recovery(self, gap_id: int) -> None:
        """A discovery cycle that begins now makes the lifecycle gap good; wait for it apart from
        the event loop, so that reading the feed never waits on a REST listing."""
        request = self._discovery.request()
        task = asyncio.create_task(self._lifecycle_made_good(gap_id, request))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _lifecycle_made_good(self, gap_id: int, request: "asyncio.Future[None]") -> None:
        try:
            await asyncio.wait_for(request, self._lifecycle_wait)
            status, note = gaps.DONE, "recovered by a discovery cycle"
        except TimeoutError:
            status, note = gaps.FAILED, "no discovery cycle succeeded in time"
        await self._db(lambda c: gaps.finish(c, gap_id, status, note=note))

    # --- the database ------------------------------------------------------------------------

    async def _save_heartbeat(self) -> None:
        if self._last_frame is None:
            return
        millis = int(self._last_frame.timestamp() * 1000)
        await self._db(lambda c: state.put(c, state.HEARTBEAT, millis))
        self._last_heartbeat = self._monotonic()

    async def _db(self, work: Callable[[asyncpg.Connection], Awaitable[T]]) -> T:
        """Run ``work`` on the pump's connection, replacing it and waiting if the database is
        away: a gap that cannot be recorded must not be lost, so this retries until it can.

        A connection runs one operation at a time, and the main loop and the recovery tasks both
        use this one, so calls are serialised.
        """
        async with self._db_lock:
            while True:
                try:
                    if self._conn is None or self._conn.is_closed():
                        self._conn = await self._connect()
                    return await work(self._conn)
                except RETRYABLE as exc:
                    log.warning(
                        "the pump's database call failed (%s); retrying", type(exc).__name__
                    )
                    if self._conn is not None:
                        with contextlib.suppress(Exception):
                            await self._conn.close(timeout=2)
                        self._conn = None
                    await self._sleep(self._retry_every)

    def stats(self) -> dict[str, Any]:
        return {
            "frames": self.frames,
            "gaps_opened": dict(self.gaps_opened),
            "resubscribe_failures": self.resubscribe_failures,
            "last_frame": None if self._last_frame is None else self._last_frame.isoformat(),
        }
