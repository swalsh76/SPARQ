from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest
from sparq_analytics.live.source import hour_files
from sparq_analytics.survey.dump import SurveyDataError
from sparq_analytics.survey.queue import (
    _reduce,
    dump_trades,
    episodes,
    fee_terms,
    hour_before,
    span,
    summarize,
    with_fills,
)
from surveyhelpers import BASE_TIME, lm, write_history, write_live

END = datetime(2026, 10, 25, 4, tzinfo=UTC)


def found_episodes(live: Path) -> pl.DataFrame:
    start, end = span(live)
    reduced = pl.concat([_reduce(p) for p in hour_files(live, "ticker", start, end)])
    return episodes(reduced, start, end)


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
    found = with_fills(found, pl.read_parquet(live / "markets.parquet"), fee_terms(kalshi))
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


def test_the_summary_excludes_left_censored_episodes_and_scales_fills_to_a_day(
    tmp_path: Path,
) -> None:
    live = write_live(tmp_path)
    kalshi = write_history(tmp_path)
    start, end = span(live)
    found = dump_trades(live, hour_before(live, found_episodes(live)), start, end)
    found = with_fills(found, pl.read_parquet(live / "markets.parquet"), fee_terms(kalshi))
    summary = summarize(found, start, end)
    assert (summary["hours"], summary["episodes"], summary["left_censored"]) == (4, 5, 1)
    sports = summary["by_category"]["Sports"]
    assert (sports["episodes"], sports["censored"], sports["markets"]) == (3, 2, 2)
    assert sports["episodes_a_day"] == 18
    assert sports["onset_size_median_e2"] == 200.0
    assert (sports["hour_before_size_median_e2"], sports["hour_before_unknown"]) == (1_500.0, 1)
    assert (sports["dump_e2"], sports["lifted_e2"]) == (7_900, 300)
    assert sports["fills_a_day_e2_onset_10000"] == 39_600  # 6,600 in 4 hours
    assert sports["fills_a_day_e2_hour_before_10000"] == 15_000
    assert sports["fee_a_day_e6_onset_10000"] == 42_624
    assert (sports["dump_e2_a_regime_hour"], sports["closed_hours_median"]) == (2_500.0, 1.0)
    weather = summary["by_category"]["Climate and Weather"]
    assert (weather["episodes"], weather["censored"], weather["dump_e2"]) == (1, 1, 15_000)
    assert weather["fills_a_day_e2_onset_10000"] == 60_000
    assert weather["fills_a_day_e2_hour_before_10000"] == 60_000
    assert weather["dump_e2_a_regime_hour"] is None  # no closed episode
    with pytest.raises(SurveyDataError, match="no whole hour"):
        summarize(found, start, start)
