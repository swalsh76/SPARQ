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
snapshot in the hours checked to its removal) and hours, matched by ``trade_id``: in count and in
contracts. A trade the recording lacks inside a lost link's downtime (from the last frame before
the loss to the reconnect) is counted apart: Kalshi replays nothing.

The books are rebuilt and compared an hour at a time, the books carried from one hour to the next,
so a day's check never holds more than an hour of rows.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from sparq_analytics.books import store
from sparq_analytics.books.keeper import Keeper, Top
from sparq_analytics.books.store import CHANGE, LEVEL, RESET, SIDE_NAME, SNAPSHOT
from sparq_analytics.live.source import hour_files, hour_of
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
MISS_SCHEMA = {"ticker": pl.String, "ts_ms": pl.Int64, "yes_bid_e6": pl.Int64, "bid_e6": pl.Int64,
               "yes_ask_e6": pl.Int64, "ask_e6": pl.Int64}  # fmt: skip


class Tops:
    """Each market's top of book after every book row that moved it, in the rows' order, fed an
    hour at a time with the books carried across. The times mix Kalshi's (a snapshot's sending
    time, a change's time) and the recorder's (a ``RESET``), so a market's times are kept from
    going back: the rows' order decides. It also keeps what the trades check needs: each market's
    first known top, its last own reset or snapshot, and the markets whose book changed."""

    def __init__(self) -> None:
        self.keeper = Keeper()
        self.last: dict[str, Top | None] = {}
        self.latest: dict[str, int] = {}  # each market's newest time
        self.first_ms: dict[str, int] = {}
        self.own_last: dict[str, tuple[int, int]] = {}  # a market's last reset or snapshot, when
        self.changed: set[str] = set()

    def standing(self) -> pl.DataFrame:
        """Every market's top as it stands now, at its newest time (the next hour's start)."""
        rows = [self._row(t, self.latest[t], top) for t, top in self.last.items()]
        return pl.DataFrame(rows, schema=TOP_SCHEMA, orient="row")

    @staticmethod
    def _row(ticker: str, t_ms: int, top: Top | None) -> tuple[Any, ...]:
        if top is None:
            return (ticker, t_ms, False, None, None, None, None)
        return (ticker, t_ms, True, top.bid_e6, top.ask_e6, top.bid_e2, top.ask_e2)

    def feed(self, book: pl.DataFrame) -> pl.DataFrame:
        """The tops that ``book``'s rows make, in order."""
        keeper, rows = self.keeper, []

        def emit(ticker: str, t_ms: int) -> None:
            top = keeper.top(ticker)
            if ticker in self.last and self.last[ticker] == top:
                return
            self.last[ticker] = top
            t_ms = self.latest[ticker] = max(t_ms, self.latest.get(ticker, t_ms))
            if top is not None:
                self.first_ms.setdefault(ticker, t_ms)
            rows.append(self._row(ticker, t_ms, top))

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
                if ticker is not None:
                    self.own_last[ticker] = (RESET, recv_ms)
                for market in gone:
                    emit(market, recv_ms)
            elif kind == SNAPSHOT:
                keeper.replace(ticker, (), ())
                self.own_last[ticker] = (SNAPSHOT, recv_ms)
                filling = (ticker, send_ms if send_ms is not None else recv_ms)
            elif kind == LEVEL:
                keeper.books[ticker].set_level(SIDE_NAME[side], price, size)
            elif kind == CHANGE:
                self.changed.add(ticker)
                if keeper.apply(ticker, SIDE_NAME[side], price, size) is not None:
                    emit(ticker, ts_ms if ts_ms is not None else recv_ms)
        if filling is not None:
            emit(*filling)
        return pl.DataFrame(rows, schema=TOP_SCHEMA, orient="row")

    def spans(self) -> pl.DataFrame:
        """Each market's recorded span: from its first known top to the reset of its removal (a
        market's own reset not followed by a snapshot), or open (null) while it is recorded."""
        rows = [
            (t, first, end if kind == RESET else None)
            for t, first in self.first_ms.items()
            for kind, end in [self.own_last.get(t, (SNAPSHOT, 0))]
        ]
        return pl.DataFrame(
            rows, schema={"ticker": pl.String, "first_ms": pl.Int64, "last_ms": pl.Int64},
            orient="row",
        )  # fmt: skip


