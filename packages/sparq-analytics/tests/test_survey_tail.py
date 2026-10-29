import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from sparq_analytics.survey import lag, tail
from sparq_analytics.survey.cli import main
from sparq_analytics.survey.dump import SurveyDataError
from sparq_analytics.survey.queue import fee_terms
from surveyhelpers import BASE_TIME, HOUR
from tailhelpers import FEES, write_tail_history, write_tail_live

CUT = date(2026, 9, 16)


def test_trade_days_come_from_the_folder(tmp_path: Path) -> None:
    kalshi = write_tail_history(tmp_path)
    assert tail.trade_days(kalshi) == [date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16)]
    (tmp_path / "empty" / "trades").mkdir(parents=True)
    with pytest.raises(SurveyDataError, match="no trade files"):
        tail.trade_days(tmp_path / "empty")
    # a missing day in the middle would read as a day without trades: refused
    (kalshi / "trades" / "date=2026-09-15" / "trades.parquet").unlink()
    with pytest.raises(SurveyDataError, match="1 trade day\\(s\\) missing"):
        tail.trade_days(kalshi)


def test_volume_by_tier_and_category(tmp_path: Path) -> None:
    kalshi = write_tail_history(tmp_path)
    reading = tail.volume(kalshi, tail.trade_days(kalshi))
    assert (reading["first_day"], reading["last_day"], reading["days"]) == (
        "2026-09-14", "2026-09-16", 3.0,
    )  # fmt: skip
    assert (reading["open_markets"], reading["traded_markets"], reading["silent_markets"]) == (
        69, 69, 1,
    )  # fmt: skip
    assert (reading["unlisted_traded_markets"], reading["unknown_life_markets"]) == (1, 1)  # U1
    assert reading["silent_by_category"] == {"Sports": 1}  # V3; V4 closed before the window
    thin = reading["by_tier"]["under 100"]
    assert thin["markets"] == 67 and thin["trades_a_day"] == 24.0  # 72 trades over 3 days
    assert thin["contracts_a_day"] == 25.0 and thin["dollars_e6_a_day"] == 22_403_333
    mid = reading["by_tier"]["100 to 1,000"]  # V2: 200 contracts in an hour-long market
    assert (mid["markets"], mid["dollars_e6_a_day"]) == (1, 33_333_333)
    assert mid["contracts_a_day"] == pytest.approx(200 / 3)
    cells = reading["by_tier_and_category"]
    assert set(cells) == {
        "under 100 | Economics",
        "under 100 | Politics",
        "under 100 | Sports",
        "100 to 1,000 | Crypto",
        "unknown | unknown",
    }
    assert cells["unknown | unknown"]["dollars_e6_a_day"] == 166_666  # U1: $0.50 over 3 days
    sports = cells["under 100 | Sports"]  # V1, S1, S2, S3, S6
    assert (sports["markets"], sports["dollars_e6_a_day"]) == (5, 2_920_000)
    assert sports["contracts_a_day"] == pytest.approx(13 / 3)
    assert cells["under 100 | Economics"]["contracts_a_day"] == 20.0


def test_spreads_by_tier(tmp_path: Path) -> None:
    live = write_tail_live(tmp_path)
    reading = tail.live_reading(live, FEES)
    assert (reading["hours"], reading["markets_with_frames"], reading["outages"]) == (2, 4, 1)
    spreads = reading["spreads_by_tier"]
    # L1: 30 minutes at 4 cents, 30 at 30, then an hour with the ask side empty (a 100-cent ask
    # of no size, as the feed shows it); L2: 4 cents all along; both in the lowest tier
    assert spreads["under 100"] == {
        "markets": 2,
        "median_spread_cents": 10.5,
        "two_sided_share": pytest.approx(11 / 14),  # L1 60 of 105 minutes, L2 all of its 60
    }
    assert spreads["10,000 and more"] == {
        "markets": 1,
        "median_spread_cents": 80.0,
        "two_sided_share": 1.0,
    }
    assert set(spreads) == {"under 100", "10,000 and more", "unknown"}
    assert spreads["unknown"] == {"markets": 1, "median_spread_cents": 2.0, "two_sided_share": 1.0}
    assert reading["spreads_by_tier_and_category"]["unknown | unknown"]["markets"] == 1
    assert reading["spreads_by_tier_and_category"]["under 100 | Sports"]["markets"] == 2


