# S1.1b: R4 refined, and the forward test pre-registered

**Status: registered on 2026-10-25, before any forward trade exists; the verdict comes after the window closes.** The rule, the settings and the frozen activity file are locked by digest `0af9ea44be7fa121f36db9d5d52629e2768da910532eea390a06c9a9b4859b01`; the activity file's hash is `edf3189cf82aa9f340aaf3b9798c7a0465c17a643e64b5b9ab36e1b9b6e7abf8`. This commit's timestamp is the public record that the rule was fixed first.

## 1. The refinement (fit and validate parts only; PLAN D10)

R4 from S1.1 (a maker in series of 100 to 100,000 contracts a market, on trades 1 second to 1 hour after the market's previous trade) was varied along a grid declared before the scan (`r4-grid.json`): four quiet windows x three activity ranges x three price bands, 36 variants, each scored on the fit part (events closing 7 August to 28 September) and the validate part (29 September to 9 October) at a one-sided 0.025. S1.1's test part was not read.

The choosing rule, fixed in advance: eligible only if the fit edge is positive and the validate edge at least half of it; the best is the eligible variant with the highest fit lower bound; among eligible variants within half a cent of it, the one with the fewest changes from R4 wins.

**Chosen:** a maker on trades **60 seconds to 1 hour** after the market's previous trade, in series of **1,000 to 100,000** contracts a market, at yes prices of **20 to 80 cents** (`forward-rule.json`). Fit +0.30 cents a contract (lower bound -2.48, 4,420 events); validate +11.99 (-4.47, 1,004 events). One other variant was eligible (10 seconds to 1 hour, same range and band: fit +0.04, validate +10.70).

**The finding that matters most:** on the fit part, the largest and oldest, **R4 itself is -0.86 cents** (lower -2.23, 11,393 events) and only two of the 36 variants are positive at all, both by less than a third of a cent. The positive figures come from the later periods: validate (+3.91 for R4) and S1.1's test part (+2.51). So the human-paced maker's edge either began around late September (the American football seasons start then, and the sports mix changes) or is chance. The forward window (events opening 26 October to 22 November) is in the same season, so the forward test asks the right question: does it hold now?

## 2. The forward test (registered, not yet run)

- **Events:** those that open on or after 26 October and close before 23 November 2026, none of whose trades existed when the rule was fixed.
- **Data:** KAPS's downloader, re-run for those days after the window closes and its markets settle (trades, then the markets step), GET only.
- **Settings:** the same sample shares as S1.1 (base 0.0366; Financials 0.4627, Crypto 0.0441, Commodities 0.1321, Climate and Weather 0.3385, the rest 1.0); series activity from the frozen file of the original fit part.
- **Gate:** one rule at a one-sided tail of 0.025, at least 300 events, the lower bound after fees under the strict fill rule above zero. **GO** resumes S1 at S1.2 (the shadow quoter); **NO-GO** stops the maker direction.
- **Opened once,** through its own ledger, by `forward-verdict --open-test`.

The full scan table is in the study folder (`rules-scan.json`) and is reproduced in the final write-up with the verdict.
