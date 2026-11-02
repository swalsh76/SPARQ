"""S6.2's keeper, store and recorder against a scripted feed and a temporary folder."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from sparq_analytics.books import recorder as rec
from sparq_analytics.books import store
from sparq_analytics.books.keeper import Book, Keeper, Top
from sparq_analytics.books.recorder import Recorder, RecorderError, Settings
from sparq_analytics.books.store import CHANGE, LEVEL, RESET, SNAPSHOT
from sparq_analytics.live.source import LiveSourceError
from sparq_core.ws import Data, FeedError, Reconnected, ResubscribeFailed, SequenceGap
from sparq_core.ws_models import parse_frame

T0 = datetime(2026, 11, 2, 18, 30, tzinfo=UTC)
T0_US = int(T0.timestamp() * 1_000_000)
SIDS = {"orderbook_delta": 1, "trade": 2, "market_lifecycle_v2": 3}
CHANNELS = {sid: channel for channel, sid in SIDS.items()}
END = object()

# -- the keeper -----------------------------------------------------------------------------------


def test_a_book_rebuilt_from_a_snapshot_and_changes() -> None:
    keeper = Keeper()
    keeper.replace("A", [(400_000, 1_000), (380_000, 500)], [(550_000, 200)])
    assert keeper.top("A") == Top(400_000, 1_000, 450_000, 200)  # the yes ask: a dollar less
    assert keeper.apply("A", "yes", 410_000, 300) is True
    assert keeper.top("A") == Top(410_000, 300, 450_000, 200)
    assert keeper.apply("A", "yes", 410_000, -300) is True  # the best level empties
    assert keeper.top("A") == Top(400_000, 1_000, 450_000, 200)
    assert keeper.apply("A", "no", 550_000, -200) is True  # the no side empties: no ask
    assert keeper.top("A") == Top(400_000, 1_000, None, None)
    assert keeper.apply("A", "yes", 380_000, 100) is True  # below the best: the best stands
    assert keeper.books["A"].yes == {400_000: 1_000, 380_000: 600}


def test_an_impossible_change_forgets_the_market_and_an_unknown_one_is_ignored() -> None:
    keeper = Keeper()
    assert keeper.apply("A", "yes", 400_000, 100) is None  # no snapshot yet
    keeper.replace("A", [(400_000, 100)], [])
    assert keeper.apply("A", "yes", 400_000, -101) is False
    assert keeper.top("A") is None and "A" not in keeper.books
    keeper.replace("A", [], [])
    keeper.replace("B", [], [])
    keeper.forget("A")
    assert list(keeper.books) == ["B"]
    keeper.forget()
    assert keeper.books == {}


def test_a_crossed_top_and_an_empty_book() -> None:
    assert Top(600_000, 1, 550_000, 1).crossed and not Top(500_000, 1, 550_000, 1).crossed
    assert not Top(None, None, 550_000, 1).crossed
    assert Book().top() == Top(None, None, None, None)
    book = Book()
    book.set_level("yes", 400_000, 0)  # a level of nothing is no level
    assert book.yes == {} and book.best_yes is None


# -- the store ------------------------------------------------------------------------------------


def book_row(recv_us: int, ticker: str | None = "A", kind: int = CHANGE) -> tuple[Any, ...]:
    return (recv_us, 1, 1, 1, ticker, kind, 1, 400_000, 100, 7, recv_us // 1000, None)


def test_the_writer_hands_over_an_hour_when_the_next_begins() -> None:
    closed: list[tuple[datetime, int]] = []
    writer = store.Writer(lambda hour, rows: closed.append((hour, len(rows["book"]))))
    writer.add("book", book_row(T0_US))
    writer.add("book", book_row(T0_US + 1))
    assert writer.size() == 2 and closed == []
    writer.add("book", book_row(T0_US + store.US_PER_HOUR))
    assert closed == [(T0.replace(minute=0), 2)] and writer.size() == 1
    assert writer.take()["book"] and writer.size() == 0


def test_parts_are_combined_into_the_hours_file_in_order(tmp_path: Path) -> None:
    hour = T0.replace(minute=0)
    store.write_part(tmp_path, "book", hour, [book_row(T0_US)])
    store.write_part(tmp_path, "book", hour, [book_row(T0_US + 5)])
    combined = store.combine(tmp_path, "book", hour)
    assert combined == store.hour_file(tmp_path, "book", hour)
    assert pl.read_parquet(combined)["recv_us"].to_list() == [T0_US, T0_US + 5]
    assert not store.parts_dir(tmp_path, "book", hour).exists()
    assert store.combine(tmp_path, "book", hour) is None  # nothing left to combine
    assert store.hours_recorded(tmp_path) == [hour]


def test_recovery_finishes_what_a_crash_left(tmp_path: Path) -> None:
    past, now = T0.replace(minute=0) - timedelta(hours=2), T0.replace(minute=0)
    done = past + timedelta(hours=1)
    store.write_part(tmp_path, "book", past, [book_row(T0_US)])  # a crash before combining
    (store.parts_dir(tmp_path, "book", past) / "p00009.parquet.tmp").write_bytes(b"half")
    store.write_part(tmp_path, "book", done, [book_row(T0_US)])
    store.combine(tmp_path, "book", done)
    store.write_part(tmp_path, "book", done, [book_row(T0_US)])  # combined, parts not removed
    store.write_part(tmp_path, "book", now, [book_row(T0_US)])
    store.combine(tmp_path, "book", now)  # a clean stop this hour
    (tmp_path / "book" / "x.parquet.tmp").write_bytes(b"half")
    report = store.recover(tmp_path, now)["done"]
    assert store.hour_file(tmp_path, "book", past).exists()
    assert pl.read_parquet(store.hour_file(tmp_path, "book", done)).height == 1
    assert not store.hour_file(tmp_path, "book", now).exists()  # reopened as its first part
    assert (store.parts_dir(tmp_path, "book", now) / "p00000.parquet").exists()
    assert not list(tmp_path.rglob("*.tmp"))
    assert len(report) == 3
    # the hour goes on after its reopened part, and a reader sees both
    store.write_part(tmp_path, "book", now, [book_row(T0_US + 9)])
    rows = store.read_hours(tmp_path, "book", past, now + timedelta(hours=1)).collect()
    assert rows.height == 4
    empty = store.read_hours(tmp_path, "trade", past, now).collect()
    assert empty.height == 0 and empty.schema == pl.Schema(store.TRADE_SCHEMA)


def test_the_gap_log_and_the_lists(tmp_path: Path) -> None:
    store.log_event(tmp_path, "started", T0_US, markets=2)
    store.log_event(tmp_path, "stopped", T0_US + 1)
    events = store.read_events(tmp_path / store.GAPS)
    assert [e["kind"] for e in events] == ["started", "stopped"]
    assert events[0]["at"] == T0.isoformat() and events[0]["markets"] == 2
    assert store.read_events(tmp_path / "none.jsonl") == []
    store.write_list(tmp_path, "2026-11-02", ["A"], {"chosen": 1})
    store.log_change(tmp_path, T0_US, "add", ["N"], "opened")
    assert json.loads((tmp_path / "lists" / "2026-11-02.json").read_text())["tickers"] == ["A"]
    change = json.loads((tmp_path / "lists" / "changes.jsonl").read_text())
    assert change == {"at": T0.isoformat(), "action": "add", "why": "opened", "tickers": ["N"]}


# -- the recorder ---------------------------------------------------------------------------------


def data(kind: str, msg: dict[str, Any], *, seq: int, at_us: int, link: int = 1) -> Data:
    sid = SIDS.get(kind if kind != "orderbook_snapshot" else "orderbook_delta", 9)
    body = {"type": kind, "sid": sid, "seq": seq, "msg": msg, "sending_ts_ms": at_us // 1000}
    return Data(parse_frame(json.dumps(body)), at_us * 1000, time.monotonic_ns(), link)


def snapshot(ticker: str, at_us: int, yes: list[list[str]], no: list[list[str]] | None = None,
             seq: int = 1) -> Data:  # fmt: skip
    body: dict[str, Any] = {"market_ticker": ticker, "clock": 5, "yes_dollars_fp": yes}
    if no:
        body["no_dollars_fp"] = no
    return data("orderbook_snapshot", body, seq=seq, at_us=at_us)


def delta(ticker: str, at_us: int, price: str, size: str, seq: int = 2, side: str = "yes") -> Data:
    body = {"market_ticker": ticker, "clock": 6, "side": side, "price_dollars": price,
            "delta_fp": size, "ts_ms": at_us // 1000}  # fmt: skip
    return data("orderbook_delta", body, seq=seq, at_us=at_us)


def lifecycle(ticker: str, event: str, at_us: int, opens: datetime | None = None) -> Data:
    body: dict[str, Any] = {"market_ticker": ticker, "event_type": event}
    if opens is not None:
        body["open_ts"] = int(opens.timestamp())
    return data("market_lifecycle_v2", body, seq=1, at_us=at_us)


class Feed:
    """Scripted: what was subscribed, changed and asked for is kept; the events are put in."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.subscribed: list[tuple[str, list[str]]] = []
        self.updates: list[tuple[int, str, list[str]]] = []
        self.snapshots: list[tuple[int, list[str]]] = []
        self.refuse: set[str] = set()
        self.connected = True
        self.sids: dict[str, int] = {}  # each channel's subscription on the current link

    async def subscribe(self, channel: str, market_tickers: list[str] | None = None) -> int:
        self.subscribed.append((channel, list(market_tickers or [])))
        self.sids[channel] = SIDS[channel]
        return SIDS[channel]

    def sid_for(self, channel: str) -> int | None:
        return self.sids.get(channel)

    async def update_subscription(
        self, sid: int, action: str, market_tickers: list[str]
    ) -> list[str]:
        if not self.connected:
            raise FeedError("not connected")
        if self.refuse & set(market_tickers):
            raise FeedError("market not found", code=8)
        self.updates.append((sid, action, list(market_tickers)))
        return []

    async def request_snapshot(self, sid: int, market_tickers: list[str]) -> None:
        if not self.connected:
            raise FeedError("not connected")
        self.snapshots.append((sid, list(market_tickers)))

    def channel_of(self, sid: int, generation: int | None = None) -> str | None:
        return CHANNELS.get(sid)

    def put(self, *events: Any) -> None:
        for event in events:
            self.queue.put_nowait(event)

    def events(self) -> Any:
        feed = self

        class Stream:
            def __aiter__(self) -> "Stream":
                return self

            async def __anext__(self) -> Any:
                item = await feed.queue.get()
                if item is END:
                    raise StopAsyncIteration
                return item

        return Stream()

    def stats(self) -> dict[str, Any]:
        return {"generation": 1}


