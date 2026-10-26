"""S1.2a's summaries and its stop rule, from the per-trade and per-order measures (D11).

Each population is summarised the same way: the book the takers met, the markouts, the two fill
rules and the refills. Averages are per contract, over events, with K0.5's studentized cluster
bootstrap (a 95% interval, 0.025 on each side). The stop rule reads the forward rule's own
population: when more than ``stop_share`` of the levels its takers cleared are refilled within
``stop_within_ms``, bots are taken to be requoting already and S1.2 is not started (D11). It needs
``stop_min_orders`` cleared orders, or it says there are too few.
"""

import math
from dataclasses import asdict
from typing import Any, cast

import polars as pl

from sparq_analytics.live.book import BookConfig
from sparq_analytics.study.bootstrap import cluster_bootstrap
from sparq_analytics.study.report import cents, table
from sparq_analytics.study.rules import Rule

TAIL = 0.025
RULE_POPULATION = "forward rule"


def populations(rule: Rule) -> dict[str, pl.Expr]:
    """The forward rule's trades, and each tier inside the rule's price band for comparison."""
    lo, hi = rule.price_lo_e6, rule.price_hi_e6
    band = (pl.col("p") >= lo) & (pl.col("p") < hi)
    label = f"{lo // 10_000}-{hi // 10_000}c"
    return {
        RULE_POPULATION: pl.col("in_rule"),
        f"middle, {label}": (pl.col("tier") == "middle") & band,
        f"busy, {label}": (pl.col("tier") == "busy") & band,
        f"thin, {label}": (pl.col("tier") == "thin") & band,
    }


def estimate(frame: pl.DataFrame, value: str, *, resamples: int, seed: int) -> dict[str, Any]:
    """The contract-weighted mean of ``value`` with its interval over events."""
    rows = frame.filter(pl.col(value).is_not_null())
    per = rows.group_by(pl.coalesce("event_ticker", "ticker").alias("event")).agg(
        num=(pl.col(value) * pl.col("count_e2")).sum(), den=pl.col("count_e2").sum()
    )
    result = cluster_bootstrap(
        per["num"].cast(pl.Float64).to_numpy(),
        per["den"].cast(pl.Float64).to_numpy(),
        tail=TAIL,
        resamples=resamples,
        seed=seed,
    )
    return asdict(result)


def _share(series: pl.Series) -> float | None:
    value = series.drop_nulls().cast(pl.Float64).mean()
    return None if value is None else float(cast(float, value))


def _median(series: pl.Series) -> float | None:
    value = series.drop_nulls().cast(pl.Float64).median()
    return None if value is None else float(cast(float, value))


def summarize_population(
    trades: pl.DataFrame, bursts: pl.DataFrame, config: BookConfig, *, resamples: int, seed: int
) -> dict[str, Any]:
    def est(frame: pl.DataFrame, value: str) -> dict[str, Any]:
        return estimate(frame, value, resamples=resamples, seed=seed)

    joinable = trades.filter("joinable")
    strict, queue = joinable.filter("filled_strict"), joinable.filter("filled_queue")
    done = bursts.filter(pl.col("cleared") & pl.col("complete"))
    return {
        "trades": trades.height,
        "events": trades["event_ticker"].n_unique(),
        "markets": trades["ticker"].n_unique(),
        "contracts_e2": int(trades["count_e2"].sum()),
        "book_share": _share(trades["bid_b"].is_not_null() & trades["ask_b"].is_not_null()),
        "spread_median_e6": _median(trades["spread_b"]),
        "touched_median_e2": _median(trades["touched_b"]),
        "earned": est(trades, "earned_e6"),
        "markouts": {str(h): est(trades, f"mk_{h}") for h in config.markouts_ms},
        "fills": {
            "joinable": joinable.height,
            "strict_share": _share(joinable["filled_strict"]),
            "queue_share": _share(joinable["filled_queue"]),
            "strict": {str(h): est(strict, f"mk_{h}") for h in config.markouts_ms},
            "queue": {str(h): est(queue, f"mk_{h}") for h in config.markouts_ms},
        },
        "refills": {
            "orders": bursts.height,
            "consistent_share": _share(bursts["consistent"]),
            "cleared": done.height,
            "cleared_events": done["event_ticker"].n_unique(),
            "within": {str(w): _share(done[f"refill_{w}"]) for w in config.refills_ms},
        },
    }


def stop_rule(bursts: pl.DataFrame, config: BookConfig) -> dict[str, Any]:
    done = bursts.filter(pl.col("cleared") & pl.col("complete"))
    share = _share(done[f"refill_{config.stop_within_ms}"])
    if done.height < config.stop_min_orders or share is None:
        outcome = "too few"
    else:
        outcome = "stop" if share > config.stop_share else "go on"
    return {
        "outcome": outcome,
        "orders": done.height,
        "events": done["event_ticker"].n_unique(),
        "share": share,
        "within_ms": config.stop_within_ms,
        "limit": config.stop_share,
        "min_orders": config.stop_min_orders,
    }