def test_half_spread_by_state_and_the_thin_bar(tmp_path: Path) -> None:
    live = write_tail_live(tmp_path)
    reading = tail.live_reading(live, FEES)
    by_state = reading["half_spread_by_state"]
    assert by_state["contested"] == {  # L1's 1 contract at a 4-cent spread, L2's 10 at 4 cents
        "markets": 2,
        "trades_a_day": 24.0,
        "contracts_a_day": 132.0,
        "half_spread_e6_a_day": 2_640_000,
        "fee_e6_a_day": 575_904,  # L2's maker fee of 43,680 and L1's of 4,312
        "net_e6_a_day": 2_064_096,
        "fee_unknown_trades": 0,
    }
    uncontested = by_state["uncontested"]  # L1's 2 contracts at 30 cents, L3's 100,000 at 80
    assert (uncontested["markets"], uncontested["half_spread_e6_a_day"]) == (2, 480_003_600_000)
    assert uncontested["contracts_a_day"] == 1_200_024.0
    assert uncontested["fee_e6_a_day"] == 88_200  # L1's 7,350 on its 2 contracts at 30
    one_sided = by_state["one_sided"]
    assert (one_sided["markets"], one_sided["half_spread_e6_a_day"]) == (1, 0)
    assert one_sided["contracts_a_day"] == 6.0
    assert one_sided["fee_e6_a_day"] == 0  # no half-spread to read, no fee charged
    unknown = by_state["unknown"]  # L3's trade after the outage, its frame before it
    assert (unknown["markets"], unknown["trades_a_day"], unknown["contracts_a_day"]) == (
        1,
        12.0,
        120.0,
    )
    assert unknown["half_spread_e6_a_day"] == 0
    thin = reading["half_spread_thin_by_state"]
    assert thin["contested"]["half_spread_e6_a_day"] == 2_640_000
    assert thin["uncontested"]["half_spread_e6_a_day"] == 3_600_000  # L1 alone: L3 is not thin
    assert (
        reading["half_spread_by_tier_and_state"]["10,000 and more | uncontested"][
            "half_spread_e6_a_day"
        ]
        == 480_000_000_000
    )
    assert reading["half_spread_thin_by_category_and_state"]["Sports | contested"]["markets"] == 2
    assert (reading["thin_uncontested_half_spread_e6_a_day"], reading["clears"]) == (
        3_600_000,
        False,
    )


def test_a_series_without_a_multiplier_has_an_unknown_fee(tmp_path: Path) -> None:
    live = write_tail_live(tmp_path)
    fees = FEES.with_columns(
        multiplier_e6=pl.when(pl.col("series_ticker") == "KXM")
        .then(pl.lit(None, dtype=pl.Int64))
        .otherwise("multiplier_e6")
    )
    by_state = tail.live_reading(live, fees)["half_spread_by_state"]
    contested = by_state["contested"]  # L1's and L2's trades, both in KXM
    assert (contested["fee_e6_a_day"], contested["fee_unknown_trades"]) == (0, 2)
    assert contested["net_e6_a_day"] == contested["half_spread_e6_a_day"]
    uncontested = by_state["uncontested"]  # L1's trade at 30 cents (KXM) and L3's (KXB, known)
    assert (uncontested["fee_e6_a_day"], uncontested["fee_unknown_trades"]) == (0, 1)


def test_live_reading_without_trades(tmp_path: Path) -> None:
    live = write_tail_live(tmp_path, trades=False)
    reading = tail.live_reading(live, FEES)
    assert reading["spreads_by_tier"]["under 100"]["markets"] == 3  # all untraded: lowest tier
    assert reading["half_spread_by_state"] == {} and reading["clears"] is False
    assert reading["thin_uncontested_half_spread_e6_a_day"] == 0


