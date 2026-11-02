"""S5.2: does the model's probability add to the book's price? (PLAN §6.5, N5 to N7.)

**The population:** the settled binary markets closing on or before the cut (2026-10-10, M3)
whose day-before price (S3.3's: the last trade from 36 to 12 hours before the close) stood from 10
to 90 cents and that are thin (under 1,000 contracts a day over their days open in the history,
S3.3's tiers). **The draw** is seeded: at most ``PER_EVENT`` markets an event and ``PER_CATEGORY``
a category, at most ``SAMPLE`` in all; the earliest third by closing day is the fit part and the
rest the test part, the day fixed from the drawn closing times alone.

**forecast** shows the model each market's title, its yes and no outcomes, its rules and the date
of the day before the close, never the price, the volume or the outcome, and asks for the chance
of yes. It reads no outcome (the draw drops the result before anything else is done); a run cut
short by ``limit`` is a pilot, reported on its own and never scored.

**score** is the only step that reads outcomes. On the fit part it fits two logistic regressions
of the outcome by Newton's method: on the price's logit alone, and on it with the model's (its
probability clipped to ``CLIP``). The gain is the test part's log loss per market, the first less
the second, in nats, with the studentized cluster bootstrap over events, and by category and by
closing week (N5: a gain in the earliest weeks alone reads as leakage, not skill). **The placebo**
gives each market the model's probability of a market of another event in the same part, category
and price band, refits and scores, ``PLACEBOS`` times. **The bar** (N6): the lower bound above
zero and the gain at least ``MIN_GAIN``, on at least ``MIN_MARKETS`` test markets in at least
``MIN_EVENTS`` events, and the placebo gains' upper ``TAIL`` quantile below ``MIN_GAIN`` and below
the gain.
"""

import hashlib
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray
from sparq_core.fixedpoint import E6

from sparq_analytics.model.client import ModelClient
from sparq_analytics.model.prompts import FORECAST
from sparq_analytics.model.read import chat_or_error, parse_reply, speed
from sparq_analytics.study.bootstrap import cluster_bootstrap
from sparq_analytics.study.dataset import scan_markets
from sparq_analytics.survey.dump import (
    CUT,
    HISTORY_START,
    RESAMPLES,
    TAIL,
    UNKNOWN,
    SurveyDataError,
    scan_trades,
    trade_files,
)
from sparq_analytics.survey.lag import bucket
from sparq_analytics.survey.tail import THIN_TIERS, day_before_prices, tiered

BAND_LOW_E6, BAND_HIGH_E6 = 100_000, 900_000  # N6: a day-before price from 10 to 90 cents
BANDS: tuple[tuple[int | None, str], ...] = (
    (300_000, "10 to 30"),
    (500_000, "30 to 50"),
    (700_000, "50 to 70"),
    (None, "70 to 90"),
)
PER_EVENT, PER_CATEGORY, SAMPLE = 20, 700, 6_000  # N7
SEED = 36
CLIP = 0.01  # the model's probability is taken from 1% to 99% before its logit
MIN_GAIN = 0.002  # N6: nats a test market
MIN_MARKETS, MIN_EVENTS = 3_000, 300  # N6, over the test part
MIN_CELL_EVENTS = 10  # a category or week is judged only with this many events, as in S3.3
PLACEBOS = 200
NEWTON_STEPS, NEWTON_TOLERANCE = 100, 1e-10
KEYS = ("probability",)
TEXT_COLUMNS = [
    "ticker",
    "open_time",
    "title",
    "yes_sub_title",
    "no_sub_title",
    "rules_primary",
    "rules_secondary",
]
DRAWN_KEYS = (
    "ticker", "event_ticker", "series_ticker", "category", "band", "part", "rank", "price_e6",
    "close_ms",
)  # fmt: skip
FORECAST_HASH = hashlib.sha256(FORECAST.encode()).hexdigest()
ROW_SCHEMA = {
    "ticker": pl.String,
    "event_ticker": pl.String,
    "series_ticker": pl.String,
    "category": pl.String,
    "band": pl.String,
    "part": pl.String,
    "rank": pl.Int64,
    "price_e6": pl.Int64,
    "close_ms": pl.Int64,
    "error": pl.String,
    "content": pl.String,
    "truncated": pl.Boolean,
    "cached": pl.Boolean,
    "seconds": pl.Float64,
    "tokens": pl.Int64,
    "parsed": pl.Boolean,
    "probability": pl.Float64,
}


