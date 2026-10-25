"""Frames in the shapes the real feed sent on 2026-10-24 (invented values), for the ingest tests."""

import json
from typing import Any

from sparq_core.ws import Data
from sparq_core.ws_models import parse_frame

RECEIVED_NS = 1_792_789_506_150 * 1_000_000  # 2026-10-23 21:05:06.150 UTC
TS_MS = 1_792_789_506_065

TICKER = {
    "market_ticker": "KXTEST-26OCT24-A",
    "price_dollars": "0.5300",
    "yes_bid_dollars": "0.5100",
    "yes_ask_dollars": "0.5200",
    "volume_fp": "30112.46",
    "open_interest_fp": "20564.30",
    "dollar_volume": 15056,
    "dollar_open_interest": 10282,
    "yes_bid_size_fp": "176.00",
    "yes_ask_size_fp": "156.00",
    "last_trade_size_fp": "214.69",
    "ts_ms": TS_MS,
}
TRADE = {
    "trade_id": "t-1",
    "market_ticker": "KXTEST-26OCT24-A",
    "yes_price_dollars": "0.6500",
    "no_price_dollars": "0.3500",
    "count_fp": "6.15",
    "taker_side": "yes",
    "taker_outcome_side": "yes",
    "taker_book_side": "bid",
    "is_block_trade": False,
    "ts_ms": TS_MS,
}
LIFECYCLE = {
    "market_ticker": "KXTEST-26OCT24-A",
    "event_type": "created",
    "open_ts": 1_792_790_100,
    "close_ts": 1_793_120_400,
    "exchange_index": 0,
    "price_level_structure": "linear_cent",
    "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}],
    "additional_metadata": {"title": "A title", "rules_primary": "If so, Yes."},
}


def data(kind: str, msg: Any, received_ns: int = RECEIVED_NS, **envelope: Any) -> Data:
    frame = parse_frame(json.dumps({"type": kind, "msg": msg, **envelope}))
    return Data(frame, received_ns, 0, 1)


def ticker(name: str = "KXTEST-26OCT24-A", **changes: Any) -> Data:
    return data("ticker", {**TICKER, "market_ticker": name, **changes}, sid=1)


def trade(trade_id: str = "t-1", name: str = "KXTEST-26OCT24-A", **changes: Any) -> Data:
    return data("trade", {**TRADE, "trade_id": trade_id, "market_ticker": name, **changes}, sid=2)


def lifecycle(**changes: Any) -> Data:
    return data("market_lifecycle_v2", {**LIFECYCLE, **changes}, sid=3, seq=1, sending_ts_ms=TS_MS)
