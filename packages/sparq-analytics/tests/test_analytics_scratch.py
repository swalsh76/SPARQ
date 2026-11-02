import os
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from sparq_analytics import scratch
from sparq_analytics.history import cli as history_cli
from sparq_analytics.live import cli as live_cli
from sparq_analytics.model import cli as model_cli
from sparq_analytics.paper import cli as paper_cli
from sparq_analytics.scratch import BUDGET_VARIABLE, SCRATCH_VARIABLES, ScratchError, configure
from sparq_analytics.study import cli as study_cli
from sparq_analytics.survey import cli as survey_cli
from sparq_analytics.weather import cli as weather_cli

VARIABLES = (*SCRATCH_VARIABLES, BUDGET_VARIABLE)


@pytest.fixture
def clean(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """No scratch variable set, and every one restored afterwards: each is set first, so the
    monkeypatch records it and removes whatever ``configure`` writes."""
    for name in VARIABLES:
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    return monkeypatch


def settings(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def folder_in(tmp_path: Path) -> Path:
    folder = tmp_path / "scratch"
    folder.mkdir()
    return folder


def test_no_file_at_the_default_path_changes_nothing(
    tmp_path: Path, clean: pytest.MonkeyPatch
) -> None:
    clean.delenv("SPARQ_ANALYTICS_CONFIG")
    clean.setenv("HOME", str(tmp_path))  # no ~/.config/sparq/analytics.toml there
    clean.setenv("USERPROFILE", str(tmp_path))
    assert configure() == {}
    assert all(name not in os.environ for name in VARIABLES)


def test_the_settings_point_the_temporary_and_spill_folders(
    tmp_path: Path, clean: pytest.MonkeyPatch
) -> None:
    folder = folder_in(tmp_path)
    body = f'scratch = "{folder.as_posix()}"\nspill_budget_mb = 60000\n'
    done = configure(settings(tmp_path / "a.toml", body))
    resolved = str(folder.resolve())
    assert done == dict.fromkeys(SCRATCH_VARIABLES, resolved) | {BUDGET_VARIABLE: "60000"}
    assert all(os.environ[name] == resolved for name in SCRATCH_VARIABLES)
    assert Path(tempfile.gettempdir()) == folder.resolve()  # Python's own temporary files too


def test_the_settings_win_over_an_inherited_temporary_folder(
    tmp_path: Path, clean: pytest.MonkeyPatch
) -> None:
    folder = folder_in(tmp_path)
    clean.setenv("TMPDIR", str(tmp_path / "internal-disk"))  # as macOS sets it in every session
    clean.setenv(BUDGET_VARIABLE, "100")
    body = f'scratch = "{folder.as_posix()}"\nspill_budget_mb = 9\n'
    configure(settings(tmp_path / "a.toml", body))
    assert (os.environ["TMPDIR"], os.environ[BUDGET_VARIABLE]) == (str(folder.resolve()), "9")


def test_a_named_settings_file_that_is_missing_is_refused(
    tmp_path: Path, clean: pytest.MonkeyPatch
) -> None:
    clean.setenv("SPARQ_ANALYTICS_CONFIG", str(tmp_path / "typo.toml"))
    with pytest.raises(ScratchError, match="names .*typo.toml, which does not exist"):
        configure()
    with pytest.raises(ScratchError, match="which does not exist"):
        configure(tmp_path / "also-missing.toml")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('scratch = "/no/such/sparq-scratch"\n', "absolute, existing scratch folder"),
        ('scratch = "relative/tmp"\n', "absolute, existing scratch folder"),
        ("scratch = 5\n", "absolute, existing scratch folder"),
        ("spill_budget_mb = -1\n", "whole number"),
        ("spill_budget_mb = true\n", "whole number"),
        ('spill_budget_mb = "60000"\n', "whole number"),
        ('threads = 4\nscratch = "/no/such"\n', "unknown settings: threads"),
        ("scratch = \n", "not valid TOML"),
    ],
)
def test_bad_settings_are_refused(
    tmp_path: Path, clean: pytest.MonkeyPatch, body: str, message: str
) -> None:
    with pytest.raises(ScratchError, match=message):
        configure(settings(tmp_path / "bad.toml", body))
    assert all(name not in os.environ for name in VARIABLES)


def test_a_file_that_cannot_be_read_is_refused(tmp_path: Path, clean: pytest.MonkeyPatch) -> None:
    (tmp_path / "bad.toml").write_bytes(b"scratch = '\xff\xfe'\n")
    with pytest.raises(ScratchError, match="cannot be read"):
        configure(tmp_path / "bad.toml")
    with pytest.raises(ScratchError, match="cannot be read"):
        configure(tmp_path)  # a folder, not a file


@pytest.mark.skipif(os.name != "posix", reason="folder modes are POSIX")
def test_a_scratch_folder_this_account_cannot_write_is_refused(
    tmp_path: Path, clean: pytest.MonkeyPatch
) -> None:
    folder = folder_in(tmp_path)
    folder.chmod(0o500)
    try:
        with pytest.raises(ScratchError, match="cannot write"):
            configure(settings(tmp_path / "a.toml", f'scratch = "{folder.as_posix()}"\n'))
    finally:
        folder.chmod(0o700)


def test_the_home_folder_is_expanded(tmp_path: Path, clean: pytest.MonkeyPatch) -> None:
    folder_in(tmp_path)
    clean.setenv("HOME", str(tmp_path))
    clean.setenv("USERPROFILE", str(tmp_path))
    done = configure(settings(tmp_path / "a.toml", 'scratch = "~/scratch"\n'))
    assert done["TMPDIR"] == str((tmp_path / "scratch").resolve())


def test_the_settings_file_comes_from_the_environment_or_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPARQ_ANALYTICS_CONFIG", "/somewhere/analytics.toml")
    assert scratch.config_path() == Path("/somewhere/analytics.toml")
    monkeypatch.delenv("SPARQ_ANALYTICS_CONFIG")
    assert scratch.config_path() == Path("~/.config/sparq/analytics.toml").expanduser()


MAINS: dict[str, tuple[Callable[[Sequence[str] | None], int], list[str]]] = {
    "history": (history_cli.main, ["--data-dir", "D", "status"]),
    "live": (live_cli.main, ["report", "--data", "D"]),
    "study": (study_cli.main, ["--data-dir", "D", "report"]),
    "survey": (survey_cli.main, ["dump", "--kalshi", "D", "--out", "D"]),
    "weather": (weather_cli.main, ["check", "--data", "D"]),
    "model": (model_cli.main, ["ping"]),
    "paper": (paper_cli.main, ["fit", "--live", "D", "--out", "D"]),
}


@pytest.mark.parametrize("name", sorted(MAINS))
def test_every_command_line_refuses_bad_settings_before_anything_else(
    tmp_path: Path, clean: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    main, argv = MAINS[name]
    clean.setenv("SPARQ_ANALYTICS_CONFIG", str(settings(tmp_path / "bad.toml", "threads = 1\n")))
    assert main([str(tmp_path) if a == "D" else a for a in argv]) == 1
    err = capsys.readouterr().err
    assert "error:" in err and "unknown settings: threads" in err
