import math

import polars as pl
import pytest
from sparq_analytics.study.ladder import fit_band_gaps, scores


def frame(rows: list[tuple[int, int, int, int]]) -> pl.LazyFrame:
    """Rows of (yes price in millionths, count in hundredths, outcome, price band)."""
    return pl.DataFrame(
        {
            "p": [r[0] for r in rows],
            "c": [r[1] for r in rows],
            "y": [r[2] for r in rows],
            "price_band": [r[3] for r in rows],
        },
        schema={"p": pl.Int64, "c": pl.Int64, "y": pl.Int8, "price_band": pl.Int8},
    ).lazy()


def test_scores_are_the_contract_weighted_brier_and_log_loss() -> None:
    # a yes at 80c that happened (miss 0.2) and a no at 20c that happened (miss 0.2)
    result = scores(frame([(800_000, 100, 1, 0), (200_000, 300, 0, 0)]), pl.col("p") / 1e6)
    assert result.brier == pytest.approx(0.04)
    assert result.log_loss == pytest.approx(-math.log(0.8))
    assert result.contracts_e2 == 400


def test_a_confident_wrong_forecast_is_kept_finite_by_the_clip() -> None:
    result = scores(frame([(0, 100, 1, 0)]), pl.col("p") / 1e6)
    assert math.isfinite(result.log_loss)
    assert result.brier == pytest.approx(1.0, abs=1e-5)


def test_scores_of_no_trades_are_not_a_number() -> None:
    result = scores(frame([]), pl.col("p") / 1e6)
    assert math.isnan(result.brier) and math.isnan(result.log_loss)
    assert result.contracts_e2 == 0


def test_a_bands_gap_is_its_weighted_yes_rate_minus_its_weighted_mean_price() -> None:
    # band 0: 10c twice with one yes, so the yes rate is 0.5 against a 10c price: gap 0.4
    # band 1: 90c once, settled yes: gap 0.1
    gaps = fit_band_gaps(frame([(100_000, 100, 1, 0), (100_000, 100, 0, 0), (900_000, 500, 1, 1)]))
    assert gaps[0] == pytest.approx(0.4)
    assert gaps[1] == pytest.approx(0.1)
