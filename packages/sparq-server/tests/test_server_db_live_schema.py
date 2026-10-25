"""Migration 0002 (the live feed), run against a real PostgreSQL with TimescaleDB."""

import datetime as dt

import asyncpg
import pytest
from dbhelpers import scalar, sql
from sparq_server.db.migrate import HEAD, current_revision, downgrade, upgrade

pytestmark = pytest.mark.db

T0 = dt.datetime(2026, 10, 24, 12, 0, tzinfo=dt.UTC)
INSERT_TICKER = (
    "INSERT INTO ticker (ts, received_at, ticker, price_e6, yes_bid_e6, yes_ask_e6, "
    "yes_bid_size_e2, volume_e2) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
)


def test_ticker_is_a_hypertable_with_day_chunks_and_compression_by_market(migrated: str) -> None:
    row = sql(
        migrated,
        "SELECT compression_enabled, num_dimensions FROM timescaledb_information.hypertables "
        "WHERE hypertable_name = 'ticker'",
    )[0]
    assert row["compression_enabled"] is True and row["num_dimensions"] == 1
    interval = scalar(
        migrated,
        "SELECT time_interval FROM timescaledb_information.dimensions "
        "WHERE hypertable_name = 'ticker'",
    )
    assert interval == dt.timedelta(days=1)
    settings = sql(
        migrated,
        "SELECT segmentby, orderby FROM timescaledb_information.hypertable_compression_settings "
        "WHERE hypertable::text = 'ticker'",
    )[0]
    assert (settings["segmentby"], settings["orderby"]) == ("ticker", "ts DESC")
    jobs = sql(
        migrated,
        "SELECT config FROM timescaledb_information.jobs "
        "WHERE hypertable_name = 'ticker' AND proc_name = 'policy_compression'",
    )
    assert len(jobs) == 1 and "1 day" in str(jobs[0]["config"])


def test_ticker_has_one_index_by_market_and_time_and_no_default_time_index(migrated: str) -> None:
    rows = sql(migrated, "SELECT indexname FROM pg_indexes WHERE tablename = 'ticker'")
    assert {r["indexname"] for r in rows} == {"ticker_ticker_ts"}


def test_a_ticker_row_may_leave_every_optional_field_empty(migrated: str) -> None:
    sql(migrated, "INSERT INTO ticker (ts, received_at, ticker) VALUES ($1, $1, 'KXTEST-A')", T0)
    assert scalar(migrated, "SELECT count(*) FROM ticker") == 1


@pytest.mark.parametrize(
    "values",
    [
        (1_000_001, 1, 1, 1, 1),  # price above a dollar
        (-1, 1, 1, 1, 1),
        (500_000, 1_000_001, 1, 1, 1),  # bid above a dollar
        (500_000, 1, -1, 1, 1),
        (500_000, 1, 1, -1, 1),  # a negative size
        (500_000, 1, 1, 1, -1),  # a negative volume
    ],
)
def test_a_ticker_row_that_breaks_a_rule_is_rejected(
    migrated: str, values: tuple[int, ...]
) -> None:
    price, bid, ask, bid_size, volume = values
    with pytest.raises(asyncpg.CheckViolationError):
        sql(migrated, INSERT_TICKER, T0, T0, "KXTEST-A", price, bid, ask, bid_size, volume)


def test_the_same_ticker_frame_may_be_stored_twice_because_a_moment_is_not_unique(
    migrated: str,
) -> None:
    for _ in range(2):
        sql(migrated, INSERT_TICKER, T0, T0, "KXTEST-A", 500_000, 490_000, 510_000, 100, 1_000)
    assert scalar(migrated, "SELECT count(*) FROM ticker") == 2


def test_compressing_a_ticker_chunk_does_not_change_the_rows(migrated: str) -> None:
    for day in range(3):
        for k in range(6):
            when = T0 - dt.timedelta(days=30 + day, seconds=k)
            args = (when, when, f"KXTEST-{k % 3}", 100_000 + k, 99_000, 101_000, 10 * k, 1_000 * k)
            sql(migrated, INSERT_TICKER, *args)
    before = sql(migrated, "SELECT * FROM ticker ORDER BY ticker, ts")
    compressed = sql(
        migrated, "SELECT compress_chunk(c, if_not_compressed => true) FROM show_chunks('ticker') c"
    )
    assert compressed
    after = sql(migrated, "SELECT * FROM ticker ORDER BY ticker, ts")
    assert [tuple(r) for r in before] == [tuple(r) for r in after] and len(after) == 18


def test_a_market_has_a_venue_that_defaults_to_kalshi(migrated: str) -> None:
    sql(migrated, "INSERT INTO market (ticker) VALUES ('KXTEST-A')")
    assert scalar(migrated, "SELECT venue FROM market") == "kalshi"
    with pytest.raises(asyncpg.NotNullViolationError):
        sql(migrated, "INSERT INTO market (ticker, venue) VALUES ('KXTEST-B', NULL)")


