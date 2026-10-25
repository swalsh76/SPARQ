"""Run the migrations from code (``sparq-admin`` calls these; the tests do too).

The URL is a SQLAlchemy URL with the asyncpg driver, for example
``postgresql+asyncpg://user:password@host:5432/sparq``. It is passed to the migration environment
as an attribute and never written to a file or a log.
"""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

MIGRATIONS = Path(__file__).parent / "migrations"
HEAD = "0003"


def _config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["url"] = url
    return config


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(_config(url), revision)


def downgrade(url: str, revision: str = "base") -> None:
    command.downgrade(_config(url), revision)


def current_revision(url: str) -> str | None:
    """The revision the database is at, or None for an empty database."""

    async def read() -> str | None:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync: MigrationContext.configure(sync).get_current_revision()
                )
        finally:
            await engine.dispose()

    return asyncio.run(read())
