"""Integrity checks on the downloaded history.

* every finished trade day: the file's row count matches the state, trade ids are unique, every
  time lies in the day, and every price is between 0 and one dollar;
* markets: tickers are unique across the historical and live files, with the label counts;
* **volume reconciliation**: for every yes/no/scalar market whose whole life lies inside days that
  were downloaded, the sum of its trade counts must equal the market's reported volume. This
  held to the last unit for the market probed on 2026-10-22 (35,032 trades).

The markets are checked as tables (millions of them), not one row at a time.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, cast

import polars as pl
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from sparq_core.fixedpoint import E6

from sparq_analytics.history.datadir import DataDir, State
from sparq_analytics.history.downloader import duplicate_ids, outside_day

MAX_EXAMPLES = 10
MAX_LIFE_DAYS = 400  # a market that lived longer is not reconciled (its days are rarely all here)
OUTCOMES = ("yes", "no", "scalar")


@dataclass(slots=True)
class VerifyReport:
    days_checked: int = 0
    day_problems: list[str] = field(default_factory=list)
    market_rows: int = 0
    duplicate_markets: int = 0
    labels: Counter[str] = field(default_factory=Counter)
    reconcile_checked: int = 0
    reconcile_matched: int = 0
    reconcile_mismatched: list[tuple[str, int, int]] = field(default_factory=list)
    reconcile_mismatch_count: int = 0
    not_checkable: int = 0

    @property
    def ok(self) -> bool:
        return (
            not self.day_problems
            and not self.duplicate_markets
            and not self.reconcile_mismatch_count
        )

    def lines(self) -> list[str]:
        out = [
            f"trade days checked: {self.days_checked}; problems: {len(self.day_problems)}",
            f"markets: {self.market_rows:,} rows; duplicate tickers {self.duplicate_markets}; "
            f"labels {dict(self.labels)}",
            f"volume reconciliation: {self.reconcile_checked:,} markets checked, "
            f"{self.reconcile_matched:,} matched, {self.reconcile_mismatch_count:,} mismatched, "
            f"{self.not_checkable:,} not checkable (their life is not inside the downloaded days)",
        ]
        out += [f"  problem: {p}" for p in self.day_problems[:MAX_EXAMPLES]]
        out += [
            f"  mismatch: {ticker}: market volume {volume} against {total} from trades (hundredths)"
            for ticker, volume, total in self.reconcile_mismatched
        ]
        return out


def _market_files(directory: DataDir) -> list[Path]:
    root = directory.root / "markets"
    return sorted(root.rglob("*.parquet")) if root.exists() else []


def _check_day(key: str, table: pa.Table, expected_rows: int) -> list[str]:
    problems: list[str] = []
    if table.num_rows != expected_rows:
        problems.append(f"{key}: file has {table.num_rows} rows, state says {expected_rows}")
    if table.num_rows == 0:
        return problems
    if duplicate_ids(table):
        problems.append(f"{key}: duplicate trade ids")
    outside = outside_day(table, date.fromisoformat(key))
    if outside is not None:
        low, high = outside
        problems.append(f"{key}: trade times {low} to {high} (Unix seconds) fall outside the day")
    price_low, price_high = (
        pc.min(table["yes_price_e6"]).as_py(),
        pc.max(table["yes_price_e6"]).as_py(),
    )
    if price_low < 0 or price_high > E6:
        problems.append(f"{key}: a yes price outside 0 to 1 dollar")
    return problems


def _all_days_downloaded(markets: pl.DataFrame, done: list[str]) -> pl.Series:
    """For each market (with ``first`` and ``last`` dates), whether every day of its life was
    downloaded: the downloaded days between them, from a running count over the calendar, equal the
    days between them."""
    if markets.is_empty():
        return pl.Series([], dtype=pl.Boolean)
    low, high = cast(date, markets["first"].min()), cast(date, markets["last"].max())
    running = (
        pl.DataFrame({"day": pl.date_range(low, high, "1d", eager=True)})
        .with_columns(downloaded=pl.col("day").dt.to_string("%Y-%m-%d").is_in(done).cast(pl.Int64))
        .with_columns(through=pl.col("downloaded").cum_sum())
    )
    counted = (
        markets.select("first", "last")
        .with_row_index("_row")
        .join(
            running.select(
                pl.col("day").alias("first"),
                pl.col("through").alias("to_first"),
                pl.col("downloaded").alias("first_downloaded"),
            ),
            on="first",
        )
        .join(
            running.select(pl.col("day").alias("last"), pl.col("through").alias("to_last")),
            on="last",
        )  # fmt: skip
        .sort("_row")
    )
    inside = counted["to_last"] - counted["to_first"] + counted["first_downloaded"]
    days = (counted["last"] - counted["first"]).dt.total_days() + 1
    return inside == days


def verify(directory: DataDir, state: State) -> VerifyReport:
    report = VerifyReport()
    days: dict[str, dict[str, Any]] = state.section("trades")
    done = sorted(k for k, v in days.items() if v.get("status") == "done")
    for key in done:
        table = pq.read_table(
            directory.trades_file(key),
            columns=["trade_id", "ticker", "created_time", "yes_price_e6", "count_e2"],
        )
        report.days_checked += 1
        report.day_problems += _check_day(key, table, int(days[key]["rows"]))
    sums = (
        pl.scan_parquet([directory.trades_file(key) for key in done])
        .group_by("ticker")
        .agg(pl.col("count_e2").sum().alias("total"))
        .collect()
        if done
        else pl.DataFrame(schema={"ticker": pl.String, "total": pl.Int64})
    )

    files = _market_files(directory)
    if not files:
        return report
    columns = ["ticker", "label", "open_time", "settlement_ts", "volume_e2"]
    markets = pl.concat([pl.read_parquet(path, columns=columns) for path in files])
    report.market_rows = markets.height
    report.duplicate_markets = markets.height - markets["ticker"].n_unique()
    report.labels = Counter(
        {str(k): int(n) for k, n in markets["label"].value_counts().iter_rows()}
    )

    outcomes = markets.filter(pl.col("label").is_in(OUTCOMES)).with_row_index("_order")
    known = pl.all_horizontal(
        pl.col("open_time").is_not_null(),
        pl.col("settlement_ts").is_not_null(),
        pl.col("volume_e2").is_not_null(),
    )
    timed = outcomes.filter(known).with_columns(
        first=pl.col("open_time").dt.date(), last=pl.col("settlement_ts").dt.date()
    )
    short = timed.filter((pl.col("last") - pl.col("first")).dt.total_days() <= MAX_LIFE_DAYS)
    checked = short.filter(_all_days_downloaded(short, done))
    report.not_checkable = outcomes.height - checked.height

    compared = (
        checked.join(sums, on="ticker", how="left")
        .with_columns(pl.col("total").fill_null(0))
        .sort("_order")
    )
    wrong = compared.filter(pl.col("total") != pl.col("volume_e2"))
    report.reconcile_checked = compared.height
    report.reconcile_mismatch_count = wrong.height
    report.reconcile_matched = compared.height - wrong.height
    report.reconcile_mismatched = [
        (str(ticker), int(volume), int(total))
        for ticker, volume, total in wrong.head(MAX_EXAMPLES)
        .select("ticker", "volume_e2", "total")
        .iter_rows()
    ]
    return report
