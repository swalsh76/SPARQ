"""Refuse to run tests, or a destructive migration, against anything but a test database.

A test run drops and recreates tables, so it must never be pointed at production. The rule is
on the name and the port (KAPS PLAN.md §6.7). A test database is named ``sparq_test`` or
``sparq_test_<something>``. The production database is ``sparq`` on port 5432, and port 5432 on any
machine other than this one is treated as production too (the test cluster on the server host has
its own port).
"""

from sqlalchemy.engine import make_url

PRODUCTION_NAME = "sparq"
PRODUCTION_PORT = 5432
TEST_PREFIX = "sparq_test"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class NotATestDatabase(Exception):
    """The URL does not name a test database."""


def check_test_database(url: str) -> None:
    """Raise NotATestDatabase unless ``url`` is clearly a test database."""
    parsed = make_url(url)
    name = parsed.database or ""
    host = parsed.host or "localhost"
    port = parsed.port or PRODUCTION_PORT
    if name == PRODUCTION_NAME:
        raise NotATestDatabase(f"{name!r} is the production database name")
    if name != TEST_PREFIX and not name.startswith(TEST_PREFIX + "_"):
        raise NotATestDatabase(f"{name!r} is not named {TEST_PREFIX} or {TEST_PREFIX}_*")
    if port == PRODUCTION_PORT and host not in LOCAL_HOSTS:
        raise NotATestDatabase(f"port {PRODUCTION_PORT} on {host!r} is the production cluster")
