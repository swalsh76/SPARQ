"""A hand-built live world for S4.1's tests (the paper quoter), with the answers worked out by
hand. Prices are yes prices in millionths; sizes are hundredths of a contract; ``lm(h, m)`` is a
moment after 2026-10-25 00:00 UTC. The span is 00:00 to 02:00 (two hours; ``days=3`` adds hours to
2026-10-27 01:00 with a dropped day in between, see the end).

**Markets** (open a day before, close three days after, unless said; all in the lowest tiers but
P3):

* ``P1`` (``KXP1``, Sports, maker fees): 00:00 bid 30 ask 60 (**uncontested**, spread 30); 00:30
  bid 40 ask 60 (spread 20: **contested**, no quote); 01:00 bid 30 ask 70. 103 contracts in two
  hours: tier "100 to 1,000". Paper quotes at 00:00: k = 5 bid 35 ask 55; k = 10 bid 40 ask 50.
  Trades: 00:10 a yes taker, 2 contracts at 58, fills both paper asks (2 contracts each); 00:20 a
  no taker, 100 contracts at 33, fills both paper bids (10 contracts for S = 10, 50 for S = 50);
  00:40 a yes taker at 59 finds no paper quote standing (the 00:30 frame is contested).
* ``P2`` (``KXP2``, Crypto, plain quadratic): 00:00 bid 40, the ask side empty (**one-sided**):
  paper bids 45 and 50, paper asks at 60 (20 cents from the standing bid). 00:15 a yes taker, 1
  contract at 70, fills both asks; 00:50 another at 65 finds the same quotes already filled (one
  fill a quote), and so does one at 01:10, in the next hour, on the carried frame. No two-sided
  frame ever: every mark is null.
* ``P3`` (``KXP3``, Sports): 20,000 contracts at 00:05: the busiest tier, out of scope.
* ``P4`` (``KXP4``, Crypto): bid 2 alone: the certainty regime, out of scope.
* ``P5`` (``KXP5``, Sports, plain quadratic): 00:00 bid 20 ask 50 (uncontested), 00:41 bid 20 ask
  60 (uncontested; paper quotes again, for the four minutes to the close), **closes at 00:45**.
  Paper bids at 00:00: 25 and 30. 00:30 a no taker, 1 contract at 22, fills both bids; 00:50 a yes
  taker at 49 comes after the close, when no quote stands.
* ``P6`` (``KXP6``, Politics): 00:00 bid 20 ask 60, uncontested, never traded: quoted (two hours
  less the outage's minute) and never filled.

**A ticker outage 01:30 to 01:31** takes a minute off the quotes standing then.

**The fills** (8 rows: P1 two asks and two bids, P2 two asks, P5 two bids) and their marks:

* P1 asks (00:10): the mid 10 minutes later (00:20) is 45 (the 00:00 frame), an hour later (01:10)
  50 (the 01:00 frame); the last two-sided mid after the fill is 50 and the market is still open
  (**censored**). k = 5 at 55 on 2 contracts: +$0.20, +$0.10, +$0.10; maker fee $0.008663 (7% of a
  quarter of 2 × 0.55 × 0.45, rounded up). k = 10 at 50: +$0.10, $0, $0; fee $0.008750.
* P1 bids (00:20): the mid at 00:30 is 50 (that frame), at 01:20 50, the last 50. k = 5 at 35: on 10
  contracts +$1.50 at every mark, fee $0.039813; on 50 contracts +$7.50, fee $0.199063. k = 10 at
  40: +$1.00 (10) and +$5.00 (50), fees $0.042 and $0.21.
* P2 asks (00:15): marks null; fee 0 (plain quadratic); traded again (00:50).
* P5 bids (00:30): the mid at 00:40 is 35 (the 00:00 frame); at 01:30 the market is closed (null);
  the last two-sided mid after the fill is 40 (the 00:41 frame), not censored; a quarter of an hour
  to close. k = 5 at 25 on 1 contract: +$0.10, null, +$0.15; k = 10 at 30: +$0.05, null, +$0.10;
  fee 0.

**Quote time** (the figures a day are twelve times the two hours): uncontested, each k: P1 30
minutes in the first hour and 59 in the second (the outage's minute off), P5 41 and 4 minutes, P6
60 and 59: 253 minutes and 6 quote rows; one-sided, each k: P2 60 and 59 minutes, 2 quote rows.

**By rule "uncontested | k = 5 | S = 10":** 3 fills on 2 markets, 13 contracts; gross +$1.80 at 10
minutes, +$1.60 at 1 hour (P5's mark null, 2 marked), +$1.75 at the close; fees $0.048476; two
fills of three censored; none never traded again. The Politics cell has 2 quote rows, 119 minutes
and no fill.

**With ``days=3``:** the hour files run to 2026-10-27 01:00, with a ticker outage 2026-10-26
05:00 to 06:00 (over ten minutes: the day is dropped; 25 hours kept). At 23:30 on 2026-10-25 P1
quotes bid 30 ask 60 and a no taker takes 1 contract at 33 at 23:40 (fills both paper bids): its
10-minute mark is 45, its 1-hour mark null, because the next hour belongs to the dropped day
(whose 00:10 frame, mid 85, is never read). On 2026-10-27 at 00:00 P1 quotes bid 30 ask 70 and a
yes taker takes 1 contract at 68 at 00:10 (fills both paper asks, 65 and 60); a yes taker at 70 on
P2 at 00:05 finds no quote, because the book is not carried across the dropped day.
"""

