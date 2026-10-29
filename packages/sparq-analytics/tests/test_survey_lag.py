from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from laghelpers import ACTIVE, FEES, GAME, _f, lt, write_lag_world
from sparq_analytics.live.source import TICKER_SCHEMA, hour_name
from sparq_analytics.survey import lag
from sparq_analytics.survey.dump import SurveyDataError
from sparq_analytics.survey.relations import pairs

G = f"KXG-{GAME}"
S = f"KXS-{GAME}"
HOUR0 = datetime(2026, 10, 25, 0, tzinfo=UTC)
HOUR1 = datetime(2026, 10, 25, 1, tzinfo=UTC)
END = datetime(2026, 10, 25, 2, tzinfo=UTC)
HORIZONS = list(lag.HORIZONS_MS)


def world(tmp_path: Path) -> tuple[Path, pl.DataFrame, pl.DataFrame]:
    live = write_lag_world(tmp_path)
    markets = pl.read_parquet(live / "markets.parquet")
    built, _ = pairs(markets, active=ACTIVE)
    return live, markets, built


def run_world(
    tmp_path: Path, fees: pl.DataFrame = FEES, hours: int | None = None
) -> tuple[dict[str, Any], dict[tuple[str, str, int], dict[str, Any]]]:
    live, markets, built = world(tmp_path)
    out = tmp_path / "out"
    start, end, total = lag.run(live, built, markets, fees, out, hours=hours)
    events = pl.read_parquet(sorted((out / lag.EVENT_FOLDER).glob("*.parquet")))
    rows = {(r["a"], r["b"], r["t"]): r for r in events.to_dicts()}
    return lag.summarize(out, start, end, total), rows


def responses(row: dict[str, Any]) -> list[int | None]:
    return [row[f"r_{h}"] for h in HORIZONS]


def test_moves_need_two_cents_of_mid_and_an_unopposed_quote(tmp_path: Path) -> None:
    live, _, _ = world(tmp_path)
    hour0 = lag.frames_of(live / "ticker" / f"{hour_name(HOUR0)}.parquet")
    found = lag.moves(pl.concat([lag.empty_frames(), hour0]))
    seen = {(r["a"], r["t"]): (r["direction"], r["a_move_e6"]) for r in found.to_dicts()}
    assert len(seen) == 16
    assert seen[("T1", lt(0, 20))] == (1, 40_000)
    assert seen[("T3", lt(0, 30))] == (1, 20_000)  # the ask alone moved: not opposed
    assert seen[(f"{G}-BBB", lt(0, 25, 0, 500))] == (-1, 40_000)
    assert seen[(f"{S}-AAA6", lt(0, 50))] == (-1, 100_000)
    assert ("T2", lt(0, 20, 5)) not in seen  # one cent
    assert ("T3", lt(0, 50)) not in seen  # bid down, ask up: opposed
    assert ("T4", lt(0, 0)) not in seen  # a first frame has no previous one
    assert seen[("B1", lt(0, 41))] == (1, 20_000)
    # a one-sided frame in between is skipped: the move is read against the last two-sided one
    flicker = pl.DataFrame(
        [
            _f(lt(0, 0), "X", 20, 22, 10),
            _f(lt(0, 10), "X", 20, None, 10),
            _f(lt(0, 15), "X", 0, 22, 0, 10),  # an empty bid, as the feed shows it
            _f(lt(0, 20), "X", 30, 32, 10),
        ],
        schema=TICKER_SCHEMA,
    )
    one = lag.moves(lag._with_mid(flicker).with_columns(carried=pl.lit(False)))
    assert one.select("a", "t", "direction", "a_move_e6").rows() == [("X", lt(0, 20), 1, 100_000)]
    # the move across the hour boundary needs the carried frame, which is never a move itself
    hour1 = lag.frames_of(live / "ticker" / f"{hour_name(HOUR1)}.parquet")
    assert lag.moves(hour1).is_empty()
    carried = hour0.filter(pl.col("ticker").is_in(["T1", "T2"])).with_columns(carried=pl.lit(True))
    later = lag.moves(pl.concat([carried, hour1]))
    assert sorted(zip(later["a"], later["t"], strict=True)) == [("T1", lt(1, 0)), ("T2", lt(1, 25))]


