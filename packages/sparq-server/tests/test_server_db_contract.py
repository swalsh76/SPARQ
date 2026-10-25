"""The tables carry exactly what the downloader writes, so K2's import is a copy."""

import pyarrow as pa
import pytest
import sqlalchemy as sa
from sparq_analytics.history.schemas import (
    FEE_CHANGES_SCHEMA,
    MARKETS_SCHEMA,
    SERIES_SCHEMA,
    TRADES_SCHEMA,
)
from sparq_server.db import schema
from sqlalchemy.dialects import postgresql


def expected_type(field: pa.Field) -> type:
    if field.name == "raw_json":
        return postgresql.JSONB
    t = field.type
    if pa.types.is_int32(t):
        return sa.Integer
    if pa.types.is_int64(t):
        return sa.BigInteger
    if pa.types.is_boolean(t):
        return sa.Boolean
    if pa.types.is_timestamp(t):
        return sa.TIMESTAMP
    if pa.types.is_list(t):
        return postgresql.ARRAY
    if pa.types.is_string(t):
        return sa.Text
    raise AssertionError(f"no mapping for {field}")


@pytest.mark.parametrize(
    ("parquet", "table"),
    [
        (SERIES_SCHEMA, schema.series),
        (FEE_CHANGES_SCHEMA, schema.fee_change),
        (MARKETS_SCHEMA, schema.market),
        (TRADES_SCHEMA, schema.trade),
    ],
    ids=["series", "fee_change", "market", "trade"],
)
def test_a_table_has_every_column_of_its_parquet_file_with_the_same_width(
    parquet: pa.Schema, table: sa.Table
) -> None:
    assert set(parquet.names) <= set(table.columns.keys())
    extra = set(table.columns.keys()) - set(parquet.names)
    # a column Parquet does not have must fill itself in, so the import stays a plain copy
    assert all(table.columns[name].server_default is not None for name in extra), extra
    for field in parquet:
        column = table.columns[field.name]
        assert type(column.type) is expected_type(field), field.name


def test_every_timestamp_column_keeps_its_time_zone() -> None:
    for table in schema.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, sa.TIMESTAMP):
                assert column.type.timezone, f"{table.name}.{column.name}"


def test_no_money_or_count_column_is_a_float() -> None:
    for table in schema.metadata.tables.values():
        for column in table.columns:
            if column.name.endswith(("_e6", "_e2")):
                assert type(column.type) in (sa.Integer, sa.BigInteger), column.name
            assert not isinstance(column.type, sa.Float | sa.Numeric), column.name


def test_the_trade_primary_key_starts_with_the_ticker_and_has_the_time_column() -> None:
    keys = [c.name for c in schema.trade.primary_key.columns]
    assert keys == ["ticker", "trade_id", "created_time"]


def test_the_labels_are_the_ones_the_downloader_writes() -> None:
    assert schema.LABELS == ("yes", "no", "scalar", "other_settled", "unsettled")
