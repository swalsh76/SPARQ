"""Rules for the middle-tier test (S1.1): filters on how busy a series is, how long the market had
been quiet before the trade, and the time of day, each scored for the maker or the taker.

Every feature uses only what was known before the trade it describes:

* ``busy`` is the median contracts per market of the trade's series, measured on the **fit part
  only**, so a validate or test trade never sees its own market's volume;
* ``quiet_s`` is the seconds since the market's previous trade (null for its first trade);
* ``ny_hour`` is the trade's hour in New York.

A maker rule is scored by the strict fill rule with the conservative maker fee (K0.5's D6 and D13);
a taker rule by the taker edge after fee. The pooled figures are weighted by one over each
category's share, and events are resampled within their category (the sampling design's strata).
The rules and every setting are locked by hash in the ledger before the test part is read.
"""

import json
from dataclasses import asdict, dataclass
from typing import Any

import polars as pl

from sparq_analytics.study.bootstrap import EMPTY, Estimate, stratified_bootstrap
from sparq_analytics.study.config import StudyConfig, sha256_of
from sparq_analytics.study.segments import PreregistrationError
from sparq_analytics.study.verdict import outcome

QUIET_LIMIT_S = 3600  # a market quiet for an hour or more is treated as stale
PACES = ("any", "human", "never_stale", "first_trade")
SIDES = ("maker", "taker")
MEASURES = {
    # name: (numerator, denominator), in K0.5's units
    "maker": ("e_mk_pess_cons", "c_pess"),
    "maker_optimistic": ("e_mk_opt_cons", "c"),
    "taker": ("e_taker", "c"),
}


def busy_table(fit: pl.LazyFrame) -> pl.DataFrame:
    """Per series, the median contracts per market in the fit part."""
    return (
        fit.group_by("series_ticker", "ticker")
        .agg((pl.col("c").sum() / 100).alias("contracts"))
        .group_by("series_ticker")
        .agg(pl.col("contracts").median().alias("busy"))
        .collect()
    )


