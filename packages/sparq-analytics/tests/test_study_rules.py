"""S1.1's rules: features with no look-ahead, the rule filters, the stratified interval, and the
whole test on synthetic worlds whose answer is known."""

import dataclasses
import datetime as dt
import json
import shutil
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from sparq_analytics.study.bootstrap import cluster_bootstrap, stratified_bootstrap
from sparq_analytics.study.cli import main
from sparq_analytics.study.config import StudyConfig
from sparq_analytics.study.dataset import tag_trades
from sparq_analytics.study.rules import (
    Rule,
    RulePrereg,
    add_rule_features,
    busy_table,
    rules_from_json,
)
from sparq_analytics.study.segments import PreregistrationError
from sparq_analytics.study.session import Study
from sparq_analytics.study.synthetic import World, write_world
from sparq_analytics.study.verdict import GO, INCONCLUSIVE, NO_GO

T0 = dt.datetime(2026, 1, 5, 15, 0, tzinfo=dt.UTC)  # 10:00 in New York


def frame(rows: list[tuple[str, str, float, int]]) -> pl.LazyFrame:
    """(ticker, series, seconds after T0, contracts) rows, in the order given."""
    return pl.DataFrame(
        {
            "ticker": [r[0] for r in rows],
            "series_ticker": [r[1] for r in rows],
            "t": [T0 + dt.timedelta(seconds=r[2]) for r in rows],
            "c": [r[3] * 100 for r in rows],
            "category": ["sports"] * len(rows),
        },
        schema_overrides={"t": pl.Datetime("us", "UTC")},
    ).lazy()


def features(
    rows: list[tuple[str, str, float, int]], fit: pl.LazyFrame | None = None
) -> pl.DataFrame:
    part = frame(rows)
    busy = busy_table(fit if fit is not None else part)
    return add_rule_features(part, busy).sort("ticker", "t").collect()


def test_quiet_time_and_first_trade_follow_each_market_in_time_order() -> None:
    out = features(
        [("A", "S", 30.0, 1), ("B", "S", 5.0, 1), ("A", "S", 0.0, 1), ("A", "S", 30.5, 1)]
    )
    a = out.filter(pl.col("ticker") == "A")
    assert a["first"].to_list() == [True, False, False]
    assert a["quiet_s"].to_list() == [None, 30.0, 0.5]
    assert out.filter(pl.col("ticker") == "B")["first"].to_list() == [True]
    assert set(out["ny_hour"].to_list()) == {10}


def test_a_later_trade_never_changes_an_earlier_trades_features() -> None:
    base = [("A", "S", 0.0, 1), ("A", "S", 10.0, 1), ("A", "S", 20.0, 1)]
    changed = [*base[:2], ("A", "S", 25.0, 50), ("A", "S", 99.0, 7)]
    fit = frame([("F", "S", 0.0, 3)])
    early = features(base, fit).head(2).select("quiet_s", "first", "busy")
    assert features(changed, fit).head(2).select("quiet_s", "first", "busy").equals(early)


def test_busy_comes_from_the_fit_part_only() -> None:
    fit = frame([("F1", "S", 0.0, 100), ("F2", "S", 0.0, 300), ("F3", "S", 0.0, 500)])
    validate = [("V", "S", 0.0, 9_999), ("W", "T", 0.0, 1)]
    out = features(validate, fit)
    assert out.filter(pl.col("ticker") == "V")["busy"].to_list() == [300.0]  # the fit median
    assert out.filter(pl.col("ticker") == "W")["busy"].to_list() == [None]  # series not in fit


def rule_hits(
    rule: Rule, quiet: list[float | None], busy: float | None = 5_000.0, price: int = 500_000
) -> list[bool]:
    rows = pl.DataFrame(
        {
            "p": [price] * len(quiet),
            "quiet_s": quiet,
            "first": [q is None for q in quiet],
            "busy": [busy] * len(quiet),
            "category": ["sports"] * len(quiet),
            "ny_hour": [10] * len(quiet),
        },
        schema_overrides={"quiet_s": pl.Float64, "busy": pl.Float64},
    )
    return rows.with_columns(rule.expr().alias("hit"))["hit"].to_list()


