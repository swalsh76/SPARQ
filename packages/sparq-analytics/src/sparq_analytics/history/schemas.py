"""Arrow schemas for the downloaded history, and the conversions from the typed models.

Prices and counts stay the exact fixed-point integers of the models (dollars in millionths,
contracts in hundredths); times are UTC microsecond timestamps. A trade's no-price is not
stored, because yes plus no is always one dollar (the model checks it).
"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import pyarrow as pa
from sparq_core.models import Candlestick, Market, Series, SeriesFeeChange, Trade

COMBO_PREFIX = "KXMVE"  # combo (multivariate) markets: skipped everywhere (CLAUDE.md)
SETTLED_STATUSES = frozenset({"finalized", "settled", "determined"})

TIMESTAMP = pa.timestamp("us", tz="UTC")

TRADES_SCHEMA = pa.schema(
    [
        ("trade_id", pa.string()),
        ("ticker", pa.string()),
        ("created_time", TIMESTAMP),
        ("yes_price_e6", pa.int32()),
        ("count_e2", pa.int64()),
        ("taker_side", pa.string()),
        ("is_block_trade", pa.bool_()),
    ]
)

_MARKET_TEXT = (
    "ticker", "event_ticker", "series_ticker", "source", "label", "status", "result", "market_type",
    "title", "yes_sub_title", "no_sub_title", "rules_primary", "rules_secondary",
    "early_close_condition", "strike_type", "floor_strike", "cap_strike", "price_level_structure",
)  # fmt: skip
_MARKET_TIME = (
    "created_time", "open_time", "close_time", "expected_expiration_time", "expiration_time",
    "latest_expiration_time", "occurrence_datetime", "settlement_ts", "updated_time",
)  # fmt: skip
_MARKET_INT = (
    "settlement_value_e6", "last_price_e6", "previous_price_e6", "yes_bid_e6", "yes_ask_e6",
    "no_bid_e6", "no_ask_e6", "yes_bid_size_e2", "yes_ask_size_e2", "volume_e2", "volume_24h_e2",
    "open_interest_e2", "notional_value_e6",
)  # fmt: skip

MARKETS_SCHEMA = pa.schema(
    [(name, pa.string()) for name in _MARKET_TEXT]
    + [("exchange_index", pa.int32()), ("can_close_early", pa.bool_())]
    + [(name, TIMESTAMP) for name in _MARKET_TIME]
    + [(name, pa.int64()) for name in _MARKET_INT]
    + [("raw_json", pa.string())]
)

SERIES_SCHEMA = pa.schema(
    [
        ("ticker", pa.string()),
        ("title", pa.string()),
        ("category", pa.string()),
        ("frequency", pa.string()),
        ("tags", pa.list_(pa.string())),
        ("exchange_index", pa.int32()),
        ("fee_type", pa.string()),
        ("fee_multiplier_e6", pa.int64()),
        ("fetched_at", TIMESTAMP),
    ]
)

FEE_CHANGES_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("series_ticker", pa.string()),
        ("fee_type", pa.string()),
        ("fee_multiplier_e6", pa.int64()),
        ("scheduled_ts", TIMESTAMP),
        ("fetched_at", TIMESTAMP),
    ]
)

CANDLES_SCHEMA = pa.schema(
    [
        ("ticker", pa.string()),
        ("period_minutes", pa.int32()),
        ("end_period", TIMESTAMP),
        ("price_open_e6", pa.int64()),
        ("price_high_e6", pa.int64()),
        ("price_low_e6", pa.int64()),
        ("price_close_e6", pa.int64()),
        ("price_mean_e6", pa.int64()),
        ("price_previous_e6", pa.int64()),
        ("yes_bid_open_e6", pa.int64()),
        ("yes_bid_high_e6", pa.int64()),
        ("yes_bid_low_e6", pa.int64()),
        ("yes_bid_close_e6", pa.int64()),
        ("yes_ask_open_e6", pa.int64()),
        ("yes_ask_high_e6", pa.int64()),
        ("yes_ask_low_e6", pa.int64()),
        ("yes_ask_close_e6", pa.int64()),
        ("volume_e2", pa.int64()),
        ("open_interest_e2", pa.int64()),
    ]
)


def is_combo(ticker: str) -> bool:
    return ticker.startswith(COMBO_PREFIX)


def series_ticker_of(event_ticker: str | None) -> str | None:
    """A market's series is the first part of its event ticker (checked live in K0.5.3b)."""
    return event_ticker.split("-")[0] if event_ticker else None


