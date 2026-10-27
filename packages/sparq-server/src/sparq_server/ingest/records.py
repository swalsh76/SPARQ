"""The reference tables: their rows and their upserts.

``market`` and ``series`` must hold exactly what the downloader's Parquet files hold, so that K2's
import of the history is a copy and the live rows and the historical rows are one kind of row. A
market row is built by :func:`sparq_core.markets.market_values`, the same function the downloader
uses; a test builds the same markets both ways and compares them.

An upsert stages a chunk with ``COPY`` and merges it with one statement that touches only rows that
really differ, so a chunk of unchanged markets costs a read and no write. For markets it also
records, in ``market_lifecycle``, when a market was first seen and every change to the fields that
matter to a study (status, result, close and expiry times, settlement, rules text), with the time
it was seen (KAPS decision 23). Volatile fields (prices, volume) are not history.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import asyncpg
from sparq_core.markets import MARKET_COLUMNS
from sparq_core.models import Series, SeriesFeeChange

from sparq_server.db import schema
from sparq_server.ingest.rows import LIFECYCLE_COLUMNS, LifecycleRow

SOURCE_DISCOVERY = "discovery"

SERIES_COLUMNS = tuple(c.name for c in schema.series.columns)
FEE_CHANGE_COLUMNS = tuple(c.name for c in schema.fee_change.columns)
# what is worth a history row when it changes
TRACKED = (
    "status", "result", "close_time", "expected_expiration_time", "expiration_time",
    "latest_expiration_time", "settlement_ts", "settlement_value_e6", "rules_primary",
    "rules_secondary", "early_close_condition",
)  # fmt: skip


def series_record(series: Series, fetched_at: datetime) -> tuple[Any, ...]:
    return (
        series.ticker, series.title, series.category, series.frequency, series.tags,
        series.exchange_index, series.fee_type, series.fee_multiplier_e6, fetched_at,
    )  # fmt: skip


def fee_change_record(change: SeriesFeeChange, fetched_at: datetime) -> tuple[Any, ...]:
    return (
        change.id, change.series_ticker, change.fee_type, change.fee_multiplier_e6,
        change.scheduled_ts, fetched_at,
    )  # fmt: skip


async def upsert(
    conn: asyncpg.Connection,
    table: str,
    columns: Sequence[str],
    key: str,
    rows: list[tuple[Any, ...]],
    ignore: Sequence[str] = (),
) -> tuple[int, int]:
    """Insert new rows and update changed ones; returns (inserted, updated).

    ``ignore`` names columns that alone never make a row "changed" (a fetch time). Duplicate keys
    in ``rows`` are collapsed first, keeping the last: a chunk can hold a market twice if it moved
    between two listings, and one statement may not touch a row twice.
    """
    if not rows:
        return 0, 0
    at = columns.index(key)
    rows = list({row[at]: row for row in rows}.values())
    # the staged rows live until the transaction ends, so the stage and the merge share one
    async with conn.transaction():
        stage = f"{table}_stage"
        await conn.execute(
            f"CREATE TEMP TABLE IF NOT EXISTS {stage} (LIKE {table} INCLUDING DEFAULTS) "  # noqa: S608
            "ON COMMIT DELETE ROWS"
        )
        await conn.copy_records_to_table(stage, records=rows, columns=list(columns))
        names = ", ".join(columns)
        compared = [c for c in columns if c != key and c not in ignore]
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != key)
        old = ", ".join(f"{table}.{c}" for c in compared)
        new = ", ".join(f"EXCLUDED.{c}" for c in compared)
        returned = await conn.fetch(
            f"INSERT INTO {table} ({names}) SELECT {names} FROM {stage} "  # noqa: S608
            f"ON CONFLICT ({key}) DO UPDATE SET {sets} "
            f"WHERE ({old}) IS DISTINCT FROM ({new}) RETURNING (xmax = 0) AS inserted"
        )
    inserted = sum(1 for r in returned if r["inserted"])
    return inserted, len(returned) - inserted


def _history_row(
    fresh: dict[str, Any], observed_at: datetime, event_type: str, changes: dict[str, list[Any]]
) -> LifecycleRow:
    details = {"changed": {k: [_plain(a), _plain(b)] for k, (a, b) in changes.items()}}
    return LifecycleRow(
        fresh["ticker"], observed_at, None, SOURCE_DISCOVERY, event_type, fresh["open_time"],
        fresh["close_time"], None, fresh["settlement_ts"], fresh["result"],
        fresh["settlement_value_e6"], None, fresh["exchange_index"],
        fresh["price_level_structure"], json.dumps(details) if changes else None,
    )  # fmt: skip


def _plain(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


async def upsert_markets(
    conn: asyncpg.Connection, rows: list[tuple[Any, ...]], observed_at: datetime
) -> tuple[int, int, int]:
    """Upsert a chunk of markets and record what was new or changed in ``market_lifecycle``.

    Returns (inserted, updated, history rows written). One transaction: the markets and their
    history land together or not at all.
    """
    if not rows:
        return 0, 0, 0
    at = MARKET_COLUMNS.index("ticker")
    rows = list({row[at]: row for row in rows}.values())
    async with conn.transaction():
        known = {
            r["ticker"]: r
            for r in await conn.fetch(
                f"SELECT ticker, {', '.join(TRACKED)} FROM market WHERE ticker = ANY($1::text[])",  # noqa: S608
                [row[at] for row in rows],
            )
        }
        history: list[LifecycleRow] = []
        for row in rows:
            fresh = dict(zip(MARKET_COLUMNS, row, strict=True))
            before = known.get(fresh["ticker"])
            if before is None:
                history.append(_history_row(fresh, observed_at, "discovered", {}))
                continue
            changes = {k: [before[k], fresh[k]] for k in TRACKED if before[k] != fresh[k]}
            if changes:
                history.append(_history_row(fresh, observed_at, "changed", changes))
        inserted, updated = await upsert(conn, "market", MARKET_COLUMNS, "ticker", rows)
        if history:
            await conn.copy_records_to_table(
                "market_lifecycle", records=history, columns=list(LIFECYCLE_COLUMNS)
            )
    return inserted, updated, len(history)
