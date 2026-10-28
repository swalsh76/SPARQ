"""The single-pass fee lookup against the method it replaced (a split, a sort and an as-of join),
which is kept here as the oracle on randomised data."""

import datetime as dt
import random
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sparq_analytics.history.schemas import FEE_CHANGES_SCHEMA, SERIES_SCHEMA
from sparq_analytics.study.dataset import with_fees

START = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
FEE_TYPES = ("quadratic", "quadratic_with_maker_fees", "fixed")


def oracle(trades: pl.LazyFrame, root: Path) -> pl.LazyFrame:
    """The previous implementation, unchanged, as the reference."""
    series = pl.scan_parquet(root / "series.parquet").select(
        pl.col("ticker").alias("series_ticker"),
        pl.col("fee_type"),
        pl.col("fee_multiplier_e6").alias("multiplier_e6"),
    )
    changes = pl.scan_parquet(root / "fee_changes.parquet").select(
        "series_ticker",
        pl.col("scheduled_ts"),
        pl.col("fee_type"),
        pl.col("fee_multiplier_e6").alias("multiplier_e6"),
    )
    changed = changes.select("series_ticker").unique().collect()["series_ticker"].to_list()
    in_changed = pl.col("series_ticker").is_in(changed).fill_null(False)
    plain = trades.filter(~in_changed).join(series, on="series_ticker", how="left")
    timeline = changes.sort("scheduled_ts")
    changing = (
        trades.filter(in_changed)
        .sort("t")
        .join_asof(
            timeline,
            left_on="t",
            right_on="scheduled_ts",
            by="series_ticker",
            strategy="backward",
            check_sortedness=False,
        )
        .drop("scheduled_ts")
    )
    return pl.concat([plain, changing.select(plain.collect_schema().names())])


def build(root: Path, seed: int) -> pl.LazyFrame:
    rng = random.Random(seed)  # noqa: S311 (test data, not security)
    names = [f"KXS{i}" for i in range(8)]
    root.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "ticker": names[:-1],  # the last series is only known through its changes
                "title": [None] * 7,
                "category": [None] * 7,
                "frequency": [None] * 7,
                "tags": [None] * 7,
                "exchange_index": [None] * 7,
                "fee_type": [rng.choice(FEE_TYPES) for _ in range(7)],
                "fee_multiplier_e6": [rng.randrange(100_000, 2_000_000) for _ in range(7)],
                "fetched_at": [START] * 7,
            },
            schema=SERIES_SCHEMA,
        ),
        root / "series.parquet",
    )
    rows: dict[str, list[object]] = {k: [] for k in FEE_CHANGES_SCHEMA.names}
    used_times: set[tuple[str, int]] = set()
    scheduled: list[tuple[str, dt.datetime]] = []
    for name in rng.sample(names, 5):  # some series change several times, the rest never
        for _ in range(rng.randrange(1, 5)):
            hours = rng.randrange(1, 24 * 20)
            if (name, hours) in used_times:
                continue  # two changes at the same instant have no defined order
            used_times.add((name, hours))
            rows["id"].append(f"{name}-{hours}")
            rows["series_ticker"].append(name)
            rows["fee_type"].append(rng.choice(FEE_TYPES))
            rows["fee_multiplier_e6"].append(rng.randrange(100_000, 2_000_000))
            rows["scheduled_ts"].append(START + dt.timedelta(hours=hours))
            scheduled.append((name, START + dt.timedelta(hours=hours)))
            rows["fetched_at"].append(START)
    pq.write_table(pa.table(rows, schema=FEE_CHANGES_SCHEMA), root / "fee_changes.parquet")
    series_of = [rng.choice([*names, None, "KXUNKNOWN"]) for _ in range(4_000)]
    times = [START + dt.timedelta(seconds=rng.randrange(0, 24 * 25 * 3600)) for _ in series_of]
    for name, when in scheduled:
        for shift in (-1, 0, 1):  # a trade just before, exactly at and just after each change
            series_of.append(name)
            times.append(when + dt.timedelta(seconds=shift))
    return pl.DataFrame(
        {"id": list(range(len(series_of))), "series_ticker": series_of, "t": times},
        schema_overrides={"t": pl.Datetime("us", "UTC")},
    ).lazy()


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_the_single_pass_lookup_gives_the_same_fees_as_the_split_and_sort(
    tmp_path: Path, seed: int
) -> None:
    trades = build(tmp_path / "kalshi", seed)
    root = tmp_path / "kalshi"
    mine = with_fees(trades, root).sort("id").collect()
    reference = oracle(trades, root).sort("id").collect()
    assert mine.height == reference.height == trades.collect().height  # none lost or doubled
    assert mine.select("id", "fee_type", "multiplier_e6").equals(
        reference.select("id", "fee_type", "multiplier_e6")
    )
    assert mine["fee_type"].null_count() > 0  # trades before a first change, or in unknown series


def test_the_oracle_scenario_really_exercises_the_cases(tmp_path: Path) -> None:
    trades = build(tmp_path / "kalshi", 1)
    result = with_fees(trades, tmp_path / "kalshi").collect()
    assert result["series_ticker"].null_count() > 0  # unknown market
    assert (result["series_ticker"] == "KXUNKNOWN").sum() > 0  # a series in no table
    assert result["fee_type"].n_unique() > 2  # several fees in force over time
