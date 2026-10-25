import datetime as dt
from collections.abc import Callable
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sparq_analytics.history.schemas import FEE_CHANGES_SCHEMA, MARKETS_SCHEMA, TRADES_SCHEMA
from sparq_analytics.study.config import StudyConfig
from sparq_analytics.study.dataset import StudyDataError, exclusion_counts, tag_trades, used
from sparq_analytics.study.synthetic import World, write_world
from sparq_core.fees import fee_schedule, maker_fee_e6, taker_fee_e6


@pytest.fixture(scope="module")
def tagged(tmp_path_factory: pytest.TempPathFactory) -> tuple[pl.DataFrame, dict[str, int]]:
    root = tmp_path_factory.mktemp("world") / "kalshi"
    counts = write_world(World(events=120, trades_per_market=8, seed=3), root)
    return tag_trades(root, StudyConfig()).collect(), counts


def test_every_trade_is_kept_with_a_reason_or_used(
    tagged: tuple[pl.DataFrame, dict[str, int]],
) -> None:
    frame, counts = tagged
    assert frame.height == counts["trades"]
    assert frame["exclusion"].null_count() == frame.height  # a clean world excludes nothing


def test_fees_match_the_scalar_reference_for_every_trade(
    tagged: tuple[pl.DataFrame, dict[str, int]],
) -> None:
    frame, _ = tagged
    schedules = {
        "KXSYN0": fee_schedule("quadratic_with_maker_fees", 1_000_000),
        "KXSYN1": fee_schedule("quadratic", 1_000_000),
    }
    for row in frame.iter_rows(named=True):
        schedule = schedules[row["series_ticker"]]
        assert schedule is not None
        assert row["tk_fee"] == schedule.taker_fee_e6(row["c"], row["p"])
        assert row["mk_fee_nom"] == schedule.maker_fee_e6(row["c"], row["p"])
        share = 50 if schedule.maker_share_e2 > 0 else 0
        assert row["mk_fee_cons"] == maker_fee_e6(1_000_000, share, row["c"], row["p"])


def test_the_outcome_time_left_and_category_are_attached(
    tagged: tuple[pl.DataFrame, dict[str, int]],
) -> None:
    frame, _ = tagged
    assert set(frame["category"].unique()) == {"sports", "politics"}
    assert set(frame["y"].unique()) <= {0, 1}
    assert (frame["tleft_s"] >= 0).all()
    assert (frame["ev_open"] < frame["ev_close"]).all()


def test_exclusions_are_counted_by_reason(tmp_path: Path) -> None:
    root = tmp_path / "kalshi"
    write_world(World(events=20, trades_per_market=4, seed=5), root)
    tagged = tag_trades(root, StudyConfig())
    assert exclusion_counts(tagged) == {"used": tagged.collect().height}
    assert used(tagged).collect().height == tagged.collect().height


def test_a_folder_without_trades_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StudyDataError):
        tag_trades(tmp_path, StudyConfig())


# --- exclusions and edge cases: edit a small written world, then tag it ---------------------------


def small_world(tmp_path: Path) -> Path:
    root = tmp_path / "kalshi"
    write_world(World(events=40, trades_per_market=5, seed=8), root)
    return root


def edit_markets(root: Path, change: Callable[[dict[str, list[Any]]], None]) -> None:
    path = root / "markets" / "historical" / "part-000000.parquet"
    columns = pq.read_table(path).to_pydict()
    change(columns)
    pq.write_table(pa.table(columns, schema=MARKETS_SCHEMA), path)


def add_trades(root: Path, rows: list[dict[str, Any]], day: str = "2026-01-02") -> None:
    columns = {name: [r.get(name) for r in rows] for name in TRADES_SCHEMA.names}
    path = root / "trades" / f"date={day}-extra" / "trades.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.table(columns, schema=TRADES_SCHEMA), path)


def a_trade(ticker: str, side: str = "yes") -> dict[str, Any]:
    when = dt.datetime(2026, 1, 3, 12, tzinfo=dt.UTC)
    return {
        "trade_id": f"x-{ticker}-{side}",
        "ticker": ticker,
        "created_time": when,
        "yes_price_e6": 400_000,
        "count_e2": 100,
        "taker_side": side,
        "is_block_trade": False,
    }


