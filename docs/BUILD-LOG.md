# BUILD-LOG

How each SPARQ slice of an **open** milestone was built and verified: one section per milestone, one subsection per slice, with the headings **Built**, **Verified** (local, on the server, live), **Review** and **Problems and fixes**, and the slice's approved plan once it is done. A closed milestone's log moves verbatim to [history/](history/) with its plan. KAPS's build log (the predecessor's, up to its freeze on 2026-10-25) is at [github.com/swalsh76/KAPS/blob/main/docs/BUILD-LOG.md](https://github.com/swalsh76/KAPS/blob/main/docs/BUILD-LOG.md).

| Closed | Outcome | Plan and log |
|---|---|---|
| S0 The fork | done, PR #1 | [history/S0-S1.md](history/S0-S1.md) |
| S1 Evidence for the quoter | closed 2026-10-28: S1.1 NO-GO, S1.1b dropped unopened | [history/S0-S1.md](history/S0-S1.md) |
| S2 Weather against station observations | closed 2026-10-28: no rules (W9) | [history/S2.md](history/S2.md) |
| S3 Three measurements of where the money is | closed 2026-10-29: S3.3 led to S4 | [history/S3.md](history/S3.md) |
| S1.2r Analysis plumbing | done 2026-11-02, PR #30 | [history/S1.2r.md](history/S1.2r.md) |

## S4. A paper quoter in the thin tail (approved 2026-10-29)

### S4.1 The paper quoter on the fit part (2026-10-29 UTC, done; PR #24; the rules registered)

**The approved slice plan (moved from the PLAN's row, 2026-11-02).** Slice plan approved 2026-10-29, as recommended: `sparq_analytics.paper fit --kalshi DIR --live DIR --out DIR [--hours N]` on the analytics node; scope per frame (thin by S3.3's rule, every category, mid between 3 and 97 cents, the book uncontested or one-sided); the paper quote per T3, valid from its frame to the market's next frame; fills per T4, one a paper quote at most; marks to the mid at 10 minutes, 1 hour and the last two-sided mid before close (a market still open at the span's end marked at its last frame and reported apart as censored), less the maker fee; figures by category, tier, k and S with cluster-bootstrap lower bounds by event and day, the fill rate a quote-hour, the inventory and its hours to close, the share of filled markets that never traded again, one-sided fills apart; `paper.json`, `paper-fills.parquet`, the write-up; at most four rules proposed at the end and registered with a pinned digest once the owner approves them. T6 stands (2026-10-28's four-hour outage drops that day, leaving about three full days on the fit part), and the invisible paper quote never re-classifies later frames as contested. Reuses S3's readers, tiers, gap log, fee expressions and bootstrap; one fresh `live extract`; nothing deployed

**Built.** `python -m sparq_analytics.paper fit --kalshi DIR --live DIR --out DIR [--hours N]` (`paper/quoter.py`, `cli.py`), writing `paper-fills.parquet` and `paper.json`.
- *Scope and quotes* (T2, T3): for every frame of a thin market (S3.3's tiers over the span, every category) whose mid or standing price is between 3 and 97 cents and whose book is uncontested (spread over 20 cents) or one-sided, a paper bid and ask k cents inside the standing quotes (5 and 10), or 20 cents from the standing quote on an empty side, of S contracts (10 and 50), standing from the frame to the market's next frame or its close.
- *Fills* (T4): each trade matched to the paper quotes at its market's last frame strictly before it; a yes taker above the paper ask or a no taker below the paper bid fills it for the smaller of S and the trade's size; the first such trade a quote and side; block trades never.
- *Marks* (T5): the mid 10 minutes and 1 hour after the fill (null past the copied frames or the market's close), the market's last two-sided mid in the span (censored when the market is still open at the span's end), the maker fee from the series' terms (unknown without them: the fill is marked but not netted); whether the market traded again.
- *Days* (T6): a UTC day touched by an outage over ten minutes is dropped, and the book is not carried across it.
- *Figures:* by rule (state, k, S), by rule and category and by rule and tier: fills, markets, contracts, quote rows, quote-hours a day, fills a quote-hour, the hours to close, the censored and never-traded-again shares, the fee, and at each mark the marked fills, gross, net, netted contracts, cents a contract and (by rule and by rule and category, at 1 hour and the close) the (event, day) cluster-bootstrap interval of the net a day.
- The tail module's outage-overlap helper is public, since the quoter reuses it; the quoter also reuses S3's frame and trade readers, span, gap log, tiers, fee expressions and bootstrap.

**Verified (local).** 10 tests on a hand-built world (`paperhelpers.py`: five markets over two hours, one contested, one busy, one in the certainty regime, one one-sided, one closing mid-span; an outage; a three-day variant with a dropped day), every figure worked out by hand in its docstring: the dropped days, the scope, the quotes' prices and windows, the fills (the first trade beyond a quote; none after it died, after the close, or on a filled quote), the marks and their nulls, the summary by rule (fills, contracts, quote time, fees, gross and net at each mark, cents a contract, intervals), the reset of the book across a dropped day, the netting of fee-unknown fills and the censored flag, the command's files and exit codes. 7 planted bugs, all caught. The paper package at 100% branch coverage; ruff and mypy clean.

**Verified (analytics node, real data).** `live extract` grew the copies to 2026-10-29 04:00 (the fit part's end; the ingest's gap log holds 31 outages). `paper fit` runs in about a minute; a six-hour smoke run first, then the span, at `d74dce8`, `3d3079d` and `0a66bfb` (the figures; the earlier runs marked some fills against a mid seen before them, item 1 under Review). Results in `docs/research/paper-quoter.md`:

| | |
|---|---|
| Span | 2026-10-24 21:00 to 2026-10-29 04:00 UTC, 103 hours, 79 kept (2026-10-28 dropped: a four-hour outage) |
| Scope | 36 M frames carried a paper quote (two values of k each); 136,162 fills on 26,800 thin markets |
| Uncontested, 5 cents inside, 10 contracts | 62,405 fills, 11,523 quotes standing at once, 0.069 fills a quote-hour; net a day $13,534 at 10 minutes, $9,804 at 1 hour, **$12,297 at the close mark** (lower bound $10,972), 15.2 cents a contract; the 10-cent variant $8,246 (10.1 cents); the 50-contract variant $32,248 (16.3 cents) |
| One-sided, 5 cents, 10 contracts | 5,447 fills; $871 a day at the close mark (lower $597), 11.8 cents; $60 a day in markets closing within 48 hours |
| By category | every category positive at the close mark but Elections and Companies (tiny); Sports 71% of fills, $6,685 a day, 13.9 cents, closing within the hour; the long-dated categories 86 to 91% censored |
| Reservations | 29% of fills censored and those read twice as rich (19.7 against 9.0 cents a contract); in markets closing within 48 hours the headline rule reads $4,385 a day; a mid of a wide book as fair value; the taker's reaction unmodelled; about $115,000 of collateral standing for the 10-contract rule |

**Against the slice's gate:** the owner approves the rules. Proposed (write-up): R1 uncontested, every category, 10 contracts; R2 the same at 50; R3 uncontested, Sports alone, 10; R4 one-sided, every category, 10; all 5 cents inside and only markets closing within 7 days of the quote, so the test reads a settlement.

**Review (PLAN Q8).** `5928f55`, `e853e2e`, `d97052c` (docs). `d74dce8` reviewed at low effort: two findings, fixed in `d93113b`: a market without a listed close read as not censored; fills in series without fee terms left the net while their contracts stayed in the divisor of the cents a contract (the net and the cents are over the fills whose fee is known, with the netted contracts reported). `d93113b` carried a test that failed (its edit script had stopped at the test file while the shell ran on; fixed in `3d3079d`, with the summary test). `d93113b..3d3079d` at low effort: no findings. The merge review, at medium effort over the branch against `main`, found 10 issues, all fixed in `0a66bfb` (reviewed at low effort: no findings):
1. The close mark was each market's last two-sided mid over the whole span, so a fill could be marked against a mid seen before it. **Fixed:** the mark must follow the fill; 8,486 uncontested fills and 1,522 one-sided ones lost their close mark, and the headline rule's close mark went from $15,982 to $12,297 a day.
2. The one-fill-a-quote rule held within an hour only; a quote carried across the hour boundary could fill again. **Fixed:** the filled keys are carried and anti-joined (two fills fewer on the real frames).
3. Cells quoted but never filled vanished from the figures. **Fixed:** the cells are built from the quote time (Companies' one-sided books: 151 quote-hours a day, no fill).
4. The hour before a dropped day read the dropped day's first hour for its marks. **Fixed:** no mark from an hour that is not kept; test.
5. The maker-share and fee-terms expressions were spelled a third time. **Fixed:** `maker_share_expr` and `fee_terms_known` in `study.feecalc`, used by queue, tail and the quoter.
6. The close mark and the last trade re-read every file after the loop, with the empty-side rule re-implemented. **Fixed:** both accumulate hour by hour from the frames and trades already read.
7. The empty seed book parsed the first hour file for its schema. **Fixed:** `lag.empty_frames`.
8. The bootstrap re-filtered the whole fills frame once a cell, size and mark. **Fixed:** one partition a cell.
9. PLAN's S4 row said "S4.1 planned" against the S4.1 row's "done", and the resume point named two branches for the node's clone. **Fixed.**
10. The paper command line imported the survey command line's private `_need` and copied its error tuple. **Fixed:** `need` and `ERRORS` are public and shared.

**Problems and fixes.**
- A `&&` chain is one line, again: the python edit of the tests failed and the next line committed and pushed a failing test; one commit later it was right. Scripts that edit and then commit now run as one chained command or not at all.
- The test harness keyed fills by market, k and side, which collapses two fills of one market on different days; keyed by the frame too.
- The 6-hour smoke run took six seconds: the paper quoter is light (one join a frame, one a trade).
- The first runs marked a fill against the market's last two-sided mid in the span even when that mid was seen before the fill (a one-sided book's only mid, or a market that went one-sided for good); the merge review caught it, and the close mark now needs a frame at or after the fill. The headline figure fell by a quarter.

**Decisions (owner, 2026-10-29).** The four rules approved as proposed ("rules approved, merge PR24") and registered in `docs/research/paper-rules.json` (sha256 `6c7a87357267…`, pinned by `test_the_registered_rules_are_pinned`); the test part is the live days after the registration.

### S4.2 The registered test (2026-10-29 UTC, built; PR #25; the run is due 2026-11-13)

**Built.** `python -m sparq_analytics.paper test --kalshi DIR --live DIR --out DIR --rules docs/research/paper-rules.json --from DAY --to DAY [--min-days N]` (`paper/trial.py`, `cli.py`), writing `paper-test-fills.parquet` and `paper-test.json`.
- *The rules:* the file's sha256 is pinned in `trial.DIGEST` (and by a test); a changed file is refused. The quoter gained a `Scope` (the values of k, and markets closing within a time of the quote): the test replays the four registered rules exactly, 5 cents inside, markets closing within 7 days, the states, categories and sizes of each.
- *The window:* whole UTC days inside the live copies, at least seven kept (a day touched by an outage over ten minutes leaves, T6; only the window's own days can be dropped, the span's last part day included); `--min-days` lowers the floor for a smoke run, never for the test.
- *Settlement:* `live extract` copies the market table's `result` and `settlement_ts` as `settlements.parquet` (the discovery pass keeps them current, so no API call); each fill is marked to settlement (yes $1, no $0) and to the 1-hour mid; fills whose market has not settled are counted apart and take no part in the bar.
- *The verdict* per rule (T7): GO when the settlement-marked net a day clears $25 at the lower bound of the (event, day) interval and the 1-hour mark is positive.

**Verified (local).** 7 tests on the paper world (a seventh market, Economics, with an uncontested fill, for the category rule): the digest pin and the refusal of a changed file; the window inside the copies and its order; the scope's one k and 7-day close; the fills, the settlement marks and each rule's figures and verdict on a world where one market has settled; the command's files, exit codes and refused flags; the dropped days bounded to the window. 6 planted bugs, all caught (a seventh, the ignored categories, was missed until the world gained the Economics fill). The paper package at 100% branch coverage; the full suite 1,943 passed, 196 skipped.

**Verified (analytics node, real data).** `live extract` copied the settlements (1,073,433 settled markets) and grew the copies to 2026-10-29 06:00. **A smoke run, not the test:** `paper test --from 2026-10-25 --to 2026-10-27 --min-days 1` at `275c4ed` (three of the fit part's days, 72 hours, 43,853 fills, 38,783 of them settled) runs in a minute. Marked to settlement, **the uncontested rules lose**: R1 −$1,588 a day (−3.1 cents a contract, interval −$1,971 to −$1,173 over 6,056 clusters), R2 −$3,602, R3 (Sports) −$1,447 (−3.8 cents); R4 (one-sided) +$171 (lower −$58). Their 1-hour mid marks stay positive ($3,716, $9,583, $1,205, $371 a day). No rule is GO on these days. This is the fit part re-read with settlements, not the registered test, whose window (2026-10-30 to 2026-11-05) has not yet passed; but it says what the mid marks could not: the mid of a wide thin book is not where the market settles, and the paper quote is picked off at settlement by about the half-spread it books against the mid.

**Review (PLAN Q8).** `68805cf` (docs). `2f563ea` reviewed at low effort: no findings; the smoke run found that a dropped day outside the window was counted (fixed in `bc1d2fb`, with a test, and the world's Economics fill for the category plant that had been missed). `bc1d2fb` reviewed at low effort: two findings, fixed in `d19de95`: the day filter's end bound excluded the span's last part day, and a clause was dead. `d19de95` reviewed at low effort: no findings. The merge review, at medium effort over the branch against `main`, found 10 issues, all fixed in `275c4ed` (reviewed at low effort: no findings):
1. The window's last hour read no frames after it, so its fills had no 1-hour mark even when the next hour was copied. **Fixed:** the replay reads the hour after the window for the marks when the copies hold it and the day is kept; test.
2. The rules file's `regime_e6`, `empty_side_e6` and `outage_day_ms` were never checked against the code's constants, so a later constant change would silently un-register the test. **Fixed:** `check_constants` refuses the mismatch; test.
3. The BUILD-LOG's Review ended in a placeholder. **Fixed:** this list.
4. The resume point kept S4.1's node sentence beside S4.2's, two states for one clone. **Fixed.**
5. The settled fills and the gross were counted over all settled fills while the net, the cents and the interval ran over those with known fees. **Fixed:** the netted fills are reported beside the marked ones, from the quoter's own cell figures.
6. `judge` re-implemented the quoter's cell figures by hand. **Fixed:** `_cells` takes its marks and judges each rule's pre-filtered fills; one copy of the logic.
7. The paper command line duplicated the survey command line's flag check. **Fixed:** `survey.cli.check(args, applies, names)` is shared.
8. `window`'s fourth parameter was misnamed. **Fixed:** `min_days`, and the window returns the marks' end.
9. The settlement mark was Int32 beside Int64 marks. **Fixed:** Int64 literals.
10. The rules file was read twice. **Fixed:** once.

**Problems and fixes.**
- The dropped-day rule read the whole gap log, so 2026-10-28's outage dropped a day outside the test window and the count of kept days was wrong; the window's own days only, now.
- The smoke run's figures are the slice's real finding and were not expected before the test's window: the settlement marks turn the fit part's edge negative. The registered test runs as registered on 2026-11-13; the owner decides what else follows.

**Decisions (owner, pending).** Whether to run the registered test on 2026-11-13 as registered (recommended: yes, it is one command, and the rules file stays pinned), and what S4 does after it.

## S5. What a local language model can add (approved 2026-10-29)

### S5 proposal (PR #27, 2026-10-29 UTC, documents only; approved by the owner)

**Built (documents only).** PLAN §6.5 (N1 to N9, S5.1 to S5.c), the S5 row in §6, the corrected model node entry in §9, and the CLAUDE.md status and resume point.

**Verified.** The model node's endpoint was probed with a model listing and three small synthetic prompts (no Kalshi data): it serves one model, a 3-bit quantized Qwen-family reasoning model, at 50 to 95 tokens a second; the weather rules reading and the threshold direction were right, the rain-title check was doubtful. A reply's `content` is empty when the token budget is spent on the reasoning, which the client must leave room for. The key was read from the assistant's memory and never printed or written to the repository.

**Review (PLAN Q8).** Documents only, so no test was run for this branch; the branch was reviewed at medium effort and found 7 issues, all fixed:
1. The Review section ended in a placeholder. **Fixed:** this list.
2. N5 asserted the sample's markets were all from after the model's cutoff without a check. **Fixed:** the cutoff is stated as unknown, the sample as closing from 2026-08-07, and the gain is reported by closing week with an early-weeks gain read as possible leakage.
3. S5.2's placebo came from the same series, whose markets can share outcomes. **Fixed:** a market of a different event, within the same category and price band.
4. S5.1's bar could pass on the whole while the rain markets, where the probe's doubtful answer sat, failed. **Fixed:** the bar holds in the highs and the rain markets separately.
5. The resume point lost the smoke outputs' folder and the run scripts' names. **Fixed:** restored in the S4.2 bullet.
6. The status line left S4 and S5 unclear as to which was active. **Fixed:** S4 is waiting for its test window; S5 is proposed and awaits the owner.
7. N7's timing had no token budget. **Fixed:** 2,000 tokens a reply, about 800 used at 60 a second, about 13 seconds a market, roughly 22 hours for 6,000, to be measured on 100 markets first; the weather reading about ten hours.

**Problems and fixes.**
- PLAN §9 had described the machine from Strata's own description (a 125B mixture-of-experts model); the probe found a much smaller model, and the entry now says what was measured.
- The CLAUDE.md resume point had grown a single paragraph holding S3.c, S4.1, S4.2 and the pause; it is split into short bullets.

**Decisions (owner, 2026-10-29).** "Approve as proposed": S5 with both measurements, and N2's rule that no market text leaves the house stands for S5.2. PR #27 awaits the owner's word to merge, then S5.1's slice plan.

### S5.1 The client and the reading check (2026-10-29 UTC, done; PR #28)

**Built.** `python -m sparq_analytics.model ping|read` (`model/client.py`, `read.py`, `prompts.py`, `cli.py`); no new package (`httpx` was already a dependency).
- *The client:* the node's address, model name and key come from a mode-600 settings file outside the repository (`SPARQ_MODEL_CONFIG`, default `~/.config/sparq/model.toml`; a file readable by group or world is refused). The key rides only in the `Authorization` header, never in `repr`, an error, the cache or a log. One chat call with a 2,000-token budget: the model is a reasoning model whose thinking is spent from the same budget, so an empty `content` is a truncated reply. A refused connection or a busy answer (429, 502, 503, 504) is retried (3 attempts, 2 seconds apart); a slow or failed reply is not. Every reply is cached under the hash of the request (model, prompt, budget, temperature) with one staging file a writer, and a damaged cache file is a miss.
- *The reading check:* `read` takes every weather market of S2's window (2026-08-01 to 2026-10-10; S2's test part is refused) and 500 earlier ones, asks for the site, direction and numbers, and compares them with `weather.stations.code_in` and Kalshi's strike fields. At most twice the workers' requests are in flight, and 20 failures in a row abort the run. It writes `model-read.json` and `model-read.parquet`. A group is judged only with ten events or more.
- *Corrections to the plan, found while building (recorded in PLAN's S5.1 row):* the history holds 52,257 weather markets back to 2024, so the reading covers S2's window and not the whole history; and the bar gained S3.3's ten-event minimum, since two events at 100% had cleared it. *Additions inside the slice, named:* 500 earlier markets read apart with no bar; and, because no 2024 or 2025 text names a climate code (0 of 17,466), the model's answer on a market whose rules name no site is classified against its series' one code (S2.1's rule), not flagged as invented.

**Verified (local).** Format, lint and `mypy --strict` (239 files) are green. 33 tests of the model package (client 14, read 19) on a hand-built world of 13 markets with a fake model node, the answers worked out by hand in `modelhelpers.py`: the settings file's mode and its refusal; the key absent from `repr`, errors, the cache and logs; the retries on a refused connection and a busy answer and none on a slow one; the cache hit, a damaged cache file and a lost save race; the parser on a fenced reply, prose before it and nested braces; the site and bounds figures by group, the ten-event minimum, the unnamed-site classes, a strike type the reply cannot state, the speed counting only the calls made; the abort after 20 failures; the test part refused. 10 planted defects, all caught; the second review's fixes were checked by setting them aside, which fails the five tests that cover them. The full suite is 1,977 passed and 197 skipped.

**Verified (live).** `model ping` answered in about a second. A timing run on the analytics node read 100 window markets and 100 earlier ones at one request in flight and at four: four gained nothing, because the model node answers one at a time, so the run uses one; the window sample was read without a miss; the same prompt at temperature 0 gave different reply text in 71 of 200 calls (mostly formatting), which is why every reply is cached.

**Verified (analytics node, real data).** The model package's tests ran there at `d389dbf` at 100% line and branch coverage (33 passed). The full run, `model read` at `5efffee`, 2026-10-29 07:07 to 16:08 UTC: 11,825 markets (11,325 in the window and 500 earlier), 11,825 calls, 9.0 hours, 21.9 markets a minute, 48 tokens a second, a median call of 2.5 seconds and 110 tokens. **Every group clears the bar with no miss:** the highs' site 7,986 of 7,986 (1,331 events) and bounds 9,654 of 9,654 (1,609 events), the rain's site and bounds 1,671 of 1,671 each (71 events), and 11,825 of 11,825 replies parse (bar 99.5%). The earlier sample's bounds agree in 500 of 500. Where the rules name no site (2,166 markets) the model declined in 2,056, named the right code in 44, gave a place name right in substance in 61, garbled 5 and named a wrong code in none. Write-up `docs/research/model-reading.md`; outputs and the reply cache are in the data drive's `sparq/model/read` folder. The summary recomputed from the cache with the final code (`d389dbf`) matches the run on every verdict figure and adds the exact lower end of each group with no miss (`exact_events`, `exact_markets`): 99.7% (site) and 99.8% (bounds) for the highs by event, **94.9% for the rain's 71 events**, 99.8% for the rain by market. The registered bar's bootstrap has no width when nothing fails, so it clears by construction; the bar stands, and the rain's weaker event-level evidence is stated for the owner to weigh. An independent recomputation from the stored replies, without the module's code, agreed. **Nothing is deployed.**

**Review (PLAN Q8).** `5c64f30` reviewed at low effort: no findings. `332a470`, `20997c5` and `5efffee` reviewed at low effort (the range from `5c64f30`, then the later commits): no findings. The merge review, at medium effort over the branch against `main`, found 9 issues, 8 fixed in `f62599e` (reviewed at low effort: no findings):
1. Two workers asking the same request shared one cache staging file, so the second save failed and crashed the run; and a save that lost the replace race raised on Windows. **Fixed:** one staging file a writer, and a lost race is no error when the other worker's copy stands; test.
2. A damaged or truncated cache file raised an uncaught error, and every restart hit it again. **Fixed:** a damaged file is a miss and is rewritten; test.
3. A busy answer (429, 503) was never retried and counted toward the 20-failure abort. **Fixed:** 429, 502, 503 and 504 are retried like a refused connection; test.
4. The reply parser's non-greedy match stopped at the first closing brace, so a nested object or a brace in a string made a correct reply unparsed. **Fixed:** the parser scans for an object with the JSON decoder; test.
5. The direction was compared with exact case, and a strike type other than greater, less or between could never agree yet stayed in the bounds base. **Fixed:** the direction is compared without case or spaces, and such a market is counted as having no ground truth; tests.
6. The speed counted cached calls, so a replay reported an impossible rate. **Fixed:** only the calls made count; test.
7. `ping` left its client open when the node was down. **Fixed:** closed in `finally`; test.
8. The CLAUDE.md status paragraph had unbalanced bold markers and a stale last-updated line. **Fixed.**
9. `read` scans the markets table twice and writes no partial output when it aborts. **No change:** the scans cost seconds in a nine-hour job, and an aborted run's answers stay in the cache, so a rerun resumes at no cost.

`75bce63` (the write-up, the log and the plan rows) reviewed at low effort: no findings. A second merge review, at medium effort over the branch once the write-up was added, found 10 issues, 4 fixed in `d389dbf` (reviewed at low effort: no findings) and 6 left with reasons:
1. With no disagreement the bootstrap lower bound equals the rate, so the 98% bar clears whatever the sample, while the write-up's exact bound for the rain is below it. **Fixed, without re-cutting the bar after the reading:** `rate` reports the exact lower end beside the bootstrap's (`exact_events`, `exact_markets`) when nothing disagrees, so the figure is one command's output; test. The registered bar stands and clears, and the write-up, PLAN row and resume point state the rain's 94.9% over 71 events.
2. An `OSError` from a cache write aborts the run and no JSON is written for the rows read. **No change:** the command line reports it as an error with exit 1 (`OSError` is in the shared error list), stopping loudly on a full disk is right, and every reply read is already cached, so a rerun resumes at no cost.
3. A truncated reply is cached and replayed on every rerun. **No change:** a truncation follows from the budget, not from load, and the cache is the record of what the model said; a larger budget is a different request and a different key. The run had none.
4. Only a refused connection was retried, and the connect timeout was the 300 seconds of every other phase, so a node that is down took minutes a call to notice. **Fixed:** a connect timeout is retried like a refused connection and the connect timeout is 10 seconds; test.
5. The abort streak counts failures in completion order. **No change:** the run asks one at a time, where the streak is exactly consecutive failures; with more workers, 20 failures with no success between is the signal intended.
6. `reading_markets` scans the table twice. **No change:** the same as the first review's ninth.
7. `market_day` raises on an event ticker with no day, and the earlier sample spans all dates. **No change:** the whole history parsed in the real run (52,257 weather markets), and a surprise ticker should fail loudly before any call is spent.
8. The `model` setting was not checked as text. **Fixed:** the three settings must be text, and the error names the setting and never its value; test.
9. A list or object given as the site is stringified and counted as a site disagreement, not as unparsed. **No change:** it is a disagreement either way, the parse bar is unaffected, and none occurred.
10. A reply's seconds included the waits between attempts. **Fixed:** the seconds of the last attempt; test. The run's median call (2.5 seconds) is robust to a few retries, but its tokens-a-second figure could have been understated by them.

**Problems and fixes.**
- The model's `content` is null when the budget goes on thinking; the client treats it as truncated and the budget is 2,000.
- The first run loop could submit past its own stop check, which made a node-only test flaky; the requests in flight are now bounded (`332a470`).
- A lint failure was committed because `ruff check` was piped into `tail`, which hid its exit code; fixed in the next commit, and the gate now runs with `set -e` and no pipe.
- The first "invented site" figure called a right inference from an older text invented; the answer is now classified against the series' code (`20997c5`).
- A first edit script was not idempotent and stopped halfway after `ruff` reflowed text; later edits re-read the file and appended tests instead of patching reflowed ones.

**Decisions (owner, 2026-10-29).** S5.1 approved as recommended. **Pending:** whether to go on to S5.2 (the default, with the expectation stated in PLAN §6.5 that the probability measurement does not pass), and the word to merge PR #28.

## S6. Recording the order books of the thin tail (approved 2026-11-02)

### S6 proposal (PR #29, 2026-11-02 UTC, documents only; approved by the owner)

**Built.** PLAN §6.6: decisions B1 to B8 and slices S6.1 to S6.3 and S6.c, returning S1.2b's recorder (approved 2026-10-26, parked by D14) aimed at S4's thin tail; the milestone row; the §9 entry points to it. Nothing else changes.

**Verified.** The figures in the proposal were read on the analytics node on 2026-11-02, read only: the data drive has 1.8 TB free; of the 57,739 markets with ticker frames in the hour from 01:00 UTC, 43,902 had a mid above 3 and below 97 cents and under 1,000 contracts a day over the seven days before (1.8 million of the hour's 3.1 million frames).

**Review (PLAN Q8).** The proposal reviewed at low effort before its commit: no findings. The merge review, at medium effort over the branch against `main`, found 10 issues, 9 fixed in `7af325b` and 1 recorded elsewhere:
1. B2's band was written "from 3 to 97 cents", inclusive, where T2 leaves 3 cents or less and 97 or more alone. **Fixed:** above 3 and below 97 (the count already used that).
2. The 99-cent queue was named as a use, but B2's band excludes it. **Fixed:** named as outside B2, needing its own decision.
3. B2 called its seven-day window S3.3's rule, which measures a market's whole life. **Fixed:** named as the live adaptation of S3.3's threshold.
4. A newly listed market has no seven days, and nothing removed a market that turned thick. **Fixed:** a young market is judged over its days open so far; the daily choice drops one that no longer qualifies.
5. The 43,902 counted only markets with a frame in one hour. **Fixed:** stated as such; S6.1 counts the whole list.
6. B5's 99% had no minimum. **Fixed:** over at least 300 markets whose book changed, and reported per market.
7. S1.2b's check of the recorded trades against the ingest's was dropped. **Fixed:** restored in B5.
8. The build slice would have run the live hour that its own design depends on. **Fixed:** a live probe (S6.1) comes first; the recorder is S6.2, the daemon S6.3.
9. The full-drive refusal applied only at start. **Fixed:** an hourly check stops the recorder cleanly at 90%.
10. CLAUDE.md's resume point was not updated on this branch. **Recorded elsewhere:** the resume point is updated in the S5.2 branch's working copy, which carries the session's state, so the two open branches do not conflict on the same lines; the dates are UTC (2026-11-02), as the resume point's rule asks.

**Decisions.** The owner approved S6 on 2026-11-02 ("build all as recommended"): the thin tail only, built now, the 500 GB pause.

### S6.1 The live probe (2026-11-02 UTC, done; PR #31)

**The approved slice plan (moved from the PLAN's row, 2026-11-02).** Slice plan approved 2026-11-02 (owner, "3 approved").

**Built.** `python -m sparq_analytics.books probe` (`books/probe.py`, `books/cli.py`): B2's markets chosen from the live copies (open at the newest copied hour, not a combo, the latest quote in the day before, the mid or the one standing price, above 3 and below 97 cents, under 1,000 contracts a day over the week or its days open so far); `orderbook_delta` and `trade` subscribed in growing batches (1,000 to 50,000) with a refusal handled by smaller batches and a subscription that takes not even one more market treated as full; every message counted by type with its JSON bytes, the book messages carrying `clock`, each market's wait for its first snapshot, the messages in every second (silent ones counted), `seq` gaps, and one deliberate reconnect halfway; `probe.json` and the first minutes as `sample.parquet` on the node's data drive. It needs `--production` and the read-only key, loaded into the probe's process by its run script and never printed; subscriptions only. In core, `KalshiFeed.drop_link()` (a deliberate loss, reconnected and restored like any other) and **a fix found live: Kalshi answers a second subscribe to a channel on the same link with an `ok` that names the channel's existing subscription on the frame, so `subscribe` now takes that sid** (it refused the reply as carrying none, which would also have broken a resubscribe of two subscriptions to one channel after a reconnect). No new package.

**Verified (local).** Format, lint and `mypy --strict` are green; the full suite is 2,020 passed and 198 skipped; the `books` package at 99% branch coverage: B2's choice on built live copies (thin, thick, young, one-sided, combo, closed, unquoted, both certainty ends), the growing batches against a fake feed that refuses large ones with code 26, a subscription that fills, the link failing while subscribing, too many attempts, the counts, snapshots, reconnect, gap and resubscribe failure through a scripted feed, silent seconds and waits measured from the start, and the command line with its refusals; in core, the dropped link and the joined subscription against the local WebSocket server.

**Verified (live, the analytics node, read-only key).** All at `74f8464`, before the merge review's fixes, which change none of the figures below beyond the day's JSON (item 5). Two 2-minute smoke runs: the first stopped at the first batch on the `ok` reply above, which led to the fix; the second ran clean. **The hour** (`s61_probe.sh hour`, `74f8464`, 2026-11-02 05:17 to 06:17 UTC; outputs in the data drive's `sparq/books/hour`):
- **Markets:** 91,623 of the 163,245 open at 03:00 UTC (122,987 quoted in the day before, 94,422 in the band; no combo among them).
- **Subscriptions:** one per channel (sid 1 for the book, 2 for trades), six batches each up to 50,000 markets, none refused, in 1.4 seconds: no per-subscription limit at this size.
- **`clock`:** on all 18,274,383 book messages: live, unlike IKARS's reading of 2026-10-26.
- **Snapshots:** all 91,623 markets, the first within a median 1.5 seconds and at most 2.3.
- **Rate:** 18,091,070 deltas, 183,313 snapshots and 58,640 trades in the hour; 5,042 messages a second steady (5,091 on average, 7,930 at the 99th percentile, 55,903 in the second of the first snapshots); the feed's queue never held more than 2,238; no overflow, stall, bad frame or parse error.
- **The reconnect** at 30 minutes: back in 0.46 seconds, both subscriptions restored, 91,650 snapshots sent again, `seq` starting over at 1 (book) and 5 (trades), no gap.
- **Size:** 5.9 GB of JSON in the hour, so about 139 GB a day; the 3-minute sample (995,553 messages) stored as compressed raw messages at 11.6% of its JSON, so **about 16 GB a day stored**. B4's warning is therefore 32 GB a day, and at this rate the 500 GB pause comes after about a month.
- **Concentration** (the sample's deltas): only 19,151 markets changed in the 3 minutes; the top 10 markets carried 23%, the top 100 50%, the top 1,000 74%; Sports 70%, Financials 12%; 20 in-play tennis set markets (`KXATPSETWINNER`) 16%. The thin tail by trades is not quiet by quotes while a game is on.

**Review (PLAN Q8).** `fef8683` reviewed at low effort: 3 findings, 2 fixed in it before the commit and 1 left: (1) the per-second figures counted only seconds with a message, overstating a quiet feed's rate: **fixed**, seconds counted from the start with silent ones as zeros; test; (2) a subscribe slower than the reconnect point stretched the run and dropped the link late: **fixed**, every wait measured from the start; test; (3) an empty list of quote hours would fail without a clear message: **no change**, it cannot happen (`last_hour_end` needs both tables copied and `hour_files` refuses a gap). `74f8464` reviewed at low effort: 1 finding, fixed in it: the subscriptions count counted accepted batches, which no longer equal subscriptions once Kalshi joins them; renamed `accepted_batches`. The merge review, at medium effort over the branch against `main`, found 10 issues, 8 fixed in `ac4a79f` (reviewed at low effort: no findings) and 2 recorded:
1. One refused market was halved down to and then read as "the subscription is full", so a ticker that closed after the copies were made would have stopped the whole channel and answered B6 falsely. **Fixed:** a single refused market is skipped and listed; the subscription is full only after ten single refusals in a row; tests.
2. Kalshi's joining of subscribes left several remembered subscriptions under one sid, and `update_subscription` wrote the whole market set into each, so a restore would send it once per batch. **Fixed in core:** joined subscriptions stay batches (a removed market leaves its batch, an added one joins the last, an emptied batch goes); test through a reconnect.
3. The reply to a joined subscribe lists every market of the channel, about 3 MB at 91,623, near the feed's 4 MB frame limit; over it the link would close on every restore. **Fixed:** the probe's feed takes frames up to 64 MB; S6.2's recorder needs the same (its plan).
4. A consumer that failed (the feed lost for good, or an error while counting) was silenced, and the probe went on to write its results. **Fixed:** a feed that fails or ends early stops the probe with its error; tests.
5. A day's JSON counted the snapshots sent again after the deliberate reconnect as the day's snapshots. **Fixed:** one round only. The hour ran before the fix: its day figure is high by the second round, about 47 MB of 139 GB.
6. The bytes are the parsed frame written again as compact JSON, not the wire's, and the stored ratio comes from the first minutes. **Recorded:** the module and this entry name the sizes as estimates; S6.2 measures its own stored size.
7. The sample was held in memory as dictionaries. **Fixed:** capped at two million messages (the hour kept 995,553 in 3 minutes).
8. The first `seq` after a reconnect was kept by sid across links, so a second reconnect would mix them. **Fixed:** kept per link.
9. This entry still read "merge review to come". **Fixed:** this list.
10. CLAUDE.md's resume point on this branch still stops at S5. **Recorded elsewhere:** the resume point lives in the S5.2 branch's working copy, which carries the session's state, so the open branches do not conflict on the same lines.

**Problems and fixes.**
- Kalshi's `ok` reply to a second subscribe, above: the first live smoke stopped on it; fixed in core with a test.
- The kept sample was cut from 10 minutes to 3 for the hour, since at 5,000 messages a second ten minutes would hold about 3 million messages in memory.
- Three of my test expectations were wrong, not the code: the band count of the built copies, the refusal text (it carries its code), and the delta count when batches fall to one market each.

**For S6.2's plan.** The recorder can stay one connection with one subscription per channel; it must take an `ok` as a join and rebuild every book from the snapshots that follow a reconnect. The size is the open design question: parsed columns instead of raw messages, and, since a few hundred busy books carry most of the traffic, whether B2 should leave out or sample the busiest in-play markets; that is a decision for the owner in S6.2's plan.

## The fresh-eyes review (2026-11-02 UTC)

**How.** Owner's request ("perform a complete fresh-eyes subagent review of the project so far"), run under PLAN Q9 as two sub-agents with no prior context, both read only: one over the code (the whole codebase at `s6/2-recorder`, and S5.2's model code), one over the plan, the research and the direction (CLAUDE.md, PLAN, ASSESSMENT, LESSONS, this log and every write-up). The code reviewer ran the suite (2,082 passed, 199 skipped), mypy and ruff, all green.

**What the code review found.** No non-negotiable broken: the REST guard allows GET only and public routes, the WebSocket client sends only subscribe, update and unsubscribe and refuses private channels, demo is the default, money is fixed point in every decision path, and no secret or real host name is tracked. Eighteen findings, mostly where the S6.2 recorder meets real failures (a crash, a power cut, a lagging queue) and at the B5 check's edges; they are fixed in S6.2 (its entry), except: the live copies are never copied again after the ingest backfills trades, and `live extract` has no lock (fixed with S6.2, since S4.2's registered test reads the copies); the paper quoter's same-millisecond trades and its fills during short outages follow the registered rules, so they are reported in S4.2's write-up, not changed; S5.2's thin filter counts trades after the decision time, one line in its write-up.

**What the direction review found.** Each study is honest and careful (digests, unopened test parts, every correction recorded), but the portfolio drifts: every strand since S2 was chosen from data already held and screened with gross-volume bars that pass easily, while the two ideas in the goal that could beat informed takers are untested: a fair value from a liquid related market used by a maker to pull or skew its quotes, and an outside anchor for sports, four fifths of the thin tail's money. S4's smoke run (−3.1 cents a contract at settlement) reads as thin-tail takers being informed relative to the book.

**Housekeeping done (owner, 2026-11-02: "perform housekeeping").**
- PLAN: the status line brought to S6; §1 gains where the work stands against the goal; Q3's first gate noted as S1's, closed; §5's heading and §7's first risk no longer wait on S1; M3's test part caveated (K0.5's, S1.1's and S4.2's smoke readings); T5 records the smoke run's reading of settlements as a deviation; T6 and this log say where the live copies really begin (2026-10-24 21:00); S4.2's row names its PR and **writes down what a NO-GO means before the run**; S3.1 cleared its bar as worded in Mentions and was not pursued (it was reported as below it); S3.2 measured taking, not a maker's use of a related market; S3.3's half-spread is gross revenue the wide makers already earn, not money left unclaimed.
- ASSESSMENT, LESSONS, `thin-tail.md`, `book-lag.md` and `certainty-dump.md` carry the same corrections; the surprise rate is said one way everywhere (favourites at 90 cents or more lost slightly less often than priced, 3.57% against 3.82%).
- This log: S5's heading says approved; S2.2's 2,240 calls against S2.1's 2,243 are footnoted as not reconciled.
- CLAUDE.md: the resume point brought to the present and carried to `main`, with S1.2r's line for the node kept.
- S5.2 (on its branch): the model's knowledge cutoff is to be probed with public events before its gain is read.

**For the owner (the direction review's decisions; nothing is changed until they are taken).**
1. Whether to measure an outside fair value for sports (sportsbook lines or Polymarket, after reading their terms) and a related market's use by a maker, beginning with a post-mortem on the three days already opened (did S4's losing fills follow moves in the same game's liquid markets; how do the incumbent wide makers fare at settlement).
2. S6's scope once S6.2 is done: hold S6.3 until the direction is chosen, or go on; whether to add a sample of middle-tier markets.
3. Whether to reverse D13 and run S1.1b's still-unopened forward test of R4 (one download and one command).
4. Kalshi's Developer Agreement: re-read its storage and public-statement clauses against months of recorded books and the public write-ups; whether `docs/research` should be private.
5. A stopping rule written in advance: what result, by what date, ends the search for an edge for a one-person seconds-scale maker.
