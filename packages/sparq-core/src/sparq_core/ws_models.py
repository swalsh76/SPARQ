"""Typed models for Kalshi's public WebSocket channels: ticker, trade, the order book and the
lifecycle channels.

Money and counts become fixed-point integers on the way in (a value more precise than the scale
is refused, never rounded), exactly as for the REST models. A frame is parsed once, here, with
floats kept as text, so a price such as ``0.56`` never exists as a binary float.

Two choices differ from a lenient reader:

* A field Kalshi adds is **counted**, not silently ignored: ``Frame.unknown`` lists it, and the
  feed keeps a running total per message type for ``status``, so a change of schema is noticed.
* A message that does not fit its model is **kept**, not dropped: ``Frame.error`` says why and
  ``Frame.raw`` still holds what arrived, so nothing recorded is lost to a parser bug.

Only the public data channels live here. The private channels (fills, orders, positions) belong to
the trading client (K7); the data key never subscribes to them.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, ValidationError, model_validator

from sparq_core.fixedpoint import E6, to_e2, to_e6
from sparq_core.models import (
    Model,
    Moment,
    OptAmountE6,
    OptCountE2,
    OptDollarsE6,
    RequiredCountE2,
    RequiredDollarsE6,
    Text,
    check_prices_add_to_a_dollar,
)

TICKER = "ticker"
TRADE = "trade"
MARKET_LIFECYCLE = "market_lifecycle_v2"
EVENT_LIFECYCLE = "event_lifecycle"
COMBO_LIFECYCLE = "multivariate_market_lifecycle"
ORDERBOOK = "orderbook_delta"  # the channel, and the type of its changes
SNAPSHOT = "orderbook_snapshot"  # the channel's first message for each market
# The channels the data key may subscribe to: market data only. The private channels (fills,
# orders, positions and the like) carry account data and are never subscribed with this key.
MARKET_DATA_CHANNELS = frozenset(
    {TICKER, TRADE, MARKET_LIFECYCLE, EVENT_LIFECYCLE, COMBO_LIFECYCLE, ORDERBOOK}
)
RESPONSE_TYPES = frozenset({"subscribed", "ok", "unsubscribed", "error"})


WsModel = Model  # the WebSocket messages share the REST models' base


class TickerMsg(WsModel):
    """The best prices and the totals of one market, sent whenever any of them changes."""

    market_ticker: str
    market_id: Text = None
    price_e6: OptDollarsE6 = Field(default=None, validation_alias="price_dollars")
    yes_bid_e6: OptDollarsE6 = Field(default=None, validation_alias="yes_bid_dollars")
    yes_ask_e6: OptDollarsE6 = Field(default=None, validation_alias="yes_ask_dollars")
    yes_bid_size_e2: OptCountE2 = Field(default=None, validation_alias="yes_bid_size_fp")
    yes_ask_size_e2: OptCountE2 = Field(default=None, validation_alias="yes_ask_size_fp")
    volume_e2: OptCountE2 = Field(default=None, validation_alias="volume_fp")
    open_interest_e2: OptCountE2 = Field(default=None, validation_alias="open_interest_fp")
    last_trade_size_e2: OptCountE2 = Field(default=None, validation_alias="last_trade_size_fp")
    dollar_volume_e6: OptAmountE6 = Field(default=None, validation_alias="dollar_volume")
    dollar_open_interest_e6: OptAmountE6 = Field(
        default=None, validation_alias="dollar_open_interest"
    )  # both arrive as whole dollars
    ts_ms: int | None = None  # when it happened, in milliseconds
    ts: int | None = None  # the same moment in whole seconds
    time: Moment = None  # the same moment as an RFC 3339 text


class TradeMsg(WsModel):
    """One trade, as it happened."""

    trade_id: str
    market_ticker: str
    yes_price_e6: RequiredDollarsE6 = Field(validation_alias="yes_price_dollars")
    no_price_e6: RequiredDollarsE6 = Field(validation_alias="no_price_dollars")
    count_e2: RequiredCountE2 = Field(validation_alias="count_fp")
    taker_side: Text = None  # yes or no; always the same as taker_outcome_side on the live feed
    taker_outcome_side: Text = None  # yes or no: the taker's outcome side
    taker_book_side: Text = None  # bid (a yes taker) or ask (a no taker)
    is_block_trade: bool = False
    ts_ms: int | None = None
    ts: int | None = None  # the same moment in whole seconds

    @model_validator(mode="after")
    def _prices_add_to_a_dollar(self) -> "TradeMsg":
        check_prices_add_to_a_dollar(self.yes_price_e6, self.no_price_e6)
        return self


class MarketLifecycleMsg(WsModel):
    """A market was created, opened, closed, determined or settled, or its terms changed.

    The times arrive as whole seconds since the epoch, which pydantic reads as UTC times.
    """

    market_ticker: Text = None
    event_type: str
    open_ts: Moment = None
    close_ts: Moment = None
    determination_ts: Moment = None
    settled_ts: Moment = None
    result: Text = None
    settlement_value_e6: OptDollarsE6 = Field(default=None, validation_alias="settlement_value")
    is_deactivated: bool | None = None
    exchange_index: int | None = None
    price_level_structure: Text = None
    additional_metadata: dict[str, Any] | None = None
    price_ranges: list[dict[str, Any]] | None = None  # the tick structure, on creation


class EventLifecycleMsg(WsModel):
    """An event was created (sent on both lifecycle channels)."""

    event_ticker: str
    series_ticker: Text = None
    title: str = ""
    subtitle: str = ""
    collateral_return_type: str = ""
    exchange_index: int | None = None


def _levels(value: Any) -> Any:
    """One side of a snapshot, ``[[price_dollars, count_fp], ...]``, as (price, count) pairs in
    millionths of a dollar and hundredths of a contract. The price must be text, as everywhere."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("a side of the book must be a list of levels")
    levels: list[tuple[int, int]] = []
    for level in value:
        if not isinstance(level, list) or len(level) != 2 or not isinstance(level[0], str):
            raise ValueError("a level must be [price as text, count]")
        price: str = level[0]
        count: str | int = level[1]
        if count is None:
            raise ValueError("a level's count is missing")
        try:
            price_e6, count_e2 = to_e6(price), to_e2(count)
        except TypeError as exc:  # a bool or float count
            raise ValueError(str(exc)) from None
        _check_price(price_e6)
        if count_e2 <= 0:
            raise ValueError(f"a level's count must be above zero, not {count!r}")
        levels.append((price_e6, count_e2))
    return tuple(levels)


