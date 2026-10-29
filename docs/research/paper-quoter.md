# A paper quoter in the thin tail: the fit part (S4.1)

**What this is.** The fit part of S4 (PLAN §6.4): a paper quoter replayed over the live frames and trades, asking what a maker standing 5 or 10 cents inside the thin tail's wide spreads would have filled and what the fills were worth against the mid afterwards. It chooses which rules to register for the test (T6); it proves nothing by itself, because every mark here is a mid or the last mid before close, never a settlement, and the paper quote is invisible to the takers it credits itself with. **On paper the quotes come out ahead at every mark:** the 5-cent rule in uncontested books nets about $16,000 a day on 10-contract quotes (lower bound $14,600) and $44,000 on 50-contract ones, 17 to 18 cents a contract; the one-sided rule about $830 a day; every category but Elections, Politics and Companies is positive. The reservations are in the limitations, and the test with settlement (S4.2) is the reading that counts.

## Data snapshot

- **Live frames and trades:** the ingest's ticker frames and trades on the server host, copied to the analytics node with `live extract` on 2026-10-29: 2026-10-24 21:00 to 2026-10-29 04:00 UTC (103 hours). **2026-10-28 is dropped** (T6: an outage of four hours), so 79 hours are read, in three whole days (10-25 to 10-27) and two parts (10-24 evening, 10-29 morning). The gap log's other 30 outages, all under two minutes, take their time off the standing quotes.
- **Markets:** the ingest's market list (1.30 M markets) for the event, category and close of each market; the tiers are S3.3's rule over the span (contracts a day over the days open). **Fees:** each series' current terms from the history download's series table; the paper quote is a maker, so the maker fee applies, which most thin series do not charge (fee $2.56 a day on the headline rule; 197 fills of 62,406 in series without known terms, marked but not netted).
- **No outcome is read** (M3 and T6: the fit part is mid-marked; settlement waits for the registered test).
- **Run:** `python -m sparq_analytics.paper fit --kalshi DIR --live DIR --out DIR` at commit `3d3079d`, about a minute; every figure below is in `paper.json`, every fill in `paper-fills.parquet`.

## Method

