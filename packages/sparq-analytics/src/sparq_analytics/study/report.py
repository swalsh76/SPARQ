"""Markdown tables for the write-up, from the JSON files the commands leave in the study folder.

Only tables: the reading of them is written by hand in ``docs/research/``. Edge figures are shown in
cents per contract (the files hold millionths of a dollar).
"""

import json
from pathlib import Path
from typing import Any

from sparq_analytics.study.config import CENT_E6


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def cents(value_e6: float | None) -> str:
    if value_e6 is None or value_e6 != value_e6:
        return "n/a"
    return f"{value_e6 / CENT_E6:+.2f}"


def with_bound(estimate: dict[str, Any]) -> str:
    return f"{cents(estimate['point_e6'])} ({cents(estimate['lower_e6'])})"


def _load(folder: Path, name: str) -> Any:
    path = folder / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _percent(share: float | None) -> str:
    return "n/a" if share is None else f"{100 * share:.2f}%"


def snapshot_section(snapshot: dict[str, Any]) -> str:
    days = snapshot["trade_days"]
    total = sum(d["trades"] for d in days)
    suggested = snapshot["suggested"]
    rows = [[reason, f"{count:,}"] for reason, count in sorted(snapshot["exclusions"].items())]
    first = days[0]["day"] if days else "n/a"
    last = days[-1]["day"] if days else "n/a"
    text = [
        f"Trade days {first} to {last}: {len(days)} days, {total:,} trades (polars "
        f"{snapshot['polars_version']}).",
        "",
        table(["Reason", "Trades"], rows),
        "",
        f"Baseline share of trades without a market record "
        f"{_percent(suggested.get('baseline_missing_share'))}, limit "
        f"{_percent(suggested.get('missing_limit'))}.",
        f"Suggested window {suggested['window_start']} to {suggested['window_end']} "
        "(end exclusive), "
        f"split dates {suggested.get('fit_end')} and {suggested.get('validate_end')}, "
        f"base sample share {suggested.get('sample_share')}, "
        f"{suggested.get('used_trades', 'n/a')} trades in the window.",
    ]
    categories = suggested.get("categories")
    if categories:
        shares = suggested.get("category_shares") or {}
        base = suggested.get("sample_share")
        rows = [
            [
                c["category"],
                f"{c['events']:,}",
                f"{c['trades']:,}",
                f"{shares.get(c['category'], base)}",
                f"{round(c['trades'] * shares.get(c['category'], base)):,}",
            ]
            for c in categories
        ]
        text += [
            "",
            "Sample by category (D16 amended; about "
            f"{suggested.get('expected_trades', 0):,} trades in all, ceiling "
            f"{suggested.get('ceiling', 0):,}):",
            "",
            table(["Category", "Events", "Trades", "Share", "Loaded"], rows),
        ]
    return "\n".join(text)


def reference_section(rows: list[dict[str, Any]]) -> str:
    body = [
        [
            r["category"],
            f"{r['markets']:,}",
            f"{r['close_median']:.0f}",
            f"{r['close_p10']:.0f} to {r['close_p90']:.0f}",
            f"{r['expected_median']:.0f}",
            f"{r['expected_p10']:.0f} to {r['expected_p90']:.0f}",
            r["nearer"],
        ]
        for r in rows
    ]
    headers = [
        "Category",
        "Markets",
        "close_time median s",
        "close 10th to 90th",
        "expected median s",
        "expected 10th to 90th",
        "Nearer",
    ]
    return table(headers, body)


def calibration_section(rows: list[dict[str, Any]], key: str) -> str:
    body = [
        [
            r[key],
            f"{r['trades']:,}",
            f"{r['events']:,}",
            f"{r['mean_price']:.4f}",
            f"{r['yes_rate']:.4f}",
            f"{r['gap'] * 100:+.2f}",
        ]
        for r in rows
    ]
    return table([key, "Trades", "Events", "Mean price", "Yes rate", "Gap (cents)"], body)


def ladder_section(ladder: dict[str, Any]) -> str:
    raw, banded = ladder["raw"], ladder["band_corrected"]
    rows = [
        ["Raw price", f"{raw['brier']:.5f}", f"{raw['log_loss']:.5f}"],
        [
            "Price plus the band's average miss",
            f"{banded['brier']:.5f}",
            f"{banded['log_loss']:.5f}",
        ],
    ]
    maker = (
        f"Naive maker, conservative fee: {cents(ladder['maker_optimistic_e6'])} cents a contract "
        f"under the optimistic fill rule, {cents(ladder['maker_pessimistic_e6'])} under the "
        "pessimistic one."
    )
    return table(["Step", "Brier", "Log loss"], rows) + "\n\n" + maker


