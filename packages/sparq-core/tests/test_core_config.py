import pytest
from sparq_core.config import ConfigError, Environment, KalshiConfig


def test_demo_is_the_default() -> None:
    config = KalshiConfig()
    assert config.environment is Environment.DEMO
    assert not config.is_production
    assert "demo" in config.rest_url


def test_production_is_an_explicit_choice() -> None:
    config = KalshiConfig(environment=Environment.PRODUCTION)
    assert config.is_production
    assert config.rest_url.startswith("https://")
    assert "demo" not in config.rest_url


def test_the_defaults_are_the_conservative_ones() -> None:
    config = KalshiConfig()
    assert config.rate_limit_per_second == 10.0
    assert config.burst_requests == 10.0
    assert config.request_cost_tokens == 10


@pytest.mark.parametrize(
    ("env", "expected"),
    [({}, Environment.DEMO), ({"KALSHI_ENV": "production"}, Environment.PRODUCTION),
     ({"KALSHI_ENV": " Production "}, Environment.PRODUCTION),
     ({"KALSHI_ENV": "DEMO"}, Environment.DEMO)],
)  # fmt: skip
def test_the_environment_comes_from_kalshi_env(env: dict[str, str], expected: Environment) -> None:
    assert KalshiConfig.from_env(env).environment is expected


def test_an_unknown_environment_is_refused() -> None:
    with pytest.raises(ConfigError, match="KALSHI_ENV"):
        KalshiConfig.from_env({"KALSHI_ENV": "staging"})


def test_the_rate_can_be_set_from_the_environment() -> None:
    config = KalshiConfig.from_env({"SPARQ_RATE_LIMIT_PER_SECOND": "2.5"})
    assert config.rate_limit_per_second == 2.5
    assert config.burst_requests == 2.5
    assert KalshiConfig.from_env({"SPARQ_RATE_LIMIT_PER_SECOND": "0.5"}).burst_requests == 1.0


@pytest.mark.parametrize("raw", ["fast", "", "0", "-1", "nan"])
def test_a_bad_rate_is_refused(raw: str) -> None:
    with pytest.raises(ConfigError):
        KalshiConfig.from_env({"SPARQ_RATE_LIMIT_PER_SECOND": raw})


def test_from_env_reads_the_process_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KALSHI_ENV", "production")
    monkeypatch.delenv("SPARQ_RATE_LIMIT_PER_SECOND", raising=False)
    assert KalshiConfig.from_env().is_production


@pytest.mark.parametrize(
    "bad",
    [
        {"rate_limit_per_second": 0.0},
        {"timeout_seconds": 0.0},
        {"backoff_base_seconds": -1.0},
        {"burst_requests": 0.5},
        {"request_cost_tokens": 0},
        {"max_retries": -1},
        {"backoff_base_seconds": 4.0, "backoff_max_seconds": 1.0},
    ],
)
def test_out_of_range_settings_are_refused(bad: dict[str, float]) -> None:
    with pytest.raises(ConfigError):
        KalshiConfig(**bad)  # type: ignore[arg-type]
