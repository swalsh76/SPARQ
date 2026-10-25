"""A one-off measurement of Kalshi's unauthenticated rate limit (GET only, no key).

    $env:KALSHI_INTEGRATION = '1'; $env:SPARQ_LIVE_RATE_PROBE = '1'
    $env:SPARQ_LIVE_SUMMARY = "$env:TEMP\\sparq-live-summary.txt"
    python -m uv run pytest packages/sparq-core/tests/test_live_ratelimit.py -q

The owner approved a ramp of 5, 10, 15 and 20 requests a second for 5 seconds each (2026-10-21),
stopping at the first 429. It runs only when ``SPARQ_LIVE_RATE_PROBE=1`` as well, so a routine live
run never does it. The downloader itself stays capped at 5 requests a second.
"""

import asyncio
import os
import statistics
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from sparq_core.config import Environment, KalshiConfig
from sparq_core.rest import KalshiAPIError, KalshiPublicClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("SPARQ_LIVE_RATE_PROBE") != "1", reason="set SPARQ_LIVE_RATE_PROBE=1 to run"
    ),
]

LINES: list[str] = []
SECONDS = 5.0


@dataclass
class Result:
    rate: int
    sent: int = 0
    ok: int = 0
    limited: int = 0
    other: list[int] = field(default_factory=list)
    first_429_after: int | None = None  # requests sent before the first 429
    headers_on_429: dict[str, str] = field(default_factory=dict)
    body_on_429: str = ""
    elapsed: float = 0.0


@pytest.fixture(scope="module", autouse=True)
def summary() -> Iterator[None]:
    yield
    target = os.environ.get("SPARQ_LIVE_SUMMARY")
    if target:
        Path(target).write_text("\n".join(LINES) + "\n", encoding="utf-8")


async def run_rate(
    path: str, params: dict[str, Any], rate: int, seconds: float = SECONDS
) -> Result:
    """Send requests at ``rate`` a second for ``seconds``, never retrying, and stop at a 429."""
    config = KalshiConfig(
        environment=Environment.PRODUCTION,
        rate_limit_per_second=rate,
        burst_requests=rate,
        max_retries=0,
    )
    result = Result(rate)
    stop = asyncio.Event()
    async with KalshiPublicClient(config) as client:

        async def one() -> None:
            if stop.is_set():
                return
            result.sent += 1
            number = result.sent
            try:
                await client.get_json(path, params)
                result.ok += 1
            except KalshiAPIError as exc:
                if exc.status == 429:
                    result.limited += 1
                    if result.first_429_after is None:
                        result.first_429_after = number
                        result.headers_on_429 = exc.headers
                        result.body_on_429 = exc.body
                    stop.set()
                else:
                    result.other.append(exc.status)

        begin = time.monotonic()
        await asyncio.gather(*(one() for _ in range(int(rate * seconds))))
        result.elapsed = time.monotonic() - begin
    return result


def report(label: str, r: Result) -> None:
    LINES.append(
        f"{label} at {r.rate}/s: sent {r.sent}, ok {r.ok}, 429 {r.limited}, other {r.other}; "
        f"{r.ok / r.elapsed:.1f} ok/s over {r.elapsed:.1f} s (the first {r.rate} go out as a burst)"
    )
    if r.limited:
        interesting = {k: v for k, v in r.headers_on_429.items() if "retry" in k or "limit" in k}
        LINES.append(
            f"  first 429 after {r.first_429_after} requests; body {r.body_on_429[:80]!r}; "
            f"retry/limit headers: {interesting or 'none'}"
        )


async def ramp(label: str, path: str, params: dict[str, Any], rates: list[int]) -> bool:
    """Returns True if a 429 was hit."""
    for rate in rates:
        result = await run_rate(path, params, rate)
        report(label, result)
        if result.limited:
            await asyncio.sleep(5)  # the docs promise no cooldown: check by trying again
            async with KalshiPublicClient(KalshiConfig(environment=Environment.PRODUCTION)) as c:
                await c.get_json(path, params)
            LINES.append(f"  one request 5 s after the 429 succeeded: True ({label})")
            return True
        await asyncio.sleep(2)
    LINES.append(f"{label}: no 429 up to {rates[-1]} requests a second")
    return False


async def test_a_cheap_endpoint_up_to_twenty_a_second() -> None:
    hit = await ramp("exchange status", "/exchange/status", {}, [5, 10, 15, 20])
    LINES.append(f"exchange status: rate limit reached = {hit}")


async def test_a_trades_page_up_to_ten_a_second() -> None:
    # the endpoint the downloader will use most, with a full page: heavier, so a gentler ramp
    hit = await ramp(
        "historical trades page (1000 rows)", "/historical/trades", {"limit": 1000}, [5, 10]
    )
    LINES.append(f"historical trades: rate limit reached = {hit}")


async def test_the_latency_of_one_request_at_a_time() -> None:
    """One at a time: the figure is Kalshi's response time, not our own limiter's wait."""
    config = KalshiConfig(
        environment=Environment.PRODUCTION, rate_limit_per_second=20, burst_requests=20
    )
    async with KalshiPublicClient(config) as client:
        for label, path, params in (
            ("exchange status", "/exchange/status", {}),
            ("historical trades page (1000 rows)", "/historical/trades", {"limit": 1000}),
            ("live trades page (1000 rows)", "/markets/trades", {"limit": 1000}),
        ):
            times = []
            for _ in range(10):
                started = time.monotonic()
                await client.get_json(path, params)
                times.append(time.monotonic() - started)
                await asyncio.sleep(0.3)
            times.sort()
            median_ms = statistics.median(times) * 1000
            LINES.append(
                f"latency, one at a time, {label}: median {median_ms:.0f} ms, "
                f"slowest {times[-1] * 1000:.0f} ms (10 requests)"
            )


async def test_a_sustained_minute_at_ten_a_second_on_full_pages() -> None:
    """The check before a long download: does a limit appear over a longer window than 5 seconds?

    Ten requests a second for 60 seconds on full 1,000-row pages is about 600 requests (the
    owner asked for it on 2026-10-22). It stops at the first 429.
    """
    result = await run_rate("/historical/trades", {"limit": 1000}, 10, seconds=60)
    report("sustained 60 s, historical trades page (1000 rows)", result)
    LINES.append(
        f"sustained minute: {result.ok} of {result.sent} requests answered, "
        f"{result.limited} rate-limited, {len(result.other)} other errors"
    )
    assert result.sent == 600 or result.limited
