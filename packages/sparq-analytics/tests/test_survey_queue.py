from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from sparq_analytics.live.source import GAP_SCHEMA, hour_files
from sparq_analytics.survey.dump import SurveyDataError
from sparq_analytics.survey.queue import (
    _reduce,
    dump_trades,
    episodes,
    expected_fill,
    fee_terms,
    hour_before,
    mark_gaps,
    span,
    summarize,
    with_fills,
)
from surveyhelpers import BASE_TIME, lm, write_history, write_live


def lm_time(hour: int) -> datetime:
    return BASE_TIME + timedelta(hours=hour)


END = datetime(2026, 10, 25, 4, tzinfo=UTC)
# the history world's Sports reversals (C's no side, D) and false regime (D)
REVERSAL_DUMPS = {"Sports": [1_500, 2_500], "Climate and Weather": []}
FALSE_DUMPS = {"Sports": [2_500]}  # the history never saw a Weather false regime here


def found_episodes(live: Path) -> pl.DataFrame:
    start, end = span(live)
    reduced = pl.concat([_reduce(p) for p in hour_files(live, "ticker", start, end)])
    return mark_gaps(episodes(reduced, start, end), live, end)


def test_the_span_starts_at_the_first_hour_with_frames(tmp_path: Path) -> None:
    live = write_live(tmp_path)
    assert span(live) == (BASE_TIME, END)
    with pytest.raises(SurveyDataError, match="run live extract"):
        span(tmp_path / "nowhere")
    # frames whose hour ends after the last hour copied for both tables: nothing usable
    short = tmp_path / "short"
    (short / "trade").mkdir(parents=True)
    (short / "ticker").mkdir()
    pl.read_parquet(live / "ticker" / "2026-10-25T00.parquet").write_parquet(
        short / "ticker" / "2026-10-25T00.parquet"
    )
    pl.read_parquet(live / "trade" / "2026-10-24T21.parquet").write_parquet(
        short / "trade" / "2026-10-24T21.parquet"
    )
    with pytest.raises(SurveyDataError, match="no ticker frames"):
        span(short)
    empty = tmp_path / "empty"
    for name in ("ticker", "trade"):
        (empty / name).mkdir(parents=True)
        pl.read_parquet(live / name / "2026-10-24T21.parquet").write_parquet(
            empty / name / "2026-10-25T00.parquet"
        )
    with pytest.raises(SurveyDataError, match="no ticker frames"):
        span(empty)


def test_episodes_run_from_the_onset_to_the_first_frame_out(tmp_path: Path) -> None:
    found = found_episodes(write_live(tmp_path))
    rows = {(r["ticker"], r["side"], r["start_ms"]): r for r in found.to_dicts()}
    assert sorted(rows) == [
        ("L1", "yes", lm(1, 30)),
        ("L1", "yes", lm(3, 0)),
        ("L2", "no", lm(1, 40)),
        ("L4", "yes", lm(1, 0)),
        ("L5", "yes", lm(0, 30)),
        ("L6", "yes", lm(0, 50)),
    ]
    first = rows[("L1", "yes", lm(1, 30))]
    assert (first["end_ms"], first["onset_size_e2"], first["frames"]) == (lm(2, 30), 1_000, 2)
    assert (first["size_mean_e2"], first["hours"], first["censored"]) == (2_000.0, 1.0, False)
    second = rows[("L1", "yes", lm(3, 0))]
    assert (second["end_ms"], second["onset_size_e2"], second["censored"]) == (None, 200, True)
    assert second["hours"] == 1.0  # to the span's end
    no = rows[("L2", "no", lm(1, 40))]
    assert (no["onset_size_e2"], no["frames"], no["censored"]) == (900, 2, True)
    assert not first["left_censored"] and not rows[("L4", "yes", lm(1, 0))]["left_censored"]
    assert rows[("L5", "yes", lm(0, 30))]["left_censored"]
    assert rows[("L6", "yes", lm(0, 50))]["left_censored"]
    # the ticker gap at 01:10 overlaps the episodes open then: L4 and L5, not L6 (over at 01:05)
    assert [k for k, r in rows.items() if r["in_gap"]] == [
        ("L4", "yes", lm(1, 0)),
        ("L5", "yes", lm(0, 30)),
    ]
    assert rows[("L6", "yes", lm(0, 50))]["end_ms"] == lm(1, 5)
    assert not mark_gaps(found.drop("in_gap"), tmp_path / "nowhere", END)["in_gap"].any()


