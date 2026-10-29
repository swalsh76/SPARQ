# Where the book is slow (S3.2)

**Outcome against M4's bar (a relation type whose stale quotes, hit at their shown size for the 10-minute move after taker fees, are worth $25 a day): no type clears it, under either reading.** Related markets answer each other within seconds: a fifth to a half of the watched markets have moved within a second of a 2-cent move next door, and 85 to 96% within ten minutes. The 10-minute pass-through is 0.15 to 0.59 cents for the four signed kinds, below the taker fee at any price between about 10 and 90 cents, and the first move agrees with the expected direction 51 to 65% of the time. The quotes that do stand for a second or more are not slow but right: weighted by their size, their 10-minute response runs *against* the expectation for thresholds, horizons and game. Hitting every stale quote would lose $1.3 M to $32 M a day a kind in fees; hitting only those that stood a second, $1.0 M to $33 M. The direction is parked (PLAN §9) with these numbers.

**Corrected twice.** *S3.3 (2026-10-29, commit `5af8248`):* the feed shows an empty side of the book as a 0-cent bid or a 100-cent ask with no size, never as a null, and the first version of this study read such frames as two-sided quotes with a mid (9% of the frames carry an empty bid and 3% an empty ask, mostly in thin ladders). With empty sides read as missing, 6.9 M event rows became one-sided and left, the threshold pass-through fell from 0.60 to 0.47 cents, the horizon pass-through rose from 0.30 to 0.59 cents (its agreement from 59 to 65%), and the brackets' apparent 2.4-cent co-movement turned into the −0.1 cents one expects of neighbours sharing probability mass. *S3.c (2026-10-29, commits `937fadc` and `cf5308f`):* an event whose move, or whose watched quote, was last seen before a logged outage had counted as clean, with the pre-outage frame as the standing quote; it is left out now, as in an outage (658,325 more event rows), and frames of one market in one millisecond are sorted in the feed's order, so a run reproduces to the unit (two runs at `cf5308f` are identical). The shares moved within a horizon rose by under a point, the pass-throughs changed by a hundredth of a cent at most and the capacities by under one percent. The conclusions above did not change; the figures below are the final ones.

## Data snapshot

- **Live frames:** the ingest's ticker frames and trades on the server host, copied to the analytics node with `live extract`: 2026-10-24 21:00 to 2026-10-29 02:00 UTC, 101 hours, about 119 M frames and 24 M trades; the ingest's gap log holds 31 outages of the ticker feed, the longest from 17:36 to 21:27 UTC on 2026-10-28 (the server host off the network). No outcome is read (M3: a structural reading).
- **Markets:** the ingest's market list, now with Kalshi's `strike_type`, `floor_strike` and `cap_strike` and, for weather markets, the rules text that names the station. 555,231 markets had a frame in the span; 454,252 of them (82%) are in at least one relation pair.
- **Fees:** each series' current terms from the history download's series table (taker fee 7% × p(1−p) × the series' multiplier); 2,641 stale quotes a day in thresholds, of 3.44 M, had no known terms and are left out of the sums.
- **Run:** `python -m sparq_analytics.survey lag --kalshi DIR --live DIR --out DIR` at commit `cf5308f`, about three and a half minutes, twice, with identical output; every figure below is in `lag.json` (its `coverage` holds the groups).

## Method

**The relation groups**, from Kalshi's own fields rather than ticker spelling; each pair is ordered (`a` moves, `b` is watched) and carries the direction `b` is expected to move when `a` rises:

| Kind | Pairs | Watched markets | Groups | Expected sign |
|---|---|---|---|---|
| Thresholds: adjacent levels of one subject in one event (the subject is the ticker without its trailing line, so a spread's two teams and a prop's players stay apart) | 543,400 | 295,748 | 12,889 events | same (opposite for a greater against a less) |
| Outcomes: the two or three alternatives of one structured event | 45,594 | 29,835 | 12,291 events | opposite |
| Game: the same team, or the total, across a sports game's series, at the other series' central line | 458,642 | 55,753 | 1,520 games | same |
| Horizons: thresholds of one underlying across series of different horizons (the nearest level in the soonest-closing open event of the other series) | 1,304,124 | 165,052 | 34 stems (BTC, ETH, SOL, gold, WTI, the Nasdaq-100, the S&P 500, Treasury yields, …) | same |
| Brackets: adjacent brackets of one event | 239,752 | 120,941 | 1,065 events | none (timing only) |
| Weather: series of one station and day | 13,966 | 1,903 | 158 station-days | none (timing only) |

100,979 active markets are in no pair: polls and rankings (`KXNCAAMBTOPAPRANK`), `custom` markets (DOGE's ladders, the 15-minute crypto leader), first scorers, draft picks, exact scores.

**The event study.** A move of `a` is a mid change of 2 cents or more from its previous two-sided frame, the bid and the ask not moving against each other; a side with no size is missing, not a quote. For every pair valid at that moment, `b`'s quote strictly before the move is read (unknown, one-sided or extreme quotes, at 3 cents or less or 97 or more, take no further part), then its response at 1 s, 10 s, 1 min, 10 min and 1 h in the expected direction (`a`'s own direction for the unsigned kinds), its first mid change within an hour and whether it agreed (the lag is unknown when no move was seen and the hour ran past the copied frames or through an outage), the takers who hit its stale quote while it stood (block trades left out; on the expected side, **with**, or the other, **against**), and, for a signed kind, the stale quote's **capacity**: the size shown on the side a taker would hit, times the 10-minute response, less the taker fee on that size at that price. Each stale quote is counted once: at the first move that left it stale, or, when it never answered within the hour, once an hour it stood. The capacity is read twice: for every stale quote, as M4 words it, and for the **reachable** ones, which stood at least a second before `b` moved (SPARQ's scale) with that lag known, since a quote that moved in the same second as `a` was never there to hit. An event is excluded when a logged outage falls between the earlier of `a`'s previous frame and `b`'s last frame and ten minutes after the move, since the move or the quote may then be older than it looks; the 1-hour response is null where an outage falls in that window stretched to the hour, past `b`'s close, or past the copied frames.

