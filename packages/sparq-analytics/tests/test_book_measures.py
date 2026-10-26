"""S1.2a's measures on the hand-built scenarios in bookhelpers (answers worked out by hand)."""

import dataclasses
from datetime import UTC, datetime

import polars as pl
import pytest
from bookhelpers import BASE, busy, frames, markets, rule, trades
from sparq_analytics.live.book import (
    BookConfig,
    book_frames,
    burst_measures,
    check_rule,
    near_gaps,
    tag_trades,
    trade_measures,
)
from sparq_analytics.study.rules import add_rule_features

FAR = BASE + 10 * 86_400_000  # a data end far beyond every window


def measured(data_end: int = FAR, config: BookConfig | None = None) -> pl.DataFrame:
    config = config or BookConfig()
    tagged = tag_trades(trades().lazy(), markets().lazy(), busy(), rule())
    book = book_frames(frames().lazy())
    return trade_measures(tagged, tagged, book, config, data_end).collect()


def bursts(data_end: int = FAR) -> pl.DataFrame:
    config = BookConfig()
    book = book_frames(frames().lazy())
    return burst_measures(measured(data_end).lazy(), book, config, data_end).collect()


def row(frame: pl.DataFrame, ticker: str, t: int) -> dict[str, object]:
    found = frame.filter((pl.col("ticker") == ticker) & (pl.col("ts_ms") == BASE + t))
    assert found.height == 1
    return found.row(0, named=True)


def test_tags_tiers_quiet_time_and_the_rule() -> None:
    tagged = tag_trades(trades().lazy(), markets().lazy(), busy(), rule()).collect()
    tiers = dict(tagged.select("ticker", "tier").unique().iter_rows())
    assert tiers == {"M1": "middle", "M2": "middle", "M3": "middle", "M4": "busy", "M5": "thin",
                     "M6": "new", "M7": "busy", "M8": "busy"}  # fmt: skip
    a1 = row(tagged, "M1", 120_000)
    assert a1["quiet_s"] == 120.0 and not a1["first"] and a1["in_rule"]
    assert row(tagged, "M1", 0)["first"]
    # quiet 50 s is under the rule's 60 s; first trades and other tiers are never scored
    assert not row(tagged, "M2", 250_000)["in_rule"]
    scored = tagged.filter("in_rule").select("ticker", "trade_id").rows()
    assert sorted(scored) == [("M1", "a1"), ("M1", "a2"), ("M1", "a3"), ("M1", "a4"),
                              ("M2", "b1"), ("M2", "b2"), ("M3", "c1")]  # fmt: skip


def test_the_rule_features_are_the_studys() -> None:
    # the same trades in the study's shape, with a microsecond time built apart from the live code
    source = (
        trades()
        .join(markets().select("ticker", "series_ticker"), on="ticker", how="left")
        .sort("ticker", "ts_ms", "trade_id")
    )
    times = [datetime.fromtimestamp(ms / 1000, UTC) for ms in source["ts_ms"]]
    study = (
        add_rule_features(
            source.with_columns(t=pl.Series(times), p=pl.col("yes_price_e6")).lazy(), busy()
        )
        .with_columns(in_rule=rule().expr())
        .collect()
    )
    live = tag_trades(trades().lazy(), markets().lazy(), busy(), rule()).collect()
    columns = ["trade_id", "quiet_s", "first", "ny_hour", "in_rule"]
    assert live.select(columns).sort("trade_id").equals(study.select(columns).sort("trade_id"))


def test_trades_at_one_millisecond_are_tagged_the_same_in_any_input_order() -> None:
    # a second trade at a3's millisecond: by trade id a3 comes first and a3b follows it at once
    tie = trades().filter(pl.col("trade_id") == "a3").with_columns(trade_id=pl.lit("a3b"))
    both = pl.concat([trades(), tie])
    forward = tag_trades(both.lazy(), markets().lazy(), busy(), rule()).collect()
    backward = tag_trades(both.reverse().lazy(), markets().lazy(), busy(), rule()).collect()
    assert forward.equals(backward)
    assert row_of(forward, "a3")["quiet_s"] == 100.0 and row_of(forward, "a3")["in_rule"]
    assert row_of(forward, "a3b")["quiet_s"] == 0.0 and not row_of(forward, "a3b")["in_rule"]


def row_of(frame: pl.DataFrame, trade_id: str) -> dict[str, object]:
    found = frame.filter(pl.col("trade_id") == trade_id)
    assert found.height == 1
    return found.row(0, named=True)


