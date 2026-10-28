"""A hand-built live world for S3.2's tests (where the book is slow), with the answers worked out
by hand. Prices are yes prices in millionths; sizes are hundredths of a contract; ``lt(h, m, s,
ms)`` is a moment after 2026-10-25 00:00 UTC. The hour files hold hours 0 and 1 (and an empty hour
before them); the span is 00:00 to 02:00.

**Markets and the pairs they form** (opens a day before, closes a day after, unless said):

* Crypto thresholds ``KXT-E1`` (greater): ``T1`` 100, ``T2`` 110, ``T3`` 120, ``T4`` 130:
  neighbours both ways, 6 ``thresholds`` pairs, sign +1.
* Crypto 15-minute thresholds ``KXT15M`` (greater_or_equal, one market an event): ``M1`` at 104,
  open 23:45 to 00:15; ``M2`` at 118, open 00:00 to 01:00. The stem ``KXT`` joins the two series:
  10 ``horizons`` pairs, sign +1: each ``T`` with ``M1`` while M1's event is the soonest to close
  (23:45 to 00:15) and with ``M2`` after it (00:15 to 01:00); ``M1`` with ``T1`` (nearest level)
  and ``M2`` with ``T3``.
* A sports game ``26OCT25AAABBB``: ``KXG`` winners ``AAA`` and ``BBB`` (structured: 2 ``outcomes``
  pairs, sign −1); ``KXS`` spreads ``AAA3`` (2.5), ``AAA6`` (5.5), ``BBB2`` (1.5) (``AAA3`` and
  ``AAA6`` are neighbours: 2 ``thresholds`` pairs); ``KXTOT`` total ``200`` (199.5); a four-part
  prop ``KXP-…-AAAJDOE1-20`` takes no part. ``game`` pairs, sign +1: ``AAA``→``AAA3`` (the central
  spread of AAA), ``AAA3``→``AAA``, ``AAA6``→``AAA``, ``BBB``→``BBB2``, ``BBB2``→``BBB``; the total
  has no partner series.
* Financial brackets ``KXB-E3`` (between): ``B0`` (below 50), ``B1`` 50 to 60, ``B2`` 60 to 70:
  4 ``brackets`` pairs, no sign.
* Weather: ``KXHIGHNY-26OCT25`` brackets ``W1`` 60 to 61 and ``W2`` 62 to 63 (rules naming
  CLINYC; 2 ``brackets`` pairs) and ``KXRAIN-26OCT25-NYC`` ``R1`` (rules naming station KNYC):
  one station-day, 4 ``weather`` pairs across the two series, no sign. ``W3`` (an event with no
  day) and ``W4`` (rules naming no station) join no weather group.
* ``KXC-E5-X`` (custom) and the four outcomes of ``KXO-E6`` take no part.

**Frames** (``bid/ask`` in cents, then the sizes) **and the moves they make** (a move is a mid
change of 2 cents or more between two-sided frames, bid and ask not opposed):

* ``T1``: 00:10 40/42 (100/100); 00:20 44/46 (**+4**); 01:00 50/52 (**+6**).
* ``T2``: 00:05 30/32 (100/200); 00:20:05 31/33 (150/250, +1: no move); 00:21 33/35 (**+2**);
  01:25 36/38 (**+3**).
* ``T3``: 00:00 20/22 (50/60); 00:30 20/26 (70/80, **+2**: the ask alone moved, not opposed);
  00:40 18/28 (mid unchanged); 00:50 10/42 (+3 but opposed: no move).
* ``T4``: 00:00 1/3 (10/10): a mid of 2 cents, extreme.
* ``M1``: 00:01 48/52 (1000/1000); 00:05 54/58 (**+6**). ``M2``: 00:16 48/52 (500/600);
  00:30:30 44/48 (**−4**).
* ``AAA``: 00:10 60/62 (10/20); 00:25 64/66 (**+4**). ``BBB``: 00:00 36/38 (300/400);
  00:25:00.500 32/34 (**−4**). ``AAA3``: 00:00 50/52 (100/100); 00:45 bid 50 alone (one-sided).
  ``AAA6``: 00:00 30/32 (40/40); 00:50 20/22 (**−10**). ``BBB2``: 00:00 70/72 (30/30).
* ``200`` (total): 00:00 50/52; 00:35 54/56 (**+4**, no partner). The prop and ``KXC-E5-X``
  likewise move once with no partner (00:36, 00:37). ``O1`` to ``O4``: one frame each.
* ``B0``: 00:00 5/7 (10/10). ``B1``: 00:00 20/22 (10/10); 00:40 24/26 (**+4**). ``B2``: 00:00
  30/32 (20/20); 00:40:20 27/29 (**−3**).
* ``W1``: 00:00 10/12 (5/5); 00:45 14/16 (**+4**). ``W2``: 00:00 40/42 (5/5). ``R1``: 00:00
  80/82 (9/9); 00:45 82/84 (**+2**, the same millisecond as W1's).

17 moves; 3 without a partner (the total, the prop, the custom market).

**Trades:** ``T2`` 00:20:02 a yes taker 300 at 32, 00:20:03 a no taker 100 at 30, 01:12 a yes
taker 500 at 35, 01:26 a yes taker 50 at 38; ``BBB`` 00:25:00.200 a no taker 150 at 36; ``T3``
00:35 a yes taker 200 at 26. Over the two hours that is 114 contracts a day for ``T2`` (tier "100
to 1,000"), 24 for ``T3``, 18 for ``BBB`` and 0 for the rest (tier "under 100").

**A ticker gap 01:30 to 01:31.** Events whose first ten minutes overlap it are excluded (T2's
move at 01:25, two events); events whose hour overlaps it lose their 1-hour response.

**The 26 event rows** (A's move → B), of which 21 are usable:

1. 00:05 M1→T1 (horizons): T1 has no frame yet: **unknown**.
2. 00:20 T1→T2 (thresholds, expected +): queue 31 before; 32 at 5 s, 34 at 60 s: responses 0,
   +1, +3, +3, +3 cents; first move at 5 s, agreeing; the yes taker at 00:20:02 took the stale ask
   (with: 1 trade, 300), the no taker at 00:20:03 the stale bid (against: 1, 100). Capacity: the
   200 shown at 32 times +3 cents is 60,000; the taker fee on 200 at 32 is 30,464; net 29,536.
3. 00:20 T1→M2 (horizons, +): M2 at 50 before, 46 from 00:30:30: responses 0 to 10 minutes, the
   1-hour one null (M2 closes 01:00); first move at 630 s, against. Capacity: 600 at 52, response
   0: fee 104,832, net −104,832.
4. 00:21 T2→T1 (thresholds, +): T1 at 45 before, 51 from 01:00: responses 0 until +6 at 1 hour;
   first move at 39 min, agreeing. Capacity: 100 at 46, fee 17,388, net −17,388.
5. 00:21 T2→T3 (thresholds, +): T3 at 21 before; 23 from 00:30; 26 from 00:50: responses 0, 0, 0,
   +2, +5; first move at 9 min, agreeing. Capacity: 60 at 22 times +2 is 12,000; fee 7,208; net
   4,792.
6. 00:21 T2→M2 (horizons, +): M2 at 50 before, 46 from 00:30:30: responses 0, 0, 0, −4, null;
   first move at 570 s, against; the same stale quote as row 3 (same first move), so it is **not
   counted** for capacity.
7. 00:25 AAA→BBB (outcomes, expected −): BBB at 37 before, 33 at 0.5 s: +4 cents at every
   horizon; first move at 500 ms, agreeing; the no taker at 00:25:00.200 hit the stale bid (with:
   1, 150). Capacity: 300 at 36 times +4 is 120,000; fee 48,384; net 71,616.
8. 00:25 AAA→AAA3 (game, +): AAA3 at 51 and never two-sided again: 0, censored. Capacity: 100 at
   52, fee 17,472, net −17,472.
9. 00:25:00.500 BBB→AAA (outcomes, expected + since both signs are negative): AAA at 65 before
   (its 00:25:00.000 frame), never moves: 0, censored. Capacity: 20 at 66, fee 3,142, net −3,142.
10. 00:25:00.500 BBB→BBB2 (game, expected −): 71, never moves. Capacity: 30 at 70, fee 4,410, net
    −4,410.
11. 00:30 T3→T2 (thresholds, +): T2 at 34 before, 37 from 01:25: 0 until +3 at 1 hour (the hour
    ends as the gap begins, so it stands); first move at 55 min, agreeing; the yes taker at 01:12
    took the stale ask (with: 1, 500). Capacity: 250 at 35, response 0: fee 39,813, net −39,813.
12. 00:30 T3→T4 (thresholds): T4's mid is 2 cents: **extreme**.
13. 00:30 T3→M2 (horizons, +): M2 at 50 before, 46 at 30 s: 0, 0, −4, −4, null; first move at
    30 s, against; the same stale quote as row 3: **not counted**.
14. 00:30:30 M2→T3 (horizons, expected −): T3 at 23 before (its 00:30 frame), 26 from 00:50:
    responses 0 to 10 minutes, the 1-hour one null (the gap); first move at 1,170 s, against; the
    yes taker at 00:35 lifted the stale ask, the wrong side (against: 1, 200). Capacity: 70 at
    20, response 0: fee 7,840, net −7,840.
15. 00:40 B1→B0 (brackets, no sign, A's direction +): B0 at 6 never moves; 1-hour null (gap).
16. 00:40 B1→B2 (brackets): B2 at 31 before, 28 at 20 s: 0, 0, −3, −3, null; first move at 20 s,
    against A's direction.
17. 00:40:20 B2→B1 (brackets): B1 at 25 (its 00:40 frame) never moves again.
18. 00:45 W1→W2 (brackets): W2 at 41 never moves.
19. 00:45 W1→R1 (weather): R1 at 81 before (its 00:00 frame), 83 at the same millisecond: +2 at
    every horizon but the hour (gap); first move at 0 ms, agreeing.
20. 00:45 R1→W1 (weather): W1 at 11 before, 15 at the same millisecond: +4; first move at 0 ms.
21. 00:45 R1→W2 (weather): 41, never moves.
22. 00:50 AAA6→AAA3 (thresholds): AAA3's last frame is one-sided: **one-sided**.
23. 00:50 AAA6→AAA (game, expected −): AAA at 65, never moves. Capacity: 10 at 64, fee 1,613,
    net −1,613.
24. 01:00 T1→T2 (thresholds, +): T2 at 34 before, 37 at 01:25: 0 to 10 minutes, the hour null
    (the gap, and the span's end); first move at 25 min, agreeing; the yes taker at 01:12 (with:
    1, 500). The same stale quote as row 11: **not counted**.
25. 01:25 T2→T1 and 26. T2→T3: **in the gap**.

By kind, usable events: thresholds 5, outcomes 2, game 3, horizons 4, brackets 4, weather 3.
Capacity a day (the span is two hours, so twelve times the sums): thresholds gross 864,000, fee
1,138,476, net −274,476 on 4 quotes counted (48 a day); outcomes net +821,688; game −281,940;
horizons −1,352,064. Nothing clears $25 a day.
"""

