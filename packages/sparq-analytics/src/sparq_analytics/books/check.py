"""S6.2: the recording against the ingest's own record (PLAN §6.6, B5).

**The books.** Each market's top of book is rebuilt from the book rows in order (``rebuild_tops``):
a snapshot sets it at Kalshi's sending time, a change moves it at the change's time, and a
``RESET`` makes it unknown. Every ticker frame the ingest recorded for a recorded market inside the
hours checked is a **moment compared**: the rebuilt top standing at the frame's time (the last at
or before it) against the frame's best bid and ask, an empty side matching an empty side (the feed
shows one as a 0-cent bid or a 100-cent ask with no size; ``frames_of`` reads it as missing).
Moments where the rebuilt book is unknown are left out and counted. B5's bar: agreement on at
least 99% of the moments, over at least 300 markets whose book changed; the agreement is also
given per market, so a quiet market's errors are not hidden by busy ones.

**The trades.** The trades recorded against the ingest's for the same markets (each from its first
snapshot in the hours checked) and hours, matched by ``trade_id``: in count and in contracts. A
trade the recording lacks inside a lost link's downtime is counted apart: Kalshi replays nothing.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from sparq_analytics.books import store
from sparq_analytics.books.keeper import Keeper, Top
from sparq_analytics.books.store import CHANGE, LEVEL, RESET, SIDE_NAME, SNAPSHOT
from sparq_analytics.live.source import hour_files
from sparq_analytics.survey.lag import frames_of

BAR_SHARE = 0.99
BAR_MARKETS = 300
DOWNTIME_MARGIN_MS = 5_000  # a trade this close to a lost link's downtime is counted with it
TOP_SCHEMA = {
    "ticker": pl.String,
    "t_ms": pl.Int64,
    "known": pl.Boolean,
    "bid_e6": pl.Int64,
    "ask_e6": pl.Int64,
    "bid_e2": pl.Int64,
    "ask_e2": pl.Int64,
}
BOOK_COLUMNS = ["recv_us", "ticker", "kind", "side", "price_e6", "size_e2", "ts_ms", "send_ms"]


def rebuild_tops(book: pl.DataFrame) -> pl.DataFrame:
    """Each market's top of book after every book row that moved it, in the rows' order."""
    keeper = Keeper()
    rows: list[tuple[Any, ...]] = []
    last: dict[str, Top | None] = {}

    def emit(ticker: str, t_ms: int) -> None:
        top = keeper.top(ticker)
        if ticker in last and last[ticker] == top:
            return
        last[ticker] = top
        if top is None:
            rows.append((ticker, t_ms, False, None, None, None, None))
        else:
            rows.append((ticker, t_ms, True, top.bid_e6, top.ask_e6, top.bid_e2, top.ask_e2))

    filling: tuple[str, int] | None = None  # the snapshot whose levels are being read
    for recv_us, ticker, kind, side, price, size, ts_ms, send_ms in book.select(
        BOOK_COLUMNS
    ).iter_rows():
        if filling is not None and kind != LEVEL:
            emit(*filling)
            filling = None
        recv_ms = recv_us // 1000
        if kind == RESET:
            gone = list(keeper.books) if ticker is None else [ticker]
            keeper.forget(ticker)
            for market in gone:
                emit(market, recv_ms)
        elif kind == SNAPSHOT:
            keeper.replace(ticker, (), ())
            filling = (ticker, send_ms if send_ms is not None else recv_ms)
        elif kind == LEVEL:
            keeper.books[ticker].set_level(SIDE_NAME[side], price, size)
        elif kind == CHANGE:
            if keeper.apply(ticker, SIDE_NAME[side], price, size) is not None:
                emit(ticker, ts_ms if ts_ms is not None else recv_ms)
    if filling is not None:
        emit(*filling)
    return pl.DataFrame(rows, schema=TOP_SCHEMA, orient="row")


def compare_books(tops: pl.DataFrame, frames: pl.DataFrame, changed: set[str]) -> dict[str, Any]:
    """The moments compared and how many agree, overall and per market."""
    frames = frames.filter(pl.col("ticker").is_in(tops["ticker"].unique().implode()))
    joined = frames.sort("ts_ms").join_asof(
        tops.sort("t_ms"),
        left_on="ts_ms",
        right_on="t_ms",
        by="ticker",
        strategy="backward",
        check_sortedness=False,  # both sorted by time just above
    )
    known = joined.filter(pl.col("known").fill_null(False))
    agree = pl.col("yes_bid_e6").eq_missing(pl.col("bid_e6")) & pl.col("yes_ask_e6").eq_missing(
        pl.col("ask_e6")
    )
    sized = (  # and the sizes at the best prices, where a side stands
        agree
        & (pl.col("bid_e6").is_null() | (pl.col("yes_bid_size_e2") == pl.col("bid_e2")))
        & (pl.col("ask_e6").is_null() | (pl.col("yes_ask_size_e2") == pl.col("ask_e2")))
    )
    known = known.with_columns(agree=agree, sized=sized)
    per = (
        known.group_by("ticker")
        .agg(moments=pl.len(), agree=pl.col("agree").sum())
        .with_columns(share=pl.col("agree") / pl.col("moments"), changed=pl.col("ticker").is_in(
            pl.Series(sorted(changed), dtype=pl.String).implode()))
    )  # fmt: skip
    moments, agreeing = known.height, int(known["agree"].sum())
    share = agreeing / moments if moments else None
    markets_changed = int(per["changed"].sum())
    shares = per["share"]
    misses = known.filter(~pl.col("agree")).head(20)
    return {
        "frames": frames.height,
        "moments_unknown": joined.height - known.height,
        "moments": moments,
        "agree": agreeing,
        "share": share,
        "share_with_sizes": int(known["sized"].sum()) / moments if moments else None,
        "markets": per.height,
        "markets_changed": markets_changed,
        "per_market": {
            "min": shares.min() if per.height else None,
            "p05": shares.quantile(0.05) if per.height else None,
            "median": shares.median() if per.height else None,
            "under_99": int((shares < BAR_SHARE).sum()),
        },
        "passes": share is not None and share >= BAR_SHARE and markets_changed >= BAR_MARKETS,
        "misses": misses.select(
            "ticker", "ts_ms", "yes_bid_e6", "bid_e6", "yes_ask_e6", "ask_e6"
        ).to_dicts(),
    }


def compare_trades(
    recorded: pl.DataFrame,
    ingest: pl.DataFrame,
    first_ms: dict[str, int],
    downtimes: list[tuple[int, int]],
) -> dict[str, Any]:
    """The recorded trades against the ingest's, matched by ``trade_id``."""
    starts = pl.DataFrame(
        {"ticker": list(first_ms), "first_ms": list(first_ms.values())},
        schema={"ticker": pl.String, "first_ms": pl.Int64},
    )
    theirs = ingest.join(starts, on="ticker").filter(pl.col("ts_ms") >= pl.col("first_ms"))
    ours = recorded.unique("trade_id", keep="first").join(starts, on="ticker")
    ours = ours.filter(pl.col("ts_ms") >= pl.col("first_ms"))
    missing = theirs.join(ours.select("trade_id"), on="trade_id", how="anti")
    extra = ours.join(theirs.select("trade_id"), on="trade_id", how="anti")
    in_downtime = pl.lit(False)
    for start, end in downtimes:
        in_downtime = in_downtime | pl.col("ts_ms").is_between(
            start - DOWNTIME_MARGIN_MS, end + DOWNTIME_MARGIN_MS
        )
    lost = missing.filter(in_downtime).height
    return {
        "recorded": ours.height,
        "recorded_contracts_e2": int(ours["count_e2"].sum()),
        "ingest": theirs.height,
        "ingest_contracts_e2": int(theirs["count_e2"].sum()),
        "missing": missing.height,
        "missing_in_downtime": lost,
        "extra": extra.height,
        "agree": missing.height - lost == 0 and extra.height == 0,
    }