from datetime import UTC, datetime
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

DAY_MS = 86_400_000
D27 = int(datetime(2026, 10, 27, tzinfo=UTC).timestamp() * 1000)
FRAMES = [
    _frame(lm(0, 0), "P1", 300_000, 10, 600_000, 10),
    _frame(lm(0, 30), "P1", 400_000, 10, 600_000, 10),
    _frame(lm(1, 0), "P1", 300_000, 10, 700_000, 10),
    _frame(lm(23, 30), "P1", 300_000, 10, 600_000, 10),
    _frame(lm(24, 10), "P1", 800_000, 10, 900_000, 10),  # in the dropped day: never read
    _frame(D27, "P1", 300_000, 10, 700_000, 10),
    _frame(lm(0, 0), "P2", 400_000, 10, 1_000_000, 0),
    _frame(lm(0, 0), "P3", 200_000, 10, 600_000, 10),
    _frame(lm(0, 0), "P4", 20_000, 10, 1_000_000, 0),
    _frame(lm(0, 0), "P5", 200_000, 10, 500_000, 10),
    _frame(lm(0, 41), "P5", 200_000, 10, 600_000, 10),
    _frame(lm(0, 0), "P6", 200_000, 10, 600_000, 10),
]
TRADES = [
    _trade(lm(0, 10), "P1", 580_000, 200, "yes"),
    _trade(lm(0, 20), "P1", 330_000, 10_000, "no"),
    _trade(lm(0, 40), "P1", 590_000, 100, "yes"),
    _trade(lm(23, 40), "P1", 330_000, 100, "no"),
    _trade(D27 + 10 * 60_000, "P1", 680_000, 100, "yes"),
    _trade(lm(0, 15), "P2", 700_000, 100, "yes"),
    _trade(lm(0, 50), "P2", 650_000, 100, "yes"),
    _trade(lm(1, 10), "P2", 700_000, 100, "yes"),
    _trade(D27 + 5 * 60_000, "P2", 700_000, 100, "yes"),
    _trade(lm(0, 5), "P3", 400_000, 2_000_000, "yes"),
    _trade(lm(0, 5), "P4", 500_000, 100, "yes"),
    _trade(lm(0, 30), "P5", 220_000, 100, "no"),
    _trade(lm(0, 50), "P5", 490_000, 100, "yes"),
]
SERIES = {"P1": "KXP1", "P2": "KXP2", "P3": "KXP3", "P4": "KXP4", "P5": "KXP5", "P6": "KXP6"}
CATEGORY = {
    "KXP1": "Sports",
    "KXP2": "Crypto",
    "KXP3": "Sports",
    "KXP4": "Crypto",
    "KXP5": "Sports",
    "KXP6": "Politics",
}
FEES = pl.DataFrame(
    {
        "series_ticker": list(CATEGORY),
        "fee_type": ["quadratic_with_maker_fees"] + ["quadratic"] * 5,
        "multiplier_e6": [1_000_000] * 6,
    }
)


def write_paper_live(root: Path, days: int = 1, gaps: list[dict[str, Any]] | None = None) -> Path:
    """The live world under ``root / "live"``: an empty hour, then the hours of the span."""
    live = root / "live"
    frames = pl.DataFrame(FRAMES, schema=TICKER_SCHEMA)
    tape = pl.DataFrame(TRADES, schema=TRADE_SCHEMA)
    hours = [BASE_TIME + i * HOUR for i in range(-1, 2 if days == 1 else 49)]
    for start in hours:
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
                "open_ms": lm(0, 0) - DAY_MS,
                "close_ms": lm(0, 45) if m == "P5" else lm(0, 0) + 3 * DAY_MS,
                "strike_type": None,
                "floor_strike": None,
                "cap_strike": None,
                "weather_rules": None,
            }
            for m, s in SERIES.items()
        ],
        schema=MARKET_SCHEMA,
    ).write_parquet(live / "markets.parquet")
    if gaps is None:
        gaps = [{"kind": "ticker", "start_ms": lm(1, 30), "end_ms": lm(1, 31), "reason": "lost"}]
        if days == 3:
            gaps.append(
                {"kind": "ticker", "start_ms": lm(29, 0), "end_ms": lm(30, 0), "reason": "lost"}
            )
    pl.DataFrame(gaps, schema=GAP_SCHEMA).write_parquet(live / "gaps.parquet")
    return live
