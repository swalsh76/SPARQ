import asyncio
from pathlib import Path

import pytest
from sparq_durability.ledger import (
    ID_SPACE,
    LedgerError,
    Span,
    first_id,
    merge,
    parse_line,
    read_ledgers,
    start_ledger,
)


def span(run: int, a: int, b: int) -> Span:
    return Span(run, first_id(run) + a, first_id(run) + b)


def test_a_span_line_is_parsed_and_the_other_lines_are_ignored() -> None:
    assert parse_line(f"1 {ID_SPACE + 1} {ID_SPACE + 100}") == Span(1, ID_SPACE + 1, ID_SPACE + 100)
    for ignored in ("", "ping", "hello 3", "# a note"):
        assert parse_line(ignored) is None
    assert Span(1, 5, 9).count == 5


@pytest.mark.parametrize(
    "line",
    [
        "nonsense",
        "1 5",
        f"1 {ID_SPACE + 9} {ID_SPACE + 3}",
        f"1 {2 * ID_SPACE + 1} {2 * ID_SPACE + 2}",
    ],
)
def test_a_malformed_or_out_of_run_line_is_an_error(line: str) -> None:
    with pytest.raises(LedgerError):
        parse_line(line)


def test_touching_and_overlapping_spans_merge_but_a_gap_or_another_run_does_not() -> None:
    spans = [span(1, 10, 19), span(1, 0, 9), span(1, 15, 30), span(1, 40, 41), span(2, 0, 4)]
    assert merge(spans) == [span(1, 0, 30), span(1, 40, 41), span(2, 0, 4)]
    assert merge([]) == []
    assert merge([span(1, 0, 9), span(1, 11, 20)]) == [
        span(1, 0, 9),
        span(1, 11, 20),
    ]  # one id missing


def test_ledger_files_are_read_with_their_notes(tmp_path: Path) -> None:
    one = first_id(1)
    (tmp_path / "ledger-run1.log").write_text(
        f"# t connected\n1 {one} {one}\n# t silent for 5 s: possible power loss\n", encoding="utf-8"
    )
    spans, notes = read_ledgers(tmp_path)
    assert spans == [Span(1, one, one)]
    assert notes == [
        "ledger-run1.log: t connected",
        "ledger-run1.log: t silent for 5 s: possible power loss",
    ]
    assert read_ledgers(tmp_path / "none") == ([], [])


def ledger_files(directory: Path) -> list[Path]:
    return list(directory.glob("ledger-run*.log"))


async def serve(tmp_path: Path, silence: float = 5.0) -> tuple[asyncio.Server, int]:
    server = await start_ledger(tmp_path, "127.0.0.1", 0, silence)
    return server, server.sockets[0].getsockname()[1]


async def test_the_ledger_records_spans_and_ignores_pings_and_other_runs(tmp_path: Path) -> None:
    server, port = await serve(tmp_path)
    async with server:
        _, out = await asyncio.open_connection("127.0.0.1", port)
        a, b, other = first_id(7), first_id(7) + 99, first_id(8)
        out.write(f"hello 7\nping\n7 {a} {a}\n7 {a + 1} {b}\n8 {other} {other}\n".encode())
        await out.drain()
        out.close()
        await out.wait_closed()
        await asyncio.sleep(0.2)
    spans, notes = read_ledgers(tmp_path)
    assert merge(spans) == [Span(7, a, b)]  # run 8's span on run 7's connection was not recorded
    assert any("closed by the writer" in note for note in notes)


async def test_a_quiet_connection_is_recorded_as_a_possible_power_loss(tmp_path: Path) -> None:
    server, port = await serve(tmp_path, silence=0.2)
    async with server:
        _, out = await asyncio.open_connection("127.0.0.1", port)
        out.write(f"hello 2\n2 {first_id(2)} {first_id(2)}\n".encode())
        await out.drain()
        await asyncio.sleep(0.6)  # the writer goes silent: no ping, no close
        out.close()
    spans, notes = read_ledgers(tmp_path)
    assert len(spans) == 1
    assert any("possible power loss" in note for note in notes)


@pytest.mark.parametrize("first_line", [b"goodbye\n", b"hello x\n", b"\xff\xfe\n"])
async def test_a_connection_that_does_not_say_hello_records_nothing(
    tmp_path: Path, first_line: bytes
) -> None:
    server, port = await serve(tmp_path)
    async with server:
        _, out = await asyncio.open_connection("127.0.0.1", port)
        out.write(first_line)
        await out.drain()
        await asyncio.sleep(0.2)
        out.close()
    assert ledger_files(tmp_path) == []


async def test_a_malformed_line_ends_the_connection_but_keeps_what_came_before(
    tmp_path: Path,
) -> None:
    server, port = await serve(tmp_path)
    async with server:
        _, out = await asyncio.open_connection("127.0.0.1", port)
        out.write(f"hello 4\n4 {first_id(4)} {first_id(4)}\nrubbish\n".encode())
        await out.drain()
        await asyncio.sleep(0.2)
        out.close()
    spans, _ = read_ledgers(tmp_path)
    assert [(x.run, x.first, x.last) for x in spans] == [(4, first_id(4), first_id(4))]
    assert spans[0].at_ms is not None  # every line is stamped with the ledger's clock
