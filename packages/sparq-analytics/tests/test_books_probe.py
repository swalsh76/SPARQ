"""S6.1's probe against built live copies and a scripted feed.

**The live copies** end at 2026-11-02 00:00 UTC: 24 hours of ticker frames and 7 days of trades
before it (most hours empty). The markets: ``A`` (mid 50 cents, 70 contracts over the week, open
ten days: 10 a day) is chosen; ``B`` (8,000 contracts in the week, 1,143 a day) is thick; ``C``
(mid 2 cents) and ``I`` (mid 97.5) are in the certainty regime; ``D`` (a bid of 40 alone) is
chosen on its one standing price; ``E`` (open 1.5 days, 1,500 contracts: 750 a day over its two
whole days) is chosen; ``J`` (open half a day, 1,200 contracts, one whole day) is thick; ``F`` is a
combo; ``G`` closed before the end; ``H`` has no quote in the day before.
"""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from sparq_analytics.books import cli, probe
from sparq_analytics.live.source import (
    HOUR,
    TICKER_SCHEMA,
    TRADE_SCHEMA,
    LiveSourceError,
    hour_name,
)
from sparq_core.auth import DataSigner
from sparq_core.ws import Data, FeedError, Reconnected, ResubscribeFailed, SequenceGap
from sparq_core.ws_models import parse_frame

END = datetime(2026, 11, 2, tzinfo=UTC)
END_MS = int(END.timestamp() * 1000)
DAY_MS = 86_400_000


def _frame(
    ticker: str, minutes_before: int, bid: int, bid_size: int, ask: int, ask_size: int
) -> dict[str, Any]:
    ts = END_MS - minutes_before * 60_000
    return {
        "ts_ms": ts, "recv_ms": ts, "ticker": ticker, "price_e6": bid, "yes_bid_e6": bid,
        "yes_ask_e6": ask, "yes_bid_size_e2": bid_size, "yes_ask_size_e2": ask_size,
        "volume_e2": 0,
    }  # fmt: skip


def _trade(ticker: str, hours_before: int, count_e2: int) -> dict[str, Any]:
    return {
        "ts_ms": END_MS - hours_before * 3_600_000 + 1, "ticker": ticker,
        "trade_id": f"{ticker}-{hours_before}", "yes_price_e6": 500_000, "count_e2": count_e2,
        "taker_side": "yes", "is_block_trade": False,
    }  # fmt: skip


