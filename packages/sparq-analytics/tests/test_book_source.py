"""Copying the ingest's tables (S1.2a): settings, whole hours, resuming, the files' contents."""

import asyncio
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from bookhelpers import FakeCopier, frames, markets, trades
from sparq_analytics.live.source import (
    LiveSource,
    LiveSourceError,
    config_path,
    connect,
    extract,
    hour_files,
    last_hour_end,
    load_source,
    whole_hours,
)

T0 = datetime(2026, 10, 25, tzinfo=UTC)


def test_settings_come_from_a_file_without_a_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "live.toml"
    monkeypatch.setenv("SPARQ_LIVE_CONFIG", str(path))
    assert config_path() == path
    with pytest.raises(LiveSourceError, match="no connection settings"):
        load_source(path)
    path.write_text('host = "h"\ndatabase = "d"\nuser = "u"\npassword = "x"\n', encoding="utf-8")
    with pytest.raises(LiveSourceError, match="password"):
        load_source(path)
    path.write_text('host = "h"\nuser = "u"\n', encoding="utf-8")
    with pytest.raises(LiveSourceError, match="database, ca_file"):
        load_source(path)
    path.write_text(
        'host = "h"\ndatabase = "d"\nuser = "u"\nca_file = "~/c.crt"\n', encoding="utf-8"
    )
    loaded = load_source(path)
    assert (loaded.host, loaded.port, loaded.database, loaded.user) == ("h", 5432, "d", "u")
    assert loaded.ca_file == Path("~/c.crt").expanduser()


def test_the_default_settings_file_is_in_the_home_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPARQ_LIVE_CONFIG", raising=False)
    assert config_path() == Path("~/.config/sparq/live.toml").expanduser()


def test_connect_checks_the_server_against_the_pinned_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncpg

    seen: dict[str, Any] = {}

    async def fake_connect(**kwargs: Any) -> str:
        seen.update(kwargs)
        return "connection"

    monkeypatch.setattr(ssl, "create_default_context", lambda cafile: ("context", cafile))
    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    live = LiveSource("server", 5433, "kaps", "reader", Path("/pinned.crt"))
    assert asyncio.run(connect(live)) == "connection"
    assert seen["ssl"] == ("context", str(Path("/pinned.crt")))
    assert (seen["host"], seen["port"], seen["database"], seen["user"]) == (
        "server",
        5433,
        "kaps",
        "reader",
    )
    assert "password" not in seen


def test_whole_hours_are_old_enough_and_rounded_down() -> None:
    start = T0 + timedelta(minutes=13)
    now = T0 + timedelta(hours=3, minutes=5)  # the third hour ended 5 minutes ago: too fresh
    assert whole_hours(start, now, now) == [T0, T0 + timedelta(hours=1)]
    assert whole_hours(start, T0 + timedelta(hours=1), now) == [T0]
    with pytest.raises(LiveSourceError, match="time zone"):
        whole_hours(datetime(2026, 10, 25), now, now)  # noqa: DTZ001


def test_extract_copies_each_hour_once_and_resumes(tmp_path: Path) -> None:
    conn = FakeCopier()
    now, end = T0 + timedelta(hours=5), T0 + timedelta(hours=2)
    lines: list[str] = []
    report = asyncio.run(extract(conn, tmp_path, T0, end, now=now, log=lines.append))
    assert report.hours_copied == 4 and report.hours_present == 0
    assert report.rows == {"ticker": frames().height, "trade": trades().height}
    assert report.markets == markets().height and report.gaps == 1
    assert pl.read_parquet(tmp_path / "gaps.parquet").row(0)[0] == "ticker"
    copied = pl.read_parquet(sorted((tmp_path / "trade").glob("*.parquet")))
    assert copied.schema["is_block_trade"] == pl.Boolean
    assert copied.sort("ticker", "ts_ms").equals(trades().sort("ticker", "ts_ms"))
    assert not list(tmp_path.rglob("*.part"))  # no staging file is left behind
    assert any(line.startswith("ticker 2026-10-25T00") for line in lines)
    again = asyncio.run(extract(conn, tmp_path, T0, end, now=now))
    assert again.hours_copied == 0 and again.hours_present == 4
    # the market list is copied afresh each run; hours only once
    assert sum("FROM market" in q for q, _ in conn.calls) == 2
    assert sum("FROM ticker" in q for q, _ in conn.calls) == 2


def test_hour_files_refuse_a_gap(tmp_path: Path) -> None:
    asyncio.run(extract(FakeCopier(), tmp_path, T0, T0 + timedelta(hours=3), now=T0 + timedelta(1)))
    assert len(hour_files(tmp_path, "ticker", T0, T0 + timedelta(hours=3))) == 3
    (tmp_path / "ticker" / "2026-10-25T01.parquet").unlink()
    with pytest.raises(LiveSourceError, match="1 ticker hour"):
        hour_files(tmp_path, "ticker", T0, T0 + timedelta(hours=3))


def test_last_hour_end_is_where_both_tables_reach(tmp_path: Path) -> None:
    assert last_hour_end(tmp_path) is None
    asyncio.run(extract(FakeCopier(), tmp_path, T0, T0 + timedelta(hours=3), now=T0 + timedelta(1)))
    assert last_hour_end(tmp_path) == T0 + timedelta(hours=3)
    (tmp_path / "trade" / "2026-10-25T02.parquet").unlink()
    assert last_hour_end(tmp_path) == T0 + timedelta(hours=2)
