"""S3.2's relation groups: the markets that should move together, from the market list's own
fields (PLAN §6.3, S3.2), each pair with its expected sign and the window in which both are open.

Kalshi's ``strike_type`` says what a market is: a **threshold** (``greater``, ``greater_or_equal``,
``less`` or ``less_or_equal``; the level is ``floor_strike``, or ``cap_strike`` for a less), a
**bracket** (``between``) or an **outcome** (``structured``, one of an event's alternatives);
``custom`` is none of these and takes no part. The kinds:

* ``thresholds``: adjacent levels of one subject in one event (the subject is the ticker without
  its trailing line, so a spread's two teams and a prop's players stay apart); same sign, opposite
  for a greater against a less.
* ``outcomes``: the two or three alternatives of one structured event; opposite sign.
* ``game``: the same team, or the total, across the series of one sports game (the game code is
  the event ticker's second part, such as ``26OCT27PORLAL``); one partner per other series, its
  central line; same sign.
* ``horizons``: thresholds of one underlying across series of different horizons (``KXBTC15M``
  against ``KXBTCD``), the partner being the nearest level in the soonest-closing open event of
  the other series; same sign. The stem is the series ticker without a ``15M`` or a final ``D``,
  ``H``, ``U`` or ``E``, when another series of the category shares it.
* ``brackets``: adjacent brackets of one event; no expected sign (measured for timing only).
* ``weather``: markets of one station and day across weather series (the station from the rules
  text, as S2 read it); no expected sign.

A pair is ordered: ``a``'s moves are the events and ``b`` is watched. ``sign`` is the direction
``b`` is expected to move for an upward move of ``a`` (null where none is expected). Levels are
compared as floats for ordering only; no money is computed here.
"""

from collections.abc import Iterable
from typing import Any

import polars as pl

from sparq_analytics.weather.stations import WeatherDataError, code_in, market_day

UP = ("greater", "greater_or_equal")
DOWN = ("less", "less_or_equal")
KINDS = ("thresholds", "outcomes", "game", "horizons", "brackets", "weather")
SIGNED = frozenset({"thresholds", "outcomes", "game", "horizons"})
GAME_CODE = r"^\d{2}[A-Z]{3}\d{2}(?:\d{4})?[A-Z]+$"  # 26OCT27PORLAL, 26OCT272000MILTB
LINE = r"[0-9.]+$"
TOTAL = "TOTAL"  # the subject of a game's total markets, which name no team
MAX_OUTCOMES = 3
PAIR_COLUMNS = ["a", "b", "kind", "sign", "group", "valid_from_ms", "valid_to_ms"]
_SIDES = ("ticker", "direction", "open_ms", "close_ms")


def describe(markets: pl.DataFrame) -> pl.DataFrame:
    """Each market with what it is (``what``), its level and direction, its subject, its ticker
    parts and, for sports, its game code."""
    kind = pl.col("strike_type")
    floor = pl.col("floor_strike").cast(pl.Float64, strict=False)
    cap = pl.col("cap_strike").cast(pl.Float64, strict=False)
    parts = pl.col("ticker").str.split("-")
    second = parts.list.get(1, null_on_oob=True)
    return markets.with_columns(
        what=pl.when(kind.is_in(UP) | kind.is_in(DOWN))
        .then(pl.lit("threshold"))
        .when(kind == "between")
        .then(pl.lit("bracket"))
        .when(kind == "structured")
        .then(pl.lit("outcome")),
        direction=pl.when(kind.is_in(UP)).then(1).when(kind.is_in(DOWN)).then(-1).cast(pl.Int8),
        level=pl.when(kind.is_in(UP)).then(floor).when(kind.is_in(DOWN)).then(cap),
        low=floor,
        subject=pl.col("ticker").str.replace(LINE, ""),
        parts=parts.list.len(),
        last=parts.list.last(),
        game=pl.when((pl.col("category") == "Sports") & second.str.contains(GAME_CODE)).then(
            second
        ),
    )


