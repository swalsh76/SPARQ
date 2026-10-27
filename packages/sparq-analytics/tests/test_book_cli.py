"""S1.2a's command line: extract, measure and report, and the refusals."""

import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from bookhelpers import RULE_JSON, FakeCopier, trades, write_hours, write_inputs
from sparq_analytics.live import cli
from sparq_analytics.live.cli import main
from sparq_analytics.study.forward import busy_digest

DAY = ["--start", "2026-10-25", "--end", "2026-10-26"]


def measure(data: Path, *extra: str) -> int:
    rule, busy = write_inputs(data.parent)
    return main(["measure", "--data", str(data), "--rule", str(rule), "--busy", str(busy), *DAY,
                 "--resamples", "100", *extra])  # fmt: skip


def test_a_day_measured_in_groups_of_markets_is_the_same_as_in_one(tmp_path: Path) -> None:
    from datetime import date

    from sparq_analytics.live.book import BookConfig
    from sparq_analytics.study.forward import read_busy
    from sparq_analytics.study.rules import rules_from_json

    data = tmp_path / "live"
    write_hours(data)
    rule_file, busy_file = write_inputs(tmp_path)
    rule = rules_from_json(json.loads(rule_file.read_text(encoding="utf-8")))[0]
    busy = read_busy(busy_file)

    def run(groups: int) -> tuple[pl.DataFrame, pl.DataFrame]:
        out = cli.measure_day(data, date(2026, 10, 25), rule, busy, BookConfig(), groups=groups)
        return out[0], out[1]

    whole_trades, whole_orders = run(1)
    assert len(cli.market_groups(whole_trades, 3)) == 3
    for groups in (2, 3, 50):
        trades_, orders = run(groups)
        assert trades_.sort(trades_.columns).equals(whole_trades.sort(whole_trades.columns))
        assert orders.equals(whole_orders)
    assert cli.market_groups(whole_trades.clear(), 4) == [[]]


def test_measure_writes_the_results_and_the_summary(tmp_path: Path) -> None:
    data = tmp_path / "live"
    write_hours(data)
    assert measure(data) == 0
    results = data / "results"
    kept = pl.read_parquet(results / "trades-2026-10-25.parquet")
    # g4, ten minutes before the feed's outage, is left out; g5 after it is kept
    assert kept.height == trades().height - 1
    assert kept.filter(pl.col("ticker") == "M7").height == 5
    assert (results / "bursts-2026-10-25.parquet").exists()
    summary = json.loads((results / "book.json").read_text(encoding="utf-8"))
    assert summary["days"] == ["2026-10-25"] and summary["near_gaps"] == 1
    assert summary["data_end"].startswith("2026-10-26 00:10")
    assert summary["busy_hash"] == busy_digest(tmp_path / "busy.json")
    assert summary["stop"]["outcome"] == "too few"
    assert summary["populations"]["forward rule"]["trades"] == 7


def test_report_prints_or_writes_the_tables(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "live"
    assert main(["report", "--data", str(data)]) == 1
    assert "run measure first" in capsys.readouterr().err
    write_hours(data)
    assert measure(data) == 0
    capsys.readouterr()
    assert main(["report", "--data", str(data)]) == 0
    assert "**Stop rule: too few**" in capsys.readouterr().out
    out = tmp_path / "book.md"
    assert main(["report", "--data", str(data), "--out", str(out)]) == 0
    assert "| forward rule |" in out.read_text(encoding="utf-8")


def test_measure_refuses_a_day_not_fully_copied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "live"
    write_hours(data, hours=20)
    assert measure(data) == 1
    assert "not fully copied" in capsys.readouterr().err


def test_measure_needs_the_list_of_outages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "live"
    write_hours(data)
    (data / "gaps.parquet").unlink()
    assert measure(data) == 1
    assert "feed outages" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--start", "2026-10-25", "--end", "2026-10-25"], "after --start"),
        (["--start", "2026-10-25T05", "--end", "2026-10-26"], "a day, not a time"),
        (["--end", "2026-10-26"], "--start is required"),
    ],
)
def test_measure_checks_its_days(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], args: list[str], message: str
) -> None:
    rule, busy = write_inputs(tmp_path)
    code = main(["measure", "--data", str(tmp_path), "--rule", str(rule), "--busy", str(busy),
                 *args])  # fmt: skip
    assert code == 1 and message in capsys.readouterr().err


def test_measure_needs_one_rule_and_the_activity_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rule, busy = write_inputs(tmp_path)
    assert main(["measure", "--data", str(tmp_path), "--rule", str(rule), *DAY]) == 1
    assert "--busy is required" in capsys.readouterr().err
    assert main(["measure", "--data", str(tmp_path), "--busy", str(busy), *DAY]) == 1
    assert "--rule is required" in capsys.readouterr().err
    two = tmp_path / "two.json"
    two.write_text(json.dumps({"rules": RULE_JSON["rules"] * 2}), encoding="utf-8")
    assert main(["measure", "--data", str(tmp_path), "--rule", str(two), "--busy", str(busy),
                 *DAY]) == 1  # fmt: skip
    assert "exactly one" in capsys.readouterr().err


def test_extract_connects_copies_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = tmp_path / "live.toml"
    settings.write_text(
        'host = "h"\ndatabase = "d"\nuser = "u"\nca_file = "c.crt"\n', encoding="utf-8"
    )
    monkeypatch.setenv("SPARQ_LIVE_CONFIG", str(settings))
    conn = FakeCopier()
    seen: list[Any] = []

    async def fake_connect(source: Any) -> FakeCopier:
        seen.append(source)
        return conn

    monkeypatch.setattr(cli, "connect", fake_connect)
    data = tmp_path / "live"
    code = main(["extract", "--data", str(data), "--start", "2026-10-25T00",
                 "--end", "2026-10-25T02"])  # fmt: skip
    assert code == 0 and conn.closed and seen[0].host == "h"
    assert (data / "ticker" / "2026-10-25T01.parquet").exists()
    assert "copied 4 hour files" in capsys.readouterr().out
    assert main(["extract", "--data", str(data)]) == 1
    assert "--start is required" in capsys.readouterr().err
