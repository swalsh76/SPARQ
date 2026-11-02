import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import polars as pl
import pytest
from forecasthelpers import RESULTS, model_transport, write_forecast_kalshi
from sparq_analytics.model import cli, forecast, read
from sparq_analytics.model.client import ModelClient, ModelSettings
from sparq_analytics.model.prompts import FORECAST
from sparq_analytics.survey.dump import SurveyDataError

SETTINGS = ModelSettings(url="http://model-node.test/v1", key="sk-test-key", model="small-model")


def asked(tmp_path: Path, mode: str) -> tuple[Path, pl.DataFrame, list[dict[str, Any]]]:
    kalshi = write_forecast_kalshi(tmp_path)
    drawn, _ = forecast.draw(forecast.population(kalshi))
    client = ModelClient(SETTINGS, transport=model_transport(mode))
    return kalshi, drawn, read.run_read(client, drawn, ask=forecast.ask_one)


def test_the_population_is_thin_settled_and_in_the_band(tmp_path: Path) -> None:
    markets = forecast.population(write_forecast_kalshi(tmp_path))
    assert (markets.height, markets["event_ticker"].n_unique()) == (241, 121)
    tickers = set(markets["ticker"])
    assert not tickers & {"TAIL", "LOW", "THICK", "LATE", "OPEN"} and "NOCAT" in tickers
    assert "result" not in markets.columns  # the forecast never holds an outcome
    nocat = markets.filter(pl.col("ticker") == "NOCAT").row(0, named=True)
    assert (nocat["category"], nocat["band"], nocat["price_e6"]) == ("unknown", "30 to 50", 400_000)
    m001 = markets.filter(pl.col("ticker") == "M001").row(0, named=True)
    assert (m001["title"], m001["rules_secondary"]) == ("Market M001", "A second rule.")
    assert (markets["contracts_a_day"] < 1_000).all()


def test_the_population_refuses_a_world_with_nothing_in_the_band(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kalshi = write_forecast_kalshi(tmp_path)
    monkeypatch.setattr(forecast, "BAND_LOW_E6", 960_000)  # above every price but none
    with pytest.raises(SurveyDataError, match="from 10 to 90 cents"):
        forecast.population(kalshi)


def test_the_draw_is_seeded_capped_and_split_by_closing_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    markets = forecast.population(write_forecast_kalshi(tmp_path))
    drawn, split = forecast.draw(markets)
    assert (drawn.height, split) == (241, date(2026, 9, 11))
    assert dict(drawn.group_by("part").len().iter_rows()) == {"fit": 80, "test": 161}
    assert (drawn.filter(pl.col("part") == "fit")["close_day"] < split).all()
    assert drawn["rank"].to_list() == list(range(241))
    again, _ = forecast.draw(markets.reverse())
    assert again["ticker"].to_list() == drawn["ticker"].to_list()  # the input order is irrelevant
    assert forecast.digest(again) == forecast.digest(drawn)
    monkeypatch.setattr(forecast, "PER_EVENT", 1)
    monkeypatch.setattr(forecast, "PER_CATEGORY", 30)
    capped, _ = forecast.draw(markets)
    assert capped["event_ticker"].n_unique() == capped.height == 91  # 30 a category, and NOCAT
    assert dict(capped.group_by("category").len().iter_rows())["Sports"] == 30
    monkeypatch.setattr(forecast, "SAMPLE", 50)
    assert forecast.draw(markets)[0].height == 50
    with pytest.raises(SurveyDataError, match="no market"):
        forecast.draw(markets.head(0))


def test_the_prompt_shows_the_text_and_the_day_but_never_the_price(tmp_path: Path) -> None:
    drawn, _ = forecast.draw(forecast.population(write_forecast_kalshi(tmp_path)))
    row = drawn.filter(pl.col("ticker") == "M001").row(0, named=True)
    text = forecast.prompt_of(row)
    assert "Today is 2026-08-31" in text  # M001 closes on 09-01
    for part in ("Title: Market M001", "Yes outcome: Yes M001", "No outcome: No M001"):
        assert part in text
    assert text.rstrip().endswith("Rules for M001.\nA second rule.")
    assert str(row["price_e6"]) not in text and "cent" not in text
    blank = row | {"title": None, "yes_sub_title": None, "no_sub_title": None}
    assert "Title: \n" in forecast.prompt_of(blank)
    assert '"probability"' in FORECAST and len(forecast.FORECAST_HASH) == 64


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"probability": 0.3}, 0.3),
        ({"probability": "0.45"}, 0.45),
        ({"probability": 0}, 0.0),
        ({"probability": 1}, 1.0),
        ({"probability": 1.5}, None),
        ({"probability": -0.1}, None),
        ({"probability": True}, None),
        ({"probability": None}, None),
        ({"probability": "NaN"}, None),
        ({"probability": "high"}, None),
        ({"probability": [0.2]}, None),
        (None, None),
    ],
)
def test_a_probability_is_a_number_from_zero_to_one(
    answer: dict[str, Any] | None, expected: float | None
) -> None:
    assert forecast.probability_of(answer) == expected


