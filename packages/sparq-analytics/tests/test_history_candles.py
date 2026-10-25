from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pyarrow.parquet as pq
from fakekalshi import CUTOFF, FakeKalshi, Rig, market
from sparq_analytics.history.candles import (
    MAX_CANDLES,
    PERIOD_MINUTES,
    _slices,
    choose_markets,
    download_candles,
)

MakeRig = Callable[..., Rig]


def candle(ts: int, volume: str = "1.00") -> dict[str, Any]:
    return {
        "end_period_ts": ts,
        "volume": volume,
        "open_interest": "5.00",
        "price": {"open": "0.1000", "high": "0.2000", "low": "0.0900", "close": "0.1500"},
        "yes_bid": {"open": "0.0900", "high": "0.1000", "low": "0.0800", "close": "0.0900"},
        "yes_ask": {"open": "0.1100", "high": "0.1200", "low": "0.1000", "close": "0.1100"},
    }


def when(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def setup(fake: FakeKalshi) -> None:
    # the cutoff is 2026-10-20 12:00 in the fake: markets settled before it are historical
    fake.historical_markets = [
        market("KXOLD-26OCT19-T0", opened=when(19, 0), settled=when(19, 9)),
        market("KXOLD-26OCT19-T1", result="no", opened=when(19, 0), settled=when(19, 9)),
        market("KXOTHER-26OCT19-T0", opened=when(19, 0), settled=when(19, 9)),
        market("KXSCALAR-26OCT19-T0", result="scalar", opened=when(19, 0), settled=when(19, 9)),
    ]
    fake.live_markets = [market("KXNEW-26OCT21-T0", opened=when(21, 0), settled=when(21, 9))]
    base = int(when(19, 0).timestamp())
    for ticker in ("KXOLD-26OCT19-T0", "KXOLD-26OCT19-T1", "KXOTHER-26OCT19-T0"):
        fake.candles[ticker] = [candle(base + 3600 * h) for h in range(1, 10)]
    fake.candles["KXNEW-26OCT21-T0"] = [candle(int(when(21, 1).timestamp()))]


async def prepare(rig: MakeRig, fake: FakeKalshi) -> Rig:
    setup(fake)
    r = rig()
    await r.downloader.download_historical_markets()
    await r.downloader.download_live_markets()
    return r


def test_slices_split_a_long_life_into_requests_the_limit_allows() -> None:
    span = MAX_CANDLES * PERIOD_MINUTES * 60
    assert _slices(0, 100) == [(0, 100)]
    assert _slices(0, span) == [(0, span)]
    assert _slices(0, span + 1) == [(0, span), (span, span + 1)]
    assert _slices(5, 5) == [(5, 5)]
    assert len(_slices(0, 3 * span)) == 3


async def test_choosing_markets_keeps_yes_and_no_of_the_wanted_series(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    r = await prepare(rig, fake)
    root = r.downloader.dir.root
    everything = choose_markets(root, series_prefix=None, sample=None)
    assert [m["ticker"] for m in everything] == [
        "KXNEW-26OCT21-T0",
        "KXOLD-26OCT19-T0",
        "KXOLD-26OCT19-T1",
        "KXOTHER-26OCT19-T0",
    ]  # the scalar market is left out
    assert [m["ticker"] for m in choose_markets(root, series_prefix="KXOLD", sample=None)] == [
        "KXOLD-26OCT19-T0",
        "KXOLD-26OCT19-T1",
    ]
    sample = choose_markets(root, series_prefix=None, sample=2)
    assert len(sample) == 2
    assert sample == choose_markets(root, series_prefix=None, sample=2)  # reproducible
    assert len(choose_markets(root, series_prefix=None, sample=99)) == 4
    assert choose_markets(root.parent / "nowhere", series_prefix=None, sample=None) == []


async def test_candles_come_from_the_endpoint_that_serves_each_market(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    r = await prepare(rig, fake)
    before = len(fake.requests)
    fetched = await download_candles(r.downloader)
    assert fetched == 4
    assert fake.count("/historical/markets/KXOLD-26OCT19-T0/candlesticks") == 1
    assert fake.count("/series/KXNEW/markets/KXNEW-26OCT21-T0/candlesticks") == 1
    assert len(fake.requests) - before == 1 + 4  # the cutoff, then one request per market
    files = sorted((r.downloader.dir.root / "candles").glob("part-*.parquet"))
    rows = [row for f in files for row in pq.read_table(f).to_pylist()]
    assert len(rows) == 9 * 3 + 1
    assert {row["period_minutes"] for row in rows} == {60}
    assert rows[0]["price_high_e6"] == 200_000
    assert (r.downloader.dir.root / "candles" / "_done.txt").read_text().split() == [
        "KXNEW-26OCT21-T0",
        "KXOLD-26OCT19-T0",
        "KXOLD-26OCT19-T1",
        "KXOTHER-26OCT19-T0",
    ]


async def test_a_second_run_fetches_only_what_is_missing(rig: MakeRig, fake: FakeKalshi) -> None:
    r = await prepare(rig, fake)
    await download_candles(r.downloader, series_prefix="KXOLD")
    assert fake.count("/historical/markets/KXOTHER-26OCT19-T0") == 0
    again = await download_candles(r.downloader)
    assert again == 2  # KXNEW and KXOTHER; the two KXOLD markets are remembered
    assert await download_candles(r.downloader) == 0


async def test_a_market_older_than_the_candle_limit_is_fetched_in_slices(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    long_life = market(
        "KXLONG-25JAN01-T0",
        opened=when(19, 0) - timedelta(days=500),
        settled=when(19, 9),
    )
    fake.historical_markets = [long_life]
    r = rig()
    await r.downloader.download_historical_markets()
    assert await download_candles(r.downloader) == 1
    assert fake.count("/historical/markets/KXLONG-25JAN01-T0/candlesticks") == 3  # 500 days
    assert CUTOFF > 0


async def test_progress_is_logged_every_file_of_markets(rig: MakeRig, fake: FakeKalshi) -> None:
    fake.historical_markets = [
        market(f"KXM-{i}", opened=when(19, 0), settled=when(19, 9)) for i in range(205)
    ]
    r = rig()
    await r.downloader.download_historical_markets()
    assert await download_candles(r.downloader) == 205
    assert any(line == "candles: 200 markets fetched" for line in r.lines)
    assert len(list((r.downloader.dir.root / "candles").glob("part-*.parquet"))) <= 2
