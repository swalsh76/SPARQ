"""Command line for S5, the model node (PLAN §6.5).

    python -m sparq_analytics.model ping [--settings FILE]
    python -m sparq_analytics.model read --kalshi DIR --out DIR [--limit N] [--workers N]
                                         [--settings FILE] [--cache DIR]
    python -m sparq_analytics.model forecast --kalshi DIR --out DIR [--limit N] [--workers N]
                                             [--settings FILE] [--cache DIR]
    python -m sparq_analytics.model score --kalshi DIR --out DIR

Run it on the analytics node. The model node's address, model name and key are in a settings file
outside the repository (``--settings``, else ``SPARQ_MODEL_CONFIG``, else
``~/.config/sparq/model.toml``, mode 600). ``ping`` sends one trivial prompt and prints the reply
and its time. ``read`` (S5.1) asks the model to read the rules text of S2's weather markets (and a
sample of earlier ones), compares its answers with the station map and the strike fields, and writes
``model-read.json`` (printed without the examples) and ``model-read.parquet``. ``forecast`` (S5.2)
draws the thin markets of the history, asks the model the chance of each (``--limit N`` asks only
the first N of the draw: a pilot, which ``score`` refuses) and writes ``model-forecast.json`` and
``model-forecast.parquet``, reading no outcome; ``score`` reads them back with the outcomes and
writes ``model-score.json``. Every reply is cached (``--cache``, default ``OUT/model-cache``), so a
rerun or a restart costs nothing for what was answered. A flag the command does not use is refused.
Exit codes: 0 fine, 1 bad command line, settings or data.
"""

import argparse
import json
import sys
import time
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from sparq_analytics.model import forecast, read
from sparq_analytics.model.client import ModelClient, ModelError, ModelSettings, load_settings
from sparq_analytics.survey.cli import ERRORS, UsageError, check, need
from sparq_analytics.survey.dump import SurveyDataError
from sparq_analytics.weather.stations import WeatherDataError

