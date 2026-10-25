"""A scripted fake Kalshi for the downloader tests, obeying the rules measured live.

* trades: ``min_ts`` inclusive, ``max_ts`` exclusive (whole seconds), newest first, cursor paging,
  page size at most 1,000; the historical endpoint serves only trades before the cutoff and the
  live endpoint only those at or after it;
* markets: the historical listing is every historical market; the live listing filters by
  settlement time and status.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
from sparq_analytics.history.downloader import DownloadConfig, Downloader
from sparq_core.config import Environment, KalshiConfig
from sparq_core.rest import KalshiPublicClient

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC).timestamp()  # newest complete day: 2026-10-21
CUTOFF = int(datetime(2026, 10, 20, 12, 0, tzinfo=UTC).timestamp())


def trade(
    n: int, ticker: str, when: datetime, yes: int = 250_000, count: int = 100
) -> dict[str, Any]:
    return {
        "trade_id": f"00000000-0000-4000-8000-{n:012d}",
        "ticker": ticker,
        "created_time": when.isoformat().replace("+00:00", "Z"),
        "yes_price_dollars": f"{yes / 1e6:.4f}",
        "no_price_dollars": f"{(1_000_000 - yes) / 1e6:.4f}",
        "count_fp": f"{count / 100:.2f}",
        "taker_side": "yes",
        "taker_outcome_side": "yes",
        "taker_book_side": "bid",
        "is_block_trade": False,
    }


def market(
    ticker: str,
    *,
    result: str = "yes",
    opened: datetime,
    settled: datetime,
    volume: int = 0,
    status: str = "finalized",
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0],
        "market_type": "binary",
        "status": status,
        "result": result,
        "open_time": opened.isoformat().replace("+00:00", "Z"),
        "close_time": settled.isoformat().replace("+00:00", "Z"),
        "settlement_ts": settled.isoformat().replace("+00:00", "Z"),
        "settlement_value_dollars": "1.0000" if result == "yes" else "0.0000",
        "volume_fp": f"{volume / 100:.2f}",
        "title": f"Market {ticker}",
    }


@dataclass
class FakeKalshi:
    cutoff: int = CUTOFF
    trades: list[dict[str, Any]] = field(default_factory=list)
    historical_markets: list[dict[str, Any]] = field(default_factory=list)
    live_markets: list[dict[str, Any]] = field(default_factory=list)
    series: list[dict[str, Any]] = field(default_factory=list)
    fee_changes: list[dict[str, Any]] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)
    crash_after: int | None = None  # raise once this many requests have been served
    reject_cursors: bool = False  # answer HTTP 400 to any request that carries a cursor
    limit_every: int | None = None  # answer 429 to every n-th request ...
    limit_after: int = 0  # ... once this many requests have been served
    fail_status: int | None = None  # answer every request with this HTTP status
    markets_page: int = 2
    candles: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def count(self, prefix: str) -> int:
        return sum(
            1 for r in self.requests if r.url.path.removeprefix("/trade-api/v2").startswith(prefix)
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if len(self.requests) > 20_000:  # a runaway loop must fail the test, not hang it
            raise AssertionError("more than 20,000 requests: the downloader did not stop")
        if self.crash_after is not None and len(self.requests) > self.crash_after:
            raise RuntimeError("simulated crash")
        served = len(self.requests)
        if self.limit_every and served > self.limit_after and served % self.limit_every == 0:
            return httpx.Response(429, text="too many requests")
        if self.fail_status is not None:
            return httpx.Response(self.fail_status, text="refused")
        params = request.url.params
        if self.reject_cursors and params.get("cursor"):
            return httpx.Response(400, text='{"error":"bad cursor"}')
        path = request.url.path.removeprefix("/trade-api/v2")
        if path == "/historical/cutoff":
            iso = datetime.fromtimestamp(self.cutoff, UTC).isoformat().replace("+00:00", "Z")
            return self.ok(
                {"market_settled_ts": iso, "trades_created_ts": iso, "orders_updated_ts": iso}
            )
        if path == "/series":
            return self.ok({"series": self.series})
        if path == "/series/fee_changes":
            return self.ok({"series_fee_change_arr": self.fee_changes})
        if path in ("/historical/trades", "/markets/trades"):
            return self.trades_page(path, params)
        if path == "/historical/markets":
            return self.paged(self.historical_markets, "markets", params, self.markets_page)
        if path == "/markets":
            low, high = int(params["min_settled_ts"]), int(params["max_settled_ts"])
            rows = [
                m
                for m in self.live_markets
                if low <= int(datetime.fromisoformat(m["settlement_ts"]).timestamp()) < high
            ]
            return self.paged(rows, "markets", params, self.markets_page)
        if path.endswith("/candlesticks"):
            return self.candles_page(path, params)
        return httpx.Response(404, text="no such path")

    def candles_page(self, path: str, params: Any) -> httpx.Response:
        """Kalshi allows 5,000 candles per request, judged by (range / interval)."""
        start = int(params["start_ts"])
        end = int(params["end_ts"])
        interval = int(params["period_interval"])
        if (end - start) / (interval * 60) > 5000:
            return httpx.Response(400, text="requested time range with candlesticks too long")
        ticker = path.split("/candlesticks")[0].rsplit("/", 1)[1]
        rows = [c for c in self.candles.get(ticker, []) if start <= c["end_period_ts"] <= end]
        return self.ok({"ticker": ticker, "candlesticks": rows})

    def trades_page(self, path: str, params: Any) -> httpx.Response:
        low, high = int(params["min_ts"]), int(params["max_ts"])
        historical = path.startswith("/historical")

        def second(row: dict[str, Any]) -> int:
            return int(datetime.fromisoformat(row["created_time"]).timestamp())

        rows = [
            r
            for r in self.trades
            if low <= second(r) < high and (second(r) < self.cutoff) == historical
        ]
        rows.sort(key=lambda r: (r["created_time"], r["trade_id"]), reverse=True)
        return self.paged(rows, "trades", params, int(params.get("limit", 100)))

    def paged(self, rows: list[dict[str, Any]], key: str, params: Any, size: int) -> httpx.Response:
        size = min(size, int(params.get("limit", size)))
        offset = int(params.get("cursor") or 0)
        page = rows[offset : offset + size]
        more = offset + size < len(rows)
        return self.ok({key: page, "cursor": str(offset + size) if more else ""})

    @staticmethod
    def ok(body: Any) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(body).encode())


class Rig:
    """A downloader over the fake, with no real waiting, and the log lines it printed."""

    def __init__(self, directory: Path, kalshi: FakeKalshi, **overrides: Any) -> None:
        settings: dict[str, Any] = {
            "data_dir": directory,
            "since": date(2026, 10, 19),
            "page_size": 3,
            "part_rows": 5,
            "market_pages_per_file": 2,
            "trades_rate": 1000.0,  # the fake clients allow 1000 a second: no real waiting
            "live_rate": 1000.0,
        }
        settings.update(overrides)
        self.config = DownloadConfig(**settings)
        self.lines: list[str] = []

        async def no_sleep(seconds: float) -> None:
            return None

        def client() -> KalshiPublicClient:
            cfg = KalshiConfig(
                environment=Environment.PRODUCTION,
                rate_limit_per_second=1000,
                burst_requests=1000,
                max_retries=3,
            )
            return KalshiPublicClient(
                cfg, transport=kalshi.transport(), sleep=no_sleep, jitter=lambda: 1.0
            )

        self.downloader = Downloader(
            self.config,
            trades_client=client(),
            live_client=client(),
            clock=lambda: NOW,
            log=self.lines.append,
        )