def settings(tmp_path: Path, **changes: Any) -> Settings:
    """Settings for a test, with copies whose newest hour ends at 19:00, so not stale at T0
    (the copies' names are all the recorder reads of them; the choice is the test's own)."""
    for name in ("ticker", "trade"):
        (tmp_path / "live" / name).mkdir(parents=True, exist_ok=True)
        (tmp_path / "live" / name / "2026-11-02T18.parquet").touch()
    base: dict[str, Any] = {"out": tmp_path / "rec", "live": tmp_path / "live",
                            "flush_seconds": 0.02, "change_seconds": 0.02,
                            "ask_seconds": 0.02}  # fmt: skip
    return Settings(**(base | changes))


class Clock:
    def __init__(self, at_us: int = T0_US) -> None:
        self.at_us = at_us

    def __call__(self) -> int:
        return self.at_us


def recorder(
    tmp_path: Path, feed: Feed, picks: list[list[str]] | None = None, **changes: Any
) -> Recorder:
    """A recorder whose choices are ``picks`` in turn (the start's first) and whose refreshes
    succeed and are kept in ``refreshes``."""
    left = list(picks or [["A", "B"], ["B", "C"]])

    def chooser(live: Path) -> tuple[list[str], dict[str, Any]]:
        tickers = left.pop(0)
        return tickers, {"at": T0.isoformat(), "chosen": len(tickers)}

    async def refresh(since: datetime) -> str | None:
        refreshes.append(since)
        return None

    refreshes: list[datetime] = []
    made = Recorder(feed, settings(tmp_path, **changes), refresh=refresh, chooser=chooser,
                    now_us=Clock())  # fmt: skip
    made.refreshes = refreshes  # type: ignore[attr-defined]
    return made