def test_events_take_the_pairs_valid_at_the_move(tmp_path: Path) -> None:
    live, markets, built = world(tmp_path)
    hour0 = lag.frames_of(live / "ticker" / f"{hour_name(HOUR0)}.parquet")
    found = lag.moves(pl.concat([lag.empty_frames(), hour0]))
    info = lag.market_info(markets, lag.tiers_of(live, HOUR0, END), FEES)
    events = lag.events_of(found, built, info)
    assert events.height == 25
    partners = {
        (r["a"], r["t"]): sorted(
            events.filter((pl.col("a") == r["a"]) & (pl.col("t") == r["t"]))["b"]
        )
        for r in found.to_dicts()
    }
    assert partners[("M1", lt(0, 5))] == ["T1"]
    assert partners[("T1", lt(0, 20))] == ["M2", "T2"]  # M1's window closed at 00:15
    assert partners[(f"KXTOT-{GAME}-200", lt(0, 35))] == []
    assert partners[("KXC-E5-X", lt(0, 37))] == []
    expected = {(r["a"], r["b"]): r["expected"] for r in events.to_dicts()}
    assert expected[(f"{G}-AAA", f"{G}-BBB")] == -1  # a rises, the other outcome should fall
    assert expected[(f"{G}-BBB", f"{G}-AAA")] == 1  # a fell, so the other should rise
    assert expected[("B2", "B1")] == -1  # unsigned: a's own direction
    row = events.filter((pl.col("a") == "T1") & (pl.col("b") == "T2")).to_dicts()[0]
    assert (row["series_a"], row["series_b"], row["category_b"], row["tier_b"]) == (
        "KXT", "KXT", "Crypto", "100 to 1,000",
    )  # fmt: skip
    assert (row["signed"], row["fee_type"], row["multiplier_e6"]) == (True, "quadratic", 1_000_000)


def test_the_quote_before_the_move_and_its_state(tmp_path: Path) -> None:
    _, rows = run_world(tmp_path)
    assert len(rows) == 28
    assert rows[("M1", "T1", lt(0, 5))]["state"] == "unknown"
    assert rows[("T3", "T4", lt(0, 30))]["state"] == "extreme"
    assert rows[(f"{S}-AAA6", f"{S}-AAA3", lt(0, 50))]["state"] == "onesided"
    assert {r["state"] for r in rows.values()} == {"ok", "unknown", "extreme", "onesided"}
    # strictly before the move: BBB's 00:25:00.000 frame for a move at 00:25:00.500, and R1's
    # 00:00 frame for W1's move at 00:45:00, when R1 itself moved in the same millisecond
    assert rows[(f"{G}-BBB", f"{G}-AAA", lt(0, 25, 0, 500))]["mid0"] == 650_000
    assert rows[("W1", "R1", lt(0, 45))]["mid0"] == 810_000
    assert rows[("W1", "R1", lt(0, 45))]["b_ts0"] == lt(0, 0)


def test_responses_at_each_horizon(tmp_path: Path) -> None:
    _, rows = run_world(tmp_path)
    assert responses(rows[("T1", "T2", lt(0, 20))]) == [0, 10_000, 30_000, 30_000, 30_000]
    assert responses(rows[("T2", "T3", lt(0, 21))]) == [0, 0, 0, 20_000, 50_000]
    assert responses(rows[(f"{G}-AAA", f"{G}-BBB", lt(0, 25))]) == [40_000] * 5
    assert responses(rows[("T3", "M2", lt(0, 30))]) == [0, 0, -40_000, -40_000, None]  # M2 closes
    assert responses(rows[("M2", "T3", lt(0, 30, 30))]) == [0, 0, 0, 0, None]  # the gap
    assert responses(rows[("T3", "T2", lt(0, 30))])[-1] == 30_000  # the hour ends as the gap begins
    assert responses(rows[("T1", "T2", lt(1, 0))]) == [0, 0, 0, 0, None]  # the span's end
    assert responses(rows[("W1", "R1", lt(0, 45))]) == [20_000, 20_000, 20_000, 20_000, None]
    assert responses(rows[("M1", "T1", lt(0, 5))]) == [None] * 5  # unknown quote


