from typing import Any

import httpx
import pytest
from sparq_analytics.history.rates import RateController, RateLimitPersisting
from sparq_core.config import Environment, KalshiConfig
from sparq_core.rest import KalshiPublicClient


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(max_rate: float = 10.0, **kwargs: Any) -> tuple[KalshiPublicClient, RateController, Clock]:
    clock = Clock()
    client = KalshiPublicClient(
        KalshiConfig(environment=Environment.PRODUCTION),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )
    return client, RateController(client, max_rate, clock=clock, **kwargs), clock


def test_it_starts_at_the_maximum_and_does_nothing_while_all_is_well() -> None:
    client, controller, clock = make()
    before = client.bucket.rate
    clock.now += 5
    controller.update()
    assert controller.rate == 10.0
    assert client.bucket.rate == before
    assert controller.slowdowns == 0


def test_a_429_halves_the_rate_and_the_bucket_follows() -> None:
    client, controller, _ = make()
    client.stats.rate_limited += 1
    controller.update()
    assert controller.rate == 5.0
    assert client.bucket.rate == 50.0  # tokens a second: the rate times the request cost of 10
    client.stats.rate_limited += 3  # several 429s inside one page are one slowdown
    controller.update()
    assert controller.rate == 2.5
    assert controller.slowdowns == 2


def test_the_rate_never_falls_below_the_minimum() -> None:
    client, controller, _ = make(give_up_events=100)
    for _ in range(10):
        client.stats.rate_limited += 1
        controller.update()
    assert controller.rate == 1.0
    assert client.bucket.rate == 10.0


def test_a_quiet_spell_brings_the_rate_back_up_in_steps_to_the_maximum() -> None:
    client, controller, clock = make()
    client.stats.rate_limited += 1
    controller.update()  # 5.0
    clock.now += 59
    controller.update()
    assert controller.rate == 5.0  # not yet
    clock.now += 2
    controller.update()
    assert controller.rate == 5.5
    for _ in range(20):
        clock.now += 61
        controller.update()
    assert controller.rate == 10.0  # capped
    assert [rate for _, rate in controller.history][:3] == [10.0, 5.0, 5.5]


def test_a_new_429_resets_the_quiet_spell() -> None:
    client, controller, clock = make()
    client.stats.rate_limited += 1
    controller.update()
    clock.now += 50
    client.stats.rate_limited += 1
    controller.update()
    assert controller.rate == 2.5
    clock.now += 50
    controller.update()
    assert controller.rate == 2.5  # 50 quiet seconds since the last change: not enough


def test_persistent_429s_stop_the_run() -> None:
    client, controller, clock = make()
    with pytest.raises(RateLimitPersisting, match=r"6 rate-limit responses in 300 s"):
        for _ in range(6):
            client.stats.rate_limited += 1
            clock.now += 10
            controller.update()


def test_old_429s_expire_from_the_window() -> None:
    client, controller, clock = make()
    for _ in range(5):
        client.stats.rate_limited += 1
        controller.update()
        clock.now += 100
    client.stats.rate_limited += 1  # the sixth, but the first is now older than 300 s
    controller.update()
    assert controller.slowdowns == 6


@pytest.mark.parametrize(
    "kwargs", [{"min_rate": 0.0}, {"min_rate": 20.0}, {"factor": 0.0}, {"factor": 1.0}]
)
def test_bad_settings_are_refused(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        make(**kwargs)
