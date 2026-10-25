import runpy
import sys
from pathlib import Path

import pytest


def test_running_the_package_runs_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["sparq_analytics.history", "--data-dir", str(tmp_path), "status"]
    )
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("sparq_analytics.history", run_name="__main__")
    assert caught.value.code == 0
    assert "trade_days_done: 0" in capsys.readouterr().out
