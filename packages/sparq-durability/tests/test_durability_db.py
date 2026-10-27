"""The power-cut test against a real PostgreSQL: it works, and it catches each kind of damage.

Run with ``SPARQ_TEST_DATABASE_URL`` set to a test database whose role may create tables and, for
the zeroed-page case, run ``COPY ... TO PROGRAM`` (a superuser).
"""

import asyncio
from pathlib import Path

import asyncpg
import pytest
from sparq_durability import writer as writer_module
from sparq_durability.connection import TABLE, connect
from sparq_durability.ledger import first_id, merge, read_ledgers, start_ledger
from sparq_durability.verify import run_pitr_check, run_verify
from sparq_durability.writer import CREATE, INSERT, WriterRefused, run_writer
from sparq_server.admin.preflight import Check

pytestmark = pytest.mark.db

ROWS = 300


async def reset_counters(conn: asyncpg.Connection) -> None:
    """Forget earlier tests' checksum failures (a restart after a crash does the same)."""
    await conn.execute("SELECT pg_stat_reset()")


async def build(url: str, directory: Path, rows: int = ROWS) -> None:
    """A fresh table holding ``rows`` rows of run 1, and a ledger that acknowledges all of them."""
    conn = await connect(url)
    try:
        await conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
        await reset_counters(conn)
        await conn.execute(CREATE)
        await conn.execute(INSERT, first_id(1), first_id(1) + rows - 1)
        await conn.execute("CHECKPOINT")
    finally:
        await conn.close()
    claim(directory, rows)


def claim(directory: Path, rows: int) -> None:
    low = first_id(1)
    (directory / "ledger-run1.log").write_text(f"1 {low} {low + rows - 1}\n", encoding="utf-8")


async def damage(url: str, sql: str) -> None:
    conn = await connect(url)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


def failed(checks: list[Check]) -> dict[str, str]:
    return {c.name: c.detail for c in checks if c.status == "fail"}


async def test_an_undamaged_database_passes_every_check(db_url: str, tmp_path: Path) -> None:
    await build(db_url, tmp_path)
    checks = await run_verify(db_url, tmp_path)
    assert failed(checks) == {}
    names = {c.name for c in checks}
    assert {"acknowledged rows", "table and index structure", "page checksums"} <= names


async def test_a_deleted_row_is_found_and_named(db_url: str, tmp_path: Path) -> None:
    await build(db_url, tmp_path)
    await damage(db_url, f"DELETE FROM {TABLE} WHERE id = {first_id(1) + 41}")
    problems = failed(await run_verify(db_url, tmp_path))
    assert "1 missing" in problems["acknowledged rows"]
    assert str(first_id(1) + 41) in problems["acknowledged rows"]


async def test_a_corrupted_payload_is_found(db_url: str, tmp_path: Path) -> None:
    await build(db_url, tmp_path)
    await damage(db_url, f"UPDATE {TABLE} SET digest = '\\x00'::bytea WHERE id = {first_id(1) + 7}")
    assert (
        "1 with a wrong checksum" in failed(await run_verify(db_url, tmp_path))["acknowledged rows"]
    )


async def test_a_fallen_row_count_is_found(db_url: str, tmp_path: Path) -> None:
    await build(db_url, tmp_path)
    await damage(db_url, f"DELETE FROM {TABLE} WHERE id > {first_id(1) + ROWS - 51}")
    assert "50 missing" in failed(await run_verify(db_url, tmp_path))["acknowledged rows"]


async def test_a_forged_ledger_fails_the_negative_control(db_url: str, tmp_path: Path) -> None:
    await build(db_url, tmp_path)
    claim(tmp_path, ROWS + 25)  # the ledger claims 25 commits the database never made
    assert "25 missing" in failed(await run_verify(db_url, tmp_path))["acknowledged rows"]


