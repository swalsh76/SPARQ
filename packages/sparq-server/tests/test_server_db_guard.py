import pytest
from sparq_server.db.guard import NotATestDatabase, check_test_database


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p@localhost:5432/sparq_test",
        "postgresql+asyncpg://u:p@127.0.0.1:5433/sparq_test_ingest",
        "postgresql+asyncpg://u:p@192.0.2.5:5433/sparq_test",
        "postgresql+asyncpg://u:p@localhost/sparq_test",  # the default port, on this machine
    ],
)
def test_a_test_database_passes(url: str) -> None:
    check_test_database(url)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("postgresql+asyncpg://u:p@192.0.2.5:5433/sparq", "production database name"),
        ("postgresql+asyncpg://u:p@localhost:5432/sparq", "production database name"),
        ("postgresql+asyncpg://u:p@localhost:5433/postgres", "not named sparq_test"),
        ("postgresql+asyncpg://u:p@localhost:5433/sparq_testing", "not named sparq_test"),
        ("postgresql+asyncpg://u:p@localhost:5433/", "not named sparq_test"),
        ("postgresql+asyncpg://u:p@192.0.2.5:5432/sparq_test", "production cluster"),
        ("postgresql+asyncpg://u:p@192.0.2.5/sparq_test", "production cluster"),
    ],
)
def test_anything_that_looks_like_production_is_refused(url: str, message: str) -> None:
    with pytest.raises(NotATestDatabase, match=message):
        check_test_database(url)
