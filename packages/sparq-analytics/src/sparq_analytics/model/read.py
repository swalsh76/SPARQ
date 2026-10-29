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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    "invented": pl.Boolean,
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


def read_one(client: ModelClient, row: dict[str, Any]) -> dict[str, Any]:
    """One market: the model's reply and how it compares."""
    base: dict[str, Any] = {
        "ticker": row["ticker"],
        "event_ticker": row["event_ticker"],
        "group": row["group"],
        "sample": row["sample"],
        "site_truth": row["site"],
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
        "invented": (answered is not None) if not named else None,
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
    """Every market read through the model, ``workers`` at a time; stops when the node stops
    answering (``ABORT_AFTER`` failures in a row)."""
    rows = markets.to_dicts()
    done: list[dict[str, Any]] = []
    streak = 0
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(read_one, client, row) for row in rows]
        for count, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            done.append(result)
            streak = streak + 1 if result["error"] else 0
            if streak >= ABORT_AFTER:
                raise ModelError(f"the model node stopped answering ({result['error']})")
            if count % PROGRESS_EVERY == 0:
                log(f"{count:,} of {len(rows):,} markets read")
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
    unnamed = frame.filter(pl.col("invented").is_not_null())
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
        "invented_site": int(unnamed["invented"].sum()) if unnamed.height else 0,
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
