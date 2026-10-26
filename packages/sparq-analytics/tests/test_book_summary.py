"""S1.2a's summaries, the stop rule and the markdown (D11)."""

import dataclasses

import polars as pl
import pytest
from bookhelpers import BASE, busy, frames, markets, rule, trades
from sparq_analytics.live.book import (
    BookConfig,
    book_frames,
    burst_measures,
    tag_trades,
    trade_measures,
)
from sparq_analytics.live.summary import (
    estimate,
    in_any_population,
    render,
    stop_rule,
    summarize,
)

FAR = BASE + 10 * 86_400_000


def measures() -> tuple[pl.DataFrame, pl.DataFrame]:
    config = BookConfig()
    tagged = tag_trades(trades().lazy(), markets().lazy(), busy(), rule())
    book = book_frames(frames().lazy())
    per_trade = trade_measures(tagged, tagged, book, config, FAR).collect()
    return per_trade, burst_measures(per_trade.lazy(), book, config, FAR).collect()


def orders(refilled: list[bool], events: int = 3) -> pl.DataFrame:
    n = len(refilled)
    return pl.DataFrame(
        {
            "event_ticker": [f"E{i % events}" for i in range(n)],
            "cleared": [True] * n,
            "complete": [True] * n,
            "refill_1000": refilled,
        },
        schema={
            "event_ticker": pl.String,
            "cleared": pl.Boolean,
            "complete": pl.Boolean,
            "refill_1000": pl.Boolean,
        },
    )


def test_estimate_weights_by_contracts_over_events() -> None:
    frame = pl.DataFrame(
        {
            "event_ticker": ["A", "A", "B", None],
            "ticker": ["a", "a", "b", "c"],
            "value": [10.0, 40.0, None, 20.0],
            "count_e2": [300, 100, 100, 100],
        }
    )
    result = estimate(frame, "value", resamples=200, seed=1)
    assert result["events"] == 2  # B has no value; c has no event, so it is its own
    assert result["point_e6"] == pytest.approx((10 * 300 + 40 * 100 + 20 * 100) / 500)


def test_the_stop_rule_reads_the_share_refilled_within_a_second() -> None:
    config = dataclasses.replace(BookConfig(), stop_min_orders=4)
    assert stop_rule(orders([True, True, True]), config)["outcome"] == "too few"
    stop = stop_rule(orders([True, True, True, False]), config)
    assert (stop["outcome"], stop["share"], stop["orders"], stop["events"]) == ("stop", 0.75, 4, 3)
    assert stop_rule(orders([True, True, False, False]), config)["outcome"] == "go on"
    assert stop_rule(orders([]), config)["outcome"] == "too few"


def test_summarize_reads_each_population() -> None:
    per_trade, per_order = measures()
    summary = summarize(per_trade, per_order, rule(), BookConfig(), resamples=200)
    groups = summary["populations"]
    assert list(groups) == ["forward rule", "middle, 20-80c", "busy, 20-80c", "thin, 20-80c"]
    scored = groups["forward rule"]
    assert scored["trades"] == 7 and scored["events"] == 3
    # a1 to a4 and b1, b2 can join (c1 is behind a better price); a1, a2, a3 and b1 fill by the
    # queue rule, a1 and a2 by the strict one (both at 42c are gone through by a3 at 44c)
    assert scored["fills"]["joinable"] == 6
    assert scored["fills"]["queue_share"] == pytest.approx(4 / 6)
    assert scored["fills"]["strict_share"] == pytest.approx(2 / 6)
    assert scored["refills"]["cleared"] == 1 and scored["refills"]["within"]["2000"] == 1.0
    assert scored["refills"]["within"]["1000"] == 0.0
    assert summary["stop"]["outcome"] == "too few"
    assert groups["busy, 20-80c"]["trades"] == 2 and groups["thin, 20-80c"]["trades"] == 1
    assert summary["config"]["stop_within_ms"] == 1_000


def test_only_the_populations_rows_are_needed_for_the_summary() -> None:
    per_trade, per_order = measures()
    keep = in_any_population(rule())
    assert per_trade.filter(keep).height < per_trade.height  # some rows really are left out
    whole = summarize(per_trade, per_order, rule(), BookConfig(), resamples=200)
    kept = summarize(
        per_trade.filter(keep), per_order.filter(keep), rule(), BookConfig(), resamples=200
    )
    assert kept == whole


def test_the_summary_does_not_depend_on_the_order_of_the_rows() -> None:
    per_trade, per_order = measures()
    forward = summarize(per_trade, per_order, rule(), BookConfig(), resamples=200)
    backward = summarize(
        per_trade.reverse(), per_order.reverse(), rule(), BookConfig(), resamples=200
    )
    assert backward == forward


def test_render_names_the_stop_rule_and_every_population() -> None:
    per_trade, per_order = measures()
    summary = summarize(per_trade, per_order, rule(), BookConfig(), resamples=200)
    summary |= {"days": ["2026-10-25"], "data_end": "2026-10-26 00:10:00+00:00"}
    text = render(summary)
    assert "**Stop rule: too few**" in text and "within 1 s" in text
    for name in summary["populations"]:
        assert f"| {name} |" in text
    assert "n/a" in text  # the thin tier has no refill to report
