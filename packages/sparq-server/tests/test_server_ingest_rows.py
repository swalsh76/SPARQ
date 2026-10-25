"""From a frame to a stored row: the rules, tested without a database."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from ingestfixtures import RECEIVED_NS, TICKER, TRADE, TS_MS, data
from sparq_core.ws import Data
from sparq_server.ingest.rows import (
    PLAUSIBLE_MS,
    LifecycleRow,
    Reject,
    Skip,
    TickerRow,
    TradeRow,
    convert,
    event_time,
)


def result(item: Data) -> Any:
    return convert(item).result


# --- the time rule ----------------------------------------------------------------------------


def test_the_exchange_time_is_used_when_it_is_believable() -> None:
    when, doubtful = event_time(TS_MS, RECEIVED_NS)
    assert when == datetime.fromtimestamp(TS_MS / 1000, tz=UTC) and doubtful is False


@pytest.mark.parametrize("ts_ms", [0, -5, 1, RECEIVED_NS // 10**6 + PLAUSIBLE_MS + 1, 10**13])
def test_a_time_that_is_zero_negative_or_far_from_ours_is_replaced_and_counted(ts_ms: int) -> None:
    when, doubtful = event_time(ts_ms, RECEIVED_NS)
    assert when == datetime.fromtimestamp(RECEIVED_NS / 1e9, tz=UTC) and doubtful is True


def test_a_time_exactly_an_hour_away_is_still_believed_and_one_millisecond_more_is_not() -> None:
    received_ms = RECEIVED_NS // 10**6
    assert event_time(received_ms - PLAUSIBLE_MS, RECEIVED_NS)[1] is False
    assert event_time(received_ms + PLAUSIBLE_MS, RECEIVED_NS)[1] is False
    assert event_time(received_ms + PLAUSIBLE_MS + 1, RECEIVED_NS)[1] is True


def test_no_exchange_time_means_our_time_without_a_complaint() -> None:
    when, doubtful = event_time(None, RECEIVED_NS)
    assert when == datetime.fromtimestamp(RECEIVED_NS / 1e9, tz=UTC) and doubtful is False


# --- tickers -----------------------------------------------------------------------------------


def test_a_ticker_becomes_a_row_with_both_times() -> None:
    row = result(data("ticker", TICKER, sid=1))
    assert isinstance(row, TickerRow)
    assert row.ts == datetime.fromtimestamp(TS_MS / 1000, tz=UTC)
    assert row.received_at == datetime.fromtimestamp(RECEIVED_NS / 1e9, tz=UTC)
    assert (row.ticker, row.price_e6, row.yes_bid_e6, row.yes_ask_e6) == (
        "KXTEST-26OCT24-A", 530_000, 510_000, 520_000,
    )  # fmt: skip
    assert (row.yes_bid_size_e2, row.yes_ask_size_e2, row.volume_e2) == (17_600, 15_600, 3_011_246)
    assert (row.dollar_volume_e6, row.dollar_open_interest_e6) == (15_056_000_000, 10_282_000_000)


def test_a_ticker_with_only_a_market_is_stored_with_empty_fields() -> None:
    row = result(data("ticker", {"market_ticker": "KXTEST-A"}))
    assert isinstance(row, TickerRow) and row.price_e6 is None and row.volume_e2 is None
    assert row.ts == row.received_at  # no exchange time: ours


@pytest.mark.parametrize(
    "change",
    [
        {"price_dollars": "1.5000"},
        {"yes_bid_dollars": "-0.0100"},
        {"yes_ask_dollars": "1.0001"},
        {"yes_bid_size_fp": "-1.00"},
        {"volume_fp": "-3.00"},
        {"dollar_volume": -1},
        {"market_ticker": ""},
        {"market_ticker": "KX\x00TEST"},
    ],
)
def test_a_ticker_that_breaks_a_rule_is_a_reject_that_keeps_what_arrived(
    change: dict[str, Any],
) -> None:
    item = data("ticker", {**TICKER, **change}, sid=1)
    outcome = result(item)
    assert isinstance(outcome, Reject) and outcome.kind == "ticker" and outcome.reason
    assert outcome.payload["msg"]["market_ticker"] == change.get(
        "market_ticker", "KXTEST-26OCT24-A"
    )


# --- trades ------------------------------------------------------------------------------------


def test_a_trade_becomes_a_row() -> None:
    row = result(data("trade", TRADE, sid=2, seq=1))
    assert row == TradeRow(
        "t-1", "KXTEST-26OCT24-A", datetime.fromtimestamp(TS_MS / 1000, tz=UTC),
        650_000, 615, "yes", False,
    )  # fmt: skip


def test_the_taker_side_comes_from_the_outcome_side_and_falls_back_to_the_old_field() -> None:
    older = {k: v for k, v in TRADE.items() if k != "taker_outcome_side"} | {"taker_side": "no"}
    assert result(data("trade", older)).taker_side == "no"
    newer = {**TRADE, "taker_side": "no", "taker_outcome_side": "yes"}
    assert result(data("trade", newer)).taker_side == "yes"


@pytest.mark.parametrize(
    "change",
    [
        {"taker_outcome_side": "maybe", "taker_side": "maybe"},
        {"taker_outcome_side": None, "taker_side": None},
        {"count_fp": "-1.00"},
        {"yes_price_dollars": "-0.5000", "no_price_dollars": "1.5000"},
        {"trade_id": "t\x00"},
    ],
)
def test_a_trade_that_breaks_a_rule_is_a_reject(change: dict[str, Any]) -> None:
    outcome = result(data("trade", {**TRADE, **change}))
    assert isinstance(outcome, Reject) and outcome.kind == "trade"


def test_a_trade_whose_prices_do_not_add_to_a_dollar_is_a_reject_from_the_parser() -> None:
    outcome = result(data("trade", {**TRADE, "no_price_dollars": "0.4000"}))
    assert isinstance(outcome, Reject) and "do not add to $1" in outcome.reason
    assert outcome.payload["msg"]["trade_id"] == "t-1"


def test_a_block_trade_is_marked() -> None:
    assert result(data("trade", {**TRADE, "is_block_trade": True})).is_block_trade is True


# --- lifecycle ---------------------------------------------------------------------------------


def test_a_created_market_keeps_its_details_as_json() -> None:
    body = {
        "market_ticker": "KXTEST-A",
        "event_type": "created",
        "open_ts": 1_792_790_100,
        "close_ts": 1_793_120_400,
        "exchange_index": 0,
        "price_level_structure": "linear_cent",
        "price_ranges": [{"start": "0.0000"}],
        "additional_metadata": {"rules_primary": "If so, Yes."},
    }
    row = result(data("market_lifecycle_v2", body, sid=3, seq=1, sending_ts_ms=TS_MS))
    assert isinstance(row, LifecycleRow)
    assert (row.ticker, row.event_type, row.source) == ("KXTEST-A", "created", "stream")
    assert row.open_time == datetime(2026, 10, 23, 21, 15, tzinfo=UTC)
    assert row.sent_at == datetime.fromtimestamp(TS_MS / 1000, tz=UTC)
    assert json.loads(row.details or "") == {
        "additional_metadata": {"rules_primary": "If so, Yes."},
        "price_ranges": [{"start": "0.0000"}],
    }


def test_a_settlement_carries_its_result_and_value_and_no_details() -> None:
    body = {
        "market_ticker": "KXTEST-A",
        "event_type": "determined",
        "result": "yes",
        "settlement_value": "1.0000",
    }
    row = result(data("market_lifecycle_v2", body))
    assert isinstance(row, LifecycleRow)
    assert (row.result, row.settlement_value_e6, row.details, row.sent_at) == (
        "yes",
        1_000_000,
        None,
        None,
    )


def test_a_lifecycle_message_with_no_market_is_skipped() -> None:
    assert result(data("market_lifecycle_v2", {"event_type": "created"})) == Skip(
        "a lifecycle message with no market"
    )


def test_a_lifecycle_message_with_a_nul_in_its_ticker_is_rejected() -> None:
    outcome = result(
        data("market_lifecycle_v2", {"market_ticker": "A\x00", "event_type": "created"})
    )
    assert isinstance(outcome, Reject) and outcome.kind == "market_lifecycle_v2"


# --- what is not stored ------------------------------------------------------------------------


def test_combo_markets_are_skipped_on_every_channel() -> None:
    assert result(data("ticker", {**TICKER, "market_ticker": "KXMVECROSS-1"})) == Skip(
        "combo market"
    )
    assert result(data("trade", {**TRADE, "market_ticker": "KXMVESPORTS-1"})) == Skip(
        "combo market"
    )
    life = {"market_ticker": "KXMVE-1", "event_type": "created"}
    assert result(data("market_lifecycle_v2", life)) == Skip("combo market")
    assert result(data("multivariate_market_lifecycle", {"event_type": "created"})) == Skip(
        "combo market"
    )


@pytest.mark.parametrize("kind", ["event_lifecycle", "orderbook_delta", "something_new"])
def test_other_message_types_are_skipped_with_their_name(kind: str) -> None:
    outcome = result(data(kind, {"event_ticker": "E"}))
    assert isinstance(outcome, Skip) and kind in outcome.reason


def test_a_frame_that_did_not_fit_its_model_is_rejected_with_the_parser_s_reason() -> None:
    outcome = result(data("trade", {"market_ticker": "M"}))  # no trade_id, no prices
    assert isinstance(outcome, Reject) and outcome.kind == "trade" and "trade_id" in outcome.reason
    assert outcome.payload["msg"] == {"market_ticker": "M"}


def test_a_doubtful_time_is_reported_with_the_row_and_does_not_reject_it() -> None:
    converted = convert(data("ticker", {**TICKER, "ts_ms": 5}))
    assert isinstance(converted.result, TickerRow) and converted.doubtful_time is True
    assert converted.result.ts == converted.result.received_at
    assert convert(data("ticker", TICKER)).doubtful_time is False
