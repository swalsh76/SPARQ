import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest
from fakekalshi import CUTOFF, FakeKalshi, Rig, market, trade
from sparq_analytics.history.downloader import (
    DuplicateTradesError,
    OutsideWindowError,
    day_segments,
    day_start,
    newest_complete_day,
    status_of,
)
from sparq_analytics.history.rates import RateLimitPersisting
from sparq_core.rest import KalshiAPIError

MakeRig = Callable[..., Rig]


def at(day: int, hour: int, minute: int = 0, second: int = 0, micro: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, second, micro, tzinfo=UTC)


def load_days(fake: FakeKalshi) -> None:
    """Trades on the 19th (historical), the 20th (straddling the cutoff) and the 21st (live)."""
    n = 0
    for day in (19, 20, 21):
        for hour in (0, 5, 11, 12, 13, 20, 23):
            for k in range(3):
                n += 1
                ticker = f"KXA-26OCT{day}-T{k}"
                fake.trades.append(trade(n, ticker, at(day, hour, k, 7, 123456 * (n % 2))))
    fake.trades.append(trade(900, "KXMVECROSS-X", at(21, 3)))  # a combo trade: skipped
    fake.trades.append(trade(901, "KXMVECROSS-Y", at(19, 3)))
    fake.trades.append(
        trade(902, "KXA-26OCT20-T0", at(20, 12, 0, 0))
    )  # exactly at the cutoff: live
    fake.trades.append(
        trade(903, "KXA-26OCT20-T0", at(20, 11, 59, 59, 999999))
    )  # just before: historical


# --- day arithmetic ---------------------------------------------------------------------------


def test_the_newest_complete_day_leaves_a_margin() -> None:
    noon = datetime(2026, 10, 22, 12, 0, tzinfo=UTC).timestamp()
    assert newest_complete_day(noon, 3600) == date(2026, 10, 21)
    just_after = datetime(2026, 10, 22, 0, 59, tzinfo=UTC).timestamp()
    assert newest_complete_day(just_after, 3600) == date(
        2026, 10, 20
    )  # 21st ended under an hour ago
    exactly = datetime(2026, 10, 22, 1, 0, tzinfo=UTC).timestamp()
    assert newest_complete_day(exactly, 3600) == date(2026, 10, 21)


def test_segments_follow_the_cutoff() -> None:
    cutoff = CUTOFF
    start = day_start(date(2026, 10, 20))
    assert [(s.side, s.start, s.end) for s in day_segments(date(2026, 10, 19), cutoff)] == [
        ("historical", start - 86400, start)
    ]
    assert [(s.side, s.start, s.end) for s in day_segments(date(2026, 10, 20), cutoff)] == [
        ("historical", start, cutoff),
        ("live", cutoff, start + 86400),
    ]
    assert [s.side for s in day_segments(date(2026, 10, 21), cutoff)] == ["live"]
    assert day_segments(date(2026, 10, 20), cutoff)[0].key == f"historical:{start}-{cutoff}"
    midnight = day_start(date(2026, 10, 21))
    assert [s.side for s in day_segments(date(2026, 10, 20), midnight)] == ["historical"]
    assert [s.side for s in day_segments(date(2026, 10, 21), midnight)] == ["live"]


# --- trades -----------------------------------------------------------------------------------