from datetime import timedelta
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
from surveyhelpers import BASE, BASE_TIME, HOUR, _frame, _trade

DAY_MS = 86_400_000
GAME = "26OCT25AAABBB"
NYC_RULES = "The high temperature at Central Park (CLINYC) on the day, as reported."
KNYC_RULES = "Rain measured at KNYC on the day, as reported."


def lt(hour: int, minute: int = 0, second: int = 0, millis: int = 0) -> int:
    return BASE + ((hour * 60 + minute) * 60 + second) * 1_000 + millis


def lt_time(hour: int, minute: int = 0, second: int = 0) -> Any:
    return BASE_TIME + timedelta(hours=hour, minutes=minute, seconds=second)


def _m(
    ticker: str,
    series: str,
    event: str,
    category: str,
    strike: str | None,
    floor: str | None = None,
    cap: str | None = None,
    *,
    rules: str | None = None,
    open_ms: int = BASE - DAY_MS,
    close_ms: int = BASE + DAY_MS,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "series_ticker": series,
        "event_ticker": event,
        "category": category,
        "open_ms": open_ms,
        "close_ms": close_ms,
        "strike_type": strike,
        "floor_strike": floor,
        "cap_strike": cap,
        "weather_rules": rules,
    }


G = f"KXG-{GAME}"
S = f"KXS-{GAME}"
MARKETS = [
    _m("T1", "KXT", "KXT-E1", "Crypto", "greater", "100"),
    _m("T2", "KXT", "KXT-E1", "Crypto", "greater", "110"),
    _m("T3", "KXT", "KXT-E1", "Crypto", "greater", "120"),
    _m("T4", "KXT", "KXT-E1", "Crypto", "greater", "130"),
    _m(
        "M1",
        "KXT15M",
        "KXT15M-26OCT250000",
        "Crypto",
        "greater_or_equal",
        "104",
        open_ms=lt(0) - 15 * 60_000,
        close_ms=lt(0, 15),
    ),  # fmt: skip
    _m(
        "M2",
        "KXT15M",
        "KXT15M-26OCT250015",
        "Crypto",
        "greater_or_equal",
        "118",
        open_ms=lt(0),
        close_ms=lt(1),
    ),  # fmt: skip
    _m(f"{G}-AAA", "KXG", G, "Sports", "structured"),
    _m(f"{G}-BBB", "KXG", G, "Sports", "structured"),
    _m(f"{S}-AAA3", "KXS", S, "Sports", "greater", "2.5"),
    _m(f"{S}-AAA6", "KXS", S, "Sports", "greater", "5.5"),
    _m(f"{S}-BBB2", "KXS", S, "Sports", "greater", "1.5"),
    _m(f"KXTOT-{GAME}-200", "KXTOT", f"KXTOT-{GAME}", "Sports", "greater", "199.5"),
    _m(f"KXP-{GAME}-AAAJDOE1-20", "KXP", f"KXP-{GAME}-AAAJDOE1", "Sports", "greater", "19.5"),
    _m("B0", "KXB", "KXB-E3", "Financials", "between", None, "50"),
    _m("B1", "KXB", "KXB-E3", "Financials", "between", "50", "60"),
    _m("B2", "KXB", "KXB-E3", "Financials", "between", "60", "70"),
    _m(
        "W1",
        "KXHIGHNY",
        "KXHIGHNY-26OCT25",
        "Climate and Weather",
        "between",
        "60",
        "61",
        rules=NYC_RULES,
    ),  # fmt: skip
    _m(
        "W2",
        "KXHIGHNY",
        "KXHIGHNY-26OCT25",
        "Climate and Weather",
        "between",
        "62",
        "63",
        rules=NYC_RULES,
    ),  # fmt: skip
    _m(
        "W3",
        "KXHIGHNY",
        "KXHIGHNY-BAD",
        "Climate and Weather",
        "between",
        "70",
        "71",
        rules=NYC_RULES,
    ),  # fmt: skip
    _m(
        "W4",
        "KXHIGHNY",
        "KXHIGHNY-26OCT26",
        "Climate and Weather",
        "between",
        "60",
        "61",
        rules="no station named here",
    ),  # fmt: skip
    _m("R1", "KXRAIN", "KXRAIN-26OCT25", "Climate and Weather", "greater", "0", rules=KNYC_RULES),
    _m("KXC-E5-X", "KXC", "KXC-E5", "Mentions", "custom"),
    *[_m(f"O{i}", "KXO", "KXO-E6", "Sports", "structured") for i in range(1, 5)],
]
SERIES = {m["series_ticker"] for m in MARKETS}
# every series charges the plain quadratic fee at the standard multiplier
FEES = pl.DataFrame(
    {
        "series_ticker": sorted(SERIES),
        "fee_type": ["quadratic"] * len(SERIES),
        "multiplier_e6": [1_000_000] * len(SERIES),
    }
)