async def test_a_zeroed_page_is_found(db_url: str, tmp_path: Path) -> None:
    """Zero the first page of the table's file behind the server's back, as a lost write would."""
    await build(db_url, tmp_path)
    conn = await connect(db_url)
    try:
        if not await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"):
            pytest.skip("the zeroed-page case needs a superuser (COPY ... TO PROGRAM)")
        path = await conn.fetchval(
            f"SELECT current_setting('data_directory') || '/' || pg_relation_filepath('{TABLE}')"
        )
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_buffercache")
        await conn.execute(f"SELECT pg_buffercache_evict_relation('{TABLE}'::regclass)")
        await conn.execute(
            f"COPY (SELECT 1) TO PROGRAM 'dd if=/dev/zero of={path} bs=8192 count=1 conv=notrunc'"
        )
    finally:
        await conn.close()
    problems = failed(await run_verify(db_url, tmp_path))
    # PostgreSQL treats an all-zero page as a valid empty one, so amcheck is silent; only the
    # comparison with the ledger can see the rows that vanished with it.
    assert " missing" in problems["acknowledged rows"]
    assert "0 missing" not in problems["acknowledged rows"]


async def test_a_database_that_is_down_fails_without_crashing(tmp_path: Path) -> None:
    claim(tmp_path, 10)
    url = "postgresql+asyncpg://t@127.0.0.1:1/sparq_test"
    assert "cannot connect" in failed(await run_verify(url, tmp_path))["database"]


