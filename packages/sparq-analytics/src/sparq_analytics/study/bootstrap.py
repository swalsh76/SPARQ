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
    denominator count as independent events that took part. The standard error is the
    cluster-robust one for a ratio of sums: the root of the summed squared residuals
    ``numerator - ratio x denominator``, over the total denominator.
    """
    keep = denominator > 0
    num, den = numerator[keep], denominator[keep]
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
    probabilities = np.full(n, 1.0 / n)
    statistics: list[NDArray[np.float64]] = []
    remaining = resamples
    while remaining > 0:
        size = min(chunk, remaining)
        counts = rng.multinomial(n, probabilities, size=size).astype(np.float64)
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
