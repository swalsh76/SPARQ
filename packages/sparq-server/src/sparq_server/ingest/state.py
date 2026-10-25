"""The ingest's memory across restarts: a few text values by key in ``ingest_state``."""

import asyncpg

HEARTBEAT = "feed.heartbeat_ms"  # the time of the last frame the feed delivered, in epoch ms
UPDATED_SINCE = "discovery.updated_since"  # epoch seconds: ask for markets updated after this
LAST_FULL = "discovery.last_full"  # epoch seconds of the last full discovery cycle
LAST_SERIES = "discovery.last_series"  # epoch seconds of the last series and fee-change refresh


async def get(conn: asyncpg.Connection, key: str) -> str | None:
    value = await conn.fetchval("SELECT value FROM ingest_state WHERE key = $1", key)
    return None if value is None else str(value)


async def get_int(conn: asyncpg.Connection, key: str) -> int | None:
    value = await get(conn, key)
    return int(value) if value is not None and value.lstrip("-").isdigit() else None


async def put(conn: asyncpg.Connection, key: str, value: str | int) -> None:
    await conn.execute(
        "INSERT INTO ingest_state (key, value) VALUES ($1, $2) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        key,
        str(value),
    )
