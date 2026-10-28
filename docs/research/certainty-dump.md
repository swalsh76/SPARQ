# The certainty dump and the 99-cent queue (S3.1)

**Outcome against M4's bar ($25 a day within one category, for a bid that joins the 99-cent queue when the price first reaches 99): cleared in one category, Mentions, at $39 a day for a 100-contract bid and $313 for 1,000, on thin evidence; every other category loses, Sports and Crypto by about $12,800 a day.** The whole 99-cent queue, all makers together, nets about $240,000 a day, but a late joiner gets a rationed share of its gains and a near-full share of its losses. The owner chooses what follows (M1).

## Data snapshot

- **History:** KAPS's download on the analytics node. Markets whose close time is on or before 2026-10-10 UTC (M3), with a binary result: 17,538,187 markets, 65 days of trades (2026-08-07 to 2026-10-10). Every trade read precedes 2026-10-11; results and settlement times are read. A market's end is the earlier of its close and its `settlement_ts`.
- **Live frames:** the ingest's ticker frames and trades, copied with `live extract`: 2026-10-24 21:00 to 2026-10-28 05:00 UTC, 80 hours (the ingest's tables hold nothing earlier), 75.5 M frames and 15.4 M trades, with seven logged outages of the ticker feed. No outcome is read from them.
- **Run:** `python -m sparq_analytics.survey dump --kalshi DIR --live DIR --out DIR` at commit `ea133c5` (the later commits change tests only). Every figure below is in `dump.json`; the run takes about ten minutes.

## Method

- **The dump.** Every trade at 97 cents or more for either side, before the market's end (75.9 M of them), with that side's price, whether it won, and who took: the taker bought the **dead side** at 1 to 3 cents, so a bid resting on the near-certain side was filled (a holder exiting or a lottery buyer; the tape cannot tell them apart), or the taker **lifted** the near-certain side.
- **The regime.** A side's 99 regime starts at its first trade at 99 after which it never printed below 97 again before the end. The winning side's regime is where a 99-cent bid collects; the losing side's is a **false regime**. 935,895 regimes, 718 of them false.
- **Two bidders**, each resting 100 (or 1,000) contracts at 99 in every episode the live frames show:
  - **reactive:** joins when the best bid first reaches 99, behind the size then shown (the queue proxy, from the ticker frame; it misses the depth behind 99 and cancellations ahead). Its risk is every side that printed 99 to a resting bid and then lost (**any-time reversals**: 2,775 in 65 days; the unit is the side, since a market whose both sides printed 99 would have had a bid on each), since it cannot tell them apart;
  - **knowing:** joins an hour before the onset, behind the size shown at 99 then (usually none), which only knowledge of the outcome allows. Its risk is the false regimes.
