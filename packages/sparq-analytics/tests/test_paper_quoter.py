import json
import runpy
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from paperhelpers import FEES, write_paper_live
from sparq_analytics.paper import quoter
from sparq_analytics.paper.cli import main
from sparq_analytics.survey.dump import SurveyDataError
from sparq_analytics.survey.lag import frames_of
from sparq_analytics.survey.queue import gaps_of
from surveyhelpers import BASE_TIME, HOUR, lm

END = BASE_TIME + 2 * HOUR
Rows = dict[tuple[str, int, str], dict[str, Any]]


def run_world(tmp_path: Path, **kwargs: Any) -> tuple[dict[str, Any], Rows, pl.DataFrame]:
    live = write_paper_live(tmp_path, **kwargs)
    out = tmp_path / "out"
    summary = quoter.run(live, FEES, out)
    fills = pl.read_parquet(out / "paper-fills.parquet")
    assert fills.select("ticker", "frame_ms", "k_e6", "side").is_unique().all()  # one fill a quote
    rows = {(r["ticker"], r["k_e6"], r["side"]): r for r in fills.to_dicts()}
    return summary, rows, fills


def test_outage_days_are_those_touched_by_a_long_outage() -> None:
    gaps = pl.DataFrame(
        {
            "start_ms": [lm(1, 30), lm(23, 50), lm(50, 0)],
            "end_ms": [lm(1, 31), lm(24, 5), lm(50, 11)],  # a minute; across midnight; 11 min
        }
    )
    assert quoter.outage_days(gaps) == [date(2026, 10, 25), date(2026, 10, 26), date(2026, 10, 27)]
    assert quoter.outage_days(gaps.head(1)) == []


def test_market_info_scopes_the_thin_markets(tmp_path: Path) -> None:
    live = write_paper_live(tmp_path)
    info = quoter.market_info(live, FEES, BASE_TIME, END)
    by = {r["ticker"]: r for r in info.to_dicts()}
    assert by["P1"]["tier"] == "100 to 1,000" and by["P3"]["tier"] == "10,000 and more"
    assert by["P2"]["tier"] == "under 100" and by["P5"]["close_ms"] == lm(0, 45)
    assert (by["P1"]["share"], by["P2"]["share"], by["P1"]["event_ticker"]) == (25, 0, "KXP1-E")


def test_paper_quotes_stand_inside_wide_or_one_sided_books(tmp_path: Path) -> None:
    live = write_paper_live(tmp_path)
    info = quoter.market_info(live, FEES, BASE_TIME, END)
    book = frames_of(live / "ticker" / "2026-10-25T00.parquet")
    quotes = quoter.paper_quotes(book, info, lm(0, 0), lm(1, 0))
    rows = {(r["ticker"], r["k_e6"], r["frame_ms"]): r for r in quotes.to_dicts()}
    assert {(m, k) for m, k, _ in rows} == {
        (m, k) for m in ("P1", "P2", "P5", "P6") for k in (50_000, 100_000)
    }
    p1 = rows[("P1", 50_000, lm(0, 0))]  # the contested 00:30 frame places no quote and ends this
    assert (p1["state"], p1["pbid_e6"], p1["pask_e6"]) == ("uncontested", 350_000, 550_000)
    assert (p1["valid_from"], p1["valid_to"]) == (lm(0, 0), lm(0, 30))
    p2 = rows[("P2", 100_000, lm(0, 0))]  # one-sided: the empty side quoted 20 cents from the bid
    assert (p2["state"], p2["pbid_e6"], p2["pask_e6"]) == ("one_sided", 500_000, 600_000)
    assert rows[("P5", 50_000, lm(0, 0))]["valid_to"] == lm(0, 41)
    assert rows[("P5", 50_000, lm(0, 41))]["valid_to"] == lm(0, 45)  # the close ends the quote
    # the next hour: the carried P2 and P6 frames stand on, P5's is past its close
    carried = (
        book.sort("ticker", "ts_ms")
        .group_by("ticker", maintain_order=True)
        .last()
        .select(book.columns)
        .with_columns(carried=pl.lit(True))
    )
    hour1 = frames_of(live / "ticker" / "2026-10-25T01.parquet")
    later = quoter.paper_quotes(pl.concat([carried, hour1]), info, lm(1, 0), lm(2, 0))
    assert sorted(set(later["ticker"])) == ["P1", "P2", "P6"]
    assert later.filter(pl.col("ticker") == "P2")["valid_from"].to_list() == [lm(1, 0)] * 2


