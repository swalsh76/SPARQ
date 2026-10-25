"""Small helpers for the database tests: run SQL through asyncpg and get rows back."""

import asyncio
from typing import Any, cast

import asyncpg


def dsn(url: str) -> str:
    """The asyncpg DSN for a SQLAlchemy URL (the driver suffix is dropped)."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def sql(url: str, statement: str, *args: Any) -> list[asyncpg.Record]:
    """Run one statement and return its rows (an empty list for a statement without any)."""

    async def run() -> list[asyncpg.Record]:
        connection = await asyncpg.connect(dsn(url))
        try:
            return cast(list[asyncpg.Record], await connection.fetch(statement, *args))
        finally:
            await connection.close()

    return asyncio.run(run())


def scalar(url: str, statement: str, *args: Any) -> Any:
    rows = sql(url, statement, *args)
    return rows[0][0] if rows else None
