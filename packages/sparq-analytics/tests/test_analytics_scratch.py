import os
import tempfile
from pathlib import Path

import pytest
from sparq_analytics import scratch
from sparq_analytics.history import cli as history_cli
from sparq_analytics.scratch import BUDGET_VARIABLE, SCRATCH_VARIABLES, ScratchError, configure
from sparq_analytics.study import cli as study_cli
from sparq_analytics.survey import cli as survey_cli


@pytest.fixture
def clean(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """No scratch variable set, and every one restored afterwards."""
    for name in (*SCRATCH_VARIABLES, BUDGET_VARIABLE):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    return monkeypatch


def settings(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_no_settings_file_changes_nothing(tmp_path: Path, clean: pytest.MonkeyPatch) -> None:
    assert configure(tmp_path / "none.toml") == {}
    assert all(name not in os.environ for name in SCRATCH_VARIABLES)


def test_the_settings_point_the_temporary_and_spill_folders(
    tmp_path: Path, clean: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "scratch"
    folder.mkdir()
    path = settings(
        tmp_path / "a.toml", f'scratch = "{folder.as_posix()}"\nspill_budget_mb = 60000\n'
    )
    done = configure(path)
    assert done == dict.fromkeys(SCRATCH_VARIABLES, folder.as_posix()) | {BUDGET_VARIABLE: "60000"}
    assert all(os.environ[name] == folder.as_posix() for name in SCRATCH_VARIABLES)
    assert Path(tempfile.gettempdir()) == folder  # Python's own temporary files go there too


def test_a_variable_already_set_wins(tmp_path: Path, clean: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "scratch"
    folder.mkdir()
    clean.setenv(BUDGET_VARIABLE, "100")
    clean.setenv("TMPDIR", str(tmp_path))
    path = settings(tmp_path / "a.toml", f'scratch = "{folder.as_posix()}"\nspill_budget_mb = 9\n')
    done = configure(path)
    assert BUDGET_VARIABLE not in done and "TMPDIR" not in done
    assert (os.environ[BUDGET_VARIABLE], os.environ["TMPDIR"]) == ("100", str(tmp_path))
    assert done["POLARS_TEMP_DIR"] == folder.as_posix()  # the ones not set are still filled in


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('scratch = "/no/such/sparq-scratch"\n', "does not exist"),
        ("scratch = 5\n", "does not exist"),
        ("spill_budget_mb = -1\n", "whole number"),
        ("spill_budget_mb = true\n", "whole number"),
        ('spill_budget_mb = "60000"\n', "whole number"),
        ("threads = 4\n", "unknown settings: threads"),
        ("scratch = \n", "not valid TOML"),
    ],
)
def test_bad_settings_are_refused(
    tmp_path: Path, clean: pytest.MonkeyPatch, body: str, message: str
) -> None:
    with pytest.raises(ScratchError, match=message):
        configure(settings(tmp_path / "bad.toml", body))
    assert all(name not in os.environ for name in SCRATCH_VARIABLES)


def test_the_settings_file_comes_from_the_environment_or_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPARQ_ANALYTICS_CONFIG", "/somewhere/analytics.toml")
    assert scratch.config_path() == Path("/somewhere/analytics.toml")
    monkeypatch.delenv("SPARQ_ANALYTICS_CONFIG")
    assert scratch.config_path() == Path("~/.config/sparq/analytics.toml").expanduser()


def test_the_command_lines_refuse_bad_settings(
    tmp_path: Path,
    clean: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    clean.setenv("SPARQ_ANALYTICS_CONFIG", str(settings(tmp_path / "bad.toml", "threads = 1\n")))
    assert survey_cli.main(["dump", "--kalshi", str(tmp_path), "--out", str(tmp_path)]) == 1
    assert "unknown settings: threads" in capsys.readouterr().err
    assert history_cli.main(["--data-dir", str(tmp_path), "status"]) == 1
    assert "unknown settings" in capsys.readouterr().err
    assert study_cli.main(["--data-dir", str(tmp_path), "report"]) == 1
    assert "unknown settings" in capsys.readouterr().err