19,559,128 moves, 17,532,597 of them with a partner, gave 73,255,966 event rows: 726,927 with no quote of `b` seen yet, 6,905,952 with a side of `b` missing, 1,477,070 with an extreme quote, 3,123,916 in an outage or with a move or a quote older than one; 61,022,101 usable, 354,620 of them with an unknown lag.

## Results

**How fast the watched market moves.** The share of events in which `b`'s mid had changed within each horizon, and how often that first move agreed with the expected direction (with `a`'s direction for brackets and weather).

| Kind | Events a day | 1 s | 10 s | 1 min | 10 min | 1 h | First move agreed |
|---|---|---|---|---|---|---|---|
| Thresholds | 5,054,169 | 35% | 64% | 79% | 89% | 95% | 58% |
| Outcomes | 782,884 | 34% | 60% | 75% | 85% | 92% | 57% |
| Game | 7,995,255 | 21% | 57% | 76% | 86% | 92% | 51% |
| Horizons | 488,053 | 45% | 77% | 89% | 96% | 98% | 65% |
| Brackets | 125,694 | 30% | 57% | 77% | 93% | 97% | 46% |
| Weather | 54,244 | 1% | 5% | 16% | 51% | 87% | 50% |

**How far it moves**, in cents of mid in the expected direction, mean over events (the 10-minute interval is a cluster bootstrap over group-hours at 2.5% each side).

