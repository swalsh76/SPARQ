"""From a parsed frame to the row that is stored, with the rules that decide what is stored.

Nothing here touches a database, so every rule is tested alone. A frame becomes exactly one of:

* a **row** for ``ticker``, ``trade`` or ``market_lifecycle``;
* a **reject**: a frame that cannot be stored (it did not fit its model, or breaks a rule the
  database also enforces). It is kept, with the reason, in ``ingest_reject`` and counted, never
  dropped silently;
* a **skip**: a frame that is deliberately not stored (a combo market, an event-level message),
  counted by reason.

The rules a row must meet mirror the database's own checks, so that a bad row is found here, one at
a time, and does not make the database refuse a whole batch.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NamedTuple

from sparq_core.fixedpoint import E6
from sparq_core.markets import is_combo
from sparq_core.ws import Data
from sparq_core.ws_models import (
    COMBO_LIFECYCLE,
    MARKET_LIFECYCLE,
    TICKER,
    TRADE,
    Frame,
    MarketLifecycleMsg,
    TickerMsg,
    TradeMsg,
)

SOURCE_STREAM = "stream"
PLAUSIBLE_MS = 3_600_000  # an exchange time further than an hour from our clock is not believed

TICKER_COLUMNS = (
    "ts", "received_at", "ticker", "price_e6", "yes_bid_e6", "yes_ask_e6", "yes_bid_size_e2",
    "yes_ask_size_e2", "volume_e2", "open_interest_e2", "last_trade_size_e2", "dollar_volume_e6",
    "dollar_open_interest_e6",
)  # fmt: skip
TRADE_COLUMNS = (
    "trade_id", "ticker", "created_time", "yes_price_e6", "count_e2", "taker_side",
    "is_block_trade",
)  # fmt: skip
LIFECYCLE_COLUMNS = (
    "ticker", "observed_at", "sent_at", "source", "event_type", "open_time", "close_time",
    "determination_time", "settled_time", "result", "settlement_value_e6", "is_deactivated",
    "exchange_index", "price_level_structure", "details",
)  # fmt: skip


class TickerRow(NamedTuple):
    ts: datetime
    received_at: datetime
    ticker: str
    price_e6: int | None
    yes_bid_e6: int | None
    yes_ask_e6: int | None
    yes_bid_size_e2: int | None
    yes_ask_size_e2: int | None
    volume_e2: int | None
    open_interest_e2: int | None
    last_trade_size_e2: int | None
    dollar_volume_e6: int | None
    dollar_open_interest_e6: int | None


class TradeRow(NamedTuple):
    trade_id: str
    ticker: str
    created_time: datetime
    yes_price_e6: int
    count_e2: int
    taker_side: str
    is_block_trade: bool


class LifecycleRow(NamedTuple):
    ticker: str
    observed_at: datetime
    sent_at: datetime | None
    source: str
    event_type: str
    open_time: datetime | None
    close_time: datetime | None
    determination_time: datetime | None
    settled_time: datetime | None
    result: str | None
    settlement_value_e6: int | None
    is_deactivated: bool | None
    exchange_index: int | None
    price_level_structure: str | None
    details: str | None  # JSON text


Row = TickerRow | TradeRow | LifecycleRow


@dataclass(frozen=True, slots=True)
class Reject:
    kind: str  # the frame type
    reason: str
    payload: Any  # the frame as received


@dataclass(frozen=True, slots=True)
class Skip:
    reason: str


def moment(milliseconds: int) -> datetime:
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)


def received_time(received_ns: int) -> datetime:
    return datetime.fromtimestamp(received_ns / 1e9, tz=UTC)


def event_time(ts_ms: int | None, received_ns: int) -> tuple[datetime, bool]:
    """The time to store: the exchange's, when it is positive and within an hour of ours.

    Returns the time and whether the exchange's time was not believed (so that it is counted).
    """
    received_ms = received_ns // 1_000_000
    if ts_ms is not None and ts_ms > 0 and abs(ts_ms - received_ms) <= PLAUSIBLE_MS:
        return moment(ts_ms), False
    return received_time(received_ns), ts_ms is not None


def clean_text(value: str | None) -> bool:
    """Usable as a stored identifier: not empty, and without the NUL that Postgres refuses."""
    return bool(value) and "\x00" not in str(value)


def _in_dollar(value: int | None) -> bool:
    return value is None or 0 <= value <= E6


def _not_negative(*values: int | None) -> bool:
    return all(v is None or v >= 0 for v in values)


def ticker_row(message: TickerMsg, received_ns: int) -> tuple[TickerRow | Reject, bool]:
    when, doubtful = event_time(message.ts_ms, received_ns)
    row = TickerRow(
        when, received_time(received_ns), message.market_ticker, message.price_e6,
        message.yes_bid_e6, message.yes_ask_e6, message.yes_bid_size_e2, message.yes_ask_size_e2,
        message.volume_e2, message.open_interest_e2, message.last_trade_size_e2,
        message.dollar_volume_e6, message.dollar_open_interest_e6,
    )  # fmt: skip
    if not clean_text(row.ticker):
        return Reject(TICKER, "the market ticker is empty or has a NUL", None), doubtful
    if not (_in_dollar(row.price_e6) and _in_dollar(row.yes_bid_e6) and _in_dollar(row.yes_ask_e6)):
        return Reject(TICKER, "a price is outside 0 to 1 dollar", None), doubtful
    if not _not_negative(
        row.yes_bid_size_e2, row.yes_ask_size_e2, row.volume_e2, row.open_interest_e2,
        row.last_trade_size_e2, row.dollar_volume_e6, row.dollar_open_interest_e6,
    ):  # fmt: skip
        return Reject(TICKER, "a size or total is negative", None), doubtful
    return row, doubtful


def check_trade(row: TradeRow) -> str | None:
    """Why a trade cannot be stored, or None. The WebSocket and the backfill both ask this."""
    if not (clean_text(row.trade_id) and clean_text(row.ticker)):
        return "the trade id or ticker is empty or has a NUL"
    if not _in_dollar(row.yes_price_e6) or row.count_e2 < 0:
        return "the price is outside 0 to 1 dollar or the count is negative"
    if row.taker_side not in ("yes", "no"):
        return f"the taker side is {row.taker_side!r}, not yes or no"
    return None


def floor_ms(moment: datetime) -> datetime:
    """A time cut to the millisecond: the WebSocket's ``ts_ms`` is REST's time cut this way, so
    storing both sources at this resolution makes the same trade the same row."""
    return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


def trade_row(message: TradeMsg, received_ns: int) -> tuple[TradeRow | Reject, bool]:
    when, doubtful = event_time(message.ts_ms, received_ns)
    side = message.taker_outcome_side or message.taker_side
    row = TradeRow(
        message.trade_id, message.market_ticker, when, message.yes_price_e6, message.count_e2,
        side or "", message.is_block_trade,
    )  # fmt: skip
    problem = check_trade(row)
    return (Reject(TRADE, problem, None) if problem else row), doubtful


def lifecycle_row(
    message: MarketLifecycleMsg, sending_ts_ms: int | None, received_ns: int
) -> LifecycleRow | Reject | Skip:
    if not message.market_ticker:
        return Skip("a lifecycle message with no market")
    if not clean_text(message.market_ticker) or not clean_text(message.event_type):
        return Reject(MARKET_LIFECYCLE, "the ticker or event type is empty or has a NUL", None)
    details: dict[str, Any] = {}
    if message.additional_metadata is not None:
        details["additional_metadata"] = message.additional_metadata
    if message.price_ranges is not None:
        details["price_ranges"] = message.price_ranges
    sent = moment(sending_ts_ms) if sending_ts_ms and sending_ts_ms > 0 else None
    return LifecycleRow(
        message.market_ticker, received_time(received_ns), sent, SOURCE_STREAM, message.event_type,
        message.open_ts, message.close_ts, message.determination_ts, message.settled_ts,
        message.result, message.settlement_value_e6, message.is_deactivated, message.exchange_index,
        message.price_level_structure, json.dumps(details) if details else None,
    )  # fmt: skip


@dataclass(frozen=True, slots=True)
class Converted:
    """The outcome for one frame: its row, a reject, or a skip, and whether its time was doubted."""

    result: Row | Reject | Skip
    doubtful_time: bool = False


def convert(data: Data) -> Converted:
    frame: Frame = data.frame
    if frame.error is not None:
        return Converted(Reject(frame.type, frame.error, frame.raw))
    payload = frame.payload
    if isinstance(payload, TickerMsg | TradeMsg) and is_combo(payload.market_ticker):
        return Converted(Skip("combo market"))
    result: Row | Reject | Skip
    doubtful = False
    if isinstance(payload, TickerMsg):
        result, doubtful = ticker_row(payload, data.received_ns)
    elif isinstance(payload, TradeMsg):
        result, doubtful = trade_row(payload, data.received_ns)
    elif isinstance(payload, MarketLifecycleMsg):
        if frame.type == COMBO_LIFECYCLE or is_combo(payload.market_ticker or ""):
            return Converted(Skip("combo market"))
        result = lifecycle_row(payload, frame.sending_ts_ms, data.received_ns)
    else:
        return Converted(Skip(f"a {frame.type} message is not recorded"))
    if isinstance(result, Reject):
        result = Reject(result.kind, result.reason, frame.raw)  # keep what arrived
    return Converted(result, doubtful)
