"""A synthetic Kalshi history with a known answer, written in the downloader's own file layout.

The study library is tested on this because the truth is known. A market's fair price ``q`` is
drawn, and the chance that it settles yes is ``q`` plus a planted bias: longshots (low ``q``) are
overpriced by up to ``bias`` and favourites (high ``q``) underpriced by up to ``bias``. So at a
price of ``q`` a taker who buys the other side has a planted gross edge of ``|bias(q)|`` per
contract, and the maker facing a taker who buys the biased side has the same edge.

* ``cluster_sd`` shifts the whole event's odds on the logit scale, so the markets of one event are
  correlated and only the events are independent.
* ``drift`` pulls later trade prices toward the outcome, which is how informed flow looks: a price
  that trades through a resting order's level then signals that the order was on the wrong side.
  With ``drift`` above zero the pessimistic maker fill rule loses what the optimistic one earns.
* Series 0 is "sports" with maker fees (a quarter share) and series 1 is "politics" without.
"""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
from numpy.typing import NDArray

from sparq_analytics.history.datadir import DataDir, write_table_atomic
from sparq_analytics.history.schemas import (
    FEE_CHANGES_SCHEMA,
    MARKETS_SCHEMA,
    SERIES_SCHEMA,
    TRADES_SCHEMA,
)

START = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
SERIES = (
    ("KXSYN0", "sports", "quadratic_with_maker_fees"),
    ("KXSYN1", "politics", "quadratic"),
)
CENT_E6 = 10_000


@dataclass(frozen=True, slots=True)
class World:
    events: int = 1_500
    markets_per_event: int = 2
    trades_per_market: int = 12
    bias: float = 0.0
    cluster_sd: float = 0.0
    drift: float = 0.0
    noise: float = 0.02
    event_hours: float = 1.0  # event i closes i x event_hours after the start
    duration_hours: float = 24.0
    long_fraction: float = 0.0  # events that stay open 60 days, for the embargo
    seed: int = 7


def _planted_shift(q: NDArray[np.float64], bias: float) -> NDArray[np.float64]:
    low = -(0.25 - q).clip(min=0) / 0.25
    high = (q - 0.75).clip(min=0) / 0.25
    return bias * (low + high)


def _table(schema: pa.Schema, columns: dict[str, list[object]], rows: int) -> pa.Table:
    full = {name: columns.get(name) or [None] * rows for name in schema.names}
    return pa.table(full, schema=schema)


def write_world(world: World, root: Path) -> dict[str, int]:
    """Write ``world`` under ``root`` as the downloader would; returns counts for the tests."""
    rng = np.random.default_rng(world.seed)
    directory = DataDir(root)
    events = world.events
    close_s = np.arange(events) * world.event_hours * 3600.0 + 48 * 3600.0
    duration_s = np.where(
        rng.random(events) < world.long_fraction, 60 * 86400.0, world.duration_hours * 3600.0
    )
    shock = rng.normal(0.0, world.cluster_sd, events)
    series_of = rng.integers(0, len(SERIES), events)

    market_rows: dict[str, list[object]] = {k: [] for k in MARKETS_SCHEMA.names}
    trade_rows: dict[str, list[object]] = {k: [] for k in TRADES_SCHEMA.names}
    yes_markets = 0
    for e in range(events):
        series_ticker = SERIES[series_of[e]][0]
        event_ticker = f"{series_ticker}-E{e:05d}"
        open_time = START + dt.timedelta(seconds=float(close_s[e] - duration_s[e]))
        close_time = START + dt.timedelta(seconds=float(close_s[e]))
        for j in range(world.markets_per_event):
            ticker = f"{event_ticker}-M{j}"
            q = np.array([rng.integers(3, 98) / 100.0])
            true = (q + _planted_shift(q, world.bias)).clip(0.01, 0.99)
            logit = np.log(true / (1 - true)) + shock[e]
            outcome = int(rng.random() < 1 / (1 + np.exp(-logit[0])))
            yes_markets += outcome
            market_rows["ticker"].append(ticker)
            market_rows["event_ticker"].append(event_ticker)
            market_rows["series_ticker"].append(series_ticker)
            market_rows["source"].append("historical")
            market_rows["label"].append("yes" if outcome else "no")
            market_rows["status"].append("finalized")
            market_rows["result"].append("yes" if outcome else "no")
            market_rows["market_type"].append("binary")
            market_rows["volume_e2"].append(world.trades_per_market * 100)  # nominal, above zero
            market_rows["open_time"].append(open_time)
            market_rows["created_time"].append(open_time - dt.timedelta(hours=1))
            market_rows["close_time"].append(close_time)
            market_rows["expected_expiration_time"].append(close_time)
            n = world.trades_per_market
            offsets = np.sort(rng.choice(int(duration_s[e]) - 1, size=n, replace=False)) + 1
            progress = np.arange(1, n + 1) / n
            path = q[0] + world.drift * (outcome - q[0]) * progress
            path = path + rng.normal(0.0, world.noise, n)
            cents = np.rint(path.clip(0.01, 0.99) * 100).astype(int)
            for k in range(n):
                when = START + dt.timedelta(seconds=float(close_s[e] - duration_s[e] + offsets[k]))
                trade_rows["trade_id"].append(f"{ticker}-{k}")
                trade_rows["ticker"].append(ticker)
                trade_rows["created_time"].append(when)
                trade_rows["yes_price_e6"].append(int(cents[k]) * CENT_E6)
                trade_rows["count_e2"].append(int(rng.integers(1, 21)) * 100)
                trade_rows["taker_side"].append("yes" if rng.random() < 0.5 else "no")
                trade_rows["is_block_trade"].append(False)

    n_markets = len(market_rows["ticker"])
    write_table_atomic(
        _table(MARKETS_SCHEMA, market_rows, n_markets), directory.historical_markets_file(0)
    )
    trades = _table(TRADES_SCHEMA, trade_rows, len(trade_rows["ticker"]))
    days = np.array([t.date().isoformat() for t in trade_rows["created_time"]])  # type: ignore[attr-defined]
    for day in sorted(set(days.tolist())):
        mask = pa.array(days == day)
        write_table_atomic(trades.filter(mask), directory.trades_file(day))
    series_columns: dict[str, list[object]] = {
        "ticker": [s[0] for s in SERIES],
        "title": [s[1] for s in SERIES],
        "category": [s[1] for s in SERIES],
        "frequency": ["custom"] * len(SERIES),
        "tags": [[] for _ in SERIES],
        "exchange_index": [0] * len(SERIES),
        "fee_type": [s[2] for s in SERIES],
        "fee_multiplier_e6": [1_000_000] * len(SERIES),
        "fetched_at": [START] * len(SERIES),
    }
    write_table_atomic(
        _table(SERIES_SCHEMA, series_columns, len(SERIES)), directory.root / "series.parquet"
    )
    write_table_atomic(_table(FEE_CHANGES_SCHEMA, {}, 0), directory.root / "fee_changes.parquet")
    return {"markets": n_markets, "trades": len(trade_rows["ticker"]), "yes": yes_markets}