def test_the_pace_rules_have_exact_edges() -> None:
    quiet = [None, 0.999, 1.0, 3599.9, 3600.0]
    assert rule_hits(Rule("h", "maker", pace="human"), quiet) == [False, False, True, True, False]
    assert rule_hits(Rule("n", "maker", pace="never_stale"), quiet) == [
        False,
        True,
        True,
        True,
        False,
    ]
    assert rule_hits(Rule("f", "taker", pace="first_trade"), quiet) == [
        True,
        False,
        False,
        False,
        False,
    ]
    assert rule_hits(Rule("a", "maker"), quiet) == [True] * 5


def test_the_busy_range_includes_its_lower_edge_and_excludes_its_upper_and_unknowns() -> None:
    middle = Rule("m", "maker", busy_lo=10_000, busy_hi=100_000)
    assert rule_hits(middle, [5.0], busy=10_000) == [True]
    assert rule_hits(middle, [5.0], busy=99_999.9) == [True]
    assert rule_hits(middle, [5.0], busy=100_000) == [False]
    assert rule_hits(middle, [5.0], busy=None) == [False]


def test_the_price_range_includes_its_lower_edge_and_excludes_its_upper() -> None:
    favourites = Rule("f", "maker", price_lo_e6=500_000, price_hi_e6=900_000)
    assert rule_hits(favourites, [5.0], price=500_000) == [True]
    assert rule_hits(favourites, [5.0], price=899_999) == [True]
    assert rule_hits(favourites, [5.0], price=900_000) == [False]
    assert rule_hits(favourites, [5.0], price=499_999) == [False]


def test_category_and_hours_filter() -> None:
    assert rule_hits(Rule("c", "maker", category="sports"), [5.0]) == [True]
    assert rule_hits(Rule("c", "maker", category="weather"), [5.0]) == [False]
    assert rule_hits(Rule("d", "maker", ny_hours=(6, 18)), [5.0]) == [True]
    assert rule_hits(Rule("e", "maker", ny_hours=(11, 18)), [5.0]) == [False]


def test_bad_rules_are_refused() -> None:
    for bad in (
        {"name": "", "side": "maker"},
        {"name": "x", "side": "both"},
        {"name": "x", "side": "maker", "pace": "slow"},
        {"name": "x", "side": "maker", "busy_lo": 10.0, "busy_hi": 10.0},
        {"name": "x", "side": "maker", "ny_hours": (18, 6)},
        {"name": "x", "side": "maker", "ny_hours": (0, 25)},
        {"name": "x", "side": "maker", "price_lo_e6": 500_000, "price_hi_e6": 500_000},
    ):
        with pytest.raises(ValueError):
            Rule(**bad)


def test_a_rules_file_drops_reasons_and_reads_hours() -> None:
    rules = rules_from_json(
        {"rules": [{"name": "r", "side": "maker", "ny_hours": [6, 18], "reason": "daytime takers"}]}
    )
    assert rules == (Rule("r", "maker", ny_hours=(6, 18)),)


def test_the_registration_refuses_bad_lists_and_locks_every_field() -> None:
    config = StudyConfig()
    one = Rule("a", "maker")
    with pytest.raises(PreregistrationError):
        RulePrereg((), config)
    with pytest.raises(PreregistrationError, match="not exploratory"):
        RulePrereg((dataclasses.replace(one, exploratory=True),), config)
    with pytest.raises(PreregistrationError, match="twice"):
        RulePrereg((one, Rule("a", "taker")), config)
    with pytest.raises(PreregistrationError, match="same trades"):
        RulePrereg((one, Rule("b", "maker")), config)
    many = tuple(Rule(f"r{i}", "maker", busy_lo=float(i)) for i in range(13))
    with pytest.raises(PreregistrationError):
        RulePrereg(many, config)
    base = RulePrereg((one, Rule("b", "taker", pace="first_trade")), config)
    assert base.alpha() == pytest.approx(0.025 / 2)
    assert base.size == 2
    variants = [
        RulePrereg((one, Rule("b", "taker", pace="first_trade", exploratory=True)), config),
        RulePrereg((one, Rule("b", "taker", pace="human")), config),
        RulePrereg((one, Rule("b", "taker", pace="first_trade")), StudyConfig(min_events=10)),
        RulePrereg((Rule("a2", "maker"), Rule("b", "taker", pace="first_trade")), config),
    ]
    assert len({base.digest(), *(v.digest() for v in variants)}) == 5
    assert variants[0].alpha() == pytest.approx(0.025)  # an exploratory rule spends nothing


