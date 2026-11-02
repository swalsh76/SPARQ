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
    store.log_event(out, "reconnect", H_US + 9_500_000, link=2, reason="connection_lost",
                    downtime=1.0)  # fmt: skip
    theirs = [trade(500, "A", "t0"), trade(3_000, "A", "t1", 200), trade(9_000, "A", "t3"),
              trade(20_000, "B", "t4"), trade(3_000, "Z", "tz")]  # fmt: skip
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
    assert (trades["recorded"], trades["ingest"]) == (2, 3)  # t0 before A's book, tz not recorded
    assert (trades["recorded_contracts_e2"], trades["ingest_contracts_e2"]) == (300, 400)
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
    result = checking.compare_trades(ours, theirs, {"A": H_MS}, [(H_MS + 8_500, H_MS + 9_500)])
    assert result["agree"] and result["missing_in_downtime"] == 1


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