def reasons(root: Path) -> dict[str, int]:
    return exclusion_counts(tag_trades(root, StudyConfig()))


def test_a_trade_in_an_unknown_market_is_counted_as_market_missing(tmp_path: Path) -> None:
    root = small_world(tmp_path)
    add_trades(root, [a_trade("NOT-A-MARKET")])
    assert reasons(root)["market_missing"] == 1


def test_a_trade_with_an_unknown_taker_side_is_counted_as_bad_side(tmp_path: Path) -> None:
    root = small_world(tmp_path)
    ticker = pq.read_table(root / "markets" / "historical" / "part-000000.parquet")["ticker"][0]
    add_trades(root, [a_trade(ticker.as_py(), side="maybe")])
    assert reasons(root)["bad_side"] == 1


def test_trades_of_a_scalar_market_are_not_binary(tmp_path: Path) -> None:
    root = small_world(tmp_path)
    victim = pq.read_table(root / "markets" / "historical" / "part-000000.parquet")["ticker"][0]

    def scalar(columns: dict[str, list[Any]]) -> None:
        columns["label"][0] = "scalar"

    edit_markets(root, scalar)
    assert reasons(root)["not_binary"] == 5
    assert victim.as_py().startswith("KXSYN")


def test_a_market_without_a_time_is_time_unknown(tmp_path: Path) -> None:
    root = small_world(tmp_path)

    def blank(columns: dict[str, list[Any]]) -> None:
        columns["close_time"][0] = None
        columns["expected_expiration_time"][0] = None

    edit_markets(root, blank)
    assert reasons(root)["time_unknown"] == 5


def test_an_event_without_an_open_time_is_event_time_unknown(tmp_path: Path) -> None:
    root = small_world(tmp_path)

    def blank(columns: dict[str, list[Any]]) -> None:
        for i, event in enumerate(columns["event_ticker"]):
            if event == columns["event_ticker"][0]:
                columns["open_time"][i] = None
                columns["created_time"][i] = None

    edit_markets(root, blank)
    assert reasons(root)["event_time_unknown"] == 10  # two markets of five trades


def test_the_fee_in_force_at_a_trade_follows_the_recorded_changes(tmp_path: Path) -> None:
    root = small_world(tmp_path)
    change_at = dt.datetime(2026, 1, 4, tzinfo=dt.UTC)
    changes = pa.table(
        {
            "id": ["c1"],
            "series_ticker": ["KXSYN0"],
            "fee_type": ["quadratic"],
            "fee_multiplier_e6": [500_000],
            "scheduled_ts": [change_at],
            "fetched_at": [change_at],
        },
        schema=FEE_CHANGES_SCHEMA,
    )
    pq.write_table(changes, root / "fee_changes.parquet")
    frame = tag_trades(root, StudyConfig()).collect()
    sports = frame.filter(pl.col("series_ticker") == "KXSYN0")
    before = sports.filter(pl.col("t") < change_at)
    after = sports.filter(pl.col("t") >= change_at)
    assert before.height > 0 and after.height > 0
    assert set(before["exclusion"].unique()) == {"fee_unknown"}
    assert before["tk_fee"].null_count() == before.height
    for row in after.iter_rows(named=True):
        assert row["exclusion"] is None
        assert row["tk_fee"] == taker_fee_e6(500_000, row["c"], row["p"])
        assert row["mk_fee_nom"] == 0
        assert row["mk_fee_cons"] == 0
    politics = frame.filter(pl.col("series_ticker") == "KXSYN1")
    assert politics["exclusion"].null_count() == politics.height


def test_the_time_reference_follows_the_category_setting(tmp_path: Path) -> None:
    root = small_world(tmp_path)

    def later_expiry(columns: dict[str, list[Any]]) -> None:
        columns["expected_expiration_time"] = [
            t + dt.timedelta(hours=1) for t in columns["close_time"]
        ]

    edit_markets(root, later_expiry)
    default = tag_trades(root, StudyConfig()).collect().sort("ticker", "t")
    expected = (
        tag_trades(
            root, StudyConfig(reference_by_category=(("sports", ("expected_expiration_time",)),))
        )
        .collect()
        .sort("ticker", "t")
    )
    difference = (expected["tleft_s"] - default["tleft_s"]).to_list()
    sports = (default["category"] == "sports").to_list()
    assert all(d == (3600 if s else 0) for d, s in zip(difference, sports, strict=True))


