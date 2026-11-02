import json
import os
import runpy
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest
from modelhelpers import MARKETS, model_transport, write_reading_kalshi
from sparq_analytics.model import cli, read
from sparq_analytics.model.client import ModelClient, ModelError, ModelSettings, Reply
from sparq_analytics.model.prompts import READ_WEATHER
from sparq_analytics.survey.dump import TAIL
from sparq_analytics.weather.stations import WeatherDataError

SETTINGS = ModelSettings(url="http://model-node.test/v1", key="sk-test-key", model="small-model")


def client_for(calls: list[httpx.Request], tmp_path: Path, **kwargs: Any) -> ModelClient:
    return ModelClient(
        SETTINGS, cache=tmp_path / "cache", transport=model_transport(calls, **kwargs)
    )


def window_and_older(kalshi: Path) -> pl.DataFrame:
    window = read.reading_markets(kalshi, read.READ_FIRST, read.READ_LAST)
    older = read.reading_markets(kalshi, date(2000, 1, 1), date(2026, 7, 31))
    return pl.concat(
        [
            window.with_columns(sample=pl.lit("window")),
            older.with_columns(sample=pl.lit("older")),
        ]
    ).join(read.series_sites(window), on="series", how="left")


def test_the_markets_read_are_s2_s_window_and_never_its_test_part(tmp_path: Path) -> None:
    kalshi = write_reading_kalshi(tmp_path)
    window = read.reading_markets(kalshi, read.READ_FIRST, read.READ_LAST)
    assert window["ticker"].to_list() == [
        "H1",
        "H2",
        "H3",
        "H4",
        "H5",
        "H6",
        "H7",
        "H8",
        "R1",
        "R2",
    ]
    rows = {r["ticker"]: r for r in window.to_dicts()}
    assert (rows["H1"]["group"], rows["R1"]["group"]) == ("highs", "rain")
    assert (rows["H1"]["site"], rows["H4"]["site"], rows["H5"]["site"]) == (
        "CLINYC",
        "CLINYC",
        None,
    )
    assert (rows["H1"]["strike_type"], rows["H1"]["floor"], rows["H1"]["cap"]) == (
        "between",
        85.0,
        86.0,
    )
    assert (rows["H6"]["strike_type"], rows["H6"]["floor"]) == (None, None)
    assert rows["R2"]["site"] == "CLIMIA" and rows["H3"]["cap"] == 79.0
    older = read.reading_markets(kalshi, date(2000, 1, 1), date(2026, 7, 31))
    assert older["ticker"].to_list() == ["O1", "O2"]
    with pytest.raises(WeatherDataError, match="test part"):
        read.reading_markets(kalshi, read.READ_FIRST, date(2026, 10, 11))  # X1's day
    # a seeded shuffle, the same markets every time, cut to the limit
    assert read.sample(window, 4)["ticker"].to_list() == read.sample(window, 4)["ticker"].to_list()
    assert read.sample(window, None).height == 10 and read.sample(window, 4).height == 4


def test_a_reply_is_read_wherever_the_object_sits() -> None:
    obj = '{"site": "CLINYC", "direction": "greater", "lower": 5, "upper": null}'
    for text in (obj, f"```json\n{obj}\n```", f"Sure! {obj} Hope that helps."):
        assert read.parse_reply(text) == json.loads(obj)
    assert read.parse_reply("{not json} then " + obj) == json.loads(obj)
    for text in ("", "I cannot tell.", '{"site": "X"}', "[1, 2]", "{broken"):
        assert read.parse_reply(text) is None


def test_sites_and_numbers_are_compared_as_the_map_names_them() -> None:
    assert [read.site_of(a) for a in ("CLINYC", " clinyc ", "KNYC", "knyc")] == ["CLINYC"] * 4
    assert [read.site_of(a) for a in ("KZZZ", "", None, 5)] == [None] * 4
    assert read.number("80") == 80.0 and read.number(None) is None and read.number("hot") is None
    assert read.same(86, 86.0) and not read.same(86, 86.5) and not read.same(None, 86)


def test_the_bounds_must_match_the_strike_fields() -> None:
    def ans(direction: str, lower: Any, upper: Any) -> dict[str, Any]:
        return {"site": None, "direction": direction, "lower": lower, "upper": upper}

    assert read.bounds_agree("between", 85.0, 86.0, ans("between", 85, "86"))
    assert not read.bounds_agree("between", 85.0, 86.0, ans("between", 85, 87))
    assert not read.bounds_agree("between", 85.0, 86.0, ans("between", None, 86))
    assert read.bounds_agree("greater", 86.0, None, ans("greater", 86, None))
    assert not read.bounds_agree("greater", 86.0, None, ans("greater", 85, None))
    assert read.bounds_agree("less", None, 79.0, ans("less", None, 79))
    assert not read.bounds_agree("less", None, 79.0, ans("less", None, 78))
    assert not read.bounds_agree(
        "greater", 86.0, None, ans("less", None, 86)
    )  # the wrong direction
    assert not read.bounds_agree("exotic", 1.0, 2.0, ans("exotic", 1, 2))


