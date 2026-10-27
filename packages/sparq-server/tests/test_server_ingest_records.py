"""The market rows are exactly the rows the downloader writes (so K2's import is a copy)."""

from typing import Any

from sparq_core.markets import MARKET_COLUMNS, market_values
from sparq_core.models import Market


def pair(ticker: str, **changes: Any) -> tuple[Market, dict[str, Any]]:
    raw = {
        "ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0],
        "market_type": "binary",
        "status": "open",
        "result": "",
        "title": "A test market",
        "rules_primary": "If so, Yes.",
        "created_time": "2026-10-20T12:00:00Z",
        "close_time": "2026-10-25T17:00:00Z",
        "updated_time": "2026-10-24T11:59:00Z",
        "yes_bid_dollars": "0.5100",
        "volume_fp": "100.00",
        "price_level_structure": "linear_cent",
        "exchange_index": 0,
        "can_close_early": True,
        **changes,
    }
    return Market.model_validate(raw), raw


def test_the_rows_are_the_rows_the_downloader_writes() -> None:
    from sparq_analytics.history.schemas import markets_table

    cases = [
        pair("KXTEST-26OCT24-A"),
        pair("KXTEST-26OCT22-A", status="settled", result="yes", settlement_value_dollars="1.0000"),
        pair(
            "KXTEST-26OCT22-B", status="finalized", result="no", settlement_value_dollars="0.0000"
        ),
        pair("KXTEST-26OCT22-C", status="determined", result=""),
        pair(
            "KXTEST-26OCT22-D", status="settled", result="scalar", settlement_value_dollars="0.4200"
        ),
        pair("KXTEST-26OCT22-E", status="closed", result=""),
    ]
    # what discovery stores, against what the downloader's file holds after a Parquet round trip
    table = markets_table([m for m, _ in cases], [r for _, r in cases], "live").to_pylist()
    for (market, raw), expected in zip(cases, table, strict=True):
        record = dict(zip(MARKET_COLUMNS, market_values(market, raw, "live"), strict=True))
        assert record == expected, market.ticker