def add_trade_features(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Add ``quiet_s``, ``first`` and ``ny_hour`` from each trade's UTC time ``t``.

    The one definition of the features a rule reads, for the study and for the live data alike.
    Trades of a market at the same time keep the frame's order, so a caller that needs a fixed
    order sorts first.
    """
    previous = pl.col("t").shift(1).over("ticker", order_by="t")
    return frame.with_columns(
        (pl.col("t") - previous).dt.total_seconds(fractional=True).alias("quiet_s"),
        previous.is_null().alias("first"),
        pl.col("t").dt.convert_time_zone("America/New_York").dt.hour().alias("ny_hour"),
    )


def add_rule_features(frame: pl.LazyFrame, busy: pl.DataFrame) -> pl.LazyFrame:
    """Add ``busy``, ``quiet_s``, ``first`` and ``ny_hour`` to a part of the study frame.

    A market belongs to one event and an event to one part, so the previous trade of a market is
    always in the same part.
    """
    return add_trade_features(frame.join(busy.lazy(), on="series_ticker", how="left"))


@dataclass(frozen=True, slots=True)
class Rule:
    """A named filter and the side it is scored for.

    ``busy_lo`` and ``busy_hi`` bound the series' median contracts per market (upper exclusive;
    None is open); ``pace`` is ``any``, ``human`` (1 second to an hour after the previous trade),
    ``never_stale`` (neither a first trade nor one after an hour of quiet) or ``first_trade``;
    ``ny_hours`` is a ``(from, to)`` hour range in New York, ``to`` exclusive; ``price_lo_e6`` and
    ``price_hi_e6`` bound the trade's yes price in millionths of a dollar (upper exclusive);
    ``quiet_lo_s`` and ``quiet_hi_s`` (S1.1b) bound the seconds since the market's previous trade
    (upper exclusive), which leaves out first trades, and are used only with ``pace="any"``.
    """

    name: str
    side: str
    busy_lo: float | None = None
    busy_hi: float | None = None
    pace: str = "any"
    category: str | None = None
    ny_hours: tuple[int, int] | None = None
    price_lo_e6: int = 0
    price_hi_e6: int = 1_000_001
    exploratory: bool = False
    quiet_lo_s: float | None = None
    quiet_hi_s: float | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a rule needs a name")
        if self.side not in SIDES:
            raise ValueError(f"side is one of {SIDES}")
        if self.pace not in PACES:
            raise ValueError(f"pace is one of {PACES}")
        if self.busy_lo is not None and self.busy_hi is not None and self.busy_lo >= self.busy_hi:
            raise ValueError("an empty busy range")
        if self.ny_hours is not None:
            start, end = self.ny_hours
            if not 0 <= start < end <= 24:
                raise ValueError("New York hours are 0 <= from < to <= 24")
        if not 0 <= self.price_lo_e6 < self.price_hi_e6:
            raise ValueError("an empty price range")
        if self.quiet_lo_s is not None or self.quiet_hi_s is not None:
            if self.pace != "any":
                raise ValueError("quiet bounds replace the pace; use them with pace='any'")
            low, high = self.quiet_lo_s or 0.0, self.quiet_hi_s
            if low < 0 or (high is not None and low >= high):
                raise ValueError("an empty quiet range")

    def expr(self) -> pl.Expr:
        keep = (pl.col("p") >= self.price_lo_e6) & (pl.col("p") < self.price_hi_e6)
        if self.busy_lo is not None:
            keep &= pl.col("busy") >= self.busy_lo
        if self.busy_hi is not None:
            keep &= pl.col("busy") < self.busy_hi
        if self.pace == "human":
            keep &= (
                ~pl.col("first") & (pl.col("quiet_s") >= 1) & (pl.col("quiet_s") < QUIET_LIMIT_S)
            )
        elif self.pace == "never_stale":
            keep &= ~pl.col("first") & (pl.col("quiet_s") < QUIET_LIMIT_S)
        elif self.pace == "first_trade":
            keep &= pl.col("first")
        if self.quiet_lo_s is not None or self.quiet_hi_s is not None:
            keep &= ~pl.col("first") & (pl.col("quiet_s") >= (self.quiet_lo_s or 0.0))
            if self.quiet_hi_s is not None:
                keep &= pl.col("quiet_s") < self.quiet_hi_s
        if self.category is not None:
            keep &= pl.col("category") == self.category
        if self.ny_hours is not None:
            keep &= (pl.col("ny_hour") >= self.ny_hours[0]) & (pl.col("ny_hour") < self.ny_hours[1])
        # an unknown feature (a series that is not in the fit part) never matches
        return keep.fill_null(False)

    def locked(self) -> dict[str, Any]:
        """The rule as the digest sees it. Fields added after S1.1 are left out while unset, so a
        rule registered before they existed keeps its digest (S1.1's lock stays valid)."""
        body = asdict(self)
        for name in _ADDED_AFTER_S1_1:
            if body[name] is None:
                del body[name]
        return body

    def definition(self) -> dict[str, Any]:
        """Everything that decides which trades count, without the name (for duplicate checks)."""
        body = self.locked()
        body.pop("name")
        return body


_ADDED_AFTER_S1_1 = ("quiet_lo_s", "quiet_hi_s")


def rules_from_json(data: dict[str, Any]) -> tuple[Rule, ...]:
    """The rules of a rules file; an item's ``reason`` is kept in the file, not in the rule."""
    rules = []
    for item in data["rules"]:
        fields = {k: v for k, v in item.items() if k != "reason"}
        if fields.get("ny_hours") is not None:
            fields["ny_hours"] = tuple(fields["ny_hours"])
        rules.append(Rule(**fields))
    return tuple(rules)


@dataclass(frozen=True, slots=True)
class RulePrereg:
    """The named rules and the settings they were named under."""

    rules: tuple[Rule, ...]
    config: StudyConfig

    def __post_init__(self) -> None:
        if not self.rules:
            raise PreregistrationError("name at least one rule")
        if all(r.exploratory for r in self.rules):
            raise PreregistrationError("name at least one rule that is not exploratory")
        if len(self.rules) > self.config.max_segments:
            raise PreregistrationError(
                f"{len(self.rules)} rules named; at most {self.config.max_segments}"
            )
        names = [r.name for r in self.rules]
        if len(set(names)) != len(names):
            raise PreregistrationError("a rule name is used twice")
        definitions = [json.dumps(r.definition(), sort_keys=True) for r in self.rules]
        if len(set(definitions)) != len(definitions):
            raise PreregistrationError("two rules select the same trades")

    def digest(self) -> str:
        body = {
            "kind": "rules",
            "rules": [r.locked() for r in self.rules],
            "config": self.config.digest(),
        }
        return sha256_of(body)

    @property
    def size(self) -> int:
        return len(self.rules)

    def alpha(self) -> float:
        """The one-sided tail each rule is tested at: the family's, over the deciding rules."""
        return self.config.family_alpha / sum(1 for r in self.rules if not r.exploratory)


@dataclass(frozen=True, slots=True)
class RuleResult:
    rule: Rule
    trades: int
    estimates: dict[str, Estimate]  # "decides" plus the other measures, for context
    go: bool
    short_of_events: bool


@dataclass(frozen=True, slots=True)
class RuleVerdict:
    outcome: str
    tail: float
    results: tuple[RuleResult, ...]


def evaluate_rule(frame: pl.LazyFrame, rule: Rule, config: StudyConfig, tail: float) -> RuleResult:
    """Score one rule at a one-sided ``tail`` on a part of the study frame that carries the rule
    features (a registration passes its corrected tail; a scan its nominal one)."""
    columns = sorted({name for pair in MEASURES.values() for name in pair})
    # each event's sums are weighted by one over its category's share; group_by order varies
    # between runs, so the events are sorted before the resampling sees them
    per_event = (
        frame.filter(rule.expr())
        .group_by(pl.col("category").fill_null("unknown"), "event_ticker")
        .agg(
            pl.len().alias("_trades"),
            *[(pl.col(c).cast(pl.Float64) * pl.col("w")).sum().alias(c) for c in columns],
        )
        .sort("category", "event_ticker")
        .collect()
    )
    strata = per_event["category"].to_numpy().astype(str)
    estimates: dict[str, Estimate] = {}
    for name, (numerator, denominator) in MEASURES.items():
        if per_event.height == 0:
            estimates[name] = EMPTY
            continue
        estimates[name] = stratified_bootstrap(
            per_event[numerator].to_numpy(),
            per_event[denominator].to_numpy(),
            strata,
            tail=tail,
            resamples=config.resamples,
            seed=config.seed,
        )
    decides = estimates[rule.side]
    short = decides.events < config.min_events
    return RuleResult(
        rule=rule,
        trades=int(per_event["_trades"].sum()) if per_event.height else 0,
        estimates={"decides": decides, **estimates},
        go=not short and decides.lower_e6 > 0,
        short_of_events=short,
    )


def decide(results: tuple[RuleResult, ...]) -> str:
    """GO if a deciding rule clears zero over enough events; inconclusive if one is short."""
    return outcome((r.go, r.short_of_events) for r in results if not r.rule.exploratory)


def evaluate_rules(frame: pl.LazyFrame, prereg: RulePrereg) -> RuleVerdict:
    tail = prereg.alpha()
    results = tuple(evaluate_rule(frame, rule, prereg.config, tail) for rule in prereg.rules)
    return RuleVerdict(decide(results), tail, results)
