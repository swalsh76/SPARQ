import hashlib
import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from paperhelpers import FEES, write_paper_live
from sparq_analytics.paper import quoter, trial
from sparq_analytics.paper.cli import main
from sparq_analytics.survey.dump import SurveyDataError
from sparq_analytics.survey.lag import frames_of
from surveyhelpers import BASE_TIME, HOUR, lm

RULES = Path(__file__).resolve().parents[3] / "docs" / "research" / "paper-rules.json"
D25, D26, D27 = date(2026, 10, 25), date(2026, 10, 26), date(2026, 10, 27)


def test_the_registered_rules_are_pinned(tmp_path: Path) -> None:
    # S4.2's rules (PLAN §6.4, T6), approved by the owner on 2026-10-29: the file may not change
    assert hashlib.sha256(RULES.read_bytes()).hexdigest() == trial.DIGEST
    rules = trial.registered(RULES)
    assert [r["id"] for r in rules["rules"]] == ["R1", "R2", "R3", "R4"]
    assert rules["shared"]["inside_e6"] == 50_000
    assert rules["shared"]["close_within_ms"] == 7 * 86_400_000
    changed = tmp_path / "paper-rules.json"
    changed.write_text(
        RULES.read_text(encoding="utf-8").replace("50000", "40000"), encoding="utf-8"
    )
    with pytest.raises(SurveyDataError, match="not the registered rules file"):
        trial.registered(changed)


def test_the_window_must_lie_inside_the_copies(tmp_path: Path) -> None:
    live = write_paper_live(tmp_path, days=3)
    assert trial.window(live, D25, D26, 1) == (BASE_TIME, BASE_TIME + 48 * HOUR)
    with pytest.raises(SurveyDataError, match="not inside them"):
        trial.window(live, D25, D27, 1)  # the copies end at 01:00 on the 27th
    with pytest.raises(SurveyDataError, match="ends .* before it starts"):
        trial.window(live, D26, D25, 1)
    with pytest.raises(SurveyDataError, match="fewer than 7 days"):
        trial.window(live, D25, D26, 7)


def test_the_scope_limits_the_quotes_to_the_rules(tmp_path: Path) -> None:
    live = write_paper_live(tmp_path)
    info = quoter.market_info(live, FEES, BASE_TIME, BASE_TIME + 2 * HOUR)
    book = frames_of(live / "ticker" / "2026-10-25T00.parquet")
    scope = quoter.Scope(inside=(50_000,), close_within_ms=86_400_000)
    quotes = quoter.paper_quotes(book, info, lm(0, 0), lm(1, 0), scope)
    # one k only; P1, P2 and P6 close three days out, past the day allowed: P5 alone
    assert set(quotes["k_e6"]) == {50_000} and set(quotes["ticker"]) == {"P5"}


def test_the_trial_marks_fills_to_settlement_and_judges_each_rule(tmp_path: Path) -> None:
    live = write_paper_live(tmp_path, days=3, extra=True)
    out = tmp_path / "out"
    result = trial.run_test(live, FEES, out, RULES, D25, D26, min_days=1)
    assert (result["days_kept"], result["dropped_days"], result["hours_kept"]) == (
        1, ["2026-10-26"], 24,
    )  # fmt: skip
    fills = pl.read_parquet(out / "paper-test-fills.parquet")
    # k = 5 only: P1 an ask and two bids (00:20 and 23:40), P2 an ask, P5 a bid, P7 a bid;
    # nothing from the dropped day
    assert set(fills["k_e6"]) == {50_000}
    assert sorted(zip(fills["ticker"], fills["side"], strict=True)) == [
        ("P1", "ask"), ("P1", "bid"), ("P1", "bid"), ("P2", "ask"), ("P5", "bid"), ("P7", "bid"),
    ]  # fmt: skip
    assert result["fills"] == 6 and result["settled_fills"] == 1  # P5 alone has settled
    r1 = result["by_rule"]["R1"]
    assert (r1["fills"], r1["settled_fills"], r1["unsettled_fills"], r1["size"]) == (5, 1, 4, 10)
    # P5's bid at 25 on 1 contract settled yes: 75 cents, no fee, in 24 hours
    assert r1["settlement"]["net_e6_a_day"] == 750_000 and r1["contracts_settled"] == 1.0
    assert r1["settlement"]["cents_a_contract"] == pytest.approx(75.0)
    assert r1["settlement"]["interval_e6_a_day"]["clusters"] == 1  # no spread to bootstrap
    assert r1["mid_1h"]["marked_fills"] == 3  # P1 twice and P7; P2 has no mid, P5 is closed
    assert r1["mid_1h"]["net_e6_a_day"] > 0
    assert r1["go"] is False  # one settled cluster has no lower bound
    r2 = result["by_rule"]["R2"]
    assert r2["size"] == 50 and r2["settlement"]["net_e6_a_day"] == 750_000  # a 1-contract trade
    r3 = result["by_rule"]["R3"]
    assert r3["categories"] == ["Sports"] and r3["fills"] == 4  # P1 and P5; not P7 (Economics)
    r4 = result["by_rule"]["R4"]
    assert (r4["state"], r4["fills"], r4["settled_fills"]) == ("one_sided", 1, 0)
    assert r4["settlement"]["cents_a_contract"] is None and r4["go"] is False
    assert result["go"] == []
    with pytest.raises(SurveyDataError, match="fewer than 2"):
        trial.run_test(live, FEES, out, RULES, D25, D26, min_days=2)  # the 26th is dropped


def test_test_command_writes_the_verdicts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    live = write_paper_live(tmp_path, days=3)
    kalshi = tmp_path / "kalshi"
    kalshi.mkdir()
    FEES.rename({"series_ticker": "ticker", "multiplier_e6": "fee_multiplier_e6"}).write_parquet(
        kalshi / "series.parquet"
    )
    out = tmp_path / "out"
    argv = [
        "test", "--kalshi", str(kalshi), "--live", str(live), "--out", str(out),
        "--rules", str(RULES), "--from", "2026-10-25", "--to", "2026-10-26",
    ]  # fmt: skip
    assert main([*argv, "--min-days", "1"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["go"] == [] and printed["by_rule"]["R1"]["fills"] == 4
    assert printed["dropped_days"] == ["2026-10-26"]
    assert json.loads((out / "paper-test.json").read_text(encoding="utf-8"))["days_kept"] == 1
    assert main(argv) == 1  # seven days by default
    assert "fewer than 7" in capsys.readouterr().err
    assert main([a for a in argv if a not in ("--from", "2026-10-25")] + ["--min-days", "1"]) == 1
    assert "--from and --to are required" in capsys.readouterr().err
    assert (
        main(
            [
                "fit",
                "--kalshi",
                str(kalshi),
                "--live",
                str(live),
                "--out",
                str(out),
                "--rules",
                str(RULES),
            ]
        )
        == 1
    )
    assert "--rules does not apply to fit" in capsys.readouterr().err
    (live / "settlements.parquet").unlink()
    assert main([*argv, "--min-days", "1"]) == 1
    assert "no settlements" in capsys.readouterr().err