def check(out: Path, live: Path, start: datetime, end: datetime) -> dict[str, Any]:
    """B5's comparison over the recorded hours of ``[start, end)``."""
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    book = store.read_hours(out, "book", start, end).collect()
    tops = rebuild_tops(book)
    changed = set(book.filter(pl.col("kind") == CHANGE)["ticker"].unique().to_list())
    frames = pl.concat([frames_of(p) for p in hour_files(live, "ticker", start, end)])
    frames = frames.filter(pl.col("ts_ms").is_between(start_ms, end_ms, closed="left"))
    books = compare_books(tops, frames, changed)
    first = tops.filter(pl.col("known")).group_by("ticker").agg(pl.col("t_ms").min())
    first_ms = dict(zip(first["ticker"].to_list(), first["t_ms"].to_list(), strict=True))
    recorded = store.read_hours(out, "trade", start, end).collect()
    ingest = pl.read_parquet(hour_files(live, "trade", start, end))
    events = store.read_events(out / store.GAPS)
    downtimes = [
        (e["at_us"] // 1000 - int(e["downtime"] * 1000), e["at_us"] // 1000)
        for e in events
        if e["kind"] == "reconnect" and start_ms <= e["at_us"] // 1000 < end_ms + 60_000
    ]
    trades = compare_trades(
        recorded.filter(pl.col("ts_ms").is_between(start_ms, end_ms, closed="left")),
        ingest.filter(pl.col("ts_ms").is_between(start_ms, end_ms, closed="left")),
        first_ms,
        downtimes,
    )
    return {
        "hours": [start.isoformat(), end.isoformat()],
        "book_rows": book.height,
        "books": books,
        "trades": trades,
        "passes": books["passes"] and trades["agree"],
    }