def test_the_reading_check_against_the_hand_figures(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    rows = read.run_read(
        client_for(calls, tmp_path), window_and_older(write_reading_kalshi(tmp_path))
    )
    assert len(rows) == 12 and len(calls) == 12
    by = {r["ticker"]: r for r in rows}
    assert (
        by["H4"]["site_answer"] == "CLINYC" and by["H4"]["site_agree"] and by["H4"]["bounds_agree"]
    )
    assert by["H5"]["site_agree"] is None and by["H5"]["unnamed_answer"] == "wrong"
    assert by["H5"]["series_site"] == "CLINYC" and by["H1"]["unnamed_answer"] is None
    assert by["H6"]["bounds_agree"] is None and by["H6"]["site_agree"] is True
    assert (by["H7"]["parsed"], by["H7"]["site_agree"], by["H7"]["bounds_agree"]) == (
        False,
        False,
        False,
    )
    assert (by["H8"]["truncated"], by["H8"]["parsed"]) == (True, False)
    assert by["H3"]["bounds_agree"] is False and by["R2"]["site_agree"] is False
    summary = read.summarize(rows, wall_seconds=60.0)
    assert summary["markets"] == 10 and summary["parse_rate"] == 0.8
    highs, rain = summary["by_group"]["highs"], summary["by_group"]["rain"]
    assert (highs["markets"], highs["parsed"], highs["truncated"], highs["errors"]) == (8, 6, 1, 0)
    assert highs["parse_rate"] == 0.75
    assert (highs["site"]["n"], highs["site"]["agree"]) == (7, 5)
    assert highs["site"]["rate"] == pytest.approx(5 / 7)
    assert (highs["bounds"]["n"], highs["bounds"]["agree"]) == (7, 4)
    assert (highs["rules_naming_no_site"], highs["no_strike_type"]) == (1, 1)
    assert highs["unnamed_answers"] == dict.fromkeys(read.UNNAMED, 0) | {"wrong": 1}
    assert highs["disagreements"] == 3
    assert [e["ticker"] for e in highs["examples"]] == ["H3", "H7", "H8"]
    assert highs["examples"][1]["truth"] == ["CLINYC", "greater", 81.0, None]
    assert (rain["markets"], rain["parsed"], rain["disagreements"]) == (2, 2, 1)
    assert (rain["site"]["agree"], rain["site"]["n"], rain["bounds"]["agree"]) == (1, 2, 2)
    assert rain["rules_naming_no_site"] == 0 and sum(rain["unnamed_answers"].values()) == 0
    older = summary["earlier_sample"]
    assert (older["markets"], older["site"]["agree"], older["bounds"]["agree"]) == (2, 2, 1)
    assert not any(summary["clears"].values()) and not summary["parse_clears"]
    assert summary["passes"] is False
    assert (summary["calls"], summary["cached"]) == (12, 0)
    assert summary["markets_a_minute"] == 12.0 and summary["tokens_a_second"] is not None
    assert highs["site"]["lower"] is not None and highs["site"]["lower"] <= highs["site"]["rate"]
    assert rain["bounds"]["rate"] == 1.0 and rain["bounds"]["lower"] == 1.0  # width zero
    assert rain["bounds"]["events"] == 2 and not rain["bounds"]["clears"]  # too few events to judge


def test_a_rerun_is_answered_from_the_cache(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    markets = window_and_older(write_reading_kalshi(tmp_path))
    client = client_for(calls, tmp_path)
    first = read.run_read(client, markets, workers=2)
    again = read.run_read(client, markets, workers=2)
    assert len(calls) == 12 and all(r["cached"] for r in again)
    assert [r["content"] for r in again] == [r["content"] for r in first]
    assert read.summarize(again, 1.0)["cached"] == 12
    assert all("sk-test-key" not in r["content"] for r in again)
    assert "sk-test-key" not in " ".join(
        str(c.url) for c in calls
    )  # the key rides in a header only


def test_the_clearing_bar_needs_a_high_rate_and_a_high_lower_bound() -> None:
    def frame(agree: list[bool], events: int = 50) -> pl.DataFrame:
        return pl.DataFrame(
            {"event_ticker": [f"E{i % events}" for i in range(len(agree))], "flag": agree}
        )

    perfect = read.rate(frame([True] * 400), "flag")
    assert (perfect["rate"], perfect["lower"], perfect["clears"]) == (1.0, 1.0, True)
    assert perfect["exact_events"] == pytest.approx(TAIL ** (1 / 50))  # 50 events
    assert perfect["exact_markets"] == pytest.approx(TAIL ** (1 / 400))  # 400 markets
    weak = read.rate(frame([True] * 396 + [False] * 4), "flag")  # 99%
    assert weak["rate"] == 0.99 and weak["lower"] is not None and weak["lower"] <= 0.99
    assert weak["exact_events"] is None and weak["exact_markets"] is None  # the bootstrap speaks
    low = read.rate(frame([True] * 90 + [False] * 10), "flag")
    assert not low["clears"]
    one_event = read.rate(frame([True] * 10, events=1), "flag")  # nothing to bootstrap
    assert (one_event["lower"], one_event["clears"]) == (None, False)
    empty = read.rate(frame([]), "flag")
    assert empty["n"] == 0 and empty["rate"] is None and not empty["clears"]
    assert empty["exact_events"] is None and empty["exact_markets"] is None
    few = read.rate(frame([True] * 400, events=9), "flag")  # perfect, but only nine events
    assert (few["rate"], few["lower"], few["events"], few["clears"]) == (1.0, 1.0, 9, False)
    assert read.rate(frame([True] * 400, events=10), "flag")["clears"]


def test_the_run_stops_when_the_node_stops_answering(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    markets = pl.concat([window_and_older(write_reading_kalshi(tmp_path)).head(1)] * 25)
    with pytest.raises(ModelError, match="stopped answering"):
        read.run_read(client_for(calls, tmp_path, status=500), markets)
    assert read.ABORT_AFTER <= len(calls) <= read.ABORT_AFTER + 2  # at most two in flight
    # a failure is a row with its error, not a crash, below the limit
    one = read.run_read(client_for([], tmp_path / "x", status=500), markets.head(3))
    assert [r["error"] for r in one] == ["the model node answered 500"] * 3
    assert not any(r["parsed"] for r in one)
    assert read.summarize(one, 1.0)["by_group"]["highs"]["errors"] == 3


def test_the_commands_ping_and_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[httpx.Request] = []
    settings = tmp_path / "model.toml"
    settings.write_text(
        'url = "http://x/v1"\nkey = "sk-k"\nmodel = "small-model"\n', encoding="utf-8"
    )
    if settings.stat().st_mode & 0o077 and __import__("os").name == "posix":
        settings.chmod(0o600)
    monkeypatch.setattr(
        cli,
        "make_client",
        lambda s, cache: ModelClient(s, cache=cache, transport=model_transport(calls)),
    )
    assert cli.main(["ping", "--settings", str(settings)]) == 0
    assert json.loads(capsys.readouterr().out)["model"] == "small-model"
    kalshi, out = write_reading_kalshi(tmp_path), tmp_path / "out"
    argv = ["read", "--kalshi", str(kalshi), "--out", str(out), "--settings", str(settings)]
    assert cli.main([*argv, "--workers", "2"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["markets"] == 10 and "examples" not in printed["by_group"]["highs"]
    assert printed["model"] == "small-model" and printed["prompt"] == read.READ_WEATHER_HASH
    saved = json.loads((out / "model-read.json").read_text(encoding="utf-8"))
    assert len(saved["by_group"]["highs"]["examples"]) == 3
    assert pl.read_parquet(out / "model-read.parquet").height == 12
    assert (out / "model-cache").is_dir()
    # a limit cuts both samples
    assert cli.main([*argv, "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["markets"] == 1
    # refusals
    assert cli.main(["ping", "--settings", str(settings), "--limit", "5"]) == 1
    assert "--limit does not apply to ping" in capsys.readouterr().err
    assert cli.main([*argv, "--workers", "0"]) == 1
    assert "at least 1" in capsys.readouterr().err
    assert cli.main(["read", "--out", str(out), "--settings", str(settings)]) == 1
    assert "--kalshi is required" in capsys.readouterr().err
    assert cli.main(["ping", "--settings", str(tmp_path / "nowhere.toml")]) == 1
    assert "no model settings" in capsys.readouterr().err


def test_the_prompt_asks_for_the_object_the_reader_parses() -> None:
    text = READ_WEATHER.format(rules="THE RULES")
    assert text.rstrip().endswith("THE RULES")
    for key in ("site", "direction", "lower", "upper"):
        assert f'"{key}"' in text
    assert len(read.READ_WEATHER_HASH) == 64 and MARKETS[0]["rules_primary"] not in text


def test_progress_is_logged_and_the_factory_builds_a_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    markets = window_and_older(write_reading_kalshi(tmp_path))
    lines: list[str] = []
    monkeypatch.setattr(read, "PROGRESS_EVERY", 5)
    read.run_read(client_for([], tmp_path), markets, log=lines.append)
    assert lines == ["5 of 12 markets read", "10 of 12 markets read"]
    built = cli.make_client(SETTINGS, tmp_path / "cache")
    assert isinstance(built, ModelClient) and built.cache == tmp_path / "cache"
    built.close()


def test_running_the_package_runs_the_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["model", "--help"])
    with pytest.raises(SystemExit) as stop:
        runpy.run_module("sparq_analytics.model", run_name="__main__")
    assert stop.value.code == 0


def test_an_answer_for_a_market_naming_no_site_is_classified_by_its_series_code() -> None:
    def say(site: Any) -> dict[str, Any]:
        return {"site": site, "direction": "greater", "lower": 1, "upper": None}

    cases = [
        (None, "CLINYC", "unparsed"),
        (say(None), "CLINYC", "declined"),
        (say(" Null "), "CLINYC", "declined"),
        (say(""), None, "declined"),
        (say("Austin Bergstrom"), "CLINYC", "other"),
        (say("KNYC"), None, "unmapped"),
        (say("knyc"), "CLINYC", "right"),
        (say("CLIMIA"), "CLINYC", "wrong"),
    ]
    for answer, series_site, expected in cases:
        assert read.classify_unnamed(answer, series_site) == expected
    assert set(read.UNNAMED) == {c[2] for c in cases}


def test_a_series_has_one_code_only_when_its_named_markets_agree() -> None:
    markets = pl.DataFrame(
        {
            "series": ["A", "A", "A", "B", "B", "C"],
            "site": ["CLINYC", "CLINYC", None, "CLIMIA", "CLISFO", None],
        }
    )
    assert read.series_sites(markets).to_dicts() == [{"series": "A", "series_site": "CLINYC"}]


def test_a_reply_with_other_objects_inside_it_is_still_read() -> None:
    obj = '{"site": "CLINYC", "direction": "greater", "lower": 5, "upper": null}'
    nested = (
        '{"site": "CLINYC", "direction": "less", "lower": null, "upper": 5, "note": {"x": "}"}}'
    )
    found = read.parse_reply("Answer: " + nested)  # braces inside the object
    assert found is not None and found["upper"] == 5
    assert read.parse_reply('{"x": 1} then ' + obj) == json.loads(obj)  # an earlier, other object


def test_the_direction_is_compared_without_case_or_spaces() -> None:
    reply = {"site": None, "direction": " Greater ", "lower": 86, "upper": None}
    assert read.bounds_agree("greater", 86.0, None, reply)


class StubClient:
    """A client that answers every prompt the same way, for one market's row."""

    def __init__(self, content: str) -> None:
        self.content = content

    def chat(self, prompt: str) -> Reply:
        return Reply(self.content, "", 5, 1.0, False)


def row_for(strike_type: str, site: str | None) -> dict[str, Any]:
    return {
        "ticker": "Z1", "event_ticker": "E", "group": "highs", "sample": "window",
        "site": site, "series_site": site, "strike_type": strike_type,
        "floor": 5.0, "cap": None, "rules": "text",
    }  # fmt: skip


def test_a_strike_type_the_reply_cannot_state_is_no_ground_truth() -> None:
    reply = '{"site": "CLINYC", "direction": "greater", "lower": 5, "upper": null}'
    result = read.read_one(StubClient(reply), row_for("greater_or_equal", "CLINYC"))  # type: ignore[arg-type]
    assert result["bounds_agree"] is None and result["site_agree"] is True
    frame = read.frame_of([result])
    assert read.figures(frame)["no_strike_type"] == 1
    assert read.rate(frame, "bounds_agree")["n"] == 0


def test_the_speed_counts_only_the_calls_made() -> None:
    reply = '{"site": null, "direction": "greater", "lower": 5, "upper": null}'
    fresh = read.read_one(StubClient(reply), row_for("greater", None))  # type: ignore[arg-type]
    assert read.summarize([fresh], 30.0)["markets_a_minute"] == 2.0
    cached = fresh | {"cached": True}
    assert read.summarize([cached], 0.1)["markets_a_minute"] is None  # nothing was asked


def test_ping_closes_its_client_when_the_node_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = tmp_path / "model.toml"
    settings.write_text('url = "http://x/v1"\nkey = "sk-k"\nmodel = "m"\n', encoding="utf-8")
    if os.name == "posix":
        settings.chmod(0o600)
    built: list[ModelClient] = []

    def down(s: ModelSettings, cache: Path | None) -> ModelClient:
        refuse = httpx.MockTransport(lambda request: httpx.Response(500, text="no"))
        built.append(ModelClient(s, transport=refuse))
        return built[-1]

    monkeypatch.setattr(cli, "make_client", down)
    assert cli.main(["ping", "--settings", str(settings)]) == 1
    assert "answered 500" in capsys.readouterr().err
    assert built[0]._http.is_closed
