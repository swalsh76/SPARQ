import polars as pl
from laghelpers import ACTIVE, DAY_MS, GAME, MARKETS, lt
from sparq_analytics.live.source import MARKET_SCHEMA
from sparq_analytics.survey.relations import KINDS, PAIR_COLUMNS, describe, pairs, stems
from sparq_analytics.survey.relations import _station_day as station_day
from surveyhelpers import BASE

MARKET_FRAME = pl.DataFrame(MARKETS, schema=MARKET_SCHEMA)
G = f"KXG-{GAME}"
S = f"KXS-{GAME}"
WHOLE = (BASE - DAY_MS, BASE + DAY_MS)


def by_kind(built: pl.DataFrame, kind: str) -> dict[tuple[str, str], dict[str, object]]:
    rows = built.filter(pl.col("kind") == kind).to_dicts()
    return {(r["a"], r["b"]): r for r in rows}


def test_describe_reads_what_each_market_is() -> None:
    d = describe(MARKET_FRAME)
    rows = {r["ticker"]: r for r in d.to_dicts()}
    assert (rows["T1"]["what"], rows["T1"]["direction"], rows["T1"]["level"]) == (
        "threshold",
        1,
        100.0,
    )
    b0 = rows["B0"]
    assert (b0["what"], b0["direction"], b0["level"], b0["low"]) == (
        "bracket", None, None, None,
    )  # fmt: skip
    assert (rows["B1"]["low"], rows[f"{G}-AAA"]["what"], rows["KXC-E5-X"]["what"]) == (
        50.0,
        "outcome",
        None,
    )
    assert rows[f"KXP-{GAME}-AAAJDOE1-20"]["subject"] == f"KXP-{GAME}-AAAJDOE1-"
    aaa3 = rows[f"{S}-AAA3"]
    assert (aaa3["subject"], aaa3["parts"], aaa3["last"]) == (
        f"{S}-AAA", 3, "AAA3",
    )  # fmt: skip
    assert rows[f"{G}-AAA"]["game"] == GAME and rows["T1"]["game"] is None
    # a less market's level is its cap, and its direction is down
    less = describe(
        pl.DataFrame(
            [
                {
                    "ticker": "X-E-L",
                    "series_ticker": "X",
                    "event_ticker": "X-E",
                    "category": "Economics",
                    "open_ms": 0,
                    "close_ms": 1,
                    "strike_type": "less",
                    "floor_strike": None,
                    "cap_strike": "20",
                    "weather_rules": None,
                }
            ],  # fmt: skip
            schema=MARKET_SCHEMA,
        )
    ).to_dicts()[0]
    assert (less["what"], less["direction"], less["level"]) == ("threshold", -1, 20.0)


def test_every_kind_finds_its_pairs() -> None:
    built, coverage = pairs(MARKET_FRAME)
    assert built.columns == PAIR_COLUMNS
    assert coverage["by_kind"] == {
        "thresholds": {"pairs": 8, "markets": 6, "groups": 2},
        "outcomes": {"pairs": 2, "markets": 2, "groups": 1},
        "game": {"pairs": 5, "markets": 5, "groups": 1},
        "horizons": {"pairs": 10, "markets": 6, "groups": 1},
        "brackets": {"pairs": 6, "markets": 5, "groups": 2},
        "weather": {"pairs": 4, "markets": 3, "groups": 1},
    }
    assert (coverage["markets"], coverage["related"], coverage["unrelated"]) == (26, 17, 9)
    assert coverage["unrelated_top_series"] == {
        "KXO": 4,
        "KXHIGHNY": 2,
        "KXC": 1,
        "KXP": 1,
        "KXTOT": 1,
    }
    assert coverage["horizon_stems"] == {"KXT": ["KXT", "KXT15M"]}
    assert list(coverage["by_kind"]) == list(KINDS)


def test_thresholds_pair_neighbours_of_one_subject_both_ways() -> None:
    built, _ = pairs(MARKET_FRAME)
    rows = by_kind(built, "thresholds")
    assert set(rows) == {
        ("T1", "T2"), ("T2", "T1"), ("T2", "T3"), ("T3", "T2"), ("T3", "T4"), ("T4", "T3"),
        (f"{S}-AAA3", f"{S}-AAA6"), (f"{S}-AAA6", f"{S}-AAA3"),
    }  # fmt: skip
    assert {r["sign"] for r in rows.values()} == {1}
    assert (rows[("T1", "T2")]["valid_from_ms"], rows[("T1", "T2")]["valid_to_ms"]) == WHOLE
    assert rows[("T1", "T2")]["group"] == "KXT-E1"
    # a greater against a less of one subject: opposite sign
    event = pl.DataFrame(
        [
            {
                "ticker": "X-E-T10",
                "series_ticker": "X",
                "event_ticker": "X-E",
                "category": "Economics",
                "open_ms": 0,
                "close_ms": 10,
                "strike_type": "greater",
                "floor_strike": "10",
                "cap_strike": None,
                "weather_rules": None,
            },
            {
                "ticker": "X-E-T20",
                "series_ticker": "X",
                "event_ticker": "X-E",
                "category": "Economics",
                "open_ms": 0,
                "close_ms": 10,
                "strike_type": "less",
                "floor_strike": None,
                "cap_strike": "20",
                "weather_rules": None,
            },
        ],  # fmt: skip
        schema=MARKET_SCHEMA,
    )
    mixed, _ = pairs(event)
    assert mixed.filter(pl.col("kind") == "thresholds")["sign"].to_list() == [-1, -1]


