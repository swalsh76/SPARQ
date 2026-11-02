"""S6.2: where the recorder keeps what it hears (PLAN §6.6, B1 and B4).

Everything lives under the recording folder on the analytics node's data drive, never in the
repository. Times are UTC; ``recv_us`` is the recorder's wall clock as a frame was read, in
microseconds since the epoch, and decides the hour a row belongs to.

* ``book/<hour>.parquet``: one row a book event, in arrival order. ``kind`` says what it is:
  ``RESET`` (the books are unknown from here: every market's when ``ticker`` is null, after a
  start, a lost link or a ``seq`` jump; one market's after an impossible change), ``SNAPSHOT``
  (the head of a snapshot: the market's book is replaced by the ``LEVEL`` rows that follow it at
  once, none when it is empty) and ``CHANGE`` (``size_e2`` is then the change, signed). ``side`` is
  1 for yes and 2 for no; prices are millionths of a dollar, sizes hundredths of a contract.
  ``clock``, ``ts_ms`` (a change's time) and ``send_ms`` (``sending_ts_ms``) are Kalshi's.
* ``trade/<hour>.parquet``: one row a trade on the trade channel.
* ``odd/<hour>.parquet``: a frame that did not fit its model, or of a type not recorded above,
  kept whole as text, so nothing heard is lost to a parser.
* ``gaps.jsonl``: the recorder's own log: starts and stops, lost links, ``seq`` jumps, refused
  resubscriptions, impossible books and failed refreshes, one JSON object a line, each written
  through to the disk.
* ``lists/<day>.json`` and ``lists/changes.jsonl``: each day's chosen markets and every market
  added or removed since.

An hour is written as it goes: every minute the rows so far become a part file under
``parts/<table>/<hour>/``, so a crash loses at most a minute, and when the hour closes its parts
are combined into the hour's file. Every file is written under a temporary name, flushed to the
drive (``F_FULLFSYNC`` on macOS, where a plain ``fsync`` stops at the drive's cache) and renamed,
and the folder is flushed after the rename, so a power cut leaves a whole file or none. A recorder
that starts again finishes what a crash left, and sets aside (``quarantine/``) a part that cannot
be read, so one torn file never stops the recording.
"""

import json
import os
import shutil
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from sparq_analytics.live.source import hour_name, hour_of

RESET, SNAPSHOT, LEVEL, CHANGE = 0, 1, 2, 3
SIDE_CODE = {"yes": 1, "no": 2}
SIDE_NAME = {1: "yes", 2: "no"}
US_PER_HOUR = 3_600_000_000
GAPS = "gaps.jsonl"

BOOK_SCHEMA: dict[str, Any] = {
    "recv_us": pl.Int64,
    "link": pl.Int16,
    "sid": pl.Int32,
    "seq": pl.Int64,
    "ticker": pl.String,
    "kind": pl.Int8,
    "side": pl.Int8,
    "price_e6": pl.Int32,
    "size_e2": pl.Int64,
    "clock": pl.Int64,
    "ts_ms": pl.Int64,
    "send_ms": pl.Int64,
}
TRADE_SCHEMA: dict[str, Any] = {
    "recv_us": pl.Int64,
    "link": pl.Int16,
    "sid": pl.Int32,
    "seq": pl.Int64,
    "trade_id": pl.String,
    "ticker": pl.String,
    "yes_price_e6": pl.Int32,
    "count_e2": pl.Int64,
    "taker_side": pl.String,
    "taker_book_side": pl.String,
    "is_block_trade": pl.Boolean,
    "ts_ms": pl.Int64,
    "send_ms": pl.Int64,
}
ODD_SCHEMA: dict[str, Any] = {
    "recv_us": pl.Int64,
    "link": pl.Int16,
    "type": pl.String,
    "error": pl.String,
    "raw": pl.String,
}
TABLES: dict[str, dict[str, Any]] = {"book": BOOK_SCHEMA, "trade": TRADE_SCHEMA, "odd": ODD_SCHEMA}
PART_ROWS = 1_000_000  # a buffer this long is written at once, whatever the minute

Rows = dict[str, list[tuple[Any, ...]]]


def _flush_to_drive(fd: int) -> None:
    if sys.platform == "darwin":  # past the drive's own cache (LESSONS: durability)
        import fcntl

        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
    else:
        os.fsync(fd)


def sync_file(path: Path) -> None:
    """Flush a written file to the drive."""
    fd = os.open(path, os.O_RDWR)
    try:
        _flush_to_drive(fd)
    finally:
        os.close(fd)


