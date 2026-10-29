# The thin tail and the surprise rate (S3.3)

**Outcome against M4's bars: both clear, the first by a wide margin and the second barely and with reservations.** In thin markets (under 1,000 contracts a day) the trades that cross a wide spread, with no competing quote within 10 cents of the mid, carry **$499,284 a day of half-spread** against a bar of $250; a tenth captured, M4's floor, is $49,928 a day, four fifths of it in sports. The surprise rate clears as M4 words it: 14 series of 313 judged settle against a side that stood at 90 cents or more the day before more often than their prices imply, where about 8 would by chance alone; the Sports category clears by a quarter of a point (5.49% against 5.21%). Over the whole tail the favourite is slightly *under*-priced (3.57% of surprises against 3.82% implied), as K0.5 found for prices in general. The half-spread is the one direction of S3 that clears its bar; S3.c weighs the strand proposal. The surprise series are leads for a test on the held-out days, not a result.

## Data snapshot

- **Volume:** the history download's trade files on the analytics node, 2026-08-07 to 2026-10-22, 77 contiguous days, and its market list (open and close times); any day, since no outcome is read (M3).
- **Spreads and the half-spread:** the ingest's ticker frames and trades copied with `live extract`, 2026-10-24 21:00 to 2026-10-29 02:00 UTC, 101 hours, about 119 M frames and 24 M trades; 555,244 markets had a frame; the gap log holds 31 outages of the ticker feed. An empty side of the book, which the feed shows as a 0-cent bid or a 100-cent ask with no size, is read as missing.
- **The surprise rate:** the settled binary markets closing on or before 2026-10-10 (M3), 17,538,187 of them, with the trades before them.
- **Fees:** each series' current terms from the history's series table (the maker fee where a series charges one).
- **Run:** `python -m sparq_analytics.survey tail --kalshi DIR --live DIR --out DIR` at commit `6a6d9c5`, about a minute; every figure below is in `tail.json`.

## Method

- **Volume.** Each traded market's contracts and dollars (its yes price times its contracts, as in S3.1) a day over the days it was open inside the window, whole days and at least one, placed in S3.2's tiers: under 100, 100 to 1,000, 1,000 to 10,000, 10,000 and more contracts a day. Markets open in the window with no trade are counted apart (the silent tail); traded markets the market list lacks have the tier `unknown`. A short-lived market with few contracts is thin by this rule, whatever its rate while it lived.
- **Spreads.** Each market's time-weighted mean quoted spread while both sides stood, and the share of its time with both sides, by tier (the live span's own contracts a day, the same rule) and category: the median of the markets' means and the mean share. A market's time ends at its close; the logged outages count for no one.
- **The half-spread.** For every live trade (block trades left out), half the quoted spread just before it times its contracts: what a maker standing at the best quote earns against the mid on that fill. Summed by tier, category and the competition at that moment: a spread of 20 cents or less, so a quote within 10 cents of the mid on each side (**contested**); a wider spread (**uncontested**); a side missing (**one-sided**, no half-spread to read); or the last frame older than an outage (**unknown**). Gross and net of the maker fee where the series charges one. M4's bar is $250 a day in the thin tiers' uncontested trades.
- **The surprise rate.** Each cut market's day-before price is its last trade from 36 to 12 hours before its close (among trades in one millisecond, the highest price). Where a side then stood at 90 cents or more, the market settled against it or not. The rate in total, by price bucket (90 to 95, 95 to 99, 99 and more), by category and by series with 50 or more such markets, each with a 95% cluster-bootstrap interval over events, against the price-implied rate (the mean of one minus the price); a cell is judged only with ten events or more; it clears when its lower bound exceeds the implied rate. The clearing series are listed with their title and the rules text of one of their markets, beside the number of judged series that would clear by chance alone (2.5% of them).

## Results

### Volume: where the markets and the money are