def test_first_move_and_whether_it_agreed(tmp_path: Path) -> None:
    _, rows = run_world(tmp_path)

    def first(a: str, b: str, t: int) -> tuple[int | None, int | None]:
        return rows[(a, b, t)]["lag_ms"], rows[(a, b, t)]["agreement"]

    assert first("T1", "T2", lt(0, 20)) == (5_000, 1)
    assert first(f"{G}-AAA", f"{G}-BBB", lt(0, 25)) == (500, 1)
    assert first("T3", "M2", lt(0, 30)) == (30_000, -1)
    assert first("M2", "T3", lt(0, 30, 30)) == (1_170_000, -1)
    assert first("W1", "R1", lt(0, 45)) == (0, 1)  # the same millisecond counts as after
    assert first("T2", "T1", lt(0, 21)) == (2_340_000, 1)
    assert first(f"{G}-AAA", f"{S}-AAA3", lt(0, 25)) == (None, None)  # never two-sided again
    assert first("B1", "B2", lt(0, 40)) == (20_000, -1)
    assert rows[("T3", "T2", lt(0, 30))]["first_ms"] == lt(1, 25)
    # the lag is known when a move was seen, or when the whole hour was copied with no outage
    assert rows[("T3", "T2", lt(0, 30))]["lag_known"] is True  # seen at 01:25
    assert rows[(f"{G}-AAA", f"{S}-AAA3", lt(0, 25))]["lag_known"] is True  # a quiet hour
    assert rows[(f"{S}-AAA6", f"{G}-AAA", lt(0, 50))]["lag_known"] is False  # the gap
    assert rows[("B1", "B0", lt(0, 41))]["lag_known"] is False


def test_trades_through_the_stale_quote(tmp_path: Path) -> None:
    _, rows = run_world(tmp_path)

    def through(a: str, b: str, t: int) -> tuple[int, int, int, int]:
        r = rows[(a, b, t)]
        return r["with_n"], r["with_e2"], r["against_n"], r["against_e2"]

    assert through("T1", "T2", lt(0, 20)) == (1, 300, 1, 100)
    assert through(f"{G}-AAA", f"{G}-BBB", lt(0, 25)) == (1, 150, 0, 0)
    assert through("T3", "T2", lt(0, 30)) == (1, 500, 0, 0)  # the 01:13 block is left out
    assert through("T1", "T2", lt(1, 0)) == (1, 500, 0, 0)  # the 01:26 trade follows the move
    assert through("M2", "T3", lt(0, 30, 30)) == (0, 0, 1, 200)  # a yes taker, the wrong side
    assert through("T2", "T3", lt(0, 21)) == (0, 0, 0, 0)


def test_capacity_of_each_stale_quote(tmp_path: Path) -> None:
    _, rows = run_world(tmp_path)

    def capacity(a: str, b: str, t: int) -> tuple[Any, ...]:
        r = rows[(a, b, t)]
        return r["stale_size_e2"], r["stale_price_e6"], r["gross_e6"], r["fee_e6"], r["net_e6"]

    assert capacity("T1", "T2", lt(0, 20)) == (200, 320_000, 60_000, 30_464, 29_536)
    assert capacity(f"{G}-AAA", f"{G}-BBB", lt(0, 25)) == (300, 360_000, 120_000, 48_384, 71_616)
    assert capacity("M2", "T3", lt(0, 30, 30)) == (70, 200_000, 0, 7_840, -7_840)  # the bid side
    assert capacity("T2", "T3", lt(0, 21)) == (60, 220_000, 12_000, 7_208, 4_792)
    assert capacity("T3", "M2", lt(0, 30)) == (600, 520_000, -240_000, 104_832, -344_832)
    assert capacity("B1", "B2", lt(0, 40)) == (None, None, None, None, None)  # unsigned
    assert capacity("T3", "T4", lt(0, 30)) == (None, None, None, None, None)  # extreme


