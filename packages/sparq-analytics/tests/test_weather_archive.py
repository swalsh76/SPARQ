from datetime import date
from pathlib import Path

import httpx
import polars as pl
import pytest
from sparq_analytics.weather import archive
from sparq_analytics.weather.archive import download, fetch, load_reports, parse_csv
from sparq_analytics.weather.bounds import REPORT_SCHEMA
from sparq_analytics.weather.stations import WeatherDataError
from sparq_core.config import WEATHER_USER_AGENT

CSV = (
    "station,valid,metar\n"
    "NYC,2026-10-20 00:51,KNYC 200051Z AUTO 05003KT 10SM CLR 12/07 A3028 RMK AO2 T01220067 $\n"
    "\n"
    "NYC,2026-10-20 01:51,KNYC 200151Z AUTO 05003KT 10SM CLR 12/08 A3027 RMK AO2 T01170078 $\n"
)
EMPTY = "station,valid,metar\n"


def client_for(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def test_the_csv_becomes_utc_milliseconds_under_the_asked_station() -> None:
    frame = parse_csv(CSV, "KNYC", "routine")
    assert frame["valid_ms"].to_list() == [1792457460000, 1792461060000]
    assert set(frame["station"]) == {"KNYC"} and set(frame["kind"]) == {"routine"}
    assert parse_csv(EMPTY, "KNYC", "special").is_empty()


def test_an_answer_that_is_not_the_csv_is_refused() -> None:
    with pytest.raises(WeatherDataError, match="not its CSV"):
        parse_csv("Too many requests, slow down", "KNYC", "routine")
    with pytest.raises(WeatherDataError):
        parse_csv("", "KNYC", "routine")


def test_fetch_waits_and_retries_on_slow_down_and_server_errors() -> None:
    answers = iter([429, 503, 200])
    waits: list[float] = []
    client = client_for(httpx.MockTransport(lambda r: httpx.Response(next(answers), text=CSV)))
    assert fetch(client, {}, sleep=waits.append) == CSV
    assert waits == [2.0, 4.0]


def test_fetch_retries_a_dropped_connection_and_gives_up_in_the_end() -> None:
    calls = 0

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("reset")
        return httpx.Response(200, text=CSV)

    assert fetch(client_for(httpx.MockTransport(flaky)), {}, sleep=lambda s: None) == CSV

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        fetch(client_for(httpx.MockTransport(broken)), {}, sleep=lambda s: None)
    always = httpx.MockTransport(lambda r: httpx.Response(503))
    with pytest.raises(WeatherDataError, match="6 tries"):
        fetch(client_for(always), {}, sleep=lambda s: None)
    missing = httpx.MockTransport(lambda r: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        fetch(client_for(missing), {}, sleep=lambda s: None)


def test_download_asks_for_routine_and_special_once_per_station_and_resumes(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=CSV if request.url.params["report_type"] == "3" else EMPTY)

    waits: list[float] = []
    client = client_for(httpx.MockTransport(answer))
    start, end = date(2026, 8, 1), date(2026, 10, 24)
    written = download(
        tmp_path,
        ["KNYC", "KMDW", "KNYC"],
        start,
        end,
        client=client,
        sleep=waits.append,
        log=lambda s: None,
    )
    assert written == {"KMDW": 2, "KNYC": 2}
    assert [(r.url.params["station"], r.url.params["report_type"]) for r in seen] == [
        ("KMDW", "3"),
        ("KMDW", "4"),
        ("KNYC", "3"),
        ("KNYC", "4"),
    ]
    params = seen[0].url.params
    assert (params["sts"], params["ets"], params["data"], params["tz"]) == (
        "2026-08-01T00:00Z",
        "2026-10-24T00:00Z",
        "metar",
        "Etc/UTC",
    )
    assert waits == [archive.PACE_SECONDS] * 4
    assert (tmp_path / "archive" / "KNYC-20260801-20261024.parquet").exists()
    assert not list((tmp_path / "archive").glob("*.partial"))
    seen.clear()
    assert download(tmp_path, ["KNYC"], start, end, client=client, sleep=waits.append) == {}
    assert seen == []


def test_download_opens_its_own_client_with_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        agents.append(request.headers["user-agent"])
        return httpx.Response(200, text=EMPTY)

    real = httpx.Client
    monkeypatch.setattr(
        "sparq_analytics.weather.archive.httpx.Client",
        lambda **kw: real(transport=httpx.MockTransport(answer), **kw),
    )
    download(
        tmp_path,
        ["KNYC"],
        date(2026, 8, 1),
        date(2026, 8, 2),
        sleep=lambda s: None,
        log=lambda s: None,
    )
    assert agents == [WEATHER_USER_AGENT] * 2


def test_load_reports_reads_every_file_once(tmp_path: Path) -> None:
    with pytest.raises(WeatherDataError, match="run download"):
        load_reports(tmp_path)
    folder = tmp_path / "archive"
    folder.mkdir()
    frame = parse_csv(CSV, "KNYC", "routine")
    frame.write_parquet(folder / "KNYC-a.parquet")
    frame.write_parquet(folder / "KNYC-b.parquet")  # an overlapping second download
    loaded = load_reports(tmp_path)
    assert loaded.height == 2
    assert loaded.equals(frame.sort("station", "valid_ms", "kind"))
    assert loaded.schema == pl.Schema(REPORT_SCHEMA)