async def test_the_writer_and_the_ledger_work_together_and_verify_passes(
    db_url: str, tmp_path: Path
) -> None:
    conn = await connect(db_url)
    try:
        await conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
    finally:
        await conn.close()
    requested = "SELECT num_requested FROM pg_stat_checkpointer"
    conn = await connect(db_url)
    try:
        before = await conn.fetchval(requested)
    finally:
        await conn.close()
    server = await start_ledger(tmp_path, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        stats = await run_writer(
            db_url, 5, "127.0.0.1", port, seconds=1.0, checkpoint_every=0.3, seed=1
        )
        await asyncio.sleep(0.3)
    assert stats.commits > 1 and stats.rows > stats.commits  # single rows and batches both
    conn = await connect(db_url)
    try:
        assert await conn.fetchval(requested) >= before + 2  # checkpoints ran during the writes
    finally:
        await conn.close()
    assert "median" in stats.summary()
    checks = await run_verify(db_url, tmp_path)
    assert failed(checks) == {}
    acknowledged = next(c for c in checks if c.name == "acknowledged rows")
    assert f"{stats.rows} acknowledged rows" in acknowledged.detail


async def test_the_writer_will_not_reuse_a_run_number_or_start_without_a_ledger(
    db_url: str, tmp_path: Path
) -> None:
    await build(db_url, tmp_path)
    with pytest.raises(WriterRefused, match="already used"):
        await run_writer(db_url, 1, "127.0.0.1", 1, seconds=0.1)
    with pytest.raises(WriterRefused, match="not reachable"):
        await run_writer(db_url, 6, "127.0.0.1", 1, seconds=0.1)


async def test_the_writer_stops_when_the_ledger_hangs_up(db_url: str) -> None:
    async def hang_up(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()  # the hello
        writer.close()

    server = await asyncio.start_server(hang_up, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        with pytest.raises((WriterRefused, ConnectionError)):
            await asyncio.wait_for(run_writer(db_url, 9, "127.0.0.1", port, seconds=10.0), 15.0)


async def test_the_ledger_is_never_ahead_of_the_database(
    db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A commit that fails must not be reported: the writer is made to fail after id 150."""
    failing = writer_module.INSERT.replace(
        "repeat('x', 200)", "repeat('x', (200 / (g - 3000000000000 <= 150)::int)::int)"
    )
    monkeypatch.setattr(writer_module, "INSERT", failing)
    conn = await connect(db_url)
    try:
        await conn.execute("DROP TABLE IF EXISTS durability_rows")
    finally:
        await conn.close()
    server = await start_ledger(tmp_path, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        with pytest.raises(Exception, match="division by zero"):
            await run_writer(db_url, 3, "127.0.0.1", port, seconds=30.0, seed=2)
        await asyncio.sleep(0.3)
    conn = await connect(db_url)
    try:
        rows = await conn.fetchval("SELECT count(*) FROM durability_rows")
    finally:
        await conn.close()
    spans, _ = read_ledgers(tmp_path)
    assert rows > 0
    assert sum(s.count for s in merge(spans)) == rows


async def test_damage_to_an_earlier_run_is_found_by_a_later_check(
    db_url: str, tmp_path: Path
) -> None:
    """Every ledger so far is checked, not only the newest."""
    await build(db_url, tmp_path)
    conn = await connect(db_url)
    try:
        await conn.execute(INSERT, first_id(2), first_id(2) + 9)
        await conn.execute(f"DELETE FROM {TABLE} WHERE id = {first_id(1) + 3}")
    finally:
        await conn.close()
    (tmp_path / "ledger-run2.log").write_text(f"2 {first_id(2)} {first_id(2) + 9}\n")
    problems = failed(await run_verify(db_url, tmp_path))
    assert "1 missing" in problems["acknowledged rows"]


async def test_a_flipped_byte_in_a_page_fails_its_checksum(db_url: str, tmp_path: Path) -> None:
    """Damage the table's file behind the server's back, with a valid-looking page header."""
    await build(db_url, tmp_path)
    conn = await connect(db_url)
    try:
        if not await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"):
            pytest.skip("the flipped-byte case needs a superuser (COPY ... TO PROGRAM)")
        path = await conn.fetchval(
            f"SELECT current_setting('data_directory') || '/' || pg_relation_filepath('{TABLE}')"
        )
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_buffercache")
        await conn.execute(f"SELECT pg_buffercache_evict_relation('{TABLE}'::regclass)")
        await conn.execute(
            "COPY (SELECT 1) TO PROGRAM 'printf \\377 | dd of="
            + path
            + " bs=1 seek=5000 count=1 conv=notrunc'"
        )
    finally:
        await conn.close()
    problems = failed(await run_verify(db_url, tmp_path))
    assert "page checksums" in problems
    assert "acknowledged rows" in problems  # the page cannot even be read


T0 = 1_800_000_000.0  # the ledger's clock, in epoch seconds


def timed_ledger(directory: Path, spans: list[tuple[int, int, float]]) -> None:
    """A ledger for run 1: (first offset, last offset, when the ledger heard of it)."""
    low = first_id(1)
    lines = [f"1 {low + a} {low + b} {int(t * 1000)}" for a, b, t in spans]
    (directory / "ledger-run1.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def pitr(url: str, directory: Path, target: float = T0 + 5) -> dict[str, Check]:
    return {c.name: c for c in await run_pitr_check(url, directory, 1, target, skew=1.0)}


async def table_with(url: str, directory: Path, rows: int) -> None:
    await build(url, directory, rows)
    timed_ledger(directory, [(0, 199, T0), (200, 299, T0 + 10)])


async def test_a_restore_holding_exactly_what_was_acknowledged_by_the_target_passes(
    db_url: str, tmp_path: Path
) -> None:
    await table_with(db_url, tmp_path, 200)
    assert failed(list((await pitr(db_url, tmp_path)).values())) == {}


async def test_rows_acknowledged_after_the_target_must_not_be_there(
    db_url: str, tmp_path: Path
) -> None:
    await table_with(db_url, tmp_path, 300)
    problems = failed(list((await pitr(db_url, tmp_path)).values()))
    assert "100 rows are present" in problems["committed after the target"]


async def test_rows_acknowledged_before_the_target_must_be_there(
    db_url: str, tmp_path: Path
) -> None:
    await table_with(db_url, tmp_path, 150)
    problems = failed(list((await pitr(db_url, tmp_path)).values()))
    assert "50 acknowledged rows are missing" in problems["acknowledged before the target"]


async def test_a_gap_inside_the_restored_rows_fails(db_url: str, tmp_path: Path) -> None:
    await table_with(db_url, tmp_path, 200)
    await damage(db_url, f"DELETE FROM {TABLE} WHERE id = {first_id(1) + 90}")
    assert "1 ids missing" in failed(list((await pitr(db_url, tmp_path)).values()))["restored rows"]


async def test_commits_within_the_clock_slack_of_the_target_may_be_either_side(
    db_url: str, tmp_path: Path
) -> None:
    for rows in (200, 250):  # the second batch was heard 0.5 s after the target
        await build(db_url, tmp_path, rows)
        timed_ledger(tmp_path, [(0, 199, T0), (200, 249, T0 + 5.5)])
        assert failed(list((await pitr(db_url, tmp_path)).values())) == {}


async def test_a_ledger_without_times_or_for_another_run_cannot_prove_a_restore(
    db_url: str, tmp_path: Path
) -> None:
    await build(db_url, tmp_path)  # claim() writes lines with no time
    assert "no timed entries" in failed(list((await pitr(db_url, tmp_path)).values()))["ledger"]
    timed_ledger(tmp_path, [(0, 9, T0)])
    other = await run_pitr_check(db_url, tmp_path, 2, T0, 1.0)
    assert other[0].status == "fail"