def test_an_unknown_fee_type_leaves_the_fee_unknown(tmp_path: Path) -> None:
    fixed = pl.when(pl.col("series_ticker") == "KXG").then(pl.lit("fixed"))
    fees = FEES.with_columns(fee_type=fixed.otherwise(pl.col("fee_type")))
    summary, rows = run_world(tmp_path, fees)
    row = rows[(f"{G}-AAA", f"{G}-BBB", lt(0, 25))]
    assert (row["gross_e6"], row["fee_e6"], row["net_e6"]) == (120_000, None, None)
    assert summary["by_kind"]["outcomes"]["capacity"]["fee_unknown"] == 2
    assert summary["by_kind"]["thresholds"]["capacity"]["fee_unknown"] == 0


def test_tiers_gaps_and_market_info(tmp_path: Path) -> None:
    live, markets, _ = world(tmp_path)
    tiers = {r["ticker"]: r["tier"] for r in lag.tiers_of(live, HOUR0, END).to_dicts()}
    assert tiers == {"T2": "100 to 1,000", "T3": "under 100", f"{G}-BBB": "under 100"}
    info = lag.market_info(markets, lag.tiers_of(live, HOUR0, END), FEES)
    by_ticker = {r["ticker"]: r for r in info.to_dicts()}
    assert by_ticker["T1"]["tier"] == "under 100" and by_ticker["T2"]["tier"] == "100 to 1,000"
    assert by_ticker["M2"]["close_ms"] == lt(1, 0)
    gaps = lag.gaps_of(live, END)
    assert gaps.to_dicts() == [{"start_ms": lt(1, 30), "end_ms": lt(1, 31)}]
    assert lag.gaps_of(tmp_path, END).is_empty()
    events = pl.DataFrame({"t": [lt(0, 30), lt(0, 30, 30), lt(1, 25)]})
    flags = lag.with_gaps(events, gaps).to_dicts()
    assert [(r["in_gap"], r["gap_1h"]) for r in flags] == [
        (False, False),
        (False, True),
        (True, True),
    ]
    none = lag.with_gaps(events, lag.gaps_of(tmp_path, END))
    assert not none["in_gap"].any() and not none["gap_1h"].any()
    assert lag._trades_of(live, [END]).is_empty()  # no trade file for that hour


