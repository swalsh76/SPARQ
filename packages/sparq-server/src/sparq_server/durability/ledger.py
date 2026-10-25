"""The ledger: the record, kept on another machine, of every commit the database acknowledged.

The writer sends ``<run> <first> <last>`` after each commit returns; it commits continuously, so
silence means something. The ledger appends each span to ``ledger-run<N>.log`` and
notes in the file when a connection goes quiet: a power cut gives no goodbye, so a read timeout is
the only sign of it. A span in the ledger is a promise the database made, so after a cut every id
in every span must be in the database.
"""

import asyncio
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ID_SPACE = 10**12  # run N owns the ids N * ID_SPACE + 1 and up
SILENCE_SECONDS = 5.0
FILE_PATTERN = "ledger-run*.log"
_SPAN = re.compile(r"^(\d+) (\d+) (\d+)(?: (\d+))?$")


class LedgerError(Exception):
    """A ledger line that is not a span, a comment or a ping."""


@dataclass(frozen=True, slots=True)
class Span:
    run: int
    first: int
    last: int
    at_ms: int | None = None  # when the ledger heard of it (its own clock), if recorded

    @property
    def count(self) -> int:
        return self.last - self.first + 1


def first_id(run: int) -> int:
    return run * ID_SPACE + 1


def parse_line(line: str) -> Span | None:
    """The span on a line, or None for a comment, a ping, a hello or a blank line."""
    text = line.strip()
    if not text or text.startswith("#") or text == "ping" or text.startswith("hello "):
        return None
    match = _SPAN.match(text)
    if match is None:
        raise LedgerError(f"not a ledger line: {text[:60]!r}")
    run, first, last = (int(group) for group in match.groups()[:3])
    at_ms = int(match.group(4)) if match.group(4) else None
    if last < first or first <= run * ID_SPACE or last >= (run + 1) * ID_SPACE:
        raise LedgerError(f"span {first}..{last} is not inside run {run}")
    return Span(run, first, last, at_ms)


def merge(spans: list[Span]) -> list[Span]:
    """Join touching and overlapping spans of the same run, so a million commits become one."""
    merged: list[Span] = []
    for span in sorted(spans, key=lambda s: (s.run, s.first)):
        if merged and merged[-1].run == span.run and span.first <= merged[-1].last + 1:
            previous = merged.pop()
            span = Span(span.run, previous.first, max(previous.last, span.last))
        merged.append(span)
    return merged


def read_ledgers(directory: Path) -> tuple[list[Span], list[str]]:
    """Every span in every ledger file, and every comment (the notes about silence and closes)."""
    spans: list[Span] = []
    notes: list[str] = []
    for path in sorted(directory.glob(FILE_PATTERN)):
        for line in path.read_text(encoding="utf-8").splitlines():
            span = parse_line(line)
            if span is not None:
                spans.append(span)
            elif line.startswith("#"):
                notes.append(f"{path.name}: {line[1:].strip()}")
    return spans, notes


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _handle(
    directory: Path,
    silence: float,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    out = None
    try:
        hello = (await asyncio.wait_for(reader.readline(), silence)).decode().split()
        if len(hello) != 2 or hello[0] != "hello" or not hello[1].isdigit():
            return
        run = int(hello[1])
        out = (directory / f"ledger-run{run}.log").open("a", encoding="utf-8")
        out.write(f"# {_stamp()} connected\n")
        synced = time.monotonic()
        while True:
            try:
                raw = await asyncio.wait_for(reader.readline(), silence)
            except TimeoutError:
                out.write(f"# {_stamp()} silent for {silence:g} s: possible power loss\n")
                return
            if not raw:
                out.write(f"# {_stamp()} closed by the writer\n")
                return
            line = raw.decode().strip()
            span = parse_line(line)
            if span is not None and span.run == run:
                out.write(f"{line} {int(time.time() * 1000)}\n")
                out.flush()
                if time.monotonic() - synced > 1.0:
                    os.fsync(out.fileno())
                    synced = time.monotonic()
    except (LedgerError, UnicodeDecodeError, ConnectionError, TimeoutError):
        pass  # a malformed or dropped connection records nothing more
    finally:
        if out is not None:
            out.flush()
            os.fsync(out.fileno())
            out.close()
        writer.close()


async def start_ledger(
    directory: Path, host: str, port: int, silence: float = SILENCE_SECONDS
) -> asyncio.Server:
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle(directory, silence, reader, writer)

    return await asyncio.start_server(handle, host, port)


async def serve_forever(directory: Path, host: str, port: int, silence: float) -> None:
    server = await start_ledger(directory, host, port, silence)
    async with server:
        await server.serve_forever()