def test_outcomes_and_game_pairs() -> None:
    built, _ = pairs(MARKET_FRAME)
    outcomes = by_kind(built, "outcomes")
    assert set(outcomes) == {(f"{G}-AAA", f"{G}-BBB"), (f"{G}-BBB", f"{G}-AAA")}
    assert {r["sign"] for r in outcomes.values()} == {-1}
    game = by_kind(built, "game")
    assert set(game) == {
        (f"{G}-AAA", f"{S}-AAA3"),
        (f"{S}-AAA3", f"{G}-AAA"),
        (f"{S}-AAA6", f"{G}-AAA"),
        (f"{G}-BBB", f"{S}-BBB2"),
        (f"{S}-BBB2", f"{G}-BBB"),
    }
    assert {r["sign"] for r in game.values()} == {1}
    assert {r["group"] for r in game.values()} == {GAME}


def test_horizons_pair_the_nearest_level_of_the_soonest_closing_event() -> None:
    built, _ = pairs(MARKET_FRAME)
    rows = by_kind(built, "horizons")
    first = (lt(0) - 15 * 60_000, lt(0, 15))
    second = (lt(0, 15), lt(1))
    expected = {(f"T{i}", "M1"): first for i in range(1, 5)}
    expected |= {(f"T{i}", "M2"): second for i in range(1, 5)}
    expected[("M1", "T1")] = first
    expected[("M2", "T3")] = (lt(0), lt(1))
    assert {k: (r["valid_from_ms"], r["valid_to_ms"]) for k, r in rows.items()} == expected
    assert {r["sign"] for r in rows.values()} == {1}
    assert {r["group"] for r in rows.values()} == {"KXT"}


def test_brackets_and_weather_pairs_carry_no_sign() -> None:
    built, _ = pairs(MARKET_FRAME)
    brackets = by_kind(built, "brackets")
    assert set(brackets) == {
        ("B0", "B1"), ("B1", "B0"), ("B1", "B2"), ("B2", "B1"), ("W1", "W2"), ("W2", "W1"),
    }  # fmt: skip
    weather = by_kind(built, "weather")
    assert set(weather) == {("W1", "R1"), ("R1", "W1"), ("W2", "R1"), ("R1", "W2")}
    assert {r["sign"] for r in brackets.values()} == {None}
    assert {r["group"] for r in weather.values()} == {"CLINYC 2026-10-25"}


def test_only_active_markets_form_pairs() -> None:
    built, coverage = pairs(MARKET_FRAME, active=ACTIVE)
    assert coverage["markets"] == 24 and coverage["unrelated"] == 7  # W3 and W4 have no frames
    narrow, cover = pairs(MARKET_FRAME, active=["T1", "T2"])
    assert set(zip(narrow["a"], narrow["b"], strict=True)) == {("T1", "T2"), ("T2", "T1")}
    assert cover["horizon_stems"] == {} and cover["by_kind"]["horizons"]["pairs"] == 0


def test_stems_join_series_that_share_one_within_a_category() -> None:
    series = pl.DataFrame(
        {
            "series_ticker": [
                "KXHYPE",
                "KXHYPED",
                "KXHYPE15M",
                "KXGOLDH",
                "KXGOLDD",
                "KXGOLD15M",
                "KXWTIH",
                "KXINXU",
                "KXINX",
                "KXNBA1H",
                "KXFOOD",
                "KXFOO",
            ],
            "category": ["Crypto"] * 3
            + ["Commodities"] * 4
            + ["Financials"] * 2
            + ["Sports"]
            + ["Economics", "Politics"],
        }  # fmt: skip
    )
    found = {r["series_ticker"]: r["stem"] for r in stems(series).to_dicts()}
    assert found == {
        "KXHYPE": "KXHYPE", "KXHYPED": "KXHYPE", "KXHYPE15M": "KXHYPE",
        "KXGOLDH": "KXGOLD", "KXGOLDD": "KXGOLD", "KXGOLD15M": "KXGOLD",
        "KXINXU": "KXINX", "KXINX": "KXINX",
    }  # fmt: skip


def test_weather_groups_need_a_station_and_a_day() -> None:
    d = describe(MARKET_FRAME).filter(pl.col("weather_rules").is_not_null())
    grouped = station_day(d)
    assert set(grouped["ticker"]) == {"W1", "W2", "R1"}  # W3 has no day, W4 no station
    assert set(grouped["group"]) == {"CLINYC 2026-10-25"}