def test_the_summary_by_kind(tmp_path: Path) -> None:
    summary, _ = run_world(tmp_path)
    assert (summary["start"], summary["end"], summary["hours"]) == (
        HOUR0.isoformat(), END.isoformat(), 2,
    )  # fmt: skip
    assert (summary["moves"], summary["moves_with_partner"], summary["events"]) == (18, 15, 28)
    assert (summary["unknown_b"], summary["onesided_b"], summary["extreme_b"]) == (1, 1, 1)
    assert (summary["gap_events"], summary["usable_events"]) == (2, 23)
    # no move seen and the hour met the gap: AAA6, B1 to B0 twice, B1 to B2 at 00:41, W1, R1
    assert summary["lag_unknown"] == 6
    kinds = summary["by_kind"]
    assert {k: v["events"] for k, v in kinds.items()} == {
        "thresholds": 5, "outcomes": 2, "game": 3, "horizons": 4, "brackets": 6, "weather": 3,
    }  # fmt: skip
    thresholds = kinds["thresholds"]
    assert (thresholds["events_a_day"], thresholds["pairs"], thresholds["markets_b"]) == (60, 4, 3)
    # the hour's mean is over the four events whose hour was known (the fifth met the gap)
    assert thresholds["response_cents"] == {
        "1s": 0.0,
        "10s": 0.2,
        "1m": 0.6,
        "10m": 1.0,
        "1h": 4.25,
    }
    assert thresholds["moved_within"] == {"1s": 0.0, "10s": 0.2, "1m": 0.2, "10m": 0.4, "1h": 1.0}
    assert thresholds["agreement"] == 1.0
    # the 01:12 trade sat in two events' windows but in one stale quote's: counted once
    assert (thresholds["through_with_share"], thresholds["through_with_e2_a_day"]) == (0.6, 9_600)
    assert (thresholds["through_against_share"], thresholds["through_against_e2_a_day"]) == (
        0.2,
        1_200,
    )
    assert thresholds["stale_size_median_e2"] == 200.0
    assert thresholds["capacity"] == {
        "gross_e6_a_day": 864_000,
        "fee_e6_a_day": 1_138_476,
        "net_e6_a_day": -274_476,
        "quotes_a_day": 48,  # four stale quotes in two hours: the fifth event shares one
        "reachable_gross_e6_a_day": 864_000,  # every one of them stood over a second
        "reachable_fee_e6_a_day": 1_138_476,
        "reachable_net_e6_a_day": -274_476,
        "reachable_quotes_a_day": 48,
        "fee_unknown": 0,
        "clears": False,
        "clears_reachable": False,
    }
    assert thresholds["stale_quotes_a_day"] == 48
    # BBB answered AAA within half a second: that quote was never there to hit
    outcomes = kinds["outcomes"]["capacity"]
    assert (outcomes["reachable_net_e6_a_day"], outcomes["reachable_quotes_a_day"]) == (
        -37_704, 12,
    )  # fmt: skip
    # B0 one (its size was re-quoted, but both moves fell in one hour with no answer); B2 two
    # (one ended at 00:40:20, one stood); B1 one (it answered at 00:41); W2 one
    assert (kinds["brackets"]["stale_quotes_a_day"], kinds["weather"]["stale_quotes_a_day"]) == (
        60, 36,
    )  # fmt: skip
    assert kinds["outcomes"]["capacity"]["net_e6_a_day"] == 821_688
    assert kinds["outcomes"]["response_cents"]["1s"] == 2.0
    assert (kinds["game"]["agreement"], kinds["game"]["capacity"]["quotes_a_day"]) == (None, 36)
    # AAA6's hour met the gap with no move seen: its lag is unknown, so not reachable
    assert kinds["game"]["capacity"]["reachable_quotes_a_day"] == 24
    assert kinds["game"]["capacity"]["reachable_net_e6_a_day"] == -262_584
    horizons = kinds["horizons"]
    assert (horizons["response_cents"]["10m"], horizons["agreement"]) == (-2.0, 0.0)
    assert (horizons["capacity"]["net_e6_a_day"], horizons["capacity"]["quotes_a_day"]) == (
        -1_352_064, 24,
    )  # fmt: skip
    assert horizons["through_against_e2_a_day"] == 2_400
    assert kinds["brackets"]["capacity"] is None and kinds["brackets"]["response_cents"][
        "10m"
    ] == pytest.approx(-5 / 6)
    assert kinds["brackets"]["response_cents"]["1h"] is None  # every hour overlapped the gap
    assert kinds["weather"]["response_cents"]["10m"] == 2.0 and kinds["weather"]["agreement"] == 1.0
    assert summary["cleared"] == [] and summary["cleared_reachable"] == []
    interval = thresholds["intervals"]["net_e6_a_day"]
    assert (interval["point"], interval["clusters"]) == (-274_476.0, 2)
    assert thresholds["intervals"]["response_10m_cents"]["point"] == 1.0
    assert "net_e6_a_day" not in kinds["brackets"]["intervals"]
    assert thresholds["intervals"]["reachable_net_e6_a_day"]["point"] == -274_476.0
    assert kinds["outcomes"]["intervals"]["reachable_net_e6_a_day"]["point"] == -37_704.0