def test_day_before_takes_the_last_trade_in_the_window(tmp_path: Path) -> None:
    kalshi = write_tail_history(tmp_path)
    found, counts = tail.day_before(kalshi, CUT)
    assert counts == {"markets": 70, "priced": 66, "unpriced": 4, "not_in_tail": 2, "in_tail": 64}
    rows = {r["ticker"]: r for r in found.to_dicts()}
    assert rows["S1"]["p_e6"] == 940_000  # not the 97 six hours out, nor the 85 forty hours out
    assert (rows["S1"]["side"], rows["S1"]["surprise"], rows["S1"]["bucket"]) == (
        "yes",
        0,
        "90 to 95",
    )
    assert (rows["S2"]["side"], rows["S2"]["surprise"]) == ("yes", 1)
    assert (rows["S3"]["side"], rows["S3"]["p_e6"], rows["S3"]["surprise"]) == ("no", 920_000, 0)
    assert (rows["S6"]["bucket"], rows["S6"]["implied"]) == ("99 and more", pytest.approx(0.01))
    assert rows["Z07"]["event_ticker"] == "KXZ-E07" and rows["Z07"]["surprise"] == 1
    assert "S4" not in rows and "S5" not in rows and "V1" not in rows


def test_surprise_rates_and_the_clearing_series(tmp_path: Path) -> None:
    kalshi = write_tail_history(tmp_path)
    reading = tail.surprise(kalshi, CUT)
    total = reading["total"]
    assert (total["markets"], total["events"], total["surprises"]) == (64, 63, 61)
    assert total["observed"] == pytest.approx(61 / 64) and total["implied"] == pytest.approx(
        0.050625
    )
    sports = reading["by_category"]["Sports"]
    assert (sports["markets"], sports["events"], sports["surprises"]) == (4, 3, 1)
    assert (sports["observed"], sports["implied"]) == (0.25, pytest.approx(0.06))
    assert sports["lower"] < 0.06 and sports["clears"] is False  # three events: not judged
    assert sports["judged"] is False
    economics = reading["by_category"]["Economics"]
    assert (economics["observed"], economics["lower"], economics["clears"]) == (1.0, 1.0, True)
    assert economics["judged"] is True  # sixty events
    assert economics["implied"] == pytest.approx(0.05)
    buckets = reading["by_bucket"]
    assert (buckets["90 to 95"]["markets"], buckets["90 to 95"]["surprises"]) == (3, 1)
    assert buckets["90 to 95"]["implied"] == pytest.approx(0.23 / 3)
    assert buckets["95 to 99"]["markets"] == 60 and buckets["99 and more"]["surprises"] == 0
    assert buckets["95 to 99"]["clears"] is True and buckets["99 and more"]["judged"] is False
    assert list(reading["by_series"]) == ["KXZ"]  # KXS has 4 markets, under the 50
    assert (reading["series_tested"], reading["expected_by_chance"]) == (1, 0.025)
    [clearing] = reading["clearing_series"]
    assert (clearing["series"], clearing["title"], clearing["category"], clearing["rules"]) == (
        "KXZ", "Zed", "Economics", "Rules Z",
    )  # fmt: skip
    assert clearing["markets"] == 60 and clearing["clears"] is True
    lowered = tail.surprise(kalshi, CUT, min_markets=4)
    assert list(lowered["by_series"]) == ["KXS", "KXZ"]  # KXS has three events: not judged
    assert (lowered["series_tested"], lowered["series_too_few_events"]) == (1, 1)
    assert lowered["by_series"]["KXS"]["observed"] == 0.25
    assert lowered["expected_by_chance"] == 0.025


def test_a_series_with_too_few_events_is_not_judged(tmp_path: Path) -> None:
    kalshi = write_tail_history(tmp_path, only=("S1", "S2", "S3", "S6"))
    reading = tail.surprise(kalshi, CUT, min_markets=4)
    assert list(reading["by_series"]) == ["KXS"] and reading["by_series"]["KXS"]["judged"] is False
    assert reading["clearing_series"] == [] and reading["expected_by_chance"] == 0.0
    assert (reading["series_tested"], reading["series_too_few_events"]) == (0, 1)