async def run_with(
    made: Recorder, feed: Feed, *events: Any, settle: float = 0.15
) -> dict[str, Any]:
    stop = asyncio.Event()
    task = asyncio.create_task(made.run(stop))
    await asyncio.sleep(0.05)
    feed.put(*events)
    await asyncio.sleep(settle)
    stop.set()
    return await task


def rows(tmp_path: Path, table: str = "book") -> pl.DataFrame:
    hour = T0.replace(minute=0)
    return store.read_hours(tmp_path / "rec", table, hour - timedelta(hours=1),
                            hour + timedelta(hours=2)).collect()  # fmt: skip


async def test_a_recording_keeps_every_book_event_trade_and_odd_frame(tmp_path: Path) -> None:
    feed = Feed()
    made = recorder(tmp_path, feed)
    trade = data("trade", {"trade_id": "t1", "market_ticker": "A", "yes_price_dollars": "0.41",
                           "no_price_dollars": "0.59", "count_fp": "2.00", "taker_side": "yes",
                           "taker_book_side": "bid", "ts_ms": T0_US // 1000}, seq=1,
                 at_us=T0_US + 30)  # fmt: skip
    result = await run_with(
        made, feed,
        snapshot("A", T0_US + 10, [["0.40", "10.00"]], [["0.55", "5.00"]]),
        snapshot("B", T0_US + 11, []),
        delta("A", T0_US + 20, "0.41", "3.00"),
        trade,
        delta("A", T0_US + 40, "0.4100001", "1.00", seq=3),  # does not fit: kept as odd
        data("multivariate_lookup", {"x": 1}, seq=1, at_us=T0_US + 50),
        data("event_lifecycle", {"event_ticker": "E"}, seq=2, at_us=T0_US + 60),  # not kept
    )  # fmt: skip
    assert [c for c, _ in feed.subscribed] == ["orderbook_delta", "trade", "market_lifecycle_v2"]
    assert feed.subscribed[0][1] == ["A", "B"] and feed.subscribed[2][1] == []
    book = rows(tmp_path)
    # the change that did not fit makes A's book unknown (a reset), and it is asked for again
    assert book["kind"].to_list() == [RESET, SNAPSHOT, LEVEL, LEVEL, SNAPSHOT, CHANGE, RESET]
    assert book["ticker"][6] == "A" and result["counts"]["book_unparsed"] == 1
    assert book["side"].to_list() == [None, None, 1, 2, None, 1, None]
    assert book["size_e2"].to_list() == [None, None, 1_000, 500, None, 300, None]
    assert (
        book["seq"].to_list()[1:6] == [1, 1, 1, 1, 2] and book["ts_ms"][5] == (T0_US + 20) // 1000
    )
    assert made.keeper.top("A") is None and (1, ["A"]) in feed.snapshots
    trades = rows(tmp_path, "trade")
    assert trades.select("trade_id", "count_e2", "taker_side", "taker_book_side").row(0) == (
        "t1", 200, "yes", "bid")  # fmt: skip
    odd = rows(tmp_path, "odd")
    assert odd["type"].to_list() == ["orderbook_delta", "multivariate_lookup"]
    assert odd["error"][0] and json.loads(odd["raw"][1])["msg"] == {"x": 1}
    kinds = [e["kind"] for e in store.read_events(tmp_path / "rec" / store.GAPS)]
    assert kinds == ["started", "stopped"]
    assert json.loads((tmp_path / "rec" / "lists" / "2026-11-02.json").read_text())["tickers"] == [
        "A", "B"]  # fmt: skip
    assert (
        result["markets"] == 2 and result["counts"]["trades"] == 1 and result["counts"]["odd"] == 2
    )


async def test_a_lost_link_or_a_seq_jump_forgets_the_books_and_asks_again(tmp_path: Path) -> None:
    feed = Feed()
    made = recorder(tmp_path, feed)
    await run_with(
        made, feed,
        snapshot("A", T0_US + 10, [["0.40", "10.00"]]),
        Reconnected(2, "connection_lost", 0.5),
        SequenceGap(2, 3, 5, 2),  # the trade channel: logged, the books stand
        snapshot("A", T0_US + 20, [["0.40", "10.00"]], seq=1),  # the restore's, on the new link
        SequenceGap(1, 2, 4, 2),  # the book channel: every book is unknown and asked for
        delta("A", T0_US + 30, "0.40", "1.00", seq=5),
    )  # fmt: skip
    book = rows(tmp_path)
    assert book["kind"].to_list() == [RESET, SNAPSHOT, LEVEL, RESET, SNAPSHOT, LEVEL, RESET, CHANGE]
    assert book["link"].to_list()[3] == 2 and book["ticker"][6] is None
    assert made.keeper.top("A") is None  # unknown until the snapshot asked for arrives
    assert (1, ["A", "B"]) in feed.snapshots
    events = store.read_events(tmp_path / "rec" / store.GAPS)
    reconnect, *gaps = [e for e in events if e["kind"] in ("reconnect", "sequence_gap")]
    assert reconnect["reason"] == "connection_lost" and reconnect["downtime"] == 0.5
    assert [g["channel"] for g in gaps] == ["trade", "orderbook_delta"]


async def test_an_impossible_change_resets_that_market_and_asks_for_its_snapshot(
    tmp_path: Path,
) -> None:
    feed = Feed()
    made = recorder(tmp_path, feed)
    result = await run_with(
        made, feed,
        snapshot("A", T0_US + 10, [["0.40", "1.00"]]),
        delta("A", T0_US + 20, "0.40", "-2.00"),
        delta("A", T0_US + 30, "0.40", "-2.00", seq=3),  # asked for already: not again so soon
    )  # fmt: skip
    book = rows(tmp_path)
    assert book.filter(pl.col("kind") == RESET)["ticker"].to_list() == [None, "A"]
    assert feed.snapshots == [(1, ["A"])]
    assert result["counts"]["impossible"] == 1
    impossible = [e for e in store.read_events(tmp_path / "rec" / store.GAPS)
                  if e["kind"] == "impossible"]  # fmt: skip
    # the second change met a book already forgotten: unknown, not impossible
    assert impossible[0]["first"] == "A" and impossible[0]["changes"] == 1


async def test_a_refused_resubscription_stops_the_recording_after_writing(tmp_path: Path) -> None:
    feed = Feed()
    made = recorder(tmp_path, feed)
    stop = asyncio.Event()
    task = asyncio.create_task(made.run(stop))
    await asyncio.sleep(0.05)
    feed.put(snapshot("A", T0_US + 10, []), ResubscribeFailed("trade", "[8] gone", 2))
    with pytest.raises(RecorderError, match="refused to restore trade"):
        await asyncio.wait_for(task, 5)
    kinds = [e["kind"] for e in store.read_events(tmp_path / "rec" / store.GAPS)]
    assert kinds == ["started", "resubscribe_failed", "stopped"]
    assert rows(tmp_path).height == 2  # the start's reset and the snapshot


async def test_a_feed_that_ends_stops_the_recording(tmp_path: Path) -> None:
    feed = Feed()
    feed.put(END)
    with pytest.raises(RecorderError, match="the feed ended"):
        await asyncio.wait_for(recorder(tmp_path, feed).run(asyncio.Event()), 5)


async def test_a_failed_write_stops_the_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any) -> None:
        raise OSError("the drive is full")

    monkeypatch.setattr(store, "write_parts", broken)
    with pytest.raises(RecorderError, match="the drive is full"):
        await asyncio.wait_for(recorder(tmp_path, Feed()).run(asyncio.Event()), 5)
    kinds = [e["kind"] for e in store.read_events(tmp_path / "rec" / store.GAPS)]
    assert kinds == ["started", "stopped"]