APPLIES: dict[str, tuple[str, ...]] = {
    "settings": ("ping", "read", "forecast"),
    "kalshi": ("read", "forecast", "score"),
    "out": ("read", "forecast", "score"),
    "limit": ("read", "forecast"),
    "workers": ("read", "forecast"),
    "cache": ("read", "forecast"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sparq_analytics.model", description=__doc__.split("\n")[0]
    )
    parser.add_argument("command", choices=("ping", "read", "forecast", "score"))
    parser.add_argument("--settings", type=Path, help="the model node's settings file")
    parser.add_argument("--kalshi", type=Path, help="the history download's kalshi folder")
    parser.add_argument("--out", type=Path, help="where the results go")
    parser.add_argument(
        "--limit",
        type=int,
        help="read: only this many markets of each sample; forecast: a pilot of the draw's first N,"
        " which score refuses",
    )
    parser.add_argument("--workers", type=int, help="requests in flight (default 1)")
    parser.add_argument("--cache", type=Path, help="the reply cache (default OUT/model-cache)")
    return parser


def make_client(settings: ModelSettings, cache: Path | None) -> ModelClient:
    """The client the commands use (tests put a fake transport behind it)."""
    return ModelClient(settings, cache=cache)


def _ping(args: argparse.Namespace) -> int:
    client = make_client(load_settings(args.settings), None)
    started = time.monotonic()
    try:
        reply = client.chat("Reply with exactly one word: ping", max_tokens=600)
    finally:
        client.close()
    print(
        json.dumps(
            {
                "model": client.settings.model,
                "content": reply.content,
                "tokens": reply.completion_tokens,
                "truncated": reply.truncated,
                "seconds": round(time.monotonic() - started, 2),
            },
            indent=2,
        )
    )
    return 0


def _workers(args: argparse.Namespace) -> int:
    workers = 1 if args.workers is None else args.workers
    if workers < 1 or (args.limit is not None and args.limit < 1):
        raise UsageError("--workers and --limit must be at least 1")
    return int(workers)


def _read(args: argparse.Namespace) -> int:
    kalshi, out = need(args.kalshi, "--kalshi"), need(args.out, "--out")
    workers = _workers(args)
    settings = load_settings(args.settings)
    out.mkdir(parents=True, exist_ok=True)
    window = read.reading_markets(kalshi, read.READ_FIRST, read.READ_LAST)
    older = read.reading_markets(kalshi, date(2000, 1, 1), read.READ_FIRST - timedelta(days=1))
    older_n = read.OLDER_SAMPLE if args.limit is None else min(args.limit, read.OLDER_SAMPLE)
    codes = read.series_sites(window)
    markets = pl.concat(
        [
            read.sample(window, args.limit).with_columns(sample=pl.lit("window")),
            read.sample(older, older_n).with_columns(sample=pl.lit("older")),
        ]
    ).join(codes, on="series", how="left")
    client = make_client(settings, args.cache or out / "model-cache")
    started = time.monotonic()
    try:
        rows = read.run_read(
            client, markets, workers, log=lambda line: print(line, file=sys.stderr)
        )
    finally:
        client.close()
    result: dict[str, Any] = read.summarize(rows, time.monotonic() - started)
    result["model"] = settings.model
    result["prompt"] = read.READ_WEATHER_HASH
    (out / "model-read.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
    read.frame_of(rows).write_parquet(out / "model-read.parquet")
    shown = json.loads(json.dumps(result, default=str))
    for group in (*shown["by_group"].values(), shown["earlier_sample"]):
        group.pop("examples")
    print(json.dumps(shown, indent=2))
    return 0


def _forecast(args: argparse.Namespace) -> int:
    kalshi, out = need(args.kalshi, "--kalshi"), need(args.out, "--out")
    workers = _workers(args)
    settings = load_settings(args.settings)
    out.mkdir(parents=True, exist_ok=True)
    markets = forecast.population(kalshi)
    drawn, split = forecast.draw(markets)
    asked = drawn if args.limit is None else drawn.filter(pl.col("rank") < args.limit)
    client = make_client(settings, args.cache or out / "model-cache")
    started = time.monotonic()
    try:
        rows = read.run_read(
            client,
            asked,
            workers,
            log=lambda line: print(line, file=sys.stderr),
            ask=forecast.ask_one,
        )
    finally:
        client.close()
    wall = time.monotonic() - started
    result = forecast.summarize(rows, markets, drawn, split, args.limit, wall)
    result["model"] = settings.model
    result["prompt"] = forecast.FORECAST_HASH
    text = json.dumps(result, indent=2, default=str)
    (out / "model-forecast.json").write_text(text, encoding="utf-8")
    forecast.frame_of(rows).write_parquet(out / "model-forecast.parquet")
    print(text)
    return 0


def _score(args: argparse.Namespace) -> int:
    kalshi, out = need(args.kalshi, "--kalshi"), need(args.out, "--out")
    run = json.loads((out / "model-forecast.json").read_text(encoding="utf-8"))
    if run["limit"] is not None:
        raise SurveyDataError(f"the forecast in {out} is a pilot (--limit {run['limit']})")
    rows = pl.read_parquet(out / "model-forecast.parquet")
    if rows.height != run["drawn"]:
        raise SurveyDataError(f"{out} holds {rows.height} replies for {run['drawn']} drawn markets")
    if forecast.digest(rows) != run["sample_digest"]:
        raise SurveyDataError(f"the replies in {out} are not the sample its forecast drew")
    if run["prompt"] != forecast.FORECAST_HASH:
        raise SurveyDataError(f"the forecast in {out} asked another prompt than the committed one")
    result = forecast.score(kalshi, rows)
    result |= {k: run[k] for k in ("model", "prompt", "sample_digest", "split_day")}
    text = json.dumps(result, indent=2, default=str)
    (out / "model-score.json").write_text(text, encoding="utf-8")
    print(text)
    return 0


HANDLERS = {"ping": _ping, "read": _read, "forecast": _forecast, "score": _score}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        check(args, APPLIES)
        return HANDLERS[args.command](args)
    except (*ERRORS, ModelError, WeatherDataError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
