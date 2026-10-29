# Where the book is slow (S3.2)

**Outcome against M4's bar (a relation type whose stale quotes, hit at their shown size for the 10-minute move after taker fees, are worth $25 a day): no type clears it, under either reading.** Related markets answer each other within seconds: a fifth to a third of the watched markets have moved within a second of a 2-cent move next door, and 85 to 88% within ten minutes. The 10-minute pass-through is 0.16 to 0.60 cents for the four signed kinds, below the taker fee at any price between about 10 and 90 cents, and the first move agrees with the expected direction only 51 to 59% of the time. The quotes that do stand for a second or more are not slow but right: weighted by their size, their 10-minute response runs *against* the expectation for thresholds, horizons and game. Hitting every stale quote would lose $1.5 M to $41 M a day a kind in fees; hitting only those that stood a second, $1.3 M to $41 M. The direction is parked (PLAN §9) with these numbers. Per M1 the owner chooses: go on to S3.3 (the default) or hear a strand proposal.

## Data snapshot

- **Live frames:** the ingest's ticker frames and trades on the server host, copied to the analytics node with `live extract`: 2026-10-24 21:00 to 2026-10-29 00:00 UTC, 99 hours, about 109 M frames and 22 M trades; the ingest's gap log holds 22 outages of the ticker feed, the longest from 17:36 to 21:27 UTC on 2026-10-28 (the server host off the network). No outcome is read (M3: a structural reading).
- **Markets:** the ingest's market list, now with Kalshi's `strike_type`, `floor_strike` and `cap_strike` and, for weather markets, the rules text that names the station. 541,994 markets had a frame in the span; 443,132 of them (82%) are in at least one relation pair.
- **Fees:** each series' current terms from the history download's series table (taker fee 7% × p(1−p) × the series' multiplier); 8,184 stale quotes a day in thresholds, of 3.76 M, had no known terms and are left out of the sums.
- **Run:** `python -m sparq_analytics.survey lag --kalshi DIR --live DIR --out DIR` at commit `5268e7e`, about four minutes; every figure below is in `lag.json` (its `coverage` holds the groups).

## Method

**The relation groups**, from Kalshi's own fields rather than ticker spelling; each pair is ordered (`a` moves, `b` is watched) and carries the direction `b` is expected to move when `a` rises:

| Kind | Pairs | Watched markets | Groups | Expected sign |
|---|---|---|---|---|
| Thresholds: adjacent levels of one subject in one event (the subject is the ticker without its trailing line, so a spread's two teams and a prop's players stay apart) | 529,540 | 288,205 | 12,570 events | same (opposite for a greater against a less) |
| Outcomes: the two or three alternatives of one structured event | 44,032 | 28,846 | 11,892 events | opposite |
| Game: the same team, or the total, across a sports game's series, at the other series' central line | 425,210 | 52,700 | 1,505 games | same |
| Horizons: thresholds of one underlying across series of different horizons (the nearest level in the soonest-closing open event of the other series) | 1,275,314 | 161,726 | 34 stems (BTC, ETH, SOL, gold, WTI, the Nasdaq-100, the S&P 500, Treasury yields, …) | same |
| Brackets: adjacent brackets of one event | 234,820 | 118,463 | 1,053 events | none (timing only) |
| Weather: series of one station and day | 13,966 | 1,903 | 158 station-days | none (timing only) |

98,862 active markets are in no pair: polls and rankings (`KXNCAAMBTOPAPRANK`), `custom` markets (DOGE's ladders, the 15-minute crypto leader), first scorers, draft picks, exact scores.

**The event study.** A move of `a` is a mid change of 2 cents or more from its previous two-sided frame, the bid and the ask not moving against each other. For every pair valid at that moment, `b`'s quote strictly before the move is read (unknown, one-sided or extreme quotes, at 3 cents or less or 97 or more, take no further part), then its response at 1 s, 10 s, 1 min, 10 min and 1 h in the expected direction (`a`'s own direction for the unsigned kinds), its first mid change within an hour and whether it agreed (the lag is unknown when no move was seen and the hour ran past the copied frames or through an outage), the takers who hit its stale quote while it stood (block trades left out; on the expected side, **with**, or the other, **against**), and, for a signed kind, the stale quote's **capacity**: the size shown on the side a taker would hit, times the 10-minute response, less the taker fee on that size at that price. Each stale quote is counted once: at the first move that left it stale, or, when it never answered within the hour, once an hour it stood. The capacity is read twice: for every stale quote, as M4 words it, and for the **reachable** ones, which stood at least a second before `b` moved (SPARQ's scale) with that lag known, since a quote that moved in the same second as `a` was never there to hit. Events whose first ten minutes overlap a logged outage are excluded; the 1-hour response is null where an outage overlaps the hour, past `b`'s close, or past the copied frames.

