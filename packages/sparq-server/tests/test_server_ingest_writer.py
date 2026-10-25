"""The ingest writer against a real PostgreSQL with TimescaleDB: batches, retries, rejects."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from dbhelpers import dsn
from ingestfixtures import RECEIVED_NS, TS_MS, data, lifecycle, ticker, trade
from sparq_server.ingest.rows import TickerRow
from sparq_server.ingest.writer import IngestWriter, WriterConfig, WriterError

pytestmark = pytest.mark.db

FAST = WriterConfig(
    batch_rows=1000, flush_interval=0.05, max_pending=2000, retry_base=0.01, retry_max=0.05,
    close_timeout=5.0,
)  # fmt: skip
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def connector(url: str) -> Any:
    async def connect() -> asyncpg.Connection:
        return await asyncpg.connect(dsn(url))

    return connect


async def rows(url: str, statement: str, *args: Any) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn(url))
    try:
        return list(await conn.fetch(statement, *args))
    finally:
        await conn.close()


async def count(url: str, table: str) -> int:
    return int((await rows(url, f"SELECT count(*) FROM {table}"))[0][0])  # noqa: S608


def writer_for(url: str, config: WriterConfig = FAST, **kwargs: Any) -> IngestWriter:
    return IngestWriter(connector(url), config, **kwargs)


# --- writing ------------------------------------------------------------------------------------


async def test_each_kind_of_frame_lands_in_its_table_with_exact_values(migrated: str) -> None:
    async with writer_for(migrated) as writer:
        await writer.add(ticker())
        await writer.add(trade())
        await writer.add(lifecycle())
        await writer.flush()
        stats = writer.stats()
    row = (await rows(migrated, "SELECT * FROM ticker"))[0]
    assert (row["ticker"], row["price_e6"], row["yes_bid_e6"], row["volume_e2"]) == (
        "KXTEST-26OCT24-A", 530_000, 510_000, 3_011_246,
    )  # fmt: skip
    assert row["ts"] == datetime.fromtimestamp(TS_MS / 1000, tz=UTC)
    assert row["received_at"] == datetime.fromtimestamp(RECEIVED_NS / 1e9, tz=UTC)
    assert row["dollar_volume_e6"] == 15_056_000_000
    tr = (await rows(migrated, "SELECT * FROM trade"))[0]
    assert (tr["trade_id"], tr["yes_price_e6"], tr["count_e2"], tr["taker_side"]) == (
        "t-1", 650_000, 615, "yes",
    )  # fmt: skip
    life = (await rows(migrated, "SELECT * FROM market_lifecycle"))[0]
    assert (life["event_type"], life["source"], life["price_level_structure"]) == (
        "created", "stream", "linear_cent",
    )  # fmt: skip
    details = json.loads(life["details"])
    assert details["additional_metadata"]["rules_primary"] == "If so, Yes."
    assert details["price_ranges"][0]["step"] == "0.0100"
    assert stats["written"] == {"ticker": 1, "trade": 1, "lifecycle": 1}


async def test_rows_appear_by_themselves_after_the_flush_interval(migrated: str) -> None:
    async with writer_for(migrated) as writer:
        for n in range(3):
            await writer.add(ticker(f"KXTEST-{n}"))
        await asyncio.sleep(0.4)
        assert await count(migrated, "ticker") == 3  # nobody called flush
        assert writer.stats()["pending"] == 0


async def test_a_full_batch_is_written_without_waiting_for_the_interval(migrated: str) -> None:
    config = WriterConfig(batch_rows=3, flush_interval=30.0, max_pending=100)
    async with writer_for(migrated, config) as writer:
        for n in range(3):
            await writer.add(ticker(f"KXTEST-{n}"))
        for _ in range(100):
            if await count(migrated, "ticker") == 3:
                break
            await asyncio.sleep(0.02)
        assert await count(migrated, "ticker") == 3


async def test_closing_writes_what_is_left_and_releases_the_connection(migrated: str) -> None:
    config = WriterConfig(batch_rows=1000, flush_interval=30.0, max_pending=2000)
    writer = writer_for(migrated, config)
    await writer.start()
    await writer.add(ticker())
    await writer.close()
    assert await count(migrated, "ticker") == 1
    assert writer._conn is None  # noqa: SLF001


# --- trades are never stored twice -------------------------------------------------------------


async def test_the_same_trade_twice_in_a_batch_and_again_later_is_stored_once(
    migrated: str,
) -> None:
    async with writer_for(migrated) as writer:
        await writer.add(trade("t-1"))
        await writer.add(trade("t-1"))
        await writer.add(trade("t-2"))
        await writer.flush()
        await writer.add(trade("t-1"))  # a backfill delivers it again
        await writer.flush()
        stats = writer.stats()
    assert await count(migrated, "trade") == 2
    assert stats["written"]["trade"] == 2 and stats["trade_duplicates"] == 2


async def test_trades_of_different_markets_with_the_same_id_are_different_trades(
    migrated: str,
) -> None:
    async with writer_for(migrated) as writer:
        await writer.add(trade("t-1", "KXTEST-A"))
        await writer.add(trade("t-1", "KXTEST-B"))
        await writer.flush()
    assert await count(migrated, "trade") == 2


# --- when the database goes away ---------------------------------------------------------------


async def test_a_connection_known_to_be_dead_is_replaced_before_the_write(migrated: str) -> None:
    async with writer_for(migrated) as writer:
        await writer.add(trade("t-0"))
        await writer.flush()
        assert writer._conn is not None  # noqa: SLF001
        pid = writer._conn.get_server_pid()  # noqa: SLF001
        await rows(migrated, "SELECT pg_terminate_backend($1)", pid)
        await asyncio.sleep(0.1)
        for n in range(1, 6):
            await writer.add(trade(f"t-{n}"))
            await writer.add(ticker(f"KXTEST-{n}"))
        await writer.flush()
        stats = writer.stats()
    assert await count(migrated, "trade") == 6 and await count(migrated, "ticker") == 5
    assert stats["reconnects"] == 1 and stats["retries"] == 0  # noticed in time: no failed write
    assert stats["written"] == {"ticker": 5, "trade": 6, "lifecycle": 0}


class DiesOnce(asyncpg.Connection):  # type: ignore[misc]
    """A real connection that is cut in the middle of the first copy it is asked to do."""

    cut = {"left": 1}

    async def copy_records_to_table(self, table_name: str, **kwargs: Any) -> Any:
        if self.cut["left"]:
            self.cut["left"] -= 1
            await self.execute(
                "SELECT pg_terminate_backend(pg_backend_pid())"
            )  # the server drops us
        return await super().copy_records_to_table(table_name, **kwargs)


async def test_a_connection_that_dies_in_the_middle_of_a_write_is_retried_once_whole(
    migrated: str,
) -> None:
    DiesOnce.cut["left"] = 1

    async def connect() -> asyncpg.Connection:
        return await asyncpg.connect(dsn(migrated), connection_class=DiesOnce)

    async with IngestWriter(connect, FAST) as writer:
        for n in range(5):
            await writer.add(trade(f"t-{n}"))
            await writer.add(ticker(f"KXTEST-{n}"))
        await writer.flush()
        stats = writer.stats()
    assert await count(migrated, "trade") == 5 and await count(migrated, "ticker") == 5
    assert stats["retries"] == 1 and stats["reconnects"] == 1
    assert stats["written"] == {"ticker": 5, "trade": 5, "lifecycle": 0}  # counted once, not twice


async def test_a_database_that_cannot_be_reached_is_retried_with_a_growing_delay(
    migrated: str,
) -> None:
    delays: list[float] = []
    failures = {"left": 3}
    real = connector(migrated)

    async def flaky() -> asyncpg.Connection:
        if failures["left"]:
            failures["left"] -= 1
            raise OSError("no route")
        return await real()

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    config = WriterConfig(
        batch_rows=1000, flush_interval=30.0, max_pending=2000, retry_base=0.5, retry_max=1.5
    )
    writer = IngestWriter(flaky, config, sleep=sleep)
    await writer.add(ticker())
    await writer.flush()
    await writer.close()
    assert delays == [0.5, 1.0, 1.5]  # doubling, capped
    assert await count(migrated, "ticker") == 1 and writer.stats()["retries"] == 3


async def test_closing_with_the_database_gone_fails_in_bounded_time_and_says_how_many_rows(
    migrated: str,
) -> None:
    async def gone() -> asyncpg.Connection:
        raise OSError("down")

    config = WriterConfig(
        batch_rows=1000, flush_interval=30.0, max_pending=2000, retry_base=0.01, retry_max=0.02,
        close_timeout=0.3,
    )  # fmt: skip
    writer = IngestWriter(gone, config)
    await writer.add(ticker())
    await writer.add(ticker("KXTEST-B"))
    with pytest.raises(WriterError, match="2 rows could not be written"):
        await asyncio.wait_for(writer.close(), 5)


# --- rows the database refuses -----------------------------------------------------------------


def poisoned(name: str, price: int | None = None) -> TickerRow:
    return TickerRow(NOW, NOW, name, price, None, None, None, None, None, None, None, None, None)


async def test_a_row_the_database_refuses_is_rejected_alone_and_the_rest_are_stored(
    migrated: str,
) -> None:
    async with writer_for(migrated) as writer:
        for n in range(40):
            await writer.add(ticker(f"KXTEST-{n}"))
            if n in (7, 31):  # two bad rows in the middle of good ones, bypassing the checks
                bad = poisoned(f"BAD\x00{n}") if n == 7 else poisoned("KXTEST-BAD", 2_000_000)
                writer._items.append(("ticker", bad))  # noqa: SLF001
                writer._pending += 1  # noqa: SLF001
        await writer.flush()
        stats = writer.stats()
    assert await count(migrated, "ticker") == 40
    rejects = await rows(migrated, "SELECT kind, reason, payload FROM ingest_reject ORDER BY id")
    assert len(rejects) == 2 and all(r["kind"] == "ticker" for r in rejects)
    assert all("the database refused it" in r["reason"] for r in rejects)
    assert json.loads(rejects[1]["payload"])["row"]["price_e6"] == 2_000_000
    assert sum(stats["rejected"].values()) == 2 and stats["written"]["ticker"] == 40


async def test_a_frame_that_never_fitted_its_model_is_kept_in_the_reject_table(
    migrated: str,
) -> None:
    async with writer_for(migrated) as writer:
        await writer.add(data("trade", {"market_ticker": "KXTEST-A"}, sid=2))
        await writer.add(ticker("KXTEST-GOOD"))
        await writer.flush()
    reject = (await rows(migrated, "SELECT kind, reason, payload FROM ingest_reject"))[0]
    assert reject["kind"] == "trade" and "trade_id" in reject["reason"]
    assert json.loads(reject["payload"])["msg"] == {"market_ticker": "KXTEST-A"}
    assert await count(migrated, "ticker") == 1 and await count(migrated, "trade") == 0


async def test_a_rule_breaking_row_is_rejected_before_it_ever_reaches_the_database(
    migrated: str,
) -> None:
    async with writer_for(migrated) as writer:
        await writer.add(ticker(price_dollars="1.5000"))
        await writer.flush()
        stats = writer.stats()
    assert await count(migrated, "ticker") == 0 and await count(migrated, "ingest_reject") == 1
    assert stats["retries"] == 0 and sum(stats["rejected"].values()) == 1


# --- what is not stored ------------------------------------------------------------------------


async def test_skipped_frames_are_counted_by_reason_and_not_stored(migrated: str) -> None:
    async with writer_for(migrated) as writer:
        await writer.add(ticker("KXMVECROSS-1"))
        await writer.add(trade("t-9", "KXMVESPORTS-1"))
        await writer.add(data("event_lifecycle", {"event_ticker": "E"}))
        await writer.add(lifecycle(market_ticker=None))
        await writer.flush()
        stats = writer.stats()
    assert stats["skipped"] == {
        "combo market": 2,
        "a event_lifecycle message is not recorded": 1,
        "a lifecycle message with no market": 1,
    }
    for table in ("ticker", "trade", "market_lifecycle"):
        assert await count(migrated, table) == 0


async def test_a_doubtful_exchange_time_is_counted_and_replaced_by_ours(migrated: str) -> None:
    async with writer_for(migrated) as writer:
        await writer.add(ticker(ts_ms=5))
        await writer.flush()
        assert writer.stats()["doubtful_times"] == 1
    row = (await rows(migrated, "SELECT ts, received_at FROM ticker"))[0]
    assert row["ts"] == row["received_at"]


async def test_the_lag_between_the_exchange_and_us_is_measured(migrated: str) -> None:
    async with writer_for(migrated) as writer:
        for n in range(10):
            await writer.add(ticker(f"KXTEST-{n}"))  # the exchange time is 85 ms before arrival
        await writer.flush()
        lag = writer.stats()["lag_ms"]
    assert lag["median"] == pytest.approx(85, abs=1)


# --- the buffer is bounded ---------------------------------------------------------------------


async def test_a_stalled_database_fills_the_buffer_and_then_makes_add_wait(migrated: str) -> None:
    config = WriterConfig(
        batch_rows=2, flush_interval=0.02, max_pending=5, retry_base=0.01, retry_max=0.05
    )
    locker = await asyncpg.connect(dsn(migrated))
    transaction = locker.transaction()
    await transaction.start()
    await locker.execute("LOCK TABLE ticker IN ACCESS EXCLUSIVE MODE")  # the writer now blocks
    try:
        async with writer_for(migrated, config) as writer:

            async def feed() -> None:
                for n in range(20):
                    await writer.add(ticker(f"KXTEST-{n}"))

            adder = asyncio.create_task(feed())
            await asyncio.sleep(0.6)
            assert not adder.done()  # add is waiting for room
            assert writer.stats()["pending"] <= 5 and writer.stats()["pending_high_water"] <= 5
            await transaction.rollback()  # the database comes back
            await asyncio.wait_for(adder, 10)
            await writer.flush()
            stats = writer.stats()
    finally:
        await locker.close()
    assert await count(migrated, "ticker") == 20  # nothing was lost while it waited
    assert stats["waits_for_room"] >= 1 and stats["pending_high_water"] <= 5


def test_the_configuration_is_checked() -> None:
    for bad in (
        {"batch_rows": 0},
        {"batch_rows": 10, "max_pending": 5},
        {"flush_interval": 0},
        {"retry_base": 0},
        {"retry_base": 2.0, "retry_max": 1.0},
        {"close_timeout": 0},
    ):
        with pytest.raises(ValueError):
            WriterConfig(**bad)


async def test_a_reject_whose_payload_holds_a_nul_is_stored_with_it_replaced(migrated: str) -> None:
    async with writer_for(migrated) as writer:
        await writer.add(ticker("KX\x00TEST"))
        await writer.flush()
    reject = (await rows(migrated, "SELECT reason, payload FROM ingest_reject"))[0]
    assert "NUL" in reject["reason"]
    assert json.loads(reject["payload"])["msg"]["market_ticker"] == "KX\ufffdTEST"


async def test_a_reject_that_cannot_itself_be_stored_is_counted_and_does_not_loop(
    migrated: str,
) -> None:
    from sparq_server.ingest.rows import Reject

    async with writer_for(migrated) as writer:
        writer._items.append(("reject", Reject("ti\x00cker", "unstorable kind", None)))  # noqa: SLF001
        writer._pending += 1  # noqa: SLF001
        await writer.add(ticker())
        await asyncio.wait_for(writer.flush(), 10)
        stats = writer.stats()
    assert stats["rejects_lost"] == 1 and stats["written"]["ticker"] == 1
    assert await count(migrated, "ingest_reject") == 0 and await count(migrated, "ticker") == 1
