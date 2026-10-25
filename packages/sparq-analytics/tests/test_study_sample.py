import datetime as dt
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest
from sparq_analytics.study.config import StudyConfig
from sparq_analytics.study.dataset import exclusion_counts, tag_trades, used
from sparq_analytics.study.sample import (
    DEFAULT_CEILING,
    chosen_events,
    event_fraction,
    share_for,
)
from sparq_analytics.study.synthetic import World, write_world


def test_the_event_hash_is_frozen() -> None:
    """A change here would silently change which events a pre-registered run samples."""
    assert event_fraction("KXSYN0-E00001", 1) == 0.17235458864771455
    assert event_fraction("KXSYN0-E00001", 2) == 0.03466366579991557
    assert event_fraction("KXNBAGAME-26OCT21LALGSW", 1) == 0.6872452729877898


def test_a_share_picks_about_that_share_and_the_seed_changes_which() -> None:
    names = [f"EV-{i}" for i in range(20_000)]
    first = chosen_events(names, 0.3, seed=1)
    assert 0.28 * len(names) < len(first) < 0.32 * len(names)
    assert first == chosen_events(names, 0.3, seed=1)
    assert set(first) != set(chosen_events(names, 0.3, seed=2))


def test_a_smaller_share_is_inside_a_larger_one() -> None:
    names = [f"EV-{i}" for i in range(5_000)]
    assert set(chosen_events(names, 0.1, 1)) <= set(chosen_events(names, 0.4, 1))


def test_a_full_share_keeps_every_event() -> None:
    assert chosen_events(["a", "b", "c"], 1.0, seed=9) == ["a", "b", "c"]


def test_the_share_keeps_the_trades_under_the_ceiling() -> None:
    assert share_for(10, 100) == 1.0
    assert share_for(100, 100) == 1.0
    assert share_for(200, 100) == 0.5
    assert share_for(150, 100) == 0.6666  # rounded down, so the ceiling holds
    assert share_for(10**12, 100) == 0.0001  # never zero
    assert share_for(700_000_000) == 0.0571
    assert DEFAULT_CEILING == 40_000_000


@pytest.mark.parametrize(
    "bad",
    [
        {"sample_share": 0.0},
        {"sample_share": 1.5},
        {"window_start": "2026-02-01", "window_end": "2026-01-01"},
        {"window_start": "2026-02-01", "window_end": "2026-02-01"},
        {"fit_end": "2026-01-05"},
        {"validate_end": "2026-01-05"},
        {"fit_end": "2026-01-09", "validate_end": "2026-01-05"},
        {"window_start": "2026-01-06", "fit_end": "2026-01-05", "validate_end": "2026-01-09"},
        {"window_end": "2026-01-08", "fit_end": "2026-01-05", "validate_end": "2026-01-09"},
    ],
)
def test_a_bad_sample_or_window_is_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        StudyConfig(**bad)  # type: ignore[arg-type]


def test_the_sample_and_the_window_are_part_of_the_pre_registered_digest() -> None:
    base = StudyConfig().digest()
    assert StudyConfig(sample_share=0.5).digest() != base
    assert StudyConfig(sample_seed=2).digest() != base
    assert StudyConfig(window_start="2026-01-02").digest() != base
    assert StudyConfig(window_end="2026-01-09").digest() != base
    assert StudyConfig(fit_end="2026-01-05", validate_end="2026-01-09").digest() != base


def test_the_window_is_read_as_utc_midnights() -> None:
    start, end = StudyConfig(window_start="2026-01-02", window_end="2026-01-09").window()
    assert start == dt.datetime(2026, 1, 2, tzinfo=dt.UTC)
    assert end == dt.datetime(2026, 1, 9, tzinfo=dt.UTC)
    assert StudyConfig().window() == (None, None)


def world(tmp_path: Path) -> tuple[Path, int]:
    root = tmp_path / "kalshi"
    counts = write_world(World(events=240, trades_per_market=6, seed=4), root)
    return root, counts["trades"]


def test_events_outside_the_window_are_excluded_and_counted(tmp_path: Path) -> None:
    root, total = world(tmp_path)
    config = StudyConfig(window_start="2026-01-04", window_end="2026-01-08")
    tagged = tag_trades(root, config)
    counts = exclusion_counts(tagged)
    assert sum(counts.values()) == total
    assert counts["outside_window"] > 0
    kept = used(tagged).collect()
    assert kept["ev_open"].min() >= dt.datetime(2026, 1, 4, tzinfo=dt.UTC)  # type: ignore[operator]
    assert kept["ev_close"].max() < dt.datetime(2026, 1, 8, tzinfo=dt.UTC)  # type: ignore[operator]


def test_a_sample_keeps_or_drops_whole_events_and_counts_the_rest(tmp_path: Path) -> None:
    root, total = world(tmp_path)
    full = used(tag_trades(root, StudyConfig())).collect()
    config = StudyConfig(sample_share=0.3, sample_seed=5)
    tagged = tag_trades(root, config)
    counts = exclusion_counts(tagged)
    assert sum(counts.values()) == total
    assert counts["not_sampled"] > 0
    kept = used(tagged).collect()
    # a kept event has every trade it had before, so the events are intact
    full_sizes = dict(full.group_by("event_ticker").len().iter_rows())
    for event, size in kept.group_by("event_ticker").len().iter_rows():
        assert size == full_sizes[event]
    share = kept["event_ticker"].n_unique() / full["event_ticker"].n_unique()
    assert 0.15 < share < 0.45


def test_dropping_the_unsampled_early_leaves_exactly_the_same_used_trades(tmp_path: Path) -> None:
    root, total = world(tmp_path)
    config = StudyConfig(sample_share=0.3, sample_seed=5, window_start="2026-01-03")
    late = used(tag_trades(root, config)).collect()
    early_frame = tag_trades(root, config, drop_unsampled=True)
    early = used(early_frame).collect()
    order = ["ticker", "t", "p", "c"]
    assert early.sort(order).equals(late.sort(order)) and early.height > 0
    counts = exclusion_counts(early_frame)
    assert "not_sampled" not in counts  # they are absent, not labelled
    assert sum(counts.values()) < total


def test_a_full_share_drops_nothing_early(tmp_path: Path) -> None:
    root, total = world(tmp_path)
    counts = exclusion_counts(tag_trades(root, StudyConfig(), drop_unsampled=True))
    assert sum(counts.values()) == total


def test_the_same_seed_samples_the_same_events_and_another_seed_does_not(tmp_path: Path) -> None:
    root, _ = world(tmp_path)

    def events(seed: int) -> set[str]:
        frame = used(tag_trades(root, StudyConfig(sample_share=0.3, sample_seed=seed)))
        return set(frame.select("event_ticker").unique().collect()["event_ticker"])

    assert events(5) == events(5)
    assert events(5) != events(6)


def test_a_window_applies_after_the_other_reasons(tmp_path: Path) -> None:
    root, total = world(tmp_path)
    path = root / "markets" / "historical" / "part-000000.parquet"
    columns = pq.read_table(path).to_pydict()
    columns["label"][0] = "scalar"
    from sparq_analytics.history.schemas import MARKETS_SCHEMA

    pq.write_table(pl.DataFrame(columns).to_arrow().cast(MARKETS_SCHEMA), path)
    config = StudyConfig(window_start="2026-01-04")
    counts = exclusion_counts(tag_trades(root, config))
    assert counts["not_binary"] == 6  # named for its own reason, not for the window
    assert sum(counts.values()) == total