| Tier (contracts a day) | Markets | Trades a day | Contracts a day | Dollars a day |
|---|---|---|---|---|
| under 100 | 455,450 | 31,497 | 466,465 | $169,355 |
| 100 to 1,000 | 500,629 | 175,157 | 6,928,540 | $2,671,870 |
| 1,000 to 10,000 | 396,800 | 814,675 | 45,580,230 | $18,952,789 |
| 10,000 and more | 242,268 | 8,907,947 | 769,479,529 | $363,375,901 |
| unknown (not in the market list) | 50,840 | 116,765 | 14,792,349 | $2,956,540 |

6,092,206 listed markets were open in the window; 1,645,987 markets traded, 50,840 of them absent from the downloaded market list (3% of the traded, $2.96 M a day that cannot be tiered); **4,497,059 never traded**, 3,881,913 of them Crypto (the untouched strikes of the 15-minute and hourly ladders), 238,755 Commodities, 178,191 Financials, 156,747 Sports. The two thin tiers hold 58% of the traded markets and 0.7% of the dollars: $2.84 M a day, of which Sports $2.04 M (554,573 markets; 138,842 trades a day), Crypto $160,000, Financials $136,000, Entertainment $100,000, Commodities $96,000, Weather $88,000, Mentions $71,000. The busiest tier is Sports ($202 M a day) and Crypto ($146 M).

### Spreads: what the thin books look like

| Tier | Markets with frames | Median spread while two-sided | Share of time two-sided |
|---|---|---|---|
| under 100 | 443,197 | 11.2 cents | 25% |
| 100 to 1,000 | 49,584 | 9.6 cents | 77% |
| 1,000 to 10,000 | 40,619 | 5.1 cents | 85% |
| 10,000 and more | 21,831 | 1.9 cents | 94% |

The thinnest markets are empty on one side three quarters of the time; a market that trades a hundred contracts a day is two-sided three quarters of the time. The thin Crypto ladders (244,128 markets under 100 a day) are two-sided 1.3% of the time; thin Commodities (20,828) 21% at 20 cents; thin Sports (109,145) 54% at 17 cents; thin Financials (20,606) 44% at 14 cents; thin Elections (24,100) 84% at 9 cents; thin Mentions (2,254) 99% at 10 cents; thin Politics (2,522) 87% at 6 cents. In the 100-to-1,000 tier Sports (34,742 markets) is two-sided 81% of the time at 13 cents and Elections (1,219) 91% at 3 cents.

### The half-spread: what a maker at the touch would have earned

Dollars a day over the 101 hours, scaled.

| Where | Competition at the trade | Markets | Trades a day | Contracts a day | Half-spread (gross) | Maker fees | Net |
|---|---|---|---|---|---|---|---|
| All tiers | contested (spread 20 cents or less) | 133,057 | 12,099,693 | 980,657,245 | $6,285,806 | $979,459 | $5,306,347 |
| All tiers | uncontested (wider) | 68,182 | 223,985 | 9,181,026 | $2,055,434 | $1,092 | $2,054,341 |
| All tiers | one-sided | 62,035 | 228,509 | 73,125,889 | (none to read) | | |
| All tiers | unknown (last frame before an outage) | 27,463 | 91,382 | 8,088,434 | (none to read) | | |
| **Thin tiers** | contested | 71,866 | 191,494 | 6,068,847 | $130,337 | $1,158 | $129,179 |
| **Thin tiers** | **uncontested** | 38,916 | 66,782 | 1,724,649 | **$499,284** | $30 | $499,254 |
| Thin tiers | one-sided | 23,134 | 18,704 | 854,449 | (none to read) | | |
| Thin tiers | unknown | 14,983 | 9,733 | 382,932 | (none to read) | | |

**Against M4's bar: $499,284 a day of half-spread in thin markets' uncontested trades, against $250; a tenth captured is $49,928 a day.** The mean half-spread at those trades is 29 cents a contract (a spread of 58 cents). By category: Sports $403,571 (31,100 markets, 51,253 trades a day, 1.38 M contracts a day), Crypto $17,470, Financials $17,416, Entertainment $14,285, Commodities $12,552, Science and Technology $9,314, Economics $9,168, Mentions $6,671, Climate and Weather $5,881, Elections $2,320, Politics $431. By tier: under 100 contracts a day $44,665 (14,285 markets, 12,837 trades a day); 100 to 1,000 $454,619 (24,631 markets, 53,945 trades a day). Maker fees hardly touch it: 163 of 15,370 series charge one.