def test_each_market_is_asked_and_its_answer_parsed(tmp_path: Path) -> None:
    _, _, rows = asked(tmp_path, "informative")
    by = {r["ticker"]: r for r in rows}
    assert len(rows) == 241 and not by["M000"]["parsed"] and by["M000"]["probability"] is None
    yes = next(t for t in by if t.startswith("M") and t != "M000" and RESULTS[t] == "yes")
    assert by[yes]["parsed"] and by[yes]["probability"] in (0.7, 0.3)
    assert set(by[yes]) == set(forecast.ROW_SCHEMA)
    assert forecast.frame_of(rows).schema == pl.Schema(forecast.ROW_SCHEMA)


def test_a_node_that_does_not_answer_is_an_error_row(tmp_path: Path) -> None:
    drawn, _ = forecast.draw(forecast.population(write_forecast_kalshi(tmp_path)))

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="no")

    client = ModelClient(SETTINGS, transport=httpx.MockTransport(down), sleep=lambda _: None)
    row = forecast.ask_one(client, drawn.row(0, named=True))
    assert row["error"] == "the model node answered 500" and not row["parsed"]


def test_the_forecast_summary_reads_no_outcome(tmp_path: Path) -> None:
    kalshi, drawn, rows = asked(tmp_path, "noise")
    markets = forecast.population(kalshi)
    summary = forecast.summarize(rows, markets, drawn, date(2026, 9, 11), 100, 60.0)
    assert summary["population"]["markets"] == 241 and summary["population"]["events"] == 121
    assert summary["population"]["by_category"]["unknown"] == {"markets": 1, "events": 1}
    assert summary["by_part"]["test"]["markets"] == 161 and summary["split_day"] == "2026-09-11"
    assert (summary["asked"], summary["parsed"], summary["limit"]) == (241, 240, 100)
    assert summary["calls"] == 241 and summary["markets_a_minute"] == pytest.approx(241.0)
    spread = summary["spread"]
    assert spread["answers"] == 240
    assert 0.1 <= spread["quantiles"]["0.05"] < spread["quantiles"]["0.95"] <= 0.9
    assert spread["distinct"] > 200 and spread["at_one_half"] == 0
    assert abs(spread["price_correlation"]) < 0.3
    assert "result" not in json.dumps(summary, default=str)
    nothing = forecast.summarize([], markets, drawn, date(2026, 9, 11), None, 0.0)
    assert nothing["spread"] == {"answers": 0} and nothing["parse_rate"] is None


def test_a_correlation_without_variation_is_none() -> None:
    assert forecast.correlation(np.full(5, 0.5), np.arange(5.0)) is None  # one answer every time
    assert forecast.correlation(np.arange(2.0), np.arange(2.0)) is None
    assert forecast.correlation(np.arange(5.0), -np.arange(5.0)) == pytest.approx(-1.0)


def test_the_logistic_fit_recovers_known_coefficients() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(size=(20_000, 2))
    z = 0.3 + 1.2 * x[:, 0] - 0.5 * x[:, 1]
    y = (rng.random(20_000) < 1 / (1 + np.exp(-z))).astype(float)
    beta, steps = forecast.fit_logistic(x, y)
    assert beta == pytest.approx([0.3, 1.2, -0.5], abs=0.06) and steps < 15
    flat, _ = forecast.fit_logistic(np.column_stack([x[:, 0], np.zeros(20_000)]), y)
    assert flat[2] == 0.0  # a column with no variation takes no weight


def test_a_fit_that_does_not_settle_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(forecast, "NEWTON_STEPS", 1)
    x = np.array([[0.0], [1.0], [2.0], [3.0]])
    with pytest.raises(SurveyDataError, match="did not settle"):
        forecast.fit_logistic(x, np.array([0.0, 1.0, 0.0, 1.0]))


def test_the_log_loss_is_in_nats() -> None:
    z, y = np.array([0.0, 2.0, 2.0]), np.array([1.0, 1.0, 0.0])
    assert forecast.log_loss(z, y) == pytest.approx(
        [np.log(2), np.log(1 + np.exp(-2)), np.log(1 + np.exp(2))]
    )
    assert forecast.logit(np.array([0.5, 0.9])) == pytest.approx([0.0, np.log(9)])


def test_a_placebo_donor_is_of_another_event_in_the_same_stratum() -> None:
    rng = np.random.default_rng(3)
    events = np.array(["a", "a", "b", "b", "c", "c", "d", "e", "e", "g", "f"])
    strata = np.array(["x"] * 6 + ["y"] * 4 + ["z"])
    for _ in range(50):
        donors, left = forecast.placebo_donors(events, strata, rng)
        assert left == 1  # "f" is alone in its stratum and keeps its own value
        assert (strata[donors] == strata).all()
        assert (events[donors] != events)[:10].all()
        assert sorted(donors.tolist()) == list(range(11))  # a reassignment: nothing twice


