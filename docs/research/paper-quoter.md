# A paper quoter in the thin tail: the fit part (S4.1)

**What this is.** The fit part of S4 (PLAN §6.4): a paper quoter replayed over the live frames and trades, asking what a maker standing 5 or 10 cents inside the thin tail's wide spreads would have filled and what the fills were worth against the mid afterwards. It chooses which rules to register for the test (T6); it proves nothing by itself, because every mark here is a mid, never a settlement, and the paper quote is invisible to the takers it credits itself with. **On paper the quotes come out ahead at every mark:** the 5-cent rule in uncontested books nets about $12,300 a day on 10-contract quotes at the close mark (lower bound $11,000; 15 cents a contract) and $32,200 on 50-contract ones; the one-sided rule about $870 a day. The optimism sits in the markets still open at the span's end, whose fills mark at 20 cents a contract against 9 for the markets that closed; in the markets closing within 48 hours, where the test will read a settlement, the 5-cent rule reads $4,400 a day and the one-sided rule $60. The reservations are in the limitations, and the test with settlement (S4.2) is the reading that counts.

## Data snapshot

- **Live frames and trades:** the ingest's ticker frames and trades on the server host, copied to the analytics node with `live extract` on 2026-10-29: 2026-10-24 21:00 to 2026-10-29 04:00 UTC (103 hours). **2026-10-28 is dropped** (T6: an outage of four hours), so 79 hours are read, in three whole days (10-25 to 10-27) and two parts (10-24 evening, 10-29 morning); nothing is carried across the dropped day, and no mark is read from it. The gap log's other 30 outages, all under two minutes, take their time off the standing quotes.
- **Markets:** the ingest's market list (1.30 M markets) for the event, category and close of each market; the tiers are S3.3's rule over the span (contracts a day over the days open). **Fees:** each series' current terms from the history download's series table; the paper quote is a maker, so the maker fee applies, which most thin series do not charge (fee $2.56 a day on the headline rule; 197 fills of 62,405 in series without known terms, marked but not netted).
- **No outcome is read** (M3 and T6: the fit part is mid-marked; settlement waits for the registered test).
- **Run:** `python -m sparq_analytics.paper fit --kalshi DIR --live DIR --out DIR` at commit `0a66bfb`, about a minute; every figure below is in `paper.json`, every fill in `paper-fills.parquet`.

## Method