def test_a_market_without_a_life_is_in_the_lowest_tier() -> None:
    day = 86_400_000
    frame = pl.DataFrame(
        {
            "ticker": ["A", "B", "C"],
            "open_ms": [0, 0, None],
            "close_ms": [day, 3 * day, None],
            "contracts_e2": [50_000, None, 5_000_000],
        }
    )
    tiers = tail.tiered(frame, 0, 2 * day)
    rows = {r["ticker"]: (r["contracts_a_day"], r["tier"]) for r in tiers.to_dicts()}
    assert rows["A"] == (500.0, "100 to 1,000")  # one day of life
    assert rows["B"] == (0.0, "under 100")  # never traded, two days inside the window
    assert rows["C"] == (None, "unknown")  # no life to speak of


def test_a_judged_cell_need_not_clear() -> None:
    # twenty events, one surprise, priced at 95: observed 0.05 against an implied 0.05
    frame = pl.DataFrame(
        {
            "event_ticker": [f"E{i}" for i in range(20)],
            "surprise": [1] + [0] * 19,
            "implied": [0.05] * 20,
        }
    )
    rate = tail._rate(frame)
    assert (rate["judged"], rate["observed"]) == (True, 0.05)
    assert rate["implied"] == pytest.approx(0.05)
    assert rate["lower"] < 0.05 and rate["clears"] is False


def test_an_hour_without_a_trade_file_has_no_trades(tmp_path: Path) -> None:
    live = write_tail_live(tmp_path)
    missing = lag.trades_of(live, [BASE_TIME + 5 * HOUR], tail.TRADE_COLUMNS)
    assert missing.is_empty() and missing.columns == ["ts_ms", "ticker", "yes_price_e6", "count_e2"]


def test_outage_time_is_taken_out_of_a_frame_s_duration() -> None:
    spans = pl.DataFrame({"start": [0, 0, 50], "end": [100, 100, 60]})
    none = spans.with_columns(out=tail.outage_overlap(pl.col("start"), pl.col("end"), []))
    assert none["out"].to_list() == [0, 0, 0]
    gaps = [(10, 20), (90, 200)]  # ten inside, ten at the end; the third span misses both
    some = spans.with_columns(out=tail.outage_overlap(pl.col("start"), pl.col("end"), gaps))
    assert some["out"].to_list() == [20, 20, 0]


def test_surprise_refuses_a_world_without_a_tail(tmp_path: Path) -> None:
    with pytest.raises(SurveyDataError, match="no trades from"):
        tail.surprise(write_tail_history(tmp_path / "early"), date(2026, 9, 13))  # only V4 closes
    kalshi = write_tail_history(tmp_path / "mid", only=("S4",))
    with pytest.raises(SurveyDataError, match="90 cents"):
        tail.surprise(kalshi, CUT)


def test_tail_command_writes_the_readings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    kalshi, live, out = write_tail_history(tmp_path), write_tail_live(tmp_path), tmp_path / "out"
    assert fee_terms(kalshi).height == 7
    argv = ["tail", "--kalshi", str(kalshi), "--live", str(live), "--out", str(out)]
    assert main([*argv, "--cut", "2026-09-16"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["cleared"] == {"half_spread": False, "surprise_series": ["KXZ"]}
    assert printed["thin_uncontested_half_spread_e6_a_day"] == 3_600_000
    assert printed["surprise_total"]["surprises"] == 61
    for name in ("tail-volume.json", "tail-live.json", "tail-surprise.json", "tail.json"):
        assert json.loads((out / name).read_text(encoding="utf-8"))
    result = json.loads((out / "tail.json").read_text(encoding="utf-8"))
    assert result["volume"]["traded_markets"] == 69 and result["live"]["hours"] == 2
    assert main([*argv, "--cut", "2026-10-11"]) == 1  # past the held-out cut (M3)
    assert "held out" in capsys.readouterr().err
    assert main(["tail", "--live", str(live), "--out", str(out)]) == 1
    assert "--kalshi is required" in capsys.readouterr().err


def test_fee_terms_cover_the_live_series(tmp_path: Path) -> None:
    kalshi = write_tail_history(tmp_path)
    terms = fee_terms(kalshi)
    assert set(terms["series_ticker"]) >= {"KXA", "KXM", "KXB"}
    assert (
        terms.filter(pl.col("series_ticker") == "KXM")["fee_type"][0] == "quadratic_with_maker_fees"
    )