def summarize(
    trades: pl.DataFrame,
    bursts: pl.DataFrame,
    rule: Rule,
    config: BookConfig,
    *,
    resamples: int = 2_000,
    seed: int = 1,
) -> dict[str, Any]:
    groups = {
        name: summarize_population(
            trades.filter(where), bursts.filter(where), config, resamples=resamples, seed=seed
        )
        for name, where in populations(rule).items()
    }
    return {
        "rule": rule.name,
        "config": asdict(config),
        "resamples": resamples,
        "populations": groups,
        "stop": stop_rule(bursts.filter(populations(rule)[RULE_POPULATION]), config),
    }


def _ci(e: dict[str, Any]) -> str:
    if e["events"] == 0:
        return "n/a"
    return f"{cents(e['point_e6'])} ({cents(e['lower_e6'])} to {cents(e['upper_e6'])})"


def _pct(share: float | None) -> str:
    return "n/a" if share is None or math.isnan(share) else f"{100 * share:.1f}%"


def _num(value: float | None, scale: float, digits: int) -> str:
    return "n/a" if value is None else f"{value / scale:.{digits}f}"


def _seconds(ms: int) -> str:
    return f"{ms / 1000:g} s"


def render(summary: dict[str, Any]) -> str:
    """The summary as markdown tables (cents a contract; intervals at 95%)."""
    config, groups, stop = summary["config"], summary["populations"], summary["stop"]
    horizons = [str(h) for h in config["markouts_ms"]]
    refills = [str(w) for w in config["refills_ms"]]
    names = list(groups)
    lines = [
        f"Rule: {summary['rule']}. Days: {', '.join(summary.get('days', []))}; data to "
        f"{summary.get('data_end', 'n/a')}; {summary.get('near_gaps', 0):,} trades near feed "
        "outages left out.",
        "",
        f"**Stop rule: {stop['outcome']}** ({_pct(stop['share'])} of {stop['orders']:,} cleared "
        f"levels over {stop['events']:,} events refilled within {_seconds(stop['within_ms'])}; "
        f"stop above {_pct(stop['limit'])}, at least {stop['min_orders']:,} orders).",
        "",
        "The book the takers met:",
        "",
        table(
            [
                "Population",
                "Trades",
                "Events",
                "Book shown",
                "Median spread, c",
                "Median size at level",
                "Half-spread earned, c",
            ],
            [
                [
                    n,
                    f"{g['trades']:,}",
                    f"{g['events']:,}",
                    _pct(g["book_share"]),
                    _num(g["spread_median_e6"], 10_000, 1),
                    _num(g["touched_median_e2"], 100, 0),
                    _ci(g["earned"]),
                ]
                for n, g in groups.items()
            ],
        ),
        "",
        "Markouts (the maker's result against the later mid, cents a contract):",
        "",
        table(
            ["Population", *[_seconds(int(h)) for h in horizons]],
            [[n, *[_ci(groups[n]["markouts"][h]) for h in horizons]] for n in names],
        ),
        "",
        f"Fills of an order joining {_seconds(config['join_delay_ms'])} after the trade, within "
        f"{_seconds(config['fill_horizon_ms'])}, and the markouts of the filled ones:",
        "",
        table(
            [
                "Population",
                "Joinable",
                "Strict fills",
                "Queue fills",
                *[f"strict {_seconds(int(h))}" for h in horizons],
                *[f"queue {_seconds(int(h))}" for h in horizons],
            ],
            [
                [
                    n,
                    f"{g['fills']['joinable']:,}",
                    _pct(g["fills"]["strict_share"]),
                    _pct(g["fills"]["queue_share"]),
                    *[_ci(g["fills"]["strict"][h]) for h in horizons],
                    *[_ci(g["fills"]["queue"][h]) for h in horizons],
                ]
                for n, g in groups.items()
            ],
        ),
        "",
        "Refills of the levels takers cleared:",
        "",
        table(
            [
                "Population",
                "Orders",
                "Frame matched",
                "Cleared",
                *[f"within {_seconds(int(w))}" for w in refills],
            ],
            [
                [
                    n,
                    f"{g['refills']['orders']:,}",
                    _pct(g["refills"]["consistent_share"]),
                    f"{g['refills']['cleared']:,}",
                    *[_pct(g["refills"]["within"][w]) for w in refills],
                ]
                for n, g in groups.items()
            ],
        ),
    ]
    return "\n".join(lines) + "\n"
