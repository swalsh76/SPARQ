"""S4.2: the registered test (PLAN §6.4, T6 and T7), run once.

The rules were registered in ``docs/research/paper-rules.json`` on 2026-10-29, after the fit part
(S4.1), and the file's digest is pinned here: the test refuses a file that changed, and a code
whose constants no longer match the file's. It replays the quoter over the test window exactly as
the rules say (5 cents inside, markets closing within 7 days of the quote, the states, categories
and sizes of each rule), on whole UTC days after the registration, at least seven of them kept (a
day with an outage over ten minutes leaves, T6), and marks every fill to **settlement** (yes $1,
no $0, from the ingest's market table copied with ``live extract``: no API call) and to the mid an
hour later. Fills whose market has not settled are reported apart and take no part in the bar. A
rule is **GO** when its settlement-marked net a day clears $25 at the lower bound of the (event,
day) cluster-bootstrap interval and its 1-hour mark is positive (T7).
"""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from sparq_core.fixedpoint import E6

from sparq_analytics.live.source import HOUR
from sparq_analytics.paper import quoter
from sparq_analytics.survey.dump import BAR_E6, SurveyDataError
from sparq_analytics.survey.lag import EXTREME_E6
from sparq_analytics.survey.queue import span

DIGEST = "6c7a87357267391093bce9c4acb2cb5876f3492bb063de9a6a6da4aecccdc3d7"
MIN_DAYS = 7
MARKS = ("1h", "settle")


def registered(path: Path) -> dict[str, Any]:
    """The registered rules, refused when the file is not the one the owner approved or the code's
    constants no longer match it."""
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != DIGEST:
        raise SurveyDataError(f"{path} is not the registered rules file (digest {digest[:12]}…)")
    rules: dict[str, Any] = json.loads(data)
    check_constants(rules["shared"])
    return rules


def check_constants(shared: dict[str, Any]) -> None:
    """The code's constants the rules rely on, as the file states them: a change un-registers the
    test and is refused."""
    expected = {
        "regime_e6": [EXTREME_E6, E6 - EXTREME_E6],
        "empty_side_e6": quoter.EMPTY_SIDE_E6,
        "outage_day_ms": quoter.OUTAGE_DAY_MS,
    }
    for key, value in expected.items():
        if shared.get(key) != value:
            raise SurveyDataError(
                f"the code's {key} ({value}) is not the registered rules' ({shared.get(key)})"
            )


def window(
    live: Path, first: date, last: date, min_days: int
) -> tuple[datetime, datetime, datetime]:
    """The whole UTC days ``first`` to ``last`` as a span the live copies cover, and the moment to
    which the marks may read (an hour past the window when the copies hold it)."""
    if last < first:
        raise SurveyDataError(f"the window ends ({last}) before it starts ({first})")
    if (last - first).days + 1 < min_days:
        raise SurveyDataError(f"the window holds fewer than {min_days} days")
    start = datetime.combine(first, datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    copied_start, copied_end = span(live)
    if start < copied_start or end > copied_end:
        raise SurveyDataError(
            f"the live copies run {copied_start:%Y-%m-%dT%H} to {copied_end:%Y-%m-%dT%H}; the"
            f" window {first} to {last} is not inside them"
        )
    return start, end, min(copied_end, end + HOUR)


def settlements(live: Path) -> pl.DataFrame:
    """Each settled market's settlement price (yes $1, no $0) and time."""
    path = live / "settlements.parquet"
    if not path.exists():
        raise SurveyDataError(f"no settlements under {live}; run live extract")
    yes = pl.col("result") == "yes"
    return pl.read_parquet(path).select(
        "ticker",
        settle_e6=pl.when(yes)
        .then(pl.lit(E6, dtype=pl.Int64))
        .otherwise(pl.lit(0, dtype=pl.Int64)),
        settled_ms=pl.col("settled_ms"),
    )


def judge(
    fills: pl.DataFrame, times: pl.DataFrame, rules: dict[str, Any], hours: int
) -> dict[str, Any]:
    """Each rule against T7's bar, on its own fills (state, categories, size), with the quoter's
    own cell figures at the settlement and 1-hour marks."""
    priced = quoter._pnl(fills, MARKS)
    k = rules["shared"]["inside_e6"]
    out: dict[str, Any] = {}
    for rule in rules["rules"]:
        in_rule = pl.col("state") == rule["state"]
        if rule["categories"] != "all":
            in_rule = in_rule & pl.col("category").is_in(rule["categories"])
        cells = quoter._cells(
            priced.filter(in_rule), times.filter(in_rule), ["state", "k_e6"], hours, True, MARKS
        )
        cell = cells.get(f"{rule['state']} | {k} | {rule['size_e2'] // 100}")
        verdict: dict[str, Any] = {
            "state": rule["state"],
            "categories": rule["categories"],
            "size": rule["size_e2"] // 100,
            "bar_e6_a_day": BAR_E6,
        }
        if cell is None:  # nothing quoted
            out[rule["id"]] = {**verdict, "fills": 0, "quotes": 0, "go": False}
            continue
        settle, hour = cell["marks"]["settle"], cell["marks"]["1h"]
        lower = settle.get("interval_e6_a_day", {}).get("lower")
        out[rule["id"]] = {
            **verdict,
            **{k_: cell[k_] for k_ in ("fills", "markets", "quotes", "quote_hours_a_day",
                                       "fills_a_quote_hour", "fee_unknown_fills")},
            "settled_fills": settle["marked_fills"],
            "unsettled_fills": cell["fills"] - settle["marked_fills"],
            "settlement": settle,
            "mid_1h": hour,
            "go": bool(lower is not None and lower >= BAR_E6 and hour["net_e6_a_day"] > 0),
        }  # fmt: skip
    return out


def run_test(
    live: Path,
    fees: pl.DataFrame,
    out: Path,
    rules_path: Path,
    first: date,
    last: date,
    min_days: int = MIN_DAYS,
    log: Callable[[str], None] = lambda _: None,
) -> dict[str, Any]:
    """The registered test over the window: the fills (with settlement marks) written to
    ``out/paper-test-fills.parquet``, the verdicts returned."""
    rules = registered(rules_path)
    start, end, marks_end = window(live, first, last, min_days)
    shared = rules["shared"]
    scope = quoter.Scope(inside=(shared["inside_e6"],), close_within_ms=shared["close_within_ms"])
    played = quoter.replay(live, fees, start, end, scope, log, marks_end)
    kept_days = (last - first).days + 1 - len(played.dropped)
    if kept_days < min_days:
        raise SurveyDataError(
            f"{kept_days} whole days kept of {(last - first).days + 1}, fewer than {min_days}"
            f" (dropped: {', '.join(d.isoformat() for d in played.dropped)})"
        )
    fills = played.fills.join(settlements(live), on="ticker", how="left").with_columns(
        mid_settle=pl.col("settle_e6")
    )
    out.mkdir(parents=True, exist_ok=True)
    fills.write_parquet(out / "paper-test-fills.parquet")
    verdicts = judge(fills, played.times, rules, played.hours)
    return {
        "rules": str(rules_path),
        "rules_digest": DIGEST,
        "window": {"first": first.isoformat(), "last": last.isoformat()},
        "hours_kept": played.hours,
        "days_kept": kept_days,
        "dropped_days": [d.isoformat() for d in played.dropped],
        "fills": fills.height,
        "settled_fills": int(fills["settle_e6"].is_not_null().sum()),
        "by_rule": verdicts,
        "go": sorted(k for k, v in verdicts.items() if v["go"]),
    }
