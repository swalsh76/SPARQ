# The certainty dump and the 99-cent queue (S3.1)

**Outcome against M4's bar ($25 a day within one category, for a bid that joins the 99-cent queue when the price first reaches 99): cleared in one category, Mentions, at $45 a day for a 100-contract bid and $361 for 1,000, on thin evidence; every other category loses, Sports and Crypto by about $13,000 a day.** The whole 99-cent queue, all makers together, nets about $240,000 a day, but a late joiner gets a rationed share of its gains and a near-full share of its losses. The owner chooses what follows (M1).

## Data snapshot

- **History:** KAPS's download on the analytics node. Markets whose close time is on or before 2026-10-10 UTC (M3), with a binary result: 17,538,187 markets, 65 days of trades (2026-08-07 to 2026-10-10). Every trade read precedes 2026-10-11; results and settlement times are read. A market's end is the earlier of its close and its `settlement_ts`.
- **Live frames:** the ingest's ticker frames and trades, copied with `live extract`: 2026-10-24 21:00 to 2026-10-28 05:00 UTC, 80 hours (the ingest's tables hold nothing earlier), 75.5 M frames and 15.4 M trades. No outcome is read from them.
- **Run:** `python -m sparq_analytics.survey dump --kalshi DIR --live DIR --out DIR` at commit `2a69a94` (the later `4d22c1b` changes only how a category the history never saw is reported, which none of these is). Every figure below is in `dump.json`; the run takes about ten minutes.

## Method

- **The dump.** Every trade at 97 cents or more for either side (76.1 M of them), with that side's price, whether it won, and who took: the taker bought the **dead side** at 1 to 3 cents, so a bid resting on the near-certain side was filled (a holder exiting or a lottery buyer; the tape cannot tell them apart), or the taker **lifted** the near-certain side.
- **The regime.** A side's 99 regime starts at its first trade at 99 after which it never printed below 97 again before the end. The winning side's regime is where a 99-cent bid collects; the losing side's is a **false regime**. 939,666 regimes, 716 of them false.
- **Two bidders**, each resting 100 (or 1,000) contracts at 99 in every episode the live frames show:
  - **reactive:** joins when the best bid first reaches 99, behind the size then shown (the queue proxy, from the ticker frame; it misses the depth behind 99 and cancellations ahead). Its risk is every market whose side printed 99 to a resting bid and then lost (**any-time reversals**: 2,776 markets in 65 days), since it cannot tell them apart;
  - **knowing:** joins an hour before the onset, behind the size shown at 99 then (usually none), which only knowledge of the outcome allows. Its risk is the false regimes.