def sync_dir(path: Path) -> None:
    """Flush a folder, so a rename in it lasts (a folder cannot be opened for this off POSIX)."""
    if sys.platform == "win32":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        _flush_to_drive(fd)
    finally:
        os.close(fd)


def _put_in_place(staging: Path, target: Path) -> None:
    sync_file(staging)
    staging.replace(target)
    sync_dir(target.parent)


def hour_start(recv_us: int) -> datetime:
    return datetime.fromtimestamp(recv_us // US_PER_HOUR * 3600, tz=UTC)


def iso(at_us: int) -> str:
    return datetime.fromtimestamp(at_us / 1e6, tz=UTC).isoformat()


def hour_file(root: Path, table: str, hour: datetime) -> Path:
    return root / table / f"{hour_name(hour)}.parquet"


def parts_dir(root: Path, table: str, hour: datetime) -> Path:
    return root / "parts" / table / hour_name(hour)


def write_part(root: Path, table: str, hour: datetime, rows: list[tuple[Any, ...]]) -> Path:
    """Write ``rows`` as the hour's next part (under a temporary name, then renamed)."""
    folder = parts_dir(root, table, hour)
    folder.mkdir(parents=True, exist_ok=True)
    taken = [int(p.stem[1:]) for p in folder.glob("p*.parquet")]
    target = folder / f"p{max(taken, default=-1) + 1:05d}.parquet"
    staging = target.with_name(target.name + ".tmp")
    pl.DataFrame(rows, schema=TABLES[table], orient="row").write_parquet(staging)
    _put_in_place(staging, target)
    return target


def write_parts(root: Path, hour: datetime, rows: Rows) -> None:
    for table, table_rows in rows.items():
        if table_rows:
            write_part(root, table, hour, table_rows)


def combine(root: Path, table: str, hour: datetime) -> Path | None:
    """Combine the hour's parts into its file and remove them; None when there are none."""
    folder = parts_dir(root, table, hour)
    parts = sorted(folder.glob("p*.parquet")) if folder.exists() else []
    if not parts:
        shutil.rmtree(folder, ignore_errors=True)
        return None
    target = hour_file(root, table, hour)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + ".tmp")
    inputs = ([target] if target.exists() else []) + parts  # never write over rows already kept
    pl.scan_parquet(inputs).sink_parquet(staging)  # streamed: an hour need not fit in memory
    _put_in_place(staging, target)
    shutil.rmtree(folder)
    return target


def combine_hour(root: Path, hour: datetime) -> None:
    for table in TABLES:
        combine(root, table, hour)


def hour_named(name: str) -> datetime | None:
    try:
        return hour_of(name)
    except ValueError:  # not an hour's name: someone else's file, left alone
        return None


def _readable(part: Path) -> bool:
    try:
        pl.read_parquet_schema(part)  # the footer: a torn file has none
    except Exception:  # noqa: BLE001 (any failure to read means the part is lost)
        return False
    return True


def recover(root: Path, now_hour: datetime) -> dict[str, list[str]]:
    """Finish what a stop or a crash left, before recording starts again: a part that cannot be
    read is set aside in ``quarantine/`` (``lost``); the parts of a past hour are combined (or
    removed, when its file was written but the parts not yet removed); and a file of the current
    hour goes back to being its first part, so the hour goes on (``done``)."""
    done: list[str] = []
    lost: list[str] = []
    for table in TABLES:
        if (root / table).exists():
            for staging in (root / table).glob("*.tmp"):
                staging.unlink()
        parts_root = root / "parts" / table
        for folder in sorted(parts_root.iterdir()) if parts_root.exists() else []:
            hour = hour_named(folder.name)
            if hour is None:
                continue
            for staging in folder.glob("*.tmp"):
                staging.unlink()
            for part in sorted(folder.glob("p*.parquet")):
                if not _readable(part):
                    aside = root / "quarantine" / table / folder.name
                    aside.mkdir(parents=True, exist_ok=True)
                    part.replace(aside / part.name)
                    lost.append(f"{table}/{folder.name}/{part.name}")
            if hour_file(root, table, hour).exists():
                shutil.rmtree(folder)  # combined already; the parts outlived the crash
                done.append(f"{table} {folder.name}: removed parts already combined")
            elif hour < now_hour:
                combine(root, table, hour)
                done.append(f"{table} {folder.name}: combined after a stop")
        current = hour_file(root, table, now_hour)
        if current.exists():
            folder = parts_dir(root, table, now_hour)
            folder.mkdir(parents=True, exist_ok=True)
            current.replace(folder / "p00000.parquet")
            done.append(f"{table} {hour_name(now_hour)}: reopened")
    return {"done": done, "lost": lost}


