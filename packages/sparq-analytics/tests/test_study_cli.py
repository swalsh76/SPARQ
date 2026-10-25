import json
import runpy
import shutil
import sys
from pathlib import Path

import pytest
from sparq_analytics.study.cli import main
from sparq_analytics.study.synthetic import World, write_world

FAST = ["--resamples", "400", "--min-events", "20"]
WINDOW = [
    "--window-start", "2026-01-02", "--window-end", "2026-04-20",
    "--fit-end", "2026-02-15", "--validate-end", "2026-02-24",
]  # fmt: skip
SEGMENTS = {
    "segments": [
        {"price_hi_e6": 250_000, "side": "no", "reason": "longshots bought on the NO side"},
        {"price_lo_e6": 750_000, "side": "yes", "reason": "favourites bought on the YES side"},
    ]
}


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("cliworld")
    write_world(World(events=1500, trades_per_market=6, bias=0.15, seed=31), base / "kalshi")
    return base


@pytest.fixture
def data(world: Path, tmp_path: Path) -> Path:
    """A private copy of the world, so each test has its own study folder and ledger."""
    copy = tmp_path / "data"
    shutil.copytree(world, copy)
    return copy


def run(data: Path, *args: str) -> int:
    return main(["--data-dir", str(data), *args])


def segments_file(data: Path, content: object = SEGMENTS) -> Path:
    path = data / "segments.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def verdict_args(data: Path, *extra: str) -> list[str]:
    return [
        "verdict", "--segments", str(segments_file(data)), *WINDOW, "--share", "1.0", *FAST, *extra,
    ]  # fmt: skip


