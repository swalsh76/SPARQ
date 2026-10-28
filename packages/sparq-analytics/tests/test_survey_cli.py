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
    assert (sports["reactive_payout"], sports["knowing_payout"]) == (0.6, 0.75)
    assert sports["episodes_a_day"] == 18
    # reactive: a cent on 396 contracts a day in 3 of 5 sides; 99 cents on the 282 contracts
    # a day a reversal's dump would fill behind the queues, in 2 of 5; the fee
    assert (sports["gain_e6_onset_10000"], sports["loss_e6_onset_10000"]) == (
        2_376_000,
        111_672_000,
    )
    assert sports["dollars_a_day_e6_onset_10000"] == -109_338_624
    # knowing: 150 contracts a day in 3 of 4; a false regime's 150 a day in 1 of 4
    assert (sports["gain_e6_hour_before_10000"], sports["loss_e6_hour_before_10000"]) == (
        1_125_000,
        37_125_000,
    )
    assert sports["dollars_a_day_e6_hour_before_10000"] == -36_000_000
    assert sports["capital_e6_onset_10000"] == 99_371_250
    assert sports["collateral_e6_onset_10000"] == 74_250_000  # 18 bids of $99 for an hour each
    weather = implied["by_category"]["Climate and Weather"]
    assert (weather["reactive_payout"], weather["knowing_payout"]) == (1.0, 1.0)
    assert weather["dollars_a_day_e6_onset_10000"] == 6_000_000
    assert weather["dollars_a_day_e6_hour_before_10000"] == 6_000_000
    assert weather["capital_e6_onset_10000"] == 272_250_000
    assert weather["collateral_e6_onset_10000"] is None  # no closed episode
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
    alone = json.loads(capsys.readouterr().out)
    assert alone["episodes"] == 5
    # without the history there is no reversal fill to report
    assert "rev_fills_a_day_e2_onset_10000" not in alone["by_category"]["Sports"]


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
    frames = {
        f"{kind}_a_day_{unit}_{p}_{s}": 1_000
        for kind, unit in (("fills", "e2"), ("fee", "e6"), ("rev_fills", "e2"))
        for p in ("onset", "hour_before")
        for s in (10_000, 100_000)
    }
    span = {"episodes_a_day": 4, "closed_hours_median": 0.5}
    live = {"by_category": {"Culture": frames | span}}
    out = combine({"by_category": {}}, live)
    culture = out["by_category"]["Culture"]
    assert (culture["reactive_payout"], culture["knowing_payout"]) == (None, None)
    assert culture["dollars_a_day_e6_onset_10000"] is None
    assert not culture["clears_onset_10000"] and out["cleared"] == []
    # only reversals: every reversal fill loses, and the capital tied is unknown
    past = {
        "winning_99_markets": 0,
        "reversal_markets": 2,
        "true_regimes": 0,
        "false_regimes": 0,
        "settle_ms_median": None,
    }
    lost = combine({"by_category": {"Culture": past}}, live)["by_category"]["Culture"]
    assert (lost["reactive_payout"], lost["knowing_payout"]) == (0.0, None)
    assert lost["gain_e6_onset_10000"] == 0
    assert lost["loss_e6_onset_10000"] == 9_900_000  # 10 contracts a day at 99 cents
    assert lost["dollars_a_day_e6_onset_10000"] == -9_901_000  # and the fee
    assert lost["capital_e6_onset_10000"] is None
    assert lost["collateral_e6_onset_10000"] == 8_250_000  # 4 bids of $99 for half an hour
    assert lost["dollars_a_day_e6_hour_before_10000"] is None
    # a live-only reading has no reversal fills, so the loss and the dollars are unknown
    bare = {k: v for k, v in frames.items() if not k.startswith("rev_fills")} | span
    alone = combine({"by_category": {"Culture": past}}, {"by_category": {"Culture": bare}})
    assert alone["by_category"]["Culture"]["loss_e6_onset_10000"] is None
    assert alone["by_category"]["Culture"]["dollars_a_day_e6_onset_10000"] is None


def test_running_the_package_runs_the_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["survey", "--help"])
    with pytest.raises(SystemExit) as stop:
        runpy.run_module("sparq_analytics.survey", run_name="__main__")
    assert stop.value.code == 0
