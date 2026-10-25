"""After a cut: is the database sound, and is every acknowledged row in it?

The checks read pages, so a damaged page shows up as an error or as missing rows; either fails the
check. The acknowledged rows are checked against every ledger so far, not just the latest.
"""

from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import asyncpg

from sparq_server.admin.preflight import Check
from sparq_server.db.guard import check_test_database
from sparq_server.durability.connection import connect
from sparq_server.durability.ledger import LedgerError, Span, first_id, merge, read_ledgers

_COUNT = (
    "SELECT count(*) FILTER (WHERE digest = sha256(convert_to(id::text, 'UTF8'))) AS good, "
    "count(*) AS present FROM durability_rows WHERE id BETWEEN $1 AND $2"
)
_MISSING = (
    "SELECT g FROM generate_series($1::bigint, $2::bigint) g "
    "LEFT JOIN durability_rows t ON t.id = g WHERE t.id IS NULL ORDER BY g LIMIT 5"
)


async def _acknowledged(conn: asyncpg.Connection, spans: list[Span]) -> Check:
    expected = lost = corrupt = 0
    first_missing: list[int] = []
    for span in spans:
        good, present = await conn.fetchrow(_COUNT, span.first, span.last) or (0, 0)
        expected += span.count
        lost += span.count - present
        corrupt += present - good
        if span.count - present and len(first_missing) < 5:
            rows = await conn.fetch(_MISSING, span.first, span.last)
            first_missing += [row["g"] for row in rows]
    detail = f"{expected} acknowledged rows: {lost} missing, {corrupt} with a wrong checksum"
    if lost or corrupt:
        return Check("acknowledged rows", "fail", f"{detail}; first missing ids {first_missing}")
    return Check("acknowledged rows", "ok", detail)


async def _unacknowledged(conn: asyncpg.Connection, spans: list[Span]) -> Check:
    total = await conn.fetchval("SELECT count(*) FROM durability_rows")
    extra = total - sum(span.count for span in spans)
    return Check(
        "unacknowledged rows",
        "ok",
        f"{extra} rows committed after the ledger's last entry (the cut came between the "
        "commit and the report; they are allowed)",
    )


async def _integrity(conn: asyncpg.Connection) -> list[Check]:
    checks = []
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS amcheck")
        bad = await conn.fetchval("SELECT count(*) FROM verify_heapam('durability_rows')")
        await conn.execute("SELECT bt_index_check('durability_rows_pkey'::regclass, true)")
        checks.append(
            Check("table and index structure", "fail" if bad else "ok", f"{bad} problems found")
        )
    except asyncpg.PostgresError as error:
        checks.append(Check("table and index structure", "fail", str(error)[:200]))
    try:
        await conn.execute(
            "SELECT pg_stat_force_next_flush()"
        )  # make this session's counts visible
        failures = await conn.fetchval(
            "SELECT checksum_failures FROM pg_stat_database WHERE datname = current_database()"
        )
        ok = failures == 0
        detail = "no page failed its checksum" if ok else f"checksum_failures = {failures}"
        checks.append(Check("page checksums", "ok" if ok else "fail", detail))
    except asyncpg.PostgresError as error:
        checks.append(Check("page checksums", "fail", str(error)[:200]))
    return checks


async def _guarded(name: str, check: Coroutine[Any, Any, Check]) -> Check:
    """A damaged page can raise while it is read: that is a failed check, not a crash."""
    try:
        return await check
    except asyncpg.PostgresError as error:
        return Check(name, "fail", str(error)[:200])


async def run_verify(url: str, ledger_directory: Path) -> list[Check]:
    check_test_database(url)  # before anything else: never even look at production
    try:
        spans, notes = read_ledgers(ledger_directory)
    except (LedgerError, OSError) as error:
        return [Check("ledgers", "fail", str(error))]
    if not spans:
        return [Check("ledgers", "fail", f"no acknowledged commits in {ledger_directory}")]
    merged = merge(spans)
    silent = sum("silent" in note for note in notes)
    checks = [
        Check(
            "ledgers",
            "ok",
            f"{len({s.run for s in spans})} runs, {len(merged)} spans, "
            f"{silent} silent connection notes",
        )
    ]
    try:
        conn = await connect(url)
    except (OSError, asyncpg.PostgresError) as error:
        return [*checks, Check("database", "fail", f"cannot connect: {str(error)[:150]}")]
    try:
        checks.append(Check("database", "ok", "online"))
        checks.append(await _guarded("acknowledged rows", _acknowledged(conn, merged)))
        checks.append(await _guarded("unacknowledged rows", _unacknowledged(conn, merged)))
        checks.extend(await _integrity(conn))
    finally:
        await conn.close()
    return checks


async def run_pitr_check(
    url: str, ledger_directory: Path, run: int, target: float, skew: float = 1.0
) -> list[Check]:
    """Is a database restored to ``target`` (epoch seconds) exactly what the ledger says it was?

    The writer commits ids in order from one connection, so a restore to time T must hold the ids
    from the run's first id up to some last id, with no gap. Every commit the ledger heard of at
    least ``skew`` seconds before T must be there; none it heard of more than ``skew`` seconds after
    T may be. The slack covers the two machines' clocks and the delay in reporting.
    """
    check_test_database(url)
    try:
        spans, _ = read_ledgers(ledger_directory)
    except (LedgerError, OSError) as error:
        return [Check("ledger", "fail", str(error))]
    mine = [s for s in spans if s.run == run]
    if not mine or any(s.at_ms is None for s in mine):
        return [Check("ledger", "fail", f"run {run} has no timed entries in {ledger_directory}")]
    must = [s.last for s in mine if s.at_ms / 1000 <= target - skew]  # type: ignore[operator]
    may = [s.last for s in mine if s.at_ms / 1000 <= target + skew]  # type: ignore[operator]
    low = first_id(run)
    need, allowed = max(must, default=low - 1), max(may, default=low - 1)
    try:
        conn = await connect(url)
    except (OSError, asyncpg.PostgresError) as error:
        return [Check("database", "fail", f"cannot connect: {str(error)[:150]}")]
    try:
        count, highest = await conn.fetchrow(
            "SELECT count(*), coalesce(max(id), $1::bigint - 1) FROM durability_rows "
            "WHERE id >= $1 AND id < $2",
            low,
            low + 10**12,
        ) or (0, low - 1)
    except asyncpg.PostgresError as error:
        return [Check("restored rows", "fail", str(error)[:200])]
    finally:
        await conn.close()
    gaps = (highest - low + 1) - count
    return [
        Check("restored rows", "ok", f"{count} rows, ids up to {highest - low + 1}")
        if gaps == 0
        else Check("restored rows", "fail", f"{gaps} ids missing inside the restored range"),
        Check("acknowledged before the target", "ok", f"all {need - low + 1} are present")
        if highest >= need
        else Check(
            "acknowledged before the target",
            "fail",
            f"{need - highest} acknowledged rows are missing",
        ),
        Check("committed after the target", "ok", "none are present")
        if highest <= allowed
        else Check("committed after the target", "fail", f"{highest - allowed} rows are present"),
    ]