def test_with_one_stratum_the_stratified_interval_is_the_plain_one() -> None:
    rng = np.random.default_rng(3)
    den = rng.integers(1, 30, 200).astype(float) * 100
    num = rng.normal(500, 20_000, 200) * den / 100
    plain = cluster_bootstrap(num, den, tail=0.01, resamples=800, seed=9)
    strata = np.array(["only"] * 200)
    assert stratified_bootstrap(num, den, strata, tail=0.01, resamples=800, seed=9) == plain


def test_resampling_within_strata_keeps_the_mix_of_strata() -> None:
    # every event in A earns one per contract and every event in B loses one: within each stratum
    # nothing varies, so only a resample that changes the mix of A and B can move the ratio
    den = np.full(60, 100.0)
    num = np.where(np.arange(60) < 20, 100.0, -100.0)
    strata = np.where(np.arange(60) < 20, "A", "B")
    point = (20 - 40) / 60
    plain = cluster_bootstrap(num, den, tail=0.01, resamples=800, seed=2)
    kept = stratified_bootstrap(num, den, strata, tail=0.01, resamples=800, seed=2)
    assert kept.point_e6 == pytest.approx(point) == plain.point_e6
    assert kept.lower_e6 == pytest.approx(point) and kept.upper_e6 == pytest.approx(point)
    assert plain.lower_e6 < point < plain.upper_e6


CONFIG = StudyConfig(min_events=40, resamples=1_000)
BUSY_ONLY = Rule("busy series, maker", "maker", busy_lo=200.0)
QUIET_ONLY = Rule("thin series, maker", "maker", busy_hi=200.0)


def world_root(factory: pytest.TempPathFactory, name: str, **settings: object) -> Path:
    root = factory.mktemp(name) / "kalshi"
    write_world(World(events=3_000, trades_by_series=(40, 8), seed=41, **settings), root)  # type: ignore[arg-type]
    return root


