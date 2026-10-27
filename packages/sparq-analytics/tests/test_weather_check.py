from datetime import date
from pathlib import Path

import polars as pl
import pytest
from sparq_analytics.weather.check import check, coverage, implied, scope_markets
from sparq_analytics.weather.stations import WeatherDataError

SEP15 = date(2026, 9, 15)


def market(
    ticker: str, result: str, strike: str, floor: str | None, cap: str | None, rules: str
) -> dict[str, str | None]:
    series, event_day = ticker.split("-")[0], ticker.split("-")[1]
    return {
        "ticker": ticker,
        "series_ticker": series,
        "event_ticker": f"{series}-{event_day}",
        "result": result,
        "strike_type": strike,
        "floor_strike": floor,
        "cap_strike": cap,
        "rules_primary": rules,
    }


NY_RULES = "If the maximum temperature recorded at New York City (CLINYC) for Sep 15, 2026, ..."
MARKETS = [
    market("KXHIGHNY-26SEP15-B70.5", "no", "between", "70", "71", NY_RULES),
    market("KXHIGHNY-26SEP15-T72", "yes", "greater", "72", None, NY_RULES),
    market("KXHIGHNY-26SEP15-T73", "yes", "less", None, "73", NY_RULES),  # the bound says no
    market("KXHIGHNY-26AUG08-T80", "no", "greater", "80", None, "recorded at New York City for"),
    market("KXRAIN-26SEP15-NYC", "yes", "greater", "0", None, "precipitation at CLINYC in NYC"),
    market("KXRAIN-26SEP15-MKE", "no", "greater", "0", None, "precipitation at KMKE in Milwaukee"),
    market("KXHIGHTSEA-26SEP15-T50", "no", "greater", "50", None, "recorded at Nowhere (CLIZZZ)"),
    market("KXHIGHNY-26SEP16-T70", "", "greater", "70", None, NY_RULES),  # not settled
    market("KXTEMPNYCH-26SEP1510-T70.99", "no", "greater", "70.99", None, NY_RULES),  # not in scope
    market("KXHIGHNY-26OCT12-T70", "no", "greater", "70", None, NY_RULES),  # test part
    # a series named like a high but not about weather, with no day in its event ticker
    market("KXHIGHINFLATION-26DEC-T3", "no", "greater", "3", None, "inflation above 3%"),
]


def write_history(root: Path, rows: list[dict[str, str | None]]) -> Path:
    folder = root / "kalshi" / "markets" / "historical"
    folder.mkdir(parents=True)
    pl.DataFrame(rows).write_parquet(folder / "part-0.parquet")
    return root / "kalshi"


def daily(rows: list[tuple[str, str, date, int | None, bool]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "station": s,
                "mode": m,
                "day": d,
                "bound_f": b,
                "rain_seen": r,
                "reports": 24,
                "routine": 24,
            }
            for s, m, d, b, r in rows
        ],
        schema={
            "station": pl.String,
            "mode": pl.String,
            "day": pl.Date,
            "bound_f": pl.Int32,
            "rain_seen": pl.Boolean,
            "reports": pl.UInt32,
            "routine": pl.UInt32,
        },
    )


@pytest.mark.parametrize(
    ("series", "strike", "floor", "cap", "bound", "rain", "margin", "expected"),
    [
        ("KXHIGHNY", "greater", 72.0, None, 74, None, 1, "yes"),  # above 72 means 73; margin 1
        ("KXHIGHNY", "greater", 72.0, None, 73, None, 1, None),
        ("KXHIGHNY", "greater", 72.0, None, 73, None, 0, "yes"),
        ("KXHIGHNY", "less", None, 73.0, 74, None, 1, "no"),  # below 73: 73 already rules it out
        ("KXHIGHNY", "less", None, 73.0, 73, None, 1, None),
        ("KXHIGHNY", "less", None, 73.0, 73, None, 0, "no"),
        ("KXHIGHNY", "between", 70.0, 71.0, 73, None, 1, "no"),  # 70 to 71: 72 rules it out
        ("KXHIGHNY", "between", 70.0, 71.0, 72, None, 1, None),
        ("KXHIGHNY", "between", 70.0, 71.0, 72, None, 0, "no"),
        ("KXHIGHNY", "greater", 72.0, None, None, None, 1, None),
        ("KXHIGHNY", "weird", 72.0, None, 99, None, 1, None),
        ("KXHIGHNY", "less", None, None, 99, None, 1, None),
        ("KXRAIN", "greater", 0.0, None, None, True, 1, "yes"),
        ("KXRAIN", "greater", 0.0, None, None, False, 1, None),
        ("KXRAIN", "greater", 0.0, None, None, None, 1, None),
    ],
)
def test_what_a_bound_settles(
    series: str,
    strike: str,
    floor: float | None,
    cap: float | None,
    bound: int | None,
    rain: bool | None,
    margin: int,
    expected: str | None,
) -> None:
    assert implied(series, strike, floor, cap, bound, rain, margin) == expected


