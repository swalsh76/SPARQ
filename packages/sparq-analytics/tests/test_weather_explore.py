import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from sparq_analytics.weather import explore as explore_module
from sparq_analytics.weather.archive import parse_csv
from sparq_analytics.weather.bounds import REPORT_SCHEMA, report_bounds
from sparq_analytics.weather.check import scope_markets
from sparq_analytics.weather.cli import main
from sparq_analytics.weather.explore import (
    explore,
    part_days,
    settled_low,
    timeline,
)
from sparq_analytics.weather.stations import WeatherDataError
from test_weather_check import market, write_history

SEP15 = date(2026, 9, 15)
NY_RULES = "If the maximum temperature recorded at New York City (CLINYC) for Sep 15, 2026, ..."
RAIN_RULES = "precipitation at CLINYC in NYC"


def at(hour: int, minute: int, day: int = 15) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def ms(hour: int, minute: int, day: int = 15) -> int:
    return int(at(hour, minute, day).timestamp() * 1000)


# Central Park on 15 September 2026 (EDT: the standard day starts 05:00 UTC, the civil day 04:00)
DAY = (
    "station,valid,metar\n"
    # 25.0 C is 77 F
    "NYC,2026-09-15 12:51,KNYC 151251Z AUTO 10SM 25/10 RMK AO2 T02500100\n"
    # 28.0 C is 82.4, so 82 F; 0.04 inch of rain since 12:51
    "NYC,2026-09-15 18:51,KNYC 151851Z AUTO 10SM 28/10 RMK AO2 T02800100 P0004\n"
    # 27.0 C (80 F), with a six-hour maximum of 30.0 C (86 F) for 15:51 to 21:51
    "NYC,2026-09-15 21:51,KNYC 152151Z AUTO 10SM 27/10 RMK AO2 T02700100 10300\n"
)
# each report counts 10 minutes later: 77 F at 13:01, 82 F and rain at 19:01, 86 F at 22:01
MARKETS = [
    market("KXHIGHNY-26SEP15-B78.5", "no", "between", "78", "79", NY_RULES),  # no from 81: 19:01
    market("KXHIGHNY-26SEP15-T85", "yes", "greater", "85", None, NY_RULES),  # yes from 87: never
    market("KXHIGHNY-26SEP15-T75", "no", "less", None, "75", NY_RULES),  # no from 76: 13:01
    # no from 83 at 22:01, but Kalshi settled it yes: a wrong call, paid in full
    market("KXHIGHNY-26SEP15-B80.5", "yes", "between", "80", "81", NY_RULES),
    market("KXRAIN-26SEP15-NYC", "yes", "greater", "0", None, RAIN_RULES),  # yes at 19:01
    market("KXHIGHNY-26SEP15-X1", "no", "weird", "1", None, NY_RULES),  # no threshold at all
]
B78, T85, T75, B80, RAIN, _ = (m["ticker"] for m in MARKETS)
# (ticker, time, taker side, yes price, contracts in hundredths)
TRADES = [
    (B78, at(18, 0), "no", 300_000, 100),  # before the call: left out
    (B78, at(19, 30), "no", 100_000, 1_000),  # ours: 0.91 for 10, fee 0.06, +0.84
    (B78, at(19, 40), "yes", 50_000, 100),  # the ruled-out side
    (B78, at(20, 0), "no", 20_000, 500),  # ours: 0.99 for 5, fee 0.01, +0.04
    (T75, at(14, 0), "no", 10_000, 200),  # ours at a dollar: nothing made, no fee
    (T85, at(20, 0), "yes", 500_000, 100),  # never called
    (B80, at(22, 30), "no", 200_000, 100),  # ours at 0.81, lost: -0.81 and fee 0.02
    (RAIN, at(19, 5), "yes", 600_000, 100),  # ours: 0.61, fee 0.02, +0.37
    (RAIN, at(19, 6), "yes", 700_000, 100),  # ours: 0.71, fee 0.02, +0.27
]


def write_world(root: Path) -> Path:
    kalshi = write_history(root, MARKETS)
    pl.DataFrame(
        {
            "ticker": ["KXHIGHNY", "KXRAIN"],
            "fee_type": ["quadratic", "quadratic"],
            "fee_multiplier_e6": [1_000_000, 1_000_000],
        }
    ).write_parquet(kalshi / "series.parquet")
    folder = kalshi / "trades" / "date=2026-09-15"
    folder.mkdir(parents=True)
    pl.DataFrame(
        {
            "trade_id": [str(i) for i in range(len(TRADES))],
            "ticker": [t[0] for t in TRADES],
            "created_time": [t[1] for t in TRADES],
            "yes_price_e6": [t[3] for t in TRADES],
            "count_e2": [t[4] for t in TRADES],
            "taker_side": [t[2] for t in TRADES],
        },
        schema_overrides={"yes_price_e6": pl.Int32, "count_e2": pl.Int64},
    ).write_parquet(folder / "trades.parquet")
    data = root / "weather" / "archive"
    data.mkdir(parents=True)
    parse_csv(DAY, "KNYC", "routine").write_parquet(data / "KNYC-20260801-20261024.parquet")
    return kalshi