def rebuild_tops(book: pl.DataFrame) -> pl.DataFrame:
    """Each market's top of book after every row of ``book`` that moved it (one chunk)."""
    return Tops().feed(book)


def market_spans(book: pl.DataFrame) -> pl.DataFrame:
    """Each market's recorded span in ``book`` (one chunk; see ``Tops.spans``)."""
    tops = Tops()
    tops.feed(book)
    return tops.spans()


def moments(tops: pl.DataFrame, frames: pl.DataFrame) -> tuple[pl.DataFrame, int, int]:
    """The frames of the markets in ``tops`` met with the top standing at their time: the known
    moments (``agree`` and ``sized`` added), the frames read, and the moments left unknown."""
    frames = frames.filter(pl.col("ticker").is_in(tops["ticker"].unique().implode()))
    # stable sorts: tops of one millisecond (the changes of one match) keep their order, so the
    # last of them is the one a frame of that millisecond meets
    joined = frames.sort("ts_ms", maintain_order=True).join_asof(
        tops.sort("t_ms", maintain_order=True),
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
    return known.with_columns(agree=agree, sized=sized), frames.height, joined.height - known.height


MISSES = 20  # disagreements kept to look at
PER_MARKET = {"moments": pl.Int64, "agree": pl.Int64, "sized": pl.Int64}


def per_market(known: pl.DataFrame) -> pl.DataFrame:
    return known.group_by("ticker").agg(
        moments=pl.len().cast(pl.Int64),
        agree=pl.col("agree").sum().cast(pl.Int64),
        sized=pl.col("sized").sum().cast(pl.Int64),
    )


def summarize_books(
    per: pl.DataFrame, frames: int, unknown: int, misses: pl.DataFrame, changed: set[str]
) -> dict[str, Any]:
    """B5's figures from the moments counted per market."""
    per = (
        per.group_by("ticker")
        .agg(pl.col(list(PER_MARKET)).sum())
        .with_columns(
            share=pl.col("agree") / pl.col("moments"),
            changed=pl.col("ticker").is_in(pl.Series(sorted(changed), dtype=pl.String).implode()),
        )
    )
    counted, agreeing = int(per["moments"].sum()), int(per["agree"].sum())
    share = agreeing / counted if counted else None
    markets_changed = int(per["changed"].sum())
    shares = per["share"]
    return {
        "frames": frames,
        "moments_unknown": unknown,
        "moments": counted,
        "agree": agreeing,
        "share": share,
        "share_with_sizes": int(per["sized"].sum()) / counted if counted else None,
        "markets": per.height,
        "markets_changed": markets_changed,
        "per_market": {
            "min": shares.min() if per.height else None,
            "p05": shares.quantile(0.05) if per.height else None,
            "median": shares.median() if per.height else None,
            "under_99": int((shares < BAR_SHARE).sum()),
        },
        "passes": share is not None and share >= BAR_SHARE and markets_changed >= BAR_MARKETS,
        "misses": misses.head(MISSES)
        .select("ticker", "ts_ms", "yes_bid_e6", "bid_e6", "yes_ask_e6", "ask_e6")
        .to_dicts(),
    }


def compare_books(tops: pl.DataFrame, frames: pl.DataFrame, changed: set[str]) -> dict[str, Any]:
    """The moments compared and how many agree, overall and per market (one chunk)."""
    known, read, unknown = moments(tops, frames)
    return summarize_books(per_market(known), read, unknown, known.filter(~pl.col("agree")),
                           changed)  # fmt: skip


def compare_trades(
    recorded: pl.DataFrame,
    ingest: pl.DataFrame,
    spans: pl.DataFrame,
    downtimes: list[tuple[int, int]],
) -> dict[str, Any]:
    """The recorded trades against the ingest's, matched by ``trade_id``, each market's from its
    first snapshot to its removal (``spans``: ``ticker``, ``first_ms``, ``last_ms``, the last null
    while it is still recorded)."""

    def inside(trades: pl.DataFrame) -> pl.DataFrame:
        within = (pl.col("ts_ms") >= pl.col("first_ms")) & (
            pl.col("last_ms").is_null() | (pl.col("ts_ms") < pl.col("last_ms"))
        )
        return trades.join(spans, on="ticker").filter(within)

    theirs = inside(ingest)
    ours = inside(recorded.unique("trade_id", keep="first"))
    missing = theirs.join(ours.select("trade_id"), on="trade_id", how="anti")
    extra = ours.join(theirs.select("trade_id"), on="trade_id", how="anti")
    sized = ours.select("trade_id", "count_e2").join(
        theirs.select("trade_id", theirs_e2="count_e2"), on="trade_id"
    )
    wrong_size = sized.filter(pl.col("count_e2") != pl.col("theirs_e2")).height
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
        "wrong_size": wrong_size,
        "agree": missing.height - lost == 0 and extra.height == 0 and wrong_size == 0,
    }


