# The thin tail and the surprise rate (S3.3)

**Outcome against M4's bars: both clear, the first by a wide margin and the second barely and with reservations.** In thin markets (under 1,000 contracts a day) the trades that cross a wide spread, with no competing quote within 10 cents of the mid, carry **$494,893 a day of half-spread** against a bar of $250; a tenth captured, M4's floor, is $49,000 a day, four fifths of it in sports. The surprise rate clears as M4 words it: 12 series of 312 judged settle against a side that stood at 90 cents or more the day before more often than their prices imply, where about 8 would by chance alone; the Sports category clears by a quarter of a point (5.46% against 5.20%). Over the whole tail the favourite is slightly *under*-priced (3.54% of surprises against 3.86% implied), as K0.5 found for prices in general. The half-spread is the one direction of S3 that clears its bar; S3.c weighs the strand proposal. The surprise series are leads for a test on the held-out days, not a result.

## Data snapshot

- **Volume:** the history download's trade files on the analytics node, 2026-08-07 to 2026-10-22, 77 days, and its market list (open and close times); any day, since no outcome is read (M3).
- **Spreads and the half-spread:** the ingest's ticker frames and trades copied with `live extract`, 2026-10-24 21:00 to 2026-10-29 01:00 UTC, 100 hours, about 114 M frames and 23 M trades; 548,453 markets had a frame. An empty side of the book, which the feed shows as a 0-cent bid or a 100-cent ask with no size, is read as missing.
- **The surprise rate:** the settled binary markets closing on or before 2026-10-10 (M3), 17,538,187 of them, with the trades before them.
- **Fees:** each series' current terms from the history's series table (the maker fee where a series charges one).
- **Run:** `python -m sparq_analytics.survey tail --kalshi DIR --live DIR --out DIR` at commit `d8a2d28`, about a minute; every figure below is in `tail.json`.

## Method

- **Volume.** Each traded market's contracts and dollars (its yes price times its contracts, as in S3.1) a day over the days it was open inside the window, whole days and at least one, placed in S3.2's tiers: under 100, 100 to 1,000, 1,000 to 10,000, 10,000 and more contracts a day. Markets open in the window with no trade are counted apart (the silent tail). A short-lived market with few contracts is thin by this rule, whatever its rate while it lived.
- **Spreads.** Each market's time-weighted mean quoted spread while both sides stood, and the share of its time with both sides, by tier (the live span's own contracts a day, the same rule) and category: the median of the markets' means and the mean share.
- **The half-spread.** For every live trade (block trades left out), half the quoted spread just before it times its contracts: what a maker standing at the best quote earns against the mid on that fill. Summed by tier, category and the competition at that moment: a spread of 20 cents or less, so a quote within 10 cents of the mid on each side (**contested**); a wider spread (**uncontested**); or a side missing (**one-sided**, no half-spread to read). Gross and net of the maker fee where the series charges one. M4's bar is $250 a day in the thin tiers' uncontested trades.
- **The surprise rate.** Each cut market's day-before price is its last trade from 36 to 12 hours before its close. Where a side then stood at 90 cents or more, the market settled against it or not. The rate in total, by price bucket (90 to 95, 95 to 99, 99 and more), by category and by series with 50 or more such markets, each with a 95% cluster-bootstrap interval over events, against the price-implied rate (the mean of one minus the price); a cell is judged only with ten events or more; it clears when its lower bound exceeds the implied rate. The clearing series are listed with their title and the rules text of one of their markets, beside the number of judged series that would clear by chance alone (2.5% of them).

## Results

### Volume: where the markets and the money are

| Tier (contracts a day) | Markets | Trades a day | Contracts a day | Dollars a day |
|---|---|---|---|---|
| under 100 | 455,450 | 31,497 | 466,465 | $169,355 |
| 100 to 1,000 | 500,629 | 175,157 | 6,928,540 | $2,671,870 |
| 1,000 to 10,000 | 396,800 | 814,675 | 45,580,230 | $18,952,789 |
| 10,000 and more | 242,268 | 8,907,947 | 769,479,529 | $363,375,901 |

6,092,206 markets were open in the window; 1,595,147 traded; **4,497,059 never traded**, 3,881,913 of them Crypto (the untouched strikes of the 15-minute and hourly ladders), 238,755 Commodities, 178,191 Financials, 156,747 Sports. The two thin tiers hold 60% of the traded markets and 0.7% of the dollars: $2.84 M a day, of which Sports $2.04 M (554,573 markets; 138,842 trades a day), Crypto $160,000, Financials $136,000, Commodities $96,000, Entertainment $100,000, Weather $88,000. The busiest tier is Sports ($202 M a day) and Crypto ($146 M).

### Spreads: what the thin books look like

| Tier | Markets with frames | Median spread while two-sided | Share of time two-sided |
|---|---|---|---|
| under 100 | 438,518 | 10.8 cents | 21% |
| 100 to 1,000 | 48,813 | 9.3 cents | 32% |
| 1,000 to 10,000 | 39,655 | 5.0 cents | 28% |
| 10,000 and more | 21,454 | 1.9 cents | 24% |