def _finish(frame: pl.DataFrame, kind: str, sign: pl.Expr) -> pl.DataFrame:
    """Pairs in the common columns, each valid while both markets are open (and, for horizons,
    while ``b``'s event is the soonest to close)."""
    froms = [pl.col("open_a"), pl.col("open_b")] + (
        [pl.col("w_from")] if "w_from" in frame.columns else []
    )
    tos = [pl.col("close_a"), pl.col("close_b")] + (
        [pl.col("w_to")] if "w_to" in frame.columns else []
    )
    return (
        frame.select(
            "a",
            "b",
            kind=pl.lit(kind),
            sign=sign.cast(pl.Int8),
            group=pl.col("group"),
            valid_from_ms=pl.max_horizontal(froms),
            valid_to_ms=pl.min_horizontal(tos),
        )
        .filter(pl.col("valid_from_ms") < pl.col("valid_to_ms"))
        .filter(pl.col("a") != pl.col("b"))
    )


def _both_ways(pairs: pl.DataFrame) -> pl.DataFrame:
    """Each pair in both orders (the sign and the window are symmetric)."""
    swapped = pairs.rename({"a": "b", "b": "a"}).select(pairs.columns)
    return pl.concat([pairs, swapped])


def _neighbours(rows: pl.DataFrame, keys: list[str], order: pl.Expr) -> pl.DataFrame:
    """Each row with its successor in ``order`` within ``keys``, as the sides of a pair."""
    rows = rows.with_columns(order=order).sort([*keys, "order", "ticker"])
    nxt = rows.with_columns(
        [pl.col(c).shift(-1).over(keys).alias(f"{c}_b") for c in _SIDES]
    ).filter(pl.col("ticker_b").is_not_null())
    return nxt.select(
        a=pl.col("ticker"),
        b=pl.col("ticker_b"),
        direction_a=pl.col("direction"),
        direction_b=pl.col("direction_b"),
        open_a=pl.col("open_ms"),
        close_a=pl.col("close_ms"),
        open_b=pl.col("open_ms_b"),
        close_b=pl.col("close_ms_b"),
        group=pl.col(keys[0]),
    )


def _thresholds(d: pl.DataFrame) -> pl.DataFrame:
    rows = d.filter((pl.col("what") == "threshold") & pl.col("level").is_not_null())
    pairs = _neighbours(rows, ["event_ticker", "subject"], pl.col("level"))
    return _both_ways(_finish(pairs, "thresholds", pl.col("direction_a") * pl.col("direction_b")))


def _brackets(d: pl.DataFrame) -> pl.DataFrame:
    rows = d.filter(pl.col("what") == "bracket")
    pairs = _neighbours(rows, ["event_ticker"], pl.col("low").fill_null(float("-inf")))
    return _both_ways(_finish(pairs, "brackets", pl.lit(None)))


def _outcomes(d: pl.DataFrame) -> pl.DataFrame:
    rows = d.filter(pl.col("what") == "outcome").filter(
        pl.len().over("event_ticker") <= MAX_OUTCOMES
    )
    pairs = rows.join(rows, on="event_ticker", suffix="_b").select(
        a=pl.col("ticker"),
        b=pl.col("ticker_b"),
        open_a=pl.col("open_ms"),
        close_a=pl.col("close_ms"),
        open_b=pl.col("open_ms_b"),
        close_b=pl.col("close_ms_b"),
        group=pl.col("event_ticker"),
    )
    return _finish(pairs, "outcomes", pl.lit(-1))