@pytest.fixture(scope="module")
def planted(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # series 0 is busy and its takers pay a 5 cent spread to the makers; series 1 is thin and fair
    return world_root(tmp_path_factory, "planted", spread=0.05, planted_series=0)


@pytest.fixture(scope="module")
def null(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return world_root(tmp_path_factory, "null")


def study(root: Path, ledger: Path) -> Study:
    return Study(tag_trades(root, CONFIG), CONFIG, ledger / "rules-ledger.json")


def test_a_planted_maker_edge_in_the_busy_series_is_found_and_the_fair_series_is_not(
    planted: Path, tmp_path: Path
) -> None:
    verdict = study(planted, tmp_path).rules_verdict(RulePrereg((BUSY_ONLY, QUIET_ONLY), CONFIG))
    busy, quiet = verdict.results
    assert verdict.outcome == GO
    assert busy.go and busy.estimates["decides"].lower_e6 > 0
    assert busy.estimates["decides"] == busy.estimates["maker"]  # a maker rule is the strict one
    strict, optimistic = busy.estimates["maker"], busy.estimates["maker_optimistic"]
    assert strict.contracts_e2 < optimistic.contracts_e2  # the strict rule counts fewer fills
    assert not quiet.go
    assert quiet.estimates["decides"].lower_e6 <= 0


def test_with_nothing_planted_no_rule_clears_zero(null: Path, tmp_path: Path) -> None:
    verdict = study(null, tmp_path).rules_verdict(RulePrereg((BUSY_ONLY, QUIET_ONLY), CONFIG))
    assert verdict.outcome == NO_GO
    assert all(not r.go and not r.short_of_events for r in verdict.results)


def test_a_rule_short_of_events_makes_the_verdict_inconclusive(
    planted: Path, tmp_path: Path
) -> None:
    late = Rule("first trades at 3am", "maker", busy_hi=200.0, pace="first_trade", ny_hours=(3, 4))
    verdict = study(planted, tmp_path).rules_verdict(RulePrereg((late,), CONFIG))
    assert verdict.results[0].short_of_events
    assert verdict.outcome == INCONCLUSIVE


def test_busy_is_taken_from_the_fit_part(planted: Path, tmp_path: Path) -> None:
    session = study(planted, tmp_path)
    assert (
        session.busy().sort("series_ticker").equals(busy_table(session.fit).sort("series_ticker"))
    )
    validate = busy_table(session.validate).sort("series_ticker")
    assert not session.busy().sort("series_ticker").equals(validate)


def test_an_exploratory_rule_that_would_pass_does_not_decide(planted: Path, tmp_path: Path) -> None:
    explored = dataclasses.replace(BUSY_ONLY, exploratory=True)
    verdict = study(planted, tmp_path).rules_verdict(RulePrereg((explored, QUIET_ONLY), CONFIG))
    assert verdict.results[0].go  # it clears zero ...
    assert verdict.outcome == NO_GO  # ... but only the deciding rule counts


def test_pooled_figures_are_weighted_by_one_over_the_categorys_share(
    planted: Path, tmp_path: Path
) -> None:
    config = dataclasses.replace(CONFIG, category_shares=(("politics", 1.0), ("sports", 0.5)))
    session = Study(tag_trades(planted, config, drop_unsampled=True), config, tmp_path / "l.json")
    pooled = Rule("all trades, maker", "maker")
    result = session.rules_on_validate(RulePrereg((pooled,), config)).results[0]
    frame = add_rule_features(session.validate, session.busy()).filter(pooled.expr()).collect()
    edge, fills, w = pl.col("e_mk_pess_cons"), pl.col("c_pess"), pl.col("w")
    weighted = float(frame.select((edge * w).sum() / (fills * w).sum()).item())
    unweighted = float(frame.select(edge.sum() / fills.sum()).item())
    assert set(frame["w"].unique().to_list()) == {1.0, 2.0}
    assert result.estimates["maker"].point_e6 == pytest.approx(weighted)
    assert abs(weighted - unweighted) > 1_000  # the weights matter here, by more than 0.1 cent


def test_a_rule_that_matches_no_trade_is_empty_and_short(planted: Path, tmp_path: Path) -> None:
    nothing = Rule("no such category", "maker", category="no-such-category")
    result = study(planted, tmp_path).rules_verdict(RulePrereg((nothing,), CONFIG)).results[0]
    assert result.trades == 0 and result.short_of_events and not result.go
    assert result.estimates["decides"].events == 0


def test_the_validate_reading_locks_nothing_and_the_test_is_locked_once_read(
    planted: Path, tmp_path: Path
) -> None:
    session = study(planted, tmp_path)
    first = RulePrereg((BUSY_ONLY,), CONFIG)
    session.rules_on_validate(first)
    session.rules_on_validate(RulePrereg((QUIET_ONLY,), CONFIG))  # choosing freely is allowed
    assert not (tmp_path / "rules-ledger.json").exists()
    session.rules_verdict(first)
    session.rules_verdict(first)  # the same registration may read it again
    with pytest.raises(PreregistrationError, match="locked"):
        session.rules_verdict(RulePrereg((QUIET_ONLY,), CONFIG))


WINDOW = [
    "--window-start", "2026-01-02", "--window-end", "2026-04-20",
    "--fit-end", "2026-02-15", "--validate-end", "2026-02-24",
    "--share", "1.0", "--resamples", "400", "--min-events", "20",
]  # fmt: skip


@pytest.fixture(scope="module")
def cli_world(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("rulescli")
    write_world(World(events=1_500, trades_per_market=6, bias=0.15, seed=31), base / "kalshi")
    return base


def test_the_command_line_explores_registers_and_opens_the_test_once(
    cli_world: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"
    shutil.copytree(cli_world, data)
    rules = tmp_path / "rules.json"
    rules.write_text(
        json.dumps({"rules": [{"name": "all, maker", "side": "maker", "reason": "the baseline"}]}),
        encoding="utf-8",
    )
    study_dir = tmp_path / "sparq-study"
    common = [
        "--data-dir",
        str(data),
        "--study-dir",
        str(study_dir),
        "--rules",
        str(rules),
        *WINDOW,
    ]
    assert main(["rules-register", *common]) == 0
    assert "pre-registration digest" in capsys.readouterr().out
    assert main(["rules-explore", *common]) == 0
    assert (study_dir / "rules-validate.json").exists()
    assert main(["rules-verdict", *common]) == 1  # no --open-test
    assert "--open-test" in capsys.readouterr().err
    assert not (study_dir / "rules-ledger.json").exists()
    assert main(["rules-verdict", *common, "--open-test"]) == 0
    assert (study_dir / "rules-ledger.json").exists() and (
        study_dir / "rules-verdict.json"
    ).exists()
    assert not (data / "study").exists()  # the data folder's own study folder is never touched
    assert "Rules on the test part" in capsys.readouterr().out
    assert main(["report", "--data-dir", str(data), "--study-dir", str(study_dir)]) == 0
    text = capsys.readouterr().out
    assert "## Rules on the validate part" in text and "## Rules on the test part" in text
    assert main(["rules-verdict", *common[: -len(WINDOW)], "--open-test"]) == 1  # settings missing
