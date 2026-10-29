"""S5.1: can the model read a weather market's rules text? (PLAN §6.5, N6.)

For every weather market of S2's window (2026-08-01 to 2026-10-10, the days before S2's test part,
which stays unopened; no outcome is read) the model is shown the rules text and asked for a JSON
object: the climate site or station it names, the direction (greater, less or between) and the
numbers. The answer is compared with what the repository already knows:

* **the site**, against ``weather.stations.code_in`` (the climate code or station id the rules
  name, mapped to a site): over the markets whose rules name one. A market whose rules name none
  is counted apart, with how often the model named one anyway;
* **the bounds**, against Kalshi's own strike fields (``strike_type``, ``floor_strike``,
  ``cap_strike``): over the markets that have a strike type.

A market whose rules name a place but no code (every one of 2024 and 2025, and some of 2026) has
no site to compare. What the model answers there is classified apart, against the one code the
market's series uses where its named markets show one (S2.1's rule): **declined** (null), **right**,
**wrong**, **other** (a name the map does not know), **unmapped** (the series has no single code) or
**unparsed**.

A reply that is missing, truncated or not the object asked for counts as disagreement. The bar (N6):
site and bounds each agree in at least 99% of markets with a 95% lower bound of at least 98%
(cluster bootstrap over events), in the highs and in the rain markets separately, and at least
99.5% of replies parse. A sample of earlier markets (before 2026-08-01) is read too, with no bar:
their texts are older and less regular, which is the closest this check comes to text no one
engineered for.
"""

import hashlib
import json
import re
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from sparq_analytics.model.client import ModelClient, ModelError, Reply
from sparq_analytics.model.prompts import READ_WEATHER
from sparq_analytics.study.bootstrap import cluster_bootstrap
from sparq_analytics.study.dataset import scan_markets
from sparq_analytics.survey.dump import RESAMPLES, TAIL
from sparq_analytics.weather.check import strike_value
from sparq_analytics.weather.stations import (
    BY_STATION,
    RAIN_SERIES,
    SCOPE,
    SITES,
    TEST_START,
    WeatherDataError,
    code_in,
    market_day,
)

READ_FIRST = date(2026, 8, 1)  # S2's first day
READ_LAST = TEST_START - timedelta(days=1)
OLDER_SAMPLE = 500
SEED = 35
AGREE, AGREE_LOWER, PARSE = 0.99, 0.98, 0.995  # N6
MIN_EVENTS = 10  # a group is judged only with this many events, as in S3.3
ABORT_AFTER = 20  # consecutive failures to reach the model node before the run stops
PROGRESS_EVERY = 500  # markets between progress lines
EXAMPLES = 10
MARKET_COLUMNS = [
    "ticker",
    "series_ticker",
    "event_ticker",
    "strike_type",
    "floor_strike",
    "cap_strike",
    "rules_primary",
]
UNNAMED = ("declined", "right", "wrong", "other", "unmapped", "unparsed")
OBJECT = re.compile(r"\{.*?\}", re.S)
READ_WEATHER_HASH = hashlib.sha256(READ_WEATHER.encode()).hexdigest()
ROW_SCHEMA = {
    "ticker": pl.String,
    "event_ticker": pl.String,
    "group": pl.String,
    "sample": pl.String,
    "site_truth": pl.String,
    "strike_type": pl.String,
    "floor": pl.Float64,
    "cap": pl.Float64,
    "error": pl.String,
    "content": pl.String,
    "truncated": pl.Boolean,
    "cached": pl.Boolean,
    "seconds": pl.Float64,
    "tokens": pl.Int64,
    "parsed": pl.Boolean,
    "site_answer": pl.String,
    "site_agree": pl.Boolean,
    "series_site": pl.String,
    "unnamed_answer": pl.String,
    "bounds_agree": pl.Boolean,
}


def reading_markets(kalshi: Path, first: date, last: date) -> pl.DataFrame:
    """The weather markets with a day from ``first`` to ``last`` (inclusive), with their rules, the
    site the rules name and the strike fields; refuses any day in S2's test part."""
    if last >= TEST_START:
        raise WeatherDataError(
            f"{last} is in S2's test part (from {TEST_START}), which stays unopened"
        )
    markets = (
        scan_markets(kalshi, MARKET_COLUMNS)
        .filter(pl.col("series_ticker").is_in(sorted(SCOPE)))
        .collect()
    )
    rows = []
    for row in markets.iter_rows(named=True):
        if not first <= (day := market_day(row["event_ticker"])) <= last:
            continue
        rows.append(
            {
                "ticker": row["ticker"],
                "series": row["series_ticker"],
                "event_ticker": row["event_ticker"],
                "group": "rain" if row["series_ticker"] == RAIN_SERIES else "highs",
                "day": day,
                "strike_type": row["strike_type"],
                "floor": strike_value(row["floor_strike"]),
                "cap": strike_value(row["cap_strike"]),
                "rules": row["rules_primary"] or "",
                "site": code_in(row["rules_primary"]),
            }
        )
    schema = {
        "ticker": pl.String,
        "series": pl.String,
        "event_ticker": pl.String,
        "group": pl.String,
        "day": pl.Date,
        "strike_type": pl.String,
        "floor": pl.Float64,
        "cap": pl.Float64,
        "rules": pl.String,
        "site": pl.String,
    }
    return pl.DataFrame(rows, schema=schema).sort("ticker")