async def test_the_run_ends_when_its_time_is_up(tmp_path: Path) -> None:
    started = time.monotonic()
    result = await recorder(tmp_path, Feed()).run(asyncio.Event(), seconds=0.1)
    assert time.monotonic() - started < 2 and result["markets"] == 2


async def test_no_market_chosen_is_refused(tmp_path: Path) -> None:
    made = Recorder(Feed(), settings(tmp_path), chooser=lambda live: ([], {"at": "then"}))
    with pytest.raises(LiveSourceError, match="no market qualifies at then"):
        await made.run(asyncio.Event())


async def test_stale_copies_are_refreshed_at_the_start(tmp_path: Path) -> None:
    made = recorder(tmp_path, Feed())
    for name in ("ticker", "trade"):  # the newest copied hour ends 2026-11-02 12:00: stale
        (tmp_path / "live" / name / "2026-11-02T18.parquet").rename(
            tmp_path / "live" / name / "2026-11-02T11.parquet"
        )
    await made.run(asyncio.Event(), seconds=0.2)
    assert made.refreshes == [(T0 - timedelta(days=8)).replace(minute=0)]  # type: ignore[attr-defined]
    chosen = [e for e in store.read_events(tmp_path / "rec" / store.GAPS) if e["kind"] == "chosen"]
    assert chosen and chosen[0]["why"] == "the copies were stale"