FRAMES = [
    _frame("A", 30, 450_000, 100, 550_000, 100),
    _frame("B", 30, 450_000, 100, 550_000, 100),
    _frame("C", 30, 10_000, 100, 30_000, 100),
    _frame("D", 30, 400_000, 100, 1_000_000, 0),  # an empty ask
    _frame("E", 90, 300_000, 100, 400_000, 100),
    _frame("F", 30, 450_000, 100, 550_000, 100),
    _frame("G", 30, 450_000, 100, 550_000, 100),
    _frame("I", 30, 960_000, 100, 990_000, 100),
    _frame("J", 30, 450_000, 100, 550_000, 100),
]
TRADES = [
    _trade("A", 5, 7_000),
    _trade("B", 5, 800_000),
    _trade("E", 5, 150_000),
    _trade("J", 5, 120_000),
]
MARKETS = {
    "A": (END_MS - 10 * DAY_MS, END_MS + DAY_MS),
    "B": (END_MS - 10 * DAY_MS, END_MS + DAY_MS),
    "C": (END_MS - 10 * DAY_MS, END_MS + DAY_MS),
    "D": (END_MS - 10 * DAY_MS, END_MS + DAY_MS),
    "E": (END_MS - DAY_MS - DAY_MS // 2, END_MS + DAY_MS),
    "KXMVEF": (END_MS - 10 * DAY_MS, END_MS + DAY_MS),
    "G": (END_MS - 10 * DAY_MS, END_MS - 1),
    "H": (END_MS - 10 * DAY_MS, END_MS + DAY_MS),
    "I": (END_MS - 10 * DAY_MS, END_MS + DAY_MS),
    "J": (END_MS - DAY_MS // 2, END_MS + DAY_MS),
}


def write_live(root: Path) -> Path:
    live = root / "live"
    for name in ("ticker", "trade"):
        (live / name).mkdir(parents=True)
    hour = END - timedelta(days=7)
    while hour < END:
        start_ms, stop_ms = int(hour.timestamp() * 1000), int((hour + HOUR).timestamp() * 1000)
        frames = [f | {"ticker": "KXMVEF"} if f["ticker"] == "F" else f for f in FRAMES]
        rows = [f for f in frames if start_ms <= f["ts_ms"] < stop_ms]
        if hour >= END - 24 * HOUR:
            pl.DataFrame(rows, schema=TICKER_SCHEMA).write_parquet(
                live / "ticker" / f"{hour_name(hour)}.parquet"
            )
        trades = [t for t in TRADES if start_ms <= t["ts_ms"] < stop_ms]
        pl.DataFrame(trades, schema=TRADE_SCHEMA).write_parquet(
            live / "trade" / f"{hour_name(hour)}.parquet"
        )
        hour += HOUR
    pl.DataFrame(
        {
            "ticker": list(MARKETS),
            "open_ms": [v[0] for v in MARKETS.values()],
            "close_ms": [v[1] for v in MARKETS.values()],
        }
    ).write_parquet(live / "markets.parquet")
    return live


def test_the_markets_are_b2s(tmp_path: Path) -> None:
    tickers, counts = probe.choose(write_live(tmp_path))
    assert tickers == ["A", "D", "E"]
    assert counts == {
        "at": END.isoformat(),
        "open": 9,  # G closed
        "combos_skipped": 1,
        "quoted_in_the_day_before": 7,  # not H, and the combo is out
        "in_the_band": 5,  # not C or I
        "chosen": 3,  # not B or J
    }


def test_no_copies_is_refused(tmp_path: Path) -> None:
    (tmp_path / "ticker").mkdir()
    (tmp_path / "trade").mkdir()
    with pytest.raises(LiveSourceError, match="no hours copied"):
        probe.choose(tmp_path)


# -- a scripted feed -------------------------------------------------------------------------


def frame(kind: str, msg: dict[str, Any], sid: int = 1, seq: int | None = None) -> Any:
    body: dict[str, Any] = {"type": kind, "sid": sid, "msg": msg}
    if seq is not None:
        body["seq"] = seq
    return parse_frame(json.dumps(body))


class FakeFeed:
    """Refuses a subscription of more than ``limit`` markets with code 26; sends a snapshot for
    every market of an order-book subscription, a delta for the first, and on a dropped link a
    reconnect, the snapshots again and a sequence starting over."""

    def __init__(self, limit: int = 1_000_000, clock: bool = True) -> None:
        self.limit, self.clock = limit, clock
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.subscribed: list[tuple[str, list[str]]] = []
        self.generation = 1

    def _put(self, kind: str, msg: dict[str, Any], seq: int | None = None) -> None:
        now = time.time_ns(), time.monotonic_ns()
        self.queue.put_nowait(Data(frame(kind, msg, seq=seq), now[0], now[1], self.generation))

    async def subscribe(self, channel: str, market_tickers: list[str] | None = None) -> int:
        tickers = list(market_tickers or [])
        if len(tickers) > self.limit:
            raise FeedError("Too many markets in one subscription", code=26)
        self.subscribed.append((channel, tickers))
        if channel == "orderbook_delta":
            for ticker in tickers:
                body = {"market_ticker": ticker, "yes_dollars_fp": [["0.40", "10.00"]]}
                self._put("orderbook_snapshot", body | ({"clock": 1} if self.clock else {}))
            self._put("orderbook_delta", {"market_ticker": tickers[0], "price_dollars": "0.41"})
        return len(self.subscribed)

    async def drop_link(self) -> None:
        self.generation += 1
        self.queue.put_nowait(Reconnected(self.generation, "connection_lost", 0.25))
        self.queue.put_nowait(SequenceGap(1, 5, 9, self.generation))
        self.queue.put_nowait(ResubscribeFailed("trade", "refused", self.generation))
        for channel, tickers in self.subscribed:
            if channel == "orderbook_delta":
                for n, ticker in enumerate(tickers, start=1):
                    self._put("orderbook_snapshot", {"market_ticker": ticker}, seq=n)

    def events(self) -> Any:
        feed = self

        class Stream:
            def __aiter__(self) -> "Stream":
                return self

            async def __anext__(self) -> Any:
                return await feed.queue.get()

        return Stream()

    def stats(self) -> dict[str, Any]:
        return {"generation": self.generation}


async def test_subscriptions_grow_until_refused_then_go_on_at_the_largest() -> None:
    feed = FakeFeed(limit=12)
    tickers = [f"M{i:02d}" for i in range(30)]
    result = await probe.subscribe_all(feed, "trade", tickers, batches=(2, 5, 10, 20))
    assert [len(t) for _, t in feed.subscribed] == [2, 5, 10, 10, 3]
    assert result["largest_accepted"] == 10 and result["accepted_batches"] == 5
    [refusal] = result["refused"]
    assert (refusal["markets"], refusal["code"]) == (13, 26)
    assert "Too many markets" in refusal["refused"]
    assert sum(len(t) for _, t in feed.subscribed) == 30


async def test_a_subscription_that_takes_no_more_is_full_and_stops_there() -> None:
    class Full(FakeFeed):
        """Joins every batch to one subscription that holds three markets at most."""

        async def subscribe(self, channel: str, market_tickers: list[str] | None = None) -> int:
            held = sum(len(t) for _, t in self.subscribed)
            if held + len(market_tickers or []) > 3:
                raise FeedError("subscription market limit", code=26)
            self.subscribed.append((channel, list(market_tickers or [])))
            return 9

    feed = Full()
    result = await probe.subscribe_all(feed, "trade", list("ABCDEF"), batches=(2, 4))
    assert result["full"] and result["subscribed_markets"] == 3 and result["sids"] == [9]
    assert [len(t) for _, t in feed.subscribed] == [2, 1]


async def test_a_link_failure_while_subscribing_ends_the_probe() -> None:
    class Broken(FakeFeed):
        async def subscribe(self, channel: str, market_tickers: list[str] | None = None) -> int:
            raise FeedError("connection closed")

    with pytest.raises(FeedError, match="connection closed"):
        await probe.subscribe_all(Broken(), "trade", ["A"])


async def test_too_many_attempts_end_the_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe, "MAX_ATTEMPTS", 2)
    with pytest.raises(FeedError, match="after 2 attempts"):
        await probe.subscribe_all(FakeFeed(limit=1), "trade", ["A", "B", "C", "D"], batches=(1,))


async def test_the_probe_counts_the_book_the_reconnect_and_keeps_a_sample(tmp_path: Path) -> None:
    feed = FakeFeed(limit=2)
    tickers = ["A", "B", "C"]
    result = await probe.run_probe(
        feed, tickers, seconds=0.3, reconnect_after=0.1, sample_seconds=60, out=tmp_path
    )
    assert result["markets"] == 3
    assert [s["channel"] for s in result["subscriptions"]] == ["orderbook_delta", "trade"]
    # three markets refused at once, then one a subscription: a delta for each of the three
    assert result["messages_by_type"] == {"orderbook_snapshot": 6, "orderbook_delta": 3}
    assert (result["book_messages"], result["book_messages_with_clock"]) == (9, 3)
    snapshots = result["snapshots"]
    assert (snapshots["markets_with_one"], snapshots["markets_without"]) == (3, 0)
    assert snapshots["sent_again_after_reconnect"] == 3 and snapshots["wait_seconds"]["max"] < 1
    assert result["reconnects"] == [
        {"generation": 2, "reason": "connection_lost", "downtime": 0.25}
    ]
    assert result["sequence_gaps"] == 1 and result["resubscribe_failures"] == ["trade: refused"]
    assert result["first_seq_after_reconnect"] == [1]
    assert result["per_second"]["max"] >= 1 and result["steady_messages_a_second"] > 0
    sample = pl.read_parquet(tmp_path / "sample.parquet")
    assert sample.height == 9 and set(sample["market_ticker"]) == {"A", "B", "C"}
    assert result["sample"]["messages"] == 9 and result["day"]["stored_bytes"] > 0


async def test_without_a_reconnect_or_a_sample(tmp_path: Path) -> None:
    result = await probe.run_probe(
        FakeFeed(clock=False), ["A"], seconds=0.05, reconnect_after=None, sample_seconds=0,
        out=tmp_path,
    )  # fmt: skip
    assert result["book_messages_with_clock"] == 0 and result["reconnects"] == []
    assert result["sample"]["messages"] == 0 and result["day"]["stored_bytes"] is None


async def test_silent_seconds_count_and_waits_run_from_the_start(tmp_path: Path) -> None:
    class Slow(FakeFeed):
        async def subscribe(self, channel: str, market_tickers: list[str] | None = None) -> int:
            await asyncio.sleep(0.15)  # longer than the reconnect point
            return await super().subscribe(channel, market_tickers)

    started = time.monotonic()
    result = await probe.run_probe(
        Slow(), ["A"], seconds=1.2, reconnect_after=0.1, sample_seconds=0, out=tmp_path
    )
    assert time.monotonic() - started < 1.6  # not stretched by the slow subscribe
    assert result["reconnects"] and result["seconds"] < 1.6
    # three messages (a snapshot and a delta, then the snapshot again) over two seconds, all in the
    # first: a mean of 1.5 a second, not 3
    assert result["per_second"]["mean"] == pytest.approx(3 / 2)


def test_quantiles_of_nothing_are_none() -> None:
    import numpy as np

    assert probe._quantiles(np.array([], dtype=float)) is None


# -- the command line ------------------------------------------------------------------------


def test_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    live, out = write_live(tmp_path), tmp_path / "out"
    feeds: list[FakeFeed] = []

    class Managed(FakeFeed):
        async def __aenter__(self) -> "Managed":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    def make(signer: object) -> Managed:
        made = Managed()
        feeds.append(made)
        return made

    monkeypatch.setattr(cli, "make_feed", make)
    monkeypatch.setattr(DataSigner, "from_environment", classmethod(lambda cls: object()))
    argv = ["probe", "--live", str(live), "--out", str(out)]
    timing = ["--minutes", "0.005", "--reconnect-at", "0.002", "--sample-minutes", "1"]
    assert cli.main([*argv, *timing, "--production"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["markets"] == 3 and printed["chosen"]["chosen"] == 3
    assert printed["feed"] == {"generation": 2}
    assert json.loads((out / "probe.json").read_text(encoding="utf-8")) == printed
    assert (out / "sample.parquet").exists()
    # refusals
    assert cli.main([*argv, *timing]) == 1
    assert "add --production" in capsys.readouterr().err
    assert cli.main([*argv, "--minutes", "1", "--reconnect-at", "2", "--production"]) == 1
    assert "inside --minutes" in capsys.readouterr().err
    assert cli.main(["probe", "--out", str(out), "--production"]) == 1
    assert "--live is required" in capsys.readouterr().err
    empty = tmp_path / "empty"
    (empty / "ticker").mkdir(parents=True)
    (empty / "trade").mkdir()
    assert cli.main(["probe", "--live", str(empty), "--out", str(out), "--production"]) == 1
    assert "no hours copied" in capsys.readouterr().err


def test_no_qualifying_market_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(probe, "THIN", 0)  # nothing is thin enough
    live = write_live(tmp_path)
    argv = ["probe", "--live", str(live), "--out", str(tmp_path / "o"), "--production"]
    assert cli.main(argv) == 1
    assert "no market qualifies" in capsys.readouterr().err


def test_the_feed_is_productions(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from sparq_core.config import WS_URLS, Environment

    feed = cli.make_feed(DataSigner("test-key", Ed25519PrivateKey.generate()))
    assert feed._config.url == WS_URLS[Environment.PRODUCTION]


def test_the_module_runs_and_refuses_bad_scratch_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["books", "--help"])
    with pytest.raises(SystemExit) as stop:
        runpy.run_module("sparq_analytics.books", run_name="__main__")
    assert stop.value.code == 0
    bad = tmp_path / "bad.toml"
    bad.write_text("threads = 1\n", encoding="utf-8")
    monkeypatch.setenv("SPARQ_ANALYTICS_CONFIG", str(bad))
    assert cli.main(["probe", "--production"]) == 1
    assert "unknown settings" in capsys.readouterr().err
