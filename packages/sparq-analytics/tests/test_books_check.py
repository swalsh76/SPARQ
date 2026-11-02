"""S6.2's ``status`` and ``check`` over built recordings and copies, and the command line.

**The recording** (the hour from 2026-11-02 18:00 UTC, times below in milliseconds into it): every
book is reset at 0; ``A``'s snapshot at 1,000 (a yes bid of 40 cents for 10 contracts and a no bid
of 55 for 2, so a yes ask of 45), a change at 5,000 to a bid of 41 for 3, an impossible change
resetting it at 9,000, and a snapshot again at 10,000 with the bid of 40 alone; ``B``'s empty
snapshot at 1,000. **The ingest's ticker frames:** ``A`` at 500 and 9,500 (its book unknown), at
2,000, 5,000 and 11,000 (agreeing; the last with an empty ask) and at 6,000 (a bid of 40 where the
book shows 41); ``B`` empty at 3,000; ``Z`` (not recorded) at 3,000.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from sparq_analytics.books import check as checking
from sparq_analytics.books import cli, store
from sparq_analytics.books import recorder as rec
from sparq_analytics.books import status as statusing
from sparq_analytics.books.store import CHANGE, LEVEL, RESET, SNAPSHOT
from sparq_analytics.live.source import TICKER_SCHEMA, TRADE_SCHEMA, hour_name
from sparq_core.auth import DataSigner
from test_books_probe import write_live
from test_books_recorder import Feed

H = datetime(2026, 11, 2, 18, tzinfo=UTC)
H_MS = int(H.timestamp() * 1000)
H_US = H_MS * 1000


def b(
    ms: int,
    ticker: str | None,
    kind: int,
    side: int | None = None,
    price: int | None = None,
    size: int | None = None,
    ts: int | None = None,
    send: int | None = None,
) -> tuple[Any, ...]:
    return (H_US + ms * 1000, 1, 1, ms, ticker, kind, side, price, size, 1, ts, send)


BOOK = [
    b(0, None, RESET),
    b(1_000, "A", SNAPSHOT, send=H_MS + 1_000),
    b(1_000, "A", LEVEL, 1, 400_000, 1_000),
    b(1_000, "A", LEVEL, 2, 550_000, 200),
    b(1_000, "B", SNAPSHOT, send=H_MS + 1_000),
    b(5_000, "A", CHANGE, 1, 410_000, 300, ts=H_MS + 5_000),
    b(9_000, "A", RESET),
    b(10_000, "A", SNAPSHOT, send=H_MS + 10_000),
    b(10_000, "A", LEVEL, 1, 400_000, 1_000),
    b(12_000, "Q", CHANGE, 1, 400_000, 100, ts=H_MS + 12_000),  # no snapshot: unknown
]


def frame(ms: int, ticker: str, bid: int, bid_size: int, ask: int, ask_size: int) -> dict[str, Any]:
    return {"ts_ms": H_MS + ms, "recv_ms": H_MS + ms, "ticker": ticker, "price_e6": bid,
            "yes_bid_e6": bid, "yes_ask_e6": ask, "yes_bid_size_e2": bid_size,
            "yes_ask_size_e2": ask_size, "volume_e2": 0}  # fmt: skip


FRAMES = [
    frame(500, "A", 400_000, 1_000, 450_000, 200),
    frame(2_000, "A", 400_000, 1_000, 450_000, 200),
    frame(3_000, "B", 0, 0, 1_000_000, 0),
    frame(3_000, "Z", 400_000, 1_000, 450_000, 200),
    frame(5_000, "A", 410_000, 300, 450_000, 200),
    frame(6_000, "A", 400_000, 1_000, 450_000, 200),
    frame(9_500, "A", 400_000, 1_000, 450_000, 200),
    frame(11_000, "A", 400_000, 1_000, 1_000_000, 0),
]


def trade(ms: int, ticker: str, trade_id: str, count: int = 100) -> dict[str, Any]:
    return {"ts_ms": H_MS + ms, "ticker": ticker, "trade_id": trade_id, "yes_price_e6": 400_000,
            "count_e2": count, "taker_side": "yes", "is_block_trade": False}  # fmt: skip


def recorded_trade(ms: int, ticker: str, trade_id: str, count: int = 100) -> tuple[Any, ...]:
    return (H_US + ms * 1000, 1, 2, 1, trade_id, ticker, 400_000, count, "yes", "bid", False,
            H_MS + ms, H_MS + ms)  # fmt: skip


def build(tmp_path: Path, *, extra_trade: bool = True) -> tuple[Path, Path]:
    out, live = tmp_path / "rec", tmp_path / "live"
    store.write_part(out, "book", H, BOOK)
    ours = [recorded_trade(3_000, "A", "t1", 200), recorded_trade(3_000, "A", "t1", 200)]
    if extra_trade:
        ours.append(recorded_trade(4_000, "A", "t5"))
    store.write_part(out, "trade", H, ours)
    store.combine_hour(out, H)
    late = (H_US + 3_600_000_000 + 50_000, 1, 2, 2, "t9", "A", 400_000, 100, "yes", "bid", False,
            H_MS + 3_599_990, H_MS + 3_599_995)  # made before 19:00, received after  # fmt: skip
    store.write_part(out, "trade", H + timedelta(hours=1), [late])
    store.log_event(out, "reconnect", H_US + 9_500_000, link=2, reason="connection_lost",
                    downtime=1.0)  # fmt: skip
    theirs = [trade(500, "A", "t0"), trade(3_000, "A", "t1", 200), trade(9_000, "A", "t3"),
              trade(20_000, "B", "t4"), trade(3_000, "Z", "tz"),
              trade(3_599_990, "A", "t9")]  # fmt: skip
    for name, rows, schema in (("ticker", FRAMES, TICKER_SCHEMA), ("trade", theirs, TRADE_SCHEMA)):
        (live / name).mkdir(parents=True)
        pl.DataFrame(rows, schema=schema).write_parquet(live / name / f"{hour_name(H)}.parquet")
    return out, live


def test_the_tops_are_rebuilt_from_the_rows_in_order() -> None:
    tops = checking.rebuild_tops(pl.DataFrame(BOOK, schema=store.BOOK_SCHEMA, orient="row"))
    assert tops.rows() == [
        ("A", H_MS + 1_000, True, 400_000, 450_000, 1_000, 200),
        ("B", H_MS + 1_000, True, None, None, None, None),
        ("A", H_MS + 5_000, True, 410_000, 450_000, 300, 200),
        ("A", H_MS + 9_000, False, None, None, None, None),
        ("A", H_MS + 10_000, True, 400_000, None, 1_000, None),
    ]


def test_the_check_compares_every_moment_and_the_trades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, live = build(tmp_path)
    result = checking.check(out, live, H, H + timedelta(hours=1))
    books = result["books"]
    assert (books["frames"], books["moments"], books["agree"], books["moments_unknown"]) == (
        7, 5, 4, 2)  # fmt: skip
    assert books["share"] == pytest.approx(0.8) and books["share_with_sizes"] == pytest.approx(0.8)
    assert (books["markets"], books["markets_changed"]) == (2, 1)
    assert (
        books["per_market"]["min"] == pytest.approx(0.75) and books["per_market"]["under_99"] == 1
    )
    assert books["misses"] == [
        {
            "ticker": "A",
            "ts_ms": H_MS + 6_000,
            "yes_bid_e6": 400_000,
            "bid_e6": 410_000,
            "yes_ask_e6": 450_000,
            "ask_e6": 450_000,
        }
    ]
    assert not books["passes"]  # 80% and one market whose book changed
    trades = result["trades"]
    # t0 came before A's book, tz is not recorded, t9 was received after the hour: still found
    assert (trades["recorded"], trades["ingest"]) == (3, 4)
    assert (trades["recorded_contracts_e2"], trades["ingest_contracts_e2"]) == (400, 500)
    assert (trades["missing"], trades["missing_in_downtime"], trades["extra"]) == (2, 1, 1)
    assert not trades["agree"] and not result["passes"] and result["book_rows"] == len(BOOK)


def test_the_bar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checking, "BAR_SHARE", 0.8)
    monkeypatch.setattr(checking, "BAR_MARKETS", 1)
    out, live = build(tmp_path)
    assert checking.check(out, live, H, H + timedelta(hours=1))["books"]["passes"]
    empty = checking.compare_books(
        pl.DataFrame(schema=checking.TOP_SCHEMA), pl.DataFrame(FRAMES[:0], schema=TICKER_SCHEMA)
        .with_columns(mid_e6=pl.lit(None, pl.Int64)), set(),
    )  # fmt: skip
    assert empty["share"] is None and not empty["passes"] and empty["per_market"]["min"] is None


def test_trades_agree_when_only_a_lost_links_are_missing() -> None:
    schema = {k: v for k, v in store.TRADE_SCHEMA.items()}
    ours = pl.DataFrame([recorded_trade(3_000, "A", "t1")], schema=schema, orient="row")
    theirs = pl.DataFrame([trade(3_000, "A", "t1"), trade(9_000, "A", "t3")], schema=TRADE_SCHEMA)
    spans = pl.DataFrame(
        {"ticker": ["A"], "first_ms": [H_MS], "last_ms": [None]},
        schema={"ticker": pl.String, "first_ms": pl.Int64, "last_ms": pl.Int64},
    )
    result = checking.compare_trades(ours, theirs, spans, [(H_MS + 8_500, H_MS + 9_500)])
    assert result["agree"] and result["missing_in_downtime"] == 1
    wrong = ours.with_columns(count_e2=pl.lit(999, pl.Int64))
    result = checking.compare_trades(wrong, theirs, spans, [(H_MS + 8_500, H_MS + 9_500)])
    assert result["wrong_size"] == 1 and not result["agree"]


def test_a_removed_market_is_compared_only_while_it_was_recorded() -> None:
    rows = [
        b(0, None, RESET),
        b(1_000, "A", SNAPSHOT, send=H_MS + 1_000),
        b(2_000, "A", RESET),  # an impossible change: a snapshot follows
        b(3_000, "A", SNAPSHOT, send=H_MS + 3_000),
        b(1_000, "R", SNAPSHOT, send=H_MS + 1_000),
        b(4_000, "R", RESET),  # removed: no snapshot follows
    ]
    book = pl.DataFrame(rows, schema=store.BOOK_SCHEMA, orient="row")
    spans = checking.market_spans(book).sort("ticker")
    assert spans.rows() == [("A", H_MS + 1_000, None), ("R", H_MS + 1_000, H_MS + 4_000)]
    theirs = pl.DataFrame([trade(3_500, "R", "r1"), trade(5_000, "R", "r2")], schema=TRADE_SCHEMA)
    ours = pl.DataFrame([recorded_trade(3_500, "R", "r1")], schema=store.TRADE_SCHEMA, orient="row")
    result = checking.compare_trades(ours, theirs, spans, [])
    assert result["agree"] and result["ingest"] == 1  # r2 came after R was removed


def test_a_markets_tops_never_go_back_in_time() -> None:
    rows = [
        b(1_000, "A", SNAPSHOT, send=H_MS + 1_000),
        b(1_000, "A", LEVEL, 1, 400_000, 100),
        b(5_000, "A", CHANGE, 1, 410_000, 100, ts=H_MS + 5_000),
        b(4_990, "A", RESET),  # stamped by the recorder's clock, a little behind Kalshi's
    ]
    tops = checking.rebuild_tops(pl.DataFrame(rows, schema=store.BOOK_SCHEMA, orient="row"))
    assert tops["t_ms"].to_list() == [H_MS + 1_000, H_MS + 5_000, H_MS + 5_000]
    assert tops["known"].to_list() == [True, True, False]


# -- status ---------------------------------------------------------------------------------------


def event(hours: float, kind: str) -> dict[str, Any]:
    return {"kind": kind, "at_us": H_US + int(hours * store.US_PER_HOUR)}


def test_whole_hours_are_those_the_recorder_ran_through() -> None:
    hours = [H + timedelta(hours=n) for n in range(-1, 4)]
    events = [event(-0.5, "started"), event(1.5, "reconnect"), event(3.2, "stopped"),
              event(2.5, "impossible")]  # fmt: skip
    whole = statusing.whole_hours(hours, events)
    assert whole == [
        H,
        H + timedelta(hours=2),
    ]  # 17:00 began unstarted, 19:00 broken, 21:00 stopped
    assert statusing.whole_hours(hours, [event(-2, "started"), event(-1.5, "stopped")]) == []


def test_the_status_of_a_recording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out, _ = build(tmp_path)
    store.log_event(out, "started", H_US - 1)
    store.write_part(out, "book", H + timedelta(hours=1), BOOK)  # an hour still in parts
    store.write_list(out, "2026-11-02", ["A"], {})
    now = datetime(2026, 11, 3, 1, tzinfo=UTC)
    for path in out.rglob("*.parquet"):  # written a minute before now
        os.utime(path, (now.timestamp() - 60, now.timestamp() - 60))
    report = statusing.status(out, now)
    assert report["hours_recorded"] == 1 and report["hours_whole"] == 0  # the reconnect broke it
    assert report["running"] and report["list"] == {"day": "2026-11-02", "file": "2026-11-02.json"}
    assert report["bytes_by_day"]["2026-11-02"] > 0 and report["bytes_in_parts"] > 0
    assert report["warnings"] == [] and report["events"] == {"reconnect": 1, "started": 1}
    monkeypatch.setattr(statusing, "WARN_BYTES_A_DAY", 1)
    monkeypatch.setattr(statusing, "PAUSE_BYTES", 1)
    warnings = statusing.status(out, now)["warnings"]
    assert "over 0 GB a day" in warnings[0] and "past the 0 GB pause" in warnings[1]
    nothing = statusing.status(tmp_path / "none", now)
    assert nothing["hours_recorded"] == 0 and nothing["first_hour"] is None
    assert nothing["list"] is None and not nothing["running"]


# -- the command line -----------------------------------------------------------------------------


def test_check_and_status_on_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out, live = build(tmp_path)
    argv = ["check", "--out", str(out), "--live", str(live)]
    assert (
        cli.main([*argv, "--start", hour_name(H), "--end", hour_name(H + timedelta(hours=1))]) == 1
    )
    printed = json.loads(capsys.readouterr().out)
    assert json.loads((out / "check.json").read_text(encoding="utf-8")) == printed
    monkeypatch.setattr(checking, "BAR_SHARE", 0.8)
    monkeypatch.setattr(checking, "BAR_MARKETS", 1)
    monkeypatch.setattr(checking, "compare_trades", lambda *a: {"agree": True})
    assert (
        cli.main([*argv, "--start", hour_name(H), "--end", hour_name(H + timedelta(hours=1))]) == 0
    )
    capsys.readouterr()
    assert cli.main(argv) == 1 and "needs --start and --end" in capsys.readouterr().err
    assert cli.main([*argv, "--start", "18", "--end", "19"]) == 1
    assert "UTC hours" in capsys.readouterr().err
    assert cli.main([*argv, "--start", hour_name(H), "--end", hour_name(H)]) == 1
    assert "after --start" in capsys.readouterr().err
    assert cli.main(["status", "--out", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["hours_recorded"] == 1


def test_record_on_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    live, out = write_live(tmp_path), tmp_path / "rec"

    class Managed(Feed):
        async def __aenter__(self) -> "Managed":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    refreshed: list[datetime] = []

    async def refresh(live: Path, since: datetime, limit_s: float) -> str | None:
        refreshed.append(since)
        return "no database here"

    monkeypatch.setattr(cli, "make_feed", lambda signer: Managed())
    monkeypatch.setattr(rec, "refresh_copies", refresh)
    monkeypatch.setattr(DataSigner, "from_environment", classmethod(lambda cls: object()))
    argv = ["record", "--live", str(live), "--out", str(out)]
    assert cli.main([*argv, "--minutes", "0.005", "--production"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["markets"] == 3 and printed["feed"] == {"generation": 1}
    assert json.loads((out / "record.json").read_text(encoding="utf-8")) == printed
    assert refreshed  # the built copies end on 2026-11-02 at midnight: stale
    assert cli.main([*argv, "--minutes", "1"]) == 1
    assert "add --production" in capsys.readouterr().err
    assert cli.main([*argv, "--minutes", "0", "--production"]) == 1
    assert "above zero" in capsys.readouterr().err


def test_a_crash_breaks_its_hour_and_a_silent_recorder_is_not_running(tmp_path: Path) -> None:
    hours = [H, H + timedelta(hours=1), H + timedelta(hours=2)]
    events = [event(-0.5, "started"), event(1.3, "crashed"), event(2.5, "started")]
    assert statusing.whole_hours(hours, events) == [H]  # 19:00 broken, 20:00 down then restarted
    out, _ = build(tmp_path)
    store.log_event(out, "started", H_US)
    now = datetime(2026, 11, 3, 1, tzinfo=UTC)
    for path in out.rglob("*.parquet"):  # the last write an hour ago
        os.utime(path, (now.timestamp() - 3_600, now.timestamp() - 3_600))
    report = statusing.status(out, now)
    assert not report["running"] and report["seconds_since_a_write"] == pytest.approx(3_600)
    assert "written nothing for 60 minutes" in report["warnings"][0]


def test_the_check_carries_the_books_from_one_hour_to_the_next(tmp_path: Path) -> None:
    out, live = build(tmp_path)
    late = H + timedelta(hours=1)
    (live / "ticker" / f"{hour_name(late)}.parquet").unlink(missing_ok=True)
    rows = [frame(3_600_000 + 1_000, "A", 400_000, 1_000, 1_000_000, 0),  # A's book from 18:00
            frame(3_600_000 + 2_000, "B", 0, 0, 1_000_000, 0)]  # fmt: skip
    pl.DataFrame(rows, schema=TICKER_SCHEMA).write_parquet(
        live / "ticker" / f"{hour_name(late)}.parquet"
    )
    pl.DataFrame(schema=TRADE_SCHEMA).write_parquet(live / "trade" / f"{hour_name(late)}.parquet")
    result = checking.check(out, live, H, late + timedelta(hours=1))
    assert result["books"]["moments"] == 7 and result["books"]["agree"] == 6


def test_a_lost_links_window_starts_at_the_last_frame(tmp_path: Path) -> None:
    out, live = build(tmp_path)
    store.log_event(out, "reconnect", H_US + 30_000_000, link=3, reason="stalled", downtime=0.1,
                    lost_at_us=H_US + 19_000_000)  # fmt: skip
    trades = checking.check(out, live, H, H + timedelta(hours=1))["trades"]
    assert trades["missing_in_downtime"] == 2  # t3 (in the first) and t4 at 20 s (in this one)


def test_a_recorder_just_started_is_running_before_its_first_part(tmp_path: Path) -> None:
    out, _ = build(tmp_path)
    now = datetime(2026, 11, 3, 1, tzinfo=UTC)
    for path in out.rglob("*.parquet"):  # the old run's files, hours old
        os.utime(path, (now.timestamp() - 7_200, now.timestamp() - 7_200))
    store.log_event(out, "started", int(now.timestamp() * 1e6) - 20_000_000)
    report = statusing.status(out, now)
    assert report["running"] and report["seconds_since_a_write"] == pytest.approx(20)
