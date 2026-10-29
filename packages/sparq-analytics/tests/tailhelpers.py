"""A hand-built world for S3.3's tests (the thin tail and the surprise rate), with the answers
worked out by hand. Prices are yes prices in millionths; counts are hundredths of a contract.

**History** (trade files for 2026-09-14, 15 and 16; the volume window is those three days):

* ``V1`` (``KXA``, Sports, yes): open 09-13 12:00, close 09-16 12:00, so 2.5 days of the window
  (3 when rounded up). Trades 09-14 10:00 at 40 for 1 contract, 09-15 10:00 at 60 for 2, 09-16
  09:00 at 50 for 3: 6 contracts over 3 days, 2 a day, the tier "under 100"; $3.10 of dollars.
* ``V2`` (``KXB``, Crypto, no): open 09-15 00:00, close 01:00 (one day when rounded up); one
  trade at 50 for 200 contracts: 200 a day, "100 to 1,000"; $100.
* ``V3`` (``KXA``, Sports): open in the window, never traded: silent.
* ``V4`` (``KXC``, Politics): closed 09-10, before the window: not counted as open.
* ``S1`` to ``S6`` (open 09-10, close 09-15 20:00: 2 days of the window) and ``Z01`` to ``Z60``
  (open 09-14, same close): one contract a trade.

The volume by tier: "under 100" holds 67 markets (V1, S1 to S6, the 60 Z) with 72 trades and 75
contracts over 3 days (25 a day) and $67.21 ($22.40 a day); "100 to 1,000" holds V2. 69 markets
were open in the window, 68 traded, 1 (V3, Sports) was silent.

**The surprise reading** (cut 2026-09-16; a market's day-before price is its last trade from 36 to
12 hours before its close):

* ``S1`` (``KXS``, Sports, event E1, yes; close 09-15 20:00): trades at 85 (40 hours before,
  outside), 92 (30 hours), 94 (20 hours, the last inside) and 97 (6 hours, outside): priced 94,
  side yes at 94, no surprise.
* ``S2`` (``KXS``, E1, no): 91 at 24 hours: side yes, **surprise**.
* ``S3`` (``KXS``, E2, no): 8 at 24 hours: side no at 92, no surprise.
* ``S4`` (``KXP``, Politics, yes): 50 at 24 hours: not in the tail.
* ``S5`` (``KXP``, yes): a trade 5 hours before only: unpriced.
* ``S6`` (``KXS``, E3, yes): 99 at 24 hours: side yes at 99, bucket "99 and more", no surprise.
* ``Z01`` to ``Z60`` (``KXZ``, Economics, each its own event, all no): 95 at 24 hours: side yes
  at 95, every one a **surprise**.
* ``V1`` is priced 60 (not in the tail); ``V2``, ``V3`` and ``V4`` have no trade in their windows.

70 settled markets; 66 priced, 4 unpriced; 2 not in the tail; 64 in it. Surprises 61 of 64 (0.953)
against an implied 0.0506; Sports 1 of 4 (0.25, implied 0.06); Economics 60 of 60 against 0.05,
an interval of width zero, so ``KXZ`` (60 markets, the only series with 50) clears and is listed
with its title "Zed" and its rules; the "95 to 99" bucket is the sixty Z, "90 to 95" is S1, S2 and
S3 (1 of 3), "99 and more" is S6.

**Live** (hour files from 2026-10-25 00:00, two hours, with an empty hour before):

* ``L1`` (``KXA``, Sports): 00:00 bid 40 ask 44 (a 4-cent spread); 00:30 bid 30 ask 60 (30 cents);
  01:00 bid 50 with the ask side empty (the feed shows a 100-cent ask of no size).
  Time-weighted: 30 minutes at 4 cents and 30 at 30, so a mean spread of 17
  cents while two-sided, and two-sided half the time. Trades: 00:10 a yes taker of 1 contract at
  44, when the spread was 4 cents (**contested**; half-spread 2 cents times 1 contract, $0.02);
  00:40 a no taker of 2 at 30 (30 cents, **uncontested**; 15 cents times 2, $0.30); 01:10 a yes
  taker of 0.5 at 50 with one side missing (**one-sided**, no half-spread to read). The live
  tiers follow the history's rule: contracts over the days open inside the span, at least one
  day, so 3.5 contracts are 3.5 a day: "under 100", thin. No maker fee in ``KXA``.
* ``L2`` (``KXM``, Sports, maker fees): 48/52 throughout (4 cents, two-sided all along). Trade
  00:20 a yes taker of 10 contracts at 52: contested, half-spread $0.20, maker fee $0.04368 (7% of
  a quarter of 10 × 0.52 × 0.48). 10 a day: "under 100", thin.
* ``L3`` (``KXB``, Crypto): 10/90 throughout (80 cents). Trade 00:15 a yes taker of 100,000
  contracts at 90: uncontested, half-spread $40,000. 100,000 a day: "10,000 and more".

Scaled to a day (twelve times the two hours): contested $2.64 gross, $0.52 of fees; uncontested
$480,003.60 over all tiers but only $3.60 in the thin tiers (L1), far below the $250 bar.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from sparq_analytics.live.source import (
    GAP_SCHEMA,
    MARKET_SCHEMA,
    TICKER_SCHEMA,
    TRADE_SCHEMA,
    hour_name,
)
from surveyhelpers import BASE_TIME, HOUR, _frame, _trade, lm

D14 = datetime(2026, 9, 14, tzinfo=UTC)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A moment ``day`` days after 2026-09-14 00:00 UTC (negative days are before it)."""
    return D14 + timedelta(days=day, hours=hour, minutes=minute)