def test_fills_take_the_first_trade_beyond_a_standing_paper_quote(tmp_path: Path) -> None:
    _, rows, _ = run_world(tmp_path)
    assert len(rows) == 8
    ask = rows[("P1", 50_000, "ask")]
    assert (ask["fill_ms"], ask["paper_price_e6"], ask["trade_price_e6"]) == (
        lm(0, 10), 550_000, 580_000,
    )  # fmt: skip
    assert (ask["fill_e2_1000"], ask["fill_e2_5000"]) == (200, 200)
    bid = rows[("P1", 100_000, "bid")]
    assert (bid["paper_price_e6"], bid["fill_e2_1000"], bid["fill_e2_5000"]) == (
        400_000, 1_000, 5_000,
    )  # fmt: skip
    # the 00:50 trade finds P2's quotes filled, and so does the 01:10 one on the carried frame
    assert rows[("P2", 50_000, "ask")]["fill_ms"] == lm(0, 15)
    assert rows[("P5", 50_000, "bid")]["paper_price_e6"] == 250_000  # the 00:50 trade: past close
    assert ("P3", 50_000, "ask") not in rows and ("P4", 50_000, "ask") not in rows


def test_marks_and_the_fill_s_fate(tmp_path: Path) -> None:
    _, rows, _ = run_world(tmp_path)

    def marks(ticker: str, k: int, side: str) -> tuple[Any, ...]:
        r = rows[(ticker, k, side)]
        return r["mid_10m"], r["mid_1h"], r["mid_close"], r["censored"], r["traded_again"]

    assert marks("P1", 50_000, "ask") == (450_000, 500_000, 500_000, True, True)
    assert marks("P1", 50_000, "bid") == (500_000, 500_000, 500_000, True, True)
    assert marks("P2", 50_000, "ask") == (None, None, None, True, True)  # never two-sided
    # closed at 00:45; the close mark is the 00:41 frame, after the fill, not the 00:00 one
    assert marks("P5", 100_000, "bid") == (350_000, None, 400_000, False, True)