def test_the_default_time_reference_is_the_close_time_with_the_expected_time_as_fallback(
    tmp_path: Path,
) -> None:
    root = small_world(tmp_path)

    def only_expected(columns: dict[str, list[Any]]) -> None:
        columns["close_time"][0] = None
        columns["expected_expiration_time"][0] = columns["open_time"][0] + dt.timedelta(hours=2)

    edit_markets(root, only_expected)
    assert reasons(root).get("time_unknown", 0) == 0


def test_a_market_in_both_the_historical_and_live_files_counts_once(tmp_path: Path) -> None:
    root = small_world(tmp_path)
    table = pq.read_table(root / "markets" / "historical" / "part-000000.parquet").to_pydict()
    table["label"] = ["unsettled"] * len(table["label"])
    live = root / "markets" / "live" / "date=2026-01-05" / "markets.parquet"
    live.parent.mkdir(parents=True)
    pq.write_table(pa.table(table, schema=MARKETS_SCHEMA), live)
    assert reasons(root) == {"used": 40 * 2 * 5}


def test_a_folder_without_markets_or_series_is_refused(tmp_path: Path) -> None:
    root = small_world(tmp_path)
    (root / "series.parquet").unlink()
    with pytest.raises(StudyDataError, match="series"):
        tag_trades(root, StudyConfig())
    root = small_world(tmp_path / "second")
    for part in (root / "markets" / "historical").glob("part-*.parquet"):
        part.unlink()
    with pytest.raises(StudyDataError, match="markets"):
        tag_trades(root, StudyConfig())


def test_a_trade_after_the_expected_time_has_no_time_left_and_is_kept(tmp_path: Path) -> None:
    root = small_world(tmp_path)
    ticker = pq.read_table(root / "markets" / "historical" / "part-000000.parquet")["ticker"][0]
    late = a_trade(ticker.as_py()) | {"created_time": dt.datetime(2026, 12, 1, tzinfo=dt.UTC)}
    add_trades(root, [late | {"count_e2": 7700}])
    row = tag_trades(root, StudyConfig()).filter(pl.col("c") == 7700).collect().row(0, named=True)
    assert row["tleft_s"] == 0
    assert row["exclusion"] is None


def test_the_takers_side_is_carried_through(tmp_path: Path) -> None:
    root = small_world(tmp_path)
    ticker = pq.read_table(root / "markets" / "historical" / "part-000000.parquet")["ticker"][0]
    add_trades(
        root,
        [
            a_trade(ticker.as_py(), side="yes") | {"count_e2": 7701},
            a_trade(ticker.as_py(), side="no") | {"count_e2": 7702},
        ],
    )
    frame = tag_trades(root, StudyConfig()).collect()
    assert frame.filter(pl.col("c") == 7701)["taker_yes"].to_list() == [True]
    assert frame.filter(pl.col("c") == 7702)["taker_yes"].to_list() == [False]


def test_no_trade_in_the_files_is_lost_with_fee_changes_and_unknown_markets(tmp_path: Path) -> None:
    """Found on the real data: a trade in a market that was never downloaded (not yet settled) has
    a null series, and a null-blind filter dropped it silently, 8.5% of one real day."""
    root = small_world(tmp_path)
    change_at = dt.datetime(2026, 1, 4, tzinfo=dt.UTC)
    changes = pa.table(
        {
            "id": ["c1"],
            "series_ticker": ["KXSYN0"],
            "fee_type": ["quadratic"],
            "fee_multiplier_e6": [500_000],
            "scheduled_ts": [change_at],
            "fetched_at": [change_at],
        },
        schema=FEE_CHANGES_SCHEMA,
    )
    pq.write_table(changes, root / "fee_changes.parquet")
    add_trades(root, [a_trade("NOT-A-MARKET"), a_trade("ALSO-NOT-ONE", side="no")])
    in_files = sum(
        pq.ParquetFile(f).metadata.num_rows for f in root.glob("trades/*/trades.parquet")
    )
    tagged = tag_trades(root, StudyConfig())
    assert tagged.collect().height == in_files
    assert sum(exclusion_counts(tagged).values()) == in_files
    assert exclusion_counts(tagged)["market_missing"] == 2
