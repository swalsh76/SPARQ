import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # so tests can import fakekalshi

from fakekalshi import FakeKalshi, Rig  # noqa: E402


@pytest.fixture
def fake() -> FakeKalshi:
    return FakeKalshi()


@pytest.fixture
def rig(tmp_path: Path, fake: FakeKalshi) -> Callable[..., Rig]:
    def build(**overrides: Any) -> Rig:
        return Rig(tmp_path / "kalshi", fake, **overrides)

    return build


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Tests marked ``integration`` talk to the real Kalshi API: they run only on request."""
    if os.environ.get("SPARQ_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set SPARQ_LIVE=1 to run")
    for item in items:
        # a real marker only: item.keywords would also match folder, module and test names
        if item.get_closest_marker("integration") is not None:
            item.add_marker(skip)
