"""S1.1b: refining a rule on old data by a fixed grid, then testing it forward on new data."""

import dataclasses
import json
import shutil
from pathlib import Path

import polars as pl
import pytest
from sparq_analytics.study.bootstrap import Estimate
from sparq_analytics.study.cli import main
from sparq_analytics.study.config import StudyConfig
from sparq_analytics.study.dataset import tag_trades
from sparq_analytics.study.forward import (
    ForwardPrereg,
    ForwardStudy,
    busy_digest,
    read_busy,
    write_busy,
)
from sparq_analytics.study.rules import (
    Rule,
    RulePrereg,
    add_rule_features,
    evaluate_rule,
    rules_from_json,
)
from sparq_analytics.study.scan import Grid, ScanRow, choose, grid_from_json, variants
from sparq_analytics.study.segments import PreregistrationError
from sparq_analytics.study.session import Study
from sparq_analytics.study.synthetic import World, write_world
from sparq_analytics.study.verdict import GO, NO_GO

DOCS = Path(__file__).resolve().parents[3] / "docs" / "research"
S1_1_SHARES = (
    ("Climate and Weather", 0.3385), ("Commodities", 0.1321), ("Companies", 1.0),
    ("Crypto", 0.0441), ("Economics", 1.0), ("Elections", 1.0), ("Entertainment", 1.0),
    ("Financials", 0.4627), ("Mentions", 1.0), ("Politics", 1.0), ("Science and Technology", 1.0),
)  # fmt: skip


def test_the_s1_1_registration_keeps_the_digest_locked_on_the_analytics_node() -> None:
    config = StudyConfig(
        window_start="2026-08-07", window_end="2026-10-19", fit_end="2026-09-29",
        validate_end="2026-10-10", sample_share=0.0366, sample_seed=1,
        category_shares=S1_1_SHARES, resamples=10_000, min_events=300,
    )  # fmt: skip
    rules = rules_from_json(json.loads((DOCS / "middle-tier-rules.json").read_text("utf-8")))
    digest = RulePrereg(rules, config).digest()
    assert digest == "95eec43a8d2d7ebefcdafef1e44a15b22ff218791ca4fc6b2af2a59775942455"


def test_the_forward_registration_keeps_its_digest() -> None:
    """S1.1b's forward test is locked by this digest until its verdict (about 2026-11-26), so
    no change to the code may move it."""
    config = StudyConfig(
        window_start="2026-10-26", window_end="2026-11-23", sample_share=0.0366, sample_seed=1,
        category_shares=S1_1_SHARES, resamples=10_000, min_events=300,
    )  # fmt: skip
    rules = rules_from_json(json.loads((DOCS / "forward-rule.json").read_text("utf-8")))
    busy = "edf3189cf82aa9f340aaf3b9798c7a0465c17a643e64b5b9ab36e1b9b6e7abf8"
    digest = ForwardPrereg(rules[0], config, busy).digest()
    assert digest == "0af9ea44be7fa121f36db9d5d52629e2768da910532eea390a06c9a9b4859b01"


def quiet_frame() -> pl.DataFrame:
    quiet = [None, 0.5, 1.0, 59.9, 60.0, 599.9, 600.0, 3599.9, 3600.0]
    return pl.DataFrame(
        {
            "quiet_s": quiet,
            "first": [q is None for q in quiet],
            "p": [500_000] * len(quiet),
            "busy": [5_000.0] * len(quiet),
            "category": ["sports"] * len(quiet),
            "ny_hour": [10] * len(quiet),
        },
        schema_overrides={"quiet_s": pl.Float64},
    )


def hits(rule: Rule) -> list[bool]:
    return quiet_frame().with_columns(rule.expr().alias("hit"))["hit"].to_list()


def test_quiet_bounds_of_one_second_to_an_hour_select_exactly_the_human_pace() -> None:
    human = Rule("h", "maker", pace="human")
    bounds = Rule("b", "maker", quiet_lo_s=1, quiet_hi_s=3600)
    assert hits(bounds) == hits(human)


