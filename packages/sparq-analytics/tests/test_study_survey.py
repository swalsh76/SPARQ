"""The snapshot, the time-reference measurement and the cell table."""

import datetime as dt
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sparq_analytics.history.schemas import MARKETS_SCHEMA
from sparq_analytics.study.cells import CELL, cell_table
from sparq_analytics.study.dataset import StudyDataError
from sparq_analytics.study.reference import measure_reference
from sparq_analytics.study.snapshot import (
    EDGE_MARGIN,
    MAX_BASELINE,
    MIN_DAYS,
    RECENT_DAYS,
    baseline_share,
    build_snapshot,
    days_table,
    window_for,
)
from sparq_analytics.study.synthetic import World, write_world

HOUR = dt.timedelta(hours=1)


def small_world(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    root = tmp_path / "kalshi"
    return root, write_world(World(events=120, trades_per_market=6, seed=11), root)


def edit_markets(root: Path, change: Callable[[dict[str, list[Any]]], None]) -> None:
    path = root / "markets" / "historical" / "part-000000.parquet"
    columns = pq.read_table(path).to_pydict()
    change(columns)
    pq.write_table(pa.table(columns, schema=MARKETS_SCHEMA), path)


# --- snapshot -------------------------------------------------------------------------------------


def test_a_complete_download_gives_a_window_over_all_its_days(tmp_path: Path) -> None:
    root, counts = small_world(tmp_path)
    days = days_table(root)
    assert days["trades"].sum() == counts["trades"]
    assert days["market_missing"].sum() == 0
    window = window_for(days)
    assert window is not None
    first, after_last = window
    assert first == "2026-01-02"
    assert after_last == "2026-01-08"


def test_the_window_ends_before_days_whose_markets_have_not_settled(tmp_path: Path) -> None:
    root, _ = small_world(tmp_path)
    cut = dt.datetime(2026, 1, 6, 12, tzinfo=dt.UTC)

    def forget_late_markets(columns: dict[str, list[Any]]) -> None:
        keep = [i for i, close in enumerate(columns["close_time"]) if close < cut]
        for name in columns:
            columns[name] = [columns[name][i] for i in keep]

    edit_markets(root, forget_late_markets)
    days = days_table(root)
    assert days.filter(pl.col("day") == dt.date(2026, 1, 7))["missing_share"][0] > 0.5
    window = window_for(days)
    assert window is not None
    assert window[1] <= "2026-01-07"  # the days that are mostly unlabelled are left out


def shares(*values: float) -> pl.DataFrame:
    """A days table with the given missing shares, one per day from 1 January, oldest first."""
    count = len(values)
    return pl.DataFrame(
        {
            "day": [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(count)],
            "trades": [1000] * count,
            "market_missing": [round(1000 * v) for v in values],
            "missing_share": list(values),
        }
    )


def test_too_few_days_to_tell_a_baseline_from_an_edge_give_no_window() -> None:
    days = shares(0.01, 0.01, 0.2)
    assert days.height < MIN_DAYS and baseline_share(days) is None and window_for(days) is None


def test_a_steady_share_of_unsettled_markets_is_not_an_edge() -> None:
    """About 1% of trades on every day belong to markets still open; the window keeps those days."""
    days = shares(*([0.011] * 40), 0.013, 0.012, 0.011)
    assert window_for(days) == ("2026-01-01", "2026-02-13")  # all 43 days
    baseline = baseline_share(days)
    assert baseline == pytest.approx(0.011)


def test_the_window_ends_before_the_first_day_the_share_climbs_past_the_margin() -> None:
    steady = [0.011] * 40
    days = shares(*steady, 0.014, 0.0169, 0.0171, 0.03, 0.09)  # limit is 0.011 + 0.005 = 0.016
    window = window_for(days)
    assert window is not None
    assert window[1] == "2026-02-11"  # the 41st day (index 40) is the last one at or under 0.016


def test_a_day_exactly_at_the_limit_is_kept_and_one_just_over_it_is_not() -> None:
    base = [0.01] * 20
    assert window_for(shares(*base, 0.015))[1] == "2026-01-22"  # type: ignore[index]
    assert window_for(shares(*base, 0.0151))[1] == "2026-01-21"  # type: ignore[index]


def test_the_newest_days_are_left_out_of_the_baseline() -> None:
    days = shares(*([0.01] * 30), *([0.5] * RECENT_DAYS))
    assert baseline_share(days) == pytest.approx(0.01)  # the edge cannot raise its own limit
    assert EDGE_MARGIN == 0.005 and RECENT_DAYS == 14 and MIN_DAYS == 4


def test_a_usual_share_too_high_to_be_open_markets_means_broken_data_and_gives_no_window() -> None:
    assert window_for(shares(*([MAX_BASELINE] * 20))) is not None  # at the ceiling: allowed
    assert window_for(shares(*([MAX_BASELINE + 0.01] * 20))) is None
    assert window_for(shares(*([1.0] * 20))) is None  # every market record missing


def test_a_short_download_still_uses_older_days_for_the_baseline() -> None:
    days = shares(0.0, 0.0, 0.0, 0.0, 0.0, 0.9)  # six days: the older three set the baseline
    assert baseline_share(days) == 0.0 and window_for(days) == ("2026-01-01", "2026-01-06")


def test_the_snapshot_suggests_the_window_and_a_share_under_the_ceiling(tmp_path: Path) -> None:
    root, counts = small_world(tmp_path)
    snapshot = build_snapshot(root, ceiling=counts["trades"] // 2)
    suggested = snapshot["suggested"]
    assert suggested["window_start"] == "2026-01-02"
    assert suggested["used_trades"] == counts["trades"]
    assert suggested["expected_trades"] <= counts["trades"] // 2 * 1.02  # the ceiling holds
    assert suggested["expected_trades"] > counts["trades"] // 2 * 0.9  # and is not wasted
    assert snapshot["exclusions"] == {"used": counts["trades"]}
    assert len(snapshot["trade_days"]) == 6
    assert build_snapshot(root)["suggested"]["sample_share"] == 1.0


def test_the_snapshot_without_a_usable_day_suggests_nothing(tmp_path: Path) -> None:
    root, _ = small_world(tmp_path)

    def forget_all(columns: dict[str, list[Any]]) -> None:
        for name in columns:
            columns[name] = columns[name][:1]

    edit_markets(root, forget_all)
    assert build_snapshot(root)["suggested"] == {"window_start": None, "window_end": None}


# --- the time reference ---------------------------------------------------------------------------


def test_each_field_wins_where_it_sits_nearer_the_last_trade(tmp_path: Path) -> None:
    root, _ = small_world(tmp_path)

    def separate(columns: dict[str, list[Any]]) -> None:
        for i, series in enumerate(columns["series_ticker"]):
            close = columns["close_time"][i]
            if series == "KXSYN0":  # sports: the close time is a week late
                columns["close_time"][i] = close + 7 * 24 * HOUR
            else:  # politics: the expected time is a week late
                columns["expected_expiration_time"][i] = close + 7 * 24 * HOUR

    edit_markets(root, separate)
    table = measure_reference(root)
    nearer = dict(zip(table["category"], table["nearer"], strict=True))
    # the late close_time of the sports markets also pushes them outside the measured days
    assert nearer.get("politics") == "close_time"
    assert table.filter(pl.col("category") == "politics")["expected_median"][0] > 6 * 86400


def test_the_reference_measure_counts_missing_expected_times(tmp_path: Path) -> None:
    root, _ = small_world(tmp_path)

    def blank(columns: dict[str, list[Any]]) -> None:
        columns["expected_expiration_time"] = [None] * len(columns["ticker"])

    edit_markets(root, blank)
    table = measure_reference(root)
    assert all(share == 1.0 for share in table["expected_null"])
    assert all(m > 0 for m in table["markets"])


# --- the cell table -------------------------------------------------------------------------------


def edge_frame() -> pl.LazyFrame:
    """Two events in one cell. Taker edge sums 10 and 30 over 1 and 2 contracts."""
    rows = {
        "price_band": [3, 3, 3],
        "tleft_band": [1, 1, 1],
        "category": ["x", "x", "x"],
        "taker_yes": [True, True, True],
        "event_ticker": ["A", "A", "B"],
    }
    measures = {
        "e_taker": [4, 6, 30],
        "c": [100, 100, 200],
        "e_mk_opt_cons": [0, 0, 0],
        "e_mk_pess_cons": [0, 0, 0],
        "c_pess": [0, 0, 0],
    }
    return pl.DataFrame({**rows, **measures}).lazy()


def test_a_cell_gives_the_ratio_and_the_clustered_standard_error() -> None:
    table = cell_table(edge_frame(), minimum_events=1)
    assert table.height == 1
    row = table.row(0, named=True)
    # sums per event: A (10, 200), B (30, 200); ratio 40 / 400 = 0.1
    assert row["events"] == 2
    assert row["trades"] == 3
    assert row["taker_e6"] == pytest.approx(0.1)
    residuals = (10 - 0.1 * 200) ** 2 + (30 - 0.1 * 200) ** 2  # 100 + 100
    assert row["taker_se_e6"] == pytest.approx(math.sqrt(residuals) / 400)
    assert row["taker_z"] == pytest.approx(0.1 / (math.sqrt(residuals) / 400))


def test_cells_with_too_few_events_are_left_out() -> None:
    assert cell_table(edge_frame(), minimum_events=3).is_empty()


def test_the_cell_columns_are_the_grid() -> None:
    assert CELL == ["price_band", "tleft_band", "category", "taker_yes"]


def test_markets_found_only_in_the_live_folder_are_measured_too(tmp_path: Path) -> None:
    root, _ = small_world(tmp_path)
    source = root / "markets" / "historical" / "part-000000.parquet"
    target = root / "markets" / "live" / "date=2026-01-05" / "markets.parquet"
    target.parent.mkdir(parents=True)
    source.rename(target)
    assert not measure_reference(root).is_empty()


def test_the_reference_measure_needs_some_market_files(tmp_path: Path) -> None:
    root, _ = small_world(tmp_path)
    (root / "markets" / "historical" / "part-000000.parquet").unlink()
    with pytest.raises(StudyDataError, match="markets"):
        measure_reference(root)


def test_report_cents_shows_a_missing_value_as_not_available() -> None:
    from sparq_analytics.study.report import cents

    assert cents(None) == "n/a"
    assert cents(float("nan")) == "n/a"
    assert cents(25_000) == "+2.50"
    assert cents(-10_000) == "-1.00"


def test_a_cell_with_exactly_the_minimum_events_is_kept() -> None:
    assert cell_table(edge_frame(), minimum_events=2).height == 1


def test_markets_that_close_outside_the_downloaded_days_are_not_measured(tmp_path: Path) -> None:
    root, _ = small_world(tmp_path)

    def push_sports_out(columns: dict[str, list[Any]]) -> None:
        for i, series in enumerate(columns["series_ticker"]):
            if series == "KXSYN0":
                columns["close_time"][i] = columns["close_time"][i] + 30 * 24 * HOUR

    edit_markets(root, push_sports_out)
    assert "sports" not in set(measure_reference(root)["category"])


def test_the_report_shows_the_lower_bound_beside_the_point_estimate() -> None:
    from sparq_analytics.study.report import with_bound

    shown = with_bound({"point_e6": 30_000, "lower_e6": 10_000, "upper_e6": 90_000})
    assert shown == "+3.00 (+1.00)"


# --- the proposed split dates ---------------------------------------------------------------------


def test_the_split_dates_are_the_event_count_quantiles_of_the_markets(tmp_path: Path) -> None:
    from sparq_analytics.study.snapshot import propose_split

    root, _ = small_world(tmp_path)  # 120 events closing hourly from 2026-01-03 00:00
    # 70% of 120 is event 84 (hour 84 of 2026-01-03: 12:00 on the 6th), 85% is event 102 (the 7th)
    assert propose_split(root, "2026-01-02", "2026-01-09", (0.70, 0.15, 0.15)) == (
        "2026-01-06",
        "2026-01-07",
    )


def test_no_split_dates_are_proposed_when_the_events_are_too_few_or_share_a_day(
    tmp_path: Path,
) -> None:
    from sparq_analytics.study.snapshot import propose_split

    root, _ = small_world(tmp_path)
    fractions = (0.70, 0.15, 0.15)
    assert propose_split(root, "2026-01-02", "2026-01-03", fractions) is None  # no event fits
    assert (
        propose_split(root, "2026-01-03", "2026-01-05", fractions) is None
    )  # both fall on one day
    assert propose_split(tmp_path / "nowhere", "2026-01-02", "2026-01-09", fractions) is None


def test_the_snapshot_carries_the_proposed_split_dates(tmp_path: Path) -> None:
    root, _ = small_world(tmp_path)
    suggested = build_snapshot(root)["suggested"]
    assert (suggested["fit_end"], suggested["validate_end"]) == ("2026-01-06", "2026-01-07")


def test_a_window_given_to_the_snapshot_is_used_as_it_stands(tmp_path: Path) -> None:
    root, _ = small_world(tmp_path)
    suggested = build_snapshot(root, window_start="2026-01-02", window_end="2026-01-09")[
        "suggested"
    ]
    assert (suggested["window_start"], suggested["window_end"]) == ("2026-01-02", "2026-01-09")
    assert suggested["fit_end"] == "2026-01-06"
    narrow = build_snapshot(root, window_start="2026-01-03", window_end="2026-01-05")["suggested"]
    assert narrow["fit_end"] is None and narrow["validate_end"] is None


def test_the_split_dates_also_come_from_markets_found_only_in_the_live_folder(
    tmp_path: Path,
) -> None:
    from sparq_analytics.study.snapshot import propose_split

    root, _ = small_world(tmp_path)
    source = root / "markets" / "historical" / "part-000000.parquet"
    target = root / "markets" / "live" / "date=2026-01-05" / "markets.parquet"
    target.parent.mkdir(parents=True)
    source.rename(target)
    assert propose_split(root, "2026-01-02", "2026-01-09", (0.70, 0.15, 0.15)) == (
        "2026-01-06",
        "2026-01-07",
    )
