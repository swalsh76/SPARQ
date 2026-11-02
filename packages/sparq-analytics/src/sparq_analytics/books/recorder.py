"""S6.2: the order-book recorder (PLAN §6.6, B1 to B4).

It records Kalshi's order-book and trade channels for B2's markets, chosen from the live copies
(S6.1's choice), into the store (``store.py``): one row a book event and a row a trade, in arrival
order, hourly. Subscriptions only, with the read-only key; never an order.

**Keeping the books whole.** Kalshi keeps one subscription a channel and link and joins every
market to it, so its ``seq`` covers every market at once:

* a ``seq`` jump on the book channel cannot say which market lost a change, so every book is
  marked unknown (a ``RESET`` row) and every market's snapshot is asked for again;
* a lost link marks every book unknown; Kalshi sends every snapshot again as the subscriptions
  are restored (measured in S6.1);
* a change that would leave a size below zero marks that market unknown and asks for its snapshot
  (at most once a minute a market);
* at the start of every hour every market's snapshot is asked for, so each hour's file can be
  read alone (owner, 2026-11-02).

**The markets.** B2's list at the start, from the copies as they are; again just after 00:00 UTC
each day, once the recorder has refreshed the copies itself (``live extract`` in a child process,
bounded in time; when it fails, yesterday's list stays and the failure is logged), and at once
when the copies were already stale at the start. A market that opens during the day joins from
the market-lifecycle channel, in batches each minute, and stays through the day's choice until it
has been open a day (B2 reads a day of quotes); a market leaves when it is determined.

Every wait is bounded and nothing prompts. A refused resubscription after a lost link stops the
recorder (its daemon, S6.3, starts it again); everything heard until then is written first.
"""

import asyncio
import contextlib
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sparq_core.markets import COMBO_PREFIX
from sparq_core.ws import Data, FeedError, FeedEvent, Reconnected, ResubscribeFailed, SequenceGap
from sparq_core.ws_models import (
    MARKET_LIFECYCLE,
    ORDERBOOK,
    SNAPSHOT,
    TRADE,
    MarketLifecycleMsg,
    OrderbookDeltaMsg,
    OrderbookSnapshotMsg,
    TradeMsg,
)

from sparq_analytics.books import store
from sparq_analytics.books.keeper import Keeper
from sparq_analytics.books.probe import choose, subscribe_all
from sparq_analytics.books.store import CHANGE, LEVEL, RESET, SIDE_CODE
from sparq_analytics.live.source import LiveSourceError, hour_name, last_hour_end

CHANNEL_OF_TYPE = {SNAPSHOT: ORDERBOOK, ORDERBOOK: ORDERBOOK, TRADE: TRADE}
REMOVED_WHEN = frozenset({"determined", "settled"})
CHANGE_NAME = {"add_markets": "add", "delete_markets": "remove"}


class Feed(Protocol):
    """What the recorder needs of a feed (``sparq_core.ws.KalshiFeed`` in a real run)."""

    async def subscribe(self, channel: str, market_tickers: list[str] | None = None) -> int: ...

    async def update_subscription(
        self, sid: int, action: str, market_tickers: list[str]
    ) -> list[str]: ...

    async def request_snapshot(self, sid: int, market_tickers: list[str]) -> None: ...

    def channel_of(self, sid: int) -> str | None: ...

    def events(self) -> Any: ...

    def stats(self) -> dict[str, Any]: ...


class RecorderError(Exception):
    """The recording cannot go on as it should (a refused resubscription, a failed write)."""


@dataclass(frozen=True, slots=True)
class Settings:
    out: Path  # the recording folder
    live: Path  # the live copies the markets are chosen from
    flush_seconds: float = 60.0  # how often the buffered rows become a part file
    snapshot_batch: int = 10_000  # markets in one get_snapshot or one change of the list
    change_seconds: float = 60.0  # how often markets that opened or were determined are applied
    ask_seconds: float = 1.0  # how often the snapshots wanted are asked for
    resnapshot_seconds: float = 60.0  # one market's snapshot is asked for at most this often
    choice_after: timedelta = timedelta(minutes=15)  # after 00:00 UTC: the copies settle first
    refresh_seconds: float = 1_800.0  # the longest a refresh of the copies may take
    stale_after: timedelta = timedelta(hours=2)  # copies older than this are refreshed at start
    lookback: timedelta = timedelta(days=8)  # the hours a refresh makes sure are copied
    young: timedelta = timedelta(days=1)  # a market added on opening stays this long


