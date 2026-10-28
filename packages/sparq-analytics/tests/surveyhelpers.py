"""Hand-built worlds for S3.1's tests, with answers worked out by hand.

**History** (one day, 2026-09-15 UTC; prices are yes prices, counts hundredths of a contract):

* ``A`` (Sports, yes wins; closes 20:00, settles 20:05): 50c, then the yes side at 97 (a taker
  bought no: 10 contracts dumped into a 97 bid), at 99 (20 dumped), back to 96, at 99 (30 dumped),
  at 99 lifted by a yes taker (5), at 98 (7 dumped), and at 99 with no taker side (3). The yes
  regime starts at 14:00, the first 99 after the last print below 97 (13:00): 6 hours to the end,
  6 h 05 min to settlement; 30 contracts dumped inside it, 5 lifted.
* ``B`` (Weather, no wins; closes 23:00 but settles 22:00, so it ends 22:00): the no side at 97
  (10 dumped), at 99 (40 dumped), at 99 lifted by a no taker (8). The no regime starts 11:00: 11
  hours to the end and to settlement.
* ``C`` (Sports, yes wins; closes 20:00, settles 20:30): the no side at 99 (a 99-cent bid for no
  bought 15 and lost: an any-time reversal, not a false regime, since no then printed at 50), then
  50c, then the yes side at 99 (12 dumped): the yes regime from 18:00, 2 hours to the end.
* ``D`` (Sports, no wins): the yes side at 99 (25 dumped) and never below 97 again: a **false
  regime** for yes, which loses.
* ``G`` (Sports, in a series with maker fees, yes wins): the yes side at 99 at 10:00 (10 dumped).
* ``H`` (Culture, yes wins): one trade at 50c, so its category never printed 99 at all.
* ``E`` closes after the cut and ``F`` is unsettled: both left out, trades and all.

**Live** (hour files from 2026-10-25 00:00 UTC, four hours, plus an empty hour at 21:00 the
day before):

* ``L1`` (Sports): bid 97, then 98, then **99 from 01:30** (1,000 shown; 3,000 at 02:00), 98 at
  02:30 and again at 02:50, **99 again from 03:00** (200 shown; 400 at 03:30, open at the
  span's end). Trades at 99:
  01:45 a no taker (25 dumped into the first episode), 02:15 a yes taker (3 lifted), 02:45 a yes
  taker outside any episode, 03:15 a no taker (12 dumped into the second).
* ``L2`` (Weather): ask 2c, then **ask 1c from 01:40** (900 shown; 1,100 at 02:40, open). Trades at
  1c: 02:00 a yes taker (150 dumped into the no bids, more than a 100-contract bid takes),
  02:10 a no taker (1 lifted).
* ``L3`` (Sports): a plain book at 50c, one trade: no episode.
* ``L4`` (Sports, maker fees): **bid 99 from 01:00** (100 shown; 150 at 02:00, open); 01:30 a no
  taker dumps 42. No frame in the two hours before 00:00: its queue an hour earlier is unknown.
* ``L5`` (Sports): bid 99 at 00:30, in the span's first hour: left-censored, excluded.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from sparq_analytics.live.source import MARKET_SCHEMA, TICKER_SCHEMA, TRADE_SCHEMA, hour_name

DAY = datetime(2026, 9, 15, tzinfo=UTC)
BASE_TIME = datetime(2026, 10, 25, tzinfo=UTC)
BASE = int(BASE_TIME.timestamp() * 1000)
HOUR = timedelta(hours=1)


def at(hour: int, minute: int = 0) -> datetime:
    return DAY.replace(hour=hour, minute=minute)


def ms(hour: int, minute: int = 0) -> int:
    return int(at(hour, minute).timestamp() * 1000)


def lm(hour: int, minute: int = 0) -> int:
    return BASE + (hour * 60 + minute) * 60_000


def _market(
    ticker: str, series: str, result: str, close: datetime, settlement: datetime | None
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "series_ticker": series,
        "event_ticker": f"{series}-E",
        "result": result,
        "close_time": close,
        "settlement_ts": settlement,
    }


MARKETS = [
    _market("A", "KXNFLGAME", "yes", at(20), at(20, 5)),
    _market("B", "KXRAIN", "no", at(23), at(22)),
    _market("C", "KXNFLGAME", "yes", at(20), at(20, 30)),
    _market("D", "KXNFLGAME", "no", at(20), at(20, 10)),
    _market("G", "KXMAKER", "yes", at(20), at(20, 1)),
    _market("H", "KXCULT", "yes", at(20), at(20, 2)),
    _market("E", "KXNFLGAME", "yes", datetime(2026, 10, 12, 20, tzinfo=UTC), None),
    _market("F", "KXNFLGAME", "", at(20), None),
]
SERIES = pl.DataFrame(
    {
        "ticker": ["KXNFLGAME", "KXRAIN", "KXMAKER", "KXCULT"],
        "category": ["Sports", "Climate and Weather", "Sports", "Culture"],
        "fee_type": ["quadratic", "quadratic", "quadratic_with_maker_fees", "quadratic"],
        "fee_multiplier_e6": [1_000_000, 1_000_000, 1_000_000, 1_000_000],
    }
)
# (ticker, time, taker side, yes price, count)
TRADES: list[tuple[str, datetime, str | None, int, int]] = [
    ("A", at(10), "yes", 500_000, 100),
    ("A", at(11), "no", 970_000, 1_000),
    ("A", at(12), "no", 990_000, 2_000),
    ("A", at(13), "yes", 960_000, 100),
    ("A", at(14), "no", 990_000, 3_000),
    ("A", at(15), "yes", 990_000, 500),
    ("A", at(16), "no", 980_000, 700),
    ("A", at(17), None, 990_000, 300),
    ("B", at(10), "yes", 30_000, 1_000),
    ("B", at(11), "yes", 10_000, 4_000),
    ("B", at(12), "no", 10_000, 800),
    ("C", at(10), "yes", 10_000, 1_500),
    ("C", at(12), "yes", 500_000, 100),
    ("C", at(18), "no", 990_000, 1_200),
    ("D", at(10), "no", 990_000, 2_500),
    ("G", at(10), "no", 990_000, 1_000),
    ("H", at(10), "yes", 500_000, 100),
    ("E", at(10), "no", 990_000, 9_000),
    ("F", at(10), "no", 990_000, 9_000),
]


def write_history(root: Path) -> Path:
    """The history world under ``root / "kalshi"``."""
    kalshi = root / "kalshi"
    historical = kalshi / "markets" / "historical"
    historical.mkdir(parents=True)
    pl.DataFrame(MARKETS).write_parquet(historical / "part-0.parquet")
    SERIES.write_parquet(kalshi / "series.parquet")
    folder = kalshi / "trades" / "date=2026-09-15"
    folder.mkdir(parents=True)
    pl.DataFrame(
        {
            "trade_id": [str(i) for i in range(len(TRADES))],
            "ticker": [t[0] for t in TRADES],
            "created_time": [t[1] for t in TRADES],
            "yes_price_e6": [t[3] for t in TRADES],
            "count_e2": [t[4] for t in TRADES],
            "taker_side": [t[2] for t in TRADES],
            "is_block_trade": [False] * len(TRADES),
        },
        schema_overrides={"yes_price_e6": pl.Int32, "count_e2": pl.Int64, "taker_side": pl.String},
    ).write_parquet(folder / "trades.parquet")
    return kalshi


def _frame(
    t: int, ticker: str, bid: int | None, bsz: int | None, ask: int | None, asz: int | None
) -> dict[str, Any]:
    return {
        "ts_ms": t,
        "recv_ms": t + 500,
        "ticker": ticker,
        "price_e6": None,
        "yes_bid_e6": bid,
        "yes_ask_e6": ask,
        "yes_bid_size_e2": bsz,
        "yes_ask_size_e2": asz,
        "volume_e2": None,
    }


def _trade(t: int, ticker: str, price: int, count: int, side: str) -> dict[str, Any]:
    return {
        "ts_ms": t,
        "ticker": ticker,
        "trade_id": f"{ticker}-{t}",
        "yes_price_e6": price,
        "count_e2": count,
        "taker_side": side,
        "is_block_trade": 0,
    }


FRAMES = [
    _frame(lm(0, 10), "L1", 970_000, 500, 990_000, 100),
    _frame(lm(1, 0), "L1", 980_000, 800, 990_000, 100),
    _frame(lm(1, 30), "L1", 990_000, 1_000, None, None),
    _frame(lm(2, 0), "L1", 990_000, 3_000, None, None),
    _frame(lm(2, 30), "L1", 980_000, 100, 990_000, 50),
    _frame(lm(2, 50), "L1", 980_000, 120, 990_000, 60),
    _frame(lm(3, 0), "L1", 990_000, 200, None, None),
    _frame(lm(3, 30), "L1", 990_000, 400, None, None),
    _frame(lm(0, 5), "L2", 10_000, 300, 20_000, 700),
    _frame(lm(1, 40), "L2", None, None, 10_000, 900),
    _frame(lm(2, 40), "L2", None, None, 10_000, 1_100),
    _frame(lm(0, 20), "L3", 500_000, 100, 520_000, 100),
    _frame(lm(2, 20), "L3", 510_000, 100, 530_000, 100),
    _frame(lm(1, 0), "L4", 990_000, 100, None, None),
    _frame(lm(2, 0), "L4", 990_000, 150, None, None),
    _frame(lm(0, 30), "L5", 990_000, 50, None, None),
]
LIVE_TRADES = [
    _trade(lm(1, 45), "L1", 990_000, 2_500, "no"),
    _trade(lm(2, 15), "L1", 990_000, 300, "yes"),
    _trade(lm(2, 45), "L1", 990_000, 600, "yes"),
    _trade(lm(3, 15), "L1", 990_000, 1_200, "no"),
    _trade(lm(2, 0), "L2", 10_000, 15_000, "yes"),
    _trade(lm(2, 10), "L2", 10_000, 100, "no"),
    _trade(lm(1, 0), "L3", 500_000, 100, "yes"),
    _trade(lm(1, 30), "L4", 990_000, 4_200, "no"),
    _trade(lm(0, 45), "L5", 990_000, 700, "no"),
]
LIVE_SERIES = {
    "L1": "KXNFLGAME",
    "L2": "KXRAIN",
    "L3": "KXNFLGAME",
    "L4": "KXMAKER",
    "L5": "KXNFLGAME",
}
CATEGORY = {"KXNFLGAME": "Sports", "KXRAIN": "Climate and Weather", "KXMAKER": "Sports"}


def write_live(root: Path) -> Path:
    """The live world under ``root / "live"``: an empty hour, then four hours of frames."""
    live = root / "live"
    frames = pl.DataFrame(FRAMES, schema=TICKER_SCHEMA)
    trades = pl.DataFrame(LIVE_TRADES, schema=TRADE_SCHEMA)
    for i in (-3, 0, 1, 2, 3):
        start = BASE_TIME + i * HOUR
        lo, hi = int(start.timestamp() * 1000), int((start + HOUR).timestamp() * 1000)
        for name, data in (("ticker", frames), ("trade", trades)):
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
                "open_ms": BASE - 86_400_000,
                "close_ms": BASE + 86_400_000,
            }
            for m, s in LIVE_SERIES.items()
        ],
        schema=MARKET_SCHEMA,
    ).write_parquet(live / "markets.parquet")
    return live
