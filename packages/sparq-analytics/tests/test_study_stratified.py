"""A share of its own for each category (D16 amended): the plan, the sample, the weights, and the
rules that keep the pooled figures honest."""

import json
import shutil
from pathlib import Path

import polars as pl
import pytest
from sparq_analytics.study.calibration import calibration_table
from sparq_analytics.study.cli import main
from sparq_analytics.study.config import StudyConfig
from sparq_analytics.study.dataset import exclusion_counts, tag_trades, used
from sparq_analytics.study.ladder import fit_band_gaps, scores
from sparq_analytics.study.sample import (
    TARGET_EVENTS,
    CategoryStat,
    chosen_events_by_category,
    event_fraction,
    plan_shares,
)
from sparq_analytics.study.segments import Prereg, PreregistrationError, Segment, add_bands
from sparq_analytics.study.snapshot import build_snapshot
from sparq_analytics.study.synthetic import World, write_world

SPORTS_ONLY_FULL = (("politics", 1.0),)


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, int]]:
    base = tmp_path_factory.mktemp("stratified")
    counts = write_world(
        World(events=1200, trades_per_market=6, bias=0.15, seed=3), base / "kalshi"
    )
    return base, counts


# --- the settings -----------------------------------------------------------------------------


def test_a_category_share_is_its_own_and_the_rest_take_the_base() -> None:
    config = StudyConfig(sample_share=0.1, category_shares=(("weather", 0.5), ("zoo", 1.0)))
    assert (config.share_of("weather"), config.share_of("zoo"), config.share_of("sports")) == (
        0.5,
        1.0,
        0.1,
    )
    assert config.sampled and config.stratified


def test_a_full_run_is_neither_sampled_nor_stratified() -> None:
    config = StudyConfig()
    assert not config.sampled and not config.stratified and config.category_shares == ()


def test_a_category_sampled_below_one_makes_the_run_sampled_even_with_a_full_base() -> None:
    config = StudyConfig(sample_share=1.0, category_shares=(("sports", 0.1),))
    assert config.sampled


@pytest.mark.parametrize(
    "shares",
    [
        (("a", 0.0),),
        (("a", 1.5),),
        (("a", 0.5), ("a", 0.6)),
        (("b", 0.5), ("a", 0.5)),  # not sorted: the digest would depend on the order given
    ],
)
def test_shares_that_make_no_sense_or_an_unstable_digest_are_refused(
    shares: tuple[tuple[str, float], ...],
) -> None:
    with pytest.raises(ValueError):
        StudyConfig(category_shares=shares)


def test_the_shares_are_part_of_the_pre_registration() -> None:
    plain = StudyConfig(sample_share=0.1)
    assert StudyConfig(sample_share=0.1, category_shares=(("a", 0.5),)).digest() != plain.digest()
    assert (
        StudyConfig(sample_share=0.1, category_shares=(("a", 0.5),)).digest()
        != StudyConfig(sample_share=0.1, category_shares=(("a", 0.6),)).digest()
    )


# --- the plan ---------------------------------------------------------------------------------

STATS = [
    CategoryStat("big", 100_000, 400_000_000),
    CategoryStat("mid", 80_000, 200_000_000),
    CategoryStat("small", 1_200, 600_000),
    CategoryStat("tiny", 40, 10_000),
]


def loaded(stats: list[CategoryStat], base: float, overrides: dict[str, float]) -> float:
    return sum(s.trades * overrides.get(s.category, base) for s in stats)


def test_everything_that_fits_under_the_ceiling_is_loaded_whole() -> None:
    assert plan_shares(STATS, ceiling=10**12) == (1.0, {})


def test_small_categories_are_loaded_in_full_and_the_big_ones_share_what_is_left() -> None:
    base, overrides = plan_shares(STATS, ceiling=40_000_000)
    assert overrides["small"] == 1.0 and overrides["tiny"] == 1.0
    assert "big" not in overrides and 0.0001 < base < 1.0
    assert loaded(STATS, base, overrides) <= 40_000_000 * 1.001


def test_a_category_gets_enough_events_for_its_segments_when_the_budget_allows() -> None:
    stats = [CategoryStat("wide", 30_000, 90_000_000), CategoryStat("mid", 9_000, 9_000_000)]
    base, overrides = plan_shares(stats, ceiling=40_000_000)
    mid = overrides.get("mid", base)
    assert 9_000 * mid >= TARGET_EVENTS * 0.99  # its test part will hold about 600 events
    assert loaded(stats, base, overrides) <= 40_000_000 * 1.001