- **Gains and losses, both first in, first out.** A cent on the contracts the live dump lets the bid fill behind its queue, in the share of markets that paid (the history's rate by market). Ninety-nine cents on the contracts a reversal's dump would have filled behind the same queue (each history reversal's dump paired with each live queue), in the share that reversed. The maker fee where the series charges one (163 of 15,370 series). The capital tied is the fills' cost for the median time to settlement; the collateral is what the resting bids hold at any moment.
- **Left-censored episodes** (begun in the span's first hour, 9,897 of 638,318) and the 14,045 overlapping a logged outage of the ticker feed take no part in the fills.

## Results

**The reactive bidder, 100 contracts a market, dollars a day.** Gains are a cent on the fills; losses are 99 cents on a reversal's fills; "episodes" are the 99 regimes seen a day in the live frames.

| Category | Episodes a day | Reversal rate (history, by market) | Gain | Loss | **Net** | Net at 1,000 contracts | Collateral held |
|---|---|---|---|---|---|---|---|
| Sports | 55,911 | 0.48% (1,454 of 304,435) | 2,020 | 14,822 | **−12,809** | −106,801 | 4,549 |
| Crypto | 81,049 | 0.69% (932 of 134,496) | 1,022 | 13,852 | **−12,830** | −91,732 | 331,541 |
| Commodities | 11,946 | 0.62% | 164 | 2,359 | **−2,195** | −17,539 | 739 |
| Economics | 5,668 | 0.79% | 16 | 1,879 | **−1,863** | −13,652 | 403 |
| Financials | 25,455 | 0.82% | 39 | 1,025 | **−987** | −4,769 | 729 |
| Entertainment | 1,233 | 0.89% | 14 | 764 | **−750** | −5,686 | 4 |
| Climate and Weather | 2,611 | 0.46% | 246 | 463 | **−218** | −1,813 | 664 |
| Politics | 415 | 0.75% | 14 | 147 | **−133** | −1,272 | 107 |
| Elections | 656 | 0.31% | 14 | 133 | **−118** | −923 | 45 |
| Science and Technology | 183 | 0.86% | 4 | 106 | **−102** | −810 | 1 |
| **Mentions** | 230 | 0.44% (28 of 6,434) | 74 | 34 | **+39** | **+313** | 900 |

**Why it loses.** The gains are rationed by the queue: in Sports the median queue at the onset is 281 contracts, about half of the 561,812 true regimes in the history saw no dump at all, and the 100-contract bid fills 203,002 contracts a day over 55,911 episodes, 3.6 a bid. A reversal's panic is not rationed the same way: the 1,454 Sports reversals dumped 13.4 M contracts, 9,230 a market, far past a 281-contract queue, so the bid takes its whole size in most of them. Only in Crypto, where the median queue is 14,000 contracts (bots), does the queue shield the bid from part of the loss, and the reversal rate there is the highest.

**Mentions is the exception, thinly.** 6,406 winning sides and 28 reversals in the history; 230 episodes a day in 80 hours of frames; a dump of 1,372 contracts a regime-hour, the heaviest after Sports. The net of $39 a day at 100 contracts rests on 28 reversals: at the one-sided 0.025 upper bound of a Poisson count (about 40), the loss rises to about $49 and the net falls to about $25, at the bar. The gain side rests on 3.3 days of frames. This is a lead, not a result.

**The knowing bidder, 100 contracts, dollars a day: Sports +7,166 (gain 7,351, loss 160), Mentions +153, Weather +163, Economics +212, Entertainment +133, Crypto +47; at 1,000 contracts Sports +39,543.** This is the ceiling for a certainty engine that is right 99.9% of the time and an hour early. Nothing is: a game's result is not knowable an hour before the book prices it, and a mention or a race call moves the book within seconds.

**The whole queue.** Every maker at 99 cents together, over the 65 days, from the history alone (a cent on every dump into the winning side's 99 bids, 99 cents on every dump into the losing side's):

| Category | Dumped into winning 99 bids (M contracts) | Gained ($) | Dumped into losing 99 bids (M) | Lost ($) | Net a day ($) |
|---|---|---|---|---|---|
| Crypto | 1,834 | 18,338,783 | 5.18 | 5,127,603 | 203,249 |
| Commodities | 122 | 1,220,483 | 0.31 | 303,557 | 14,106 |
| Sports | 1,367 | 13,665,879 | 13.4 | 13,282,130 | 5,904 |
| Climate and Weather | 50.7 | 507,271 | 0.15 | 144,097 | 5,587 |
| Elections | 30.0 | 299,747 | 0.02 | 18,026 | 4,334 |
| Economics | 26.0 | 260,126 | 0.02 | 18,597 | 3,716 |
| Mentions | 19.4 | 193,529 | 0.14 | 136,860 | 872 |
| Entertainment | 8.8 | 88,338 | 0.04 | 41,512 | 720 |
| Science and Technology | 3.1 | 31,019 | 0.01 | 5,150 | 398 |
| Financials | 2.9 | 29,307 | 0.01 | 13,374 | 245 |
| Politics | 6.7 | 66,738 | 0.06 | 56,059 | 164 |

About $240,000 a day in all, 85% of it in crypto 15-minute markets (`KXBTC15M` alone took 1,565 M contracts in 6,161 regimes), where the queue is made of bots. In Sports the reversals take back 97% of the gross.

**The regimes.** The winning side's 99 regime lasts a median of 22 minutes to the end in Sports (p90 2.2 hours), 5 minutes in Crypto, 42 minutes in Weather, 11 hours in Economics and 165 hours in Elections; nothing after a market's end counts, and some markets settle before their scheduled close. Settlement follows the end by a median of 3 minutes. The false regimes sit in hourly and 15-minute financial and commodity markets (`KXNKY` 51, `KXPLATINUMH` 41, `KXDJI` 30) and in MLB home-run markets (`KXMLBHRR` 38), not in game winners.

## Benchmark ladder

- **The raw price.** A 99-cent contract pays 99.1% to 99.7% of the time by market (the reactive rates above) and 99.6% to 99.96% in the hindsight regimes: calibrated, as K0.5 found, with 0.1 to 0.7 cents of fair value above the price before any queue effect.
- **A bias correction by price bucket.** The 99 bucket's realized rate by category is that correction; it is what the reactive bidder's rate measures, and it does not survive the fill asymmetry.
- **A naive maker.** The whole queue above: about $240,000 a day to every maker at 99 cents together. The marginal late bid, which is what SPARQ could place, is the first table.

## What is parked

The 99-cent bids stay in PLAN §9 with these numbers. Mentions is the one lead, to be read again once the live frames hold more than 3.3 days (the ingest keeps recording), before any strand is proposed on it. The knowing bidder's figures size a certainty engine's ceiling and say that none can reach it in these categories.