SERIES = pl.DataFrame(
    {
        "ticker": ["KXA", "KXB", "KXC", "KXS", "KXP", "KXZ", "KXM"],
        "title": ["Alpha", "Beta", "Gamma", "Sigma", "Pi", "Zed", "Mu"],
        "category": ["Sports", "Crypto", "Politics", "Sports", "Politics", "Economics", "Sports"],
        "fee_type": ["quadratic"] * 6 + ["quadratic_with_maker_fees"],
        "fee_multiplier_e6": [1_000_000] * 7,
    }
)


def _market(
    ticker: str,
    series: str,
    event: str,
    result: str,
    open_time: datetime,
    close: datetime,
    rules: str,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "series_ticker": series,
        "event_ticker": event,
        "result": result,
        "open_time": open_time,
        "close_time": close,
        "settlement_ts": close + timedelta(minutes=5),
        "rules_primary": rules,
    }


GAME_CLOSE = at(1, 20)
MARKETS = [
    _market("V1", "KXA", "KXA-E1", "yes", at(-1, 12), at(2, 12), "Rules A"),
    _market("V2", "KXB", "KXB-E1", "no", at(1, 0), at(1, 1), "Rules B"),
    _market("V3", "KXA", "KXA-E3", "yes", at(1, 0), at(2, 0), "Rules A"),
    _market("V4", "KXC", "KXC-E1", "yes", at(-13, 0), at(-4, 12), "Rules C"),
    _market("S1", "KXS", "KXS-E1", "yes", at(-4, 0), GAME_CLOSE, "Rules S"),
    _market("S2", "KXS", "KXS-E1", "no", at(-4, 0), GAME_CLOSE, "Rules S"),
    _market("S3", "KXS", "KXS-E2", "no", at(-4, 0), GAME_CLOSE, "Rules S"),
    _market("S4", "KXP", "KXP-E4", "yes", at(-4, 0), GAME_CLOSE, "Rules P"),
    _market("S5", "KXP", "KXP-E5", "yes", at(-4, 0), GAME_CLOSE, "Rules P"),
    _market("S6", "KXS", "KXS-E3", "yes", at(-4, 0), GAME_CLOSE, "Rules S"),
    *[
        _market(f"Z{i:02d}", "KXZ", f"KXZ-E{i:02d}", "no", at(0, 0), GAME_CLOSE, "Rules Z")
        for i in range(1, 61)
    ],
]
# (ticker, time, taker side, yes price, count)
TRADES: list[tuple[str, datetime, str, int, int]] = [
    ("V1", at(0, 10), "yes", 400_000, 100),
    ("V1", at(1, 10), "yes", 600_000, 200),
    ("V1", at(2, 9), "no", 500_000, 300),
    ("V2", at(1, 0, 30), "yes", 500_000, 20_000),
    ("S1", at(0, 4), "yes", 850_000, 100),
    ("S1", at(0, 14), "yes", 920_000, 100),
    ("S1", at(1, 0), "yes", 940_000, 100),
    ("S1", at(1, 14), "yes", 970_000, 100),
    ("S2", at(0, 20), "yes", 910_000, 100),
    ("S3", at(0, 20), "no", 80_000, 100),
    ("S4", at(0, 20), "yes", 500_000, 100),
    ("S5", at(1, 15), "yes", 950_000, 100),
    ("S6", at(0, 20), "yes", 990_000, 100),
    *[(f"Z{i:02d}", at(0, 20), "yes", 950_000, 100) for i in range(1, 61)],
]


