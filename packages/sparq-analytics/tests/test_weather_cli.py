import json
import runpy
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from sparq_analytics.weather import archive, live
from sparq_analytics.weather.archive import parse_csv
from sparq_analytics.weather.cli import main
from test_weather_check import MARKETS, write_history

# Central Park on 15 September 2026 (EDT; the standard day starts 05:00 UTC): 23.5 C (74 F) and rain
DAY = (
    "station,valid,metar\n"
    "NYC,2026-09-15 06:51,KNYC 150651Z AUTO 10SM 20/10 RMK AO2 T02000100\n"
    "NYC,2026-09-15 18:51,KNYC 151851Z AUTO 10SM 24/10 RMK AO2 T02350100 P0004\n"
)


def write_archive(data: Path) -> None:
    folder = data / "archive"
    folder.mkdir(parents=True)
    parse_csv(DAY, "KNYC", "routine").write_parquet(folder / "KNYC-20260801-20261024.parquet")


def test_stations_lists_the_sites_and_flags_an_unmapped_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_history(tmp_path, MARKETS)
    assert main(["stations", "--kalshi", str(root)]) == 2
    out, err = capsys.readouterr()
    assert "CLINYC" in out and "KMKE" in out
    assert "1 markets name no mapped site" in err
    mapped = write_history(tmp_path / "ok", [m for m in MARKETS if "ZZ" not in str(m["ticker"])])
    assert main(["stations", "--kalshi", str(mapped)]) == 0


def test_check_writes_its_summary_and_fails_on_a_wrong_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "weather"
    write_archive(data)
    root = write_history(tmp_path, MARKETS)
    assert main(["check", "--data", str(data), "--kalshi", str(root)]) == 2
    saved = json.loads((data / "check.json").read_text(encoding="utf-8"))
    assert saved["first"] == "2026-08-07" and saved["last"] == "2026-09-28"
    assert {w["ticker"] for w in saved["wrong"]} == {"KXHIGHNY-26SEP15-T73"}
    assert "no mapped site" in capsys.readouterr().err
    clean = [
        m for m in MARKETS if m["ticker"] not in ("KXHIGHNY-26SEP15-T73", "KXHIGHZZ-26SEP15-T50")
    ]
    assert (
        main(["check", "--data", str(data), "--kalshi", str(write_history(tmp_path / "c", clean))])
        == 0
    )
    totals = {
        t["mode"]: t
        for t in json.loads((data / "check.json").read_text(encoding="utf-8"))["totals"]
    }
    assert (totals["standard"]["called"], totals["standard"]["wrong"]) == (3, 0)


def test_check_refuses_the_test_part(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "weather"
    write_archive(data)
    root = write_history(tmp_path, MARKETS)
    assert main(["check", "--data", str(data), "--kalshi", str(root), "--last", "2026-10-11"]) == 1
    assert "test part" in capsys.readouterr().err


def test_download_passes_the_mapped_stations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []

    def fake(folder: Path, stations: list[str], start: date, end: date) -> dict[str, int]:
        calls.append((folder, list(stations), start, end))
        return {"KNYC": 3}

    monkeypatch.setattr(archive, "download", fake)
    assert (
        main(["download", "--data", str(tmp_path), "--start", "2026-08-01", "--end", "2026-10-24"])
        == 0
    )
    folder, stations, start, end = calls[0]
    assert folder == tmp_path and "KNYC" in stations and len(stations) == 39
    assert (start, end) == (date(2026, 8, 1), date(2026, 10, 24))


@pytest.mark.parametrize(
    "argv",
    [
        ["download", "--data", "x", "--start", "2026-08-02", "--end", "2026-08-01"],
        ["download", "--data", "x"],
        ["check", "--kalshi", "x"],
        ["stations"],
    ],
)
def test_a_bad_command_line_exits_one(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(argv) == 1
    assert "error:" in capsys.readouterr().err


def test_delay_records_into_the_live_folder_and_the_report_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[Any, ...]] = []

    def fake(path: Path, stations: list[str], hours: float) -> int:
        calls.append((path, hours))
        return 0

    monkeypatch.setattr(live, "record", fake)
    assert main(["delay", "--data", str(tmp_path), "--hours", "2"]) == 0
    assert calls == [(tmp_path / "live" / "delay.csv", 2.0)]
    path = tmp_path / "live" / "delay.csv"
    path.parent.mkdir()
    pl.DataFrame(
        [
            {
                "station": "KNYC",
                "kind": "METAR",
                "obs_ms": 0,
                "report_ms": None,
                "receipt_ms": 60_000,
                "first_seen_ms": 120_000,
                "baseline": 0,
                "raw": "KNYC 010000Z 10SM 20/10",
            }
        ],
        schema=live.CSV_SCHEMA,
    ).write_csv(path)
    assert main(["delay-report", "--data", str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["first_seen_after_obs_s"]["median"] == 120.0 and "archive" not in report
    write_archive(tmp_path)
    assert main(["delay-report", "--data", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["archive"]["found"] == 0


def test_running_the_package_runs_the_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["weather", "stations"])
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("sparq_analytics.weather", run_name="__main__")
    assert caught.value.code == 1