async def test_an_hour_is_combined_when_the_next_begins(tmp_path: Path) -> None:
    feed = Feed()
    made = recorder(tmp_path, feed)
    late = T0_US + 29 * 60 * 1_000_000  # 18:59
    await run_with(
        made, feed,
        snapshot("A", late, []),
        snapshot("A", late + 120 * 1_000_000, [], seq=2),  # 19:01
    )  # fmt: skip
    hour = T0.replace(minute=0)
    assert pl.read_parquet(store.hour_file(tmp_path / "rec", "book", hour)).height == 2
    assert store.parts_dir(tmp_path / "rec", "book", hour + timedelta(hours=1)).exists()


async def test_the_hourly_round_asks_for_every_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = Feed()
    made = recorder(tmp_path, feed)
    made.now_us = Clock(T0_US + 30 * 60 * 1_000_000 - 50_000)  # 50 ms before 19:00
    await made.run(asyncio.Event(), seconds=0.3)
    assert (1, ["A", "B"]) in feed.snapshots and made.counts["snapshot_rounds"] >= 1


async def test_markets_that_open_join_and_those_determined_leave(tmp_path: Path) -> None:
    feed = Feed()
    made = recorder(tmp_path, feed)
    made.tickers, feed.sids = {"A", "B"}, {"orderbook_delta": 1, "trade": 2}
    made.handle(lifecycle("N", "created", T0_US, opens=T0 - timedelta(minutes=1)))
    made.handle(lifecycle("F", "created", T0_US, opens=T0 + timedelta(minutes=5)))
    made.handle(lifecycle("KXMVEX", "created", T0_US))
    made.handle(lifecycle("A", "determined", T0_US))
    made.handle(lifecycle("Q", "settled", T0_US))  # not recorded: nothing to remove
    await made._apply_changes()
    assert feed.updates == [
        (1, "add_markets", ["N"]), (2, "add_markets", ["N"]),
        (1, "delete_markets", ["A"]), (2, "delete_markets", ["A"]),
    ]  # fmt: skip
    assert made.tickers == {"B", "N"} and made.added_us == {"N": T0_US} and not made.want
    resets = [r for r in made.writer.rows["book"] if r[5] == RESET]
    assert [r[4] for r in resets] == ["A"]  # the removed market's book is unknown in the files
    made.now_us = Clock(T0_US + 6 * 60 * 1_000_000)  # F has opened
    await made._apply_changes()
    assert feed.updates[-1] == (2, "add_markets", ["F"]) and "F" in made.tickers
    changes = (tmp_path / "rec" / "lists" / "changes.jsonl").read_text().splitlines()
    assert [json.loads(c)["action"] for c in changes] == ["add", "remove", "add"]


async def test_changes_wait_for_a_link_and_refused_markets_are_left_out(tmp_path: Path) -> None:
    feed = Feed()
    made = recorder(tmp_path, feed)
    made.tickers = {"A"}
    assert await made._change("add_markets", ["X"], "opened") is False  # no sid yet
    feed.sids = {"orderbook_delta": 1, "trade": 2}
    feed.connected = False
    assert await made._change("add_markets", ["X"], "opened") is False
    feed.connected, feed.refuse = True, {"BAD"}
    assert await made._change("add_markets", ["X", "BAD", "Y", "Z"], "opened") is True
    assert made.tickers == {"A", "X", "Y", "Z"}
    refused = [e for e in store.read_events(tmp_path / "rec" / store.GAPS)
               if e["kind"] == "change_refused"]  # fmt: skip
    assert refused[0]["markets"] == 1 and refused[0]["first"] == "BAD"


async def test_snapshots_wait_for_a_link_and_are_asked_again_when_it_fails(tmp_path: Path) -> None:
    feed = Feed()
    made = recorder(tmp_path, feed, snapshot_batch=1)
    made.tickers, made.want_all = {"A", "B"}, True
    await made._ask_snapshots()  # no sid on this link yet: Kalshi sends a restore's itself
    assert feed.snapshots == [] and made.want_all
    feed.sids = {"orderbook_delta": 1}
    feed.connected = False
    await made._ask_snapshots()
    assert made.want == {"A", "B"} and not made.want_all
    feed.connected = True
    await made._ask_snapshots()
    assert feed.snapshots == [(1, ["A"]), (1, ["B"])] and made.want == set()


async def test_the_days_choice_refreshes_and_changes_the_list(tmp_path: Path) -> None:
    feed = Feed()
    made = recorder(tmp_path, feed, picks=[["B", "C"]])
    made.tickers, feed.sids = {"A", "B", "N", "OLD"}, {"orderbook_delta": 1, "trade": 2}
    made.added_us = {"N": T0_US - 3_600_000_000, "OLD": T0_US - 2 * 86_400_000_000}
    await made.rechoose("the day's choice")
    assert made.tickers == {"B", "C", "N"}  # A and OLD out, C in, N kept while young
    assert (1, "delete_markets", ["A", "OLD"]) in feed.updates
    assert (1, "add_markets", ["C"]) in feed.updates and not made.want
    listed = json.loads((tmp_path / "rec" / "lists" / "2026-11-02.json").read_text())
    assert listed["tickers"] == ["B", "C", "N"] and listed["counts"]["why"] == "the day's choice"
    assert made.added_us == {"N": T0_US - 3_600_000_000}