def _game(d: pl.DataFrame) -> pl.DataFrame:
    """Each market of a game with the central market of every other series for its subject: the
    team named in its last part, or the total when it names none."""
    rows = d.filter(
        pl.col("game").is_not_null()
        & (pl.col("parts") == 3)
        & pl.col("what").is_in(["threshold", "outcome"])
    )
    team = pl.col("last").str.replace(LINE, "")
    rows = rows.with_columns(
        subject=pl.when(pl.col("what") == "outcome")
        .then(pl.col("last"))
        .when(team == "")
        .then(pl.lit(TOTAL))
        .otherwise(team)
    )
    keys = ["game", "series_ticker", "subject"]
    central = (
        rows.sort([*keys, "level", "ticker"])
        .with_columns(i=pl.int_range(pl.len()).over(keys), n=pl.len().over(keys))
        .filter(pl.col("i") == (pl.col("n") - 1) // 2)
        .select(
            "game",
            "subject",
            series_b=pl.col("series_ticker"),
            b=pl.col("ticker"),
            open_b=pl.col("open_ms"),
            close_b=pl.col("close_ms"),
        )
    )
    pairs = (
        rows.join(central, on=["game", "subject"])
        .filter(pl.col("series_ticker") != pl.col("series_b"))
        .select(
            a=pl.col("ticker"),
            b=pl.col("b"),
            open_a=pl.col("open_ms"),
            close_a=pl.col("close_ms"),
            open_b=pl.col("open_b"),
            close_b=pl.col("close_b"),
            group=pl.col("game"),
        )
    )
    return _finish(pairs, "game", pl.lit(1))


def stems(series: pl.DataFrame) -> pl.DataFrame:
    """``(series_ticker, stem)`` for every series whose stem (itself without a ``15M`` or a final
    horizon letter) is shared by another series of its category."""
    t = pl.col("series_ticker")
    candidates = (
        series.with_columns(
            stem=pl.concat_list(
                [
                    t,
                    pl.when(t.str.ends_with("15M")).then(t.str.head(-3)),
                    pl.when(t.str.contains(r"[DHUE]$")).then(t.str.head(-1)),
                ]
            )
        )
        .explode("stem")
        .drop_nulls("stem")
    )
    shared = (
        candidates.group_by("stem", "category")
        .agg(t.n_unique().alias("n"))
        .filter(pl.col("n") >= 2)
        .drop("n")
    )
    return (
        candidates.join(shared, on=["stem", "category"])
        .sort(["series_ticker", pl.col("stem").str.len_chars()], descending=[False, True])
        .unique("series_ticker", keep="first", maintain_order=True)
        .select("series_ticker", "stem")
    )


def _horizon_rows(d: pl.DataFrame) -> pl.DataFrame:
    """The markets a horizon pair can hold: thresholds with a level, outside sports."""
    return d.filter(
        (pl.col("what") == "threshold")
        & pl.col("level").is_not_null()
        & (pl.col("category") != "Sports")
    )


def _horizons(d: pl.DataFrame) -> pl.DataFrame:
    rows = _horizon_rows(d)
    rows = rows.join(stems(rows.select("series_ticker", "category").unique()), on="series_ticker")
    by_series = ["stem", "series_ticker"]
    events = (
        rows.group_by([*by_series, "event_ticker"])
        .agg(pl.col("open_ms").min().alias("e_open"), pl.col("close_ms").max().alias("e_close"))
        .sort([*by_series, "e_close", "event_ticker"])
        .with_columns(prev_close=pl.col("e_close").shift(1).over(by_series))
        .select(
            "stem",
            series_b=pl.col("series_ticker"),
            event_b=pl.col("event_ticker"),
            w_from=pl.max_horizontal("e_open", pl.col("prev_close").fill_null(pl.col("e_open"))),
            w_to=pl.col("e_close"),
        )
    )
    a = rows.select(
        a=pl.col("ticker"),
        stem=pl.col("stem"),
        series_a=pl.col("series_ticker"),
        level_a=pl.col("level"),
        direction_a=pl.col("direction"),
        open_a=pl.col("open_ms"),
        close_a=pl.col("close_ms"),
    )
    b = rows.select(
        event_b=pl.col("event_ticker"),
        b=pl.col("ticker"),
        level_b=pl.col("level"),
        direction_b=pl.col("direction"),
        open_b=pl.col("open_ms"),
        close_b=pl.col("close_ms"),
    )
    pairs = (
        a.join(events, on="stem")
        .filter(
            (pl.col("series_a") != pl.col("series_b"))
            & (pl.col("w_from") < pl.col("close_a"))
            & (pl.col("w_to") > pl.col("open_a"))
        )
        .join(b, on="event_b")
        .with_columns(diff=(pl.col("level_b") - pl.col("level_a")).abs())
        .sort(["a", "event_b", "diff", "level_b"])
        .unique(["a", "event_b"], keep="first", maintain_order=True)
        .with_columns(group=pl.col("stem"))
    )
    return _finish(pairs, "horizons", pl.col("direction_a") * pl.col("direction_b"))


def _station_day(rows: pl.DataFrame) -> pl.DataFrame:
    """Each weather market's group, the station code and the day, from the rules text and the
    event ticker; markets naming neither are dropped."""
    codes = {rules: code_in(rules) for rules in rows["weather_rules"].unique().to_list()}
    days: dict[str, str | None] = {}
    for event in rows["event_ticker"].unique().to_list():
        try:
            days[event] = market_day(event).isoformat()
        except WeatherDataError:
            days[event] = None
    return (
        rows.with_columns(
            code=pl.col("weather_rules").replace_strict(
                list(codes), list(codes.values()), default=None, return_dtype=pl.String
            ),
            day=pl.col("event_ticker").replace_strict(
                list(days), list(days.values()), default=None, return_dtype=pl.String
            ),
        )
        .drop_nulls(["code", "day"])
        .with_columns(group=pl.concat_str([pl.col("code"), pl.col("day")], separator=" "))
    )


def _weather(d: pl.DataFrame) -> pl.DataFrame:
    rows = _station_day(d.filter(pl.col("weather_rules").is_not_null()))
    pairs = (
        rows.join(rows, on="group", suffix="_b")
        .filter(pl.col("series_ticker") != pl.col("series_ticker_b"))
        .select(
            a=pl.col("ticker"),
            b=pl.col("ticker_b"),
            open_a=pl.col("open_ms"),
            close_a=pl.col("close_ms"),
            open_b=pl.col("open_ms_b"),
            close_b=pl.col("close_ms_b"),
            group=pl.col("group"),
        )
    )
    return _finish(pairs, "weather", pl.lit(None))


BUILDERS = {
    "thresholds": _thresholds,
    "outcomes": _outcomes,
    "game": _game,
    "horizons": _horizons,
    "brackets": _brackets,
    "weather": _weather,
}


def pairs(
    markets: pl.DataFrame, active: Iterable[str] | None = None
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Every pair of every kind among ``markets`` (those in ``active`` when given: the markets
    with frames in the span), with the coverage: pairs, markets and groups by kind, the horizon
    stems, and the active markets no kind relates."""
    if active is not None:
        markets = markets.filter(pl.col("ticker").is_in(list(active)))
    d = describe(markets.unique("ticker"))
    built = pl.concat([BUILDERS[kind](d).select(PAIR_COLUMNS) for kind in KINDS])
    built = built.unique(["a", "b", "kind", "valid_from_ms"], maintain_order=True).sort(
        ["kind", "a", "valid_from_ms", "b"]
    )
    by_kind = {
        row["kind"]: {k: row[k] for k in ("pairs", "markets", "groups")}
        for row in built.group_by("kind")
        .agg(
            pl.len().alias("pairs"),
            pl.col("a").n_unique().alias("markets"),
            pl.col("group").n_unique().alias("groups"),
        )
        .sort("kind")
        .to_dicts()
    }
    stem_table = stems(_horizon_rows(d).select("series_ticker", "category").unique())
    horizon_stems = {
        row["stem"]: row["series"]
        for row in stem_table.group_by("stem")
        .agg(pl.col("series_ticker").sort().alias("series"))
        .sort("stem")
        .to_dicts()
    }
    related = set(built["a"].to_list())
    unrelated = d.filter(~pl.col("ticker").is_in(list(related)))
    coverage = {
        "markets": d.height,
        "related": len(related),
        "by_kind": {
            kind: by_kind.get(kind, {"pairs": 0, "markets": 0, "groups": 0}) for kind in KINDS
        },
        "horizon_stems": horizon_stems,
        "unrelated": unrelated.height,
        "unrelated_top_series": {
            row["series_ticker"]: row["n"]
            for row in unrelated.group_by("series_ticker")
            .agg(pl.len().alias("n"))
            .sort(["n", "series_ticker"], descending=[True, False])
            .head(15)
            .to_dicts()
        },
    }
    return built, coverage
