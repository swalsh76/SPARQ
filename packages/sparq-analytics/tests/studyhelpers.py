"""Typed helpers for the study tests: polars scalars come back as a wide union, and the scalar
fee functions take the fixed-point types."""

import datetime as dt

import polars as pl
from sparq_core.fees import maker_fee_e6, taker_fee_e6
from sparq_core.fixedpoint import CountE2, DollarsE6


def latest(series: pl.Series) -> dt.datetime:
    value = series.max()
    assert isinstance(value, dt.datetime)
    return value


def earliest(series: pl.Series) -> dt.datetime:
    value = series.min()
    assert isinstance(value, dt.datetime)
    return value


def taker(count_e2: int, price_e6: int, multiplier_e6: int = 1_000_000) -> int:
    return taker_fee_e6(multiplier_e6, CountE2(count_e2), DollarsE6(price_e6))


def maker(count_e2: int, price_e6: int, share_e2: int, multiplier_e6: int = 1_000_000) -> int:
    return maker_fee_e6(multiplier_e6, share_e2, CountE2(count_e2), DollarsE6(price_e6))
