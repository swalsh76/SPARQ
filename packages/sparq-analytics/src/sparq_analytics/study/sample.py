"""Sampling by event (D16, amended 2026-10-25): every trade of a hash-chosen share of the events,
with a share of its own for each category.

The run cannot hold every trade in memory, and thinning trades would break the events apart.
Choosing whole events keeps the events independent, so the clustered intervals stay valid; only
their number, and so the power, falls. The choice is a hash of the seed and the event ticker, taken
with ``hashlib`` and not polars' own hash, which is not promised to be stable between versions.
"""

import hashlib
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass

DEFAULT_CEILING = 40_000_000  # trades loaded at once: about 18 GB of the analytics node's 36 GB
_MIN_SHARE = 0.0001


def event_fraction(event: str, seed: int) -> float:
    """A fixed number in [0, 1) for this event under this seed."""
    digest = hashlib.blake2b(f"{seed}:{event}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


def chosen_events(events: Iterable[str], share: float, seed: int) -> list[str]:
    """The events whose fraction falls below ``share``, in the order given."""
    if share >= 1.0:
        return list(events)
    return [event for event in events if event_fraction(event, seed) < share]


def share_for(trades: int, ceiling: int = DEFAULT_CEILING) -> float:
    """The largest share, to four decimals, that keeps about ``ceiling`` trades or fewer."""
    if trades <= ceiling:
        return 1.0
    return max(_MIN_SHARE, int(ceiling / trades * 10_000) / 10_000)


def chosen_events_by_category(
    events: Iterable[tuple[str, str]], share_of: Callable[[str], float], seed: int
) -> list[str]:
    """The ``(event, category)`` pairs' events whose fraction falls below their category's share.

    A smaller share is inside a larger one for every category, as with one share for all."""
    return [event for event, category in events if event_fraction(event, seed) < share_of(category)]


@dataclass(frozen=True, slots=True)
class CategoryStat:
    category: str
    events: int
    trades: int


TARGET_EVENTS = 4_000
"""Events a category needs in the whole window for its test part (15% of them) to hold about 600
events, twice the 300 a segment needs, so a segment covering half of them still qualifies."""


def _share(stat: CategoryStat, base: float, scale: float, target: int) -> float:
    need = scale * target / stat.events if stat.events else 1.0
    return min(1.0, max(base, need))


def _cost(stats: list[CategoryStat], base: float, scale: float, target: int) -> float:
    return sum(s.trades * _share(s, base, scale, target) for s in stats)


def plan_shares(
    stats: list[CategoryStat], ceiling: int = DEFAULT_CEILING, target_events: int = TARGET_EVENTS
) -> tuple[float, dict[str, float]]:
    """A base share and the larger shares of the categories that need them (D16 amended).

    A category with few events needs a high share to reach ``target_events``, and is cheap to load
    in full because its events hold few trades; a category with many events and trades gets the
    base share, the largest that keeps the whole sample within ``ceiling`` trades. If even the
    smallest base cannot keep the ceiling with the targets met, the targets are scaled down evenly
    until it does, so the ceiling is never exceeded.
    """
    if sum(s.trades for s in stats) <= ceiling:
        return 1.0, {}
    scale = 1.0
    if _cost(stats, _MIN_SHARE, 1.0, target_events) > ceiling:
        low, high = 0.0, 1.0
        for _ in range(60):
            mid = (low + high) / 2
            if _cost(stats, _MIN_SHARE, mid, target_events) <= ceiling:
                low = mid
            else:
                high = mid
        scale, base = low, _MIN_SHARE
    else:
        low, high = _MIN_SHARE, 1.0
        for _ in range(60):
            mid = (low + high) / 2
            if _cost(stats, mid, 1.0, target_events) <= ceiling:
                low = mid
            else:
                high = mid
        base = max(_MIN_SHARE, int(low * 10_000) / 10_000)
    overrides: dict[str, float] = {}
    for stat in stats:
        share = min(
            1.0, math.ceil(_share(stat, base, scale, target_events) * 10_000 - 1e-9) / 10_000
        )
        if share > base:
            overrides[stat.category] = share
    return base, overrides