def midnight_ms(day: date) -> int:
    return int(datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)


def population(kalshi: Path, cut: date = CUT) -> pl.DataFrame:
    """The thin settled markets whose day-before price stood from 10 to 90 cents, with their text
    and price band, and without their result."""
    priced, _ = day_before_prices(kalshi, cut)
    price = pl.col("price_e6")
    band = priced.filter((price >= BAND_LOW_E6) & (price <= BAND_HIGH_E6)).drop("result")
    if band.is_empty():
        raise SurveyDataError(
            "no settled market stood from 10 to 90 cents the day before its close"
        )
    tickers = band.select("ticker").lazy()
    contracts = (
        scan_trades(trade_files(kalshi, HISTORY_START, cut))
        .join(tickers, on="ticker", how="semi")
        .group_by("ticker")
        .agg(pl.col("count_e2").sum().alias("contracts_e2"))
        .collect()
    )
    text = (
        scan_markets(kalshi, TEXT_COLUMNS, traded=tickers)
        .with_columns(open_ms=pl.col("open_time").dt.epoch("ms"))
        .drop("open_time")
        .collect()
    )
    joined = band.join(contracts, on="ticker", how="left").join(text, on="ticker", how="left")
    window = (midnight_ms(HISTORY_START), midnight_ms(cut + timedelta(days=1)))
    return (
        tiered(joined, *window)
        .filter(pl.col("tier").is_in(THIN_TIERS))
        .with_columns(pl.col("category").fill_null(UNKNOWN), band=bucket(price, BANDS))
        .select(
            "ticker",
            "event_ticker",
            "series_ticker",
            "category",
            "band",
            "price_e6",
            "close_ms",
            "contracts_a_day",
            *TEXT_COLUMNS[2:],
        )  # fmt: skip
        .sort("ticker")
    )


