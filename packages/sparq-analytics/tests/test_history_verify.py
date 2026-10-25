from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from fakekalshi import FakeKalshi, Rig, market, trade
from sparq_analytics.history.datadir import write_table_atomic
from sparq_analytics.history.verify import verify

MakeRig = Callable[..., Rig]


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def populate(fake: FakeKalshi) -> None:
    """Day 21 fully downloaded. Markets: A (all inside, volume = its trades), B (opened on the
    20th, so not checkable), C (volume 1 too high: a mismatch), D (a scalar, checked too)."""
    fake.trades = [
        trade(1, "KXA-26OCT21-T0", at(21, 1), count=150),
        trade(2, "KXA-26OCT21-T0", at(21, 2), count=250),
        trade(3, "KXC-26OCT21-T0", at(21, 3), count=100),
        trade(4, "KXD-26OCT21-T0", at(21, 4), count=700),
        trade(5, "KXB-26OCT21-T0", at(21, 5), count=900),
    ]
    fake.live_markets = [
        market("KXA-26OCT21-T0", opened=at(21, 0), settled=at(21, 9), volume=400),
        market("KXB-26OCT21-T0", opened=at(20, 20), settled=at(21, 9), volume=900),
        market("KXC-26OCT21-T0", opened=at(21, 0), settled=at(21, 9), volume=101),
        market("KXD-26OCT21-T0", result="scalar", opened=at(21, 0), settled=at(21, 9), volume=700),
        market("KXE-26OCT21-T0", result="", opened=at(21, 0), settled=at(21, 9), volume=0),
    ]


async def run(rig: MakeRig, fake: FakeKalshi) -> Rig:
    r = rig(since=date(2026, 10, 21))
    await r.downloader.download_trades()
    await r.downloader.download_live_markets()
    return r


async def test_volume_reconciliation_matches_mismatches_and_skips(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    populate(fake)
    r = await run(rig, fake)
    report = verify(r.downloader.dir, r.downloader.state)
    assert report.days_checked == 1
    assert report.day_problems == []
    assert report.market_rows == 5
    assert report.reconcile_checked == 3  # A, C and the scalar D
    assert report.reconcile_matched == 2
    assert report.reconcile_mismatch_count == 1
    assert report.reconcile_mismatched == [("KXC-26OCT21-T0", 101, 100)]
    assert report.not_checkable == 1  # B opened on a day that was not downloaded
    assert dict(report.labels) == {"yes": 3, "scalar": 1, "other_settled": 1}
    assert not report.ok
    text = "\n".join(report.lines())
    assert "mismatch: KXC-26OCT21-T0: market volume 101 against 100" in text
    assert "1 mismatched" in text


async def test_everything_in_order_is_ok(rig: MakeRig, fake: FakeKalshi) -> None:
    populate(fake)
    fake.live_markets = [m for m in fake.live_markets if m["ticker"][:3] in ("KXA", "KXD")]
    r = await run(rig, fake)
    report = verify(r.downloader.dir, r.downloader.state)
    assert report.ok
    assert report.reconcile_matched == 2


async def test_a_market_without_a_volume_or_times_is_not_checkable(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    populate(fake)
    no_volume = market("KXF-26OCT21-T0", opened=at(21, 0), settled=at(21, 9))
    del no_volume["volume_fp"]
    no_open = market("KXG-26OCT21-T0", opened=at(21, 0), settled=at(21, 9), volume=5)
    del no_open["open_time"]
    fake.live_markets = [no_volume, no_open]
    r = await run(rig, fake)
    report = verify(r.downloader.dir, r.downloader.state)
    assert report.not_checkable == 2
    assert report.reconcile_checked == 0


async def test_a_market_that_lived_longer_than_a_year_is_not_checkable(
    rig: MakeRig, fake: FakeKalshi
) -> None:
    populate(fake)
    old = market(
        "KXH-26OCT21-T0", opened=datetime(2024, 1, 1, tzinfo=UTC), settled=at(21, 9), volume=5
    )
    fake.live_markets = [old]
    r = await run(rig, fake)
    assert verify(r.downloader.dir, r.downloader.state).not_checkable == 1


async def test_duplicate_markets_across_files_are_reported(rig: MakeRig, fake: FakeKalshi) -> None:
    populate(fake)
    fake.live_markets = [market("KXA-26OCT21-T0", opened=at(21, 0), settled=at(21, 9), volume=400)]
    fake.historical_markets = [
        market("KXA-26OCT21-T0", opened=at(21, 0), settled=at(21, 9), volume=400)
    ]
    r = await run(rig, fake)
    await r.downloader.download_historical_markets()
    report = verify(r.downloader.dir, r.downloader.state)
    assert report.duplicate_markets == 1
    assert not report.ok


async def test_a_damaged_day_file_is_reported(rig: MakeRig, fake: FakeKalshi) -> None:
    populate(fake)
    r = await run(rig, fake)
    path = r.downloader.dir.trades_file("2026-10-21")
    table = pq.read_table(path)
    rows: list[dict[str, Any]] = table.to_pylist()
    rows.append({**rows[0], "created_time": at(22, 1)})  # a duplicate id, outside the day
    rows.append({**rows[1], "trade_id": "x", "yes_price_e6": 2_000_000})
    write_table_atomic(pa.Table.from_pylist(rows, schema=table.schema), path)
    report = verify(r.downloader.dir, r.downloader.state)
    problems = "\n".join(report.day_problems)
    assert "file has 7 rows, state says 5" in problems
    assert "duplicate trade ids" in problems
    assert "fall outside the day" in problems
    assert "a yes price outside 0 to 1 dollar" in problems
    assert not report.ok


async def test_an_empty_folder_verifies_clean(rig: MakeRig) -> None:
    r = rig()
    report = verify(r.downloader.dir, r.downloader.state)
    assert report.ok
    assert report.days_checked == 0
    assert report.market_rows == 0


async def test_a_day_with_no_trades_verifies_clean(rig: MakeRig, fake: FakeKalshi) -> None:
    fake.trades = [trade(1, "KXA-26OCT20-T0", at(20, 3))]  # the 21st is empty
    r = rig(since=date(2026, 10, 21))
    await r.downloader.download_trades()
    report = verify(r.downloader.dir, r.downloader.state)
    assert report.days_checked == 1
    assert report.ok


async def test_only_the_first_few_mismatches_are_listed(rig: MakeRig, fake: FakeKalshi) -> None:
    fake.live_markets = [
        market(f"KXM{i:02d}-26OCT21-T0", opened=at(21, 0), settled=at(21, 9), volume=5)
        for i in range(13)
    ]
    r = rig(since=date(2026, 10, 21))
    await r.downloader.download_trades()
    await r.downloader.download_live_markets()
    report = verify(r.downloader.dir, r.downloader.state)
    assert report.reconcile_mismatch_count == 13
    assert len(report.reconcile_mismatched) == 10