def test_the_summary_by_rule(tmp_path: Path) -> None:
    summary, _, _ = run_world(tmp_path)
    assert (summary["hours_kept"], summary["dropped_days"], summary["fills"]) == (2, [], 8)
    assert summary["markets_filled"] == 3
    rule = summary["by_rule"]["uncontested | 50000 | 10"]
    assert (rule["fills"], rule["markets"], rule["contracts"], rule["quotes"]) == (3, 2, 13.0, 6)
    assert rule["quote_hours_a_day"] == pytest.approx(253 / 60 * 12)
    assert rule["fills_a_quote_hour"] == pytest.approx(3 / (253 / 60))
    assert (rule["censored"], rule["never_traded_again"], rule["fee_unknown_fills"]) == (
        pytest.approx(2 / 3), 0.0, 0,
    )  # fmt: skip
    assert rule["hours_to_close_median"] == pytest.approx(71 + 2 / 3)  # P1's bid, three days out
    assert rule["fee_e6_a_day"] == 48_476 * 12
    ten = rule["marks"]["10m"]
    assert (ten["marked_fills"], ten["gross_e6_a_day"], ten["net_e6_a_day"]) == (
        3, 1_800_000 * 12, (1_800_000 - 48_476) * 12,
    )  # fmt: skip
    assert ten["netted_contracts"] == 13.0
    assert ten["cents_a_contract"] == pytest.approx((1_800_000 - 48_476) / 13 / 10_000)
    hour = rule["marks"]["1h"]
    assert (hour["marked_fills"], hour["gross_e6_a_day"]) == (2, 1_600_000 * 12)
    assert hour["interval_e6_a_day"]["clusters"] == 1  # P5's hour mark is null: P1's event alone
    close = rule["marks"]["close"]
    assert (close["marked_fills"], close["gross_e6_a_day"]) == (3, 1_750_000 * 12)
    assert close["interval_e6_a_day"]["clusters"] == 2
    assert close["interval_e6_a_day"]["lower"] is not None
    big = summary["by_rule"]["uncontested | 50000 | 50"]
    assert (big["contracts"], big["marks"]["10m"]["gross_e6_a_day"]) == (53.0, 7_800_000 * 12)
    assert big["marks"]["close"]["gross_e6_a_day"] == 7_750_000 * 12
    assert big["fee_e6_a_day"] == (8_663 + 199_063) * 12
    one = summary["by_rule"]["one_sided | 100000 | 10"]
    assert (one["fills"], one["contracts"], one["quotes"]) == (1, 1.0, 2)
    assert one["quote_hours_a_day"] == pytest.approx(119 / 60 * 12)
    assert one["marks"]["close"]["marked_fills"] == 0
    assert one["marks"]["close"]["cents_a_contract"] is None  # nothing marked, nothing netted
    assert set(summary["by_rule"]) == {
        f"{s} | {k} | {size}"
        for s in ("uncontested", "one_sided")
        for k in (50000, 100000)
        for size in (10, 50)
    }
    sports = summary["by_rule_and_category"]["uncontested | 50000 | Sports | 10"]
    assert sports["fills"] == 3 and "interval_e6_a_day" in sports["marks"]["close"]
    politics = summary["by_rule_and_category"]["uncontested | 50000 | Politics | 10"]  # quoted only
    assert (politics["fills"], politics["quotes"], politics["fills_a_quote_hour"]) == (0, 2, 0.0)
    assert politics["quote_hours_a_day"] == pytest.approx(119 / 60 * 12)
    assert politics["marks"]["close"]["marked_fills"] == 0
    assert "interval_e6_a_day" not in politics["marks"]["close"]
    tier = summary["by_rule_and_tier"]["uncontested | 100000 | under 100 | 10"]  # P5 alone
    assert (tier["fills"], tier["marks"]["10m"]["gross_e6_a_day"]) == (1, 50_000 * 12)
    assert "interval_e6_a_day" not in tier["marks"]["close"]


def test_a_dropped_day_resets_the_book_and_bounds_the_marks(tmp_path: Path) -> None:
    summary, _, fills = run_world(tmp_path, days=3)
    assert summary["dropped_days"] == ["2026-10-26"] and summary["hours_kept"] == 25
    assert summary["fills"] == 12
    late = fills.filter(pl.col("fill_ms") >= lm(48, 0))
    # P1's two paper asks of the third day; none for P2, whose book was not carried across
    assert sorted(zip(late["ticker"], late["k_e6"], late["side"], strict=True)) == [
        ("P1", 50_000, "ask"),
        ("P1", 100_000, "ask"),
    ]
    eve = fills.filter(pl.col("fill_ms") == lm(23, 40)).to_dicts()
    assert len(eve) == 2 and all(r["mid_10m"] == 450_000 for r in eve)
    assert all(r["mid_1h"] is None for r in eve)  # the next hour's frames belong to the dropped day
    with pytest.raises(SurveyDataError, match="every hour"):
        quoter.run(
            write_paper_live(
                tmp_path / "gone",
                gaps=[
                    {"kind": "ticker", "start_ms": lm(0, 30), "end_ms": lm(1, 0), "reason": "lost"}
                ],
            ),
            FEES,
            tmp_path / "gone-out",
        )