def c(cents: float) -> int:
    return int(round(cents * 10_000))


def _f(
    t: int, ticker: str, bid: float | None, ask: float | None, bsz: int, asz: int | None = None
) -> dict[str, Any]:
    asz = bsz if asz is None else asz
    return _frame(
        t,
        ticker,
        None if bid is None else c(bid),
        None if bid is None else bsz,
        None if ask is None else c(ask),
        None if ask is None else asz,
    )


FRAMES = [
    _f(lt(0, 10), "T1", 40, 42, 100),
    _f(lt(0, 20), "T1", 44, 46, 100),
    _f(lt(1, 0), "T1", 50, 52, 100),
    _f(lt(0, 5), "T2", 30, 32, 100, 200),
    _f(lt(0, 20, 5), "T2", 31, 33, 150, 250),
    _f(lt(0, 21), "T2", 33, 35, 150, 250),
    _f(lt(1, 25), "T2", 36, 38, 150, 250),
    _f(lt(0, 0), "T3", 20, 22, 50, 60),
    _f(lt(0, 30), "T3", 20, 26, 70, 80),
    _f(lt(0, 40), "T3", 18, 28, 70, 80),
    _f(lt(0, 50), "T3", 10, 42, 70, 80),
    _f(lt(0, 0), "T4", 1, 3, 10),
    _f(lt(0, 1), "M1", 48, 52, 1_000),
    _f(lt(0, 5), "M1", 54, 58, 1_000),
    _f(lt(0, 16), "M2", 48, 52, 500, 600),
    _f(lt(0, 30, 30), "M2", 44, 48, 500, 600),
    _f(lt(0, 10), f"{G}-AAA", 60, 62, 10, 20),
    _f(lt(0, 25), f"{G}-AAA", 64, 66, 10, 20),
    _f(lt(0, 0), f"{G}-BBB", 36, 38, 300, 400),
    _f(lt(0, 25, 0, 500), f"{G}-BBB", 32, 34, 300, 400),
    _f(lt(0, 0), f"{S}-AAA3", 50, 52, 100),
    _f(lt(0, 45), f"{S}-AAA3", 50, None, 100),
    _f(lt(0, 0), f"{S}-AAA6", 30, 32, 40),
    _f(lt(0, 50), f"{S}-AAA6", 20, 22, 40),
    _f(lt(0, 0), f"{S}-BBB2", 70, 72, 30),
    _f(lt(0, 0), f"KXTOT-{GAME}-200", 50, 52, 10),
    _f(lt(0, 35), f"KXTOT-{GAME}-200", 54, 56, 10),
    _f(lt(0, 0), f"KXP-{GAME}-AAAJDOE1-20", 45, 47, 10),
    _f(lt(0, 36), f"KXP-{GAME}-AAAJDOE1-20", 49, 51, 10),
    _f(lt(0, 0), "KXC-E5-X", 50, 52, 10),
    _f(lt(0, 37), "KXC-E5-X", 54, 56, 10),
    *[_f(lt(0, 0), f"O{i}", 20, 22, 10) for i in range(1, 5)],
    _f(lt(0, 0), "B0", 5, 7, 10),
    _f(lt(0, 0), "B1", 20, 22, 10),
    _f(lt(0, 40), "B1", 24, 26, 10),
    _f(lt(0, 0), "B2", 30, 32, 20),
    _f(lt(0, 40, 20), "B2", 27, 29, 20),
    _f(lt(0, 0), "W1", 10, 12, 5),
    _f(lt(0, 45), "W1", 14, 16, 5),
    _f(lt(0, 0), "W2", 40, 42, 5),
    _f(lt(0, 0), "R1", 80, 82, 9),
    _f(lt(0, 45), "R1", 82, 84, 9),
]
TRADES = [
    _trade(lt(0, 20, 2), "T2", c(32), 300, "yes"),
    _trade(lt(0, 20, 3), "T2", c(30), 100, "no"),
    _trade(lt(1, 12), "T2", c(35), 500, "yes"),
    _trade(lt(1, 26), "T2", c(38), 50, "yes"),
    _trade(lt(0, 25, 0, 200), f"{G}-BBB", c(36), 150, "no"),
    _trade(lt(0, 35), "T3", c(26), 200, "yes"),
]
GAPS = [{"kind": "ticker", "start_ms": lt(1, 30), "end_ms": lt(1, 31), "reason": "lost"}]
ACTIVE = sorted({f["ticker"] for f in FRAMES})