| Kind | 1 s | 10 s | 1 min | 10 min (interval) | 1 h |
|---|---|---|---|---|---|
| Thresholds | 0.98 | 0.68 | 0.53 | **0.47** (0.45 to 0.48) | 0.35 |
| Outcomes | 0.46 | 0.42 | 0.39 | **0.32** (0.29 to 0.34) | 0.03 |
| Game | 0.09 | 0.12 | 0.15 | **0.15** (0.13 to 0.18) | 0.06 |
| Horizons | 0.78 | 0.92 | 0.95 | **0.59** (0.51 to 0.66) | 0.24 |
| Brackets (in `a`'s direction) | −0.17 | −0.23 | −0.17 | −0.09 (−0.15 to −0.04) | 0.09 |
| Weather (in `a`'s direction) | 0.01 | 0.06 | 0.14 | 0.16 (0.11 to 0.21) | 0.17 |

The pass-through is a fraction of the 2-cent-or-more move, and for thresholds it peaks at one second and fades: the immediate co-movement partly reverts. A larger move does not help much: a threshold neighbour answers a 2-to-3-cent move with 0.36 cents at ten minutes and a 10-cent-or-more move with 0.92 (agreement 58 to 59% either way); the game kind answers every size with 0.12 to 0.17 cents. Adjacent brackets move against each other, as neighbours sharing probability mass should.

**Capacity against M4's bar**, dollars a day, the span's sums scaled to a day (the interval is a cluster bootstrap over the span's 101 hours).

| Kind | Stale quotes a day | Every quote: gross | Fee | **Net** (interval) | Reachable quotes a day | Reachable: gross | Fee | **Net** (interval) |
|---|---|---|---|---|---|---|---|---|
| Thresholds | 3,438,245 | +20,954,573 | 52,356,844 | **−31,402,271** (−43.5 M to −20.5 M) | 2,007,190 | −2,316,729 | 30,304,240 | **−32,620,969** (−43.4 M to −25.7 M) |
| Outcomes | 514,791 | +1,158,923 | 2,664,148 | **−1,505,225** (−1.92 M to −1.02 M) | 288,415 | +116,450 | 1,867,270 | **−1,750,819** (−2.07 M to −1.46 M) |
| Game | 1,154,349 | −2,004,537 | 30,017,586 | **−32,022,123** (−39.0 M to −26.8 M) | 811,887 | −1,780,297 | 26,424,842 | **−28,205,139** (−33.8 M to −23.8 M) |
| Horizons | 164,910 | +440,959 | 1,700,563 | **−1,259,604** (−1.80 M to −0.82 M) | 91,113 | −102,177 | 881,327 | **−983,504** (−1.29 M to −0.74 M) |

The sums are large because the quotes are many (millions a day) and often big (the median shown size is 40 to 126 contracts, the mean far more), and the fee is paid on the whole shown size every time; a taker who hit them all would pay $52 M a day in thresholds alone for $21 M of price moves. **The reachable gross is negative** for thresholds, horizons and game: a quote that did not react within a second goes on, weighted by its size, to move against the related market's signal. Where the book is slow, it is usually right.

**Is anyone taking them already?** The share of events with a taker hitting `b`'s stale quote on the expected side, against the share hitting it on the other side: thresholds 2.1% against 1.8%; outcomes 4.6% against 4.0%; game 8.3% against 8.0%; horizons 18.6% against 18.2%; weather 31% against 31%. Trading through a stale quote happens at the market's own trading rate, on both sides alike: nobody is systematically picking these quotes off, and nothing says anyone should.

**By activity tier of the watched market** (contracts a day over its days open in the span). The thin tier is the slowest and still mostly fast: in thresholds, markets under 100 contracts a day had moved within ten minutes in 85% of events (8.7 M events; response 0.44 cents; reachable net −$20 M a day), against 92% to 95% in the busier tiers; the busiest (10,000 and more) answers with 0.89 cents and is hit *with* in 14.0% of events, *against* in 12.2%. In every tier of every signed kind the net is negative.

**By category.** The largest responses are in thin, slow categories: Elections thresholds (181,787 events) answer with 1.66 cents at ten minutes, 63% agreement, 74% moved within ten minutes, and net +$39,511 a day under the every-quote reading, which the reachable reading turns into **−$39,003**; Crypto thresholds 0.71 cents (65% agreement); Commodities thresholds 0.66 (65%; 98% moved within ten minutes). Sports hold 83% of the usable events and answer with 0.15 (game) to 0.49 cents (thresholds).

**The slow spots.** The twenty series pairs with the largest reachable net are in `lag.json`; the first three are the AAA gas-price ladders of New Jersey, Colorado and Wisconsin at +$48,797, +$47,129 and +$43,502 a day (2,719, 20,520 and 10,855 events; responses 1.67, 1.40 and 0.48 cents; agreement 61, 63 and 55%; 291, 1,699 and 937 reachable quotes a day). They are the top of a ranking of about a thousand series pairs over four days, which is where noise lands: leads for a pre-registered test at most, not results, and none of them is a relation type.

## The readings, as a ladder

1. **Do nothing:** $0.
2. **Hit every stale quote at its shown size, M4's wording:** −$1.3 M to −$32 M a day a kind.
3. **Hit only the quotes that stood a second:** −$1.0 M to −$33 M a day a kind; their gross before fees is negative for three kinds of four.
4. **A taker with a model** of which stale quotes will follow: the ceiling is the gross of reading 2, +$21 M a day in thresholds before fees, most of it inside the first second (the 1-second response is twice the 10-minute one), which is not a seconds-scale game.

## What this says for SPARQ

SPARQ's founding thesis was a fair value from related markets. On Kalshi's related markets at a 2-cent resolution, that fair value is already in the neighbouring book within seconds, the pass-through is a fraction of a cent, and the quotes that lag are the ones that know better. The weather books are the exception in speed (half still unmoved after ten minutes), but S2.2 showed they are priced hours ahead of the public observation and their cross-series response here is 0.15 cents with coin-flip agreement. Model-based grouping (the model node, PLAN §9) could find relations this reading cannot, but the lag it would exploit is measured here as seconds, not minutes.

## Limitations

- Four days of frames, with sports (83% of usable events) dominant; a longer span changes the sums, not the shape.
- The response is read on the mid, so a one-sided book has none: 9% of the event rows met a watched market with a side missing and left.
- The capacity hits the whole shown size at the best; depth behind it and the fill a taker would really get are not in the frames (the order-book recorder, parked in §9). A quote that stood unanswered is counted once an hour it stood, so a long-standing quote's capacity scales with the hours, not with the one quote.
- The game kind pairs a team's markets at the other series' central line, which for a moneyline against a spread is not the same probability; a model could pair better.
- The horizons kind pairs by the nearest level, and the 15-minute markets' level is the price at their start, so the pair is always near the money; the daily ladder's far levels respond little by construction.
- The fee terms are each series' current ones, as in S3.1.
- After an outage of the feed a market's quote is unknown until its next frame, so the events that follow one are thinned until the books refresh: 4% of the event rows left for an outage, or for a move or a quote older than one.