**What the figure is and is not.** It is the spread a maker standing at the best quote would have collected against the mid on the trades that happened, summed. It assumes the maker is at the quote when the taker comes and that the mid of a 58-cent-wide book is the fair price, neither of which is given: a taker who crosses a wide spread in a thin in-play sports market may know the score, and S3.2 showed that quotes which stand are usually right about where the price is going. Whether a tenth, more or less of the half-spread survives adverse selection is exactly what a pre-registered paper test of a quoter would measure. M4 set a tenth as the floor; at a tenth the thin tail's uncontested sports trades alone are worth $40,000 a day.

### The surprise rate: do 90-cent sides lose more often than they should?

Of 17,538,187 settled markets, 327,878 (1.9%) traded between 36 and 12 hours before their close (the rest are short-lived or silent), 111,148 of them with a side at 90 cents or more.

| Cell | Markets | Events | Surprises | Observed (95% interval) | Implied | Clears |
|---|---|---|---|---|---|---|
| All | 111,148 | 30,659 | 3,964 | 3.57% (3.40 to 3.75) | 3.82% | no |
| 90 to 95 cents | 32,154 | 18,673 | 2,604 | 8.10% (7.75 to 8.50) | 7.95% | no |
| 95 to 99 cents | 38,913 | 16,443 | 1,131 | 2.91% (2.69 to 3.16) | 3.33% | no |
| 99 cents and more | 40,081 | 10,050 | 229 | 0.57% (0.46 to 0.71) | 0.97% | no |
| Sports | 51,419 | 22,029 | 2,821 | 5.49% (5.25 to 5.72) | 5.21% | **yes** |
| Financials | 15,986 | 1,171 | 348 | 2.18% (1.62 to 2.91) | 2.89% | no |
| Climate and Weather | 14,370 | 3,079 | 172 | 1.20% (1.02 to 1.42) | 2.13% | no |
| Economics | 8,664 | 1,307 | 161 | 1.86% (1.49 to 2.38) | 2.38% | no |
| Crypto | 6,527 | 804 | 229 | 3.51% (2.78 to 4.51) | 3.44% | no |
| Entertainment | 4,985 | 826 | 68 | 1.36% (1.06 to 1.75) | 1.90% | no |
| Commodities | 3,984 | 294 | 97 | 2.43% (1.69 to 3.52) | 3.42% | no |
| Mentions | 2,346 | 297 | 26 | 1.11% (0.69 to 1.74) | 2.57% | no |

The tail as a whole is priced a little too *generously* to the underdog: a side at 95 to 99 cents loses 2.9% of the time where its price says 3.3%, and at 99 and more 0.6% where it says 1.0%, so a buyer of the favourite at the day-before price earns about four tenths of a cent a contract before fees, the same mild favourite-longshot bias K0.5 measured on all prices and found too small to trade. Sports is the exception and clears the bar: its favourites lose 5.49% of the time against 5.21% implied, a quarter of a cent against them, which the taker fee at 90 cents (0.63 cents) swallows.

**By series.** 362 series had 50 or more markets in the tail; 313 of them had the ten events a judgement needs; **14 clear**, where 7.8 would by chance alone at the interval's level (a Poisson tail of about 3%), so the list as a whole is a few series beyond noise and each entry needs its own test:

