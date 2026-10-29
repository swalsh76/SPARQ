"""The exact fee formulas of ``sparq_core.fees`` as polars expressions, for whole columns.

Identical integer arithmetic (the numerator kept exact in ``Int128`` and rounded up), checked
against the scalar functions in the tests. The largest numerator allowed is about 10^34 and
``Int128`` holds about 1.7 x 10^38, but polars wraps silently past that, so
:func:`check_bounds` guards the inputs.
"""

import polars as pl
from sparq_core.fees import MAKER_SHARE_E2
from sparq_core.fixedpoint import E6

_I128 = pl.Int128
_TAKER_DENOMINATOR = E6 * 100 * 100 * E6
_MAKER_DENOMINATOR = _TAKER_DENOMINATOR * 100
MAX_COUNT_E2 = 10**12  # ten billion contracts in one trade; the numerator then stays below 10^34


def _lit(value: int) -> pl.Expr:
    return pl.lit(value, dtype=_I128)


def _ceil_div(numerator: pl.Expr, denominator: int) -> pl.Expr:
    return ((numerator + _lit(denominator - 1)) // _lit(denominator)).cast(pl.Int64)


def taker_fee_expr(multiplier_e6: pl.Expr, count_e2: pl.Expr, price_e6: pl.Expr) -> pl.Expr:
    m, c, p = multiplier_e6.cast(_I128), count_e2.cast(_I128), price_e6.cast(_I128)
    return _ceil_div(m * _lit(7) * c * p * (_lit(E6) - p), _TAKER_DENOMINATOR)


def maker_share_expr(fee_type: pl.Expr, multiplier_e6: pl.Expr) -> pl.Expr:
    """The series' maker share in hundredths: null outside the quadratic family or without a
    multiplier, so the fee is unknown rather than zero."""
    return pl.when(multiplier_e6.is_not_null()).then(
        fee_type.replace_strict(MAKER_SHARE_E2, default=None, return_dtype=pl.Int64)
    )


def fee_terms_known(share_e2: pl.Expr, multiplier_e6: pl.Expr) -> pl.Expr:
    """Whether a maker fee can be computed from the series' terms."""
    return share_e2.is_not_null() & multiplier_e6.is_not_null()


def maker_fee_expr(
    multiplier_e6: pl.Expr, share_e2: pl.Expr, count_e2: pl.Expr, price_e6: pl.Expr
) -> pl.Expr:
    m, s = multiplier_e6.cast(_I128), share_e2.cast(_I128)
    c, p = count_e2.cast(_I128), price_e6.cast(_I128)
    return _ceil_div(m * _lit(7) * s * c * p * (_lit(E6) - p), _MAKER_DENOMINATOR)


def check_bounds(frame: pl.DataFrame, *, multiplier: str, count: str, price: str) -> None:
    """Raise unless every input is in range, so no ``Int128`` product can wrap."""
    bounds = frame.select(
        pl.col(multiplier).min().alias("m_lo"),
        pl.col(multiplier).max().alias("m_hi"),
        pl.col(count).min().alias("c_lo"),
        pl.col(count).max().alias("c_hi"),
        pl.col(price).min().alias("p_lo"),
        pl.col(price).max().alias("p_hi"),
    ).row(0, named=True)
    if frame.height == 0:
        return
    if bounds["p_lo"] < 0 or bounds["p_hi"] > E6:
        raise ValueError("a price is outside 0 to one dollar")
    if bounds["c_lo"] < 0 or bounds["c_hi"] > MAX_COUNT_E2:
        raise ValueError("a count is outside 0 to ten billion contracts")
    if bounds["m_lo"] < 0 or bounds["m_hi"] > 10 * E6:
        raise ValueError("a fee multiplier is outside 0 to ten")