def test_when_the_targets_cannot_all_be_met_they_shrink_and_the_ceiling_still_holds() -> None:
    stats = [CategoryStat("a", 100, 50_000_000), CategoryStat("b", 100, 50_000_000)]
    base, overrides = plan_shares(stats, ceiling=10_000_000)
    assert base == 0.0001
    assert loaded(stats, base, overrides) <= 10_000_000 * 1.001
    assert 0.0 < overrides["a"] < 1.0 and overrides["a"] == overrides["b"]


def test_a_category_with_no_events_does_not_break_the_plan() -> None:
    stats = [*STATS, CategoryStat("empty", 0, 0)]
    base, overrides = plan_shares(stats, ceiling=40_000_000)
    assert loaded(stats, base, overrides) <= 40_000_000 * 1.001


def test_only_categories_above_the_base_are_named() -> None:
    base, overrides = plan_shares(STATS, ceiling=40_000_000)
    assert all(share > base for share in overrides.values())


# --- the sample -------------------------------------------------------------------------------


def test_each_category_is_sampled_at_its_own_share() -> None:
    events = [(f"S-{i}", "sports") for i in range(6_000)] + [
        (f"P-{i}", "politics") for i in range(600)
    ]
    shares = {"sports": 0.1, "politics": 1.0}
    chosen = set(chosen_events_by_category(events, lambda c: shares[c], seed=4))
    sports = sum(1 for e, c in events if c == "sports" and e in chosen)
    politics = sum(1 for e, c in events if c == "politics" and e in chosen)
    assert 480 < sports < 720 and politics == 600


def test_a_smaller_share_stays_inside_a_larger_one_in_every_category() -> None:
    events = [(f"E-{i}", "a" if i % 2 else "b") for i in range(4_000)]
    small = set(chosen_events_by_category(events, lambda c: 0.1 if c == "a" else 0.3, seed=1))
    large = set(chosen_events_by_category(events, lambda c: 0.4 if c == "a" else 0.6, seed=1))
    assert small <= large and all(event_fraction(e, 1) < 0.6 for e in large)


def sample_config(**changes: object) -> StudyConfig:
    base: dict[str, object] = {
        "sample_share": 0.2,
        "category_shares": SPORTS_ONLY_FULL,
        "sample_seed": 3,
    }
    return StudyConfig(**{**base, **changes})  # type: ignore[arg-type]


def test_a_category_given_the_full_share_keeps_every_one_of_its_events(
    world: tuple[Path, dict[str, int]],
) -> None:
    root = world[0] / "kalshi"
    everything = used(tag_trades(root, StudyConfig())).collect()
    kept = used(tag_trades(root, sample_config())).collect()
    for category in ("politics", "sports"):
        all_events = everything.filter(pl.col("category") == category)["event_ticker"].n_unique()
        kept_events = kept.filter(pl.col("category") == category)["event_ticker"].n_unique()
        if category == "politics":
            assert kept_events == all_events
        else:
            assert 0.1 * all_events < kept_events < 0.3 * all_events


def test_every_trade_carries_the_weight_of_its_categorys_share(
    world: tuple[Path, dict[str, int]],
) -> None:
    root = world[0] / "kalshi"
    kept = used(tag_trades(root, sample_config())).collect()
    assert set(kept.filter(pl.col("category") == "politics")["w"]) == {1.0}
    assert set(kept.filter(pl.col("category") == "sports")["w"]) == {5.0}  # the base, 1 / 0.2
    both = sample_config(category_shares=(("politics", 0.5), ("sports", 0.25)))
    mapped = used(tag_trades(root, both)).collect()
    assert set(mapped.filter(pl.col("category") == "politics")["w"]) == {2.0}  # 1 / 0.5
    assert set(mapped.filter(pl.col("category") == "sports")["w"]) == {4.0}  # 1 / 0.25
    plain = used(tag_trades(root, StudyConfig(sample_share=0.25))).collect()
    assert set(plain["w"]) == {4.0}  # one share for all: the same weight everywhere, which cancels