Refresh = Callable[[datetime], Awaitable[str | None]]  # copy the hours since a time; why it failed
Choose = Callable[[Path], tuple[list[str], dict[str, Any]]]


async def refresh_copies(live: Path, since: datetime, limit_s: float) -> str | None:
    """Bring the live copies up to date with ``live extract`` in a child process (the database
    reader's own settings; the Kalshi key is not passed on). Returns why it failed, or None."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPARQ_KALSHI_")}
    command = [sys.executable, "-m", "sparq_analytics.live", "extract", "--data", str(live)]
    process = await asyncio.create_subprocess_exec(
        *command, "--start", hour_name(since), env=env,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    try:
        _, err = await asyncio.wait_for(process.communicate(), limit_s)
    except TimeoutError:
        return f"the refresh took longer than {limit_s:g} s"
    finally:
        if process.returncode is None:  # too slow, or the recorder is stopping: no child is left
            process.kill()
            await process.wait()
    if process.returncode:
        return f"live extract exited {process.returncode}: {err.decode(errors='replace')[-300:]}"
    return None


def _us(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000)


class Recorder:
    def __init__(
        self,
        feed: Feed,
        settings: Settings,
        *,
        refresh: Refresh | None = None,
        chooser: Choose = choose,
        now_us: Callable[[], int] = lambda: time.time_ns() // 1000,
    ) -> None:
        self.feed, self.settings = feed, settings
        self.refresh: Refresh = refresh or (
            lambda since: refresh_copies(settings.live, since, settings.refresh_seconds)
        )
        self.chooser, self.now_us = chooser, now_us
        self.keeper = Keeper()
        self.writer = store.Writer(self._hour_closed)
        self.tickers: set[str] = set()
        self.added_us: dict[str, int] = {}  # markets added on opening, when
        self.opening: dict[str, int] = {}  # created markets not yet open, and when they open
        self.to_add: set[str] = set()
        self.to_remove: set[str] = set()
        self.sid: dict[str, int] = {}  # each channel's subscription on the current link
        self.want_all = False
        self.want: set[str] = set()
        self.asked_us: dict[str, int] = {}
        self.counts: Counter[str] = Counter()
        self.disk: asyncio.Queue[Callable[[], Any]] = asyncio.Queue()

    # -- the run ----------------------------------------------------------------------------------

    async def run(self, stop: asyncio.Event, seconds: float | None = None) -> dict[str, Any]:
        """Record until ``stop`` is set or ``seconds`` have passed; returns what was counted."""
        out = self.settings.out
        started = self.now_us()
        recovered = await asyncio.to_thread(store.recover, out, store.hour_start(started))
        tickers, counts = await asyncio.to_thread(self.chooser, self.settings.live)
        if not tickers:
            raise LiveSourceError(f"no market qualifies at {counts['at']}")
        store.write_list(out, self._day(started), tickers, counts)
        store.log_event(out, "started", started, markets=len(tickers), recovered=recovered)
        self._book_row(started, 0, None, None, None, RESET)  # nothing is known before this
        consumer = asyncio.create_task(self._consume())
        disk = asyncio.create_task(self._disk())
        tasks: list[asyncio.Task[Any]] = [consumer, disk]
        subscriptions: list[dict[str, Any]] = []
        try:
            for channel in (ORDERBOOK, TRADE):
                result = await subscribe_all(self.feed, channel, tickers)
                subscriptions.append(result)
                if result["sids"]:
                    self.sid[channel] = result["sids"][0]
            skipped = set(subscriptions[0]["skipped"])
            self.tickers = set(tickers) - skipped
            if skipped:
                store.log_change(out, self.now_us(), "remove", sorted(skipped), "refused")
            await self.feed.subscribe(MARKET_LIFECYCLE)
            tasks += [
                asyncio.create_task(self._every(self.settings.flush_seconds, self._flush)),
                asyncio.create_task(self._every(self.settings.ask_seconds, self._ask_snapshots)),
                asyncio.create_task(self._every(self.settings.change_seconds, self._apply_changes)),
                asyncio.create_task(self._hourly()),
                asyncio.create_task(self._daily()),
            ]
            end = last_hour_end(self.settings.live)
            if end is None or started - _us(end) > self.settings.stale_after.total_seconds() * 1e6:
                tasks.append(asyncio.create_task(self.rechoose("the copies were stale")))
            stopper = asyncio.create_task(stop.wait())
            tasks.append(stopper)
            await self._until_stopped(stopper, consumer, set(tasks), seconds)
        finally:
            for task in tasks:
                if task is not disk:
                    task.cancel()
            await asyncio.gather(*(t for t in tasks if t is not disk), return_exceptions=True)
            self._flush()
            try:
                await self._drain(disk)
            finally:  # logged even when a write failed, so status never shows a dead recorder
                store.log_event(out, "stopped", self.now_us(), counts=dict(self.counts))
        return {
            "markets": len(self.tickers),
            "chosen": counts,
            "subscriptions": subscriptions,
            "counts": dict(self.counts),
            "feed": self.feed.stats(),
        }

    async def _until_stopped(
        self,
        stopper: asyncio.Task[Any],
        consumer: asyncio.Task[Any],
        watched: set[asyncio.Task[Any]],
        seconds: float | None,
    ) -> None:
        """Wait for the stop, the time to run out, or a task to fail; a one-off task that simply
        finishes (the refresh at start) is let go."""
        deadline = None if seconds is None else time.monotonic() + seconds
        while True:
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            done, _ = await asyncio.wait(watched, timeout=left, return_when=asyncio.FIRST_COMPLETED)
            for task in done - {stopper}:
                task.result()  # a task that failed stops the recording with its error
            if consumer in done:
                raise RecorderError("the feed ended")
            if stopper in done or not done:
                return
            watched -= done

    async def _drain(self, disk: asyncio.Task[Any]) -> None:
        """Let the disk work queued so far finish, then stop the worker."""
        if not disk.done():
            waiter = asyncio.create_task(self.disk.join())
            await asyncio.wait({waiter, disk}, return_when="FIRST_COMPLETED")
            waiter.cancel()
            disk.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await disk  # a write that failed raises here

    async def _every(self, seconds: float, step: Callable[[], Any]) -> None:
        while True:
            await asyncio.sleep(seconds)
            result = step()
            if asyncio.iscoroutine(result):
                await result

    async def _disk(self) -> None:
        """Do the disk work one job at a time, off the event loop, in the order it was queued."""
        while True:
            job = await self.disk.get()
            try:
                await asyncio.to_thread(job)
            except OSError as error:
                raise RecorderError(f"a write failed: {error}") from error
            finally:
                self.disk.task_done()

    async def _hourly(self) -> None:
        """At the start of every hour, ask for every market's snapshot."""
        while True:
            now = self.now_us()
            next_hour = (now // store.US_PER_HOUR + 1) * store.US_PER_HOUR
            await asyncio.sleep((next_hour - now) / 1e6)
            self.want_all = True

    async def _daily(self) -> None:
        """Just after 00:00 UTC, refresh the copies and choose the day's markets."""
        day_us = 24 * store.US_PER_HOUR
        after = int(self.settings.choice_after.total_seconds() * 1e6)
        while True:
            now = self.now_us()
            moment = (now - after) // day_us * day_us + day_us + after
            await asyncio.sleep((moment - now) / 1e6)
            await self.rechoose("the day's choice")

    # -- the events -------------------------------------------------------------------------------

    async def _consume(self) -> None:
        async for event in self.feed.events():
            self.handle(event)

    def handle(self, event: FeedEvent) -> None:
        if isinstance(event, Data):
            self._data(event)
            return
        at = self.now_us()
        out = self.settings.out
        if isinstance(event, Reconnected):
            self.sid.clear()  # the old link's ids mean nothing now
            self._forget_all(at, event.generation)
            store.log_event(
                out, "reconnect", at, link=event.generation, reason=event.reason,
                downtime=event.downtime,
            )  # fmt: skip
        elif isinstance(event, SequenceGap):
            channel = self.feed.channel_of(event.sid)
            store.log_event(
                out, "sequence_gap", at, link=event.generation, channel=channel,
                expected=event.expected, got=event.got,
            )  # fmt: skip
            if channel in (ORDERBOOK, None):  # unknown: the books may be wrong, so ask again
                self._forget_all(at, event.generation)
                self.want_all = True
        elif isinstance(event, ResubscribeFailed):
            store.log_event(out, "resubscribe_failed", at, channel=event.channel, error=event.error)
            raise RecorderError(f"Kalshi refused to restore {event.channel}: {event.error}")

    def _data(self, event: Data) -> None:
        frame, recv_us, link = event.frame, event.received_ns // 1000, event.generation
        message = frame.payload
        channel = CHANNEL_OF_TYPE.get(frame.type)
        if channel is not None and frame.sid is not None:
            self.sid[channel] = frame.sid
        if isinstance(message, OrderbookSnapshotMsg):
            self.keeper.replace(message.market_ticker, message.yes, message.no)
            self._book_row(recv_us, link, frame, message.market_ticker, None, store.SNAPSHOT,
                           clock=message.clock)  # fmt: skip
            for side, levels in (("yes", message.yes), ("no", message.no)):
                for price, size in levels:
                    self._book_row(recv_us, link, frame, message.market_ticker, side, LEVEL,
                                   price, size, message.clock)  # fmt: skip
        elif isinstance(message, OrderbookDeltaMsg):
            ticker = message.market_ticker
            self._book_row(recv_us, link, frame, ticker, message.side, CHANGE, message.price_e6,
                           message.delta_e2, message.clock, message.ts_ms)  # fmt: skip
            if self.keeper.apply(ticker, message.side, message.price_e6, message.delta_e2) is False:
                self._book_row(recv_us, link, None, ticker, None, RESET)
                self.counts["impossible"] += 1
                store.log_event(self.settings.out, "impossible", recv_us, ticker=ticker,
                                side=message.side, price_e6=message.price_e6)  # fmt: skip
                self.want.add(ticker)
        elif isinstance(message, TradeMsg):
            self.counts["trades"] += 1
            self.writer.add("trade", (
                recv_us, link, frame.sid, frame.seq, message.trade_id, message.market_ticker,
                message.yes_price_e6, message.count_e2, message.taker_outcome_side or
                message.taker_side, message.taker_book_side, message.is_block_trade,
                message.ts_ms, frame.sending_ts_ms,
            ))  # fmt: skip
        elif isinstance(message, MarketLifecycleMsg):
            self.counts["lifecycle"] += 1
            self._lifecycle(message, recv_us)
        else:
            self.counts["odd"] += 1
            self.writer.add("odd", (recv_us, link, frame.type, frame.error,
                                    json.dumps(frame.raw, separators=(",", ":"))))  # fmt: skip
        if self.writer.size() >= store.PART_ROWS:
            self._flush()

    def _book_row(
        self,
        recv_us: int,
        link: int,
        frame: Any,
        ticker: str | None,
        side: str | None,
        kind: int,
        price_e6: int | None = None,
        size_e2: int | None = None,
        clock: int | None = None,
        ts_ms: int | None = None,
    ) -> None:
        self.counts[f"book_{kind}"] += 1
        self.writer.add("book", (
            recv_us, link, None if frame is None else frame.sid,
            None if frame is None else frame.seq, ticker, kind,
            None if side is None else SIDE_CODE[side], price_e6, size_e2, clock, ts_ms,
            None if frame is None else frame.sending_ts_ms,
        ))  # fmt: skip

    def _forget_all(self, at_us: int, link: int) -> None:
        self.keeper.forget()
        self._book_row(at_us, link, None, None, None, RESET)

    def _lifecycle(self, message: MarketLifecycleMsg, recv_us: int) -> None:
        ticker = message.market_ticker
        if not ticker or ticker.startswith(COMBO_PREFIX):
            return
        if message.event_type == "created":
            opens = recv_us if message.open_ts is None else _us(message.open_ts)
            if opens <= recv_us:
                self.to_add.add(ticker)
            else:
                self.opening[ticker] = opens
        elif message.event_type in REMOVED_WHEN:
            self.opening.pop(ticker, None)
            self.to_add.discard(ticker)
            if ticker in self.tickers:
                self.to_remove.add(ticker)

    # -- writing ----------------------------------------------------------------------------------

    def _flush(self) -> None:
        hour = self.writer.hour
        if hour is None or not self.writer.size():
            return
        rows = self.writer.take()
        self.disk.put_nowait(lambda: store.write_parts(self.settings.out, hour, rows))

    def _hour_closed(self, hour: datetime, rows: store.Rows) -> None:
        out = self.settings.out
        self.disk.put_nowait(lambda: store.write_parts(out, hour, rows))
        self.disk.put_nowait(lambda: store.combine_hour(out, hour))

    # -- asking Kalshi ----------------------------------------------------------------------------

    async def _ask_snapshots(self) -> None:
        sid = self.sid.get(ORDERBOOK)
        if sid is None:
            return  # a new link not yet confirmed: Kalshi sends the snapshots of a restore itself
        now = self.now_us()
        if self.want_all:
            wanted, self.want_all = sorted(self.tickers), False
            self.want.clear()
            self.counts["snapshot_rounds"] += 1
        else:
            limit = int(self.settings.resnapshot_seconds * 1e6)
            wanted = sorted(t for t in self.want if now - self.asked_us.get(t, -limit) >= limit)
            self.want -= set(wanted)
        batch = self.settings.snapshot_batch
        for start in range(0, len(wanted), batch):
            chunk = wanted[start : start + batch]
            try:
                await self.feed.request_snapshot(sid, chunk)
            except FeedError:
                self.want |= set(wanted[start:])  # not connected: asked again on the next turn
                return
            for ticker in chunk:
                self.asked_us[ticker] = now
            self.counts["snapshots_asked"] += len(chunk)

    async def _apply_changes(self) -> None:
        """Add the markets that opened and remove those determined, both channels alike."""
        now = self.now_us()
        for ticker, opens in list(self.opening.items()):
            if opens <= now:
                self.to_add.add(ticker)
                del self.opening[ticker]
        adds = sorted(self.to_add - self.tickers)
        removes = sorted(self.to_remove & self.tickers)
        if adds and await self._change("add_markets", adds, "opened"):
            self.to_add -= set(adds)
            self.added_us.update(dict.fromkeys(adds, now))
            self.want.update(adds)  # their first book, in case adding sends none
        if removes:
            await self._change("delete_markets", removes, "determined")
        self.to_add -= self.tickers

    async def _change(self, action: str, tickers: list[str], why: str) -> bool:
        """Change both channels' market lists; False when not connected (tried again later). A
        market Kalshi refuses is left out: not added, or taken as gone already."""
        book, trade = self.sid.get(ORDERBOOK), self.sid.get(TRADE)
        if book is None or trade is None:
            return False
        refused: set[str] = set()
        batch = self.settings.snapshot_batch
        try:
            for sid in (book, trade):
                for start in range(0, len(tickers), batch):
                    refused |= await self._update(sid, action, tickers[start : start + batch])
        except FeedError:
            return False
        at = self.now_us()
        if action == "add_markets":
            done = [t for t in tickers if t not in refused]
            self.tickers |= set(done)
            self.to_add -= refused
        else:
            done = tickers
            self.tickers -= set(done)
            self.to_remove -= set(done)
            for ticker in done:
                self.keeper.forget(ticker)
        if done:
            store.log_change(self.settings.out, at, CHANGE_NAME[action], done, why)
        if refused:
            store.log_event(self.settings.out, "change_refused", at, action=action,
                            markets=len(refused), first=min(refused))  # fmt: skip
        return True

    async def _update(self, sid: int, action: str, chunk: list[str]) -> set[str]:
        """Change one subscription by ``chunk``; returns the markets refused, found by halving a
        refused batch. A failure without an error code is the link's, and is raised."""
        try:
            await self.feed.update_subscription(sid, action, chunk)
        except FeedError as error:
            if error.code is None:
                raise
            if len(chunk) == 1:
                return set(chunk)
            half = len(chunk) // 2
            return await self._update(sid, action, chunk[:half]) | await self._update(
                sid, action, chunk[half:]
            )
        return set()

    async def rechoose(self, why: str) -> None:
        """Refresh the copies, choose B2's markets afresh and change the subscriptions to them.
        When the refresh or the choice fails the list stays as it is, and the failure is logged."""
        out, now = self.settings.out, self.now_us()
        since = datetime.fromtimestamp(now / 1e6, tz=UTC) - self.settings.lookback
        failure = await self.refresh(since.replace(minute=0, second=0, microsecond=0))
        if failure is None:
            try:
                tickers, counts = await asyncio.to_thread(self.chooser, self.settings.live)
            except (LiveSourceError, OSError) as error:
                failure = f"the choice failed: {error}"
        if failure is not None:
            store.log_event(out, "refresh_failed", self.now_us(), why=why, error=failure)
            return
        young_us = int(self.settings.young.total_seconds() * 1e6)
        young = {t for t, at in self.added_us.items() if now - at < young_us and t in self.tickers}
        chosen = set(tickers) | young
        await self._change("delete_markets", sorted(self.tickers - chosen), "the day's choice")
        adds = sorted(chosen - self.tickers)
        if adds and await self._change("add_markets", adds, "the day's choice"):
            self.want.update(adds)
        self.added_us = {t: at for t, at in self.added_us.items() if t in young}
        store.write_list(out, self._day(self.now_us()), sorted(self.tickers), counts | {"why": why})
        store.log_event(out, "chosen", self.now_us(), why=why, markets=len(self.tickers),
                        kept_young=len(young))  # fmt: skip

    @staticmethod
    def _day(at_us: int) -> str:
        return datetime.fromtimestamp(at_us / 1e6, tz=UTC).date().isoformat()