def run(tmp_path: Path) -> tuple[dict[str, Any], pl.DataFrame]:
    kalshi = write_world(tmp_path)
    reports = pl.read_parquet(tmp_path / "weather" / "archive" / "*.parquet")
    return explore(
        kalshi, scope_markets(kalshi, SEP15, SEP15), report_bounds(reports), SEP15, SEP15
    )


def test_the_parts_are_fit_and_validate_and_never_test() -> None:
    assert part_days("fit") == (date(2026, 8, 7), date(2026, 9, 28))
    assert part_days("validate") == (date(2026, 9, 29), date(2026, 10, 10))
    with pytest.raises(WeatherDataError, match="test part"):
        part_days("test")


def test_a_bound_counts_only_where_both_kinds_of_day_allow_it() -> None:
    reports = (
        pl.DataFrame(
            [
                # 00:10 and 00:50 EDT: the clock's 15th but the standard 14th, hot, then rain
                {"valid_ms": ms(4, 10), "metar": "KNYC 150410Z AUTO 10SM 30/10 RMK AO2 T03000100"},
                {"valid_ms": ms(4, 50), "metar": "KNYC 150450Z AUTO 10SM 29/10 RMK AO2 P0003"},
                {"valid_ms": ms(12, 51), "metar": "KNYC 151251Z AUTO 10SM 25/10 RMK AO2 T02500100"},
            ],
        )
        .with_columns(station=pl.lit("KNYC"), kind=pl.lit("routine"))
        .select(list(REPORT_SCHEMA))
    )
    line = timeline(report_bounds(reports)).filter(pl.col("day") == SEP15)
    assert line["counted_ms"].to_list() == [ms(4, 20), ms(5, 0), ms(13, 1)]
    # the standard day has nothing before 13:01, then only 77 F: the civil day's 86 F and its
    # rain never count
    assert line["bound_f"].to_list() == [None, None, 77]
    assert line["hourly_f"].to_list() == [None, None, 77]
    assert line["rain"].to_list() == [False, False, False]


def test_ours_are_the_takers_of_the_certain_side_after_the_call_priced_one_cent_worse(
    tmp_path: Path,
) -> None:
    _, after = run(tmp_path)
    assert T85 not in after["ticker"].to_list()  # never called
    assert after.filter(pl.col("ticker") == B78).height == 3  # the 18:00 trade came before
    ours = after.filter(pl.col("ours")).sort("ticker", "t_ms")
    assert ours.select("ticker", "price_e6", "fee_e6", "profit_e6").rows() == [
        (B78, 910_000, 60_000, 840_000),
        (B78, 990_000, 10_000, 40_000),
        (B80, 810_000, 20_000, -830_000),
        (T75, 1_000_000, 0, 0),
        (RAIN, 610_000, 20_000, 370_000),
        (RAIN, 710_000, 20_000, 270_000),
    ]
    assert ours.filter(pl.col("ticker") == B78)["after_s"].to_list() == [1_740.0, 3_540.0]
    assert ours.filter(pl.col("ticker") == B78)["hour"].to_list() == [15, 16]  # EDT


def test_capacity_has_an_upper_bound_of_every_trade_and_a_lower_of_the_first(
    tmp_path: Path,
) -> None:
    summary, _ = run(tmp_path)
    upper, lower = summary["upper"], summary["lower"]
    assert (upper["trades"], upper["markets"], upper["city_days"]) == (6, 4, 1)
    assert upper["profit_dollars"] == 0.69
    assert upper["fee_dollars"] == 0.13
    assert upper["contracts"] == 20.0
    assert upper["dollars_a_day"] == 0.69
    assert lower["trades"] == 4
    assert lower["profit_dollars"] == 0.38  # 0.84 + 0 - 0.83 + 0.37
    # one city-day: the interval collapses to the point
    assert upper["cents_per_contract"] == upper["cents_per_contract_lower"] == 3.45
    assert summary["trades_fee_unknown"] == 0