def test_the_summary_nets_only_the_fills_whose_fee_is_known() -> None:
    # two marked bid fills of 10 contracts at 40 against a mid of 50 (+$1.00 each); the second's
    # series has no fee terms, so it is marked but not netted; a market without a listed close is
    # censored
    fills = pl.DataFrame(
        {
            "ticker": ["A", "B"],
            "event_ticker": ["A-E", "B-E"],
            "category": ["Sports", "Sports"],
            "tier": ["under 100", "under 100"],
            "state": ["uncontested", "uncontested"],
            "k_e6": [50_000, 50_000],
            "side": ["bid", "bid"],
            "paper_price_e6": [400_000, 400_000],
            "fill_ms": [lm(0, 10), lm(0, 20)],
            "fill_e2_1000": [1_000, 1_000],
            "fill_e2_5000": [1_000, 1_000],
            "mid_10m": [500_000, 500_000],
            "mid_1h": [None, None],
            "mid_close": [500_000, 500_000],
            "close_ms": [lm(5, 0), None],
            "multiplier_e6": [1_000_000, None],
            "share": [0, None],
            "censored": [False, True],
            "traded_again": [True, False],
        },
        schema_overrides={
            "mid_1h": pl.Int64, "close_ms": pl.Int64, "multiplier_e6": pl.Int64, "share": pl.Int64,
        },
    )  # fmt: skip
    times = pl.DataFrame(
        {
            "state": ["uncontested"],
            "k_e6": [50_000],
            "tier": ["under 100"],
            "category": ["Sports"],
            "quotes": [2],
            "quote_ms": [7_200_000],
        }
    )
    summary = quoter.summarize(fills, times, BASE_TIME, END, 2, [])
    rule = summary["by_rule"]["uncontested | 50000 | 10"]
    ten = rule["marks"]["10m"]
    assert (ten["marked_fills"], ten["gross_e6_a_day"]) == (2, 2_000_000 * 12)
    assert (ten["netted_contracts"], ten["net_e6_a_day"]) == (10.0, 1_000_000 * 12)
    assert ten["cents_a_contract"] == pytest.approx(10.0)  # not 5: the unknown fee's fill is out
    assert (rule["fee_unknown_fills"], rule["censored"]) == (1, 0.5)
    assert rule["marks"]["1h"]["cents_a_contract"] is None


def test_fit_command_writes_the_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    live = write_paper_live(tmp_path)
    kalshi = tmp_path / "kalshi"
    kalshi.mkdir()
    FEES.rename({"series_ticker": "ticker", "multiplier_e6": "fee_multiplier_e6"}).write_parquet(
        kalshi / "series.parquet"
    )
    out = tmp_path / "out"
    argv = ["fit", "--kalshi", str(kalshi), "--live", str(live), "--out", str(out)]
    assert main(argv) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["fills"] == 8 and "uncontested | 50000 | 10" in printed["by_rule"]
    assert json.loads((out / "paper.json").read_text(encoding="utf-8"))["hours_kept"] == 2
    assert pl.read_parquet(out / "paper-fills.parquet").height == 8
    # the first hour alone: the 1-hour marks lie past the copied frames
    assert main([*argv, "--hours", "1"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["hours_kept"] == 1
    assert first["by_rule"]["uncontested | 50000 | 10"]["marks"]["1h"]["marked_fills"] == 0
    assert main(["fit", "--live", str(live), "--out", str(out)]) == 1
    assert "--kalshi is required" in capsys.readouterr().err
    assert gaps_of(live, datetime(2026, 10, 25, 2, tzinfo=UTC)).height == 1


def test_running_the_package_runs_the_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["paper", "--help"])
    with pytest.raises(SystemExit) as stop:
        runpy.run_module("sparq_analytics.paper", run_name="__main__")
    assert stop.value.code == 0


def test_an_outage_outside_the_span_drops_no_day(tmp_path: Path) -> None:
    live = write_paper_live(
        tmp_path,
        gaps=[
            {"kind": "ticker", "start_ms": lm(1, 30), "end_ms": lm(1, 31), "reason": "lost"},
            {"kind": "ticker", "start_ms": lm(30, 0), "end_ms": lm(34, 0), "reason": "a later day"},
        ],
    )
    summary = quoter.run(live, FEES, tmp_path / "out")
    assert (summary["dropped_days"], summary["hours_kept"], summary["fills"]) == ([], 2, 8)