def sample(markets: pl.DataFrame, limit: int | None) -> pl.DataFrame:
    """A seeded shuffle, cut to ``limit``; the same markets every run."""
    shuffled = markets.sample(fraction=1.0, shuffle=True, seed=SEED)
    return shuffled if limit is None else shuffled.head(limit)


def parse_reply(content: str) -> dict[str, Any] | None:
    """The JSON object in a reply (it may sit in a code fence or after a line of prose), or None."""
    for found in OBJECT.finditer(content):
        try:
            value = json.loads(found.group(0))
        except ValueError:
            continue
        if isinstance(value, dict) and {"site", "direction", "lower", "upper"} <= value.keys():
            return value
    return None


def site_of(answer: Any) -> str | None:
    """The site an answer names (a climate code, or a station id of the map), or None."""
    code = str(answer or "").strip().upper()
    if code in SITES:
        return code
    return BY_STATION.get(code)


def number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def same(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) < 1e-9


def bounds_agree(
    strike_type: str, floor: float | None, cap: float | None, answer: dict[str, Any]
) -> bool:
    """Whether the answer's direction and numbers are the strike fields' (a greater market has only
    its floor, a less market only its cap)."""
    if answer["direction"] != strike_type:
        return False
    lower, upper = number(answer["lower"]), number(answer["upper"])
    if strike_type == "greater":
        return same(lower, floor)
    if strike_type == "less":
        return same(upper, cap)
    return strike_type == "between" and same(lower, floor) and same(upper, cap)


def series_sites(markets: pl.DataFrame) -> pl.DataFrame:
    """Each series' one code, where the markets of ``markets`` that name a site name the same."""
    return (
        markets.filter(pl.col("site").is_not_null())
        .group_by("series")
        .agg(pl.col("site").unique().alias("sites"))
        .filter(pl.col("sites").list.len() == 1)
        .select("series", series_site=pl.col("sites").list.first())
    )


def classify_unnamed(answer: dict[str, Any] | None, series_site: str | None) -> str:
    """What the model answered for a market whose rules name no site."""
    if answer is None:
        return "unparsed"
    given = answer["site"]
    if given is None or str(given).strip().lower() in ("", "null", "none"):
        return "declined"
    if (code := site_of(given)) is None:
        return "other"
    if series_site is None:
        return "unmapped"
    return "right" if code == series_site else "wrong"


def read_one(client: ModelClient, row: dict[str, Any]) -> dict[str, Any]:
    """One market: the model's reply and how it compares."""
    base: dict[str, Any] = {
        "ticker": row["ticker"],
        "event_ticker": row["event_ticker"],
        "group": row["group"],
        "sample": row["sample"],
        "site_truth": row["site"],
        "series_site": row.get("series_site"),
        "strike_type": row["strike_type"],
        "floor": row["floor"],
        "cap": row["cap"],
    }
    try:
        reply = client.chat(READ_WEATHER.format(rules=row["rules"]))
        error = None
    except ModelError as problem:
        reply, error = Reply("", "", 0, 0.0, False), str(problem)
    answer = parse_reply(reply.content)
    named = row["site"] is not None
    known = row["strike_type"] is not None
    answered = site_of(answer["site"]) if answer is not None else None
    return base | {
        "error": error,
        "content": reply.content,
        "truncated": reply.truncated,
        "cached": reply.cached,
        "seconds": reply.seconds,
        "tokens": reply.completion_tokens,
        "parsed": answer is not None,
        "site_answer": answered,
        "site_agree": (answered == row["site"]) if named else None,
        "unnamed_answer": None if named else classify_unnamed(answer, row.get("series_site")),
        "bounds_agree": (
            answer is not None
            and bounds_agree(row["strike_type"], row["floor"], row["cap"], answer)
        )
        if known
        else None,
    }


def run_read(
    client: ModelClient,
    markets: pl.DataFrame,
    workers: int = 1,
    log: Callable[[str], None] = lambda _: None,
) -> list[dict[str, Any]]:
    """Every market read through the model, ``workers`` at a time with at most twice that many
    requests in flight (so a node that has stopped answering is noticed within a few calls); stops
    when ``ABORT_AFTER`` requests in a row fail."""
    rows = iter(markets.to_dicts())
    total = markets.height
    done: list[dict[str, Any]] = []
    streak = 0
    pool = ThreadPoolExecutor(max_workers=workers)
    pending: set[Future[dict[str, Any]]] = set()

    def feed() -> None:
        while len(pending) < 2 * workers and (row := next(rows, None)) is not None:
            pending.add(pool.submit(read_one, client, row))

    try:
        feed()
        while pending:
            finished, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                pending.discard(future)
                result = future.result()
                done.append(result)
                streak = streak + 1 if result["error"] else 0
                if streak >= ABORT_AFTER:
                    raise ModelError(f"the model node stopped answering ({result['error']})")
                if len(done) % PROGRESS_EVERY == 0:
                    log(f"{len(done):,} of {total:,} markets read")
            feed()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return sorted(done, key=lambda r: r["ticker"])