def label_of(market: Market) -> str:
    """The outcome label the study uses (KAPS decision 23).

    ``yes`` and ``no`` are the usable binary outcomes. ``scalar`` markets settle at a fraction
    of a dollar (the settlement value) and are kept apart. ``other_settled`` is anything with a
    settled status but no result, which the study leaves out until a study says otherwise.
    """
    if market.result in ("yes", "no", "scalar"):
        return market.result
    if market.status in SETTLED_STATUSES:
        return "other_settled"
    return "unsettled"


def trades_table(trades: Sequence[Trade]) -> pa.Table:
    return pa.table(
        {
            "trade_id": [t.trade_id for t in trades],
            "ticker": [t.ticker for t in trades],
            "created_time": [t.created_time for t in trades],
            "yes_price_e6": [t.yes_price_e6 for t in trades],
            "count_e2": [t.count_e2 for t in trades],
            "taker_side": [t.taker_side for t in trades],
            "is_block_trade": [t.is_block_trade for t in trades],
        },
        schema=TRADES_SCHEMA,
    )


def markets_table(
    markets: Sequence[Market], raw: Sequence[dict[str, Any]], source: str
) -> pa.Table:
    """Markets with their label, derived series ticker and the original JSON of each row."""
    columns: dict[str, list[Any]] = {field.name: [] for field in MARKETS_SCHEMA}
    for market, original in zip(markets, raw, strict=True):
        for name in MARKETS_SCHEMA.names:
            if name == "series_ticker":
                value = series_ticker_of(market.event_ticker)
            elif name == "source":
                value = source
            elif name == "label":
                value = label_of(market)
            elif name == "raw_json":
                value = json.dumps(original, separators=(",", ":"))
            else:
                value = getattr(market, name)
            columns[name].append(value)
    return pa.table(columns, schema=MARKETS_SCHEMA)


def series_table(series: Sequence[Series], fetched_at: datetime) -> pa.Table:
    return pa.table(
        {
            "ticker": [s.ticker for s in series],
            "title": [s.title for s in series],
            "category": [s.category for s in series],
            "frequency": [s.frequency for s in series],
            "tags": [s.tags for s in series],
            "exchange_index": [s.exchange_index for s in series],
            "fee_type": [s.fee_type for s in series],
            "fee_multiplier_e6": [s.fee_multiplier_e6 for s in series],
            "fetched_at": [fetched_at] * len(series),
        },
        schema=SERIES_SCHEMA,
    )


def fee_changes_table(changes: Sequence[SeriesFeeChange], fetched_at: datetime) -> pa.Table:
    return pa.table(
        {
            "id": [c.id for c in changes],
            "series_ticker": [c.series_ticker for c in changes],
            "fee_type": [c.fee_type for c in changes],
            "fee_multiplier_e6": [c.fee_multiplier_e6 for c in changes],
            "scheduled_ts": [c.scheduled_ts for c in changes],
            "fetched_at": [fetched_at] * len(changes),
        },
        schema=FEE_CHANGES_SCHEMA,
    )


def candles_table(ticker: str, period_minutes: int, candles: Sequence[Candlestick]) -> pa.Table:
    return pa.table(
        {
            "ticker": [ticker] * len(candles),
            "period_minutes": [period_minutes] * len(candles),
            "end_period": [c.end_period for c in candles],
            "price_open_e6": [c.price.open_e6 for c in candles],
            "price_high_e6": [c.price.high_e6 for c in candles],
            "price_low_e6": [c.price.low_e6 for c in candles],
            "price_close_e6": [c.price.close_e6 for c in candles],
            "price_mean_e6": [c.price.mean_e6 for c in candles],
            "price_previous_e6": [c.price.previous_e6 for c in candles],
            "yes_bid_open_e6": [c.yes_bid.open_e6 for c in candles],
            "yes_bid_high_e6": [c.yes_bid.high_e6 for c in candles],
            "yes_bid_low_e6": [c.yes_bid.low_e6 for c in candles],
            "yes_bid_close_e6": [c.yes_bid.close_e6 for c in candles],
            "yes_ask_open_e6": [c.yes_ask.open_e6 for c in candles],
            "yes_ask_high_e6": [c.yes_ask.high_e6 for c in candles],
            "yes_ask_low_e6": [c.yes_ask.low_e6 for c in candles],
            "yes_ask_close_e6": [c.yes_ask.close_e6 for c in candles],
            "volume_e2": [c.volume_e2 for c in candles],
            "open_interest_e2": [c.open_interest_e2 for c in candles],
        },
        schema=CANDLES_SCHEMA,
    )
