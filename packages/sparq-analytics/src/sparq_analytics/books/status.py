"""S6.2: what the recording holds, read from its folder alone (PLAN §6.6, B4 and B5).

An hour is **whole** when its book file is written and the recorder ran through all of it: started
before it, with no stop, start, crash, lost link, ``seq`` jump or refused resubscription inside it.
A crash (a recorder killed, or a power cut) writes no stop; the next start logs it, at the time of
the last row kept. A recorder that has written nothing for ``SILENT_SECONDS`` is not running,
whatever the log says. One
market's impossible book does not break an hour (that market alone is asked for again); those are
counted. The size of each day is the bytes of its hour files; ``status`` warns when the last
complete day passes ``WARN_BYTES_A_DAY``, twice the rate measured in S6.2's live hour (B4). The
500 GB pause is reported here; stopping for it is S6.3's.
"""

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sparq_analytics.books import store
from sparq_analytics.live.source import hour_name, hour_of

WARN_BYTES_A_DAY = 14 * 10**9  # twice the rate measured in the live hour (BUILD-LOG S6.2)
PAUSE_BYTES = 500 * 10**9  # B4: the recording pauses for the owner's word past this
BREAKS = frozenset(
    {"started", "stopped", "crashed", "reconnect", "sequence_gap", "resubscribe_failed"}
)
ENDS = ("stopped", "crashed", "resubscribe_failed")  # after these the recorder is not running
SILENT_SECONDS = 300  # a part is written every minute: five without one means it has stopped


def whole_hours(hours: list[datetime], events: list[dict[str, Any]]) -> list[datetime]:
    """The recorded hours the recorder ran through without a break."""
    marks = sorted((e["at_us"], e["kind"]) for e in events if e["kind"] in BREAKS)
    whole = []
    for hour in hours:
        start_us = int(hour.timestamp() * 1_000_000)
        end_us = start_us + store.US_PER_HOUR
        before = [kind for at, kind in marks if at < start_us]
        running = bool(before) and before[-1] not in ENDS
        broken = any(start_us <= at < end_us for at, _ in marks)
        if running and not broken:
            whole.append(hour)
    return whole


def _bytes(paths: list[Path]) -> int:
    return sum(p.stat().st_size for p in paths)


def status(out: Path, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    events = store.read_events(out / store.GAPS)
    hours = store.hours_recorded(out)
    whole = whole_hours(hours, events)
    by_day: Counter[str] = Counter()
    for table in store.TABLES:
        for path in (out / table).glob("*.parquet") if (out / table).exists() else []:
            by_day[hour_of(path.stem).date().isoformat()] += path.stat().st_size
    in_parts = _bytes(list((out / "parts").rglob("*.parquet"))) if (out / "parts").exists() else 0
    total = sum(by_day.values()) + in_parts
    today = now.date().isoformat()
    complete = sorted(day for day in by_day if day < today)
    last_day = complete[-1] if complete else None
    kinds = Counter(e["kind"] for e in events)
    last = next((e for e in reversed(events) if e["kind"] == "started" or e["kind"] in ENDS), None)
    logged = last is not None and last["kind"] == "started"
    written = store.newest_write(out)
    if logged and last is not None:  # a run just started has written nothing of its own yet
        written = max(written or 0.0, last["at_us"] / 1e6)
    silent = None if written is None else now.timestamp() - written
    running = logged and silent is not None and silent < SILENT_SECONDS
    lists = sorted((out / "lists").glob("*.json")) if (out / "lists").exists() else []
    warnings = []
    if logged and not running:
        quiet = "ever" if silent is None else f"{silent / 60:.0f} minutes"
        warnings.append(
            f"the log says the recorder is running, but it has written nothing for {quiet}"
        )
    if last_day is not None and by_day[last_day] > WARN_BYTES_A_DAY:
        warnings.append(f"{last_day} took {by_day[last_day] / 1e9:.1f} GB, over "
                        f"{WARN_BYTES_A_DAY / 1e9:.0f} GB a day")  # fmt: skip
    if total > PAUSE_BYTES:
        held, pause = total / 1e9, PAUSE_BYTES / 1e9
        warnings.append(f"the recording holds {held:.0f} GB, past the {pause:.0f} GB pause")
    first = hours[0] if hours else None
    span = int((hours[-1] - first) / timedelta(hours=1)) + 1 if first is not None else 0
    return {
        "running": running,
        "seconds_since_a_write": silent,
        "hours_recorded": len(hours),
        "hours_whole": len(whole),
        "hours_missing_in_span": span - len(hours),
        "first_hour": None if first is None else hour_name(first),
        "last_hour": None if not hours else hour_name(hours[-1]),
        "bytes_by_day": dict(sorted(by_day.items())),
        "bytes_in_parts": in_parts,
        "bytes_total": total,
        "events": dict(kinds),
        "impossible_books": kinds["impossible"],
        "last_event": events[-1] if events else None,
        "list": None if not lists else {"day": lists[-1].stem, "file": lists[-1].name},
        "warnings": warnings,
    }