def test_quiet_bounds_have_exact_edges_and_leave_out_first_trades() -> None:
    assert hits(Rule("a", "maker", quiet_lo_s=60, quiet_hi_s=600)) == [
        False, False, False, False, True, True, False, False, False,
    ]  # fmt: skip
    assert hits(Rule("b", "maker", quiet_lo_s=0))[0] is False  # a first trade has no quiet time


def test_quiet_bounds_are_checked() -> None:
    for bad in (
        {"pace": "human", "quiet_lo_s": 1.0},
        {"quiet_lo_s": 10.0, "quiet_hi_s": 10.0},
        {"quiet_lo_s": -1.0},
    ):
        with pytest.raises(ValueError):
            Rule("x", "maker", **bad)


def test_the_declared_grid_has_36_variants_with_the_base_among_them() -> None:
    grid = grid_from_json(json.loads((DOCS / "r4-grid.json").read_text("utf-8")))
    found = variants(grid)
    assert len(found) == 36
    assert len({rule.name for rule, _ in found}) == 36
    unchanged = [rule for rule, changes in found if changes == 0]
    assert len(unchanged) == 1
    assert unchanged[0].definition() == dataclasses.replace(grid.base, name="x").definition()
    assert sorted({changes for _, changes in found}) == [0, 1, 2, 3]


def test_a_grid_must_contain_its_base_and_state_its_quiet_window() -> None:
    base = Rule("b", "maker", busy_lo=100, busy_hi=100_000, quiet_lo_s=1, quiet_hi_s=3600)
    with pytest.raises(ValueError, match="one of the grid"):
        Grid(base, quiet=((1, 600),), busy=((100, 100_000),), price=((0, 1_000_001),))
    with pytest.raises(ValueError, match="quiet bounds"):
        Grid(
            Rule("p", "maker", pace="human"),
            quiet=((1, 3600),),
            busy=((100, 1e5),),
            price=((0, 1),),
        )
    with pytest.raises(ValueError, match="at least one value"):
        Grid(base, quiet=(), busy=((100, 100_000),), price=((0, 1_000_001),))


def row(name: str, changes: int, fit: tuple[float, float], validate_point: float) -> ScanRow:
    rule = Rule(name, "maker", busy_lo=float(len(name)))
    return ScanRow(
        rule, changes, Estimate(fit[0], fit[1], 0, 400, 1), Estimate(validate_point, 0, 0, 400, 1)
    )


def test_the_choosing_rule_needs_validate_support_and_prefers_the_simplest_close_variant() -> None:
    rows = (
        row("lucky", 0, (50_000, 40_000), 10_000),  # best on fit but validate is under half
        row("best", 2, (30_000, 12_000), 20_000),
        row("close and simpler", 1, (28_000, 8_000), 15_000),  # within half a cent of the best
        row("simplest but far", 0, (20_000, 1_000), 12_000),  # eligible, but not close
        row("negative", 0, (-5_000, -9_000), -1_000),
    )
    chosen = choose(rows)
    assert chosen is not None and chosen.rule.name == "close and simpler"
    assert choose(rows, margin_e6=1_000).rule.name == "best"  # type: ignore[union-attr]
    assert choose((rows[0], rows[-1])) is None


def test_the_frozen_activity_file_round_trips_and_its_hash_ignores_layout(tmp_path: Path) -> None:
    busy = pl.DataFrame({"series_ticker": ["B", "A"], "busy": [2.0, 1.5]})
    path = tmp_path / "busy.json"
    digest = write_busy(busy, path)
    assert read_busy(path).sort("series_ticker").equals(busy.sort("series_ticker"))
    path.write_text(json.dumps(json.loads(path.read_text("utf-8"))), encoding="utf-8")
    assert busy_digest(path) == digest
    path.write_text(json.dumps({"busy": {"A": 1.5, "B": 2.5}}), encoding="utf-8")
    assert busy_digest(path) != digest


FORWARD = StudyConfig(
    window_start="2026-03-20", window_end="2026-05-20", min_events=40, resamples=1_000
)
BUSY_RULE = Rule("busy, maker", "maker", busy_lo=200.0)


