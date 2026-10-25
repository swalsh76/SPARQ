"""Back-off for a long download: slow down at once after a 429, creep back up when it is quiet.

The rates were measured live (BUILD-LOG K0.5.4): historical trades held 10 requests a second for a
full minute, while the live trades endpoint and the markets listings allowed about 4. The limits
are Kalshi's and can change, so the controller watches the client's 429 counter after every page:

* on a new 429 it halves the rate (never below ``min_rate``);
* after ``recover_after`` quiet seconds it adds ``recover_step`` back, up to ``max_rate``;
* if there are ``give_up_events`` separate 429s inside ``give_up_window`` seconds it raises
  :class:`RateLimitPersisting`, so an unattended run stops and says why instead of hammering.
"""

import time
from collections import deque
from collections.abc import Callable

from sparq_core.rest import KalshiPublicClient


class RateLimitPersisting(Exception):
    """Kalshi keeps answering 429 even at the slowest rate."""


class RateController:
    def __init__(
        self,
        client: KalshiPublicClient,
        max_rate: float,
        *,
        request_cost: int = 10,
        min_rate: float = 1.0,
        factor: float = 0.5,
        recover_after: float = 60.0,
        recover_step: float = 0.5,
        give_up_events: int = 6,
        give_up_window: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0 < min_rate <= max_rate:
            raise ValueError("min_rate must be positive and at most max_rate")
        if not 0 < factor < 1:
            raise ValueError("factor must be between 0 and 1")
        self._client = client
        self._max = max_rate
        self._min = min_rate
        self._cost = request_cost
        self._factor = factor
        self._recover_after = recover_after
        self._step = recover_step
        self._give_up_events = give_up_events
        self._give_up_window = give_up_window
        self._clock = clock
        self._rate = max_rate
        self._seen = client.stats.rate_limited
        self._events: deque[float] = deque()
        self._last_change = clock()
        self.history: list[tuple[float, float]] = [(self._last_change, max_rate)]
        self.slowdowns = 0

    @property
    def rate(self) -> float:
        return self._rate

    def _apply(self, rate: float, now: float) -> None:
        self._rate = rate
        self._client.bucket.set_rate(rate * self._cost, capacity=max(rate, 1.0) * self._cost)
        self._last_change = now
        self.history.append((now, rate))

    def update(self) -> None:
        """Call after every page (or request). Cheap when nothing has happened."""
        now = self._clock()
        count = self._client.stats.rate_limited
        if count > self._seen:
            self._seen = count
            self.slowdowns += 1
            self._events.append(now)
            while self._events and now - self._events[0] > self._give_up_window:
                self._events.popleft()
            if len(self._events) >= self._give_up_events:
                raise RateLimitPersisting(
                    f"{len(self._events)} rate-limit responses in {self._give_up_window:.0f} s "
                    f"(now at {self._rate:.2f} requests a second)"
                )
            self._apply(max(self._min, self._rate * self._factor), now)
        elif self._rate < self._max and now - self._last_change >= self._recover_after:
            self._apply(min(self._max, self._rate + self._step), now)