def last_row_us(root: Path) -> int | None:
    """The receive time of the newest book row kept (in an hour's file or its parts), or None."""
    files = [p for p in (root / "book").glob("*.parquet") if hour_named(p.stem)] if (
        root / "book").exists() else []  # fmt: skip
    parts_root = root / "parts" / "book"
    for folder in parts_root.iterdir() if parts_root.exists() else []:
        if hour_named(folder.name):
            files += [p for p in folder.glob("p*.parquet") if _readable(p)]
    if not files:
        return None
    newest = max(files, key=lambda p: p.stat().st_mtime)
    value = pl.scan_parquet(newest).select(pl.col("recv_us").max()).collect().item()
    return None if value is None else int(value)


def newest_write(root: Path) -> float | None:
    """When the recording last wrote a part or an hour's file (seconds since the epoch)."""
    stamps = [p.stat().st_mtime for p in _written(root)]
    return max(stamps, default=None)


def _written(root: Path) -> Iterator[Path]:
    for table in TABLES:
        if (root / table).exists():
            yield from (root / table).glob("*.parquet")
    if (root / "parts").exists():
        yield from (root / "parts").rglob("*.parquet")


class Writer:
    """Rows buffered by table for the current hour. ``take`` hands over what is buffered (for a
    part file); a row of a new hour first hands the old hour's rows to ``on_hour``. An hour once
    closed is never reopened: a row stamped before the current hour (a frame read just before
    the hour turned but handled after) stays in the current hour's file."""

    def __init__(self, on_hour: Callable[[datetime, Rows], None]) -> None:
        self.hour: datetime | None = None
        self.rows: Rows = {table: [] for table in TABLES}
        self._on_hour = on_hour
        self._key = -1  # the current hour as a count of hours, cheap to compare on every row

    def add(self, table: str, row: tuple[Any, ...]) -> None:
        key = row[0] // US_PER_HOUR
        if key > self._key:
            if self.hour is not None:
                self._on_hour(self.hour, self.take())
            self._key, self.hour = key, hour_start(row[0])
        self.rows[table].append(row)

    def take(self) -> Rows:
        taken = self.rows
        self.rows = {table: [] for table in TABLES}
        return taken

    def size(self) -> int:
        return sum(len(rows) for rows in self.rows.values())


def log_event(root: Path, kind: str, at_us: int, **detail: Any) -> dict[str, Any]:
    """Append one event to the gap log and write it through to the disk."""
    event = {"kind": kind, "at": iso(at_us), "at_us": at_us, **detail}
    root.mkdir(parents=True, exist_ok=True)
    with (root / GAPS).open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, default=str) + "\n")
        file.flush()
        _flush_to_drive(file.fileno())
    return event


def read_events(path: Path) -> list[dict[str, Any]]:
    """The events logged, a line torn by a crash left out."""
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def write_list(root: Path, day: str, tickers: list[str], counts: dict[str, Any]) -> None:
    folder = root / "lists"
    folder.mkdir(parents=True, exist_ok=True)
    staging = folder / f"{day}.json.tmp"
    staging.write_text(json.dumps({"counts": counts, "tickers": tickers}), encoding="utf-8")
    _put_in_place(staging, folder / f"{day}.json")


def log_change(root: Path, at_us: int, action: str, tickers: list[str], why: str) -> None:
    folder = root / "lists"
    folder.mkdir(parents=True, exist_ok=True)
    event = {"at": iso(at_us), "action": action, "why": why, "tickers": tickers}
    with (folder / "changes.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(event) + "\n")
        file.flush()
        _flush_to_drive(file.fileno())


def hours_recorded(root: Path, table: str = "book") -> list[datetime]:
    folder = root / table
    hours = [hour_named(p.stem) for p in folder.glob("*.parquet")] if folder.exists() else []
    return sorted(h for h in hours if h is not None)


def read_hours(root: Path, table: str, start: datetime, end: datetime) -> pl.LazyFrame:
    """The rows of ``table`` in the recorded hours of ``[start, end)``, an hour still in parts read
    from them; an hour not recorded is simply absent (the gap log says why)."""
    paths: list[Path] = []
    hour = start
    while hour < end:
        whole = hour_file(root, table, hour)
        if whole.exists():
            paths.append(whole)
        else:
            paths += sorted(parts_dir(root, table, hour).glob("p*.parquet"))
        hour += timedelta(hours=1)
    if not paths:
        return pl.LazyFrame(schema=TABLES[table])
    return pl.scan_parquet(paths)
