"""S1.2a's measures from the ingest's top of book and trades (D11).

The ticker feed gives the best bid and ask with their sizes about once a second per market, and a
frame follows a trade within about 150 ms (measured 2026-10-25), so these measures resolve to about
a second. Prices are yes prices in millionths of a dollar, sizes hundredths of a contract, times
milliseconds since the epoch. A maker's result at a mark ``x`` is ``m * (p - x)`` a contract, where
``m`` is +1 when the taker bought yes (the maker sold yes at ``p``) and -1 when the taker sold yes.

Four measures, per trade or per taker order:

* **The book the taker met:** the last frame strictly before the trade; its spread, the size shown
  at the level the trade touched, and the half-spread the maker earned, ``m * (p - mid)``.
* **Markouts:** ``m * (p - mid)`` with the mid at fixed times after the trade.
* **Fills:** a maker order joins at ``p`` on the maker's side ``join_delay_ms`` after the trade.
  S1.1's strict rule fills it only when a later trade goes through ``p``; the queue rule also fills
  it when the contracts traded at ``p`` exceed the size shown at ``p`` when it joined. Cancellations
  ahead of it are not credited, so the queue rule is itself cautious. An order that would join
  behind a better price is not placed.
* **Refills:** after a taker order clears the level it hit (it traded beyond it, or took at least
  the size shown there), how soon a frame shows that side at the same price or better again.

Trades near an outage of the ticker feed are left out and counted (``near_gaps``).

Nothing here looks more than ten minutes past a trade and nothing reads a settlement result: until
S1.1b's verdict D11 allows book measures and markouts of ten minutes or less only.
"""

from dataclasses import dataclass

import polars as pl

from sparq_analytics.study.rules import Rule, add_trade_features

GUARD_MS = 600_000  # D11: ten minutes
BOOK_SETTLE_MS = 60_000  # after an outage, a minute for every market's book to be shown again
ONE_E6 = 1_000_000
NO_ASK = 2 * ONE_E6  # an empty ask, above every price
NO_BID = -1  # an empty bid, below every price
SIDES = ("bid", "bid_size", "ask", "ask_size")


@dataclass(frozen=True, slots=True)
class BookConfig:
    """The windows of the measures and the stop rule (the share of cleared levels refilled within
    ``stop_within_ms`` above which bots are taken to be requoting already, read once there are
    ``stop_min_orders`` cleared levels)."""

    join_delay_ms: int = 1_000
    fill_horizon_ms: int = 600_000
    markouts_ms: tuple[int, ...] = (5_000, 60_000, 600_000)
    refills_ms: tuple[int, ...] = (500, 1_000, 2_000, 5_000, 10_000, 60_000, 600_000)
    stop_within_ms: int = 1_000
    stop_share: float = 0.5
    stop_min_orders: int = 300

    def __post_init__(self) -> None:
        windows = (self.fill_horizon_ms, *self.markouts_ms, *self.refills_ms)
        if min(windows) <= 0:
            raise ValueError("every window must be positive")
        if max(windows) > GUARD_MS:
            raise ValueError("D11 allows windows of ten minutes or less before S1.1b's verdict")
        if not 0 <= self.join_delay_ms < self.fill_horizon_ms:
            raise ValueError("the join delay must be shorter than the fill horizon")
        if self.stop_within_ms not in self.refills_ms:
            raise ValueError("the stop rule's window must be one of the refill windows")
        if not 0.0 < self.stop_share < 1.0:
            raise ValueError("the stop share is between 0 and 1")
        if self.stop_min_orders < 1:
            raise ValueError("the stop rule needs at least one order")

    @property
    def lookahead_ms(self) -> int:
        return max(self.fill_horizon_ms, *self.markouts_ms, *self.refills_ms)


def check_rule(rule: Rule) -> tuple[float, float]:
    """The rule's activity range, which also names the tiers; the rule must score makers."""
    if rule.side != "maker":
        raise ValueError("S1.2a measures a maker rule")
    if rule.busy_lo is None or rule.busy_hi is None:
        raise ValueError("the rule must bound the series' activity on both sides")
    return rule.busy_lo, rule.busy_hi


