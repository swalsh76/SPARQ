"""The first migration, run against a real PostgreSQL with TimescaleDB (CI, or a test cluster)."""

import datetime as dt

import asyncpg
import pytest
from dbhelpers import scalar, sql
from sparq_server.db import schema
from sparq_server.db.migrate import HEAD, current_revision, downgrade, upgrade

pytestmark = pytest.mark.db

T0 = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.UTC)
TABLES = {
    "series", "fee_change", "market", "trade", "gap_log", "alembic_version", "ticker",
    "market_lifecycle", "exchange_status", "exchange_schedule", "ingest_reject", "ingest_state",
}  # fmt: skip
INSERT_TRADE = (
    "INSERT INTO trade (ticker, trade_id, created_time, yes_price_e6, count_e2, taker_side) "
    "VALUES ($1, $2, $3, $4, $5, $6)"
)


def names(url: str) -> set[str]:
    rows = sql(
        url, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )
    return {r["table_name"] for r in rows}


def test_the_upgrade_creates_the_tables_and_records_its_revision(migrated: str) -> None:
    assert names(migrated) >= TABLES
    assert current_revision(migrated) == HEAD == "0003"


def test_upgrading_again_changes_nothing(migrated: str) -> None:
    upgrade(migrated)
    assert current_revision(migrated) == HEAD


def test_the_downgrade_removes_every_table_and_an_upgrade_brings_them_back(migrated: str) -> None:
    downgrade(migrated, "base")
    assert not (TABLES - {"alembic_version"}) & names(migrated)
    assert current_revision(migrated) is None
    upgrade(migrated)
    assert names(migrated) >= TABLES


def test_trades_are_a_hypertable_with_one_day_chunks_and_compression(migrated: str) -> None:
    row = sql(
        migrated,
        "SELECT compression_enabled, num_dimensions FROM timescaledb_information.hypertables "
        "WHERE hypertable_name = 'trade'",
    )[0]
    assert row["compression_enabled"] is True and row["num_dimensions"] == 1
    interval = scalar(
        migrated,
        "SELECT time_interval FROM timescaledb_information.dimensions "
        "WHERE hypertable_name = 'trade'",
    )
    assert interval == dt.timedelta(days=1)
    policies = scalar(
        migrated,
        "SELECT count(*) FROM timescaledb_information.jobs "
        "WHERE hypertable_name = 'trade' AND proc_name = 'policy_compression'",
    )
    assert policies == 1


def test_the_database_matches_the_schema_module_column_for_column(migrated: str) -> None:
    kinds = {
        "text": "TEXT", "integer": "INTEGER", "bigint": "BIGINTEGER", "boolean": "BOOLEAN",
        "timestamp with time zone": "TIMESTAMP", "ARRAY": "ARRAY", "jsonb": "JSONB",
    }  # fmt: skip
    for table in schema.metadata.sorted_tables:
        rows = sql(
            migrated,
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1",
            table.name,
        )
        in_database = {r["column_name"]: kinds[r["data_type"]] for r in rows}
        in_module = {c.name: type(c.type).__name__.upper() for c in table.columns}
        assert in_database == in_module, table.name


@pytest.mark.parametrize(
    ("price", "count", "side"),
    [(1_000_001, 100, "yes"), (-1, 100, "yes"), (500_000, -1, "yes"), (500_000, 100, "maybe")],
)
def test_a_trade_that_breaks_a_rule_is_rejected(
    migrated: str, price: int, count: int, side: str
) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        sql(migrated, INSERT_TRADE, "KXTEST-A", "t1", T0, price, count, side)


def test_a_market_label_outside_the_five_is_rejected(migrated: str) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        sql(migrated, "INSERT INTO market (ticker, label) VALUES ('KXTEST-A', 'maybe')")


def test_a_trade_cannot_be_stored_twice(migrated: str) -> None:
    sql(migrated, INSERT_TRADE, "KXTEST-A", "t1", T0, 500_000, 100, "yes")
    with pytest.raises(asyncpg.UniqueViolationError):
        sql(migrated, INSERT_TRADE, "KXTEST-A", "t1", T0, 500_000, 100, "yes")


def test_integers_come_back_exactly_including_the_largest_count(migrated: str) -> None:
    largest = 2**63 - 1
    sql(migrated, INSERT_TRADE, "KXTEST-A", "big", T0, 999_999, largest, "no")
    row = sql(migrated, "SELECT yes_price_e6, count_e2 FROM trade WHERE trade_id = 'big'")[0]
    assert (row["yes_price_e6"], row["count_e2"]) == (999_999, largest)


def test_compressing_a_chunk_does_not_change_the_rows(migrated: str) -> None:
    for day in range(4):
        for k in range(5):
            when = T0 - dt.timedelta(days=30 + day, minutes=k)
            sql(
                migrated,
                INSERT_TRADE,
                f"KXTEST-{k % 2}",
                f"t{day}-{k}",
                when,
                100_000 + k,
                100 * k,
                "yes",
            )
    before = sql(migrated, "SELECT * FROM trade ORDER BY ticker, trade_id")
    compressed = sql(
        migrated, "SELECT compress_chunk(c, if_not_compressed => true) FROM show_chunks('trade') c"
    )
    assert compressed  # at least one chunk was compressed
    after = sql(migrated, "SELECT * FROM trade ORDER BY ticker, trade_id")
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    assert len(after) == 20


def test_the_gap_log_numbers_its_rows_and_checks_its_kinds(migrated: str) -> None:
    sql(
        migrated,
        "INSERT INTO gap_log (kind, source, started_at) VALUES ('ticker', 'ingest', $1)",
        T0,
    )
    sql(
        migrated,
        "INSERT INTO gap_log (kind, source, started_at) VALUES ('trades', 'ingest', $1)",
        T0,
    )
    assert [r["id"] for r in sql(migrated, "SELECT id FROM gap_log ORDER BY id")] == [1, 2]
    with pytest.raises(asyncpg.CheckViolationError):
        sql(
            migrated,
            "INSERT INTO gap_log (kind, source, started_at) VALUES ('nothing', 'x', $1)",
            T0,
        )
