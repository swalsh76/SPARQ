"""The go rule (KAPS PLAN.md §6.9, with D6, D11 and D13) applied to the pre-registered segments.

GO needs, in at least one segment, a lower bound above zero at the segment's corrected tail, over
at least ``min_events`` independent events. For the taker that is the after-fee taker edge. For
the maker it is the pessimistic-fill edge with the conservative maker fee. An edge that shows only
under the optimistic fill rule is not a GO and makes the verdict inconclusive, as does a segment
with too few events. NO-GO is every segment having enough events and none having a positive bound.
"""

from collections.abc import Iterable
from dataclasses import dataclass

import polars as pl

from sparq_analytics.study.bootstrap import Estimate, cluster_bootstrap
from sparq_analytics.study.edge import MEASURES
from sparq_analytics.study.segments import Prereg, Segment

GO = "GO"
NO_GO = "NO-GO"
INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class SegmentResult:
    segment: Segment
    trades: int
    events: int
    estimates: dict[str, Estimate]
    taker_go: bool
    maker_go: bool
    optimistic_only: bool
    short_of_events: bool
    exploratory: bool


@dataclass(frozen=True, slots=True)
class Verdict:
    outcome: str
    tail: float
    segments: tuple[SegmentResult, ...]


def outcome(results: Iterable[tuple[bool, bool]]) -> str:
    """The verdict over deciding results, each given as (goes, unsure): GO if any goes, else
    INCONCLUSIVE if any is unsure (too few events, or an edge only an optimistic rule shows),
    else NO-GO. The segments here and S1.1's rules both decide by it."""
    results = list(results)
    if any(goes for goes, _ in results):
        return GO
    if any(unsure for _, unsure in results):
        return INCONCLUSIVE
    return NO_GO


def _positive(estimate: Estimate, minimum: int) -> bool:
    return estimate.events >= minimum and estimate.lower_e6 > 0


def evaluate_segment(test: pl.LazyFrame, segment: Segment, prereg: Prereg) -> SegmentResult:
    config = prereg.config
    columns = sorted({name for pair in MEASURES.values() for name in pair})
    per_event = (
        test.filter(segment.expr())
        .group_by("event_ticker")
        .agg(
            pl.len().alias("_trades"),
            *[pl.col(c).cast(pl.Int128).sum().cast(pl.Float64).alias(c) for c in columns],
        )
        .sort("event_ticker")  # group_by order varies between runs; the resampling must not
        .collect()
    )
    estimates = {}
    for name, (numerator, denominator) in MEASURES.items():
        estimates[name] = cluster_bootstrap(
            per_event[numerator].to_numpy(),
            per_event[denominator].to_numpy(),
            tail=prereg.alpha(),
            resamples=config.resamples,
            seed=config.seed,
        )
    minimum = config.min_events
    taker_go = _positive(estimates["taker"], minimum)
    maker_go = _positive(estimates["mk_pess_cons"], minimum)
    optimistic = _positive(estimates["mk_opt_cons"], minimum)
    short = per_event.height < minimum or estimates["mk_pess_cons"].events < minimum
    return SegmentResult(
        segment=segment,
        trades=int(per_event["_trades"].sum()) if per_event.height else 0,
        events=per_event.height,
        estimates=estimates,
        taker_go=taker_go,
        maker_go=maker_go,
        optimistic_only=optimistic and not maker_go,
        short_of_events=short,
        exploratory=segment.exploratory,
    )


def decide(results: tuple[SegmentResult, ...]) -> str:
    """The outcome over the deciding segments; exploratory ones are reported but never counted."""
    return outcome(
        (r.taker_go or r.maker_go, r.optimistic_only or r.short_of_events)
        for r in results
        if not r.exploratory
    )


def evaluate(test: pl.LazyFrame, prereg: Prereg) -> Verdict:
    results = tuple(evaluate_segment(test, s, prereg) for s in prereg.segments)
    return Verdict(decide(results), prereg.alpha(), results)
