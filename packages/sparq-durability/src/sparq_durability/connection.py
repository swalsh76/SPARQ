"""Opening a connection for the power-cut test, only ever to a test database."""

import asyncpg
from sparq_server.db.guard import check_test_database

TABLE = "durability_rows"


async def connect(url: str) -> asyncpg.Connection:
    """Connect to ``url`` after the guard has checked that it names a test database."""
    check_test_database(url)
    return await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://", 1))