def test_a_full_run_weighs_every_trade_one(world: tuple[Path, dict[str, int]]) -> None:
    assert set(used(tag_trades(world[0] / "kalshi", StudyConfig())).collect()["w"]) == {1.0}


def test_dropping_the_unsampled_early_leaves_the_same_trades_when_stratified(
    world: tuple[Path, dict[str, int]],
) -> None:
    root = world[0] / "kalshi"
    late = used(tag_trades(root, sample_config())).collect()
    early_frame = tag_trades(root, sample_config(), drop_unsampled=True)
    early = used(early_frame).collect()
    order = ["ticker", "t", "p", "c"]
    assert early.sort(order).equals(late.sort(order)) and early.height > 0
    assert "not_sampled" not in exclusion_counts(early_frame)
    assert exclusion_counts(tag_trades(root, sample_config()))["not_sampled"] > 0


# --- the pooled figures are weighted ----------------------------------------------------------


def two_groups() -> pl.LazyFrame:
    """Two categories in one band: A sampled at one in four (weight 4), B in full (weight 1)."""
    return pl.DataFrame(
        {
            "event_ticker": ["a1", "a1", "b1", "b1"],
            "price_band": [3, 3, 3, 3],
            "p": [200_000, 200_000, 500_000, 500_000],
            "c": [100, 100, 100, 100],
            "y": [0, 0, 1, 1],
            "w": [4.0, 4.0, 1.0, 1.0],
        }
    ).lazy()


def test_the_calibration_table_pools_categories_by_weight() -> None:
    row = calibration_table(two_groups(), ["price_band"]).row(0, named=True)
    # A stands for 8 trades' worth (weights 4+4) at 0.2 and no yes; B for 2 at 0.5, both yes:
    assert row["mean_price"] == pytest.approx((8 * 0.2 + 2 * 0.5) / 10)
    assert row["yes_rate"] == pytest.approx(0.2)
    assert (row["trades"], row["events"], row["contracts_e2"]) == (4, 2, 400)  # what was loaded


def test_the_band_gaps_of_the_ladder_use_the_same_weights() -> None:
    assert fit_band_gaps(two_groups())[3] == pytest.approx(0.2 - 0.26)


def test_the_scores_weigh_the_categories_by_what_they_stand_for() -> None:
    result = scores(two_groups(), pl.col("p").cast(pl.Float64) / 1_000_000)
    # A misses by 0.2 (squared 0.04) and counts 8; B misses by 0.5 (squared 0.25) and counts 2
    assert result.brier == pytest.approx((8 * 0.04 + 2 * 0.25) / 10)
    assert result.brier != pytest.approx((0.04 + 0.25) / 2)  # not the unweighted figure
    assert result.contracts_e2 == 400


def test_a_frame_without_weights_is_weighed_one() -> None:
    plain = two_groups().drop("w")
    assert calibration_table(plain, ["price_band"]).row(0, named=True)["mean_price"] == (
        pytest.approx(0.35)
    )


def test_a_stratified_sample_recovers_the_pooled_calibration_of_the_full_data(
    world: tuple[Path, dict[str, int]],
) -> None:
    root = world[0] / "kalshi"
    plain = StudyConfig()
    full = calibration_table(add_bands(used(tag_trades(root, plain)), plain), ["price_band"])
    config = sample_config(sample_share=0.3)
    sample = add_bands(used(tag_trades(root, config, drop_unsampled=True)), config)
    weighted = calibration_table(sample, ["price_band"])

    def pooled(table: pl.DataFrame, column: str) -> float:
        rates, contracts = table[column].to_numpy(), table["contracts_e2"].to_numpy()
        return float((rates * contracts).sum() / contracts.sum())

    assert pooled(weighted, "yes_rate") == pytest.approx(pooled(full, "yes_rate"), abs=0.04)
    assert pooled(weighted, "mean_price") == pytest.approx(pooled(full, "mean_price"), abs=0.04)


# --- the pre-registration ---------------------------------------------------------------------


def test_a_segment_must_name_its_category_when_the_sample_is_stratified() -> None:
    stratified = StudyConfig(sample_share=0.1, category_shares=(("sports", 0.5),))
    with pytest.raises(PreregistrationError, match="name a category"):
        Prereg((Segment(price_hi_e6=250_000),), stratified)
    assert Prereg((Segment(price_hi_e6=250_000, category="sports"),), stratified)