def test_the_queue_an_hour_before_is_the_size_at_99_then_or_none_or_unknown(
    tmp_path: Path,
) -> None:
    live = write_live(tmp_path)
    found = hour_before(live, found_episodes(live))
    rows = {(r["ticker"], r["start_ms"]): r["hour_before_size_e2"] for r in found.to_dicts()}
    assert rows[("L1", lm(1, 30))] == 0  # the bid was 97 an hour before
    assert rows[("L1", lm(3, 0))] == 3_000  # at 02:00 the bid was already 99, 3,000 shown
    assert rows[("L2", lm(1, 40))] == 0  # the ask was 2c
    assert rows[("L4", lm(1, 0))] is None  # no frame in the two hours before
    assert rows[("L5", lm(0, 30))] is None  # no file at all in the two hours before


def test_the_dump_is_the_dead_side_taken_at_99_inside_an_episode(tmp_path: Path) -> None:
    live = write_live(tmp_path)
    found = dump_trades(live, found_episodes(live), *span(live))
    rows = {(r["ticker"], r["start_ms"]): (r["dump_e2"], r["lifted_e2"]) for r in found.to_dicts()}
    assert rows[("L1", lm(1, 30))] == (2_500, 300)
    assert rows[("L1", lm(3, 0))] == (1_200, 0)  # the 02:45 trade fell between the episodes
    assert rows[("L2", lm(1, 40))] == (15_000, 100)
    assert rows[("L4", lm(1, 0))] == (4_200, 0)


def test_fills_are_first_in_first_out_behind_the_queue_with_the_maker_fee(tmp_path: Path) -> None:
    live = write_live(tmp_path)
    kalshi = write_history(tmp_path)
    found = dump_trades(live, hour_before(live, found_episodes(live)), *span(live))
    found = with_fills(
        found,
        pl.read_parquet(live / "markets.parquet"),
        fee_terms(kalshi),
        REVERSAL_DUMPS,
        FALSE_DUMPS,
    )
    rows = {(r["ticker"], r["start_ms"]): r for r in found.to_dicts()}
    first = rows[("L1", lm(1, 30))]
    assert (first["fill_onset_10000"], first["fill_hour_before_10000"]) == (1_500, 2_500)
    second = rows[("L1", lm(3, 0))]
    assert (second["fill_onset_10000"], second["fill_hour_before_10000"]) == (1_000, 0)
    assert (
        rows[("L2", lm(1, 40))]["fill_onset_10000"],
        rows[("L2", lm(1, 40))]["fill_hour_before_10000"],
    ) == (10_000, 10_000)  # capped at the bid
    l4 = rows[("L4", lm(1, 0))]
    assert (l4["fill_onset_10000"], l4["fill_hour_before_10000"]) == (4_100, None)
    assert (l4["fee_onset_10000"], first["fee_onset_10000"]) == (7_104, 0)  # a quarter of 7% at 99c
    assert first["fill_onset_100000"] == 1_500  # a bigger bid fills no more than the dump allows
    assert rows[("L2", lm(1, 40))]["fill_onset_100000"] == 14_100
    assert (first["category"], l4["share"]) == ("Sports", 25)
    # a reversal's dump (15 or 25 contracts in the Sports history) behind each queue
    assert (first["rev_fill_onset_10000"], second["rev_fill_onset_10000"]) == (1_000, 1_800)
    assert l4["rev_fill_onset_10000"] == 1_900
    assert rows[("L2", lm(1, 40))]["rev_fill_onset_10000"] == 0  # Weather never reversed
    assert rows[("L2", lm(1, 40))]["rev_fill_hour_before_10000"] is None  # not measured
    # a false regime's dump (25 contracts) behind the hour-before queue
    assert (first["rev_fill_hour_before_10000"], second["rev_fill_hour_before_10000"]) == (2_500, 0)
    assert l4["rev_fill_hour_before_10000"] is None  # the queue then is unknown


def test_an_episode_overlapping_a_ticker_gap_is_marked(tmp_path: Path) -> None:
    found = pl.DataFrame(
        {
            "ticker": ["A", "B", "C", "D"],
            "side": ["yes"] * 4,
            "start_ms": [lm(10), lm(12), lm(12, 30), lm(9)],
            "end_ms": [lm(11), lm(13), None, lm(9, 30)],  # C is open at the span's end
        }
    )
    gaps = [
        {"kind": "ticker", "start_ms": lm(10, 30), "end_ms": lm(10, 40), "reason": "lost"},
        {"kind": "all", "start_ms": lm(12, 45), "end_ms": None, "reason": "open"},
        {"kind": "trade", "start_ms": lm(9, 10), "end_ms": lm(9, 20), "reason": "not a frame gap"},
    ]
    (tmp_path / "gaps.parquet").write_bytes(b"")
    pl.DataFrame(gaps, schema=GAP_SCHEMA).write_parquet(tmp_path / "gaps.parquet")
    marked = mark_gaps(found, tmp_path, lm_time(14))
    assert marked["in_gap"].to_list() == [True, True, True, False]
    pl.DataFrame(gaps[2:], schema=GAP_SCHEMA).write_parquet(tmp_path / "gaps.parquet")
    assert not mark_gaps(found, tmp_path, lm_time(14))["in_gap"].any()  # no frame gap at all


