"""The data folder: where the downloaded history lives, and the state that lets a run resume.

Layout under ``<root>`` (the folder is git-ignored; nothing in it is ever committed)::

    state.json                         what is done and where each unfinished piece stands
    series.parquet, fee_changes.parquet
    markets/historical/part-*.parquet  markets that settled before the cutoff
    markets/live/date=YYYY-MM-DD/markets.parquet
    trades/date=YYYY-MM-DD/trades.parquet   one file per UTC day, sorted by market and time
    candles/part-*.parquet
    _work/                             part files of the day being downloaded

Every file is written under a temporary name and renamed only when complete, so a crash leaves
either the old file or the new one and never half of one.
"""

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

STATE_VERSION = 1


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_table_atomic(table: pa.Table, path: Path) -> None:
    """Write a Parquet file (zstd) under a temporary name, then rename it into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    pq.write_table(
        table, temporary, compression="zstd", compression_level=3, row_group_size=1_000_000
    )
    with temporary.open("r+b") as handle:  # Windows needs a writable handle to fsync
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class DataDir:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state_path = root / "state.json"
        self.work = root / "_work"

    # --- paths ----------------------------------------------------------------------------

    def trades_file(self, day: str) -> Path:
        return self.root / "trades" / f"date={day}" / "trades.parquet"

    def live_markets_file(self, day: str) -> Path:
        return self.root / "markets" / "live" / f"date={day}" / "markets.parquet"

    def historical_markets_file(self, number: int) -> Path:
        return self.root / "markets" / "historical" / f"part-{number:06d}.parquet"

    def work_dir(self, *parts: str) -> Path:
        return self.work.joinpath(*parts)

    # --- space ----------------------------------------------------------------------------

    def bytes_used(self) -> int:
        """Total size of every file under the folder (finished files and work files)."""
        if not self.root.exists():
            return 0
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())

    def clear_work(self, *parts: str) -> None:
        shutil.rmtree(self.work_dir(*parts), ignore_errors=True)

    def remove_stale_temporaries(self) -> int:
        """Delete ``*.tmp`` files left by a crash (they are never read). Returns how many."""
        if not self.root.exists():
            return 0
        stale = [p for p in self.root.rglob("*.tmp") if p.is_file()]
        for path in stale:
            path.unlink()
        return len(stale)


class State:
    """A small JSON document, saved atomically after every unit of progress."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.data: dict[str, Any] = {"version": STATE_VERSION}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if loaded.get("version") != STATE_VERSION:
                raise ValueError(
                    f"{path} has state version {loaded.get('version')!r}, expected {STATE_VERSION}"
                )
            self.data = loaded

    def section(self, name: str) -> dict[str, Any]:
        value: dict[str, Any] = self.data.setdefault(name, {})
        return value

    def save(self) -> None:
        write_bytes_atomic(
            self._path, json.dumps(self.data, indent=1, sort_keys=True).encode("utf-8")
        )
