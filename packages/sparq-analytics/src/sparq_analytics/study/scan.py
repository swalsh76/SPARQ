"""S1.1b: refine a rule on the fit and validate parts by a grid fixed in advance (PLAN D10).

The grid varies a base rule along three dimensions (the quiet window, the series' activity range
and the yes-price band); every variant is scored on the fit part and on the validate part at the
nominal one-sided tail, and all of them are reported. The choosing rule is fixed before the scan:

1. a variant is eligible only if its fit edge is positive and its validate edge is at least half
   of it (same sign, not a lucky fit);
2. the best is the eligible variant with the highest fit lower bound;
3. among the eligible variants within ``margin`` of the best's fit lower bound, the one with the
   fewest changes from the base rule wins (then the higher fit lower bound, then the name).

The test part is never read here.
"""

import dataclasses
from dataclasses import dataclass
from typing import Any

from sparq_analytics.study.bootstrap import Estimate
from sparq_analytics.study.rules import Rule

Band = tuple[float, float]
CHOICE_MARGIN_E6 = 5_000  # half a cent: variants this close to the best count as equally good


@dataclass(frozen=True, slots=True)
class Grid:
    base: Rule
    quiet: tuple[Band, ...]  # seconds since the previous trade, upper exclusive
    busy: tuple[Band, ...]  # median contracts per market, upper exclusive
    price: tuple[tuple[int, int], ...]  # yes price in millionths of a dollar, upper exclusive

    def __post_init__(self) -> None:
        if not (self.quiet and self.busy and self.price):
            raise ValueError("every dimension of the grid needs at least one value")
        if self.base.pace != "any" or self.base.quiet_lo_s is None:
            raise ValueError("the base rule states its quiet window with quiet bounds")
        base = (
            (self.base.quiet_lo_s, self.base.quiet_hi_s),
            (self.base.busy_lo, self.base.busy_hi),
            (self.base.price_lo_e6, self.base.price_hi_e6),
        )
        if base not in [(q, b, p) for q in self.quiet for b in self.busy for p in self.price]:
            raise ValueError("the base rule must be one of the grid's variants")


def grid_from_json(data: dict[str, Any]) -> Grid:
    base = {k: v for k, v in data["base"].items() if k != "reason"}
    return Grid(
        base=Rule(**base),
        quiet=tuple((float(lo), float(hi)) for lo, hi in data["quiet"]),
        busy=tuple((float(lo), float(hi)) for lo, hi in data["busy"]),
        price=tuple((int(lo), int(hi)) for lo, hi in data["price"]),
    )


def _label(value: float) -> str:
    return f"{value:g}"


def variants(grid: Grid) -> tuple[tuple[Rule, int], ...]:
    """Every variant with the number of dimensions in which it differs from the base rule."""
    base = grid.base
    out = []
    for q_lo, q_hi in grid.quiet:
        for b_lo, b_hi in grid.busy:
            for p_lo, p_hi in grid.price:
                name = (
                    f"quiet {_label(q_lo)}-{_label(q_hi)}s, busy {_label(b_lo)}-{_label(b_hi)}, "
                    f"price {p_lo // 10_000}-{p_hi // 10_000}c"
                )
                rule = dataclasses.replace(
                    base,
                    name=name,
                    quiet_lo_s=q_lo,
                    quiet_hi_s=q_hi,
                    busy_lo=b_lo,
                    busy_hi=b_hi,
                    price_lo_e6=p_lo,
                    price_hi_e6=p_hi,
                )
                changes = sum(
                    (
                        (q_lo, q_hi) != (base.quiet_lo_s, base.quiet_hi_s),
                        (b_lo, b_hi) != (base.busy_lo, base.busy_hi),
                        (p_lo, p_hi) != (base.price_lo_e6, base.price_hi_e6),
                    )
                )
                out.append((rule, changes))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class ScanRow:
    rule: Rule
    changes: int
    fit: Estimate
    validate: Estimate

    @property
    def eligible(self) -> bool:
        return self.fit.point_e6 > 0 and self.validate.point_e6 >= 0.5 * self.fit.point_e6


def choose(rows: tuple[ScanRow, ...], margin_e6: float = CHOICE_MARGIN_E6) -> ScanRow | None:
    """The variant the fixed choosing rule picks, or None when no variant is eligible."""
    eligible = [row for row in rows if row.eligible]
    if not eligible:
        return None
    best = max(row.fit.lower_e6 for row in eligible)
    close = [row for row in eligible if row.fit.lower_e6 >= best - margin_e6]
    return min(close, key=lambda row: (row.changes, -row.fit.lower_e6, row.rule.name))
