import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # so tests can import the fake server

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Tests marked ``integration`` talk to the real Kalshi API: they run only on request."""
    if os.environ.get("SPARQ_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set SPARQ_LIVE=1 to run")
    for item in items:
        # a real marker only: item.keywords would also match folder, module and test names
        if item.get_closest_marker("integration") is not None:
            item.add_marker(skip)


@pytest.fixture
def sample() -> Callable[[str], dict[str, Any]]:
    """A fixture file read the way the client reads a response: floats kept as text."""

    def load(name: str) -> dict[str, Any]:
        text = (FIXTURES / name).read_text(encoding="utf-8")
        data: dict[str, Any] = json.loads(text, parse_float=str)
        return data

    return load
