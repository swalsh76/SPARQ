from datetime import UTC, datetime

import pytest
from sparq_analytics.history.schemas import (
    MARKETS_SCHEMA,
    candles_table,
    fee_changes_table,
    markets_table,
    series_table,
    trades_table,
)
from sparq_core.markets import MARKET_COLUMNS
from sparq_core.models import Candlestick, Market, Series, SeriesFeeChange, Trade

WHEN = datetime(2026, 10, 21, 12, tzinfo=UTC)


def test_the_market_file_has_the_shared_columns() -> None:
    assert tuple(MARKETS_SCHEMA.names) == MARKET_COLUMNS


def test_a_market_table_keeps_none_as_null_and_the_original_json() -> None:
    market = Market.model_validate(
        {"ticker": "KXA-1-X", "event_ticker": "KXA-1", "status": "active"}
    )
    table = markets_table([market], [{"ticker": "KXA-1-X", "extra": "1.5"}], "live")
    row = table.to_pylist()[0]
    assert row["series_ticker"] == "KXA"
    assert row["label"] == "unsettled"
    assert row["source"] == "live"
    assert row["settlement_ts"] is None
    assert row["raw_json"] == '{"ticker":"KXA-1-X","extra":"1.5"}'


def test_markets_and_raw_rows_must_pair_up() -> None:
    with pytest.raises(ValueError):
        markets_table([Market.model_validate({"ticker": "A"})], [], "live")


def test_a_trade_table_has_the_compact_columns() -> None:
    trade = Trade.model_validate(
        {
            "trade_id": "t1",
            "ticker": "KXA-1",
            "created_time": "2026-10-21T12:00:00.5Z",
            "yes_price_dollars": "0.2200",
            "no_price_dollars": "0.7800",
            "count_fp": "1.50",
            "taker_outcome_side": "no",
            "is_block_trade": True,
        }
    )
    row = trades_table([trade]).to_pylist()[0]
    assert row["yes_price_e6"] == 220_000
    assert row["count_e2"] == 150
    assert row["taker_side"] == "no"
    assert row["is_block_trade"] is True
    assert "no_price_e6" not in row  # implied by yes + no = $1
    assert trades_table([]).num_rows == 0


def test_series_fee_change_and_candle_tables() -> None:
    series = Series.model_validate(
        {"ticker": "KXA", "fee_type": "quadratic", "fee_multiplier": "0.5", "tags": ["x"]}
    )
    row = series_table([series], WHEN).to_pylist()[0]
    assert (row["ticker"], row["fee_multiplier_e6"], row["tags"]) == ("KXA", 500_000, ["x"])

    change = SeriesFeeChange.model_validate(
        {
            "id": "i",
            "series_ticker": "KXA",
            "fee_type": "quadratic",
            "fee_multiplier": 1,
            "scheduled_ts": "2026-09-03T18:35:23Z",
        }
    )
    assert fee_changes_table([change], WHEN).to_pylist()[0]["fee_multiplier_e6"] == 1_000_000

    candle = Candlestick.model_validate(
        {
            "end_period_ts": 1_792_600_000,
            "volume": "1.00",
            "open_interest": "2.00",
            "price": {"open": "0.1000", "high": "0.2000", "low": "0.0500", "close": "0.1500",
                      "mean": "0.1200", "previous": "0.1000"},
            "yes_bid": {"open": "0.0900", "high": "0.1000", "low": "0.0800", "close": "0.0900"},
            "yes_ask": {"open": "0.1100", "high": "0.1200", "low": "0.1000", "close": "0.1100"},
        }
    )  # fmt: skip
    out = candles_table("KXA-1", 60, [candle]).to_pylist()[0]
    assert (out["ticker"], out["period_minutes"]) == ("KXA-1", 60)
    assert (out["price_high_e6"], out["yes_ask_close_e6"]) == (200_000, 110_000)
    assert (out["volume_e2"], out["open_interest_e2"]) == (100, 200)
