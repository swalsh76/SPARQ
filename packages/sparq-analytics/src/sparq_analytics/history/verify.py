"""Integrity checks on the downloaded history.

* every finished trade day: the file's row count matches the state, trade ids are unique, every
  time lies in the day, and every price is between 0 and one dollar;
* markets: tickers are unique across the historical and live files, with the label counts;
* **volume reconciliation**: for every yes/no/scalar market whose whole life lies inside days that
  were downloaded, the sum of its trade counts must equal the market's reported volume. This
  held to the last unit for the market probed on 2026-10-22 (35,032 trades).
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from sparq_analytics.history.datadir import DataDir, State
from sparq_analytics.history.downloader import DAY, day_start

MAX_EXAMPLES = 10


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


def _market_files(directory: DataDir) -> list[Any]:
    root = directory.root / "markets"
    return sorted(root.rglob("*.parquet")) if root.exists() else []


def verify(directory: DataDir, state: State) -> VerifyReport:
    report = VerifyReport()
    days: dict[str, dict[str, Any]] = state.section("trades")
    done = sorted(k for k, v in days.items() if v.get("status") == "done")
    sums: dict[str, int] = {}
    for key in done:
        table = pq.read_table(
            directory.trades_file(key),
            columns=["trade_id", "ticker", "created_time", "yes_price_e6", "count_e2"],
        )
        report.days_checked += 1
        problems = _check_day(key, table, int(days[key]["rows"]))
        report.day_problems += problems
        if table.num_rows:
            grouped = table.group_by("ticker").aggregate([("count_e2", "sum")])
            for ticker, total in zip(
                grouped["ticker"].to_pylist(), grouped["count_e2_sum"].to_pylist(), strict=True
            ):
                sums[ticker] = sums.get(ticker, 0) + int(total)

    seen: set[str] = set()
    done_days = set(done)
    for path in _market_files(directory):
        markets = pq.read_table(
            path, columns=["ticker", "label", "open_time", "settlement_ts", "volume_e2"]
        )
        report.market_rows += markets.num_rows
        for row in markets.to_pylist():
            ticker = row["ticker"]
            if ticker in seen:
                report.duplicate_markets += 1
            seen.add(ticker)
            report.labels[row["label"]] += 1
            if row["label"] not in ("yes", "no", "scalar"):
                continue
            opened, settled = row["open_time"], row["settlement_ts"]
            if opened is None or settled is None or row["volume_e2"] is None:
                report.not_checkable += 1
                continue
            first, last = opened.date(), settled.date()
            if (last - first).days > 400 or any(
                (first + timedelta(days=i)).isoformat() not in done_days
                for i in range((last - first).days + 1)
            ):
                report.not_checkable += 1
                continue
            report.reconcile_checked += 1
            total = sums.get(ticker, 0)
            if total == row["volume_e2"]:
                report.reconcile_matched += 1
            else:
                report.reconcile_mismatch_count += 1
                if len(report.reconcile_mismatched) < MAX_EXAMPLES:
                    report.reconcile_mismatched.append((ticker, int(row["volume_e2"]), total))
    return report


def _check_day(key: str, table: pa.Table, expected_rows: int) -> list[str]:
    problems: list[str] = []
    if table.num_rows != expected_rows:
        problems.append(f"{key}: file has {table.num_rows} rows, state says {expected_rows}")
    if table.num_rows == 0:
        return problems
    if pc.count_distinct(table["trade_id"]).as_py() != table.num_rows:
        problems.append(f"{key}: duplicate trade ids")
    low = pc.min(table["created_time"]).value // 1_000_000
    high = pc.max(table["created_time"]).value // 1_000_000
    start = day_start(date.fromisoformat(key))
    if low < start or high >= start + DAY:
        problems.append(f"{key}: trade times {low} to {high} (Unix seconds) fall outside the day")
    price_low, price_high = (
        pc.min(table["yes_price_e6"]).as_py(),
        pc.max(table["yes_price_e6"]).as_py(),
    )
    if price_low < 0 or price_high > 1_000_000:
        problems.append(f"{key}: a yes price outside 0 to 1 dollar")
    return problems