async def test_trades_are_downloaded_newest_day_first_and_split_at_the_cutoff(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    load_days(fake)
    r = rig()
    report = await r.downloader.download_trades()
    assert report.days_done == 3
    assert report.newest_day == "2026-10-21"
    assert report.oldest_day == "2026-10-19"
    assert "reached the start date" in report.stopped
    expected = {
        d: [
            t
            for t in fake.trades
            if t["created_time"].startswith(f"2026-10-{d}") and not t["ticker"].startswith("KXMVE")
        ]
        for d in (19, 20, 21)
    }
    for day in (19, 20, 21):
        table = pq.read_table(r.downloader.dir.trades_file(f"2026-10-{day}"))
        assert table.num_rows == len(expected[day])
        assert "KXMVE" not in "".join(table["ticker"].to_pylist())
    assert report.rows == sum(len(v) for v in expected.values())
    assert report.combos_skipped == 2
    # newest day first
    days_in_log = [line[:10] for line in r.lines if line.startswith("2026-")]
    assert days_in_log == ["2026-10-21", "2026-10-20", "2026-10-19"]
    # the work files are gone and no temporary file is left
    assert not (r.downloader.dir.work).exists() or not any(r.downloader.dir.work.rglob("*.parquet"))
    assert not list(r.downloader.dir.root.rglob("*.tmp"))


async def test_the_cutoff_second_belongs_to_the_live_side_and_nothing_is_lost(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    load_days(fake)
    r = rig()
    await r.downloader.download_trades()
    table = pq.read_table(r.downloader.dir.trades_file("2026-10-20"))
    ids = set(table["trade_id"].to_pylist())
    assert trade(902, "x", at(20, 12))["trade_id"] in ids  # at the cutoff second
    assert trade(903, "x", at(20, 11))["trade_id"] in ids  # a microsecond-fraction before it
    day_ids = [t["trade_id"] for t in fake.trades if t["created_time"].startswith("2026-10-20")]
    assert sorted(ids) == sorted(day_ids)  # every trade of the day exactly once


async def test_each_day_file_is_sorted_by_market_then_time(rig: MakeRig, fake: FakeKalshi) -> None:
    load_days(fake)
    r = rig()
    await r.downloader.download_trades()
    rows = pq.read_table(r.downloader.dir.trades_file("2026-10-21")).to_pylist()
    keys = [(row["ticker"], row["created_time"], row["trade_id"]) for row in rows]
    assert keys == sorted(keys)
    first = rows[0]
    assert first["yes_price_e6"] == 250_000
    assert first["count_e2"] == 100
    assert first["taker_side"] == "yes"
    assert first["is_block_trade"] is False


async def test_a_second_run_does_nothing_and_asks_only_for_the_cutoff(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    load_days(fake)
    r = rig()
    await r.downloader.download_trades()
    before = len(fake.requests)
    again = await rig().downloader.download_trades()
    assert again.days_done == 0
    assert again.days_skipped == 3
    assert len(fake.requests) == before + 1  # only the cutoff
    assert fake.count("/historical/cutoff") == 2


async def test_the_size_cap_stops_before_a_new_day_and_a_later_run_continues(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    load_days(fake)
    small = await rig(max_bytes=1).downloader.download_trades()
    assert (
        small.days_done == 1
    )  # the first day is always started; the cap is checked before the next
    assert "size cap reached" in small.stopped
    rest = await rig().downloader.download_trades()
    assert rest.days_done == 2
    assert rest.days_skipped == 1


async def test_the_number_of_new_days_can_be_limited(rig: MakeRig, fake: FakeKalshi) -> None:
    load_days(fake)
    report = await rig().downloader.download_trades(limit_days=2)
    assert report.days_done == 2
    assert "stopped after 2 day(s) as asked" in report.stopped


async def test_a_day_that_is_not_over_yet_is_left_for_a_later_run(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    fake.trades.append(trade(1, "KXA-1", at(22, 3)))  # the 22nd is today at the fake's clock
    report = await rig(since=date(2026, 10, 21)).downloader.download_trades()
    assert report.newest_day == "2026-10-21"
    assert not rig().downloader.dir.trades_file("2026-10-22").exists()


async def test_empty_days_in_a_row_mean_the_data_has_not_started(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    fake.trades.append(trade(1, "KXA-1", at(21, 3)))
    report = await rig(since=date(2026, 1, 1), empty_days_to_stop=4).downloader.download_trades()
    assert report.days_done == 5  # the 21st with data, then 4 empty days
    assert "4 empty days in a row" in report.stopped
    again = await rig(since=date(2026, 1, 1), empty_days_to_stop=4).downloader.download_trades()
    assert again.days_done == 0
    assert "4 empty days in a row" in again.stopped  # remembered from the finished empty days


async def test_an_interrupted_day_resumes_without_duplicates_or_losses(
    rig: MakeRig, fake: FakeKalshi, tmp_path: Path
) -> None:
    load_days(fake)
    reference = rig()
    await reference.downloader.download_trades()
    expected = pq.read_table(reference.downloader.dir.trades_file("2026-10-20"))

    fresh = FakeKalshi(trades=list(fake.trades), cutoff=fake.cutoff)
    fresh.crash_after = 9  # dies part-way through the first day
    folder = tmp_path / "resume"
    first = Rig(folder, fresh)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await first.downloader.download_trades()
    assert first.downloader.state.data.get("trades")  # some progress was saved
    fresh.crash_after = None
    second = Rig(folder, fresh)
    await second.downloader.download_trades()
    for day in (19, 20, 21):
        got = pq.read_table(second.downloader.dir.trades_file(f"2026-10-{day}"))
        want = pq.read_table(reference.downloader.dir.trades_file(f"2026-10-{day}"))
        assert got.equals(want), day
    assert pq.read_table(second.downloader.dir.trades_file("2026-10-20")).equals(expected)


async def test_a_refused_cursor_restarts_that_part_of_the_day(
    rig: MakeRig, fake: FakeKalshi, tmp_path: Path
) -> None:
    load_days(fake)
    reference = rig()
    await reference.downloader.download_trades(limit_days=1)
    want = pq.read_table(reference.downloader.dir.trades_file("2026-10-21"))

    crashing = FakeKalshi(trades=list(fake.trades), cutoff=fake.cutoff, crash_after=6)
    folder = tmp_path / "refused"
    with pytest.raises(RuntimeError):
        await Rig(folder, crashing).downloader.download_trades(limit_days=1)
    crashing.crash_after = None
    crashing.reject_cursors = True  # the saved cursor is no longer accepted
    resumed = Rig(folder, crashing)
    # with every cursor refused the restart itself fails on page two: the error is not swallowed
    with pytest.raises(KalshiAPIError) as caught:
        await resumed.downloader.download_trades(limit_days=1)
    assert caught.value.status == 400
    crashing.reject_cursors = False
    final = Rig(folder, crashing)
    await final.downloader.download_trades(limit_days=1)
    assert pq.read_table(final.downloader.dir.trades_file("2026-10-21")).equals(want)


async def test_a_cursor_refused_once_is_recovered_from(
    rig: MakeRig, fake: FakeKalshi, tmp_path: Path
) -> None:
    load_days(fake)
    reference = rig()
    await reference.downloader.download_trades(limit_days=1)
    want = pq.read_table(reference.downloader.dir.trades_file("2026-10-21"))

    flaky = FakeKalshi(trades=list(fake.trades), cutoff=fake.cutoff, crash_after=5)
    folder = tmp_path / "once"
    with pytest.raises(RuntimeError):
        await Rig(folder, flaky).downloader.download_trades(limit_days=1)
    flaky.crash_after = None
    original = flaky.handle
    refused = {"done": False}

    def handle(request: Any) -> Any:
        if request.url.params.get("cursor") and not refused["done"]:
            refused["done"] = True
            import httpx

            return httpx.Response(400, text="expired cursor")
        return original(request)

    flaky.handle = handle  # type: ignore[method-assign]
    resumed = Rig(folder, flaky)
    await resumed.downloader.download_trades(limit_days=1)
    assert any("cursor was refused" in line for line in resumed.lines)
    assert pq.read_table(resumed.downloader.dir.trades_file("2026-10-21")).equals(want)


async def test_duplicate_trade_ids_are_a_loud_error(rig: MakeRig, fake: FakeKalshi) -> None:
    fake.trades += [trade(1, "KXA-1", at(21, 3)), trade(1, "KXA-1", at(21, 4))]
    with pytest.raises(DuplicateTradesError, match="1 duplicate"):
        await rig(since=date(2026, 10, 21)).downloader.download_trades()


async def test_a_trade_outside_its_window_is_a_loud_error(rig: MakeRig, fake: FakeKalshi) -> None:
    fake.trades.append(trade(1, "KXA-1", at(21, 3)))
    original = fake.handle

    def shifted(request: Any) -> Any:
        response = original(request)
        if request.url.path.endswith("/markets/trades"):
            body = response.json()
            for row in body["trades"]:
                row["created_time"] = "2026-10-20T23:59:59Z"  # the day before the one asked for
            return httpx.Response(200, content=json.dumps(body).encode())
        return response

    fake.handle = shifted  # type: ignore[method-assign]
    with pytest.raises(OutsideWindowError):
        await rig(since=date(2026, 10, 21)).downloader.download_trades()


async def test_a_moved_cutoff_discards_the_old_partial_attempt(
    rig: MakeRig, fake: FakeKalshi, tmp_path: Path
) -> None:
    load_days(fake)
    crashing = FakeKalshi(trades=list(fake.trades), cutoff=fake.cutoff, crash_after=12)
    folder = tmp_path / "moved"
    with pytest.raises(RuntimeError):
        await Rig(folder, crashing).downloader.download_trades(limit_days=3)
    partial = Rig(folder, crashing).downloader.state.section("trades")["2026-10-20"]
    assert partial["status"] == "partial"  # the 20th was under way when the cutoff moved
    crashing.crash_after = None
    crashing.cutoff = day_start(date(2026, 10, 21))  # the cutoff moved forward overnight
    r = Rig(folder, crashing)
    await r.downloader.download_trades()
    got = pq.read_table(r.downloader.dir.trades_file("2026-10-20"))
    day_ids = [t["trade_id"] for t in fake.trades if t["created_time"].startswith("2026-10-20")]
    assert sorted(got["trade_id"].to_pylist()) == sorted(day_ids)
    # only the segments of the new cutoff are recorded: nothing stale from the earlier attempt
    start = day_start(date(2026, 10, 20))
    recorded = r.downloader.state.section("trades")["2026-10-20"]["segments"]
    assert recorded == [f"historical:{start}-{start + 86400}"]


# --- back-off ---------------------------------------------------------------------------------


async def test_a_429_slows_the_run_down_and_it_still_completes(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    load_days(fake)
    fake.limit_every = 7
    r = rig(trades_rate=8.0, live_rate=8.0)
    report = await r.downloader.download_trades()
    assert report.days_done == 3
    assert r.downloader.trades_rate.slowdowns + r.downloader.live_rate.slowdowns >= 1


async def test_constant_429s_stop_the_run_with_a_clear_error(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    load_days(fake)
    fake.limit_every = 1
    fake.limit_after = 1  # the cutoff request is served, then everything is refused
    r = rig()
    with pytest.raises(RateLimitPersisting, match="rate-limit responses"):
        await r.downloader.download_trades()
    assert min(r.downloader.live_rate.rate, r.downloader.trades_rate.rate) < 1000


async def test_constant_429s_also_stop_the_series_and_markets_downloads(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    fake.limit_every = 1
    for step in ("download_series", "download_historical_markets"):
        with pytest.raises(RateLimitPersisting):
            await getattr(rig().downloader, step)()
    fake.limit_after = len(fake.requests) + 1  # serve the cutoff request, refuse the rest
    fake.live_markets = [market("KXL-A", opened=at(20, 0), settled=at(20, 13))]
    with pytest.raises(RateLimitPersisting):
        await rig().downloader.download_live_markets()


# --- series -----------------------------------------------------------------------------------


async def test_series_and_fee_changes_are_saved(rig: MakeRig, fake: FakeKalshi) -> None:
    fake.series = [
        {
            "ticker": "KXA",
            "title": "A",
            "category": "Sports",
            "frequency": "daily",
            "tags": None,
            "fee_type": "quadratic",
            "fee_multiplier": 1,
            "exchange_index": 0,
        },
        {
            "ticker": "KXB",
            "title": "B",
            "category": "Crypto",
            "frequency": "hourly",
            "tags": ["x"],
            "fee_type": "quadratic_with_maker_fees",
            "fee_multiplier": 0.5,
            "exchange_index": 2,
        },
    ]
    fake.fee_changes = [
        {
            "id": "f1",
            "series_ticker": "KXB",
            "fee_type": "quadratic_with_maker_fees",
            "fee_multiplier": 1,
            "scheduled_ts": "2026-09-03T18:35:23Z",
        }
    ]
    r = rig()
    assert await r.downloader.download_series() == (2, 1)
    series = pq.read_table(r.downloader.dir.root / "series.parquet").to_pylist()
    assert [(s["ticker"], s["category"], s["fee_multiplier_e6"], s["tags"]) for s in series] == [
        ("KXA", "Sports", 1_000_000, []),
        ("KXB", "Crypto", 500_000, ["x"]),
    ]
    changes = pq.read_table(r.downloader.dir.root / "fee_changes.parquet").to_pylist()
    assert changes[0]["fee_multiplier_e6"] == 1_000_000
    assert r.downloader.state.section("series")["series"] == 2


# --- markets ----------------------------------------------------------------------------------


def historical(n: int) -> list[dict[str, Any]]:
    return [
        market(
            f"KXH{i // 3}-{i}",
            result="yes" if i % 2 else "no",
            opened=at(10, 0),
            settled=at(10, 1 + i % 5),
        )
        for i in range(n)
    ]


async def test_historical_markets_are_saved_in_part_files_with_labels_and_raw_json(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    fake.historical_markets = historical(9) + [
        market("KXS-1", result="scalar", opened=at(10, 0), settled=at(10, 2)),
        market("KXV-1", result="", opened=at(10, 0), settled=at(10, 2)),
    ]
    r = rig()
    assert await r.downloader.download_historical_markets() == 11
    files = sorted((r.downloader.dir.root / "markets" / "historical").glob("part-*.parquet"))
    assert len(files) == 3  # 11 markets, 2 per page, 2 pages per file
    rows = [row for f in files for row in pq.read_table(f).to_pylist()]
    assert len(rows) == 11
    by_ticker = {row["ticker"]: row for row in rows}
    assert by_ticker["KXS-1"]["label"] == "scalar"
    assert by_ticker["KXV-1"]["label"] == "other_settled"
    assert by_ticker["KXH0-1"]["label"] == "yes"
    assert by_ticker["KXH0-1"]["series_ticker"] == "KXH0"
    assert by_ticker["KXH0-1"]["source"] == "historical"
    assert '"ticker":"KXH0-1"' in by_ticker["KXH0-1"]["raw_json"]
    assert r.downloader.state.section("markets_historical")["done"] is True
    # done: a second run asks for nothing
    count = len(fake.requests)
    assert await rig().downloader.download_historical_markets() == 11
    assert len(fake.requests) == count


async def test_an_interrupted_markets_listing_resumes(
    rig: MakeRig, fake: FakeKalshi, tmp_path: Path
) -> None:
    fake.historical_markets = historical(14)
    crashing = FakeKalshi(historical_markets=list(fake.historical_markets), crash_after=4)
    folder = tmp_path / "markets"
    with pytest.raises(RuntimeError):
        await Rig(folder, crashing).downloader.download_historical_markets()
    crashing.crash_after = None
    r = Rig(folder, crashing)
    assert await r.downloader.download_historical_markets() == 14
    tickers = [
        row["ticker"]
        for f in sorted((folder / "markets" / "historical").glob("*.parquet"))
        for row in pq.read_table(f).to_pylist()
    ]
    assert sorted(tickers) == sorted(m["ticker"] for m in fake.historical_markets)


async def test_a_refused_markets_cursor_starts_the_listing_again(
    rig: MakeRig, fake: FakeKalshi, tmp_path: Path
) -> None:
    fake.historical_markets = historical(10)
    crashing = FakeKalshi(historical_markets=list(fake.historical_markets), crash_after=4)
    folder = tmp_path / "markets-refused"
    with pytest.raises(RuntimeError):
        await Rig(folder, crashing).downloader.download_historical_markets()
    crashing.crash_after = None
    original = crashing.handle
    refused = {"done": False}

    def handle(request: Any) -> Any:
        if request.url.params.get("cursor") and not refused["done"]:
            refused["done"] = True
            import httpx

            return httpx.Response(400, text="expired")
        return original(request)

    crashing.handle = handle  # type: ignore[method-assign]
    r = Rig(folder, crashing)
    assert await r.downloader.download_historical_markets() == 10
    assert any("cursor was refused" in line for line in r.lines)
    tickers = [
        row["ticker"]
        for f in sorted((folder / "markets" / "historical").glob("*.parquet"))
        for row in pq.read_table(f).to_pylist()
    ]
    assert sorted(tickers) == sorted(m["ticker"] for m in fake.historical_markets)


async def test_a_markets_error_other_than_a_refused_cursor_is_not_swallowed(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    fake.historical_markets = historical(4)
    fake.fail_status = 403
    r = rig()
    r.downloader.state.section("markets_historical").update(cursor="2", files=1, rows=2)
    with pytest.raises(KalshiAPIError) as caught:
        await r.downloader.download_historical_markets()
    assert caught.value.status == 403


async def test_live_markets_are_fetched_one_settlement_day_at_a_time(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    fake.live_markets = [
        market("KXL-A", opened=at(20, 0), settled=at(20, 12, 0, 0)),  # exactly at the cutoff
        market("KXL-B", opened=at(20, 0), settled=at(20, 11, 59, 59)),  # before it: historical
        market("KXL-C", opened=at(20, 0), settled=at(20, 18)),
        market("KXL-D", opened=at(21, 0), settled=at(21, 0, 0, 0)),  # midnight belongs to the 21st
        market("KXL-E", result="no", opened=at(21, 0), settled=at(21, 9)),
        market("KXL-F", opened=at(22, 0), settled=at(22, 2)),  # today: not yet
    ]
    r = rig()
    total = await r.downloader.download_live_markets()
    assert total == 4  # A, C on the 20th (B is before the cutoff); D, E on the 21st
    day20 = pq.read_table(r.downloader.dir.live_markets_file("2026-10-20")).to_pylist()
    assert sorted(row["ticker"] for row in day20) == ["KXL-A", "KXL-C"]
    assert all(row["source"] == "live" for row in day20)
    day21 = pq.read_table(r.downloader.dir.live_markets_file("2026-10-21")).to_pylist()
    assert sorted(row["ticker"] for row in day21) == ["KXL-D", "KXL-E"]
    count = len(fake.requests)
    assert await rig().downloader.download_live_markets() == 4
    assert len(fake.requests) == count + 1  # only the cutoff again


# --- status and housekeeping -----------------------------------------------------------------


async def test_status_reports_what_is_in_the_folder(rig: MakeRig, fake: FakeKalshi) -> None:
    load_days(fake)
    fake.historical_markets = historical(3)
    r = rig()
    await r.downloader.download_trades()
    await r.downloader.download_historical_markets()
    status = r.downloader.status()
    assert status["trade_days_done"] == 3
    assert status["newest_day"] == "2026-10-21"
    assert status["oldest_day"] == "2026-10-19"
    assert status["combo_trades_skipped"] == 2
    assert status["historical_markets"] == {"done": True, "rows": 3}
    assert r.downloader.dir.bytes_used() > 0
    assert status_of(r.downloader.dir, r.downloader.state, 10**9)["cap_gigabytes"] == 1.0
    assert r.downloader.now() > 0


async def test_leftover_temporary_files_are_removed_at_start(
    rig: MakeRig, fake: FakeKalshi, tmp_path: Path
) -> None:
    folder = tmp_path / "kalshi" / "trades" / "date=2026-10-21"
    folder.mkdir(parents=True)
    (folder / "trades.parquet.tmp").write_bytes(b"half a file")
    r = rig()
    assert not (folder / "trades.parquet.tmp").exists()
    assert r.downloader.dir.remove_stale_temporaries() == 0


async def test_a_state_file_from_another_version_is_refused(tmp_path: Path) -> None:
    from sparq_analytics.history.datadir import State

    (tmp_path / "state.json").write_text('{"version": 99}', encoding="utf-8")
    with pytest.raises(ValueError, match="state version"):
        State(tmp_path / "state.json")


async def test_the_clients_are_closed_with_the_downloader(rig: MakeRig) -> None:
    r = rig()
    async with r.downloader as d:
        assert d is r.downloader
    assert r.downloader.live_data._client._http.is_closed  # noqa: SLF001
    assert not (day_start(date(2026, 10, 21)) % 86400)
    assert isinstance(timedelta(days=1), timedelta)


@pytest.mark.parametrize("crash_after", [12, 16])
async def test_a_day_split_at_the_cutoff_resumes_inside_either_half(
    rig: MakeRig, fake: FakeKalshi, tmp_path: Path, crash_after: int
) -> None:
    load_days(fake)
    reference = rig()
    await reference.downloader.download_trades()
    want = pq.read_table(reference.downloader.dir.trades_file("2026-10-20"))

    crashing = FakeKalshi(trades=list(fake.trades), cutoff=fake.cutoff, crash_after=crash_after)
    folder = tmp_path / f"half-{crash_after}"
    with pytest.raises(RuntimeError):
        await Rig(folder, crashing).downloader.download_trades()
    saved = Rig(folder, crashing).downloader.state.section("trades")["2026-10-20"]
    assert saved["status"] == "partial"  # the crash really did land inside the straddling day
    crashing.crash_after = None
    final = Rig(folder, crashing)
    await final.downloader.download_trades()
    assert pq.read_table(final.downloader.dir.trades_file("2026-10-20")).equals(want)


async def test_the_real_client_construction_uses_the_two_rates(tmp_path: Path) -> None:
    from sparq_analytics.history.downloader import DownloadConfig, Downloader

    fake = FakeKalshi()
    config = DownloadConfig(data_dir=tmp_path / "kalshi", trades_rate=8.0, live_rate=2.0)
    async with Downloader.create(config, transport=fake.transport(), log=lambda line: None) as d:
        assert d.trades_rate.rate == 8.0
        assert d.live_rate.rate == 2.0
        assert d.trades_data._client.bucket.rate == 80.0  # noqa: SLF001
        assert d.live_data._client.bucket.rate == 20.0  # noqa: SLF001
        assert d.trades_data._client is not d.live_data._client  # noqa: SLF001
        assert (await d.cutoffs())[0] == fake.cutoff