def test_fractional_strikes_use_whole_degrees() -> None:
    assert implied("KXHIGHNY", "greater", 72.5, None, 74, None, 1) == "yes"  # above 72.5 is 73
    assert implied("KXHIGHNY", "less", None, 72.5, 74, None, 1) == "no"  # ceil 73, plus margin


def test_the_scope_takes_settled_markets_with_their_site(tmp_path: Path) -> None:
    root = write_history(tmp_path, MARKETS)
    markets = scope_markets(root, date(2026, 8, 7), date(2026, 9, 28))
    by_ticker = {r["ticker"]: r for r in markets.to_dicts()}
    assert set(by_ticker) == {
        "KXHIGHNY-26SEP15-B70.5",
        "KXHIGHNY-26SEP15-T72",
        "KXHIGHNY-26SEP15-T73",
        "KXHIGHNY-26AUG08-T80",
        "KXRAIN-26SEP15-NYC",
        "KXRAIN-26SEP15-MKE",
        "KXHIGHTSEA-26SEP15-T50",
    }
    assert by_ticker["KXHIGHNY-26AUG08-T80"]["code"] == "CLINYC"  # the series' only code
    assert by_ticker["KXHIGHNY-26AUG08-T80"]["station"] == "KNYC"
    assert by_ticker["KXRAIN-26SEP15-MKE"]["station"] == "KMKE"
    assert by_ticker["KXRAIN-26SEP15-MKE"]["city"] == "MKE"
    assert by_ticker["KXHIGHTSEA-26SEP15-T50"]["station"] is None
    assert by_ticker["KXHIGHNY-26SEP15-T72"]["day"] == SEP15


def test_the_scope_never_reads_the_test_part(tmp_path: Path) -> None:
    root = write_history(tmp_path, MARKETS)
    with pytest.raises(WeatherDataError, match="test part"):
        scope_markets(root, date(2026, 8, 7), date(2026, 10, 11))


def test_the_check_counts_calls_and_finds_the_wrong_one(tmp_path: Path) -> None:
    markets = scope_markets(write_history(tmp_path, MARKETS), date(2026, 8, 7), date(2026, 9, 28))
    days = daily(
        [
            ("KNYC", "standard", SEP15, 74, True),
            ("KNYC", "civil", SEP15, 72, True),
        ]
    )
    summary, wrong = check(markets, days)
    standard = {r["city"]: r for r in summary.filter(pl.col("mode") == "standard").to_dicts()}
    new_york = standard["KXHIGHNY"]
    assert (new_york["markets"], new_york["with_station"], new_york["with_data"]) == (4, 4, 3)
    assert (new_york["called"], new_york["wrong"]) == (3, 1)  # B70.5 no, T72 yes, T73 no (wrong)
    assert (new_york["called_no_margin"], new_york["wrong_no_margin"]) == (3, 1)
    assert (standard["NYC"]["called"], standard["NYC"]["wrong"]) == (1, 0)
    assert (standard["MKE"]["with_data"], standard["MKE"]["called"]) == (0, 0)
    assert standard["KXHIGHTSEA"]["with_station"] == 0
    civil = {r["city"]: r for r in summary.filter(pl.col("mode") == "civil").to_dicts()}
    # at 72 only B70.5 is settled with no margin, and the T73 call needs 73
    assert (civil["KXHIGHNY"]["called"], civil["KXHIGHNY"]["called_no_margin"]) == (0, 1)
    assert wrong.filter(pl.col("mode") == "standard")["ticker"].to_list() == [
        "KXHIGHNY-26SEP15-T73"
    ]


def test_coverage_counts_short_days() -> None:
    days = daily([("KNYC", "standard", SEP15, 70, False), ("KNYC", "civil", SEP15, 70, False)])
    days = pl.concat(
        [
            days,
            daily([("KNYC", "standard", date(2026, 9, 16), 70, False)]).with_columns(
                routine=pl.lit(20, pl.UInt32)
            ),
        ]
    )
    assert coverage(days).to_dicts() == [
        {"station": "KNYC", "days": 2, "short_days": 1, "fewest_routine": 20}
    ]