def tag_trades(
    trades: pl.LazyFrame, markets: pl.LazyFrame, busy: pl.DataFrame, rule: Rule
) -> pl.LazyFrame:
    """Each trade with its series' activity and tier, the seconds since the market's previous
    trade, and whether the rule would score it (``in_rule``).

    A market's first trade in the frame counts as ``first``, so a trade whose previous one is
    outside the frame is never scored; give the frame an hour before the trades that matter
    (the rule's quiet range ends below an hour).
    """
    lo, hi = check_rule(rule)
    tier = (
        pl.when(pl.col("busy").is_null())
        .then(pl.lit("new"))
        .when(pl.col("busy") < lo)
        .then(pl.lit("thin"))
        .when(pl.col("busy") < hi)
        .then(pl.lit("middle"))
        .otherwise(pl.lit("busy"))
    )
    joined = (
        trades.join(
            markets.select("ticker", "series_ticker", "event_ticker", "category"),
            on="ticker",
            how="left",
        )
        .join(busy.lazy(), on="series_ticker", how="left")
        .sort("ticker", "ts_ms", "trade_id")  # trades at the same millisecond in a fixed order
        .with_columns(t=pl.from_epoch("ts_ms", time_unit="ms").dt.replace_time_zone("UTC"))
    )
    # the rule's features exactly as the study defined them when the rule was chosen
    return (
        add_trade_features(joined)
        .with_columns(
            p=pl.col("yes_price_e6"),
            taker_yes=pl.col("taker_side") == "yes",
            tier=tier,
        )
        .with_columns(in_rule=rule.expr())
        .drop("t")
    )


def near_gaps(gaps: pl.DataFrame, config: BookConfig, data_end_ms: int) -> pl.Expr:
    """True for a trade whose windows reach into an outage of the ticker feed, or that comes in
    the minute after one (its book would be stale). Trades are backfilled after an outage but
    frames are lost, so such a trade would read a book that is not there: a refill would be
    missed, which leans the stop rule toward going on. An outage with no end yet runs to the end
    of the data."""
    near = pl.lit(False)
    for start, end in gaps.select("start_ms", "end_ms").iter_rows():
        last = data_end_ms if end is None else end
        near |= pl.col("ts_ms").is_between(start - config.lookahead_ms, last + BOOK_SETTLE_MS)
    return near


def book_frames(frames: pl.LazyFrame) -> pl.LazyFrame:
    """Frames as the best bid and ask with their sizes; a side showing no size is empty."""
    bid = (pl.col("yes_bid_size_e2").fill_null(0) > 0) & (pl.col("yes_bid_e6").fill_null(0) > 0)
    ask = (pl.col("yes_ask_size_e2").fill_null(0) > 0) & (
        pl.col("yes_ask_e6").fill_null(ONE_E6) < ONE_E6
    )
    book = frames.select(
        "ticker",
        "ts_ms",
        bid=pl.when(bid).then(pl.col("yes_bid_e6")),
        bid_size=pl.when(bid).then(pl.col("yes_bid_size_e2")),
        ask=pl.when(ask).then(pl.col("yes_ask_e6")),
        ask_size=pl.when(ask).then(pl.col("yes_ask_size_e2")),
    )
    return book.with_columns(mid=_mid("bid", "ask")).sort("ts_ms")


def _mid(bid: str, ask: str) -> pl.Expr:
    return pl.when(pl.col(bid) < pl.col(ask)).then((pl.col(bid) + pl.col(ask)) / 2.0)


def _asof(
    left: pl.LazyFrame,
    book: pl.LazyFrame,
    key: str,
    columns: tuple[str, ...],
    suffix: str,
    *,
    exact: bool,
) -> pl.LazyFrame:
    """The book's ``columns`` at each row's ``key`` time (the last frame before it, or at it when
    ``exact``), as ``<column>_<suffix>``."""
    right = book.select(
        "ticker", pl.col("ts_ms").alias(key), *[pl.col(c).alias(f"{c}_{suffix}") for c in columns]
    )
    return left.sort(key).join_asof(
        right,
        on=key,
        by="ticker",
        strategy="backward",
        allow_exact_matches=exact,
        check_sortedness=False,
    )


def _side(yes: str, no: str) -> pl.Expr:
    """The column for the side the trade touched: the ask when the taker bought yes."""
    return pl.when(pl.col("taker_yes")).then(pl.col(yes)).otherwise(pl.col(no))


