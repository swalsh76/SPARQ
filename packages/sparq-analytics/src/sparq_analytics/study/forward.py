"""S1.1b: test one rule forward, on events that opened after the rule was fixed (PLAN D10).

Every event inside the forward window is the test part: there is no fit or validate part, because
the rule was chosen on older data. A series' activity (``busy``) is not re-measured on the forward
days: it is read from a **frozen file** written from the original fit part, and the file's hash is
part of the registration, so the forward test cannot shift the tiers it was registered with. The
test is opened through the same kind of ledger as every other test, once.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from sparq_analytics.study.config import StudyConfig
from sparq_analytics.study.dataset import used
from sparq_analytics.study.edge import add_edges
from sparq_analytics.study.rules import (
    Rule,
    RulePrereg,
    RuleVerdict,
    add_rule_features,
    evaluate_rules,
)
from sparq_analytics.study.segments import PreregistrationError, PreregLedger, add_bands


def write_busy(busy: pl.DataFrame, path: Path) -> str:
    """Freeze the per-series activity as JSON; returns its hash."""
    series = {str(s): float(b) for s, b in busy.sort("series_ticker").iter_rows()}
    text = json.dumps({"busy": series}, sort_keys=True, indent=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return busy_digest(path)


def busy_digest(path: Path) -> str:
    """The hash of a frozen activity file's parsed content (so whitespace never counts)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def read_busy(path: Path) -> pl.DataFrame:
    series = json.loads(path.read_text(encoding="utf-8"))["busy"]
    return pl.DataFrame(
        {"series_ticker": list(series), "busy": [float(v) for v in series.values()]},
        schema={"series_ticker": pl.String, "busy": pl.Float64},
    )


@dataclass(frozen=True, slots=True)
class ForwardPrereg:
    """One rule, the settings of the forward window, and the frozen activity it uses."""

    rule: Rule
    config: StudyConfig
    busy_hash: str

    def __post_init__(self) -> None:
        if self.rule.exploratory:
            raise PreregistrationError("the forward rule decides; it cannot be exploratory")
        if self.config.split_dates() is not None:
            raise PreregistrationError("a forward test has no fit or validate part")
        start, end = self.config.window()
        if start is None or end is None:
            raise PreregistrationError("a forward test needs a window with a start and an end")

    @property
    def rules(self) -> RulePrereg:
        return RulePrereg((self.rule,), self.config)

    def digest(self) -> str:
        body = {
            "kind": "forward",
            "rule": self.rule.locked(),
            "config": self.config.digest(),
            "busy": self.busy_hash,
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    @property
    def size(self) -> int:
        return 1


class ForwardStudy:
    """The forward window's trades, reachable only through the lock."""

    def __init__(self, tagged: pl.LazyFrame, config: StudyConfig, ledger: Path) -> None:
        self.config = config
        self._ledger = PreregLedger(ledger)
        self._frame = add_edges(add_bands(used(tagged), config))

    def verdict(self, prereg: ForwardPrereg, busy_file: Path) -> RuleVerdict:
        if busy_digest(busy_file) != prereg.busy_hash:
            raise PreregistrationError(
                "the activity file is not the one the rule was registered with"
            )
        self._ledger.open_test(prereg)
        frame = add_rule_features(self._frame, read_busy(busy_file))
        return evaluate_rules(frame, prereg.rules)
