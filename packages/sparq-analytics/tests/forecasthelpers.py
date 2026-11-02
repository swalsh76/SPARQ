"""A built world for S5.2's tests (the model's probability against the book's price).

**History:** 240 markets ``M000`` to ``M239`` in 120 events of two (event ``i`` holds markets ``2i``
and ``2i + 1``), the events in turn Sports (``KXS``), Financials (``KXF``) and Economics
(``KXE``). Event ``i`` closes on 2026-09-01 plus ``i // 4`` days at 18:00 UTC, so eight markets
close each day from 09-01 to 09-30. Each opened three days before its close and traded once, one
contract, 24 hours before it, at a price drawn from 15 to 85 cents; it settled yes with that
chance (the price is calibrated). Beside them:

* ``TAIL`` (95 cents) and ``LOW`` (5 cents): outside the 10-to-90 band;
* ``THICK`` (50 cents, 5,000 contracts over two days, 2,500 a day): not thin;
* ``LATE``: its only trade six hours before its close, so no day-before price;
* ``OPEN``: never settled;
* ``NOCAT`` (40 cents, closing 09-30, series ``KXN``, which the series table lacks): category
  "unknown", kept.

So the population is 241 markets in 121 events. With the default caps all are drawn; the split day
is that of the 81st by closing time, 2026-09-11: 80 markets in the fit part, 161 in the test part.

**The model** (``model_transport``): ``informative`` answers 0.7 where the market settled yes and
0.3 where it settled no, but the other way round for a fifth of the markets (by a hash of the
ticker), so it knows much and not all; ``noise`` answers a number drawn from the ticker alone;
either way ``M000`` answers "I cannot tell" (unparsed).
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import polars as pl

START = datetime(2026, 9, 1, 18, tzinfo=UTC)
CATEGORIES = (("KXS", "Sports"), ("KXF", "Financials"), ("KXE", "Economics"))
SERIES = pl.DataFrame(
    {
        "ticker": [s for s, _ in CATEGORIES],
        "title": ["Sports", "Finance", "Economy"],
        "category": [c for _, c in CATEGORIES],
        "fee_type": ["quadratic"] * 3,
        "fee_multiplier_e6": [1_000_000] * 3,
    }
)


def _market(
    ticker: str, series: str, event: str, result: str, close: datetime, days_open: int = 3
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "series_ticker": series,
        "event_ticker": event,
        "result": result,
        "open_time": close - timedelta(days=days_open),
        "close_time": close,
        "settlement_ts": close + timedelta(minutes=5),
        "title": f"Market {ticker}",
        "yes_sub_title": f"Yes {ticker}",
        "no_sub_title": f"No {ticker}",
        "rules_primary": f"Rules for {ticker}.",
        "rules_secondary": "A second rule." if ticker == "M001" else "",
    }


def _world() -> tuple[list[dict[str, Any]], list[tuple[str, datetime, int, int]]]:
    rng = np.random.default_rng(7)
    markets, trades = [], []
    for i in range(120):
        series = CATEGORIES[i % 3][0]
        close = START + timedelta(days=i // 4)
        for ticker in (f"M{2 * i:03d}", f"M{2 * i + 1:03d}"):
            price = int(rng.integers(15, 86)) * 10_000
            result = "yes" if rng.random() < price / 1_000_000 else "no"
            markets.append(_market(ticker, series, f"{series}-E{i:03d}", result, close))
            trades.append((ticker, close - timedelta(hours=24), price, 100))
    late = START + timedelta(days=29)
    for ticker, series, price, count, result, hours, days_open in (
        ("TAIL", "KXS", 950_000, 100, "yes", 24, 3),
        ("LOW", "KXS", 50_000, 100, "no", 24, 3),
        ("THICK", "KXS", 500_000, 500_000, "yes", 24, 2),
        ("LATE", "KXS", 500_000, 100, "yes", 6, 3),
        ("OPEN", "KXS", 500_000, 100, "", 24, 3),
        ("NOCAT", "KXN", 400_000, 100, "no", 24, 3),
    ):
        markets.append(_market(ticker, series, f"{series}-{ticker}", result, late, days_open))
        trades.append((ticker, late - timedelta(hours=hours), price, count))
    return markets, trades


MARKETS, TRADES = _world()
RESULTS = {m["ticker"]: m["result"] for m in MARKETS}


def write_forecast_kalshi(root: Path) -> Path:
    """The history world under ``root / "kalshi"``."""
    kalshi = root / "kalshi"
    historical = kalshi / "markets" / "historical"
    historical.mkdir(parents=True)
    pl.DataFrame(MARKETS).write_parquet(historical / "part-0.parquet")
    SERIES.write_parquet(kalshi / "series.parquet")
    days = sorted({t[1].date() for t in TRADES})
    for day in days:
        rows = [t for t in TRADES if t[1].date() == day]
        folder = kalshi / "trades" / f"date={day}"
        folder.mkdir(parents=True)
        pl.DataFrame(
            {
                "trade_id": [f"{t[0]}-{n}" for n, t in enumerate(rows)],
                "ticker": [t[0] for t in rows],
                "created_time": [t[1] for t in rows],
                "yes_price_e6": [t[2] for t in rows],
                "count_e2": [t[3] for t in rows],
                "taker_side": ["yes"] * len(rows),
                "is_block_trade": [False] * len(rows),
            },
            schema_overrides={
                "yes_price_e6": pl.Int32,
                "count_e2": pl.Int64,
                "created_time": pl.Datetime("us", "UTC"),
            },
        ).write_parquet(folder / "trades.parquet")
    return kalshi


def _draw(ticker: str) -> float:
    """A number from 0 to 1 fixed by the ticker."""
    return int(hashlib.sha256(ticker.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def answer_for(ticker: str, mode: str) -> str:
    if ticker == "M000":
        return "I cannot tell"
    if mode == "informative":
        right = (RESULTS[ticker] == "yes") != (_draw(ticker) < 0.2)
        p = 0.7 if right else 0.3
    else:
        p = 0.1 + 0.8 * _draw(ticker)
    return json.dumps({"probability": round(p, 4)})


def model_transport(mode: str, calls: list[str] | None = None) -> httpx.MockTransport:
    """A fake model node answering each market by its title (see the module's docstring); the
    prompts are appended to ``calls``."""

    def answer(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["messages"][0]["content"]
        if calls is not None:
            calls.append(prompt)
        ticker = prompt.split("Title: Market ", 1)[1].split("\n", 1)[0]
        body = {
            "choices": [
                {"message": {"content": answer_for(ticker, mode)}, "finish_reason": "stop"}
            ],
            "usage": {"completion_tokens": 30},
        }
        return httpx.Response(200, json=body)

    return httpx.MockTransport(answer)
