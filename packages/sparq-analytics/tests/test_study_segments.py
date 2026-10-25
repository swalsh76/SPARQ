import dataclasses
from pathlib import Path

import polars as pl
import pytest
from sparq_analytics.study.calibration import band_labels, calibration_table
from sparq_analytics.study.config import StudyConfig
from sparq_analytics.study.segments import (
    Prereg,
    PreregistrationError,
    PreregLedger,
    Segment,
    add_bands,
)


def frame() -> pl.LazyFrame:
    return pl.DataFrame(
        {
            "event_ticker": ["A", "A", "B", "C"],
            "p": [20_000, 480_000, 500_000, 990_000],
            "c": [100, 300, 100, 200],
            "y": [0, 1, 1, 1],
            "tleft_s": [30, 700, 90_000, 5_000_000],
            "category": ["sports", "sports", "politics", "sports"],
            "taker_yes": [True, False, True, False],
        }
    ).lazy()


def test_bands_number_prices_and_times_from_zero() -> None:
    out = add_bands(frame(), StudyConfig()).collect()
    assert out["price_band"].to_list() == [0, 5, 6, 11]  # 2c, 48c, 50c, 99c
    assert out["tleft_band"].to_list() == [0, 1, 4, 7]  # 30s, 11m, 25h, 58d


def test_a_segment_selects_price_time_category_and_side() -> None:
    def selected(segment: Segment) -> list[int]:
        return frame().filter(segment.expr()).collect()["p"].to_list()

    assert selected(Segment()) == [20_000, 480_000, 500_000, 990_000]
    assert selected(Segment(price_lo_e6=400_000, price_hi_e6=500_000)) == [480_000]
    assert selected(Segment(tleft_lo_s=600, tleft_hi_s=100_000)) == [480_000, 500_000]
    assert selected(Segment(category="politics")) == [500_000]
    assert selected(Segment(side="yes")) == [20_000, 500_000]
    assert selected(Segment(side="no", category="sports")) == [480_000, 990_000]


@pytest.mark.parametrize(
    "bad",
    [
        {"price_lo_e6": 5, "price_hi_e6": 5},
        {"tleft_lo_s": 10, "tleft_hi_s": 10},
        {"side": "maybe"},
    ],
)
def test_a_malformed_segment_is_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Segment(**bad)  # type: ignore[arg-type]


def test_a_prereg_needs_segments_but_not_too_many_or_duplicates() -> None:
    config = StudyConfig()
    with pytest.raises(PreregistrationError):
        Prereg((), config)
    many = tuple(Segment(price_lo_e6=i, price_hi_e6=i + 1) for i in range(13))
    with pytest.raises(PreregistrationError):
        Prereg(many, config)
    Prereg(many[:12], config)
    with pytest.raises(PreregistrationError):
        Prereg((Segment(), Segment()), config)


def test_each_segment_is_tested_at_its_share_of_the_family_tail() -> None:
    prereg = Prereg(
        (Segment(), Segment(category="x"), Segment(category="y"), Segment(side="no")), StudyConfig()
    )
    assert prereg.alpha() == pytest.approx(0.025 / 4)


def test_an_exploratory_segment_spends_none_of_the_family_tail() -> None:
    deciding = (Segment(), Segment(category="x"), Segment(category="y"))
    prereg = Prereg((*deciding, Segment(category="small", exploratory=True)), StudyConfig())
    assert prereg.alpha() == pytest.approx(0.025 / 3)
    assert len(prereg.segments) == 4  # it still counts toward the limit of twelve
    twelve = tuple(Segment(price_lo_e6=i, price_hi_e6=i + 1) for i in range(11))
    with pytest.raises(PreregistrationError):
        Prereg(
            (*twelve, Segment(category="a", exploratory=True), Segment(category="b")), StudyConfig()
        )


def test_a_pre_registration_of_only_exploratory_segments_is_refused() -> None:
    with pytest.raises(PreregistrationError, match="not exploratory"):
        Prereg((Segment(exploratory=True),), StudyConfig())


