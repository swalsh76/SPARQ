import json
import runpy
import sys
from pathlib import Path

import polars as pl
import pytest
from sparq_analytics.survey.cli import main
from sparq_analytics.survey.implied import combine
from surveyhelpers import write_history, write_live


def test_dump_writes_both_readings_and_prints_the_implied_dollars(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    kalshi, live, out = write_history(tmp_path), write_live(tmp_path), tmp_path / "out"
    argv = ["dump", "--kalshi", str(kalshi), "--live", str(live), "--out", str(out)]
    assert main([*argv, "--cut", "2026-09-15"]) == 0
    implied = json.loads(capsys.readouterr().out)
    assert implied["cleared"] == []
    sports = implied["by_category"]["Sports"]
    assert sports["payout"] == 0.75
    # 396 contracts a day; a cent won on three quarters, 99 cents lost on a quarter; the fee
    assert sports["dollars_a_day_e6_onset_10000"] == -95_082_624
    assert sports["capital_e6_onset_10000"] == 99_371_250
    weather = implied["by_category"]["Climate and Weather"]
    assert weather["dollars_a_day_e6_onset_10000"] == 6_000_000
    assert weather["dollars_a_day_e6_hour_before_10000"] == 6_000_000
    assert weather["capital_e6_onset_10000"] == 272_250_000
    assert not weather["clears_onset_10000"]
    for name in ("dump-history.json", "dump-live.json", "dump.json"):
        assert json.loads((out / name).read_text(encoding="utf-8"))
    assert pl.read_parquet(out / "dump-regimes.parquet").height == 5
    assert pl.read_parquet(out / "dump-episodes.parquet").height == 5


def test_dump_can_do_one_reading(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kalshi, live, out = write_history(tmp_path), write_live(tmp_path), tmp_path / "out"
    base = ["dump", "--kalshi", str(kalshi), "--out", str(out), "--cut", "2026-09-15"]
    assert main([*base, "--only", "history"]) == 0
    assert json.loads(capsys.readouterr().out)["regimes"] == 5
    assert not (out / "dump.json").exists()
    assert main([*base, "--live", str(live), "--only", "live"]) == 0
    assert json.loads(capsys.readouterr().out)["episodes"] == 5


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["dump", "--live", "l", "--out", "o"], "--kalshi is required"),
        (["dump", "--kalshi", "k", "--out", "o", "--cut", "2026-10-11"], "held out"),
    ],
)
def test_a_bad_command_line_or_a_held_out_cut_exits_one(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    argv = [str(tmp_path / a) if a in ("k", "l", "o") else a for a in argv]
    if "--kalshi" in argv:
        write_history(tmp_path)
        argv[argv.index("--kalshi") + 1] = str(tmp_path / "kalshi")
    assert main(argv) == 1
    assert message in capsys.readouterr().err


def test_a_category_without_a_history_regime_has_no_dollars() -> None:
    live = {
        "by_category": {
            "Culture": {
                f"{kind}_a_day_{unit}_{p}_{s}": 1_000
                for kind, unit in (("fills", "e2"), ("fee", "e6"))
                for p in ("onset", "hour_before")
                for s in (10_000, 100_000)
            }
        }
    }
    out = combine({"by_category": {}}, live)
    culture = out["by_category"]["Culture"]
    assert (culture["payout"], culture["regimes_in_history"]) == (None, 0)
    assert culture["dollars_a_day_e6_onset_10000"] is None
    assert not culture["clears_onset_10000"] and out["cleared"] == []
    # only false regimes: the bid loses on every fill, and the capital tied is unknown
    past = {"true_regimes": 0, "false_regimes": 2, "settle_ms_median": None}
    lost = combine({"by_category": {"Culture": past}}, live)["by_category"]["Culture"]
    assert lost["payout"] == 0.0
    assert lost["dollars_a_day_e6_onset_10000"] == -9_901_000  # 10 contracts at -99c, the fee
    assert lost["capital_e6_onset_10000"] is None


def test_running_the_package_runs_the_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["survey", "--help"])
    with pytest.raises(SystemExit) as stop:
        runpy.run_module("sparq_analytics.survey", run_name="__main__")
    assert stop.value.code == 0