def test_a_market_lifecycle_keeps_the_time_it_was_seen_and_its_details(migrated: str) -> None:
    sql(
        migrated,
        "INSERT INTO market_lifecycle (ticker, observed_at, event_type, details) "
        "VALUES ('KXTEST-A', $1, 'created', $2::jsonb)",
        T0,
        '{"rules_primary": "If so, Yes."}',
    )
    row = sql(migrated, "SELECT source, details FROM market_lifecycle")[0]
    assert row["source"] == "stream" and "If so, Yes." in row["details"]
    with pytest.raises(asyncpg.CheckViolationError):
        sql(
            migrated,
            "INSERT INTO market_lifecycle (ticker, observed_at, event_type) VALUES ('X', $1, '')",
            T0,
        )


def test_the_gap_log_tracks_a_backfill_through_its_states(migrated: str) -> None:
    sql(migrated, "INSERT INTO gap_log (kind, source, started_at) VALUES ('trades', 'ws', $1)", T0)
    assert scalar(migrated, "SELECT status FROM gap_log") == "open"
    sql(
        migrated,
        "UPDATE gap_log SET status = 'done', reason = 'overflow', generation = 3, "
        "trades_found = 10, trades_added = 7, trades_duplicate = 3",
    )
    row = sql(migrated, "SELECT * FROM gap_log")[0]
    counts = (row["status"], row["trades_found"], row["trades_added"], row["trades_duplicate"])
    assert counts == ("done", 10, 7, 3)
    with pytest.raises(asyncpg.CheckViolationError):
        sql(migrated, "UPDATE gap_log SET status = 'maybe'")


def test_a_reject_is_stamped_with_the_time_it_was_stored(migrated: str) -> None:
    sql(migrated, "INSERT INTO ingest_reject (kind, reason) VALUES ('ticker', 'price too high')")
    assert scalar(migrated, "SELECT at IS NOT NULL FROM ingest_reject") is True


def test_exchange_status_and_schedule_store_what_the_poller_will_give(migrated: str) -> None:
    sql(
        migrated,
        "INSERT INTO exchange_status (observed_at, exchange_index, exchange_active, "
        "trading_active) VALUES ($1, 0, true, false)",
        T0,
    )
    sql(
        migrated,
        "INSERT INTO exchange_schedule VALUES ($1, $2::jsonb)",
        T0,
        '{"standard_hours": []}',
    )
    assert scalar(migrated, "SELECT trading_active FROM exchange_status") is False
    with pytest.raises(asyncpg.UniqueViolationError):
        sql(migrated, "INSERT INTO exchange_schedule VALUES ($1, '{}')", T0)


def test_upgrading_a_database_that_holds_data_keeps_all_of_it(migrated: str) -> None:
    downgrade(migrated, "0001")
    assert current_revision(migrated) == "0001"
    sql(migrated, "INSERT INTO market (ticker, status) VALUES ('KXTEST-A', 'open')")
    sql(migrated, "INSERT INTO gap_log (kind, source, started_at) VALUES ('all', 'ws', $1)", T0)
    sql(
        migrated,
        "INSERT INTO trade (ticker, trade_id, created_time, yes_price_e6, count_e2, taker_side) "
        "VALUES ('KXTEST-A', 't1', $1, 500000, 100, 'yes')",
        T0,
    )
    upgrade(migrated)
    assert current_revision(migrated) == HEAD
    market = sql(migrated, "SELECT ticker, status, venue FROM market")[0]
    assert tuple(market) == ("KXTEST-A", "open", "kalshi")
    assert scalar(migrated, "SELECT status FROM gap_log") == "open"
    assert scalar(migrated, "SELECT count(*) FROM trade") == 1


def test_going_back_to_0001_drops_only_what_0002_added(migrated: str) -> None:
    sql(migrated, "INSERT INTO market (ticker) VALUES ('KXTEST-A')")
    downgrade(migrated, "0001")
    rows = sql(
        migrated, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )
    tables = {r["table_name"] for r in rows}
    assert not tables & {"ticker", "market_lifecycle", "exchange_status", "ingest_reject"}
    assert tables >= {"market", "trade", "gap_log", "series"}
    rows = sql(
        migrated,
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'market'",
    )
    assert "venue" not in {r["column_name"] for r in rows}
    assert scalar(migrated, "SELECT count(*) FROM market") == 1


def test_the_ingest_state_keeps_a_value_by_key_and_stamps_it(migrated: str) -> None:
    sql(migrated, "INSERT INTO ingest_state (key, value) VALUES ('heartbeat', '1792789506150')")
    row = sql(migrated, "SELECT value, updated_at IS NOT NULL AS stamped FROM ingest_state")[0]
    assert (row["value"], row["stamped"]) == ("1792789506150", True)
    with pytest.raises(asyncpg.UniqueViolationError):
        sql(migrated, "INSERT INTO ingest_state (key, value) VALUES ('heartbeat', '2')")
    with pytest.raises(asyncpg.NotNullViolationError):
        sql(migrated, "INSERT INTO ingest_state (key, value) VALUES ('x', NULL)")