def write_tail_history(root: Path, only: Sequence[str] | None = None) -> Path:
    """The history world under ``root / "kalshi"``; with ``only``, just those markets."""
    kalshi = root / "kalshi"
    historical = kalshi / "markets" / "historical"
    historical.mkdir(parents=True)
    markets = [m for m in MARKETS if only is None or m["ticker"] in only]
    trades = [t for t in TRADES if only is None or t[0] in only]
    pl.DataFrame(markets).write_parquet(historical / "part-0.parquet")
    SERIES.write_parquet(kalshi / "series.parquet")
    for day in (0, 1, 2):
        start, end = at(day, 0), at(day + 1, 0)
        rows = [t for t in trades if start <= t[1] < end]
        folder = kalshi / "trades" / f"date={start.date()}"
        folder.mkdir(parents=True)
        pl.DataFrame(
            {
                "trade_id": [f"{t[0]}-{i}" for i, t in enumerate(rows)],
                "ticker": [t[0] for t in rows],
                "created_time": [t[1] for t in rows],
                "yes_price_e6": [t[3] for t in rows],
                "count_e2": [t[4] for t in rows],
                "taker_side": [t[2] for t in rows],
                "is_block_trade": [False] * len(rows),
            },
            schema_overrides={
                "yes_price_e6": pl.Int32,
                "count_e2": pl.Int64,
                "taker_side": pl.String,
                "created_time": pl.Datetime("us", "UTC"),
            },
        ).write_parquet(folder / "trades.parquet")
    return kalshi


FRAMES = [
    _frame(lm(0, 0), "L1", 400_000, 10, 440_000, 10),
    _frame(lm(0, 30), "L1", 300_000, 10, 600_000, 10),
    _frame(lm(1, 0), "L1", 500_000, 10, 1_000_000, 0),  # an empty ask, as the feed shows it
    _frame(lm(0, 0), "L2", 480_000, 10, 520_000, 10),
    _frame(lm(0, 0), "L3", 100_000, 10, 900_000, 10),
]
LIVE_TRADES = [
    _trade(lm(0, 10), "L1", 440_000, 100, "yes"),
    _trade(lm(0, 40), "L1", 300_000, 200, "no"),
    _trade(lm(1, 10), "L1", 500_000, 50, "yes"),
    _trade(lm(0, 20), "L2", 520_000, 1_000, "yes"),
    _trade(lm(0, 15), "L3", 900_000, 10_000_000, "yes"),
]
LIVE_SERIES = {"L1": "KXA", "L2": "KXM", "L3": "KXB"}
CATEGORY = {"KXA": "Sports", "KXM": "Sports", "KXB": "Crypto"}
FEES = pl.DataFrame(
    {
        "series_ticker": ["KXA", "KXM", "KXB"],
        "fee_type": ["quadratic", "quadratic_with_maker_fees", "quadratic"],
        "multiplier_e6": [1_000_000] * 3,
    }
)


def write_tail_live(root: Path, trades: bool = True) -> Path:
    """The live world under ``root / "live"``: an empty hour, then two hours."""
    live = root / "live"
    frames = pl.DataFrame(FRAMES, schema=TICKER_SCHEMA)
    tape = pl.DataFrame(LIVE_TRADES if trades else [], schema=TRADE_SCHEMA)
    for i in (-1, 0, 1):
        start = BASE_TIME + i * HOUR
        lo, hi = int(start.timestamp() * 1000), int((start + HOUR).timestamp() * 1000)
        for name, data in (("ticker", frames), ("trade", tape)):
            path = live / name / f"{hour_name(start)}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            data.filter((pl.col("ts_ms") >= lo) & (pl.col("ts_ms") < hi)).write_parquet(path)
    pl.DataFrame(
        [
            {
                "ticker": m,
                "series_ticker": s,
                "event_ticker": f"{s}-E",
                "category": CATEGORY[s],
                "open_ms": lm(0, 0) - 86_400_000,
                "close_ms": lm(0, 0) + 86_400_000,
                "strike_type": None,
                "floor_strike": None,
                "cap_strike": None,
                "weather_rules": None,
            }
            for m, s in LIVE_SERIES.items()
        ],
        schema=MARKET_SCHEMA,
    ).write_parquet(live / "markets.parquet")
    pl.DataFrame([], schema=GAP_SCHEMA).write_parquet(live / "gaps.parquet")
    return live