def _check_price(price_e6: int) -> None:
    if not 0 < price_e6 < E6:
        raise ValueError(f"a bid must lie between 0 and 1 dollar, not {price_e6 / E6}")


Levels = Annotated[tuple[tuple[int, int], ...], BeforeValidator(_levels)]
"""A side of the book: (price_e6, count_e2) pairs, as Kalshi lists them."""


class OrderbookSnapshotMsg(WsModel):
    """The whole book of one market: both sides are bids (the yes ask is a dollar less the best no
    bid), and a side with no bids is left out. It holds every change up to its ``clock``."""

    market_ticker: str
    market_id: Text = None
    clock: int | None = None
    yes: Levels = Field(default=(), validation_alias="yes_dollars_fp")
    no: Levels = Field(default=(), validation_alias="no_dollars_fp")


class OrderbookDeltaMsg(WsModel):
    """A change of the size bid at one price on one side; negative when contracts left."""

    market_ticker: str
    market_id: Text = None
    clock: int | None = None
    side: Literal["yes", "no"]
    price_e6: RequiredDollarsE6 = Field(validation_alias="price_dollars")
    delta_e2: RequiredCountE2 = Field(validation_alias="delta_fp")
    client_order_id: Text = None  # only on one's own orders, which the data key never has
    ts_ms: int | None = None
    ts: Moment = None  # the same moment as an RFC 3339 text (not seconds, as on the trades)

    @model_validator(mode="after")
    def _a_price_on_the_book(self) -> "OrderbookDeltaMsg":
        _check_price(self.price_e6)
        return self


Payload = (
    TickerMsg
    | TradeMsg
    | MarketLifecycleMsg
    | EventLifecycleMsg
    | OrderbookSnapshotMsg
    | OrderbookDeltaMsg
)
MODELS: dict[str, type[WsModel]] = {
    TICKER: TickerMsg,
    TRADE: TradeMsg,
    SNAPSHOT: OrderbookSnapshotMsg,
    ORDERBOOK: OrderbookDeltaMsg,
    MARKET_LIFECYCLE: MarketLifecycleMsg,
    COMBO_LIFECYCLE: MarketLifecycleMsg,
    EVENT_LIFECYCLE: EventLifecycleMsg,
}


def known_keys(model: type[WsModel]) -> frozenset[str]:
    """Every name under which the model reads a field: its alias if it has one, else its name."""
    names: set[str] = set()
    for name, field in model.model_fields.items():
        alias = field.validation_alias
        names.add(alias if isinstance(alias, str) else name)
        names.add(name)
    return frozenset(names)


@dataclass(frozen=True, slots=True)
class Frame:
    """One frame off the socket, parsed once."""

    type: str
    sid: int | None
    seq: int | None
    id: int | None
    sending_ts_ms: int | None
    payload: Payload | None  # the typed message, for a known data type that parsed
    raw: Mapping[str, Any]  # the frame as received (floats as text)
    unknown: tuple[str, ...] = ()  # fields in the message that the model does not know
    error: str | None = None  # why a known data type did not parse, or why the frame is odd

    @property
    def is_response(self) -> bool:
        return self.type in RESPONSE_TYPES


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_frame(raw: str | bytes) -> Frame:
    """Parse one frame. Raises ``ValueError`` only when it is not a JSON object with a ``type``."""
    document = json.loads(raw, parse_float=str)
    if not isinstance(document, dict) or not isinstance(document.get("type"), str):
        raise ValueError("a frame must be a JSON object with a text 'type'")
    kind = document["type"]
    message = document.get("msg")
    payload: Payload | None = None
    unknown: tuple[str, ...] = ()
    error: str | None = None
    model = MODELS.get(kind)
    if model is not None:
        if isinstance(message, dict):
            unknown = tuple(sorted(set(message) - known_keys(model)))
            try:
                payload = model.model_validate(message)  # type: ignore[assignment]
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(part) for part in first["loc"])
                error = f"{kind}: {where}: {first['msg']}"[:300]
        else:
            error = f"{kind}: the message is not an object"
    return Frame(
        type=kind,
        sid=_int_or_none(document.get("sid")),
        seq=_int_or_none(document.get("seq")),
        id=_int_or_none(document.get("id")),
        sending_ts_ms=_int_or_none(document.get("sending_ts_ms")),
        payload=payload,
        raw=document,
        unknown=unknown,
        error=error,
    )


__all__ = [
    "COMBO_LIFECYCLE",
    "EVENT_LIFECYCLE",
    "Frame",
    "MARKET_LIFECYCLE",
    "ORDERBOOK",
    "RESPONSE_TYPES",
    "SNAPSHOT",
    "TICKER",
    "TRADE",
    "EventLifecycleMsg",
    "Levels",
    "MarketLifecycleMsg",
    "OrderbookDeltaMsg",
    "OrderbookSnapshotMsg",
    "Payload",
    "TickerMsg",
    "TradeMsg",
    "known_keys",
    "parse_frame",
]