def test_the_exploratory_flag_is_part_of_the_locked_digest() -> None:
    plain = Prereg((Segment(), Segment(category="x")), StudyConfig())
    flagged = Prereg((Segment(), Segment(category="x", exploratory=True)), StudyConfig())
    assert plain.digest() != flagged.digest()


def test_the_digest_changes_with_the_segments_and_with_the_settings() -> None:
    base = Prereg((Segment(),), StudyConfig())
    assert base.digest() == Prereg((Segment(),), StudyConfig()).digest()
    assert base.digest() != Prereg((Segment(category="x"),), StudyConfig()).digest()
    assert base.digest() != Prereg((Segment(),), StudyConfig(min_events=10)).digest()


def test_the_ledger_locks_the_first_pre_registration(tmp_path: Path) -> None:
    ledger = PreregLedger(tmp_path / "state" / "prereg.json")
    first = Prereg((Segment(),), StudyConfig())
    ledger.open_test(first)
    ledger.open_test(first)  # the same registration may open the test again
    with pytest.raises(PreregistrationError, match="locked"):
        ledger.open_test(Prereg((Segment(category="x"),), StudyConfig()))
    with pytest.raises(PreregistrationError):
        ledger.open_test(Prereg((Segment(),), StudyConfig(min_events=10)))


def test_the_config_rejects_bad_bands_and_fractions() -> None:
    for bad in (
        {"fractions": (0.5, 0.5, 0.5)},
        {"fractions": (1.0, 0.0, 0.0)},
        {"price_edges_e6": (0, 500_000, 400_000, 1_000_000)},
        {"price_edges_e6": (10_000, 1_000_000)},
        {"tleft_edges_s": (5, 10)},
        {"verdict_maker_share_e2": 101},
    ):
        with pytest.raises(ValueError):
            StudyConfig(**bad)


def test_the_time_reference_can_differ_by_category() -> None:
    config = StudyConfig(reference_by_category=(("sports", ("close_time",)),))
    assert config.reference_for("sports") == ("close_time",)
    assert config.reference_for("politics") == ("close_time", "expected_expiration_time")
    assert dataclasses.is_dataclass(config)


def test_calibration_reports_the_weighted_gap_between_outcome_and_price() -> None:
    banded = add_bands(frame(), StudyConfig())
    table = calibration_table(banded, ["category"])
    sports = table.filter(pl.col("category") == "sports").row(0, named=True)
    assert sports["trades"] == 3
    assert sports["events"] == 2
    assert sports["contracts_e2"] == 600
    assert sports["mean_price"] == pytest.approx((0.02 * 100 + 0.48 * 300 + 0.99 * 200) / 600)
    assert sports["yes_rate"] == pytest.approx((300 + 200) / 600)
    assert sports["gap"] == pytest.approx(sports["yes_rate"] - sports["mean_price"])


def test_band_labels_name_the_price_bands_in_cents() -> None:
    labels = band_labels(StudyConfig())
    assert labels[0] == "0-5c"
    assert labels[-1] == "95-100c"
    assert len(labels) == 12


def test_a_segments_lower_edges_are_inclusive_and_its_upper_edges_exclusive() -> None:
    edge = pl.DataFrame(
        {
            "p": [400_000, 500_000],
            "tleft_s": [600, 1200],
            "category": ["x", "x"],
            "taker_yes": [True, True],
        }
    ).lazy()

    def picked(segment: Segment) -> int:
        return edge.filter(segment.expr()).collect().height

    assert picked(Segment(price_lo_e6=400_000, price_hi_e6=500_000)) == 1  # 400_000 in, 500_000 out
    assert picked(Segment(price_lo_e6=500_000)) == 1
    assert picked(Segment(tleft_lo_s=600, tleft_hi_s=1200)) == 1  # 600 in, 1200 out
    assert picked(Segment(tleft_lo_s=1200)) == 1