def test_the_expected_fill_of_a_reversal_is_first_in_first_out_behind_the_queue() -> None:
    queues = np.asarray([0, 1_000, 5_000], dtype=np.int64)
    dumps = np.asarray([1_500, 2_500], dtype=np.int64)
    assert expected_fill(queues, dumps, 10_000).tolist() == [2_000, 1_000, 0]
    assert expected_fill(queues, dumps, 1_000).tolist() == [1_000, 750, 0]  # capped
    assert expected_fill(queues, np.asarray([], dtype=np.int64), 1_000).tolist() == [0, 0, 0]


def test_the_summary_excludes_left_censored_episodes_and_scales_fills_to_a_day(
    tmp_path: Path,
) -> None:
    live = write_live(tmp_path)
    kalshi = write_history(tmp_path)
    start, end = span(live)
    raw = dump_trades(live, hour_before(live, found_episodes(live)), start, end)
    found = with_fills(
        raw,
        pl.read_parquet(live / "markets.parquet"),
        fee_terms(kalshi),
        REVERSAL_DUMPS,
        FALSE_DUMPS,
    )
    summary = summarize(found, start, end)
    assert (summary["hours"], summary["episodes"], summary["left_censored"]) == (4, 6, 2)
    assert summary["gap_episodes"] == 2  # L4, and L5 which is left-censored as well
    sports = summary["by_category"]["Sports"]
    # L1's two episodes; L4 sits in the gap, L5 and L6 are left-censored
    assert (sports["episodes"], sports["censored"], sports["markets"]) == (2, 1, 1)
    assert sports["episodes_a_day"] == 12
    assert sports["onset_size_median_e2"] == 600.0
    assert (sports["hour_before_size_median_e2"], sports["hour_before_unknown"]) == (1_500.0, 0)
    assert (sports["dump_e2"], sports["lifted_e2"]) == (3_700, 300)
    assert sports["fills_a_day_e2_onset_10000"] == 15_000  # 2,500 in 4 hours
    assert sports["fills_a_day_e2_hour_before_10000"] == 15_000
    assert sports["fee_a_day_e6_onset_10000"] == 0  # the maker-fee market is in the gap
    assert sports["fee_unknown_episodes"] == 0
    assert sports["rev_fills_a_day_e2_onset_10000"] == 16_800  # 2,800 in 4 hours
    assert sports["rev_fills_a_day_e2_hour_before_10000"] == 15_000
    weather = summary["by_category"]["Climate and Weather"]
    assert weather["rev_fills_a_day_e2_onset_10000"] == 0
    assert weather["rev_fills_a_day_e2_hour_before_10000"] is None
    assert (sports["dump_e2_a_regime_hour"], sports["closed_hours_median"]) == (2_500.0, 1.0)
    weather = summary["by_category"]["Climate and Weather"]
    assert (weather["episodes"], weather["censored"], weather["dump_e2"]) == (1, 1, 15_000)
    assert weather["fills_a_day_e2_onset_10000"] == 60_000
    assert weather["fills_a_day_e2_hour_before_10000"] == 60_000
    assert weather["dump_e2_a_regime_hour"] is None  # no closed episode
    with pytest.raises(SurveyDataError, match="no whole hour"):
        summarize(found, start, start)
    # a fee type outside the quadratic family leaves the fee unknown, not free
    odd = fee_terms(kalshi).with_columns(
        pl.when(pl.col("series_ticker") == "KXRAIN")
        .then(pl.lit("flat"))
        .otherwise("fee_type")
        .alias("fee_type")
    )
    unknown = with_fills(raw, pl.read_parquet(live / "markets.parquet"), odd)
    assert unknown.filter(pl.col("ticker") == "L2")["fee_onset_10000"].to_list() == [None]
    assert (
        summarize(unknown, start, end)["by_category"]["Climate and Weather"]["fee_unknown_episodes"]
        == 1
    )