async def test_a_failed_refresh_or_choice_keeps_the_list(tmp_path: Path) -> None:
    async def failing(since: datetime) -> str | None:
        return "live extract exited 1: no settings"

    def broken(live: Path) -> tuple[list[str], dict[str, Any]]:
        raise LiveSourceError("2 trade hour(s) not copied")

    made = Recorder(Feed(), settings(tmp_path), refresh=failing, now_us=Clock())
    made.tickers = {"A"}
    await made.rechoose("the day's choice")
    made.refresh = lambda since: asyncio.sleep(0, result=None)
    made.chooser = broken
    await made.rechoose("the day's choice")
    assert made.tickers == {"A"}
    errors = [e["error"] for e in store.read_events(tmp_path / "rec" / store.GAPS)]
    assert errors == ["live extract exited 1: no settings",
                      "the choice failed: LiveSourceError: 2 trade hour(s) not copied"]  # fmt: skip


async def test_the_daily_choice_waits_for_just_after_midnight(tmp_path: Path) -> None:
    made = recorder(tmp_path, Feed(), choice_after=timedelta(seconds=0.05))
    midnight = int(datetime(2026, 11, 3, tzinfo=UTC).timestamp() * 1_000_000)
    made.now_us = Clock(midnight - 50_000)
    chosen: list[str] = []

    async def rechoose(why: str) -> None:
        chosen.append(why)

    made.rechoose = rechoose  # type: ignore[method-assign]
    task = asyncio.create_task(made._daily())
    await asyncio.sleep(0.3)
    task.cancel()
    assert chosen and chosen[0] == "the day's choice"


async def test_refreshing_the_copies_runs_extract_in_a_child_and_reports_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SPARQ_LIVE_CONFIG", str(tmp_path / "no-such.toml"))
    monkeypatch.setenv("SPARQ_KALSHI_KEY_ID", "never-passed-on")
    failure = await rec.refresh_copies(tmp_path / "live", T0, 120)
    assert failure is not None and failure.startswith("live extract exited 1")
    assert "never-passed-on" not in failure
    assert await rec.refresh_copies(tmp_path / "live", T0, 0.001) == (
        "the refresh took longer than 0.001 s"
    )


def test_an_events_row_takes_the_time_of_the_frame_before_it(tmp_path: Path) -> None:
    feed = Feed()
    made = recorder(tmp_path, feed)
    made.handle(snapshot("A", T0_US + 10, []))
    made.now_us = Clock(T0_US + 40 * 60 * 1_000_000)  # handled long after, past the hour
    made.handle(Reconnected(2, "connection_lost", 0.5))
    made.handle(SequenceGap(1, 2, 4, 2))
    times = [r[0] for r in made.writer.rows["book"] if r[5] == RESET]
    assert times == [T0_US + 10, T0_US + 10] and made.writer.hour == T0.replace(minute=0)


def test_a_late_row_stays_in_the_current_hour() -> None:
    closed: list[datetime] = []
    writer = store.Writer(lambda hour, rows: closed.append(hour))
    writer.add("book", book_row(T0_US + store.US_PER_HOUR))
    writer.add("book", book_row(T0_US))  # read before the hour turned, handled after
    assert closed == [] and writer.size() == 2
    assert writer.hour == T0.replace(minute=0) + timedelta(hours=1)


def test_combining_again_keeps_the_rows_already_combined(tmp_path: Path) -> None:
    hour = T0.replace(minute=0)
    store.write_part(tmp_path, "book", hour, [book_row(T0_US)])
    store.combine(tmp_path, "book", hour)
    store.write_part(tmp_path, "book", hour, [book_row(T0_US + 1)])
    store.combine(tmp_path, "book", hour)
    assert pl.read_parquet(store.hour_file(tmp_path, "book", hour)).height == 2


async def test_a_change_goes_on_from_where_a_lost_link_stopped_it(tmp_path: Path) -> None:
    class Flaky(Feed):
        def __init__(self) -> None:
            super().__init__()
            self.fail_trade = True

        async def update_subscription(
            self, sid: int, action: str, market_tickers: list[str]
        ) -> list[str]:
            if sid == 2 and self.fail_trade:
                self.fail_trade = False
                raise FeedError("connection closed")
            return await super().update_subscription(sid, action, market_tickers)

    feed = Flaky()
    feed.sids = {"orderbook_delta": 1, "trade": 2}
    made = recorder(tmp_path, feed)
    assert await made._change("add_markets", ["N"], "opened") is False
    assert await made._change("add_markets", ["N"], "opened") is True
    assert feed.updates == [(1, "add_markets", ["N"]), (2, "add_markets", ["N"])]  # not twice
    assert made.tickers == {"N"} and made.applied == {
        ("add_markets", "orderbook_delta"): set(), ("add_markets", "trade"): set()}  # fmt: skip


async def test_a_choice_waits_for_the_link_and_says_when_it_could_not_be_applied(
    tmp_path: Path,
) -> None:
    feed = Feed()
    made = recorder(tmp_path, feed, picks=[["B"], ["B"]], retry_seconds=0.01, retries=50)
    made.tickers = {"A"}

    async def link_back() -> None:
        await asyncio.sleep(0.05)
        feed.sids = {"orderbook_delta": 1, "trade": 2}

    await asyncio.gather(made.rechoose("the day's choice"), link_back())
    assert made.tickers == {"B"}
    feed.sids = {}
    made.settings = settings(tmp_path, retry_seconds=0.0, retries=2)
    made.tickers = {"A"}
    await made.rechoose("the day's choice")
    kinds = [e["kind"] for e in store.read_events(tmp_path / "rec" / store.GAPS)]
    assert kinds == ["chosen", "choice_not_applied"] and made.tickers == {"A"}