A thin market is empty on one side four fifths of the time. The thin Crypto ladders (241,565 markets) are two-sided 0.3% of the time; thin Commodities (20,537) 4%; thin Sports (107,267) 45% with a median spread of 16 cents; thin Elections (24,109) 84% at 9 cents; thin Mentions (2,281) 99% at 10 cents; thin Politics (2,529) 87% at 6 cents.

### The half-spread: what a maker at the touch would have earned

Dollars a day over the 100 hours, scaled.

| Where | Competition at the trade | Markets | Trades a day | Contracts a day | Half-spread (gross) | Maker fees | Net |
|---|---|---|---|---|---|---|---|
| All tiers | contested (spread 20 cents or less) | 132,884 | 12,072,963 | 978,828,525 | $6,260,786 | $976,361 | $5,284,425 |
| All tiers | uncontested (wider) | 66,658 | 218,804 | 8,939,179 | $2,006,329 | $1,084 | $2,005,245 |
| All tiers | one-sided | 65,710 | 246,570 | 73,774,716 | (none to read) | | |
| **Thin tiers** | contested | 72,980 | 196,958 | 6,295,162 | $132,333 | $1,239 | $131,094 |
| **Thin tiers** | **uncontested** | 38,363 | 66,062 | 1,702,299 | **$494,893** | $32 | $494,860 |
| Thin tiers | one-sided | 26,969 | 21,319 | 975,617 | (none to read) | | |

**Against M4's bar: $494,893 a day of half-spread in thin markets' uncontested trades, against $250; a tenth captured is $49,489 a day.** The mean spread at those trades is 58 cents (29 cents of half-spread a contract). By category: Sports $397,886 (30,486 markets, 50,160 trades a day, 1.36 M contracts a day), Financials $17,672, Crypto $17,512, Entertainment $14,760, Commodities $12,702, Economics $9,323, Science and Technology $8,798, Mentions $6,971, Climate and Weather $6,046, Elections $2,584, Politics $431. By tier: under 100 contracts a day $45,478 (14,362 markets, 12,791 trades a day); 100 to 1,000 $449,415 (24,001 markets, 53,271 trades a day). Maker fees hardly touch it: 163 of 15,370 series charge one.

**What the figure is and is not.** It is the spread a maker standing at the best quote would have collected against the mid on the trades that happened, summed. It assumes the maker is at the quote when the taker comes and that the mid of a 58-cent-wide book is the fair price, neither of which is given: a taker who crosses a wide spread in a thin in-play sports market may know the score, and S3.2 showed that quotes which stand are usually right about where the price is going. Whether a tenth, more or less of the half-spread survives adverse selection is exactly what a pre-registered paper test of a quoter would measure. M4 set a tenth as the floor; at a tenth the thin tail's uncontested sports trades alone are worth $40,000 a day.

### The surprise rate: do 90-cent sides lose more often than they should?

Of 17,538,187 settled markets, 327,878 (1.9%) traded between 36 and 12 hours before their close (the rest are short-lived or silent), 111,333 of them with a side at 90 cents or more.

| Cell | Markets | Events | Surprises | Observed (95% interval) | Implied | Clears |
|---|---|---|---|---|---|---|
| All | 111,333 | 30,676 | 3,944 | 3.54% (3.37 to 3.72) | 3.86% | no |
| 90 to 95 cents | 32,541 | 18,675 | 2,611 | 8.02% (7.65 to 8.40) | 7.95% | no |
| 95 to 99 cents | 40,244 | 16,406 | 1,107 | 2.75% (2.52 to 2.99) | 3.32% | no |
| 99 cents and more | 38,548 | 10,084 | 226 | 0.59% (0.48 to 0.73) | 0.97% | no |
| Sports | 51,583 | 22,047 | 2,814 | 5.46% (5.22 to 5.70) | 5.20% | **yes** |
| Financials | 15,889 | 1,169 | 340 | 2.14% (1.56 to 2.92) | 3.28% | no |
| Climate and Weather | 14,444 | 3,080 | 170 | 1.18% (0.99 to 1.40) | 2.08% | no |
| Economics | 8,660 | 1,307 | 162 | 1.87% (1.50 to 2.37) | 2.39% | no |
| Crypto | 6,535 | 804 | 228 | 3.49% (2.76 to 4.49) | 3.44% | no |
| Entertainment | 5,002 | 826 | 66 | 1.32% (1.01 to 1.69) | 1.89% | no |
| Commodities | 3,989 | 294 | 98 | 2.46% (1.69 to 3.55) | 3.42% | no |
| Mentions | 2,353 | 298 | 25 | 1.06% (0.65 to 1.70) | 2.56% | no |