def verdict_section(verdict: dict[str, Any]) -> str:
    rows = []
    for index, result in enumerate(verdict["segments"], start=1):
        est = result["estimates"]
        rows.append(
            [
                index,
                json.dumps({k: v for k, v in result["segment"].items() if v not in (None, 0)}),
                f"{result['events']:,}",
                with_bound(est["taker"]),
                with_bound(est["mk_pess_cons"]),
                with_bound(est["mk_opt_cons"]),
                ("yes" if result["taker_go"] or result["maker_go"] else "no")
                + (" (exploratory, does not decide)" if result["exploratory"] else ""),
            ]
        )
    headers = [
        "#",
        "Segment",
        "Events",
        "Taker, cents (lower bound)",
        "Maker pessimistic",
        "Maker optimistic",
        "GO",
    ]
    return (
        f"**Verdict: {verdict['outcome']}** (each segment tested at a one-sided tail of "
        f"{verdict['tail']:.5f}).\n\n" + table(headers, rows)
    )


def rules_section(verdict: dict[str, Any], part: str) -> str:
    """The rules' table (S1.1): what decides each rule, with the other measures for context."""
    rows = []
    for result in verdict["results"]:
        rule, est = result["rule"], result["estimates"]
        decides = est["decides"]
        verdict_cell = "yes" if result["go"] else "no"
        if result["short_of_events"]:
            verdict_cell += " (short of events)"
        if rule["exploratory"]:
            verdict_cell += " (exploratory, does not decide)"
        rows.append(
            [
                rule["name"],
                rule["side"],
                f"{decides['events']:,}",
                f"{result['trades']:,}",
                with_bound(decides),
                with_bound(est["maker"]),
                cents(est["maker_optimistic"]["point_e6"]),
                with_bound(est["taker"]),
                verdict_cell,
            ]
        )
    headers = [
        "Rule",
        "Scored for",
        "Events",
        "Trades",
        "Decides, cents (lower bound)",
        "Maker strict",
        "Maker optimistic",
        "Taker",
        "GO",
    ]
    return (
        f"**Rules on the {part} part: {verdict['outcome']}** (each rule at a one-sided tail of "
        f"{verdict['tail']:.5f}).\n\n" + table(headers, rows)
    )


def scan_section(rows: list[dict[str, Any]], chosen: str | None) -> str:
    """S1.1b's scan: every variant on the fit and validate parts, best fit lower bound first."""
    ordered = sorted(rows, key=lambda row: -row["fit"]["lower_e6"])
    body = []
    for row in ordered:
        eligible = row["fit"]["point_e6"] > 0 and (
            row["validate"]["point_e6"] >= 0.5 * row["fit"]["point_e6"]
        )
        mark = "**chosen**" if row["rule"]["name"] == chosen else ("eligible" if eligible else "")
        body.append(
            [
                row["rule"]["name"],
                row["changes"],
                f"{row['fit']['events']:,}",
                with_bound(row["fit"]),
                f"{row['validate']['events']:,}",
                with_bound(row["validate"]),
                mark,
            ]
        )
    headers = ["Variant", "Changes", "Fit events", "Fit", "Validate events", "Validate", ""]
    verdict = f"chosen: {chosen}" if chosen else "no variant is eligible"
    return (
        f"**Scan of {len(rows)} variants ({verdict}).** Cents a contract after fees, lower bound "
        "at a one-sided 0.025 in brackets.\n\n" + table(headers, body)
    )


def render(folder: Path) -> str:
    """Every section whose file exists, as one markdown text."""
    parts: list[str] = []
    snapshot = _load(folder, "snapshot.json")
    if snapshot:
        parts += ["## Data snapshot", snapshot_section(snapshot)]
    reference = _load(folder, "reference.json")
    if reference:
        parts += ["## Which end time is nearest the last trade", reference_section(reference)]
    explore = _load(folder, "explore.json")
    if explore:
        split = explore["split"]["events"]
        parts += [
            "## The run's split and exclusions",
            f"Events: {split}. Window {explore['config']['window_start']} to "
            f"{explore['config']['window_end']}, sample share {explore['config']['sample_share']}, "
            f"seed {explore['config']['sample_seed']}.",
            table(
                ["Reason", "Trades"],
                [[k, f"{v:,}"] for k, v in sorted(explore["exclusions"].items())],
            ),
            "## Calibration by price band (validate part)",
            calibration_section(explore["calibration_validate"], "price_band"),
            "## Benchmark ladder (fitted on fit, scored on validate)",
            ladder_section(explore["ladder_validate"]),
        ]
    verdict = _load(folder, "verdict.json")
    if verdict:
        parts += [
            "## Benchmark ladder (fitted on fit, scored on test)",
            ladder_section(verdict["ladder"]),
            "## Verdict on the test part",
            verdict_section(verdict["verdict"]),
        ]
    for name, part in (("rules-validate.json", "validate"), ("rules-verdict.json", "test")):
        rules = _load(folder, name)
        if rules:
            parts += [f"## Rules on the {part} part", rules_section(rules["verdict"], part)]
    scan = _load(folder, "rules-scan.json")
    if scan:
        parts += ["## The scan (fit and validate)", scan_section(scan["rows"], scan["chosen"])]
    forward = _load(folder, "forward-verdict.json")
    if forward:
        parts += ["## The forward test", rules_section(forward["verdict"], "forward")]
    return "\n\n".join(parts) + "\n"
