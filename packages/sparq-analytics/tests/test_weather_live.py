import json
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest
from sparq_analytics.weather import live
from sparq_analytics.weather.archive import parse_csv
from sparq_analytics.weather.live import poll, record, summarize
from sparq_core.config import WEATHER_USER_AGENT

OBS = 1793065860  # 2026-10-27 01:51 UTC
ARCHIVED_0151 = "KNYC 270151Z AUTO VRB04KT 10SM CLR 18/07 A3018 RMK AO2 SLP211 T01780067 $"
ARCHIVED_0251 = "KNYC 270251Z AUTO VRB04KT 10SM CLR 18/07 A3018 RMK AO2 SLP211 T01790067 $"
RAW = "METAR KNYC 270151Z AUTO VRB04KT 10SM CLR 18/07 A3018 RMK AO2 SLP211 T01780067 $"


def item(
    station: str, obs: int, raw: str, receipt: str | None = "2026-10-27T01:54:12.993Z"
) -> dict[str, Any]:
    return {
        "icaoId": station,
        "metarType": "METAR",
        "obsTime": obs,
        "reportTime": "2026-10-27T02:00:00.000Z",
        "receiptTime": receipt,
        "rawOb": raw,
    }


def test_poll_asks_for_every_station_at_once_and_reads_the_times() -> None:
    asked: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, json=[item("KNYC", OBS, RAW), item("KMDW", OBS, RAW, None)])

    reports = poll(httpx.Client(transport=httpx.MockTransport(answer)), ["KNYC", "KMDW"])
    assert asked[0].url.params["ids"] == "KMDW,KNYC"
    new_york = {
        "station": "KNYC",
        "kind": "METAR",
        "obs_ms": OBS * 1000,
        "report_ms": 1793066400000,
        "receipt_ms": 1793066052993,
        "raw": RAW,
    }
    # a report without a receipt time keeps it unknown
    assert reports == [new_york, new_york | {"station": "KMDW", "receipt_ms": None}]


class Clock:
    def __init__(self) -> None:
        self.now = float(OBS + 120)

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_record_marks_the_first_answer_as_baseline_and_keeps_new_reports(tmp_path: Path) -> None:
    clock = Clock()
    answers = iter(
        [
            httpx.Response(200, json=[item("KNYC", OBS, RAW)]),
            httpx.Response(503),
            httpx.Response(
                200,
                json=[
                    item("KNYC", OBS, RAW),
                    item("KNYC", OBS + 3600, RAW.replace("0151Z", "0251Z")),
                ],
            ),
        ]
    )
    client = httpx.Client(transport=httpx.MockTransport(lambda r: next(answers)))
    lines: list[str] = []
    path = tmp_path / "live" / "delay.csv"
    written = record(
        path, ["KNYC"], 3 / 60, client=client, clock=clock, sleep=clock.sleep, log=lines.append
    )
    assert written == 1
    rows = pl.read_csv(path).to_dicts()
    assert [(r["obs_ms"], r["baseline"]) for r in rows] == [
        (OBS * 1000, 1),
        ((OBS + 3600) * 1000, 0),
    ]
    assert rows[1]["first_seen_ms"] == (OBS + 120 + 120) * 1000  # the third poll, two minutes on
    assert any("poll failed" in line for line in lines)
    # a second run appends without a second header
    again = iter([httpx.Response(200, json=[])])
    client = httpx.Client(transport=httpx.MockTransport(lambda r: next(again)))
    record(path, ["KNYC"], 1 / 60, client=client, clock=clock, sleep=clock.sleep, log=lines.append)
    assert pl.read_csv(path).height == 2
    # a restart does not write the reports it already holds again
    repeat = iter([httpx.Response(200, json=[item("KNYC", OBS, RAW)])])
    client = httpx.Client(transport=httpx.MockTransport(lambda r: next(repeat)))
    record(path, ["KNYC"], 1 / 60, client=client, clock=clock, sleep=clock.sleep, log=lines.append)
    assert pl.read_csv(path).height == 2