def _forward_fills(
    evaluated: pl.LazyFrame, trades: pl.LazyFrame, config: BookConfig
) -> pl.LazyFrame:
    """For each evaluated row (by ``row``), over the trades in ``(join, join + width]``: whether one
    went through ``p`` on the same side, and the contracts traded at exactly ``p`` on that side.

    The windows run forward, so time is reversed and each row looks back over ``[r - width, r)``.
    """
    width = f"{config.fill_horizon_ms - config.join_delay_ms}i"
    real = trades.select(
        "ticker",
        "ts_ms",
        "taker_yes",
        "p",
        "count_e2",
        probe=pl.lit(False),
        row=pl.lit(None, dtype=pl.Int64),
    )
    probes = evaluated.select(
        "ticker",
        pl.col("ts_ms") + config.join_delay_ms,
        "taker_yes",
        "p",
        count_e2=pl.lit(0, dtype=pl.Int64),
        probe=pl.lit(True),
        row=pl.col("row").cast(pl.Int64),
    )
    both = (
        pl.concat([real, probes])
        .with_columns(
            r=-pl.col("ts_ms"),
            yes_px=pl.when(~pl.col("probe") & pl.col("taker_yes")).then(pl.col("p")).otherwise(-1),
            no_px=pl.when(~pl.col("probe") & ~pl.col("taker_yes"))
            .then(pl.col("p"))
            .otherwise(NO_ASK),
        )
        .sort("ticker", "r")
    )
    ahead = both.with_columns(
        fwd_yes=pl.col("yes_px").rolling_max_by("r", width, closed="left").over("ticker"),
        fwd_no=pl.col("no_px").rolling_min_by("r", width, closed="left").over("ticker"),
        fwd_at=pl.col("count_e2")
        .rolling_sum_by("r", width, closed="left")
        .over("ticker", "taker_yes", "p"),
    )
    return ahead.filter("probe").select(
        "row",
        through=pl.when(pl.col("taker_yes"))
        .then(pl.col("fwd_yes").fill_null(-1) > pl.col("p"))
        .otherwise(pl.col("fwd_no").fill_null(NO_ASK) < pl.col("p")),
        at_price_e2=pl.col("fwd_at").fill_null(0),
    )


TRADE_COLUMNS = (
    "ticker",
    "event_ticker",
    "category",
    "ts_ms",
    "taker_yes",
    "p",
    "count_e2",
    "tier",
    "in_rule",
    "bid_b",
    "ask_b",
    "bid_size_b",
    "ask_size_b",
    "spread_b",
    "touched_b",
    "earned_e6",
    "joinable",
    "q_ahead_e2",
    "filled_strict",
    "filled_queue",
)


def trade_measures(
    evaluated: pl.LazyFrame,
    trades: pl.LazyFrame,
    book: pl.LazyFrame,
    config: BookConfig,
    data_end_ms: int,
) -> pl.LazyFrame:
    """Per evaluated trade: the book it met, its markouts and the two fill rules.

    ``trades`` are all tagged trades the windows may reach (the evaluated ones and those after
    them); a measure whose window passes ``data_end_ms`` is left empty (null).
    """
    m = pl.when(pl.col("taker_yes")).then(1).otherwise(-1)
    out = evaluated.with_row_index("row").with_columns(at_b=pl.col("ts_ms"))
    out = _asof(out, book, "at_b", SIDES, "b", exact=False)
    out = out.with_columns(at_j=pl.col("ts_ms") + config.join_delay_ms)
    out = _asof(out, book, "at_j", SIDES, "j", exact=True)
    for h in config.markouts_ms:
        out = out.with_columns((pl.col("ts_ms") + h).alias(f"at_{h}"))
        out = _asof(out, book, f"at_{h}", ("mid",), str(h), exact=True)
    level_j, size_j = _side("ask_j", "bid_j"), _side("ask_size_j", "bid_size_j")
    better = (
        pl.when(pl.col("taker_yes")).then(level_j < pl.col("p")).otherwise(level_j > pl.col("p"))
    )
    in_data = pl.col("ts_ms") + config.fill_horizon_ms <= data_end_ms
    out = out.with_columns(
        [
            pl.when(pl.col("ts_ms") + h <= data_end_ms)
            .then(m * (pl.col("p") - pl.col(f"mid_{h}")))
            .alias(f"mk_{h}")
            for h in config.markouts_ms
        ],
        spread_b=pl.col("ask_b") - pl.col("bid_b"),
        touched_b=_side("ask_size_b", "bid_size_b"),
        earned_e6=m * (pl.col("p") - _mid("bid_b", "ask_b")),
        joinable=in_data & ~better.fill_null(False),
        q_ahead_e2=pl.when(level_j == pl.col("p")).then(size_j).otherwise(0),
    )
    fills = _forward_fills(out.filter("joinable"), trades, config)
    out = out.join(fills, on="row", how="left").with_columns(
        filled_strict=pl.col("joinable") & pl.col("through").fill_null(False),
        filled_queue=pl.col("joinable")
        & (
            pl.col("through").fill_null(False)
            | (pl.col("at_price_e2").fill_null(0) > pl.col("q_ahead_e2"))
        ),
    )
    markouts = [f"mk_{h}" for h in config.markouts_ms]
    return out.select(*TRADE_COLUMNS, *markouts).sort("ticker", "ts_ms")