def test_an_informative_model_gains_and_clears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kalshi, _, rows = asked(tmp_path, "informative")
    monkeypatch.setattr(forecast, "MIN_GAIN", 0.01)  # 161 test markets: the placebo is noisier
    result = forecast.score(
        kalshi, forecast.frame_of(rows), min_markets=150, min_events=80, placebos=40
    )
    gain = result["gain"]
    assert (gain["markets"], gain["events"]) == (161, 81) and gain["lower"] > 0.05
    assert result["unanswered_by_part"] == {"fit": 1} and result["fit"]["markets"] == 79
    losses = result["test_log_loss"]
    assert gain["gain"] == pytest.approx(losses["price_fitted"] - losses["price_and_model"])
    assert result["coefficients"]["price_and_model"][2] > 0.5
    assert result["placebo"]["upper"] < 0.01 and result["placebo"]["own_event_at_most"] == 1
    assert all(result["clears"].values()) and result["passes"]
    assert set(result["by_category"]) == {"Economics", "Financials", "Sports", "unknown"}
    assert not result["by_category"]["unknown"]["judged"]
    assert list(result["by_week"]) == ["2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
    assert sum(w["markets"] for w in result["by_week"].values()) == 161


def test_noise_gains_nothing_and_fails(tmp_path: Path) -> None:
    kalshi, _, rows = asked(tmp_path, "noise")
    result = forecast.score(kalshi, forecast.frame_of(rows), placebos=10)
    assert result["gain"]["lower"] < 0
    assert not result["clears"]["enough"]  # 161 markets against N6's 3,000
    assert not result["clears"]["lower_above_zero"] and not result["passes"]
    assert result["bars"] == {"min_gain": 0.002, "min_markets": 3_000, "min_events": 300}
    unplaced = forecast.score(kalshi, forecast.frame_of(rows), placebos=0)
    assert unplaced["placebo"]["upper"] is None and not unplaced["clears"]["placebo"]


def test_the_score_refuses_markets_without_a_result_or_a_part(tmp_path: Path) -> None:
    kalshi, _, rows = asked(tmp_path, "noise")
    frame = forecast.frame_of(rows)
    stray = frame.filter(pl.col("parsed")).head(1).with_columns(ticker=pl.lit("NOWHERE"))
    with pytest.raises(SurveyDataError, match="no settled result"):
        forecast.score(kalshi, pl.concat([frame, stray]), placebos=0)
    with pytest.raises(SurveyDataError, match="no answered market"):
        forecast.score(kalshi, frame.filter(pl.col("part") == "test"), placebos=0)


def write_settings(path: Path) -> Path:
    path.write_text('url = "http://x/v1"\nkey = "sk-k"\nmodel = "small-model"\n', encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path


def test_the_commands_forecast_and_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(
        cli,
        "make_client",
        lambda s, cache: ModelClient(
            s, cache=cache, transport=model_transport("informative", prompts)
        ),
    )
    settings = write_settings(tmp_path / "model.toml")
    kalshi, out = write_forecast_kalshi(tmp_path), tmp_path / "out"
    argv = ["--kalshi", str(kalshi), "--out", str(out)]
    # a pilot: the first ten of the draw, which score refuses
    assert cli.main(["forecast", *argv, "--settings", str(settings), "--limit", "10"]) == 0
    pilot = json.loads(capsys.readouterr().out)
    assert (pilot["asked"], pilot["limit"], pilot["drawn"]) == (10, 10, 241)
    assert pilot["model"] == "small-model" and pilot["prompt"] == forecast.FORECAST_HASH
    assert cli.main(["score", *argv]) == 1
    assert "is a pilot (--limit 10)" in capsys.readouterr().err
    # the full run asks only what the pilot did not
    assert cli.main(["forecast", *argv, "--settings", str(settings)]) == 0
    full = json.loads(capsys.readouterr().out)
    assert (full["asked"], full["cached"], full["calls"], len(prompts)) == (241, 10, 231, 241)
    assert pl.read_parquet(out / "model-forecast.parquet").height == 241
    monkeypatch.setattr(forecast, "PLACEBOS", 5)
    assert cli.main(["score", *argv]) == 0
    scored = json.loads(capsys.readouterr().out)
    assert scored["gain"]["markets"] == 161 and scored["model"] == "small-model"
    assert scored["placebo"]["draws"] == 5
    assert scored["sample_digest"] == full["sample_digest"] and scored["split_day"] == "2026-09-11"
    assert json.loads((out / "model-score.json").read_text(encoding="utf-8")) == scored
    # refusals
    replies = out / "model-forecast.parquet"
    pl.read_parquet(replies).head(5).write_parquet(replies)
    assert cli.main(["score", *argv]) == 1
    assert "holds 5 replies for 241 drawn markets" in capsys.readouterr().err
    assert cli.main(["score", *argv, "--limit", "5"]) == 1
    assert "--limit does not apply to score" in capsys.readouterr().err
    assert cli.main(["score", *argv, "--settings", str(settings)]) == 1
    assert "--settings does not apply to score" in capsys.readouterr().err
    assert cli.main(["score", "--kalshi", str(kalshi), "--out", str(tmp_path / "none")]) == 1
    assert "model-forecast.json" in capsys.readouterr().err