def test_a_torn_part_is_set_aside_and_stray_names_are_left_alone(tmp_path: Path) -> None:
    hour = T0.replace(minute=0) - timedelta(hours=1)
    store.write_part(tmp_path, "book", hour, [book_row(T0_US)])
    (store.parts_dir(tmp_path, "book", hour) / "p00001.parquet").write_bytes(b"torn by a power cut")
    (tmp_path / "parts" / "book" / "notes").mkdir(parents=True)
    (tmp_path / "book").mkdir()
    (tmp_path / "book" / "notes.parquet").write_bytes(b"")
    report = store.recover(tmp_path, T0.replace(minute=0))
    assert report["lost"] == [f"book/{hour.strftime('%Y-%m-%dT%H')}/p00001.parquet"]
    assert (tmp_path / "quarantine" / "book").exists()
    assert pl.read_parquet(store.hour_file(tmp_path, "book", hour)).height == 1
    assert store.hours_recorded(tmp_path) == [hour]


def test_a_torn_last_line_of_the_log_is_left_out(tmp_path: Path) -> None:
    store.log_event(tmp_path, "started", T0_US)
    with (tmp_path / store.GAPS).open("a", encoding="utf-8") as file:
        file.write('{"kind": "stop')
    assert [e["kind"] for e in store.read_events(tmp_path / store.GAPS)] == ["started"]


def test_the_newest_row_and_write(tmp_path: Path) -> None:
    assert store.last_row_us(tmp_path) is None and store.newest_write(tmp_path) is None
    hour = T0.replace(minute=0)
    store.write_part(tmp_path, "book", hour, [book_row(T0_US), book_row(T0_US + 7)])
    assert store.last_row_us(tmp_path) == T0_US + 7
    assert store.newest_write(tmp_path) is not None


async def test_a_run_that_never_logged_its_end_is_logged_as_a_crash(tmp_path: Path) -> None:
    out = tmp_path / "rec"
    store.log_event(out, "started", T0_US - 600_000_000)
    store.write_part(out, "book", T0.replace(minute=0), [book_row(T0_US - 300_000_000)])
    (store.parts_dir(out, "book", T0.replace(minute=0)) / "p00001.parquet").write_bytes(b"torn")
    await recorder(tmp_path, Feed()).run(asyncio.Event(), seconds=0.05)
    events = store.read_events(out / store.GAPS)
    assert [e["kind"] for e in events][:4] == ["started", "crashed", "lost_part", "started"]
    assert events[1]["at_us"] == T0_US - 300_000_000  # the last row kept


async def test_markets_a_full_subscription_left_out_are_not_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Full(Feed):
        async def subscribe(self, channel: str, market_tickers: list[str] | None = None) -> int:
            if channel == "trade" and len(market_tickers or []) > 1:
                raise FeedError("subscription market limit", code=26)
            if channel == "trade" and market_tickers == ["B"]:
                raise FeedError("subscription market limit", code=26)
            return await super().subscribe(channel, market_tickers)

    import sparq_analytics.books.probe as probe

    monkeypatch.setattr(probe, "FULL_AFTER", 1)
    feed = Full()
    made = recorder(tmp_path, feed)
    result = await made.run(asyncio.Event(), seconds=0.05)
    assert result["markets"] == 1 and made.tickers == {"A"}
    assert feed.updates == [(1, "delete_markets", ["B"])]  # off the book channel too
    full = [e for e in store.read_events(tmp_path / "rec" / store.GAPS)
            if e["kind"] == "subscription_full"]  # fmt: skip
    assert full and full[0]["channel"] == "trade" and full[0]["left_out"] == 1


async def test_a_book_message_with_no_market_resets_every_book(tmp_path: Path) -> None:
    feed = Feed()
    made = recorder(tmp_path, feed)
    made.handle(snapshot("A", T0_US + 10, [["0.40", "1.00"]]))
    made.handle(data("orderbook_delta", {"side": "yes"}, seq=2, at_us=T0_US + 20))
    assert made.keeper.books == {} and made.want_all
    assert [r[5] for r in made.writer.rows["book"]][-1] == RESET


async def test_a_snapshot_asked_for_and_never_sent_is_asked_for_again(tmp_path: Path) -> None:
    feed = Feed()
    feed.sids = {"orderbook_delta": 1}
    made = recorder(tmp_path, feed, resnapshot_seconds=60.0)
    made.tickers, made.want = {"A", "B"}, {"A", "B"}
    await made._ask_snapshots()
    made.handle(snapshot("A", T0_US + 10, []))  # A's comes; B's never does
    assert made.awaiting == {"B": T0_US}
    made.now_us = Clock(T0_US + 61_000_000)
    await made._ask_snapshots()
    assert feed.snapshots[-1] == (1, ["B"]) and made.counts["snapshots_overdue"] == 1
    overdue = [e for e in store.read_events(tmp_path / "rec" / store.GAPS)
               if e["kind"] == "snapshots_overdue"]  # fmt: skip
    assert overdue[0]["first"] == "B"


