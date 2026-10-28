"""Read the ingest's live tables into Parquet, one UTC hour per file, read-only (S1.2a, D11).

The connection settings are a TOML file on the analytics node (``SPARQ_LIVE_CONFIG``, default
``~/.config/sparq/live.toml``)::

    host = "server-host"
    port = 5432
    database = "kaps"
    user = "sparq_reader"
    ca_file = "~/.config/sparq/server.crt"   # the server's certificate, pinned

It never holds a password: the PostgreSQL client reads that from ``~/.pgpass``. Every query is
bounded by time, because the hypertables are indexed by market and time, not by time alone. An hour
is copied only once it is ``SETTLE`` old, written under a temporary name and renamed, so a run can
stop anywhere and the next resumes at the first missing hour. Settlement results are never copied:
until S1.1b's verdict no live-data work may compute an edge to settlement (D11).
"""

import os
import ssl
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

SETTLE = timedelta(minutes=10)
HOUR = timedelta(hours=1)
DEFAULT_CONFIG = "~/.config/sparq/live.toml"


class LiveSourceError(Exception):
    """The connection settings are missing or wrong, or the copied hours are incomplete."""


# times leave the database as whole milliseconds since the epoch, so nothing depends on how a
# time zone is printed
TICKER_SQL = (
    "SELECT floor(extract(epoch FROM ts) * 1000)::bigint AS ts_ms, "
    "floor(extract(epoch FROM received_at) * 1000)::bigint AS recv_ms, ticker, price_e6, "
    "yes_bid_e6, yes_ask_e6, yes_bid_size_e2, yes_ask_size_e2, volume_e2 "
    "FROM ticker WHERE ts >= $1 AND ts < $2"
)
TRADE_SQL = (
    "SELECT floor(extract(epoch FROM created_time) * 1000)::bigint AS ts_ms, ticker, trade_id, "
    "yes_price_e6, count_e2, taker_side, is_block_trade::int AS is_block_trade "
    "FROM trade WHERE created_time >= $1 AND created_time < $2"
)
# the strike fields say what a market is about (S3.2's relation groups); the rules text is
# copied for weather markets only, flattened to one line, because the station a weather
# market reports is named nowhere else
MARKET_SQL = (
    "SELECT m.ticker, m.series_ticker, m.event_ticker, s.category, "
    "floor(extract(epoch FROM m.open_time) * 1000)::bigint AS open_ms, "
    "floor(extract(epoch FROM m.close_time) * 1000)::bigint AS close_ms, "
    "m.strike_type, m.floor_strike, m.cap_strike, "
    "CASE WHEN s.category = 'Climate and Weather' "
    "THEN regexp_replace(m.rules_primary, '\\s+', ' ', 'g') END AS weather_rules "
    "FROM market m LEFT JOIN series s ON s.ticker = m.series_ticker"
)
# the outages in which ticker frames were lost (trades are backfilled, frames cannot be); an open
# one has no end yet
GAP_SQL = (
    "SELECT kind, floor(extract(epoch FROM started_at) * 1000)::bigint AS start_ms, "
    "floor(extract(epoch FROM ended_at) * 1000)::bigint AS end_ms, reason "
    "FROM gap_log WHERE kind IN ('ticker', 'all')"
)

TICKER_SCHEMA = {
    "ts_ms": pl.Int64,
    "recv_ms": pl.Int64,
    "ticker": pl.String,
    "price_e6": pl.Int64,
    "yes_bid_e6": pl.Int64,
    "yes_ask_e6": pl.Int64,
    "yes_bid_size_e2": pl.Int64,
    "yes_ask_size_e2": pl.Int64,
    "volume_e2": pl.Int64,
}
TRADE_SCHEMA = {
    "ts_ms": pl.Int64,
    "ticker": pl.String,
    "trade_id": pl.String,
    "yes_price_e6": pl.Int64,
    "count_e2": pl.Int64,
    "taker_side": pl.String,
    "is_block_trade": pl.Int8,
}
MARKET_SCHEMA = {
    "ticker": pl.String,
    "series_ticker": pl.String,
    "event_ticker": pl.String,
    "category": pl.String,
    "open_ms": pl.Int64,
    "close_ms": pl.Int64,
    "strike_type": pl.String,
    "floor_strike": pl.String,  # a level, not a price: text of any precision (sparq_core.models)
    "cap_strike": pl.String,
    "weather_rules": pl.String,  # null outside Climate and Weather
}
GAP_SCHEMA = {"kind": pl.String, "start_ms": pl.Int64, "end_ms": pl.Int64, "reason": pl.String}
TABLES = (("ticker", TICKER_SQL, TICKER_SCHEMA), ("trade", TRADE_SQL, TRADE_SCHEMA))


@dataclass(frozen=True, slots=True)
class LiveSource:
    host: str
    port: int
    database: str
    user: str
    ca_file: Path