def test_of_two_frames_at_one_millisecond_the_book_is_the_one_received_last() -> None:
    def frame(t: int, recv: int, ask_size: int) -> dict[str, object]:
        return {"ts_ms": BASE + t, "recv_ms": BASE + recv, "ticker": "M1", "price_e6": None,
                "yes_bid_e6": 400_000, "yes_ask_e6": 420_000, "yes_bid_size_e2": 1_000,
                "yes_ask_size_e2": ask_size, "volume_e2": None}  # fmt: skip

    trade = trades().filter(pl.col("trade_id") == "a1")  # yes at 42c, 120 s; joins at 121 s
    # the later arrival is listed first, so only the arrival time can put it last
    rows = [frame(0, 500, 500), frame(121_000, 121_200, 900), frame(121_000, 121_100, 300)]
    book = book_frames(pl.DataFrame(rows, schema=frames().schema).lazy())
    tagged = tag_trades(trade.lazy(), markets().lazy(), busy(), rule())
    out = trade_measures(tagged, tagged, book, BookConfig(), FAR).collect()
    assert out["q_ahead_e2"].to_list() == [900]


def test_the_book_the_taker_met_is_the_last_frame_strictly_before() -> None:
    a1 = row(measured(), "M1", 120_000)
    assert (a1["bid_b"], a1["ask_b"], a1["spread_b"], a1["touched_b"]) == (
        400_000,
        420_000,
        20_000,
        500,
    )
    assert a1["earned_e6"] == 10_000  # sold yes at 42c against a mid of 41c
    b1 = row(measured(), "M2", 100_000)
    assert b1["touched_b"] == 2_000 and b1["earned_e6"] == 10_000  # bought yes at 29c, mid 30c
    # a frame at the trade's own millisecond is the book after it, not the one the taker met
    a4 = row(measured(), "M1", 400_000)
    assert (a4["bid_b"], a4["ask_b"]) == (430_000, 450_000)
    g1 = row(measured(), "M7", 100_000)
    assert g1["touched_b"] == 100


def test_markouts_take_the_mid_at_each_horizon() -> None:
    a1 = row(measured(), "M1", 120_000)
    assert (a1["mk_5000"], a1["mk_60000"], a1["mk_600000"]) == (10_000, -20_000, -40_000)
    b1 = row(measured(), "M2", 100_000)
    assert (b1["mk_5000"], b1["mk_60000"], b1["mk_600000"]) == (10_000, 10_000, 10_000)


def test_a_markout_past_the_end_of_the_data_is_empty() -> None:
    a1 = row(measured(data_end=BASE + 200_000), "M1", 120_000)
    assert a1["mk_5000"] == 10_000 and a1["mk_60000"] == -20_000
    assert a1["mk_600000"] is None
    assert not a1["joinable"]  # its fill window passes the end too


def test_strict_and_queue_fills() -> None:
    frame = measured()
    a1 = row(frame, "M1", 120_000)  # the ask was cleared: nothing ahead, then a trade through it
    assert a1["joinable"] and a1["q_ahead_e2"] == 0
    assert a1["filled_strict"] and a1["filled_queue"]
    b1 = row(frame, "M2", 100_000)  # 15 contracts ahead; 10 then 6 trade at the price
    assert b1["joinable"] and b1["q_ahead_e2"] == 1_500
    assert b1["filled_queue"] and not b1["filled_strict"]
    b2 = row(frame, "M2", 200_000)  # joins behind 15 again; only 6 more trade
    assert not b2["filled_queue"] and not b2["filled_strict"]
    a3 = row(frame, "M1", 300_000)  # a later trade at its own price, none through it
    assert a3["filled_queue"] and not a3["filled_strict"]
    # joins behind 3 contracts 1 s after the trade; the half contract traded before it joined
    # does not count, and the 2.8 after it do not pass the queue
    g1 = row(frame, "M7", 100_000)
    assert g1["q_ahead_e2"] == 300 and not g1["filled_queue"]


def test_an_order_behind_a_better_price_is_not_placed() -> None:
    c1 = row(measured(), "M3", 130_000)
    assert not c1["joinable"] and not c1["filled_queue"] and not c1["filled_strict"]


def test_a_longer_join_delay_sees_a_later_book() -> None:
    config = dataclasses.replace(BookConfig(), join_delay_ms=2_000)
    a1 = row(measured(config=config), "M1", 120_000)
    assert a1["q_ahead_e2"] == 200  # the refilled ask of 2 contracts at 42c is ahead now


