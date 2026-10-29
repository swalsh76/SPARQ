"""S4.2: the registered test (PLAN §6.4, T6 and T7), run once.

The rules were registered in ``docs/research/paper-rules.json`` on 2026-10-29, after the fit part
(S4.1), and the file's digest is pinned here: the test refuses a file that changed. It replays the
quoter over the test window exactly as the rules say (5 cents inside, markets closing within 7 days
of the quote, the states, categories and sizes of each rule), on whole UTC days after the
registration, at least seven of them kept (a day with an outage over ten minutes leaves, T6), and
marks every fill to **settlement** (yes $1, no $0, from the ingest's market table copied with
``live extract``: no API call) and to the mid an hour later. Fills whose market has not settled are
reported apart and take no part in the bar. A rule is **GO** when its settlement-marked net a day
clears $25 at the lower bound of the (event, day) cluster-bootstrap interval and its 1-hour mark is
positive (T7).
"""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from sparq_core.fixedpoint import E6

from sparq_analytics.paper import quoter
from sparq_analytics.survey.dump import BAR_E6, CENTS, SurveyDataError
from sparq_analytics.survey.lag import a_day
from sparq_analytics.survey.queue import span

DIGEST = "6c7a87357267391093bce9c4acb2cb5876f3492bb063de9a6a6da4aecccdc3d7"
MIN_DAYS = 7
MARKS = ("1h", "settle")


def registered(path: Path) -> dict[str, Any]:
    """The registered rules, refused when the file is not the one the owner approved."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != DIGEST:
        raise SurveyDataError(f"{path} is not the registered rules file (digest {digest[:12]}…)")
    rules: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return rules


def window(live: Path, first: date, last: date, dropped_min_days: int) -> tuple[datetime, datetime]:
    """The whole UTC days ``first`` to ``last`` as a span the live copies cover."""
    if last < first:
        raise SurveyDataError(f"the window ends ({last}) before it starts ({first})")
    start = datetime.combine(first, datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(last + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    copied_start, copied_end = span(live)
    if start < copied_start or end > copied_end:
        raise SurveyDataError(
            f"the live copies run {copied_start:%Y-%m-%dT%H} to {copied_end:%Y-%m-%dT%H}; the"
            f" window {first} to {last} is not inside them"
        )
    if (last - first).days + 1 < dropped_min_days:
        raise SurveyDataError(f"the window holds fewer than {dropped_min_days} days")
    return start, end


def settlements(live: Path) -> pl.DataFrame:
    """Each settled market's settlement price (yes $1, no $0) and time."""
    path = live / "settlements.parquet"
    if not path.exists():
        raise SurveyDataError(f"no settlements under {live}; run live extract")
    return pl.read_parquet(path).select(
        "ticker",
        settle_e6=pl.when(pl.col("result") == "yes").then(E6).otherwise(0),
        settled_ms=pl.col("settled_ms"),
    )


def judge(
    fills: pl.DataFrame, times: pl.DataFrame, rules: dict[str, Any], hours: int
) -> dict[str, Any]:
    """Each rule against T7's bar, on its own fills (state, categories, size)."""
    priced = quoter._pnl(fills, MARKS)
    out: dict[str, Any] = {}
    for rule in rules["rules"]:
        s = rule["size_e2"]
        mine = priced.filter(pl.col("state") == rule["state"])
        if rule["categories"] != "all":
            mine = mine.filter(pl.col("category").is_in(rule["categories"]))
        settled = mine.filter(pl.col("settle_e6").is_not_null())
        net_settle = int(settled[f"net_settle_{s}"].sum() or 0)
        gross_settle = int(settled[f"gross_settle_{s}"].sum() or 0)
        net_1h = int(mine[f"net_1h_{s}"].sum() or 0)
        netted = (
            int(settled.filter(pl.col(f"net_settle_{s}").is_not_null())[f"fill_e2_{s}"].sum() or 0)
            / 100
        )
        interval = quoter._interval(settled, f"net_settle_{s}", hours)
        lower = interval["lower"]
        quote_ms = (
            times.filter(pl.col("state") == rule["state"])
            .filter(
                pl.lit(True)
                if rule["categories"] == "all"
                else pl.col("category").is_in(rule["categories"])
            )["quote_ms"]
            .sum()
        )
        out[rule["id"]] = {
            "state": rule["state"],
            "categories": rule["categories"],
            "size": s // 100,
            "fills": mine.height,
            "markets": mine["ticker"].n_unique(),
            "settled_fills": settled.height,
            "unsettled_fills": mine.height - settled.height,
            "contracts_settled": netted,
            "quote_hours_a_day": quote_ms / 3_600_000 * 24 / hours,
            "fee_unknown_fills": int(mine["fee_unknown"].sum()),
            "settlement": {
                "gross_e6_a_day": a_day(gross_settle, hours),
                "net_e6_a_day": a_day(net_settle, hours),
                "cents_a_contract": None if netted == 0 else net_settle / netted / CENTS,
                "interval_e6_a_day": interval,
            },
            "mid_1h": {
                "marked_fills": int(mine[f"gross_1h_{s}"].is_not_null().sum()),
                "net_e6_a_day": a_day(net_1h, hours),
            },
            "bar_e6_a_day": BAR_E6,
            "go": bool(lower is not None and lower >= BAR_E6 and net_1h > 0),
        }
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
    start, end = window(live, first, last, min_days)
    shared = rules["shared"]
    scope = quoter.Scope(inside=(shared["inside_e6"],), close_within_ms=shared["close_within_ms"])
    played = quoter.replay(live, fees, start, end, scope, log)
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
