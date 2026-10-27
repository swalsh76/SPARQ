"""Hand-built books and trades for S1.2a's tests, with answers worked out by hand.

Times are milliseconds from ``BASE`` (2026-10-25 00:00 UTC). Sizes and counts are hundredths of a
contract, prices millionths of a dollar.

* ``M1`` (middle tier): a taker buys yes at 42c and clears the ask (5 contracts shown, 5 taken);
  the ask is back at 42c 1.5 s later. A later yes trade at 42c fills a joined order by the queue
  rule, and one at 44c goes through it (the strict rule). A last yes trade at 44c, met by a frame
  at its own millisecond, fills the order joined after the first 44c trade by the queue rule only.
* ``M2`` (middle tier): a taker sells yes at 29c into a bid of 20 contracts and takes 5; 15 are left
  ahead of a joined order, and two later sales at 29c (10, then 6 contracts) fill it by the queue
  rule only. No sale goes below 29c, so the strict rule never fills it.
* ``M3`` (middle tier): a taker buys yes at 50c, but the frame before shows 51c (a stale frame), and
  a better ask of 49c is shown when the order would join.
* ``M4`` to ``M6``: one trade each in a busy, a thin and an unknown series.
* ``M7`` (busy tier, 90c, outside the rule's band): a taker buys yes at 90c and clears the ask of
  1 contract; a frame at the trade's own millisecond shows 3 contracts at 90c. That frame is not
  the book the taker met, and not a refill. Half a contract trades at 90c before a joined order
  could be placed (it joins 1 s later behind 3), and 2.8 more after: the queue is not passed.
* ``M8``: a series exactly at the rule's upper activity bound, which is the busy tier.
* A 5 s outage of the ticker feed one hour in (``gaps``): the M7 trade 10 minutes before it is
  left out by the command line, the one 6.5 minutes after it is kept.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from sparq_analytics.live.source import (
    GAP_SCHEMA,
    HOUR,
    MARKET_SCHEMA,
    TICKER_SCHEMA,
    TRADE_SCHEMA,
    hour_name,
)
from sparq_analytics.study.rules import Rule, rules_from_json

BASE_TIME = datetime(2026, 10, 25, tzinfo=UTC)
BASE = int(BASE_TIME.timestamp() * 1000)

RULE_JSON: dict[str, Any] = {
    "rules": [
        {
            "name": "quiet 60-3600s, busy 1000-100000, price 20-80c",
            "side": "maker",
            "busy_lo": 1000.0,
            "busy_hi": 100000.0,
            "pace": "any",
            "category": None,
            "ny_hours": None,
            "price_lo_e6": 200000,
            "price_hi_e6": 800000,
            "exploratory": False,
            "quiet_lo_s": 60.0,
            "quiet_hi_s": 3600.0,
        }
    ]
}
BUSY_JSON = {"busy": {"MID": 5000.0, "BIG": 200000.0, "THIN": 10.0, "EDGE": 100000.0}}


def rule() -> Rule:
    return rules_from_json(RULE_JSON)[0]


def busy() -> pl.DataFrame:
    series = BUSY_JSON["busy"]
    return pl.DataFrame(
        {"series_ticker": list(series), "busy": list(series.values())},
        schema={"series_ticker": pl.String, "busy": pl.Float64},
    )


def _frame(
    t: int, ticker: str, bid: int | None, bsz: int | None, ask: int | None, asz: int | None
) -> dict[str, Any]:
    return {
        "ts_ms": BASE + t,
        "recv_ms": BASE + t + 500,
        "ticker": ticker,
        "price_e6": None,
        "yes_bid_e6": bid,
        "yes_ask_e6": ask,
        "yes_bid_size_e2": bsz,
        "yes_ask_size_e2": asz,
        "volume_e2": None,
    }


def _trade(t: int, ticker: str, trade_id: str, price: int, count: int, side: str) -> dict[str, Any]:
    return {
        "ts_ms": BASE + t,
        "ticker": ticker,
        "trade_id": trade_id,
        "yes_price_e6": price,
        "count_e2": count,
        "taker_side": side,
        "is_block_trade": 0,
    }


def frames() -> pl.DataFrame:
    rows = [
        # M1
        _frame(10_000, "M1", 400_000, 1_000, 420_000, 500),
        _frame(120_200, "M1", 400_000, 1_000, 430_000, 300),
        _frame(121_500, "M1", 400_000, 1_000, 420_000, 200),
        _frame(150_000, "M1", 430_000, 1_000, 450_000, 200),
        _frame(400_000, "M1", 450_000, 1_000, 470_000, 200),
        # M2
        _frame(1_000, "M2", 290_000, 2_000, 310_000, 1_000),
        _frame(100_100, "M2", 290_000, 1_500, 310_000, 1_000),
        # M3
        _frame(5_000, "M3", 480_000, 100, 510_000, 100),
        _frame(130_500, "M3", 480_000, 100, 490_000, 100),
        # M4 to M6 and M8: a plain book
        *[_frame(1_000, m, 300_000, 100, 320_000, 100) for m in ("M4", "M5", "M6", "M8")],
        # M7
        _frame(1_000, "M7", 880_000, 100, 900_000, 100),
        _frame(100_000, "M7", 880_000, 100, 900_000, 300),
        _frame(103_000, "M7", 880_000, 100, 910_000, 100),
    ]
    return pl.DataFrame(rows, schema=TICKER_SCHEMA)


def trades() -> pl.DataFrame:
    rows = [
        _trade(0, "M1", "a0", 410_000, 100, "no"),
        _trade(120_000, "M1", "a1", 420_000, 500, "yes"),
        _trade(200_000, "M1", "a2", 420_000, 100, "yes"),
        _trade(300_000, "M1", "a3", 440_000, 100, "yes"),
        _trade(400_000, "M1", "a4", 440_000, 100, "yes"),
        _trade(0, "M2", "b0", 300_000, 100, "yes"),
        _trade(100_000, "M2", "b1", 290_000, 500, "no"),
        _trade(200_000, "M2", "b2", 290_000, 1_000, "no"),
        _trade(250_000, "M2", "b3", 290_000, 600, "no"),
        _trade(0, "M3", "c0", 500_000, 100, "no"),
        _trade(130_000, "M3", "c1", 500_000, 100, "yes"),
        _trade(0, "M4", "d0", 310_000, 100, "yes"),
        _trade(0, "M5", "e0", 310_000, 100, "yes"),
        _trade(0, "M6", "f0", 310_000, 100, "yes"),
        _trade(0, "M7", "g0", 890_000, 100, "no"),
        _trade(100_000, "M7", "g1", 900_000, 100, "yes"),
        _trade(100_500, "M7", "g2", 900_000, 50, "yes"),
        _trade(200_000, "M7", "g3", 900_000, 280, "yes"),
        _trade(3_000_000, "M7", "g4", 900_000, 100, "yes"),
        _trade(4_000_000, "M7", "g5", 900_000, 100, "yes"),
        _trade(0, "M8", "h0", 310_000, 100, "yes"),
    ]
    return pl.DataFrame(rows, schema=TRADE_SCHEMA).with_columns(
        pl.col("is_block_trade").cast(pl.Boolean)
    )


def markets() -> pl.DataFrame:
    series = {"M1": "MID", "M2": "MID", "M3": "MID", "M4": "BIG", "M5": "THIN", "M6": "NEW",
              "M7": "BIG", "M8": "EDGE"}  # fmt: skip
    return pl.DataFrame(
        [
            {
                "ticker": m,
                "series_ticker": s,
                "event_ticker": f"E-{m}",
                "category": "Sports",
                "open_ms": BASE - 86_400_000,
                "close_ms": BASE + 86_400_000,
            }
            for m, s in series.items()
        ],
        schema=MARKET_SCHEMA,
    )


def gaps() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "kind": "ticker",
                "start_ms": BASE + 3_600_000,
                "end_ms": BASE + 3_605_000,
                "reason": "connection_lost",
            }
        ],
        schema=GAP_SCHEMA,
    )


def write_hours(folder: Path, hours: int = 25) -> None:
    """The scenario as hour files from ``BASE`` (``hours`` of them; empty where nothing happened),
    with the market list."""
    tick, trade = frames(), trades()
    for i in range(hours):
        start = BASE_TIME + timedelta(hours=i)
        lo, hi = int(start.timestamp() * 1000), int((start + HOUR).timestamp() * 1000)
        for name, data in (("ticker", tick), ("trade", trade)):
            path = folder / name / f"{hour_name(start)}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            data.filter((pl.col("ts_ms") >= lo) & (pl.col("ts_ms") < hi)).write_parquet(path)
    markets().write_parquet(folder / "markets.parquet")
    gaps().write_parquet(folder / "gaps.parquet")


class FakeCopier:
    """A database connection that answers the three queries as PostgreSQL's COPY would, writing
    CSV with a header from the scenario."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    async def copy_from_query(
        self, query: str, *args: Any, output: str, format: str, header: bool
    ) -> str:
        assert format == "csv" and header
        self.calls.append((query, args))
        if "FROM ticker" in query:
            data = self._between(frames(), args)
        elif "FROM trade" in query:
            data = self._between(trades(), args).with_columns(
                pl.col("is_block_trade").cast(pl.Int8)
            )
        elif "FROM gap_log" in query:
            data = gaps()
        else:
            data = markets()
        data.write_csv(output)
        return "COPY"

    @staticmethod
    def _between(data: pl.DataFrame, args: tuple[Any, ...]) -> pl.DataFrame:
        lo, hi = (int(a.timestamp() * 1000) for a in args)
        return data.filter((pl.col("ts_ms") >= lo) & (pl.col("ts_ms") < hi))

    async def close(self) -> None:
        self.closed = True


def write_inputs(folder: Path) -> tuple[Path, Path]:
    rule_path, busy_path = folder / "rule.json", folder / "busy.json"
    rule_path.write_text(json.dumps(RULE_JSON), encoding="utf-8")
    busy_path.write_text(json.dumps(BUSY_JSON), encoding="utf-8")
    return rule_path, busy_path