- **Gains and losses, both first in, first out.** A cent on the contracts the live dump lets the bid fill behind its queue, in the share of markets that paid (the history's rate by market). Ninety-nine cents on the contracts a reversal's dump would have filled behind the same queue (each history reversal's dump paired with each live queue), in the share that reversed. The maker fee where the series charges one (163 of 15,370 series). The capital tied is the fills' cost for the median time to settlement; the collateral is what the resting bids hold at any moment.
- **Left-censored episodes** (begun in the span's first hour, 9,897 of 638,318) take no part in the fills.

## Results

**The reactive bidder, 100 contracts a market, dollars a day.** Gains are a cent on the fills; losses are 99 cents on a reversal's fills; "episodes" are the 99 regimes seen a day in the live frames.

| Category | Episodes a day | Reversal rate (history, by market) | Gain | Loss | **Net** | Net at 1,000 contracts | Collateral held |
|---|---|---|---|---|---|---|---|
| Sports | 56,913 | 0.47% (1,455 of 308,897) | 2,068 | 14,801 | **−12,741** | −106,285 | 4,891 |
| Crypto | 82,429 | 0.69% (932 of 134,496) | 1,032 | 13,981 | **−12,948** | −92,707 | 338,225 |
| Commodities | 11,988 | 0.62% | 164 | 2,369 | **−2,205** | −17,608 | 755 |
| Economics | 5,717 | 0.79% | 19 | 1,888 | **−1,869** | −13,711 | 413 |
| Financials | 25,764 | 0.82% | 39 | 1,034 | **−996** | −4,812 | 797 |
| Entertainment | 1,334 | 0.89% | 17 | 789 | **−772** | −5,857 | 6 |
| Climate and Weather | 2,756 | 0.46% | 284 | 480 | **−196** | −1,688 | 982 |
| Politics | 439 | 0.75% | 15 | 152 | **−137** | −1,312 | 118 |
| Elections | 732 | 0.31% | 18 | 142 | **−124** | −976 | 50 |
| Science and Technology | 205 | 0.86% | 5 | 112 | **−107** | −845 | 1 |
| **Mentions** | 245 | 0.44% (28 of 6,436) | 81 | 37 | **+45** | **+361** | 1,017 |

**Why it loses.** The gains are rationed by the queue: in Sports the median queue at the onset is 294 contracts, half of the 565,582 true regimes in the history saw no dump at all, and the 100-contract bid fills 207,730 contracts a day over 56,913 episodes, 3.6 a bid. A reversal's panic is not rationed the same way: the 1,455 Sports reversals dumped 13.6 M contracts, 9,350 a market, far past a 294-contract queue, so the bid takes its whole size in most of them. Only in Crypto, where the median queue is 14,000 contracts (bots), does the queue shield the bid from part of the loss, and the reversal rate there is the highest.

**Mentions is the exception, thinly.** 6,408 winning markets and 28 reversals in the history; 245 episodes a day in 80 hours of frames; a dump of 1,106 contracts a regime-hour, the heaviest after Sports. The net of $45 a day at 100 contracts rests on 28 reversals: at the one-sided 0.025 upper bound of a Poisson count (about 40), the loss rises to about $53 and the net falls to about $28, at the bar. The gain side rests on 3.3 days of frames. This is a lead, not a result.

**The knowing bidder, 100 contracts, dollars a day: Sports +7,276 (gain 7,461, loss 160), Mentions +166, Weather +223, Economics +216, Entertainment +136, Crypto +50; at 1,000 contracts Sports +40,215.** This is the ceiling for a certainty engine that is right 99.9% of the time and an hour early. Nothing is: a game's result is not knowable an hour before the book prices it, and a mention or a race call moves the book within seconds.

**The whole queue.** Every maker at 99 cents together, over the 65 days, from the history alone (a cent on every dump into the winning side's 99 bids, 99 cents on every dump into the losing side's):

| Category | Dumped into winning 99 bids (M contracts) | Gained ($) | Dumped into losing 99 bids (M) | Lost ($) | Net a day ($) |
|---|---|---|---|---|---|
| Crypto | 1,834 | 18,338,783 | 5.18 | 5,127,603 | 203,249 |
| Commodities | 122 | 1,220,483 | 0.31 | 303,557 | 14,106 |
| Sports | 1,400 | 13,998,897 | 13.6 | 13,471,095 | 8,120 |
| Climate and Weather | 50.7 | 507,271 | 0.15 | 144,097 | 5,587 |
| Elections | 30.0 | 299,747 | 0.02 | 18,026 | 4,334 |
| Economics | 26.0 | 260,128 | 0.02 | 18,597 | 3,716 |
| Mentions | 19.4 | 194,354 | 0.14 | 136,860 | 884 |
| Entertainment | 8.8 | 88,338 | 0.04 | 41,512 | 720 |
| Science and Technology | 3.1 | 31,019 | 0.01 | 5,150 | 398 |
| Financials | 2.9 | 29,320 | 0.01 | 13,374 | 245 |
| Politics | 6.7 | 66,738 | 0.06 | 56,059 | 164 |

About $240,000 a day in all, 85% of it in crypto 15-minute markets (`KXBTC15M` alone took 1,565 M contracts in 6,161 regimes), where the queue is made of bots. In Sports the reversals take back 96% of the gross.

**The regimes.** The winning side's 99 regime lasts a median of 22 minutes to the end in Sports (p90 2.2 hours), 5 minutes in Crypto, 42 minutes in Weather, 11 hours in Economics and 165 hours in Elections. Settlement follows the end by a median of 3 minutes. The false regimes sit in hourly and 15-minute financial and commodity markets (`KXNKY` 51, `KXPLATINUMH` 41, `KXDJI` 30) and in MLB home-run markets (`KXMLBHRR` 38), not in game winners.

## Benchmark ladder

- **The raw price.** A 99-cent contract pays 99.1% to 99.7% of the time by market (the reactive rates above) and 99.6% to 99.96% in the hindsight regimes: calibrated, as K0.5 found, with 0.1 to 0.7 cents of fair value above the price before any queue effect.
- **A bias correction by price bucket.** The 99 bucket's realized rate by category is that correction; it is what the reactive bidder's rate measures, and it does not survive the fill asymmetry.
- **A naive maker.** The whole queue above: about $240,000 a day to every maker at 99 cents together. The marginal late bid, which is what SPARQ could place, is the first table.

## What is parked

The 99-cent bids stay in PLAN §9 with these numbers. Mentions is the one lead, to be read again once the live frames hold more than 3.3 days (the ingest keeps recording), before any strand is proposed on it. The knowing bidder's figures size a certainty engine's ceiling and say that none can reach it in these categories.