def test_record_opens_its_own_client_with_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        agents.append(request.headers["user-agent"])
        return httpx.Response(200, json=[])

    real = httpx.Client
    monkeypatch.setattr(
        "sparq_analytics.weather.live.httpx.Client",
        lambda **kw: real(transport=httpx.MockTransport(answer), **kw),
    )
    clock = Clock()
    record(tmp_path / "d.csv", ["KNYC"], 1 / 60, clock=clock, sleep=clock.sleep, log=lambda s: None)
    assert agents == [WEATHER_USER_AGENT]


def write_live(path: Path, rows: list[dict[str, Any]]) -> None:
    pl.DataFrame(
        rows,
        schema={
            f: (pl.String if f in ("station", "kind", "raw") else pl.Int64) for f in live.FIELDS
        },
    ).write_csv(path)


def test_summarize_gives_the_delays_and_the_archive_match(tmp_path: Path) -> None:
    path = tmp_path / "delay.csv"
    base = {"station": "KNYC", "kind": "METAR", "report_ms": None}
    write_live(
        path,
        [
            base
            | {
                "obs_ms": OBS * 1000,
                "receipt_ms": None,
                "first_seen_ms": OBS * 1000 + 999_000,
                "baseline": 1,
                "raw": RAW,
            },
            base
            | {
                "obs_ms": (OBS + 3600) * 1000,
                "receipt_ms": (OBS + 3780) * 1000,
                "first_seen_ms": (OBS + 3840) * 1000,
                "baseline": 0,
                "raw": RAW.replace("0151Z", "0251Z"),
            },
            base
            | {
                "obs_ms": (OBS + 7200) * 1000,
                "receipt_ms": (OBS + 7380) * 1000,
                "first_seen_ms": (OBS + 7500) * 1000,
                "baseline": 0,
                "raw": RAW.replace("0151Z", "0351Z"),
            },
        ],
    )
    archived = parse_csv(
        "station,valid,metar\n"
        f"NYC,2026-10-27 01:51,{ARCHIVED_0151}\n"
        f"NYC,2026-10-27 02:51,{ARCHIVED_0251}\n",
        "KNYC",
        "routine",
    )
    out = summarize(path, archived)
    assert (out["reports"], out["timed"], out["stations"]) == (3, 2, 1)
    assert out["first_seen_after_obs_s"] == {"median": 270.0, "p95": 300.0, "max": 300.0}
    assert out["receipt_after_obs_s"] == {"median": 180.0, "p95": 180.0, "max": 180.0}
    # the 01:51 report is identical; the 02:51 one differs in its tenths; 03:51 is not archived
    assert out["archive"] == {"found": 2, "same_text": 1, "same_reading": 1}
    json.dumps(out)


def test_summarize_matches_each_live_report_once_when_the_archive_has_two_in_a_minute(
    tmp_path: Path,
) -> None:
    path = tmp_path / "delay.csv"
    write_live(
        path,
        [
            {
                "station": "KNYC",
                "kind": "METAR",
                "obs_ms": OBS * 1000,
                "report_ms": None,
                "receipt_ms": None,
                "first_seen_ms": OBS * 1000 + 200_000,
                "baseline": 0,
                "raw": RAW,
            }
        ],
    )
    archived = pl.concat(
        [
            parse_csv(f"station,valid,metar\nNYC,2026-10-27 01:51,{ARCHIVED_0151}\n", "KNYC", "r"),
            parse_csv(f"station,valid,metar\nNYC,2026-10-27 01:51,{ARCHIVED_0251}\n", "KNYC", "s"),
        ]
    )
    assert summarize(path, archived)["archive"] == {"found": 1, "same_text": 1, "same_reading": 1}


def test_summarize_with_nothing_timed_and_no_archive(tmp_path: Path) -> None:
    path = tmp_path / "delay.csv"
    write_live(
        path,
        [
            {
                "station": "KNYC",
                "kind": "METAR",
                "obs_ms": OBS * 1000,
                "report_ms": None,
                "receipt_ms": None,
                "first_seen_ms": OBS * 1000,
                "baseline": 1,
                "raw": RAW,
            }
        ],
    )
    out = summarize(path)
    assert out["first_seen_after_obs_s"] == {"median": None, "p95": None, "max": None}
    assert "archive" not in out