21,740,377 moves, 18,876,795 of them with a partner, gave 74,134,650 event rows: 788,086 with no quote of `b` seen yet, 7,824,147 with an extreme quote, 1,689,405 in an outage; 63,833,012 usable, 377,578 of them with an unknown lag.

## Results

**How fast the watched market moves.** The share of events in which `b`'s mid had changed within each horizon, and how often that first move agreed with the expected direction (with `a`'s direction for brackets and weather).

| Kind | Events a day | 1 s | 10 s | 1 min | 10 min | 1 h | First move agreed |
|---|---|---|---|---|---|---|---|
| Thresholds | 5,643,122 | 34% | 62% | 76% | 88% | 94% | 58% |
| Outcomes | 803,443 | 34% | 60% | 74% | 85% | 92% | 57% |
| Game | 7,936,520 | 21% | 56% | 75% | 85% | 91% | 51% |
| Horizons | 787,429 | 35% | 61% | 71% | 87% | 99% | 59% |
| Brackets | 247,120 | 24% | 50% | 69% | 89% | 98% | 54% |
| Weather | 57,032 | 1% | 6% | 18% | 52% | 87% | 51% |

**How far it moves**, in cents of mid in the expected direction, mean over events (the 10-minute interval is a cluster bootstrap over group-hours at 2.5% each side).

