"""A study session: the prepared frame, with the test part reachable only through the lock.

``fit`` and ``validate`` may be looked at freely, to choose the segments. The test part is not
exposed: :meth:`Study.open_test` records the pre-registration in the ledger (or refuses a
different one), and only then do :meth:`Study.verdict` and :meth:`Study.ladder` read it.
"""

from pathlib import Path

import polars as pl

from sparq_analytics.study import ladder as ladder_module
from sparq_analytics.study.config import StudyConfig
from sparq_analytics.study.dataset import used
from sparq_analytics.study.edge import add_edges
from sparq_analytics.study.rules import (
    RulePrereg,
    RuleVerdict,
    add_rule_features,
    busy_table,
    evaluate_rule,
    evaluate_rules,
)
from sparq_analytics.study.scan import Grid, ScanRow, variants
from sparq_analytics.study.segments import Prereg, PreregLedger, Registration, add_bands
from sparq_analytics.study.split import assign_parts
from sparq_analytics.study.verdict import Verdict, evaluate


class Study:
    def __init__(self, tagged: pl.LazyFrame, config: StudyConfig, ledger: Path) -> None:
        self.config = config
        self._ledger = PreregLedger(ledger)
        banded = add_bands(used(tagged), config)
        parted, self.boundaries = assign_parts(banded, config)
        self._frame = add_edges(parted)
        self._busy_table: pl.DataFrame | None = None

    @property
    def fit(self) -> pl.LazyFrame:
        return self._frame.filter(pl.col("part") == "fit")

    @property
    def validate(self) -> pl.LazyFrame:
        return self._frame.filter(pl.col("part") == "validate")

    def open_test(self, prereg: Registration) -> pl.LazyFrame:
        """The test part, after the pre-registration is locked in the ledger."""
        self._ledger.open_test(prereg)
        return self._frame.filter(pl.col("part") == "test")

    def verdict(self, prereg: Prereg) -> Verdict:
        return evaluate(self.open_test(prereg), prereg)

    def ladder(self, prereg: Prereg) -> ladder_module.Ladder:
        return ladder_module.ladder(self.fit, self.open_test(prereg))

    def busy(self) -> pl.DataFrame:
        """Each series' median contracts per market, from the **fit part only** (no look-ahead)."""
        if self._busy_table is None:
            self._busy_table = busy_table(self.fit)
        return self._busy_table

    def rules_on_validate(self, prereg: RulePrereg) -> RuleVerdict:
        """The rules scored on the validate part, to choose them; nothing is locked."""
        return evaluate_rules(add_rule_features(self.validate, self.busy()), prereg)

    def scan(self, grid: Grid) -> tuple[ScanRow, ...]:
        """Every variant of ``grid`` on the fit and the validate part, at the nominal tail (S1.1b);
        the test part is not read."""
        fit = add_rule_features(self.fit, self.busy())
        validate = add_rule_features(self.validate, self.busy())
        tail = self.config.family_alpha
        rows = []
        for rule, changes in variants(grid):
            on_fit = evaluate_rule(fit, rule, self.config, tail).estimates["decides"]
            on_validate = evaluate_rule(validate, rule, self.config, tail).estimates["decides"]
            rows.append(ScanRow(rule, changes, on_fit, on_validate))
        return tuple(rows)

    def rules_verdict(self, prereg: RulePrereg) -> RuleVerdict:
        """The rules scored on the test part, after they are locked in the ledger."""
        return evaluate_rules(add_rule_features(self.open_test(prereg), self.busy()), prereg)