def test_the_summary_by_tier_category_and_the_slow_spots(tmp_path: Path) -> None:
    summary, _ = run_world(tmp_path)
    tiers = summary["by_tier"]
    assert tiers["thresholds | 100 to 1,000"]["events"] == 3  # T2 is the watched market
    assert tiers["thresholds | under 100"]["events"] == 2
    assert tiers["thresholds | 100 to 1,000"]["capacity"]["quotes_a_day"] == 24  # two spells of T2
    assert summary["by_category"]["outcomes | Sports"]["capacity"]["net_e6_a_day"] == 821_688
    moves = summary["by_move"]
    assert {k: v["events"] for k, v in moves.items() if k.startswith("thresholds")} == {
        "thresholds | 2 to 3": 3,  # T2's and T3's two-cent moves
        "thresholds | 3 to 5": 1,  # T1's four cents at 00:20
        "thresholds | 5 to 10": 1,  # T1's six cents at 01:00
    }
    assert moves["game | 10 and more"]["events"] == 1  # AAA6's ten cents
    # the spots rank by the reachable capacity: BBB's half-second answer drops the outcomes pair
    spots = {(s["kind"], s["series_a"], s["series_b"]): s for s in summary["slow_spots"]}
    assert list(spots)[:2] == [("outcomes", "KXG", "KXG"), ("horizons", "KXT15M", "KXT")]
    assert spots[("outcomes", "KXG", "KXG")]["capacity"]["reachable_net_e6_a_day"] == -37_704
    assert ("game", "KXS", "KXG") not in spots  # its one quote's lag is unknown: none reachable
    assert spots[("outcomes", "KXG", "KXG")]["through_against_share"] == 0.0
    assert spots[("outcomes", "KXG", "KXG")]["capacity"]["net_e6_a_day"] == 821_688
    assert spots[("thresholds", "KXT", "KXT")]["capacity"]["net_e6_a_day"] == -274_476
    assert ("thresholds", "KXS", "KXS") not in spots  # its one event had a one-sided quote
    assert all(s["capacity"] is not None for s in spots.values())


def test_run_writes_an_hour_a_file_and_can_stop_early(tmp_path: Path) -> None:
    live, markets, built = world(tmp_path)
    out = tmp_path / "out"
    stale = out / lag.EVENT_FOLDER / "stale.parquet"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"")
    start, end, total = lag.run(live, built, markets, FEES, out, hours=1)
    assert (start, end, total) == (HOUR0, HOUR1, 16)
    assert sorted(p.name for p in stale.parent.glob("*.parquet")) == [f"{hour_name(HOUR0)}.parquet"]
    # one hour alone: the 1-hour responses reach past the copied frames
    summary = lag.summarize(out, start, end, total)
    assert summary["hours"] == 1 and summary["events"] == 25
    assert summary["by_kind"]["thresholds"]["response_cents"]["1h"] is None
    # the next hour's frames and trades were not read: T2's answer at 01:25 is unknown, not
    # "never", so the quote is not reachable, and the 01:12 trade is not counted
    written = pl.read_parquet(stale.parent / f"{hour_name(HOUR0)}.parquet")
    rows = {(r["a"], r["b"], r["t"]): r for r in written.to_dicts()}
    row = rows[("T3", "T2", lt(0, 30))]
    assert (row["lag_ms"], row["lag_known"], row["with_n"]) == (None, False, 0)
    assert rows[("T1", "T2", lt(0, 20))]["lag_known"] is True
    # T1's answer at 01:00 and T2's at 01:25 lie past the hour: two of the four lags unknown
    assert summary["by_kind"]["thresholds"]["capacity"]["reachable_quotes_a_day"] == 48
    with pytest.raises(SurveyDataError, match="no whole hour"):
        lag.summarize(out, start, start, 0)
    with pytest.raises(SurveyDataError, match="no events"):
        lag.summarize(tmp_path / "elsewhere", start, end, 0)
