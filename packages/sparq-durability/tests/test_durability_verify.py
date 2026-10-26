"""The structure check, driven by a scripted connection (the real damage needs a real server)."""

from typing import Any

import asyncpg
import pytest
from sparq_durability.verify import _integrity


class Scripted:
    def __init__(self, bad: int, checksum_failures: int | None, fail_on: str | None = None) -> None:
        self.bad, self.checksum_failures, self.fail_on = bad, checksum_failures, fail_on

    async def execute(self, sql: str) -> None:
        if self.fail_on and self.fail_on in sql:
            raise asyncpg.PostgresError("index is corrupt")

    async def fetchval(self, sql: str) -> Any:
        if "verify_heapam" in sql:
            return self.bad
        return self.checksum_failures


async def run(conn: Scripted) -> dict[str, str]:
    return {c.name: c.status for c in await _integrity(conn)}


async def test_a_clean_table_passes_both_checks() -> None:
    assert await run(Scripted(0, 0)) == {
        "table and index structure": "ok",
        "page checksums": "ok",
    }


async def test_problems_found_by_amcheck_fail_the_structure_check() -> None:
    assert (await run(Scripted(3, 0)))["table and index structure"] == "fail"


async def test_a_corrupt_index_fails_the_structure_check() -> None:
    assert (await run(Scripted(0, 0, fail_on="bt_index_check")))[
        "table and index structure"
    ] == "fail"


@pytest.mark.parametrize("failures", [2, None])
async def test_checksum_failures_or_checksums_switched_off_fail_the_checksum_check(
    failures: int | None,
) -> None:
    assert (await run(Scripted(0, failures)))["page checksums"] == "fail"
