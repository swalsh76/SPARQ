"""The edge arithmetic, on trades small enough to work out by hand, and against a slow reference."""

import datetime as dt

import polars as pl
import pytest
from sparq_analytics.study.edge import add_edges
from studyhelpers import maker, taker

T0 = dt.datetime(2026, 5, 1, 12, 0, 0, tzinfo=dt.UTC)


def trades(rows: list[tuple[int, int, int, bool, int]]) -> pl.LazyFrame:
    """Rows of (seconds after T0, yes price in cents, count in hundredths, taker bought yes, y)
    for one market whose fee multiplier is one with a quarter maker share."""
    data = {
        "ticker": ["M"] * len(rows),
        "t": [T0 + dt.timedelta(seconds=r[0]) for r in rows],
        "p": [r[1] * 10_000 for r in rows],
        "c": [r[2] for r in rows],
        "taker_yes": [r[3] for r in rows],
        "y": [r[4] for r in rows],
    }
    frame = pl.DataFrame(data).with_columns(pl.col("t").dt.cast_time_unit("us"))
    fees = [
        (
            taker(r[2], r[1] * 10_000),
            maker(r[2], r[1] * 10_000, 25),
            maker(r[2], r[1] * 10_000, 50),
        )
        for r in rows
    ]
    return frame.with_columns(
        pl.Series("tk_fee", [f[0] for f in fees], dtype=pl.Int64),
        pl.Series("mk_fee_nom", [f[1] for f in fees], dtype=pl.Int64),
        pl.Series("mk_fee_cons", [f[2] for f in fees], dtype=pl.Int64),
    ).lazy()


def test_a_taker_who_buys_yes_and_wins_earns_the_dollar_minus_price_and_fee() -> None:
    # 10 contracts bought at 40 cents that settle yes: gross 60 cents each, 6.00 dollars
    out = add_edges(trades([(0, 40, 1000, True, 1)])).collect().row(0, named=True)
    fee = taker(1000, 400_000)
    assert out["e_taker"] == 1000 * 600_000 - 100 * fee
    assert out["e_mk_opt_none"] == -1000 * 600_000  # the maker sold yes at 40 and paid out a dollar


def test_a_taker_who_buys_no_pays_the_complement_of_the_yes_price() -> None:
    # yes price 30c, taker buys NO at 70c, market settles no: gross 30 cents each
    out = add_edges(trades([(0, 30, 200, False, 0)])).collect().row(0, named=True)
    assert out["e_taker"] == 200 * 300_000 - 100 * taker(200, 300_000)


def test_the_maker_edge_is_the_negative_of_the_taker_gross_less_the_maker_fee() -> None:
    out = add_edges(trades([(0, 40, 1000, True, 0)])).collect().row(0, named=True)
    # the taker lost 40 cents each, so the maker kept 40 cents each
    assert out["e_mk_opt_none"] == 1000 * 400_000
    assert out["e_mk_opt_nom"] == 1000 * 400_000 - 100 * maker(1000, 400_000, 25)
    assert out["e_mk_opt_cons"] == 1000 * 400_000 - 100 * maker(1000, 400_000, 50)


def test_a_later_trade_through_the_level_fills_the_pessimistic_maker() -> None:
    # taker buys yes at 40c; a later second trades at 41c, through a resting ask at 40c
    out = add_edges(trades([(0, 40, 100, True, 1), (5, 41, 100, True, 1)])).collect()
    first = out.sort("t").row(0, named=True)
    assert first["filled_pess"] is True
    assert first["c_pess"] == 100


def test_a_trade_that_only_touches_the_level_does_not_fill_the_pessimistic_maker() -> None:
    out = add_edges(trades([(0, 40, 100, True, 1), (5, 40, 100, True, 1)])).collect()
    assert out.sort("t").row(0, named=True)["filled_pess"] is False


def test_trades_in_the_same_second_are_not_later_than_each_other() -> None:
    out = add_edges(trades([(0, 40, 100, True, 1), (0, 45, 100, True, 1)])).collect()
    assert not out["filled_pess"].any()


def test_a_buy_of_no_is_filled_when_the_price_later_falls_through() -> None:
    # taker hits a resting yes bid at 40c (buys NO); later the yes price trades at 39c
    out = add_edges(trades([(0, 40, 100, False, 0), (9, 39, 100, False, 0)])).collect()
    assert out.sort("t").row(0, named=True)["filled_pess"] is True


def test_a_move_the_other_way_does_not_fill_the_pessimistic_maker() -> None:
    out = add_edges(trades([(0, 40, 100, True, 1), (9, 39, 100, True, 1)])).collect()
    assert out.sort("t").row(0, named=True)["filled_pess"] is False


def test_the_last_trade_of_a_market_is_never_filled_pessimistically() -> None:
    out = add_edges(trades([(0, 40, 100, True, 1), (5, 41, 100, True, 1)])).collect()
    assert out.sort("t").row(1, named=True)["filled_pess"] is False


def test_pessimistic_columns_are_zero_when_not_filled() -> None:
    out = add_edges(trades([(0, 40, 100, True, 1)])).collect().row(0, named=True)
    assert out["e_mk_pess_cons"] == 0
    assert out["c_pess"] == 0


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_polars_edges_match_a_slow_python_reference(seed: int) -> None:
    import random

    rng = random.Random(seed)  # noqa: S311
    rows = [
        (i * 3, rng.randint(1, 99), rng.randint(1, 5000), rng.random() < 0.5, rng.randint(0, 1))
        for i in range(60)
    ]
    out = add_edges(trades(rows)).collect().sort("t")
    for index, (_, cents, count, buys_yes, yes) in enumerate(rows):
        price = cents * 10_000
        paid = price if buys_yes else 1_000_000 - price
        won = yes if buys_yes else 1 - yes
        gross = count * (won * 1_000_000 - paid)
        later = [r for r in rows[index + 1 :]]
        through = any(r[1] > cents for r in later) if buys_yes else any(r[1] < cents for r in later)
        row = out.row(index, named=True)
        assert row["e_taker"] == gross - 100 * taker(count, price)
        assert row["e_mk_opt_cons"] == -gross - 100 * maker(count, price, 50)
        assert row["filled_pess"] is through


def test_a_buy_of_no_that_only_touches_the_level_does_not_fill_the_pessimistic_maker() -> None:
    out = add_edges(trades([(0, 40, 100, False, 0), (5, 40, 100, False, 0)])).collect()
    assert out.sort("t").row(0, named=True)["filled_pess"] is False


def test_a_buy_of_no_in_the_same_second_as_a_lower_price_is_not_filled() -> None:
    out = add_edges(trades([(0, 40, 100, False, 0), (0, 35, 100, False, 0)])).collect()
    assert not out["filled_pess"].any()