def test_a_forward_registration_is_one_deciding_rule_over_a_window_without_a_split() -> None:
    with pytest.raises(PreregistrationError, match="exploratory"):
        ForwardPrereg(dataclasses.replace(BUSY_RULE, exploratory=True), FORWARD, "h")
    with pytest.raises(PreregistrationError, match="no fit or validate"):
        ForwardPrereg(
            BUSY_RULE,
            dataclasses.replace(FORWARD, fit_end="2026-04-01", validate_end="2026-04-10"),
            "h",
        )
    with pytest.raises(PreregistrationError, match="window"):
        ForwardPrereg(BUSY_RULE, StudyConfig(window_start="2026-03-20"), "h")
    base = ForwardPrereg(BUSY_RULE, FORWARD, "h")
    assert base.size == 1 and base.rules.alpha() == pytest.approx(0.025)
    others = [
        ForwardPrereg(BUSY_RULE, FORWARD, "other activity file"),
        ForwardPrereg(dataclasses.replace(BUSY_RULE, busy_lo=300.0), FORWARD, "h"),
        ForwardPrereg(BUSY_RULE, dataclasses.replace(FORWARD, window_end="2026-05-21"), "h"),
    ]
    assert len({base.digest(), *(o.digest() for o in others)}) == 4


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("forward") / "kalshi"
    # events close hourly from 3 January; series 0 is busy and its takers pay a 5 cent spread
    write_world(
        World(events=3_000, trades_by_series=(40, 8), seed=43, spread=0.05, planted_series=0), root
    )
    return root


def frozen_busy(root: Path, folder: Path) -> tuple[Path, str]:
    """Activity from an older study's fit part, frozen as the forward test would use it."""
    older = StudyConfig(
        window_start="2026-01-02",
        window_end="2026-03-20",
        fit_end="2026-02-20",
        validate_end="2026-03-05",
    )
    session = Study(tag_trades(root, older), older, folder / "old-ledger.json")
    path = folder / "busy-fit.json"
    return path, write_busy(session.busy(), path)


def test_a_planted_spread_is_found_forward_and_a_fair_series_is_not(
    world: Path, tmp_path: Path
) -> None:
    busy, digest = frozen_busy(world, tmp_path)
    forward = ForwardStudy(tag_trades(world, FORWARD), FORWARD, tmp_path / "forward-ledger.json")
    found = forward.verdict(ForwardPrereg(BUSY_RULE, FORWARD, digest), busy)
    assert found.outcome == GO and found.results[0].estimates["decides"].events >= 40
    thin = Rule("thin, maker", "maker", busy_hi=200.0)
    other = ForwardStudy(tag_trades(world, FORWARD), FORWARD, tmp_path / "second-ledger.json")
    assert other.verdict(ForwardPrereg(thin, FORWARD, digest), busy).outcome == NO_GO


def test_the_forward_window_opens_once_and_only_with_its_own_activity_file(
    world: Path, tmp_path: Path
) -> None:
    busy, digest = frozen_busy(world, tmp_path)
    forward = ForwardStudy(tag_trades(world, FORWARD), FORWARD, tmp_path / "forward-ledger.json")
    prereg = ForwardPrereg(BUSY_RULE, FORWARD, digest)
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps({"busy": {"KXSYN0": 1.0}}), encoding="utf-8")
    with pytest.raises(PreregistrationError, match="activity file"):
        forward.verdict(prereg, changed)
    assert not (tmp_path / "forward-ledger.json").exists()
    forward.verdict(prereg, busy)
    forward.verdict(prereg, busy)  # the same registration may read it again
    with pytest.raises(PreregistrationError, match="locked"):
        forward.verdict(
            ForwardPrereg(dataclasses.replace(BUSY_RULE, busy_lo=300.0), FORWARD, digest), busy
        )