| Kind | 1 s | 10 s | 1 min | 10 min (interval) | 1 h |
|---|---|---|---|---|---|
| Thresholds | 1.26 | 0.94 | 0.74 | **0.60** (0.58 to 0.62) | 0.50 |
| Outcomes | 0.46 | 0.43 | 0.43 | **0.33** (0.30 to 0.35) | −0.02 |
| Game | 0.09 | 0.12 | 0.16 | **0.16** (0.13 to 0.18) | 0.06 |
| Horizons | 0.50 | 0.59 | 0.60 | **0.30** (0.21 to 0.38) | 0.08 |
| Brackets (in `a`'s direction) | 1.09 | 1.33 | 1.86 | 2.41 (2.13 to 2.70) | 0.15 |
| Weather (in `a`'s direction) | 0.02 | 0.14 | 0.45 | 0.53 (0.42 to 0.66) | 0.55 |

The pass-through is a fraction of the 2-cent-or-more move, and for thresholds it peaks at one second and fades: the immediate co-movement partly reverts. A larger move does not help much: a threshold neighbour answers a 2-to-3-cent move with 0.35 cents at ten minutes and a 10-cent-or-more move with 1.45 (agreement 59% either way); the game kind answers every size with 0.14 to 0.17 cents.

**Capacity against M4's bar**, dollars a day, the span's sums scaled to a day (the interval is a cluster bootstrap over the span's 99 hours).

| Kind | Stale quotes a day | Every quote: gross | Fee | **Net** (interval) | Reachable quotes a day | Reachable: gross | Fee | **Net** (interval) |
|---|---|---|---|---|---|---|---|---|
| Thresholds | 3,750,275 | +11,905,531 | 52,743,460 | **−40,837,929** (−54.0 M to −29.9 M) | 2,208,897 | −10,192,926 | 30,544,632 | **−40,737,559** (−51.7 M to −33.2 M) |
| Outcomes | 520,539 | +1,220,151 | 2,683,752 | **−1,463,601** (−1.90 M to −0.93 M) | 291,486 | +125,255 | 1,872,176 | **−1,746,921** (−2.09 M to −1.44 M) |
| Game | 1,129,666 | −1,997,699 | 30,086,042 | **−32,083,741** (−39.2 M to −26.5 M) | 797,137 | −1,771,850 | 26,534,344 | **−28,306,194** (−34.3 M to −23.7 M) |
| Horizons | 194,913 | +184,258 | 1,890,659 | **−1,706,401** (−2.30 M to −1.25 M) | 110,805 | −278,148 | 998,264 | **−1,276,412** (−1.63 M to −1.01 M) |

The sums are large because the quotes are many (millions a day) and often big (the median shown size is 38 to 100 contracts, the mean far more), and the fee is paid on the whole shown size every time; a taker who hit them all would pay $53 M a day in thresholds alone for $12 M of price moves. **The reachable gross is negative** for thresholds, horizons and game: a quote that did not react within a second goes on, weighted by its size, to move against the related market's signal. Where the book is slow, it is usually right.

**Is anyone taking them already?** The share of events with a taker hitting `b`'s stale quote on the expected side, against the share hitting it on the other side: thresholds 1.9% against 1.6%; outcomes 4.5% against 3.8%; game 8.1% against 7.8%; horizons 17.1% against 16.8%; weather 30% against 30%. Trading through a stale quote happens at the market's own trading rate, on both sides alike: nobody is systematically picking these quotes off, and nothing says anyone should.

**By activity tier of the watched market** (contracts a day in the span). The thin tier is the slowest and still mostly fast: in thresholds, markets under 100 contracts a day had moved within ten minutes in 83% of events (10.5 M events; response 0.71 cents; reachable net −$26.8 M a day), against 91% to 94% in the busier tiers; the busiest (10,000 and more) answers with 0.87 cents and is hit *with* in 13.8% of events, *against* in 12.0%. In every tier of every signed kind the net is negative.

**By category.** The largest responses are in thin, slow categories: Elections thresholds (263,228 events) answer with 3.85 cents at ten minutes, 67% agreement, 78% moved within ten minutes, and net +$59,258 a day under the every-quote reading, which the reachable reading turns into **−$45,525**; Crypto thresholds 0.85 cents (63% agreement); Commodities thresholds 0.78 (62%; 98% moved within ten minutes). Sports hold 78% of the usable events and answer with 0.16 (game) to 0.55 cents (thresholds).

**The slow spots.** The twenty series pairs with the largest reachable net are in `lag.json`; the first is the MLB spread against the MLB moneyline at +$115,995 a day on 163 reachable quotes a day (3,492 events; response 0.57 cents; 34% moved within a second, 98.5% within ten minutes; agreement 61%; the moneyline was hit *with* in 74% of events and *against* in 69%). It is the top of a ranking of about a thousand series pairs over four days, which is where noise lands: the second entry (NFL touchdown props, +$55,097) has a mean response of 0.14 cents and 53% agreement. These are leads for a pre-registered test at most, not results, and none of them is a relation type.

## The readings, as a ladder

1. **Do nothing:** $0.
2. **Hit every stale quote at its shown size, M4's wording:** −$1.5 M to −$41 M a day a kind.
3. **Hit only the quotes that stood a second:** −$1.3 M to −$41 M a day a kind; their gross before fees is negative for three kinds of four.
4. **A taker with a model** of which stale quotes will follow: the ceiling is the gross of reading 2, +$12 M a day in thresholds before fees, most of it inside the first second (the 1-second response is twice the 10-minute one), which is not a seconds-scale game.

## What this says for SPARQ

SPARQ's founding thesis was a fair value from related markets. On Kalshi's related markets at a 2-cent resolution, that fair value is already in the neighbouring book within seconds, the pass-through is a fraction of a cent, and the quotes that lag are the ones that know better. The weather books are the exception in speed (half still unmoved after ten minutes), but S2.2 showed they are priced hours ahead of the public observation and their cross-series response here is half a cent with coin-flip agreement. Model-based grouping (the model node, PLAN §9) could find relations this reading cannot, but the lag it would exploit is measured here as seconds, not minutes.

## Limitations

- Four days of frames, with sports (78% of usable events) dominant; a longer span changes the sums, not the shape.
- The response is read on the mid, so a one-sided widening is not a move and a crossed or empty side is skipped; one-sided quotes were none in this span (every frame carried both sides).
- The capacity hits the whole shown size at the best; depth behind it and the fill a taker would really get are not in the frames (the order-book recorder, parked in §9). A quote that stood unanswered is counted once an hour it stood, so a long-standing quote's capacity scales with the hours, not with the one quote.
- The game kind pairs a team's markets at the other series' central line, which for a moneyline against a spread is not the same probability; a model could pair better.
- The horizons kind pairs by the nearest level, and the 15-minute markets' level is the price at their start, so the pair is always near the money; the daily ladder's far levels respond little by construction.
- The fee terms are each series' current ones, as in S3.1.