def test_refills_after_a_cleared_level() -> None:
    frame = bursts()
    a1 = row(frame, "M1", 120_000)
    assert a1["consistent"] and a1["cleared"] and a1["complete"]
    assert [a1[f"refill_{w}"] for w in (500, 1_000, 2_000, 5_000)] == [False, False, True, True]
    b1 = row(frame, "M2", 100_000)  # took 5 of 20: not cleared, so no refill is measured
    assert b1["consistent"] and not b1["cleared"] and b1["refill_1000"] is None
    c1 = row(frame, "M3", 130_000)  # the frame before showed 51c, not the 50c it traded at
    assert not c1["consistent"] and not c1["cleared"]
    # a frame at the trade's own millisecond showing the old price is not a refill
    g1 = row(frame, "M7", 100_000)
    assert g1["cleared"] and not g1["refill_500"] and not g1["refill_600000"]


def test_a_taker_order_of_several_trades_is_one_order() -> None:
    extra = pl.DataFrame(
        [
            {
                "ts_ms": BASE + 120_000,
                "ticker": "M1",
                "trade_id": "a1b",
                "yes_price_e6": 430_000,
                "count_e2": 100,
                "taker_side": "yes",
                "is_block_trade": False,
            },
        ],  # fmt: skip
        schema=trades().schema,
    )
    tagged = tag_trades(pl.concat([trades(), extra]).lazy(), markets().lazy(), busy(), rule())
    book = book_frames(frames().lazy())
    config = BookConfig()
    per_trade = trade_measures(tagged, tagged, book, config, FAR)
    order = row(burst_measures(per_trade, book, config, FAR).collect(), "M1", 120_000)
    assert order["p"] == 420_000 and order["count_e2"] == 600 and order["cleared"]


def test_an_order_near_the_end_of_the_data_is_not_complete() -> None:
    a1 = row(bursts(data_end=BASE + 300_000), "M1", 120_000)
    assert a1["cleared"] and not a1["complete"] and a1["refill_2000"] is None


def test_trades_near_an_outage_of_the_feed() -> None:
    config = BookConfig()
    outage = pl.DataFrame(
        {"kind": ["ticker"], "start_ms": [1_000_000], "end_ms": [1_005_000]},
        schema={"kind": pl.String, "start_ms": pl.Int64, "end_ms": pl.Int64},
    )
    times = pl.DataFrame({"ts_ms": [399_999, 400_000, 1_002_000, 1_065_000, 1_065_001]})
    near = times.select(near_gaps(outage, config, 10**9)).to_series().to_list()
    # from ten minutes before it starts to a minute after it ends, both ends included
    assert near == [False, True, True, True, False]
    still_open = outage.with_columns(pl.lit(None, dtype=pl.Int64).alias("end_ms"))
    later = pl.DataFrame({"ts_ms": [5_000_000]})
    assert later.select(near_gaps(still_open, config, 5_000_000)).item()
    assert not times.select(near_gaps(outage.clear(), config, 10**9)).to_series().any()


def test_a_side_without_size_is_empty() -> None:
    raw = pl.DataFrame(
        {
            "ts_ms": [1, 2],
            "recv_ms": [500, 501],
            "ticker": ["X", "X"],
            "yes_bid_e6": [300_000, 0],
            "yes_ask_e6": [1_000_000, 500_000],
            "yes_bid_size_e2": [0, 100],
            "yes_ask_size_e2": [100, 0],
        },
    )
    books = book_frames(raw.lazy()).collect()
    for book in books.iter_rows(named=True):  # no size, a zero bid or a 100c ask: all empty
        assert book["bid"] is None and book["ask"] is None and book["mid"] is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"markouts_ms": (5_000, 700_000)}, "ten minutes"),
        ({"fill_horizon_ms": 600_001}, "ten minutes"),
        ({"refills_ms": (0, 1_000)}, "positive"),
        ({"join_delay_ms": 600_000}, "join delay"),
        ({"stop_within_ms": 3_000}, "refill windows"),
        ({"stop_share": 1.0}, "between"),
        ({"stop_min_orders": 0}, "at least one"),
    ],
)
def test_settings_are_checked(change: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        dataclasses.replace(BookConfig(), **change)  # type: ignore[arg-type]


def test_the_rule_must_score_makers_within_an_activity_range() -> None:
    assert check_rule(rule()) == (1_000.0, 100_000.0)
    with pytest.raises(ValueError, match="maker"):
        check_rule(dataclasses.replace(rule(), side="taker"))
    with pytest.raises(ValueError, match="both sides"):
        check_rule(dataclasses.replace(rule(), busy_hi=None))