def test_snapshot_writes_the_manifest_and_suggests_a_window(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(data, "snapshot") == 0
    snapshot = json.loads((data / "study" / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["suggested"]["window_start"] == "2026-01-02"
    assert snapshot["suggested"]["fit_end"] < snapshot["suggested"]["validate_end"]
    assert "Suggested window" in capsys.readouterr().out


def test_reference_writes_the_table(data: Path) -> None:
    assert run(data, "reference") == 0
    rows = json.loads((data / "study" / "reference.json").read_text(encoding="utf-8"))
    assert {r["category"] for r in rows} == {"sports", "politics"}


def test_explore_reads_only_the_fit_and_validate_parts(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(data, "explore", *WINDOW, "--share", "0.5", *FAST) == 0
    explore = json.loads((data / "study" / "explore.json").read_text(encoding="utf-8"))
    assert explore["config"]["sample_share"] == 0.5
    assert explore["split"]["events"]["test"] > 0  # the split exists ...
    assert not (data / "study" / "prereg-ledger.json").exists()  # ... but the test was not opened
    assert (data / "study" / "cells-validate.csv").exists()
    assert "Brier" in capsys.readouterr().out


def test_explore_works_out_the_share_from_the_ceiling_when_none_is_given(data: Path) -> None:
    assert run(data, "explore", *WINDOW, "--max-trades", "9000", *FAST) == 0
    explore = json.loads((data / "study" / "explore.json").read_text(encoding="utf-8"))
    assert 0.0 < explore["config"]["sample_share"] < 1.0


def test_register_shows_the_digest_without_opening_the_test(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["register", "--segments", str(segments_file(data)), *WINDOW, "--share", "1.0", *FAST]
    assert run(data, *args) == 0
    out = capsys.readouterr().out
    assert "2 segments" in out and "pre-registration digest" in out
    assert not (data / "study" / "prereg-ledger.json").exists()


def test_verdict_refuses_without_the_open_test_flag(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(data, *verdict_args(data)) == 1
    assert "--open-test" in capsys.readouterr().err
    assert not (data / "study" / "prereg-ledger.json").exists()


@pytest.mark.parametrize(
    "missing", ["--window-start", "--window-end", "--share", "--fit-end", "--validate-end"]
)
def test_verdict_refuses_when_a_setting_is_not_spelled_out(data: Path, missing: str) -> None:
    args = verdict_args(data, "--open-test")
    index = args.index(missing)
    del args[index : index + 2]
    assert run(data, *args) == 1
    assert not (data / "study" / "prereg-ledger.json").exists()


def test_verdict_opens_the_test_once_and_locks_the_pre_registration(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(data, *verdict_args(data, "--open-test")) == 0
    result = json.loads((data / "study" / "verdict.json").read_text(encoding="utf-8"))
    assert result["verdict"]["outcome"] in ("GO", "NO-GO", "INCONCLUSIVE")
    assert len(result["verdict"]["segments"]) == 2
    assert (data / "study" / "prereg-ledger.json").exists()
    assert "Verdict:" in capsys.readouterr().out
    # the same registration may be read again; a different one is refused
    assert run(data, *verdict_args(data, "--open-test")) == 0
    changed = {"segments": [{"price_hi_e6": 100_000, "side": "no"}]}
    path = segments_file(data, changed)
    args = ["verdict", "--segments", str(path), *WINDOW, "--share", "1.0", *FAST, "--open-test"]
    assert run(data, *args) == 1
    assert "locked" in capsys.readouterr().err


def test_an_exploratory_flag_in_the_segments_file_reaches_the_verdict_and_the_table(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    content = {
        "segments": [
            SEGMENTS["segments"][0],
            {**SEGMENTS["segments"][1], "exploratory": True},
        ]
    }
    args = ["verdict", "--segments", str(segments_file(data, content)), *WINDOW]
    assert run(data, *args, "--share", "1.0", *FAST, "--open-test") == 0
    result = json.loads((data / "study" / "verdict.json").read_text(encoding="utf-8"))
    flags = [s["exploratory"] for s in result["verdict"]["segments"]]
    assert flags == [False, True]
    assert result["verdict"]["tail"] == pytest.approx(0.025)  # one deciding segment
    assert capsys.readouterr().out.count("exploratory, does not decide") == 1


def test_report_renders_every_section_that_has_a_file(
    data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(data, "snapshot")
    run(data, "reference")
    run(data, "explore", *WINDOW, "--share", "1.0", *FAST)
    run(data, *verdict_args(data, "--open-test"))
    capsys.readouterr()
    target = tmp_path / "edge.md"
    assert run(data, "report", "--out", str(target)) == 0
    text = target.read_text(encoding="utf-8")
    for heading in (
        "## Data snapshot",
        "## Which end time is nearest the last trade",
        "## Calibration by price band (validate part)",
        "## Benchmark ladder (fitted on fit, scored on test)",
        "## Verdict on the test part",
    ):
        assert heading in text
    assert run(data, "report") == 0
    assert "## Verdict on the test part" in capsys.readouterr().out


def test_report_of_an_empty_folder_is_empty(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(data, "report") == 0
    assert capsys.readouterr().out.strip() == ""


def test_a_missing_segments_file_option_is_a_usage_error(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["register", *WINDOW, "--share", "1.0"]
    assert run(data, *args) == 1
    assert "--segments" in capsys.readouterr().err


def test_a_folder_without_data_is_reported_not_crashed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path, "explore", *WINDOW, "--share", "1.0") == 1
    assert "error:" in capsys.readouterr().err


def test_running_the_package_runs_the_command_line(
    data: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["sparq_analytics.study", "--data-dir", str(data), "report"])
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("sparq_analytics.study", run_name="__main__")
    assert caught.value.code == 0


def test_a_segments_file_keeps_every_field_and_drops_only_the_reason(tmp_path: Path) -> None:
    from sparq_analytics.study.cli import _segments
    from sparq_analytics.study.segments import Segment

    path = tmp_path / "segments.json"
    path.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "price_lo_e6": 40_000,
                        "price_hi_e6": 200_000,
                        "tleft_lo_s": 600,
                        "tleft_hi_s": 3_600,
                        "category": "sports",
                        "side": "no",
                        "reason": "why it was chosen",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert _segments(path) == (Segment(40_000, 200_000, 600, 3_600, "sports", "no"),)


def test_explore_needs_the_split_dates(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = [
        "explore",
        "--window-start",
        "2026-01-02",
        "--window-end",
        "2026-04-20",
        "--share",
        "1.0",
    ]
    assert run(data, *args) == 1
    assert "--fit-end" in capsys.readouterr().err
