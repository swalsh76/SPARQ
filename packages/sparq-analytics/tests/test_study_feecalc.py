import polars as pl
import pytest
from sparq_analytics.study.feecalc import MAX_COUNT_E2, check_bounds, maker_fee_expr, taker_fee_expr
from sparq_core.fees import maker_fee_e6, taker_fee_e6
from sparq_core.fixedpoint import E6

PRICES = [0, 1, 10_000, 130_000, 500_000, 777_777, 990_000, E6]
COUNTS = [0, 1, 100, 12_345, 10**9, MAX_COUNT_E2]
MULTIPLIERS = [0, 17_500, 500_000, E6, 3 * E6]


def grid() -> pl.DataFrame:
    rows = [(m, c, p) for m in MULTIPLIERS for c in COUNTS for p in PRICES]
    return pl.DataFrame(rows, schema=["m", "c", "p"], orient="row")


def test_the_taker_fee_column_equals_the_scalar_function_everywhere_on_a_grid() -> None:
    frame = grid()
    out = frame.with_columns(taker_fee_expr(pl.col("m"), pl.col("c"), pl.col("p")).alias("fee"))
    for row in out.iter_rows(named=True):
        assert row["fee"] == taker_fee_e6(row["m"], row["c"], row["p"])


@pytest.mark.parametrize("share", [0, 25, 50, 100])
def test_the_maker_fee_column_equals_the_scalar_function_everywhere_on_a_grid(share: int) -> None:
    out = grid().with_columns(
        maker_fee_expr(pl.col("m"), pl.lit(share), pl.col("c"), pl.col("p")).alias("fee")
    )
    for row in out.iter_rows(named=True):
        assert row["fee"] == maker_fee_e6(row["m"], share, row["c"], row["p"])


def test_the_largest_allowed_inputs_cannot_wrap_int128() -> None:
    worst = 10 * E6 * 7 * 100 * MAX_COUNT_E2 * (E6 // 2) * (E6 // 2)
    assert worst < 2**127


def test_inputs_in_range_pass_the_guard() -> None:
    check_bounds(grid(), multiplier="m", count="c", price="p")
    check_bounds(grid().head(0), multiplier="m", count="c", price="p")


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("p", E6 + 1, "price"),
        ("p", -1, "price"),
        ("c", MAX_COUNT_E2 + 1, "count"),
        ("c", -1, "count"),
        ("m", 10 * E6 + 1, "multiplier"),
        ("m", -1, "multiplier"),
    ],
)
def test_an_input_out_of_range_is_refused(column: str, value: int, message: str) -> None:
    frame = pl.DataFrame({"m": [E6], "c": [100], "p": [500_000]}).with_columns(
        pl.lit(value).alias(column)
    )
    with pytest.raises(ValueError, match=message):
        check_bounds(frame, multiplier="m", count="c", price="p")