def test_the_scan_scores_every_variant_on_fit_and_validate_and_reads_no_test(
    world: Path, tmp_path: Path
) -> None:
    config = StudyConfig(
        window_start="2026-01-02",
        window_end="2026-03-20",
        fit_end="2026-02-20",
        validate_end="2026-03-05",
        min_events=40,
        resamples=400,
    )
    session = Study(tag_trades(world, config), config, tmp_path / "ledger.json")
    base = Rule("base", "maker", busy_lo=50, busy_hi=100_000, quiet_lo_s=1, quiet_hi_s=86_400)
    grid = Grid(
        base,
        quiet=((1, 86_400), (60, 86_400)),
        busy=((50, 100_000), (200, 100_000)),
        price=((0, 1_000_001),),
    )
    rows = session.scan(grid)
    assert len(rows) == 4
    assert not (tmp_path / "ledger.json").exists()  # nothing was opened
    validate = add_rule_features(session.validate, session.busy())
    for scanned in rows:  # each validate figure is the validate part's, and only its
        alone = evaluate_rule(validate, scanned.rule, config, config.family_alpha)
        assert scanned.validate == alone.estimates["decides"]
    # from 50 takes in the thin, fair series too; from 200 keeps only the busy one with the spread
    chosen = choose(rows, margin_e6=0)
    assert chosen is not None and chosen.rule.busy_lo == 200


WINDOW = [
    "--window-start", "2026-01-02", "--window-end", "2026-03-20",
    "--fit-end", "2026-02-20", "--validate-end", "2026-03-05",
    "--share", "1.0", "--resamples", "400", "--min-events", "40",
]  # fmt: skip
FORWARD_WINDOW = [
    "--window-start", "2026-03-20", "--window-end", "2026-05-20",
    "--share", "1.0", "--resamples", "400", "--min-events", "40",
]  # fmt: skip


def test_the_command_line_scans_freezes_registers_and_opens_forward_once(
    world: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"
    shutil.copytree(world.parent, data)
    out = tmp_path / "study"
    grid = tmp_path / "grid.json"
    grid.write_text(
        json.dumps(
            {
                "base": {
                    "name": "base",
                    "side": "maker",
                    "busy_lo": 100,
                    "busy_hi": 100000,
                    "quiet_lo_s": 1,
                    "quiet_hi_s": 86400,
                },
                "quiet": [[1, 86400]],
                "busy": [[100, 100000], [200, 100000]],
                "price": [[0, 1000001]],
            }
        ),
        encoding="utf-8",
    )
    common = ["--data-dir", str(data), "--study-dir", str(out)]
    assert main(["rules-scan", *common, "--grid", str(grid), *WINDOW]) == 0
    assert "chosen:" in capsys.readouterr().out
    chosen, busy = out / "chosen-rule.json", out / "busy-fit.json"
    assert chosen.exists() and busy.exists()
    older = StudyConfig(
        window_start="2026-01-02", window_end="2026-03-20", fit_end="2026-02-20",
        validate_end="2026-03-05", resamples=400, min_events=40,
    )  # fmt: skip
    fit_busy = Study(tag_trades(data / "kalshi", older), older, tmp_path / "x.json").busy()
    assert read_busy(busy).sort("series_ticker").equals(fit_busy.sort("series_ticker"))
    forward = [*common, "--rules", str(chosen), "--busy", str(busy), *FORWARD_WINDOW]
    assert main(["forward-register", *forward]) == 0
    assert "pre-registration digest" in capsys.readouterr().out
    assert (
        main(
            [
                "forward-register",
                *forward,
                "--fit-end",
                "2026-04-01",
                "--validate-end",
                "2026-04-10",
            ]
        )
        == 1
    )
    assert "leave out the split dates" in capsys.readouterr().err  # refused by the command itself
    assert main(["forward-verdict", *forward]) == 1  # no --open-test
    assert not (out / "forward-ledger.json").exists()
    assert main(["forward-verdict", *forward, "--open-test"]) == 0
    assert (out / "forward-ledger.json").exists() and (out / "forward-verdict.json").exists()
    assert main(["report", *common]) == 0
    text = capsys.readouterr().out
    assert "## The scan (fit and validate)" in text and "## The forward test" in text
    two = tmp_path / "two.json"
    two.write_text(
        json.dumps({"rules": [{"name": "a", "side": "maker"}, {"name": "b", "side": "taker"}]}),
        encoding="utf-8",
    )
    assert (
        main(
            ["forward-register", *common, "--rules", str(two), "--busy", str(busy), *FORWARD_WINDOW]
        )
        == 1
    )