| Series | Category | Markets | Events | Surprises | Observed (interval) | Implied |
|---|---|---|---|---|---|---|
| `KXITFDOUBLES`, ITF doubles match | Sports | 65 | 51 | 27 | 41.5% (28.3 to 55.9) | 7.0% |
| `KXNCAAMSOCCERGAME`, college soccer game | Sports | 68 | 63 | 28 | 41.2% (28.5 to 54.9) | 6.9% |
| `KXCS2MAP`, Counter-Strike 2 map winner | Sports | 71 | 57 | 23 | 32.4% (20.8 to 46.3) | 6.0% |
| `KXBNBD`, BNB daily threshold | Crypto | 62 | 24 | 16 | 25.8% (6.9 to 47.9) | 3.2% |
| `KXSHIBA`, Shiba range | Crypto | 153 | 44 | 33 | 21.6% (10.8 to 48.4) | 1.6% |
| `KXSHIBAD`, Shiba daily threshold | Crypto | 183 | 38 | 29 | 15.8% (6.6 to 29.8) | 1.6% |
| `KXNFLSPREAD`, pro football spread | Sports | 380 | 111 | 62 | 16.3% (11.4 to 23.3) | 7.1% |
| `KXCONCACAFNLTOTAL`, Concacaf Nations League total | Sports | 75 | 48 | 11 | 14.7% (7.9 to 24.0) | 6.8% |
| `KXPERLIGA1TOTAL`, Peruvian league total | Sports | 78 | 54 | 10 | 12.8% (6.4 to 22.1) | 6.2% |
| `KXAAAGASDCO`, Colorado gas price | Economics | 64 | 22 | 8 | 12.5% (3.4 to 38.1) | 2.4% |
| `KXWTAGTOTAL`, WTA total games | Sports | 90 | 77 | 10 | 11.1% (5.6 to 19.7) | 5.3% |
| `KXNCAAWVMATCH`, women's college volleyball | Sports | 245 | 167 | 22 | 9.0% (5.7 to 13.7) | 4.1% |
| `KXNCAAFTOPAPRANK`, college football AP top rank | Sports | 129 | 18 | 8 | 6.2% (2.6 to 10.8) | 2.2% |
| `KXAAAGASDFL`, Florida gas price | Economics | 355 | 46 | 21 | 5.9% (2.9 to 12.0) | 2.9% |

Their rules text is in `tail.json`. What they have in common is thinness: in a series where a market's day-before price is one trade by one taker, that price is not the market's opinion, and the "surprise" is as much the lone trade's as the outcome's. The three largest (doubles tennis, college soccer, esports maps, a third to two fifths of the favourites losing) also belong to sports with walkovers and forfeits, whose settlement rules the owner should read before anything is built on them. The crypto daily thresholds and ranges are a day's price move away from their day-before trade. None of this is tested: a lead here means a pre-registered test on the held-out days (2026-10-11 onward in the history, and the live days since), per M7.

## The readings, as a ladder

1. **The raw price as the probability** of a 90-cent side losing: 3.82% implied against 3.57% observed; the price is a little too kind to the underdog.
2. **A correction by price bucket:** the 95-to-99 and 99-and-more buckets overstate the surprise by 0.4 points each; the 90-to-95 bucket is right to a sixth of a point. A buyer of favourites gains about four tenths of a cent a contract, before a fee of 0.3 to 0.6 cents.
3. **A naive maker in the thin tail:** at the best quote on every uncontested thin trade, $499,284 a day of half-spread before adverse selection; a tenth captured, $49,928.

## What this says for SPARQ

Three measurements, two bars cleared. The half-spread in thin, wide-spread markets is the first place S3 has found money that is not already taken: 67,000 trades a day cross spreads averaging 58 cents in markets with no competing quote near the mid, four fifths of them in sports, and nobody stands at the touch when they come. Whether a quoter can stand there without being the one who is wrong is the question a strand would test, on paper first. The surprise rate confirms K0.5: prices in the tail are nearly right, slightly kind to the underdog, and the series that stand out are thin enough that a single trade sets their "price".

## Limitations

- The live span is four days; the half-spread a day scales with it, the shape does not.
- The half-spread is read against the mid of a wide book, which is a weak fair value; the capture rate is the unknown the strand would measure.
- A one-sided book has no half-spread to read, and the thin tiers' trades are one-sided a fifth of the time (18,704 of 87,000 a day): the maker's opportunity there, quoting the empty side, is not counted; nor are the 9,733 trades a day whose book was last seen before an outage.
- 50,840 traded markets (3%) are absent from the downloaded market list and cannot be tiered; their $2.96 M a day is reported apart.
- The day-before price is a single trade; a volume-weighted or quote-based price would be fairer to the series with one lone trade a market.
- The surprise test is one-sided at 2.5% a series over 313 series; the 14 clearing against 7.8 expected is a Poisson tail of about 3%.
- The fee terms are each series' current ones, as in S3.1.
