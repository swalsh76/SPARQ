"""Discovery against a real PostgreSQL: full and incremental cycles, history, rejects, retries."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from dbhelpers import dsn
from sparq_core.endpoints import TypedPage
from sparq_core.models import Market, Series, SeriesFeeChange
from sparq_server.ingest import records, state
from sparq_server.ingest.discovery import Discovery, DiscoveryConfig, DiscoveryRunner

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def market_json(ticker: str = "KXTEST-26OCT24-A", **changes: Any) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0],
        "market_type": "binary",
        "status": "open",
        "result": "",
        "title": "A test market",
        "yes_sub_title": "Yes",
        "no_sub_title": "No",
        "rules_primary": "If so, Yes.",
        "rules_secondary": "",
        "created_time": "2026-10-20T12:00:00Z",
        "open_time": "2026-10-21T12:00:00Z",
        "close_time": "2026-10-25T17:00:00Z",
        "expected_expiration_time": "2026-10-25T18:00:00Z",
        "expiration_time": "2026-11-01T17:00:00Z",
        "latest_expiration_time": "2026-11-01T17:00:00Z",
        "updated_time": "2026-10-24T11:59:00Z",
        "yes_bid_dollars": "0.5100",
        "yes_ask_dollars": "0.5200",
        "last_price_dollars": "0.5300",
        "volume_fp": "100.00",
        "open_interest_fp": "40.00",
        "price_level_structure": "linear_cent",
        "exchange_index": 0,
        "can_close_early": True,
        **changes,
    }


def pair(ticker: str = "KXTEST-26OCT24-A", **changes: Any) -> tuple[Market, dict[str, Any]]:
    raw = market_json(ticker, **changes)
    return Market.model_validate(raw), raw


def page_of(
    items: list[tuple[Market, dict[str, Any]]], bad: list[dict[str, Any]] | None = None
) -> TypedPage[Market]:
    return TypedPage(
        [m for m, _ in items],
        [r for _, r in items],
        None,
        None,
        [(row, "cannot read Market") for row in bad or []],
    )


class FakeData:
    """Stands in for MarketData: scripted market listings, and a record of what was asked."""

    def __init__(self) -> None:
        self.by_status: dict[str | None, list[TypedPage[Market]]] = {}
        self.calls: list[dict[str, Any]] = []
        self.series_calls = 0
        self.series = [
            Series.model_validate(
                {
                    "ticker": "KXTEST",
                    "title": "Test",
                    "category": "Sports",
                    "tags": None,
                    "fee_type": "quadratic",
                    "fee_multiplier": 1,
                }
            )
        ]
        self.fee_changes = [
            SeriesFeeChange.model_validate(
                {
                    "id": "fc-1",
                    "series_ticker": "KXTEST",
                    "fee_type": "quadratic",
                    "fee_multiplier": 1,
                    "scheduled_ts": "2026-11-01T00:00:00Z",
                }
            )
        ]
        self.fail_on: str | None = None

    def script(self, status: str | None, *pages: TypedPage[Market]) -> None:
        self.by_status[status] = list(pages)

    async def markets(self, **kwargs: Any) -> AsyncIterator[TypedPage[Market]]:
        self.calls.append(kwargs)
        status = kwargs.get("status")
        if self.fail_on is not None and self.fail_on == status:
            raise RuntimeError("Kalshi is unavailable")
        for page in self.by_status.get(status, []):
            yield page

    async def series_list(self) -> list[Series]:
        self.series_calls += 1
        return self.series

    async def series_fee_changes(self) -> list[SeriesFeeChange]:
        return self.fee_changes


class Clock:
    def __init__(self, at: datetime = NOW) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def discovery_for(url: str, data: FakeData, clock: Clock, **config: Any) -> Discovery:
    async def connect() -> asyncpg.Connection:
        return await asyncpg.connect(dsn(url))

    return Discovery(data, connect, DiscoveryConfig(**config), clock=clock)  # type: ignore[arg-type]


async def rows(url: str, statement: str, *args: Any) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn(url))
    try:
        return list(await conn.fetch(statement, *args))
    finally:
        await conn.close()


async def bookmark(url: str, key: str) -> int | None:
    conn = await asyncpg.connect(dsn(url))
    try:
        return await state.get_int(conn, key)
    finally:
        await conn.close()


def scripted() -> FakeData:
    data = FakeData()
    data.script("unopened", page_of([pair("KXTEST-26OCT25-A", status="unopened")]))
    data.script("open", page_of([pair("KXTEST-26OCT24-A"), pair("KXTEST-26OCT24-B")]))
    data.script("closed", page_of([pair("KXTEST-26OCT23-A", status="closed")]))
    data.script(
        "settled",
        page_of(
            [
                pair(
                    "KXTEST-26OCT22-A",
                    status="settled",
                    result="yes",
                    settlement_value_dollars="1.0000",
                )
            ]
        ),
    )
    return data


# --- the first, full cycle ----------------------------------------------------------------------


async def test_the_first_cycle_is_full_and_stores_markets_series_and_fee_changes(
    migrated: str,
) -> None:
    data, clock = scripted(), Clock()
    report = await discovery_for(migrated, data, clock).cycle()
    assert report.mode == "full"
    assert (report.markets.fetched, report.markets.inserted, report.markets.updated) == (5, 5, 0)
    assert (report.series.inserted, report.fee_changes.inserted) == (1, 1)
    assert [c.get("status") for c in data.calls] == ["unopened", "open", "closed", "settled"]
    assert data.calls[3]["min_settled_ts"] == int((NOW - timedelta(days=7)).timestamp())
    assert all(c["mve_filter"] == "exclude" and c["tolerant"] is True for c in data.calls)
    market = (await rows(migrated, "SELECT * FROM market WHERE ticker = 'KXTEST-26OCT22-A'"))[0]
    assert (market["label"], market["source"], market["venue"], market["series_ticker"]) == (
        "yes",
        "live",
        "kalshi",
        "KXTEST",
    )
    assert market["settlement_value_e6"] == 1_000_000 and market["yes_bid_e6"] == 510_000
    assert json.loads(market["raw_json"])["ticker"] == "KXTEST-26OCT22-A"
    assert (await rows(migrated, "SELECT tags FROM series"))[0]["tags"] == []


async def test_the_bookmarks_are_set_a_little_back_only_after_the_cycle(migrated: str) -> None:
    await discovery_for(migrated, scripted(), Clock()).cycle()
    assert await bookmark(migrated, state.UPDATED_SINCE) == int(
        (NOW - timedelta(minutes=2)).timestamp()
    )
    assert await bookmark(migrated, state.LAST_FULL) == int(NOW.timestamp())


async def test_every_new_market_gets_a_discovered_history_row(migrated: str) -> None:
    await discovery_for(migrated, scripted(), Clock()).cycle()
    history = await rows(migrated, "SELECT ticker, event_type, source FROM market_lifecycle")
    assert len(history) == 5 and {(r["event_type"], r["source"]) for r in history} == {
        ("discovered", "discovery")
    }


# --- the cycles after it ------------------------------------------------------------------------


async def test_the_next_cycle_is_incremental_and_asks_only_for_what_changed(migrated: str) -> None:
    data, clock = scripted(), Clock()
    discovery = discovery_for(migrated, data, clock)
    await discovery.cycle()
    data.calls.clear()
    clock.at = NOW + timedelta(minutes=1)
    data.script(None, page_of([pair("KXTEST-26OCT24-A", yes_bid_dollars="0.5500")]))
    report = await discovery.cycle()
    assert (
        report.mode == "incremental" and data.series_calls == 1
    )  # the big list is not fetched again
    assert len(data.calls) == 1 and "status" not in data.calls[0]
    assert data.calls[0]["min_updated_ts"] == int((NOW - timedelta(minutes=2)).timestamp())
    assert (report.markets.fetched, report.markets.updated, report.markets.inserted) == (1, 1, 0)


async def test_a_change_to_a_tracked_field_is_recorded_with_what_it_was(migrated: str) -> None:
    data, clock = scripted(), Clock()
    discovery = discovery_for(migrated, data, clock)
    await discovery.cycle()
    clock.at = NOW + timedelta(minutes=1)
    data.script(
        None,
        page_of(
            [
                pair(
                    "KXTEST-26OCT24-A",
                    status="closed",
                    close_time="2026-10-24T12:30:00Z",
                    rules_primary="If not so, No.",
                )
            ]
        ),
    )
    report = await discovery.cycle()
    assert report.history_rows == 1
    row = (await rows(migrated, "SELECT * FROM market_lifecycle WHERE event_type = 'changed'"))[0]
    changed = json.loads(row["details"])["changed"]
    assert set(changed) == {"status", "close_time", "rules_primary"}
    assert changed["status"] == ["open", "closed"] and changed["rules_primary"] == [
        "If so, Yes.",
        "If not so, No.",
    ]
    assert row["observed_at"] == clock.at and row["source"] == "discovery"


async def test_a_change_to_prices_or_volume_alone_is_an_update_but_not_history(
    migrated: str,
) -> None:
    data, clock = scripted(), Clock()
    discovery = discovery_for(migrated, data, clock)
    await discovery.cycle()
    clock.at = NOW + timedelta(minutes=1)
    data.script(
        None, page_of([pair("KXTEST-26OCT24-A", volume_fp="250.00", yes_ask_dollars="0.5400")])
    )
    report = await discovery.cycle()
    assert (report.markets.updated, report.history_rows) == (1, 0)
    assert (await rows(migrated, "SELECT volume_e2 FROM market WHERE ticker = 'KXTEST-26OCT24-A'"))[
        0
    ][0] == 25_000


async def test_a_market_fetched_again_unchanged_is_not_rewritten(migrated: str) -> None:
    data, clock = scripted(), Clock()
    discovery = discovery_for(migrated, data, clock)
    await discovery.cycle()
    clock.at = NOW + timedelta(minutes=1)
    data.script(None, page_of([pair("KXTEST-26OCT24-A")]))
    report = await discovery.cycle()
    assert (report.markets.fetched, report.markets.unchanged, report.history_rows) == (1, 1, 0)


async def test_a_full_cycle_comes_round_again_after_a_day(migrated: str) -> None:
    data, clock = scripted(), Clock()
    discovery = discovery_for(migrated, data, clock)
    await discovery.cycle()
    clock.at = NOW + timedelta(hours=23)
    assert (await discovery.cycle()).mode == "incremental"
    clock.at = NOW + timedelta(hours=24, seconds=1)
    assert (await discovery.cycle()).mode == "full" and data.series_calls == 2
    assert (await discovery.cycle(full=True)).mode == "full"


async def test_series_and_fee_changes_are_updated_when_they_change_not_when_only_refetched(
    migrated: str,
) -> None:
    data, clock = scripted(), Clock()
    discovery = discovery_for(migrated, data, clock)
    await discovery.cycle()
    clock.at = NOW + timedelta(hours=25)
    again = await discovery.cycle()
    assert (again.series.updated, again.fee_changes.updated) == (0, 0)  # only fetched_at moved
    data.series = [
        Series.model_validate(
            {
                "ticker": "KXTEST",
                "title": "Renamed",
                "category": "Sports",
                "tags": ["a"],
                "fee_type": "quadratic",
                "fee_multiplier": 1,
            }
        )
    ]
    clock.at = NOW + timedelta(hours=50)
    changed = await discovery.cycle()
    assert changed.series.updated == 1
    assert (await rows(migrated, "SELECT title, tags FROM series"))[0]["title"] == "Renamed"


# --- when things go wrong -----------------------------------------------------------------------


async def test_a_failed_cycle_leaves_the_bookmarks_and_is_retried_from_the_same_point(
    migrated: str,
) -> None:
    data, clock = scripted(), Clock()
    data.fail_on = "closed"
    discovery = discovery_for(migrated, data, clock)
    with pytest.raises(RuntimeError):
        await discovery.cycle()
    assert (
        await bookmark(migrated, state.UPDATED_SINCE) is None
        and await bookmark(migrated, state.LAST_FULL) is None
    )
    data.fail_on = None
    report = await discovery.cycle()
    assert report.mode == "full" and report.markets.fetched == 5
    assert await bookmark(migrated, state.LAST_FULL) == int(NOW.timestamp())
    count = (await rows(migrated, "SELECT count(*) FROM market"))[0][0]
    assert count == 5  # what the failed cycle had already stored was not stored twice


async def test_a_market_that_does_not_fit_the_model_is_set_aside_and_the_cycle_succeeds(
    migrated: str,
) -> None:
    data, clock = scripted(), Clock()
    odd = {"ticker": "KXTEST-ODD", "volume_fp": "1.001"}
    data.script("open", page_of([pair("KXTEST-26OCT24-A")], bad=[odd]))
    report = await discovery_for(migrated, data, clock).cycle()
    assert report.markets.rejected == 1 and report.markets.fetched == 4
    reject = (await rows(migrated, "SELECT kind, reason, payload FROM ingest_reject"))[0]
    assert reject["kind"] == "market" and json.loads(reject["payload"])["ticker"] == "KXTEST-ODD"
    assert await bookmark(migrated, state.LAST_FULL) == int(NOW.timestamp())


async def test_combo_markets_are_never_stored(migrated: str) -> None:
    data = scripted()
    data.script("open", page_of([pair("KXTEST-26OCT24-A"), pair("KXMVECROSS-1-A")]))
    report = await discovery_for(migrated, data, Clock()).cycle()
    assert report.markets.fetched == 4
    assert (await rows(migrated, "SELECT count(*) FROM market WHERE ticker LIKE 'KXMVE%'"))[0][
        0
    ] == 0


async def test_a_market_listed_twice_in_a_chunk_is_stored_once_with_the_last_version(
    migrated: str,
) -> None:
    data = scripted()
    data.script(
        "open",
        page_of(
            [pair("KXTEST-26OCT24-A", volume_fp="1.00"), pair("KXTEST-26OCT24-A", volume_fp="2.00")]
        ),
    )
    await discovery_for(migrated, data, Clock()).cycle()
    assert (await rows(migrated, "SELECT volume_e2 FROM market WHERE ticker = 'KXTEST-26OCT24-A'"))[
        0
    ][0] == 200


# --- the runner ---------------------------------------------------------------------------------


async def test_the_runner_retries_a_failed_cycle_and_can_be_asked_for_one_now(
    migrated: str,
) -> None:
    data, clock = scripted(), Clock()
    data.fail_on = "open"
    runner = DiscoveryRunner(discovery_for(migrated, data, clock), interval=30.0, retry_after=0.05)
    task = asyncio.create_task(runner.run())
    for _ in range(100):
        if runner.failures >= 1:
            break
        await asyncio.sleep(0.02)
    assert runner.failures >= 1 and "Kalshi is unavailable" in (runner.last_error or "")
    data.fail_on = None
    for _ in range(200):
        if runner.cycles >= 1:
            break
        await asyncio.sleep(0.02)
    assert runner.cycles >= 1 and runner.last_error is None  # it came back by itself
    done = runner.cycles
    clock.at = NOW + timedelta(minutes=1)
    runner.request()  # the interval is 30 s, but a lifecycle gap wants one now
    for _ in range(200):
        if runner.cycles > done:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    assert runner.cycles > done and runner.stats()["failures"] >= 1


async def test_a_request_is_met_only_by_a_cycle_that_began_after_it_and_succeeded(
    migrated: str,
) -> None:
    data = scripted()
    data.fail_on = "open"
    runner = DiscoveryRunner(
        discovery_for(migrated, data, Clock()), interval=30.0, retry_after=0.05
    )
    task = asyncio.create_task(runner.run())
    request = runner.request()
    await asyncio.sleep(0.4)
    assert not request.done() and runner.failures >= 1  # failing cycles do not satisfy it
    data.fail_on = None
    await asyncio.wait_for(request, 5)
    assert runner.cycles >= 1
    done = runner.cycles
    await asyncio.wait_for(runner.request(), 5)  # a later request wakes the idle loop at once
    assert runner.cycles > done
    task.cancel()


async def test_a_series_listed_twice_in_one_upsert_is_stored_once_with_the_last_version(
    migrated: str,
) -> None:
    first = Series.model_validate({"ticker": "KXTEST", "title": "First", "tags": []})
    second = Series.model_validate({"ticker": "KXTEST", "title": "Second", "tags": []})
    conn = await asyncpg.connect(dsn(migrated))
    try:
        counts = await records.upsert(
            conn, "series", records.SERIES_COLUMNS, "ticker",
            [records.series_record(first, NOW), records.series_record(second, NOW)],
            ignore=("fetched_at",),
        )  # fmt: skip
        title = await conn.fetchval("SELECT title FROM series")
    finally:
        await conn.close()
    assert counts == (1, 0) and title == "Second"


async def test_a_cycle_already_under_way_does_not_satisfy_a_request_made_during_it(
    migrated: str,
) -> None:
    runner = DiscoveryRunner(discovery_for(migrated, scripted(), Clock()))
    runner._starts = 5  # noqa: SLF001 (five cycles begun; the fifth is running)
    request = runner.request()
    runner._resolve(5)  # noqa: SLF001 (the running cycle succeeds)
    assert not request.done()  # it may have read the markets before the change that prompted this
    runner._resolve(6)  # noqa: SLF001 (the next one succeeds)
    assert request.done()