The quoter follows PLAN §6.4 (T1 to T6) exactly. For every frame of a **thin** market (under 1,000 contracts a day, every category) whose mid or standing price is between 3 and 97 cents, and whose book is **uncontested** (a spread wider than 20 cents) or **one-sided**, it places a paper bid and ask **k cents inside** the standing quotes (k = 5 and 10), or 20 cents from the standing quote on an empty side, of **S contracts** (10 and 50). The quote stands from its frame to the market's next frame or its close. A taker trade strictly after the frame, at a price **strictly beyond** the paper quote (a yes taker above the paper ask, a no taker below the paper bid; block trades never), fills it for the smaller of S and the trade's size; one fill a quote and side. Each fill is marked to the mid **10 minutes** and **1 hour** later (null past the market's close or the copied frames) and to the market's **last two-sided mid in the span** (its pre-close mid when the market closed in the span; otherwise the fill is **censored** and marked at the last mid seen), less the maker fee. Intervals are cluster bootstraps over (event, day) clusters, 2.5% each side.

36 M frames carried a paper quote (two values of k each); 136,164 fills on 26,800 markets.

## Results

**By rule** (the figures a day scale the 79 hours; "net" is after the maker fee, over the fills whose fee is known; the interval is the close mark's).

| Rule (state, k, S) | Fills | Markets | Contracts | Quotes standing at once | Fills a quote-hour | Net a day at 10 min | at 1 h | at the close mark (interval) | Cents a contract at close |
|---|---|---|---|---|---|---|---|---|---|
| Uncontested, 5, 10 | 62,406 | 24,576 | 314,514 | 11,523 | 0.069 | $13,548 | $9,918 | **$15,982** ($14,576 to $17,400) | 16.8 |
| Uncontested, 10, 10 | 62,820 | 24,676 | 316,003 | 11,523 | 0.069 | $9,641 | $7,197 | **$11,233** ($10,070 to …) | 11.8 |
| Uncontested, 5, 50 | 62,406 | 24,576 | 798,623 | 11,523 | 0.069 | $37,229 | $26,951 | **$43,965** ($39,836 to …) | 18.2 |
| Uncontested, 10, 50 | 62,820 | 24,676 | 801,651 | 11,523 | 0.069 | $27,234 | $19,999 | **$31,906** ($28,624 to …) | 13.2 |
| One-sided, 5, 10 | 5,447 | 3,644 | 33,520 | 14,384 | 0.005 | $756 | $793 | **$832** ($554 to …) | 9.2 |
| One-sided, 10, 10 | 5,491 | 3,682 | 33,711 | 14,384 | 0.005 | $425 | $529 | **$420** ($177 to …) | 4.6 |
| One-sided, 5, 50 | 5,447 | 3,644 | 92,386 | 14,384 | 0.005 | $1,950 | $2,031 | **$2,103** ($1,402 to …) | 8.5 |
| One-sided, 10, 50 | 5,491 | 3,682 | 92,814 | 14,384 | 0.005 | $1,056 | $1,292 | **$993** ($373 to …) | 4.0 |

(The upper bounds are in `paper.json`; the 1-hour mark's lower bounds are $8,641 and $6,270 for the uncontested 10-contract rules, $23,294 and $17,322 for the 50-contract ones.)

Two things decide the shape of the table. **The 5-cent quote beats the 10-cent one at every mark** with the same fills: a taker who crosses a 30-cent spread crosses it whatever stands inside, and the median trade printed exactly k cents beyond the paper quote, that is, at the displayed touch, so the paper quote 5 cents inside is hit first and earns 5 cents more a contract. **The size S mostly scales**: 36% of the filling trades were 10 contracts or more and 11 to 14% were 50 or more (the median trade is 3 to 4 contracts), so the 50-contract quote fills 2.5 times the contracts at a slightly better rate (the big trades pay more for the whole size).

**The fills' fate.** Fills are frequent per market and rare per quote: a quote stands about 14 hours for one fill. Round trips are rare (both sides of one quote filled in 0.1% of quotes). A fill's market traded again before the span's end 85% of the time (uncontested) and 79% (one-sided). The median fill is 0.9 hours from its market's close in uncontested books (Sports 0.5 hours: in-game and same-day markets) and 5.1 hours in one-sided ones. **29% of uncontested fills are censored** (their market still open at the span's end), and the censored fills read richer than the closed ones: 19.8 cents a contract at the last mid against 12.2 at the pre-close mid (medians 21.5 and 13.0); the long-dated categories are mostly censored (Economics 86%, Entertainment 91%, Elections 86%).

The per-fill mark at the close is wide: for the 5-cent uncontested rule the 5th percentile is −23.5 cents a contract, the quartiles −2.0, +14.5 and +31.5, the 95th +48.5. The mean by day runs from 10.1 to 16.7 cents on the five calendar days, with no day negative.

**By category** (uncontested, k = 5, S = 10; the close mark).

| Category | Fills | Markets | Net a day | Cents a contract | Lower bound | Censored | Median hours to close |
|---|---|---|---|---|---|---|---|
| Sports | 44,531 | 19,939 | $9,855 | 16.3 | $8,839 | 17% | 0.5 |
| Crypto | 4,244 | 925 | $1,485 | 18.9 | $1,006 | 56% | 75 |
| Commodities | 2,347 | 896 | $1,113 | 20.7 | $823 | 23% | 0.2 |
| Economics | 2,331 | 347 | $1,019 | 20.0 | $739 | 86% | 358 |
| Financials | 4,131 | 968 | $981 | 13.4 | $644 | 58% | 124 |
| Entertainment | 1,547 | 468 | $631 | 19.4 | $358 | 91% | 1,327 |
| Climate and Weather | 1,189 | 304 | $435 | 20.6 | $249 | 60% | 766 |
| Science and Technology | 962 | 232 | $349 | 20.3 | $245 | 62% | 126 |
| Mentions | 701 | 258 | $66 | 5.9 | $31 | 26% | 2.2 |
| Politics | 137 | 55 | $45 | 17.8 | −$1 | 80% | 1,537 |
| Elections | 275 | 174 | $6 | 1.0 | −$21 | 86% | 8,930 |
| Companies | 11 | 10 | −$3 | −10.4 | −$81 | 100% | 4,500 |

Sports carries 71% of the fills and 62% of the dollars, and its markets close within the hour, so its close mark is nearly a settlement mark; the long-dated categories' figures are last-mid marks on censored fills, and the test will not settle them inside its window. **The one-sided rule** earns 12.7 cents a contract in Crypto (1,908 fills, lower bound $166 a day) and 6.2 in Sports (2,247, $131), loses in Weather, Elections and Politics, and its edge vanishes in the markets that close soon (below).

**By tier.** Markets under 100 contracts a day: 12,821 fills, 20.4 cents a contract at the close, $3,469 a day; 100 to 1,000 a day: 49,585 fills, 16.0 cents, $12,514.

**Markets that close soon**, where the registered test will read a settlement (T6 and T7): 79% of the uncontested fills are in markets closing within 7 days of the fill (10% censored) and 71% within 48 hours (1% censored). On those the 5-cent rule nets $9,680 a day at the close mark (14.6 cents a contract) within 7 days and $7,971 (14.0 cents) within 48 hours, on 10-contract quotes. The one-sided rule's net falls to $410 a day (5.3 cents) within 7 days and **$36 (0.6 cents) within 48 hours**; its 10-cent variant is negative there.

## What a full-scale quoter would carry

The paper quoter quotes everything in scope at once: about **11,500 uncontested quotes standing** at any moment (14,400 one-sided), each side risking its size times its price. At 10 contracts and a mean paper price of 45 cents that is about **$115,000 of collateral** for the uncontested rule ($576,000 at 50 contracts), earning on paper $16,000 a day: 14% a day on collateral, a figure that says more about the mid as a fair value than about the opportunity. Inventory: 314,514 contracts filled in 79 hours, mostly held to a close under an hour away (Sports) or for days (the rest).

## The readings, as a ladder

1. **Do nothing:** $0.
2. **The paper quoter on the fit part, mid-marked:** $16,000 a day (lower bound $14,600) on 10-contract quotes 5 cents inside uncontested thin books; $830 for one-sided books.
3. **The same, in the markets that close within 48 hours** (where a settlement will be read): $8,000 a day on 10-contract quotes; one-sided $36.
4. **The registered test** (S4.2): at least seven new live days, each fill marked to settlement. What it can show that this cannot: whether the mid of a wide thin book is near where the market settles, which is the whole question.

## The rules proposed for registration (T6: at most four)

Every rule quotes 5 cents inside (the 10-cent variant is worse at every mark with the same fills) and, so that the test reads a settlement, only markets closing within 7 days of the quote:

1. **R1:** uncontested books, every category, 10 contracts a side.
2. **R2:** uncontested books, every category, 50 contracts a side.
3. **R3:** uncontested books, Sports alone, 10 contracts a side (the bulk of the fills, settled within the hour).
4. **R4:** one-sided books, every category, 10 contracts a side (weak on the fit part: $410 a day at 7 days, $36 at 48 hours; registered to be tested, not assumed).

T7's bar for each: settlement-marked dollars a day clearing $25 at the lower bound of the (event, day) interval, with the 1-hour mid mark positive as well.

## Limitations

- **The marks are mids.** The fair value is the mid of a book 20 cents or more wide, and the "close" mark is the last two-sided mid before the close, not the settlement; 29% of fills are censored and those read richer. Only S4.2's settlement marking answers this.
- **The taker's reaction is not modelled.** A tighter displayed spread may draw more flow, or less; a taker who crossed 30 cents may not have crossed 20. The paper quote is credited with the first trade beyond it as if nothing changed.
- **The paper quote is first in line** because it improves the touch; a competitor doing the same would halve the fills.
- **Four and a half days, three of them whole**, sports-heavy (71% of fills), with 2026-10-28 dropped; a Sunday to Tuesday with a Monday-night and two NFL-free weekdays.
- **Capital is not priced.** The collateral above is what quoting everything at once would tie up; a real quoter would choose markets, and the inventory risk in a market that never trades again (15% of fills) is marked at a mid that may not exist.
- Trades in series without fee terms (0.3%) are marked but not netted; the fee is negligible anyway, most thin series charging no maker fee.
