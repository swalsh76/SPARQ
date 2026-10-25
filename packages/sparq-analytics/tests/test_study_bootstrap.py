import math

import numpy as np
import pytest
from sparq_analytics.study import bootstrap
from sparq_analytics.study.bootstrap import EMPTY, cluster_bootstrap


def run(num: list[float], den: list[float], **kwargs: float) -> bootstrap.Estimate:
    options = {"tail": 0.025, "resamples": 2000, "seed": 1} | kwargs
    return cluster_bootstrap(
        np.array(num),
        np.array(den),
        tail=options["tail"],
        resamples=int(options["resamples"]),
        seed=int(options["seed"]),
    )


def test_the_point_estimate_is_the_ratio_of_the_sums() -> None:
    estimate = run([10.0, 20.0, 30.0], [1.0, 2.0, 3.0])
    assert estimate.point_e6 == pytest.approx(10.0)
    assert estimate.events == 3
    assert estimate.contracts_e2 == 6


def test_events_with_no_contracts_are_not_independent_events() -> None:
    estimate = run([10.0, 0.0, 0.0], [1.0, 0.0, 0.0])
    assert estimate.events == 1


def test_no_events_gives_the_empty_estimate() -> None:
    assert run([], []) is EMPTY
    assert math.isnan(EMPTY.point_e6)
    assert run([1.0], [0.0]) is EMPTY


def test_the_same_seed_gives_the_same_interval() -> None:
    num = [float(x) for x in range(1, 40)]
    den = [1.0] * 39
    assert run(num, den, seed=5) == run(num, den, seed=5)
    assert run(num, den, seed=5) != run(num, den, seed=6)


def test_a_larger_tail_gives_a_narrower_interval() -> None:
    rng = np.random.default_rng(0)
    num, den = rng.normal(1, 3, 200).tolist(), [1.0] * 200
    wide, narrow = run(num, den, tail=0.001), run(num, den, tail=0.1)
    assert wide.lower_e6 < narrow.lower_e6 < narrow.upper_e6 < wide.upper_e6


def test_resampling_in_several_chunks_matches_one_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(2)
    num, den = rng.normal(0, 1, 50), np.ones(50)
    whole = cluster_bootstrap(num, den, tail=0.025, resamples=400, seed=9)
    monkeypatch.setattr(bootstrap, "_CELLS_PER_CHUNK", 500)  # ten resamples at a time
    chunked = cluster_bootstrap(num, den, tail=0.025, resamples=400, seed=9)
    assert chunked.point_e6 == whole.point_e6
    assert chunked.lower_e6 == pytest.approx(whole.lower_e6, abs=0.35)


def test_the_interval_covers_the_true_mean_about_as_often_as_promised() -> None:
    rng = np.random.default_rng(11)
    covered = 0
    runs = 300
    for i in range(runs):
        num = rng.normal(2.0, 5.0, 150)
        estimate = cluster_bootstrap(num, np.ones(150), tail=0.025, resamples=400, seed=i)
        covered += estimate.lower_e6 <= 2.0 <= estimate.upper_e6
    assert 0.90 <= covered / runs <= 0.99


def test_with_no_true_edge_a_positive_lower_bound_is_rare() -> None:
    rng = np.random.default_rng(12)
    false_go = 0
    runs = 300
    for i in range(runs):
        num = rng.normal(0.0, 5.0, 100)
        estimate = cluster_bootstrap(num, np.ones(100), tail=0.025, resamples=400, seed=i)
        false_go += estimate.lower_e6 > 0
    assert false_go / runs <= 0.06


def test_resampling_whole_events_is_wider_than_resampling_correlated_trades() -> None:
    rng = np.random.default_rng(13)
    shock = rng.normal(0, 4, 80)  # one shock per event, shared by its 25 trades
    trades = shock[:, None] + rng.normal(0, 1, (80, 25))
    by_event = cluster_bootstrap(
        trades.sum(axis=1), np.full(80, 25.0), tail=0.025, resamples=1000, seed=1
    )
    by_trade = cluster_bootstrap(
        trades.ravel(), np.ones(trades.size), tail=0.025, resamples=1000, seed=1
    )
    event_width = by_event.upper_e6 - by_event.lower_e6
    trade_width = by_trade.upper_e6 - by_trade.lower_e6
    assert event_width > 3 * trade_width


def test_a_skewed_payoff_does_not_inflate_false_positives_at_the_far_tail() -> None:
    """Selling a 10c longshot wins a little 90% of the time and loses a lot 10% of the time, with a
    true edge of zero. The plain percentile interval fired 3.9% of the time here (nominal 0.6%);
    the studentized one must stay near the nominal rate."""
    rng = np.random.default_rng(14)
    tail, runs, n = 0.00625, 1500, 80
    false_positive = 0
    for i in range(runs):
        den = rng.integers(1, 7, n).astype(np.float64)
        num = np.where(rng.random(n) < 0.9, 0.1, -0.9) * den
        false_positive += cluster_bootstrap(num, den, tail=tail, resamples=400, seed=i).lower_e6 > 0
    assert false_positive / runs <= 0.012


def test_one_event_or_identical_events_give_a_zero_width_interval() -> None:
    one = run([5.0], [2.0])
    assert (one.lower_e6, one.point_e6, one.upper_e6) == (2.5, 2.5, 2.5)
    same = run([3.0, 3.0, 3.0], [1.0, 1.0, 1.0])
    assert same.lower_e6 == same.point_e6 == same.upper_e6 == 3.0


def test_the_interval_brackets_the_point_estimate() -> None:
    rng = np.random.default_rng(15)
    estimate = run(rng.normal(2, 6, 120).tolist(), [1.0] * 120)
    assert estimate.lower_e6 < estimate.point_e6 < estimate.upper_e6