async def test_a_reconnect_logs_where_the_link_was_lost(tmp_path: Path) -> None:
    feed = Feed()
    made = recorder(tmp_path, feed)
    made.handle(snapshot("A", T0_US + 10, []))
    made.now_us = Clock(T0_US + 90_000_000)
    made.handle(Reconnected(2, "stalled", 0.5))
    event = store.read_events(tmp_path / "rec" / store.GAPS)[-1]
    assert event["lost_at_us"] == T0_US + 10 and event["at_us"] == T0_US + 90_000_000


async def test_one_choice_at_a_time(tmp_path: Path) -> None:
    feed = Feed()
    feed.sids = {"orderbook_delta": 1, "trade": 2}
    made = recorder(tmp_path, feed, picks=[["A"], ["A"]])
    active, most = 0, 0

    async def slow(since: datetime) -> str | None:
        nonlocal active, most
        active += 1
        most = max(most, active)
        await asyncio.sleep(0.05)
        active -= 1
        return None

    made.refresh = slow
    await asyncio.gather(made.rechoose("the copies were stale"), made.rechoose("the day's choice"))
    assert most == 1


async def test_an_unexpected_failure_of_the_choice_keeps_the_list(tmp_path: Path) -> None:
    def broken(live: Path) -> tuple[list[str], dict[str, Any]]:
        raise ValueError("a torn copy")

    made = Recorder(Feed(), settings(tmp_path), refresh=lambda since: asyncio.sleep(0, result=None),
                    chooser=broken, now_us=Clock())  # fmt: skip
    made.tickers = {"A"}
    await made.rechoose("the day's choice")
    error = store.read_events(tmp_path / "rec" / store.GAPS)[-1]["error"]
    assert error == "the choice failed: ValueError: a torn copy" and made.tickers == {"A"}


async def test_a_lost_book_message_for_a_market_not_recorded_is_not_asked_for(
    tmp_path: Path,
) -> None:
    made = recorder(tmp_path, Feed())
    made.tickers = {"A"}
    made.handle(data("orderbook_delta", {"market_ticker": "GONE", "side": "maybe"}, seq=2,
                     at_us=T0_US + 20))  # fmt: skip
    assert made.want == set() and [r[4] for r in made.writer.rows["book"]] == ["GONE"]


async def test_a_market_leaves_when_it_closes(tmp_path: Path) -> None:
    feed = Feed()
    feed.sids = {"orderbook_delta": 1, "trade": 2}
    made = recorder(tmp_path, feed)
    made.tickers = {"A", "B"}
    made.closes = {"A": T0_US - 1}  # closed a moment ago; B has no close known
    made.handle(lifecycle("N", "created", T0_US, opens=T0 - timedelta(minutes=1)))
    moved = {"market_ticker": "N", "event_type": "close_date_updated",
             "close_ts": int(T0.timestamp()) - 5}  # fmt: skip
    made.handle(data("market_lifecycle_v2", moved, seq=2, at_us=T0_US))
    await made._apply_changes()
    assert made.tickers == {"B"}  # A closed; N closed before it could be added
    assert (1, "delete_markets", ["A"]) in feed.updates
    assert not any(action == "add_markets" for _, action, _ in feed.updates)


def test_close_times_come_from_the_copies(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    assert rec.close_times(live, ["A"]) == {}
    pl.DataFrame({"ticker": ["A", "B", "C"], "close_ms": [5, None, 7]}).write_parquet(
        live / "markets.parquet"
    )
    assert rec.close_times(live, ["A", "B"]) == {"A": 5_000}


async def test_a_change_stops_when_a_new_link_arrives_between_batches(tmp_path: Path) -> None:
    class Relinked(Feed):
        relinked = False

        async def update_subscription(
            self, sid: int, action: str, market_tickers: list[str]
        ) -> list[str]:
            result = await super().update_subscription(sid, action, market_tickers)
            if not self.relinked:
                self.relinked = True
                self.sids = {"orderbook_delta": 5, "trade": 6}  # the link was lost and restored
            return result

    feed = Relinked()
    feed.sids = {"orderbook_delta": 1, "trade": 2}
    made = recorder(tmp_path, feed, snapshot_batch=1)
    assert await made._change("add_markets", ["X", "Y"], "opened") is False
    assert feed.updates == [(1, "add_markets", ["X"])]  # Y never went to a stale id
    assert await made._change("add_markets", ["X", "Y"], "opened") is True  # it goes on from Y
    assert feed.updates[1:] == [(5, "add_markets", ["Y"]), (6, "add_markets", ["X"]),
                                (6, "add_markets", ["Y"])]  # fmt: skip
    assert made.tickers == {"X", "Y"}


async def test_snapshots_stop_when_a_new_link_arrives_between_batches(tmp_path: Path) -> None:
    class Relinked(Feed):
        async def request_snapshot(self, sid: int, market_tickers: list[str]) -> None:
            await super().request_snapshot(sid, market_tickers)
            self.sids = {"orderbook_delta": 5}

    feed = Relinked()
    feed.sids = {"orderbook_delta": 1}
    made = recorder(tmp_path, feed, snapshot_batch=1)
    made.tickers, made.want_all = {"A", "B"}, True
    await made._ask_snapshots()
    assert feed.snapshots == [(1, ["A"])] and made.want == {"B"}