def config_path() -> Path:
    return Path(os.environ.get("SPARQ_LIVE_CONFIG", DEFAULT_CONFIG)).expanduser()


def load_source(path: Path) -> LiveSource:
    if not path.exists():
        raise LiveSourceError(f"no connection settings at {path}")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if "password" in data:
        raise LiveSourceError(f"{path} must not hold a password; the client reads ~/.pgpass")
    missing = [key for key in ("host", "database", "user", "ca_file") if key not in data]
    if missing:
        raise LiveSourceError(f"{path} lacks {', '.join(missing)}")
    return LiveSource(
        host=str(data["host"]),
        port=int(data.get("port", 5432)),
        database=str(data["database"]),
        user=str(data["user"]),
        ca_file=Path(str(data["ca_file"])).expanduser(),
    )


async def connect(source: LiveSource) -> Any:
    """A TLS connection that checks the server against the pinned certificate and its name."""
    import asyncpg

    context = ssl.create_default_context(cafile=str(source.ca_file))
    return await asyncpg.connect(
        host=source.host,
        port=source.port,
        user=source.user,
        database=source.database,
        ssl=context,
        timeout=15,
    )


def whole_hours(start: datetime, end: datetime, now: datetime) -> list[datetime]:
    """The UTC hours from ``start`` (rounded down to the hour) that end by ``end`` and are at
    least ``SETTLE`` old at ``now``."""
    if start.tzinfo is None or end.tzinfo is None or now.tzinfo is None:
        raise LiveSourceError("times must carry a time zone")
    hour = start.replace(minute=0, second=0, microsecond=0)
    limit = min(end, now - SETTLE)
    hours = []
    while hour + HOUR <= limit:
        hours.append(hour)
        hour += HOUR
    return hours


def hour_name(hour: datetime) -> str:
    return hour.strftime("%Y-%m-%dT%H")


async def _copy(
    conn: Any, query: str, args: tuple[Any, ...], schema: dict[str, Any], target: Path
) -> int:
    staging = target.with_name(target.name + ".csv.part")
    finished = target.with_name(target.name + ".part")
    await conn.copy_from_query(query, *args, output=str(staging), format="csv", header=True)
    frame = pl.read_csv(staging, schema=schema)
    if "is_block_trade" in frame.columns:
        frame = frame.with_columns(pl.col("is_block_trade").cast(pl.Boolean))
    frame.write_parquet(finished)
    finished.replace(target)
    staging.unlink()
    return frame.height


@dataclass(frozen=True, slots=True)
class ExtractReport:
    hours_copied: int
    hours_present: int
    rows: dict[str, int]
    markets: int
    gaps: int


async def extract(
    conn: Any,
    folder: Path,
    start: datetime,
    end: datetime,
    *,
    now: datetime,
    log: Callable[[str], None] = lambda _: None,
) -> ExtractReport:
    """Copy each missing whole hour of ticker frames and trades, then the market list and the
    ticker gaps afresh."""
    copied = present = 0
    rows = {name: 0 for name, _, _ in TABLES}
    for hour in whole_hours(start, end, now):
        for name, query, schema in TABLES:
            target = folder / name / f"{hour_name(hour)}.parquet"
            if target.exists():
                present += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            count = await _copy(conn, query, (hour, hour + HOUR), schema, target)
            rows[name] += count
            copied += 1
            log(f"{name} {hour_name(hour)}: {count:,} rows")
    markets = await _copy(conn, MARKET_SQL, (), MARKET_SCHEMA, folder / "markets.parquet")
    log(f"markets: {markets:,} rows")
    gaps = await _copy(conn, GAP_SQL, (), GAP_SCHEMA, folder / "gaps.parquet")
    log(f"ticker gaps: {gaps:,}")
    return ExtractReport(copied, present, rows, markets, gaps)


def hour_files(folder: Path, name: str, start: datetime, end: datetime) -> list[Path]:
    """The files of ``name`` for every hour in ``[start, end)``; refuses a gap, because a missing
    hour would read as an hour with no book and no trades."""
    files: list[Path] = []
    missing: list[Path] = []
    hour = start.replace(minute=0, second=0, microsecond=0)
    while hour < end:
        path = folder / name / f"{hour_name(hour)}.parquet"
        (files if path.exists() else missing).append(path)
        hour += HOUR
    if missing:
        raise LiveSourceError(
            f"{len(missing)} {name} hour(s) not copied, first {missing[0].stem}; run extract"
        )
    return files


def last_hour_end(folder: Path) -> datetime | None:
    """The end of the newest hour copied for both tables, or None when nothing is copied."""
    ends = []
    for name, _, _ in TABLES:
        names = sorted(p.stem for p in (folder / name).glob("*.parquet"))
        if not names:
            return None
        ends.append(datetime.strptime(names[-1] + "+0000", "%Y-%m-%dT%H%z") + HOUR)
    return min(ends)
