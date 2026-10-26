"""Small summaries for the counters the server's programs report."""

from collections.abc import Iterable


def percentile(values: Iterable[float], fraction: float) -> float:
    """The value at ``fraction`` of the way through the sorted values (0.0 when there are none):
    the nearest-rank kind, so 0.5 is the median and 1.0 the largest."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return float(ordered[min(len(ordered) - 1, int(fraction * len(ordered)))])
