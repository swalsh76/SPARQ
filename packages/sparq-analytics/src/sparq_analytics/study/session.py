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
from sparq_analytics.study.segments import Prereg, PreregLedger, add_bands
from sparq_analytics.study.split import assign_parts
from sparq_analytics.study.verdict import Verdict, evaluate


class Study:
    def __init__(self, tagged: pl.LazyFrame, config: StudyConfig, ledger: Path) -> None:
        self.config = config
        self._ledger = PreregLedger(ledger)
        banded = add_bands(used(tagged), config)
        parted, self.boundaries = assign_parts(banded, config)
        self._frame = add_edges(parted)

    @property
    def fit(self) -> pl.LazyFrame:
        return self._frame.filter(pl.col("part") == "fit")

    @property
    def validate(self) -> pl.LazyFrame:
        return self._frame.filter(pl.col("part") == "validate")

    def open_test(self, prereg: Prereg) -> pl.LazyFrame:
        """The test part, after the pre-registration is locked in the ledger."""
        self._ledger.open_test(prereg)
        return self._frame.filter(pl.col("part") == "test")

    def verdict(self, prereg: Prereg) -> Verdict:
        return evaluate(self.open_test(prereg), prereg)

    def ladder(self, prereg: Prereg) -> ladder_module.Ladder:
        return ladder_module.ladder(self.fit, self.open_test(prereg))
