"""The gap log: every stretch of time the feed may have missed, and what became of it.

Kalshi replays nothing after a dropped link, so each outage (a reconnect, a restart, a sequence
jump, a queue overflow) is written here at once. What can be recovered is: **trades** are fetched
from REST, **lifecycle** changes are picked up by a discovery cycle. What cannot is marked
``unrecoverable`` so the record says plainly that ticker data is missing for that window.
"""

from dataclasses import dataclass
from datetime import datetime

import asyncpg

# kinds: what was missed
TICKER = "ticker"
TRADES = "trades"
LIFECYCLE = "lifecycle"
ALL = "all"  # every channel (a subscription could not be restored)

# statuses: what became of it
OPEN = "open"
BACKFILLING = "backfilling"
DONE = "done"
FAILED = "failed"
UNRECOVERABLE = "unrecoverable"
UNFINISHED = (OPEN, BACKFILLING)


@dataclass(frozen=True, slots=True)
class Gap:
    id: int
    kind: str
    source: str
    started_at: datetime
    ended_at: datetime | None
    reason: str | None
    status: str


async def open_gap(
    conn: asyncpg.Connection,
    kind: str,
    started_at: datetime,
    ended_at: datetime | None,
    reason: str,
    *,
    source: str = "ws",
    generation: int | None = None,
    status: str = OPEN,
    note: str | None = None,
) -> int:
    gap_id = await conn.fetchval(
        "INSERT INTO gap_log (kind, source, started_at, ended_at, reason, generation, status, "
        "note) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id",
        kind, source, started_at, ended_at, reason, generation, status, note,
    )  # fmt: skip
    return int(gap_id)


async def get(conn: asyncpg.Connection, gap_id: int) -> Gap | None:
    row = await conn.fetchrow(
        "SELECT id, kind, source, started_at, ended_at, reason, status FROM gap_log WHERE id = $1",
        gap_id,
    )
    return None if row is None else Gap(**dict(row))


async def finish(
    conn: asyncpg.Connection,
    gap_id: int,
    status: str,
    *,
    found: int | None = None,
    added: int | None = None,
    duplicate: int | None = None,
    note: str | None = None,
) -> None:
    await conn.execute(
        "UPDATE gap_log SET status = $2, trades_found = coalesce($3, trades_found), "
        "trades_added = coalesce($4, trades_added), "
        "trades_duplicate = coalesce($5, trades_duplicate), note = coalesce($6, note) "
        "WHERE id = $1",
        gap_id, status, found, added, duplicate, note,
    )  # fmt: skip


async def mark(conn: asyncpg.Connection, gap_id: int, status: str) -> None:
    await conn.execute("UPDATE gap_log SET status = $2 WHERE id = $1", gap_id, status)


async def unfinished(conn: asyncpg.Connection) -> list[Gap]:
    """Gaps a previous run left open or half done, oldest first: they are run again, because
    writing a trade twice is harmless."""
    rows = await conn.fetch(
        "SELECT id, kind, source, started_at, ended_at, reason, status FROM gap_log "
        "WHERE status = ANY($1::text[]) ORDER BY started_at, id",
        list(UNFINISHED),
    )
    return [Gap(**dict(r)) for r in rows]
