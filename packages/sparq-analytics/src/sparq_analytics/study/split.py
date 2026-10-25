"""The split by time (D12): fit, validate, test, by event and ordered by the event's close time.

The boundaries are either fixed UTC dates (``fit_end`` and ``validate_end`` in the settings, which
is how the real run is done: they do not move as trade days arrive) or, when no dates are given,
close-time quantiles taken by event count.

An event in validate or test that opened before the latest close of the part before it was trading
while some outcomes of that part were still unknown, so it is dropped from both (the embargo):
everything that is left opened after the earlier part's last label was known. With fixed dates the
latest close of the part before is taken to be its boundary date.
"""

from dataclasses import dataclass

import polars as pl

from sparq_analytics.study.config import StudyConfig

PARTS = ("fit", "validate", "test")


class SplitError(Exception):
    """Too few events to split."""


@dataclass(frozen=True, slots=True)
class Boundaries:
    fit_end: object  # events closing before this are the fit part
    validate_end: object  # ... before this, the validate part; the rest are the test part
    events: dict[str, int]  # events per part, and "dropped" for the embargoed ones


def assign_parts(frame: pl.LazyFrame, config: StudyConfig) -> tuple[pl.LazyFrame, Boundaries]:
    """Add a ``part`` column (fit, validate, test, or null for an embargoed event)."""
    events = (
        frame.select("event_ticker", "ev_open", "ev_close")
        .unique(subset="event_ticker")
        .sort("ev_close")
        .collect()
    )
    n = events.height
    dates = config.split_dates()
    if dates is not None:
        if n == 0:
            raise SplitError("no events to split")
        fit_end, validate_end = dates
        fit_last, validate_last = fit_end, validate_end
    else:
        if n < 3:
            raise SplitError(f"{n} events cannot be split in three")
        first, second, _ = config.fractions
        fit_count = min(n - 2, max(1, int(first * n)))
        validate_count = min(n - fit_count - 1, max(1, int((first + second) * n) - fit_count))
        fit_end = events["ev_close"][fit_count]
        validate_end = events["ev_close"][fit_count + validate_count]
        fit_last = events["ev_close"][fit_count - 1]
        validate_last = events["ev_close"][fit_count + validate_count - 1]
    close, opened = pl.col("ev_close"), pl.col("ev_open")
    part = (
        pl.when(close < fit_end)
        .then(pl.lit("fit"))
        .when(close < validate_end)
        .then(pl.when(opened >= fit_last).then(pl.lit("validate")))
        .otherwise(pl.when(opened >= validate_last).then(pl.lit("test")))
    )
    labelled = events.with_columns(part.alias("part"))
    counts = {name: 0 for name in PARTS} | {"dropped": 0}
    for name, count in labelled.group_by(pl.col("part").fill_null("dropped")).len().iter_rows():
        counts[name] = int(count)
    return frame.with_columns(part.alias("part")), Boundaries(fit_end, validate_end, counts)