def test_a_segment_without_a_category_is_fine_with_one_share_for_all() -> None:
    assert Prereg((Segment(price_hi_e6=250_000),), StudyConfig(sample_share=0.1))


# --- the snapshot and the command line --------------------------------------------------------


def test_the_snapshot_plans_shares_by_category_within_the_ceiling(
    world: tuple[Path, dict[str, int]],
) -> None:
    root, counts = world[0] / "kalshi", world[1]
    ceiling = counts["trades"] // 2
    suggested = build_snapshot(root, ceiling=ceiling)["suggested"]
    assert suggested["expected_trades"] <= ceiling * 1.02
    assert {c["category"] for c in suggested["categories"]} == {"sports", "politics"}
    assert [c["trades"] for c in suggested["categories"]] == sorted(
        (c["trades"] for c in suggested["categories"]), reverse=True
    )
    assert suggested["used_trades"] == sum(c["trades"] for c in suggested["categories"])
    assert set(suggested["category_shares"]) <= {"sports", "politics"}


WINDOW = [
    "--window-start", "2026-01-02", "--window-end", "2026-04-20",
    "--fit-end", "2026-02-15", "--validate-end", "2026-02-24",
]  # fmt: skip
FAST = ["--resamples", "300", "--min-events", "10"]


@pytest.fixture
def data(world: tuple[Path, dict[str, int]], tmp_path: Path) -> Path:
    copy = tmp_path / "data"
    shutil.copytree(world[0], copy)
    return copy


def segments_file(data: Path, *, category: str | None) -> Path:
    item: dict[str, object] = {"price_hi_e6": 250_000, "side": "no", "reason": "longshots"}
    if category is not None:
        item["category"] = category
    path = data / "segments.json"
    path.write_text(json.dumps({"segments": [item]}), encoding="utf-8")
    return path


def test_explore_takes_category_shares_from_the_command_line(data: Path) -> None:
    args = ["--data-dir", str(data), "explore", *WINDOW, "--share", "0.3", *FAST]
    assert main([*args, "--category-share", "politics=1.0"]) == 0
    explore = json.loads((data / "study" / "explore.json").read_text(encoding="utf-8"))
    assert explore["config"]["category_shares"] == [["politics", 1.0]]
    stratified_digest = explore["digest"]
    assert main(args) == 0
    plain = json.loads((data / "study" / "explore.json").read_text(encoding="utf-8"))
    assert plain["config"]["category_shares"] == [] and plain["digest"] != stratified_digest


def test_explore_plans_the_shares_itself_when_none_are_given(data: Path) -> None:
    args = ["--data-dir", str(data), "explore", *WINDOW, "--max-trades", "5000", *FAST]
    assert main(args) == 0
    config = json.loads((data / "study" / "explore.json").read_text(encoding="utf-8"))["config"]
    assert config["category_shares"], "a ceiling under the total should give some category a share"


@pytest.mark.parametrize("bad", ["weather", "=0.5", "weather=lots"])
def test_a_malformed_category_share_is_a_usage_error(
    data: Path, bad: str, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--data-dir", str(data), "explore", *WINDOW, "--share", "0.3", *FAST]
    assert main([*args, "--category-share", bad]) == 1
    assert "--category-share" in capsys.readouterr().err


def test_register_refuses_a_segment_with_no_category_under_a_stratified_sample(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["--data-dir", str(data), "register", *WINDOW, "--share", "0.3", *FAST]
    stratified = [*base, "--category-share", "politics=1.0"]
    assert main([*stratified, "--segments", str(segments_file(data, category=None))]) == 1
    assert "name a category" in capsys.readouterr().err
    assert main([*stratified, "--segments", str(segments_file(data, category="sports"))]) == 0
    assert main([*base, "--segments", str(segments_file(data, category=None))]) == 0


def test_the_shares_change_the_pre_registration_digest(
    data: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seg = str(segments_file(data, category="sports"))
    base = [
        "--data-dir",
        str(data),
        "register",
        "--segments",
        seg,
        *WINDOW,
        "--share",
        "0.3",
        *FAST,
    ]
    digests = []
    for extra in ([], ["--category-share", "politics=1.0"], ["--category-share", "politics=0.9"]):
        assert main([*base, *extra]) == 0
        digests.append(capsys.readouterr().out.rsplit("digest ", 1)[1].strip())
    assert len(set(digests)) == 3