def burst_measures(
    measured: pl.LazyFrame, book: pl.LazyFrame, config: BookConfig, data_end_ms: int
) -> pl.LazyFrame:
    """Per taker order (the trades of one market, millisecond and side): whether it cleared the
    level it hit, and whether that side was refilled at the same price or better within each
    refill window. ``consistent`` is false when the last frame before it did not show the price
    it first traded at (a stale frame); such orders are counted and left out."""
    p = pl.col("p")
    bursts = (
        measured.with_columns(level=_side("ask_b", "bid_b"), size=_side("ask_size_b", "bid_size_b"))
        .group_by("ticker", "ts_ms", "taker_yes")
        .agg(
            pl.col("event_ticker").first(),
            pl.col("category").first(),
            pl.col("tier").first(),
            pl.col("in_rule").any(),
            pl.col("level").first(),
            pl.col("size").first(),
            pmin=p.min(),
            pmax=p.max(),
            at_min=pl.col("count_e2").filter(p == p.min()).sum(),
            at_max=pl.col("count_e2").filter(p == p.max()).sum(),
            count_e2=pl.col("count_e2").sum(),
        )
        .with_columns(
            p=_side("pmin", "pmax"),
            worst=_side("pmax", "pmin"),
            at_p=_side("at_min", "at_max"),
        )
        .with_columns(consistent=(pl.col("level") == pl.col("p")).fill_null(False))
        .with_columns(
            cleared=pl.col("consistent")
            & ((pl.col("worst") != pl.col("p")) | (pl.col("at_p") >= pl.col("size"))),
            complete=pl.col("ts_ms") + max(config.refills_ms) <= data_end_ms,
        )
        .sort("ticker", "ts_ms", "taker_yes")
        .with_row_index("row")
    )
    asked = bursts.filter(pl.col("cleared") & pl.col("complete"))
    probes = asked.select(
        "ticker",
        "ts_ms",
        ask=pl.lit(NO_ASK, dtype=pl.Int64),
        bid=pl.lit(NO_BID, dtype=pl.Int64),
        probe=pl.lit(True),
        row=pl.col("row").cast(pl.Int64),
    )
    frames = book.join(asked.select("ticker").unique(), on="ticker", how="semi").select(
        "ticker",
        "ts_ms",
        ask=pl.col("ask").fill_null(NO_ASK),
        bid=pl.col("bid").fill_null(NO_BID),
        probe=pl.lit(False),
        row=pl.lit(None, dtype=pl.Int64),
    )
    both = pl.concat([frames, probes]).with_columns(r=-pl.col("ts_ms")).sort("ticker", "r")
    windows = []
    for w in config.refills_ms:
        windows += [
            pl.col("ask").rolling_min_by("r", f"{w}i", closed="left").over("ticker").alias(f"a{w}"),
            pl.col("bid").rolling_max_by("r", f"{w}i", closed="left").over("ticker").alias(f"b{w}"),
        ]
    seen = (
        both.with_columns(windows)
        .filter("probe")
        .select("row", *[c for w in config.refills_ms for c in (f"a{w}", f"b{w}")])
    )
    joined = bursts.join(seen, on="row", how="left")
    refilled = [
        pl.when(pl.col("cleared") & pl.col("complete"))
        .then(
            pl.when(pl.col("taker_yes"))
            .then(pl.col(f"a{w}").fill_null(NO_ASK) <= pl.col("p"))
            .otherwise(pl.col(f"b{w}").fill_null(NO_BID) >= pl.col("p"))
        )
        .alias(f"refill_{w}")
        for w in config.refills_ms
    ]
    return joined.with_columns(refilled).select(
        "ticker",
        "event_ticker",
        "category",
        "ts_ms",
        "taker_yes",
        "p",
        "count_e2",
        "tier",
        "in_rule",
        "consistent",
        "cleared",
        "complete",
        *[f"refill_{w}" for w in config.refills_ms],
    )