The quoter follows PLAN §6.4 (T1 to T6) exactly. For every frame of a **thin** market (under 1,000 contracts a day, every category) whose mid or standing price is between 3 and 97 cents, and whose book is **uncontested** (a spread wider than 20 cents) or **one-sided**, it places a paper bid and ask **k cents inside** the standing quotes (k = 5 and 10), or 20 cents from the standing quote on an empty side, of **S contracts** (10 and 50). The quote stands from its frame to the market's next frame or its close. A taker trade strictly after the frame, at a price **strictly beyond** the paper quote (a yes taker above the paper ask, a no taker below the paper bid; block trades never), fills it for the smaller of S and the trade's size; one fill a quote and side, across hours too. Each fill is marked to the mid **10 minutes** and **1 hour** later (null past the market's close, the copied frames or a dropped day) and to the market's **last two-sided mid at or after the fill** in the kept hours (its pre-close mid when the market closed in the span; otherwise the fill is **censored** and marked at the last mid seen; a fill no two-sided frame followed has no close mark), less the maker fee. Intervals are cluster bootstraps over (event, day) clusters, 2.5% each side.

36 M frames carried a paper quote (two values of k each); 136,162 fills on 26,800 markets.

## Results

**By rule** (the figures a day scale the 79 hours; "net" is after the maker fee, over the marked fills whose fee is known; the interval is the close mark's).

| Rule (state, k, S) | Fills | Markets | Contracts | Quotes standing at once | Fills a quote-hour | Net a day at 10 min | at 1 h | at the close mark (interval) | Cents a contract at close |
|---|---|---|---|---|---|---|---|---|---|
| Uncontested, 5, 10 | 62,405 | 24,576 | 314,510 | 11,523 | 0.069 | $13,534 | $9,804 | **$12,297** ($10,972 to $14,020) | 15.2 |
| Uncontested, 10, 10 | 62,819 | 24,676 | 315,999 | 11,523 | 0.069 | $9,630 | $7,112 | **$8,246** ($7,197 to $9,561) | 10.1 |
| Uncontested, 5, 50 | 62,405 | 24,576 | 798,619 | 11,523 | 0.069 | $37,200 | $26,667 | **$32,248** ($28,492 to $37,241) | 16.3 |
| Uncontested, 10, 50 | 62,819 | 24,676 | 801,646 | 11,523 | 0.069 | $27,211 | $19,782 | **$22,381** ($19,363 to $26,245) | 11.3 |
| One-sided, 5, 10 | 5,447 | 3,644 | 33,520 | 14,384 | 0.005 | $755 | $790 | **$871** ($597 to $1,395) | 11.8 |
| One-sided, 10, 10 | 5,491 | 3,682 | 33,711 | 14,384 | 0.005 | $424 | $528 | **$528** ($285 to $985) | 7.1 |
| One-sided, 5, 50 | 5,447 | 3,644 | 92,386 | 14,384 | 0.005 | $1,947 | $2,025 | **$2,270** ($1,565 to $3,410) | 11.4 |
| One-sided, 10, 50 | 5,491 | 3,682 | 92,814 | 14,384 | 0.005 | $1,054 | $1,291 | **$1,344** ($723 to $2,321) | 6.7 |

The close mark exists for 53,919 of the 62,405 uncontested fills (a two-sided frame followed the fill) and 3,925 of the 5,447 one-sided ones; the 1-hour mark's lower bounds are $8,546 and $6,147 for the uncontested 10-contract rules, $23,001 and $17,026 for the 50-contract ones.

Two things decide the shape of the table. **The 5-cent quote beats the 10-cent one at every mark** with the same fills: a taker who crosses a 30-cent spread crosses it whatever stands inside, and the median trade printed exactly k cents beyond the paper quote, that is, at the displayed touch, so the paper quote 5 cents inside is hit first and earns 5 cents more a contract. **The size S mostly scales**: 36% of the filling trades were 10 contracts or more and 11 to 14% were 50 or more (the median trade is 3 to 4 contracts), so the 50-contract quote fills 2.5 times the contracts at a slightly better rate (the big trades pay more for the whole size).

**The fills' fate.** Fills are frequent per market and rare per quote: a quote stands about 14 hours for one fill. Round trips are rare (both sides of one quote filled in 0.1% of quotes). A fill's market traded again before the span's end 85% of the time (uncontested) and 79% (one-sided). The median fill is 0.9 hours from its market's close in uncontested books (Sports 0.5 hours: in-game and same-day markets) and 5.1 hours in one-sided ones. **29% of uncontested fills are censored** (their market still open at the span's end), and the censored fills read far richer than the closed ones: **19.7 cents a contract at the last mid against 9.0 at the pre-close mid** (medians 21.0 and 8.5); the long-dated categories are mostly censored (Economics 86%, Entertainment 91%, Elections 86%).

The per-fill mark at the close is wide: for the 5-cent uncontested rule the 5th percentile is −25.5 cents a contract, the quartiles −4.5, +13.0 and +29.5, the 95th +51.5. The mean by day runs from 7.9 to 15.3 cents on the five calendar days, with no day negative.

**By category** (uncontested, k = 5, S = 10; the close mark).

| Category | Fills | Markets | Net a day | Cents a contract | Lower bound | Censored | Median hours to close |
|---|---|---|---|---|---|---|---|
| Sports | 44,530 | 19,939 | $6,685 | 13.9 | $5,719 | 17% | 0.5 |
| Crypto | 4,244 | 925 | $1,363 | 18.3 | $915 | 56% | 75 |
| Economics | 2,331 | 347 | $1,017 | 19.9 | $728 | 86% | 358 |
| Financials | 4,131 | 968 | $935 | 13.1 | $622 | 58% | 124 |
| Commodities | 2,347 | 896 | $854 | 18.6 | $597 | 23% | 0.2 |
| Entertainment | 1,547 | 468 | $608 | 19.2 | $350 | 91% | 1,327 |
| Climate and Weather | 1,189 | 304 | $422 | 20.5 | $228 | 60% | 766 |
| Science and Technology | 962 | 232 | $339 | 20.1 | $239 | 62% | 126 |
| Politics | 137 | 55 | $44 | 17.8 | $0 | 80% | 1,537 |
| Mentions | 701 | 258 | $32 | 3.3 | $8 | 26% | 2.2 |
| Elections | 275 | 174 | $0 | 0.0 | −$24 | 86% | 8,930 |
| Companies | 11 | 10 | −$3 | −10.4 | −$81 | 100% | 4,500 |

Sports carries 71% of the fills and 54% of the dollars, and its markets close within the hour, so its close mark is nearly a settlement mark; the long-dated categories' figures are last-mid marks on censored fills, and the test will not settle them inside its window. Companies' one-sided books were quoted 151 quote-hours a day and never filled. **The one-sided rule** earns 16.3 cents a contract in Crypto (1,908 fills, lower bound $197 a day) and 8.5 in Sports (2,247, $160), loses in Weather, Elections and Politics, and its edge thins in the markets that close soon (below).

**By tier.** Markets under 100 contracts a day: 12,821 fills, 19.1 cents a contract at the close, $2,835 a day; 100 to 1,000 a day: 49,584 fills, 14.3 cents, $9,461.

**Markets that close soon**, where the registered test will read a settlement (T6 and T7): 79% of the uncontested fills are in markets closing within 7 days of the fill (10% censored, 83% with a close mark) and 71% within 48 hours (1% censored). On those the 5-cent rule nets **$6,050 a day at the close mark (11.5 cents a contract) within 7 days and $4,385 (10.2 cents) within 48 hours**, on 10-contract quotes. The one-sided rule's net falls to $437 a day (8.2 cents) within 7 days and **$60 (1.6 cents) within 48 hours**; its 10-cent variant is negative there.

## What a full-scale quoter would carry

The paper quoter quotes everything in scope at once: about **11,500 uncontested quotes standing** at any moment (14,400 one-sided), each side risking its size times its price. At 10 contracts and a mean paper price of 45 cents that is about **$115,000 of collateral** for the uncontested rule ($576,000 at 50 contracts), earning on paper $12,300 a day: 11% a day on collateral, a figure that says more about the mid as a fair value than about the opportunity. Inventory: 314,510 contracts filled in 79 hours, mostly held to a close under an hour away (Sports) or for days (the rest).

## The readings, as a ladder

1. **Do nothing:** $0.
2. **The paper quoter on the fit part, mid-marked:** $12,300 a day (lower bound $11,000) on 10-contract quotes 5 cents inside uncontested thin books; $870 for one-sided books.
3. **The same, in the markets that close within 48 hours** (where a settlement will be read): $4,400 a day on 10-contract quotes; one-sided $60.
4. **The registered test** (S4.2): at least seven new live days, each fill marked to settlement. What it can show that this cannot: whether the mid of a wide thin book is near where the market settles, which is the whole question.

## The rules proposed for registration (T6: at most four)

Every rule quotes 5 cents inside (the 10-cent variant is worse at every mark with the same fills) and, so that the test reads a settlement, only markets closing within 7 days of the quote:

1. **R1:** uncontested books, every category, 10 contracts a side.
2. **R2:** uncontested books, every category, 50 contracts a side.
3. **R3:** uncontested books, Sports alone, 10 contracts a side (the bulk of the fills, settled within the hour).
4. **R4:** one-sided books, every category, 10 contracts a side (weak on the fit part: $437 a day at 7 days, $60 at 48 hours; registered to be tested, not assumed).

T7's bar for each: settlement-marked dollars a day clearing $25 at the lower bound of the (event, day) interval, with the 1-hour mid mark positive as well.

## Limitations

- **The marks are mids.** The fair value is the mid of a book 20 cents or more wide, and the "close" mark is the last two-sided mid before the close, not the settlement; 29% of fills are censored and those read twice as rich as the closed ones. Only S4.2's settlement marking answers this.
- **The taker's reaction is not modelled.** A tighter displayed spread may draw more flow, or less; a taker who crossed 30 cents may not have crossed 20. The paper quote is credited with the first trade beyond it as if nothing changed.
- **The paper quote is first in line** because it improves the touch; a competitor doing the same would halve the fills.
- **Four and a half days, three of them whole**, sports-heavy (71% of fills), with 2026-10-28 dropped; a Sunday to Tuesday with a Monday night and two NFL-free weekdays.
- **Capital is not priced.** The collateral above is what quoting everything at once would tie up; a real quoter would choose markets, and the inventory risk in a market that never trades again (15% of fills) is marked at a mid that may not exist.
- Trades in series without fee terms (0.3%) are marked but not netted; the fee is negligible anyway, most thin series charging no maker fee.