def test_repricing_reports_the_first_and_last_of_ours_and_the_wrong_calls(
    tmp_path: Path,
) -> None:
    summary, _ = run(tmp_path)
    reprice = summary["repricing"]
    assert reprice["called_markets"] == 4
    assert reprice["markets_with_ours"] == 4
    # first of ours: rain 240 s, B78 1,740 s, B80 1,740 s, T75 3,540 s
    assert reprice["first_ours_s"]["median"] == 1_740.0
    assert reprice["last_profitable_s"]["max"] == 3_540.0  # B78 at 20:00
    assert (reprice["ruled_out_trades"], reprice["ruled_out_contracts"]) == (1, 1.0)
    assert reprice["wrong_calls"] == [{"ticker": B80, "call": "no", "result": "yes"}]


def test_the_gap_measures_the_settled_high_above_the_hourly_reports(tmp_path: Path) -> None:
    summary, _ = run(tmp_path)
    gap = summary["gap"]
    # settled at least 86 (above 85), hourly at most 82; the six-hour maximum showed 86 three hours
    # after the hourly peak counted
    assert (gap["city_days"], gap["known"], gap["above_hourly"]) == (1, 1, 1)
    assert gap["by_degrees"] == {"4": 1}
    assert gap["reached_later"] == 1
    assert gap["hours_later"]["median"] == 3.0
    assert gap["never_reached"] == 0


@pytest.mark.parametrize(
    ("strike", "floor", "expected"),
    [("between", 78.0, 78), ("between", 78.5, 79), ("greater", 85.0, 86), ("greater", 85.5, 86)],
)
def test_the_settled_high_is_at_least_the_winners_lowest_degree(
    strike: str, floor: float, expected: int
) -> None:
    assert settled_low(strike, floor) == expected


def test_a_winner_without_a_lower_edge_leaves_the_high_unknown() -> None:
    assert settled_low("less", None) is None
    assert settled_low("weird", 70.0) is None


def test_explore_refuses_an_empty_window_and_a_missing_trade_folder(tmp_path: Path) -> None:
    kalshi = write_world(tmp_path)
    empty = scope_markets(kalshi, date(2026, 8, 7), date(2026, 8, 8))
    with pytest.raises(WeatherDataError, match="no settled market"):
        explore(kalshi, empty, pl.DataFrame(), date(2026, 8, 7), date(2026, 8, 8))
    reports = pl.read_parquet(tmp_path / "weather" / "archive" / "*.parquet")
    markets = scope_markets(kalshi, SEP15, SEP15)
    with pytest.raises(WeatherDataError, match="no trades"):
        explore(kalshi, markets, report_bounds(reports), date(2026, 9, 1), date(2026, 9, 2))


def test_explore_writes_its_summary_and_trades(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    kalshi = write_world(tmp_path)
    data = tmp_path / "weather"
    monkeypatch.setitem(explore_module.PARTS, "fit", (SEP15, SEP15))
    argv = ["explore", "--data", str(data), "--kalshi", str(kalshi), "--part", "fit"]
    assert main(argv) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["upper"]["profit_dollars"] == 0.69
    assert "by_series" not in printed
    written = json.loads((data / "explore-fit.json").read_text(encoding="utf-8"))
    assert {row["hour"] for row in written["by_hour"]} == {10, 15, 16, 18}
    assert pl.read_parquet(data / "explore-fit-trades.parquet").height == 7


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["explore", "--data", "d", "--kalshi", "k"], "--part is required"),
        (["explore", "--data", "d", "--kalshi", "k", "--part", "test"], "test part"),
    ],
)
def test_explore_needs_a_part_and_refuses_the_test_part(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 1
    assert message in capsys.readouterr().err


def test_markets_without_a_threshold_are_counted_but_never_called(tmp_path: Path) -> None:
    summary, _ = run(tmp_path)
    assert (summary["markets"], summary["with_threshold"]) == (6, 5)


def test_a_window_without_a_call_measures_nothing(tmp_path: Path) -> None:
    kalshi = write_world(tmp_path)
    reports = pl.read_parquet(tmp_path / "weather" / "archive" / "*.parquet")
    markets = scope_markets(kalshi, SEP15, SEP15).filter(pl.col("ticker") == T85)
    summary, after = explore(kalshi, markets, report_bounds(reports), SEP15, SEP15)
    assert after.is_empty()
    assert summary["upper"]["trades"] == summary["lower"]["trades"] == 0
    assert summary["upper"]["cents_per_contract"] is None
    assert summary["repricing"]["first_ours_s"] == {"median": None, "p90": None, "max": None}
