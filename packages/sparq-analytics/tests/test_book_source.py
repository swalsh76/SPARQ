"""Copying the ingest's tables (S1.2a): settings, whole hours, resuming, the files' contents."""

import asyncio
import ssl
import sys
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
    assert report.settlements == 1
    assert pl.read_parquet(tmp_path / "settlements.parquet").row(0)[:2] == ("M4", "yes")
    assert pl.read_parquet(tmp_path / "gaps.parquet").row(0)[0] == "ticker"
    copied = pl.read_parquet(sorted((tmp_path / "trade").glob("*.parquet")))
    assert copied.schema["is_block_trade"] == pl.Boolean
    assert copied.sort("ticker", "ts_ms").equals(trades().sort("ticker", "ts_ms"))
    assert not list(tmp_path.rglob("*.part"))  # no staging file is left behind
    assert any(line.startswith("ticker 2026-10-25T00") for line in lines)
    again = asyncio.run(extract(conn, tmp_path, T0, end, now=now))
    assert again.hours_copied == 0 and again.hours_present == 4
    # the market list is copied afresh each run; hours only once
    assert sum("AS weather_rules" in q for q, _ in conn.calls) == 2
    assert sum("settlement_ts" in q for q, _ in conn.calls) == 2
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


def test_a_trades_hour_is_copied_again_until_its_backfill_is_done(tmp_path: Path) -> None:
    conn = FakeCopier()
    now, end = T0 + timedelta(hours=5), T0 + timedelta(hours=2)
    start_ms = int(T0.timestamp() * 1000)
    conn.trade_gaps = pl.DataFrame(
        {"kind": ["trades"], "status": ["backfilling"], "start_ms": [start_ms + 600_000],
         "end_ms": [start_ms + 900_000]},
    )  # fmt: skip
    report = asyncio.run(extract(conn, tmp_path, T0, end, now=now))
    marks = sorted(p.name for p in (tmp_path / "trade").glob("*.provisional"))
    assert marks == ["2026-10-25T00.provisional"] and report.provisional == 1
    again = asyncio.run(extract(conn, tmp_path, T0, end, now=now))
    assert again.recopied == 1 and again.hours_copied == 1 and again.provisional == 1
    conn.trade_gaps = conn.trade_gaps.with_columns(status=pl.lit("done"))
    done = asyncio.run(extract(conn, tmp_path, T0, end, now=now))
    assert done.recopied == 1 and done.provisional == 0
    assert not list((tmp_path / "trade").glob("*.provisional"))
    settled = asyncio.run(extract(conn, tmp_path, T0, end, now=now))
    assert settled.hours_copied == 0  # finished: copied for the last time
    # hours copied before the rule: --recopy-gaps copies again every one a trades gap overlaps
    forced = asyncio.run(extract(conn, tmp_path, T0, end, now=now, recopy_gaps=True))
    assert forced.recopied == 1 and forced.provisional == 0
    assert pl.read_parquet(tmp_path / "trade-gaps.parquet").height == 1


@pytest.mark.skipif(sys.platform == "win32", reason="the lock is POSIX's")
def test_one_extract_at_a_time(tmp_path: Path) -> None:
    from sparq_analytics.live.source import locked

    async def two() -> None:
        async with locked(tmp_path):
            with pytest.raises(LiveSourceError, match="held"):
                async with locked(tmp_path, wait=timedelta(seconds=0)):
                    pass

    asyncio.run(two())


def test_a_provisional_hour_is_marked_before_its_copy(tmp_path: Path) -> None:
    conn = FakeCopier()
    start_ms = int(T0.timestamp() * 1000)
    conn.trade_gaps = pl.DataFrame(
        {"kind": ["all"], "status": ["open"], "start_ms": [start_ms], "end_ms": [None]},
        schema={"kind": pl.String, "status": pl.String, "start_ms": pl.Int64, "end_ms": pl.Int64},
    )
    seen: list[bool] = []
    original = conn.copy_from_query

    async def watching(query: str, *args: Any, **kw: Any) -> str:
        if "FROM trade " in query:
            seen.append((tmp_path / "trade" / "2026-10-25T00.provisional").exists())
        return await original(query, *args, **kw)

    conn.copy_from_query = watching  # type: ignore[method-assign]
    asyncio.run(extract(conn, tmp_path, T0, T0 + timedelta(hours=1), now=T0 + timedelta(hours=2)))
    assert seen == [True]