def rate(frame: pl.DataFrame, flag: str) -> dict[str, Any]:
    """The share of rows where ``flag`` is true (nulls are not in the base), with the lower bound
    of a cluster bootstrap over events."""
    base = frame.filter(pl.col(flag).is_not_null())
    if base.is_empty():
        return {"n": 0, "agree": 0, "rate": None, "lower": None, "events": 0, "clears": False}
    by_event = (
        base.group_by("event_ticker")
        .agg(
            pl.col(flag).cast(pl.Float64).sum().alias("num"), pl.len().cast(pl.Float64).alias("den")
        )
        .sort("event_ticker")
    )
    lower = None
    if by_event.height >= 2:
        lower = cluster_bootstrap(
            by_event["num"].to_numpy(),
            by_event["den"].to_numpy(),
            tail=TAIL,
            resamples=RESAMPLES,
            seed=SEED,
        ).lower_e6
    agree = int(base[flag].sum())
    share = agree / base.height
    return {
        "n": base.height,
        "agree": agree,
        "rate": share,
        "lower": lower,
        "events": by_event.height,
        "clears": bool(
            by_event.height >= MIN_EVENTS
            and lower is not None
            and share >= AGREE
            and lower >= AGREE_LOWER
        ),
    }


def figures(frame: pl.DataFrame) -> dict[str, Any]:
    """One group's figures: replies, the site over the markets that name one, the bounds over those
    with a strike type, and examples of disagreement."""
    unnamed = frame.filter(pl.col("unnamed_answer").is_not_null())
    bad = frame.filter(
        ~pl.col("parsed")
        | pl.col("site_agree").eq(False)  # a null (nothing to compare) is not a disagreement
        | pl.col("bounds_agree").eq(False)
    )
    examples = [
        {
            "ticker": r["ticker"],
            "truth": [r["site_truth"], r["strike_type"], r["floor"], r["cap"]],
            "answer": r["content"][:240],
            "error": r["error"],
        }
        for r in bad.head(EXAMPLES).to_dicts()
    ]
    return {
        "markets": frame.height,
        "parsed": int(frame["parsed"].sum()),
        "parse_rate": frame["parsed"].mean() if frame.height else None,
        "truncated": int(frame["truncated"].sum()),
        "errors": int(frame["error"].is_not_null().sum()),
        "site": rate(frame, "site_agree"),
        "bounds": rate(frame, "bounds_agree"),
        "rules_naming_no_site": unnamed.height,
        "unnamed_answers": {
            kind: int((unnamed["unnamed_answer"] == kind).sum()) for kind in UNNAMED
        },
        "no_strike_type": int(frame["strike_type"].is_null().sum()),
        "disagreements": bad.height,
        "examples": examples,
    }


def frame_of(rows: list[dict[str, Any]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=ROW_SCHEMA)


def summarize(rows: list[dict[str, Any]], wall_seconds: float) -> dict[str, Any]:
    """N6's figures: by group for S2's window, the earlier sample apart, the speed, the verdict."""
    frame = frame_of(rows)
    window = frame.filter(pl.col("sample") == "window")
    groups = {g: figures(window.filter(pl.col("group") == g)) for g in ("highs", "rain")}
    spent = frame.filter(~pl.col("cached") & pl.col("error").is_null())
    parse_rate = int(window["parsed"].sum()) / window.height if window.height else None
    clears = {
        f"{g}_{part}": groups[g][part]["clears"] for g in groups for part in ("site", "bounds")
    }
    return {
        "window": [READ_FIRST.isoformat(), READ_LAST.isoformat()],
        "markets": window.height,
        "by_group": groups,
        "earlier_sample": figures(frame.filter(pl.col("sample") == "older")),
        "parse_rate": parse_rate,
        "calls": spent.height,
        "cached": int(frame["cached"].sum()),
        "wall_seconds": wall_seconds,
        "markets_a_minute": None if wall_seconds <= 0 else frame.height / (wall_seconds / 60),
        "tokens_a_second": (
            int(spent["tokens"].sum()) / float(spent["seconds"].sum())
            if spent.height and float(spent["seconds"].sum()) > 0
            else None
        ),
        "seconds_median": spent["seconds"].median() if spent.height else None,
        "tokens_median": spent["tokens"].median() if spent.height else None,
        "clears": clears,
        "parse_clears": bool(parse_rate is not None and parse_rate >= PARSE),
        "passes": bool(all(clears.values()) and parse_rate is not None and parse_rate >= PARSE),
    }