def draw(markets: pl.DataFrame) -> tuple[pl.DataFrame, date]:
    """The seeded sample: a shuffle, then the first ``PER_EVENT`` of each event and the first
    ``PER_CATEGORY`` of each category, at most ``SAMPLE``, with each market's ``rank`` in the
    draw; and the split day, the closing day of the market a third of the way through the sample
    by closing time (the fit part closes before it)."""
    if markets.is_empty():
        raise SurveyDataError("no market to draw from")
    order = np.random.default_rng(SEED).permutation(markets.height)
    shuffled = markets.sort("ticker").with_columns(order=pl.Series(order)).sort("order")
    first = pl.int_range(pl.len())
    kept = (
        shuffled.filter(first.over("event_ticker") < PER_EVENT)
        .filter(first.over("category") < PER_CATEGORY)
        .head(SAMPLE)
        .drop("order")
        .with_columns(rank=pl.int_range(pl.len()))
        .with_columns(close_day=pl.from_epoch("close_ms", time_unit="ms").dt.date())
    )
    split: date = kept["close_day"].sort()[kept.height // 3]
    part = pl.when(pl.col("close_day") < split).then(pl.lit("fit")).otherwise(pl.lit("test"))
    return kept.with_columns(part=part), split


def digest(drawn: pl.DataFrame) -> str:
    """The sample's fingerprint: its tickers and parts."""
    lines = (drawn.sort("ticker")["ticker"] + " " + drawn.sort("ticker")["part"]).to_list()
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def prompt_of(row: dict[str, Any]) -> str:
    """The market as the model sees it: no price, no volume, no outcome."""
    day = (datetime.fromtimestamp(row["close_ms"] / 1000, UTC) - timedelta(days=1)).date()
    rules = "\n".join(t for t in (row["rules_primary"], row["rules_secondary"]) if t)
    return FORECAST.format(
        day=day.isoformat(),
        title=row["title"] or "",
        yes=row["yes_sub_title"] or "",
        no=row["no_sub_title"] or "",
        rules=rules,
    )


def probability_of(answer: dict[str, Any] | None) -> float | None:
    """The probability an answer gives, when it is a number from 0 to 1."""
    value = None if answer is None else answer["probability"]
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        p = float(value)
    except ValueError:
        return None
    return p if math.isfinite(p) and 0.0 <= p <= 1.0 else None


def ask_one(client: ModelClient, row: dict[str, Any]) -> dict[str, Any]:
    """One market: the model's reply and the probability in it."""
    reply, error = chat_or_error(client, prompt_of(row))
    p = probability_of(parse_reply(reply.content, KEYS))
    return {key: row[key] for key in DRAWN_KEYS} | {
        "error": error,
        "content": reply.content,
        "truncated": reply.truncated,
        "cached": reply.cached,
        "seconds": reply.seconds,
        "tokens": reply.completion_tokens,
        "parsed": p is not None,
        "probability": p,
    }


def frame_of(rows: list[dict[str, Any]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=ROW_SCHEMA)


def counts(frame: pl.DataFrame, keys: list[str]) -> dict[str, dict[str, int]]:
    """Markets and events by ``keys``."""
    grouped = frame.group_by(keys).agg(
        pl.len().alias("markets"), pl.col("event_ticker").n_unique().alias("events")
    )
    return {
        " | ".join(str(r[k]) for k in keys): {"markets": r["markets"], "events": r["events"]}
        for r in grouped.sort(keys).to_dicts()
    }


def correlation(a: NDArray[np.float64], b: NDArray[np.float64]) -> float | None:
    """Pearson's correlation, or None where either side has no variation (or too few values)."""
    if a.size < 3 or float(np.std(a)) == 0.0 or float(np.std(b)) == 0.0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def spread(frame: pl.DataFrame) -> dict[str, Any]:
    """How the parsed answers spread: whether the model says anything but one number."""
    answers = frame.filter(pl.col("parsed"))
    if answers.is_empty():
        return {"answers": 0}
    p = answers["probability"]
    price = answers["price_e6"].cast(pl.Float64) / E6
    return {
        "answers": answers.height,
        "mean": p.mean(),
        "std": p.std(),
        "quantiles": {f"{q:.2f}": p.quantile(q) for q in (0.05, 0.25, 0.5, 0.75, 0.95)},
        "distinct": p.n_unique(),
        "at_one_half": int((p == 0.5).sum()),
        "price_correlation": correlation(p.to_numpy(), price.to_numpy()),
    }


def summarize(
    rows: list[dict[str, Any]],
    markets: pl.DataFrame,
    drawn: pl.DataFrame,
    split: date,
    limit: int | None,
    wall_seconds: float,
) -> dict[str, Any]:
    """The forecast run: the population, the draw, the replies, the pace and the answers' spread
    (no outcome is read)."""
    frame = frame_of(rows)
    return {
        "cut": CUT.isoformat(),
        "population": {
            "markets": markets.height,
            "events": markets["event_ticker"].n_unique(),
            "by_category": counts(markets, ["category"]),
        },
        "drawn": drawn.height,
        "drawn_events": drawn["event_ticker"].n_unique(),
        "by_part": counts(drawn, ["part"]),
        "by_category_and_part": counts(drawn, ["category", "part"]),
        "split_day": split.isoformat(),
        "sample_digest": digest(drawn),
        "limit": limit,
        "asked": frame.height,
        "parsed": int(frame["parsed"].sum()),
        "parse_rate": frame["parsed"].mean() if frame.height else None,
        "truncated": int(frame["truncated"].sum()),
        "errors": int(frame["error"].is_not_null().sum()),
        **speed(frame, wall_seconds),
        "spread": spread(frame),
    }


# -- score: the only step that reads outcomes -----------------------------------------------------


def logit(p: NDArray[np.float64]) -> NDArray[np.float64]:
    return np.log(p) - np.log1p(-p)


def log_loss(z: NDArray[np.float64], y: NDArray[np.float64]) -> NDArray[np.float64]:
    """Each market's log loss in nats, at log-odds ``z`` for an outcome ``y`` of 1 or 0."""
    return np.where(y == 1.0, np.logaddexp(0.0, -z), np.logaddexp(0.0, z))


def fit_logistic(x: NDArray[np.float64], y: NDArray[np.float64]) -> tuple[NDArray[np.float64], int]:
    """The coefficients (the intercept first) of a logistic regression of ``y`` on the columns of
    ``x`` by Newton's method, and its steps; a step is the least-squares solution, so a column
    with no variation leaves the predictions defined."""
    design = np.column_stack([np.ones(len(y)), x])
    beta = np.zeros(design.shape[1])
    for step in range(1, NEWTON_STEPS + 1):
        p = 0.5 * (1.0 + np.tanh(0.5 * (design @ beta)))  # the logistic, without overflow
        gradient = design.T @ (y - p)
        hessian = design.T @ (design * (p * (1.0 - p))[:, None])
        move = np.linalg.lstsq(hessian, gradient, rcond=None)[0]
        beta = beta + move
        if float(np.max(np.abs(move))) < NEWTON_TOLERANCE:
            return beta, step
    raise SurveyDataError(f"the logistic fit did not settle in {NEWTON_STEPS} steps")


def gain_estimate(frame: pl.DataFrame) -> dict[str, Any]:
    """The mean gain of test markets, with its interval over events."""
    by_event = (
        frame.group_by("event_ticker")
        .agg(pl.col("gain").sum().alias("num"), pl.len().cast(pl.Float64).alias("den"))
        .sort("event_ticker")  # a group-by's order is not fixed; the draws must be
    )
    estimate = cluster_bootstrap(
        by_event["num"].to_numpy(),
        by_event["den"].to_numpy(),
        tail=TAIL,
        resamples=RESAMPLES,
        seed=SEED,
    )
    return {
        "markets": frame.height,
        "events": by_event.height,
        "gain": estimate.point_e6,  # the ratio itself: nats a market, not millionths
        "lower": estimate.lower_e6,
        "upper": estimate.upper_e6,
        "judged": by_event.height >= MIN_CELL_EVENTS,
    }


def placebo_donors(
    events: NDArray[np.str_], strata: NDArray[np.str_], rng: np.random.Generator
) -> tuple[NDArray[np.int64], int]:
    """For each market, another market of its stratum drawn at random (a shuffle within each
    stratum, then swaps that move any market off its own event); and the markets left with their
    own event, where the stratum offers nothing else."""
    donors = np.arange(len(events))
    for name in np.unique(strata):
        members = np.flatnonzero(strata == name)
        given = rng.permutation(members)
        for i in np.flatnonzero(events[given] == events[members]):
            if events[given[i]] != events[members[i]]:
                continue  # an earlier swap mended it
            for j in rng.permutation(len(members)):
                if (
                    events[given[j]] != events[members[i]]
                    and events[given[i]] != events[members[j]]
                ):
                    given[i], given[j] = given[j], given[i]
                    break
        donors[members] = given
    return donors, int((events[donors] == events).sum())


def score(
    kalshi: Path,
    rows: pl.DataFrame,
    min_markets: int = MIN_MARKETS,
    min_events: int = MIN_EVENTS,
    placebos: int | None = None,
) -> dict[str, Any]:
    """The comparison on the answered markets of a full forecast run, and N6's verdict
    (``placebos`` draws, ``PLACEBOS`` by default)."""
    placebos = PLACEBOS if placebos is None else placebos
    answered = rows.filter(pl.col("parsed"))
    results = scan_markets(kalshi, ["ticker", "result"], traded=answered.select("ticker").lazy())
    data = answered.join(results.collect(), on="ticker", how="left")
    unsettled = data.filter(~pl.col("result").is_in(["yes", "no"]).fill_null(False))
    if not unsettled.is_empty():
        raise SurveyDataError(
            f"{unsettled.height} drawn market(s) have no settled result ({unsettled['ticker'][0]})"
        )
    data = data.sort("ticker").with_columns(
        week=pl.from_epoch("close_ms", time_unit="ms").dt.date().dt.truncate("1w"),
        stratum=pl.concat_str(["part", "category", "band"], separator=" | "),
    )
    y = (data["result"] == "yes").cast(pl.Float64).to_numpy()
    price = logit(data["price_e6"].to_numpy() / E6)
    model = logit(np.clip(data["probability"].to_numpy(), CLIP, 1.0 - CLIP))
    fit = (data["part"] == "fit").to_numpy()
    test = ~fit
    if not fit.any() or not test.any():
        raise SurveyDataError("the fit part or the test part has no answered market")
    alone, alone_steps = fit_logistic(price[fit, None], y[fit])
    loss_alone = log_loss(alone[0] + alone[1] * price[test], y[test])

    def with_model(values: NDArray[np.float64]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        both, _ = fit_logistic(np.column_stack([price, values])[fit], y[fit])
        z = both[0] + both[1] * price[test] + both[2] * values[test]
        return log_loss(z, y[test]), both

    loss_both, both = with_model(model)
    tested = data.filter(pl.Series(test)).with_columns(gain=pl.Series(loss_alone - loss_both))
    total = gain_estimate(tested)
    rng = np.random.default_rng(SEED)
    events, strata = data["event_ticker"].to_numpy(), data["stratum"].to_numpy()
    placebo, own_event = [], 0
    for _ in range(placebos):
        donors, left = placebo_donors(events, strata, rng)
        own_event = max(own_event, left)
        placebo.append(float(np.mean(loss_alone - with_model(model[donors])[0])))
    high = float(np.quantile(placebo, 1.0 - TAIL)) if placebo else None
    clears = {
        "enough": total["markets"] >= min_markets and total["events"] >= min_events,
        "lower_above_zero": total["lower"] > 0,
        "gain": total["gain"] >= MIN_GAIN,
        "placebo": high is not None and high < MIN_GAIN and high < total["gain"],
    }
    unanswered = rows.filter(~pl.col("parsed"))
    return {
        "markets": rows.height,
        "answered": data.height,
        "unanswered_by_part": {
            r["part"]: r["len"] for r in unanswered.group_by("part").len().sort("part").to_dicts()
        },
        "fit": {
            "markets": int(fit.sum()),
            "events": data.filter(pl.Series(fit))["event_ticker"].n_unique(),
        },
        "coefficients": {
            "price_alone": alone.tolist(),
            "price_and_model": both.tolist(),
            "steps": alone_steps,
        },
        "test_yes_rate": float(y[test].mean()),
        "test_log_loss": {
            "price_as_is": float(log_loss(price[test], y[test]).mean()),
            "model_as_is": float(log_loss(model[test], y[test]).mean()),
            "price_fitted": float(loss_alone.mean()),
            "price_and_model": float(loss_both.mean()),
        },
        "gain": total,
        "by_category": {
            str(k[0]): gain_estimate(g)
            for k, g in sorted(tested.group_by("category"), key=lambda kv: str(kv[0][0]))
        },
        "by_week": {
            k[0].isoformat(): gain_estimate(g)
            for k, g in sorted(tested.group_by("week"), key=lambda kv: str(kv[0][0]))
        },
        "placebo": {
            "draws": placebos,
            "upper": high,
            "mean": float(np.mean(placebo)) if placebo else None,
            "own_event_at_most": own_event,
        },
        "bars": {"min_gain": MIN_GAIN, "min_markets": min_markets, "min_events": min_events},
        "clears": clears,
        "passes": all(clears.values()),
    }
