# Reading weather rules text with the model node (S5.1)

**What this is.** The first measurement of S5 (PLAN §6.5): whether the model node reads a weather market's rules text correctly, at scale, against ground truth the repository already holds. It asks one thing, whether the model is a reliable reader. It does not ask whether the model can forecast; that is S5.2. **The bar is cleared in every group, with no miss.** The model returned the right strike direction and numbers for all 11,825 markets read and the right station for all 9,659 whose rules name one. Every reply parsed, none ran out of its token budget, and the model never named a wrong station where the rules name none. It did this at 21.9 markets a minute, one request at a time, in 9 hours. The reservations are in the limitations: the ground truth is what a regular expression reads, and a run with no miss gives an interval that cannot show how far from 100% the true rate could lie.

## Data snapshot

- **Markets:** the history download's market table (`kalshi/markets/historical`) on the analytics node, as of 2026-10-29. The reading covers **S2's window, 2026-08-01 to 2026-10-10** (the markets S2.1 verified by hand): **11,325 markets in 1,680 events**, of which 9,654 are daily highs and 1,671 are rain. S2's test part (from 2026-10-11) is refused by the command and was not read.
- **An extra, un-barred sample:** 500 earlier markets (before 2026-08-01, back to 2024), drawn at random with seed 35: 498 highs and 2 rain. Their texts are the nearest this check comes to text that no one engineered for.
- **The model:** `qwen3.8-flash-next-iq3_s`, the only model the model node serves, a 3-bit quantized reasoning model; temperature 0, a budget of 2,000 tokens a reply (the thinking is spent from the same budget). The prompt is `READ_WEATHER` in `model/prompts.py`, sha256 `cfea473c3fcef9685d9c96227b57769868515fe081d0a1a72cbcd4556a1f80c5`. It was committed once (`5c64f30`) and never changed, so nothing was tuned on a result.
- **Ground truth:** the station from `weather.stations.code_in` applied to the rules text, and the bounds from Kalshi's own `strike_type`, `floor_strike` and `cap_strike`.
- **Run:** `python -m sparq_analytics.model read --kalshi DIR --out DIR` at commit `5efffee` on the analytics node, 2026-10-29 07:07 to 16:08 UTC. The summary was then recomputed from the cached replies with the final code (`f62599e`, the merge review's fixes), in under a second, and **every verdict figure is identical**; only the speed fields differ, because a replay makes no calls. The figures here are the run's.

## Method

For each market the model gets the rules text and is asked to answer a JSON object: the site it names (or null), the direction (`greater`, `less` or `between`) and the lower and upper numbers. The reply may sit in a code fence, after a line of prose or hold other objects; the parser takes the first object with all four keys.

- **The site agrees** when the answer, mapped through the station map, is the site the rules name. A station's own code (KNYC for CLINYC) counts as its site. Judged only over the markets whose rules name a site.
- **The bounds agree** when the direction is the strike type and the numbers are the strike fields (a greater market has its floor, a less market its cap, a between market both). Judged over the markets whose strike type is one of the three.
- **A market whose rules name no site** has no station truth. The model's answer is classified apart against the one code the market's own series uses where its named markets show one (S2.1's rule): declined, right, wrong, other (a name the station map does not know), unmapped or unparsed.
- **The bar (N6, set before reading):** in the highs and in the rain markets separately, the site and the bounds each agree in at least 99% of markets with a 95% lower bound of at least 98% (a bootstrap over events, resampling whole events), and at least 99.5% of replies parse. A group is judged only with ten events or more.
- **Reproducibility.** The model's replies are not bit-reproducible: the same prompt at temperature 0 gave different reply text in 71 of 200 calls across two timing runs, mostly formatting. Every reply is therefore cached under the hash of the request, and a rerun reads the cache and reproduces the figures exactly.

## Results

**The bar, by group** (S2's window).

| Group | Measure | Markets judged | Agree | Rate | 95% lower bound | Events | Clears |
|---|---|---|---|---|---|---|---|
| Highs | site | 7,986 | 7,986 | 100% | 100% | 1,331 | yes |
| Highs | bounds | 9,654 | 9,654 | 100% | 100% | 1,609 | yes |
| Rain | site | 1,671 | 1,671 | 100% | 100% | 71 | yes |
| Rain | bounds | 1,671 | 1,671 | 100% | 100% | 71 | yes |

**Replies.** 11,825 of 11,825 parsed (11,325 in the window and the 500 earlier), none was truncated and none failed (bar: 99.5%). The strike types read were `between` (6,770), `greater` (3,363) and `less` (1,692); no market carried another type. An independent recomputation from the stored replies, without the module's own code, found all 11,825 bounds right and all 9,659 sites right (9,654 by the climate code itself, 5 by the station's code: KCLL, KLEX, KMKE, KPVD and KDTW on rain markets, which the station map joins to CLICLL, CLILEX, CLIMKE, CLIPVD and CLIDTW).

**The earlier sample** (no bar). The bounds agree in 500 of 500 (486 events). Only 2 of the 500 name a site, and the site agrees in both. The other 498 name a place but no code, which is every text of 2024 and 2025 that was read.

**Where the rules name no site.** 2,166 markets (1,668 in the window and 498 of the earlier sample) have no station truth. The model's answers:

| Answer | Window | Earlier | Total |
|---|---|---|---|
| Declined (null) | 1,581 | 475 | 2,056 |
| The right code, though none was named | 37 | 7 | 44 |
| A name the map does not know, right in substance | 46 | 15 | 61 |
| A name the map does not know, garbled (KEBG, KEB on the Austin series) | 4 | 1 | 5 |
| **A wrong code** | **0** | **0** | **0** |

The names right in substance are places the rules text gives as a name: "Austin Bergstrom" (28), LAX (16), MIA (12) and a few others. The model declines to invent a station in 95% of these markets and never names a wrong one. S2.1's rule (the series' one code) remains the site for such a market, so the model adds nothing there; the number matters as evidence that it does not make a station up.

**Speed** (one request at a time; more in flight gained nothing, because the model node answers one at a time).

| Figure | Value |
|---|---|
| Calls | 11,825 |
| Wall time | 9.0 hours (32,443 seconds) |
| Markets a minute | 21.9 |
| Tokens a second | 48 |
| Median call | 2.5 seconds and 110 tokens (window 104, earlier sample 184) |
| Longest call | 23.6 seconds, 1,544 tokens of the 2,000 budget |

N7 had forecast about three seconds a market and some ten hours; the run took 2.5 seconds at the median and 9 hours.

## Limitations

- **The ground truth is what a regular expression already reads.** S2.1's rules text is templated, so 100% shows the model reliable on engineered text, not able on text no one has engineered for. The earlier sample is the nearest test of that, and it can speak only to the bounds, because 498 of its 500 texts name no site.
- **A run with no miss gives an empty interval.** With no disagreement the bootstrap over events returns 100% to 100% whatever the sample, so it clears the bar by construction, and the interval says nothing about how far the true rate could lie from 100%. An exact one-sided 95% bound, treating each event as one trial, is 99.8% for the highs (1,331 and 1,609 events) and **95.9% for the rain markets, which come from only 71 events**; treating each market as a trial it is 99.8% for rain. The bar as set before reading is cleared, and it is not re-cut here. What the run supports is that no market of 11,825 failed, not that the failure rate is below 2% in every rain event.
- **Reading is the easy half.** It says nothing about whether the model's probability adds information to a price. PLAN §6.5 stated, before this run, the expectation that the reading passes and the probability does not; the first half is now measured.
- **One model, one prompt.** The prompt was fixed before the run and not tuned, so the figures are not an optimum; a different prompt or model would be a different measurement.

## Reproduce

```
python -m sparq_analytics.model ping
python -m sparq_analytics.model read --kalshi DIR --out DIR [--cache DIR] [--limit N]
```

`ping` needs the model node's settings file (`SPARQ_MODEL_CONFIG`, default `~/.config/sparq/model.toml`, mode 600). `read` writes `model-read.json` (every figure above) and `model-read.parquet` (every market's reply and verdicts). With the run's cache, `read` replays all 11,825 replies without a single call.
