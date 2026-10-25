# S1.1: does a maker in second-tier markets earn after fees?

**Verdict: NO-GO.** On the held-out test part none of the four pre-registered rules has an after-fee edge whose corrected lower bound is above zero, and every rule had more than 300 events. The test part was opened once, under pre-registration digest `95eec43a8d2d…`, on 2026-10-25 at 20:49 UTC. Under PLAN §6.1 D3 a NO-GO stops S1 for a rethink; the same rules are not re-tested on a wider window.

## 1. The question

KAPS's review (ASSESSMENT §7.4) found, exploratorily, that in series of middle activity human-paced takers lose 4 to 7 cents a contract and the makers they trade with gain. If that holds out of sample, an automated maker at a seconds scale could collect it. S1.1 tests it the same way KAPS's K0.5 tested the price: rules fixed on the fit and validate parts, approved by the owner, then the test part read once.

## 2. Data and method

- **Data:** KAPS's download (77 trade days to 22 October 2026), K0.5's window (events opened from 7 August, closed before 19 October), split dates (fit before 29 September, validate before 10 October, test after) and stratified sample (40 million trades; Sports at 3.66%, Crypto 4.41%, Commodities 13.21%, Climate and Weather 33.85%, Financials 46.27%, the rest 100%). K0.5's test part had been read only for price bands, so it was fresh for these rules.
- **Features, with no look-ahead:** a series' activity is its median contracts per market **in the fit part**; a trade's quiet time is the seconds since the market's previous trade; its first trade is flagged.
- **Scoring:** a maker rule by K0.5's strict fill rule (a resting order counts as filled only if a later second traded through its level) with the conservative maker fee; a taker rule by the taker edge after fee. Pooled figures weighted by one over each category's share; intervals by K0.5's studentized bootstrap over events, **resampled within each category**; each rule at a one-sided tail of 0.025 / 4 = 0.00625.
- **Rules (approved by the owner from seven candidates read on the validate part; `middle-tier-rules.json`):**
  - **R1** maker, middle-activity series (median 10,000 to 100,000 contracts a market);
  - **R2** maker, middle-activity series, human pace (1 second to 1 hour after the previous trade);
  - **R4** maker, series of 100 to 100,000 contracts a market, human pace;
  - **R5** taker, a market's first trade.
  Dropped before the test: R3 (never stale; the same as R1 to within 0.01 cent), R6 (Sports only; 127 validate events, short of 300) and R7 (New York daytime; a weaker part of R1).

## 3. Results

Cents a contract after fees, with the lower bound at the rule's tail in brackets.

| Rule | Validate events | Validate | Test events | **Test** | Maker optimistic (test) |
|---|---|---|---|---|---|
| R1 middle, maker | 870 | +2.34 (-2.29) | 599 | **-0.09 (-2.00)** | +1.00 |
| R2 middle, human pace, maker | 870 | +5.79 (-1.61) | 599 | **+2.71 (-3.66)** | +3.19 |
| R4 100 to 100k, human pace, maker | 3,690 | +3.91 (-2.26) | 2,518 | **+2.51 (-1.30)** | +3.30 |
| R5 first trade, taker | 4,660 | +4.66 (-1.82) | 3,947 | **-1.29 (-3.98)** | (taker) |

(Validate figures are at the seven-candidate tail of 0.00357, test figures at 0.00625.)

What this shows:

- **The broad middle-tier maker (R1) has no edge out of sample:** -0.09 cents, with takers in those series losing 2.11 rather than 4.44. The validate reading was partly chance.
- **The stale first trade (R5) reversed:** the taker who hits a market's first quote lost 1.29 cents on the test part.
- **The human-paced maker (R2, R4) kept its sign but not its size,** and its intervals include zero: +2.71 and +2.51 cents on the test part against +5.79 and +3.91 on validate. It is the only pattern that was positive in both parts. It is not a result: it did not clear the pre-registered bar, and part of every validate figure is selection.
- **The strict and optimistic fill rules are close** (for R4, +2.51 against +3.30), so the gap between them, about 0.8 cent, is small next to the noise.

## 4. What it can and cannot say

It can say that, with the trade tape alone and K0.5's fill rule, none of the four rules shows a maker or taker edge in second-tier markets that survives a strict, pre-registered test on 11 weeks of data.

It cannot say:

- **How a real quoter would fare.** The strict fill rule is a model: it assumes our order would have been at the front of the queue whenever a later trade went through its price, and it cannot see the order book, cancellations or the queue. A shadow quoter with the live book (S1.2) measures that, but S1.2 starts only on a passed S1.1.
- **Anything about a smaller, sharper rule.** The human-paced maker is the one consistent lead; testing it again on these same days would spend the protection the pre-registration gave. Only new data (trades recorded after 22 October) can test it cleanly.
- **Anything about other strands** in the parking lot (outside fair values with hedges, weather observations, late-game sports, the long-tail forecasts).

## 5. What follows (for the owner)

Under D3 S1 stops for a rethink. Options, not recommendations made here:

1. **Stop** the maker direction.
2. **A forward test of one rule** (the human-paced maker, R4's definition) on trades recorded **after** 22 October, by KAPS's ingest and a new download of later days, pre-registered now with a fixed end date; this is new data, not a re-test of the same days.
3. **Another strand** from PLAN §9.

## 6. Reproducing it

The SPARQ commit on branch `s1/1-middle-tier`; on the analytics node `~/sparq/check`, the script that runs `rules-register` and `rules-verdict --open-test` with the settings above, and the rules file `docs/research/middle-tier-rules.json`. The rules ledger refuses any other rules or settings for this test part. Data and outputs are not committed.