def check(out: Path, live: Path, start: datetime, end: datetime) -> dict[str, Any]:
    """B5's comparison over the recorded hours of ``[start, end)``, an hour at a time."""
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    tops = Tops()
    pers: list[pl.DataFrame] = []
    misses: list[pl.DataFrame] = []
    read = unknown = rows = 0
    for frame_path in hour_files(live, "ticker", start, end):
        hour = hour_of(frame_path.stem)
        book = store.read_hours(out, "book", hour, hour + timedelta(hours=1)).collect()
        rows += book.height
        standing = tops.standing()  # the books as the hour begins
        hour_tops = pl.concat([standing, tops.feed(book)])
        known, frames, left = moments(hour_tops, frames_of(frame_path).filter(
            pl.col("ts_ms").is_between(start_ms, end_ms, closed="left")))  # fmt: skip
        pers.append(per_market(known))
        if sum(m.height for m in misses) < MISSES:
            misses.append(known.filter(~pl.col("agree")).head(MISSES))
        read, unknown = read + frames, unknown + left
    per = pl.concat(pers) if pers else pl.DataFrame(schema={"ticker": pl.String, **PER_MARKET})
    missed = pl.concat(misses) if misses else pl.DataFrame(schema=MISS_SCHEMA)
    books = summarize_books(per, read, unknown, missed, tops.changed)
    spans = tops.spans()
    # a trade made just before the end may be received just after it, in the next hour's file
    recorded = store.read_hours(out, "trade", start, end + timedelta(hours=1)).collect()
    ingest = pl.read_parquet(hour_files(live, "trade", start, end))
    events = store.read_events(out / store.GAPS)
    downtimes = [  # from the last frame before the loss (or the downtime the feed measured)
        (e.get("lost_at_us", e["at_us"] - int(e["downtime"] * 1e6)) // 1000, e["at_us"] // 1000)
        for e in events
        if e["kind"] == "reconnect" and start_ms <= e["at_us"] // 1000 < end_ms + 60_000
    ]
    trades = compare_trades(
        recorded.filter(pl.col("ts_ms").is_between(start_ms, end_ms, closed="left")),
        ingest.filter(pl.col("ts_ms").is_between(start_ms, end_ms, closed="left")),
        spans,
        downtimes,
    )
    return {
        "hours": [start.isoformat(), end.isoformat()],
        "book_rows": rows,
        "books": books,
        "trades": trades,
        "passes": books["passes"] and trades["agree"],
    }
