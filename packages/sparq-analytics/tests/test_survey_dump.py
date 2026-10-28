from datetime import date
from pathlib import Path

import polars as pl
import pytest
from sparq_analytics.survey.dump import (
    SurveyDataError,
    cut_markets,
    near_certain_trades,
    regimes,
    side_extremes,
    summarize,
    trade_files,
)
from surveyhelpers import ms, write_history

SEP15 = date(2026, 9, 15)


def world(tmp_path: Path) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    kalshi = write_history(tmp_path)
    markets = cut_markets(kalshi, SEP15)
    files = trade_files(kalshi, SEP15, SEP15)
    extremes = side_extremes(files, markets)
    near = near_certain_trades(files, markets)
    return markets, extremes, near, regimes(near, extremes)


def test_the_cut_markets_are_settled_closing_by_the_cut_and_end_at_the_earlier_time(
    tmp_path: Path,
) -> None:
    markets = cut_markets(write_history(tmp_path), SEP15)
    assert sorted(markets["ticker"]) == ["A", "B", "C", "D", "G"]  # E after the cut, F unsettled
    by = {row["ticker"]: row for row in markets.to_dicts()}
    assert by["B"]["end_ms"] == ms(22) < by["B"]["close_ms"]  # settled before its close
    assert by["A"]["end_ms"] == ms(20)
    assert by["B"]["category"] == "Climate and Weather"
    assert by["G"]["fee_type"] == "quadratic_with_maker_fees"


def test_the_cut_refuses_the_held_out_days(tmp_path: Path) -> None:
    kalshi = write_history(tmp_path)
    with pytest.raises(SurveyDataError, match="held out"):
        cut_markets(kalshi, date(2026, 10, 11))
    with pytest.raises(SurveyDataError, match="no settled market"):
        cut_markets(kalshi, date(2026, 8, 7))
    with pytest.raises(SurveyDataError, match="no trades"):
        trade_files(kalshi, date(2026, 8, 7), date(2026, 8, 8))


def test_each_side_remembers_when_it_last_printed_below_97(tmp_path: Path) -> None:
    _, extremes, _, _ = world(tmp_path)
    by = {row["ticker"]: row for row in extremes.to_dicts()}
    assert (by["A"]["last_yes_below_ms"], by["A"]["last_no_below_ms"]) == (ms(13), ms(17))
    assert (by["B"]["last_yes_below_ms"], by["B"]["last_no_below_ms"]) == (ms(12), None)
    assert (by["D"]["last_yes_below_ms"], by["D"]["last_no_below_ms"]) == (None, ms(10))
    assert "E" not in by and "F" not in by


def test_near_certain_trades_are_priced_on_their_side_with_who_took(tmp_path: Path) -> None:
    _, _, near, _ = world(tmp_path)
    assert near.height == 13
    a = near.filter(pl.col("ticker") == "A").sort("t_ms")
    assert a["cents"].to_list() == [97, 99, 99, 99, 98, 99]
    assert a["taker_bought_dead"].to_list() == [True, True, True, False, True, None]
    assert a["won"].all()
    c = near.filter(pl.col("ticker") == "C").sort("t_ms")
    assert c.select("side", "cents", "won", "taker_bought_dead").rows() == [
        ("no", 99, False, True),
        ("yes", 99, True, True),
    ]
    assert near.filter(pl.col("ticker") == "B").sort("t_ms")["dollars_e6"].to_list() == [
        9_700_000,
        39_600_000,
        7_920_000,
    ]


def test_a_regime_starts_at_the_first_99_after_the_last_print_below_97(tmp_path: Path) -> None:
    _, _, _, regs = world(tmp_path)
    rows = {(r["ticker"], r["side"]): r for r in regs.to_dicts()}
    assert sorted(rows) == [("A", "yes"), ("B", "no"), ("C", "yes"), ("D", "yes"), ("G", "yes")]
    a = rows[("A", "yes")]
    assert (a["start_ms"], a["dump_e2"], a["lifted_e2"], a["won"]) == (ms(14), 3_000, 500, True)
    assert (a["hours_to_end"], a["settle_ms"]) == (6.0, 21_900_000)
    b = rows[("B", "no")]
    assert (b["start_ms"], b["dump_e2"], b["lifted_e2"], b["hours_to_end"]) == (
        ms(11),
        4_000,
        800,
        11.0,
    )
    assert (rows[("C", "yes")]["start_ms"], rows[("C", "yes")]["hours_to_end"]) == (ms(18), 2.0)
    d = rows[("D", "yes")]
    assert (d["won"], d["dump_e2"]) == (False, 2_500)  # the false regime


def test_the_summary_has_the_payout_the_dump_and_the_reversals_per_category(
    tmp_path: Path,
) -> None:
    markets, _, near, regs = world(tmp_path)
    summary = summarize(markets, near, regs, SEP15, SEP15)
    assert (summary["markets"], summary["trades_near_certain"], summary["regimes"]) == (5, 13, 5)
    assert summary["side_unknown_trades"] == 1
    sports = summary["by_category"]["Sports"]
    assert (sports["markets"], sports["true_regimes"], sports["false_regimes"]) == (4, 3, 1)
    assert sports["payout"] == 0.75
    assert (sports["dump_e2"], sports["dump_e6"], sports["lifted_e2"]) == (5_200, 51_480_000, 500)
    assert sports["false_dump_e2"] == 2_500
    assert (sports["reversal_markets"], sports["reversal_e2"]) == (2, 4_000)
    assert (sports["winning_99_markets"], sports["reactive_payout"]) == (3, 0.6)
    assert (sports["hours_to_end_median"], sports["settle_ms_median"]) == (6.0, 21_900_000)
    assert sports["makers_gain_a_day_e6"] == 520_000  # 52 contracts at a cent, over one day
    weather = summary["by_category"]["Climate and Weather"]
    assert (weather["true_regimes"], weather["false_regimes"], weather["payout"]) == (1, 0, 1.0)
    assert (weather["dump_e2"], weather["reversal_markets"]) == (4_000, 0)
    assert weather["reactive_payout"] == 1.0
    levels = {
        (r["category"], r["cents"], r["taker_bought_dead"], r["in_regime"]): r["contracts_e2"]
        for r in summary["levels"]
    }
    assert levels[("Sports", 99, True, True)] == 5_200
    assert levels[("Sports", 99, True, False)] == 2_000
    assert levels[("Sports", 97, True, False)] == 1_000
    assert levels[("Sports", 98, True, True)] == 700
    assert levels[("Sports", 99, False, True)] == 500
    assert levels[("Climate and Weather", 99, False, True)] == 800
    assert [(r["series_ticker"], r["dump_e2"]) for r in summary["top_series"]] == [
        ("KXNFLGAME", 4_200),
        ("KXRAIN", 4_000),
        ("KXMAKER", 1_000),
    ]
