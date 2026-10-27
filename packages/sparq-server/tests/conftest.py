import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # so tests can import fakesystem and dbhelpers

from sparq_server.db.guard import NotATestDatabase, check_test_database  # noqa: E402
from sparq_server.db.migrate import downgrade, upgrade  # noqa: E402

DATABASE_VARIABLE = "SPARQ_TEST_DATABASE_URL"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Tests marked ``db`` need a real PostgreSQL with TimescaleDB: they run only on request."""
    if os.environ.get(DATABASE_VARIABLE):
        return
    skip = pytest.mark.skip(reason=f"database test: set {DATABASE_VARIABLE} to a test database")
    for item in items:
        # a real marker only: item.keywords would also match folder, module and test names
        if item.get_closest_marker("db") is not None:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def db_url() -> str:
    """The test database's URL, after the guard has checked that it is one."""
    url = os.environ[DATABASE_VARIABLE]
    try:
        check_test_database(url)
    except NotATestDatabase as error:
        pytest.exit(f"refusing to run database tests: {error}", returncode=2)
    return url


@pytest.fixture
def migrated(db_url: str) -> Iterator[str]:
    """An empty database brought to the newest schema, for one test."""
    downgrade(db_url, "base")
    upgrade(db_url, "head")
    yield db_url