def write_lag_world(root: Path) -> Path:
    """The world under ``root / "live"``: an empty hour, then hours 0 and 1."""
    live = root / "live"
    frames = pl.DataFrame(FRAMES, schema=TICKER_SCHEMA)
    trades = pl.DataFrame(TRADES, schema=TRADE_SCHEMA)
    for i in (-1, 0, 1):
        start = BASE_TIME + i * HOUR
        lo, hi = int(start.timestamp() * 1000), int((start + HOUR).timestamp() * 1000)
        for name, data in (("ticker", frames), ("trade", trades)):
            path = live / name / f"{hour_name(start)}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            data.filter((pl.col("ts_ms") >= lo) & (pl.col("ts_ms") < hi)).write_parquet(path)
    pl.DataFrame(MARKETS, schema=MARKET_SCHEMA).write_parquet(live / "markets.parquet")
    pl.DataFrame(GAPS, schema=GAP_SCHEMA).write_parquet(live / "gaps.parquet")
    return live


def write_lag_kalshi(root: Path) -> Path:
    """A history folder holding only the series' fee terms, which ``survey lag`` reads."""
    kalshi = root / "kalshi"
    kalshi.mkdir(parents=True, exist_ok=True)
    FEES.rename({"series_ticker": "ticker", "multiplier_e6": "fee_multiplier_e6"}).write_parquet(
        kalshi / "series.parquet"
    )
    return kalshi
