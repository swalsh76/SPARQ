"""Intervals that resample whole events (D7).

Trades inside one event share an outcome and a cause, so they are not independent. The unit that
is resampled is the event: its sums of edge and of contracts are drawn with replacement, and the
ratio of the sums is the edge per contract.

The interval is the studentized one (bootstrap-t), not the plain percentile one. Edge payoffs are
skewed (a maker earns a little most of the time and loses a lot rarely), and on such data the
percentile interval at the far tails D11 asks for fires several times too often: measured on a
skewed payoff with 80 events, 3.9% against a nominal 0.6% (K0.5.5). The studentized interval
divides each resample's error by its own cluster-robust standard error and stayed at or below the
nominal rate in the same test.
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

_CELLS_PER_CHUNK = 20_000_000  # bound the size of one block of resampling weights


@dataclass(frozen=True, slots=True)
class Estimate:
    """Edge per contract in millionths of a dollar, with the lower and upper bound at the tail
    asked for, over ``events`` independent events holding ``contracts_e2`` hundredths."""

    point_e6: float
    lower_e6: float
    upper_e6: float
    events: int
    contracts_e2: int


EMPTY = Estimate(float("nan"), float("nan"), float("nan"), 0, 0)


def _studentized(
    num: NDArray[np.float64],
    den: NDArray[np.float64],
    draw: Callable[[np.random.Generator, int], NDArray[np.float64]],
    *,
    tail: float,
    resamples: int,
    seed: int,
) -> Estimate:
    """The ratio of sums with a studentized interval; ``draw`` gives a block of resample counts.

    The standard error is the cluster-robust one for a ratio of sums: the root of the summed
    squared residuals ``numerator - ratio x denominator``, over the total denominator.
    """
    n = int(num.size)
    if n == 0:
        return EMPTY
    total = float(den.sum())
    point = float(num.sum() / total)
    contracts = int(total)
    se = float(np.sqrt(((num - point * den) ** 2).sum()) / total)
    if se == 0.0:
        return Estimate(point, point, point, n, contracts)
    num_sq, num_den, den_sq = num * num, num * den, den * den
    rng = np.random.default_rng(seed)
    chunk = max(1, _CELLS_PER_CHUNK // n)
    statistics: list[NDArray[np.float64]] = []
    remaining = resamples
    while remaining > 0:
        size = min(chunk, remaining)
        counts = draw(rng, size)
        top, bottom = counts @ num, counts @ den
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = top / bottom
            squares = (
                counts @ num_sq - 2 * ratio * (counts @ num_den) + ratio**2 * (counts @ den_sq)
            )
            error = np.sqrt(np.maximum(squares, 0.0)) / bottom
            statistics.append(np.where((bottom > 0) & (error > 0), (ratio - point) / error, np.nan))
        remaining -= size
    draws = np.concatenate(statistics)
    high, low = np.nanquantile(draws, [1.0 - tail, tail])
    return Estimate(point, float(point - se * high), float(point - se * low), n, contracts)


def cluster_bootstrap(
    numerator: NDArray[np.float64],
    denominator: NDArray[np.float64],
    *,
    tail: float,
    resamples: int,
    seed: int,
) -> Estimate:
    """The ratio of sums over events, with a studentized interval at ``tail`` on each side.

    ``numerator`` and ``denominator`` hold one entry per event; only events with a positive
    denominator count as independent events that took part.
    """
    keep = denominator > 0
    num, den = numerator[keep], denominator[keep]
    n = int(num.size)
    probabilities = np.full(n, 1.0 / n) if n else np.empty(0)

    def draw(rng: np.random.Generator, size: int) -> NDArray[np.float64]:
        return rng.multinomial(n, probabilities, size=size).astype(np.float64)

    return _studentized(num, den, draw, tail=tail, resamples=resamples, seed=seed)


def stratified_bootstrap(
    numerator: NDArray[np.float64],
    denominator: NDArray[np.float64],
    strata: NDArray[np.str_],
    *,
    tail: float,
    resamples: int,
    seed: int,
) -> Estimate:
    """As :func:`cluster_bootstrap`, but events are resampled within their stratum.

    The sample takes a different share of each category (D16 as amended), so each category is its
    own stratum: a resample keeps every stratum's number of events, as the sampling design did. The
    caller weights each event's sums by one over its stratum's share. With a single stratum this is
    exactly :func:`cluster_bootstrap`.
    """
    keep = denominator > 0
    num, den, groups = numerator[keep], denominator[keep], strata[keep]
    names = np.unique(groups)
    if names.size <= 1:
        return cluster_bootstrap(num, den, tail=tail, resamples=resamples, seed=seed)
    order = np.argsort(groups, kind="stable")
    num, den, groups = num[order], den[order], groups[order]
    sizes = [int((groups == name).sum()) for name in names]

    def draw(rng: np.random.Generator, size: int) -> NDArray[np.float64]:
        blocks = [rng.multinomial(k, np.full(k, 1.0 / k), size=size) for k in sizes]
        return np.hstack(blocks).astype(np.float64)

    return _studentized(num, den, draw, tail=tail, resamples=resamples, seed=seed)