The tail as a whole is priced a little too *generously* to the underdog: a side at 95 to 99 cents loses 2.75% of the time where its price says 3.32%, and at 99 and more 0.59% where it says 0.97%, so a buyer of the favourite at the day-before price earns a third to a half of a cent a contract before fees, the same mild favourite-longshot bias K0.5 measured on all prices and found too small to trade. Sports is the exception and clears the bar: its favourites lose 5.46% of the time against 5.20% implied, a quarter of a cent against them, which the taker fee at 90 cents (0.63 cents) swallows.

**By series.** 362 series had 50 or more markets in the tail; 312 of them had the ten events a judgement needs; **12 clear**, where 7.8 would by chance alone at the interval's level, so the list as a whole is a few series beyond noise and each entry needs its own test:

| Series | Category | Markets | Events | Surprises | Observed (interval) | Implied |
|---|---|---|---|---|---|---|
| `KXITFDOUBLES`, ITF doubles match | Sports | 65 | 51 | 27 | 41.5% (28.0 to 56.2) | 6.9% |
| `KXNCAAMSOCCERGAME`, college soccer game | Sports | 64 | 59 | 25 | 39.1% (26.5 to 52.2) | 6.6% |
| `KXCS2MAP`, Counter-Strike 2 map winner | Sports | 69 | 57 | 23 | 33.3% (21.8 to 47.5) | 6.2% |
| `KXBNBD`, BNB daily threshold | Crypto | 63 | 24 | 16 | 25.4% (6.9 to 47.4) | 3.3% |
| `KXSHIBA`, Shiba range | Crypto | 153 | 44 | 33 | 21.6% (10.8 to 48.4) | 1.6% |
| `KXSHIBAD`, Shiba daily threshold | Crypto | 183 | 38 | 29 | 15.8% (6.6 to 29.8) | 1.6% |
| `KXNFLSPREAD`, pro football spread | Sports | 381 | 111 | 62 | 16.3% (11.3 to 23.3) | 7.0% |
| `KXCONCACAFNLTOTAL`, Concacaf Nations League total | Sports | 75 | 48 | 11 | 14.7% (7.9 to 24.0) | 6.8% |
| `KXPERLIGA1TOTAL`, Peruvian league total | Sports | 78 | 54 | 10 | 12.8% (6.4 to 22.1) | 6.2% |
| `KXAAAGASDCO`, Colorado gas price | Economics | 64 | 22 | 8 | 12.5% (3.4 to 38.1) | 2.4% |
| `KXNCAAWVMATCH`, women's college volleyball | Sports | 243 | 164 | 22 | 9.1% (5.8 to 13.7) | 4.1% |
| `KXNCAAFTOPAPRANK`, college football AP top rank | Sports | 129 | 18 | 8 | 6.2% (2.6 to 10.8) | 2.3% |

Their rules text is in `tail.json`. What they have in common is thinness: in a series where a market's day-before price is one trade by one taker, that price is not the market's opinion, and the "surprise" is as much the lone trade's as the outcome's. The three largest (doubles tennis, college soccer, esports maps, a third to two fifths of the favourites losing) also belong to sports with walkovers and forfeits, whose settlement rules the owner should read before anything is built on them. The crypto daily thresholds and ranges are a day's price move away from their day-before trade. None of this is tested: a lead here means a pre-registered test on the held-out days (2026-10-11 onward in the history, and the live days since), per M7.

## The readings, as a ladder

1. **The raw price as the probability** of a 90-cent side losing: 3.86% implied against 3.54% observed; the price is a little too kind to the underdog.
2. **A correction by price bucket:** the 95-to-99 and 99-and-more buckets overstate the surprise by 0.6 and 0.4 points; the 90-to-95 bucket is right to a tenth of a point. A buyer of favourites gains a third to a half of a cent a contract, before a fee of 0.3 to 0.6 cents.
3. **A naive maker in the thin tail:** at the best quote on every uncontested thin trade, $494,893 a day of half-spread before adverse selection; a tenth captured, $49,489.

## What this says for SPARQ

Three measurements, two bars cleared. The half-spread in thin, wide-spread markets is the first place S3 has found money that is not already taken: 66,000 trades a day cross spreads averaging 58 cents in markets with no competing quote near the mid, four fifths of them in sports, and nobody stands at the touch when they come. Whether a quoter can stand there without being the one who is wrong is the question a strand would test, on paper first. The surprise rate confirms K0.5: prices in the tail are nearly right, slightly kind to the underdog, and the series that stand out are thin enough that a single trade sets their "price".

## Limitations

- The live span is four days; the half-spread a day scales with it, the shape does not.
- The half-spread is read against the mid of a wide book, which is a weak fair value; the capture rate is the unknown the strand would measure.
- A one-sided book has no half-spread to read, and the thin tiers' trades are one-sided a quarter of the time (21,319 of 88,000 a day): the maker's opportunity there, quoting the empty side, is not counted.
- The day-before price is a single trade; a volume-weighted or quote-based price would be fairer to the series with one lone trade a market.
- The surprise test is one-sided at 2.5% a series over 312 series; the 12 clearing against 7.8 expected is a Poisson tail of about 9%.
- The fee terms are each series' current ones, as in S3.1.
