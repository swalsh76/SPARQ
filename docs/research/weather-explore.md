# Weather against station observations: the exploration (S2.2)

**Outcome: no rules (W9, owner, 2026-10-28).** Taking stale quotes after a station report proves a market's result made almost nothing, because the book has usually priced the result long before the report proves it. S2.3's pre-registered test is not run, and the test part stays unopened.

## Data snapshot

- **Trades and markets:** KAPS's history download on the analytics node (2026-08-07 to 2026-10-22), read for markets whose day is in the fit part (2026-08-07 to 2026-09-28) or the validate part (2026-09-29 to 2026-10-10). No market of the test part (2026-10-11 to 2026-10-22) was read. Its first two days' trade files were opened only for the validate markets' last trades, since a market trades until about 01:00 local after its day.
- **Reports:** the Iowa Environmental Mesonet's archive of routine and special reports for 39 stations, 2026-08-01 to 2026-10-28 (S2.1).
- **Scope:** 24 daily-high series and `KXRAIN` (W1); 8,397 settled markets on the fit part and 2,088 on the validate part.
- **Run:** `python -m sparq_analytics.weather explore --part fit|validate` at commit `ff195cc`. Every figure below is in its output (`explore-PART.json`), and two runs give identical files.

## Method

- **When a report counts.** 10 minutes after its observation time (W4). A bound counts only where both the standard and the clock's climate day allow it.
- **The call.** A bracket is ruled out once the bound clears its top by 1 °F (W4), and rain counts from a report of at least 0.01 inch. The call is the first counted moment that settles the market. No call was wrong on either part.
- **Ours.** A trade after the call whose taker bought the side the call made certain.
  - It is priced one cent worse, with the taker fee rounded up to a cent as one order.
  - It is paid on Kalshi's result.
- **Capacity.** The upper bound takes every such trade. The lower bound takes only the first in each market that was worth taking when it came, with its price and fee below a dollar. The interval is a studentized bootstrap clustered by city-day.

## Results

| | fit (53 days) | validate (12 days) |
|---|---|---|
| Called markets (highs / rain) | 2,240 (1,972 / 268) | 532 (461 / 71) |
| Markets with a trade of ours | 19 (16 rain) | 1 |
| Upper bound: profit, a day | $2,173.60, $41.01 | $0.00 |
| Upper bound: cents a contract (lower 2.5%) | 2.03 (0.72) | 0 |
| Lower bound: profit, a day | $34.23, $0.65 | $0.00 |
| Signalled city-days | 19 | 1 |

W7 asks for a lower bound above zero over at least 100 signalled city-days and $25 a day. Neither part comes close, so no rule was proposed.

**The book is ahead of the reports.** In the called markets, the certain side last traded below 95 cents:

| | fit | validate |
|---|---|---|
| Highs: median lead before the call (10% of markets under) | 276 minutes (70) | 325 minutes (67) |
| Rain: median lead before the call (10% of markets under) | 41 minutes (10) | 40 minutes (12) |

Traders price the outcome from forecasts, radar and the trend, well before an hourly report proves it.

**After the call, the trading is holders selling certain wins at 99 cents.** That was 38,381 of the 40,159 fit-part trades on the ruled-out side: 8.7 M of their 8.8 M contracts (2.1 M on validate, all of them). Some may be takers buying the dead side at 1 cent; the trade record does not say which. The 1 cent goes to the maker, and these series charge no maker fee. It is a maker question, parked in PLAN §9.

**The settled high and the hourly reports.**
- *Fit part:* the settled high was above the day's highest hourly report on 679 of 1,086 city-days: by 1 °F on 440, by 2 °F on 202, by 3 °F on 32 and by 4 °F or more on 5.
  - On 426 of the 679, the six-hour maximum later showed it, a median of 4 hours after the hourly peak (p90 6). On 12 of these it was already showing by the hourly peak.
  - On 253 it never did.
- *Validate part:* the settled high was above the hourly reports on 137 of 253 city-days.

Earlier readings from the station (PLAN §9) would make the call sooner, but they would still have to beat traders' forecasts, not the hourly reports.

## Benchmark ladder

- **The raw price.** A taker who buys the certain side at the market price after the call is exactly "ours": $41 a day at most on the fit part and $0 on validate. In 2,221 of the 2,240 fit-part markets, no taker bought the certain side after the call at any price.
- **A bias correction by price bucket.** It corrects the price's calibration, and there is nothing to correct here. The certain side traded at 99 cents in 96% of the post-call trades on the other side, and in every one on the validate part.
- **A naive maker.** It rests a bid at 99 cents for the certain side once the call is made. The fills on offer to all makers together were 8.7 M contracts on the fit part (about $87,000, or $1,640 a day) and 2.1 M on validate (about $21,400, or $1,780 a day), at 1 cent each with no maker fee. This is a ceiling for everyone in the queue, not SPARQ's share, which only the recorded order books can show (PLAN §9).
