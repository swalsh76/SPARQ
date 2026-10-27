# BUILD-LOG

How each SPARQ slice was built and verified. One section per milestone, one subsection per slice, with the headings **Built**, **Verified** (local, on the server, live) and **Problems and fixes**, and any decisions taken. KAPS's build log (the predecessor's, up to its freeze on 2026-10-25) is at [github.com/swalsh76/KAPS/blob/main/docs/BUILD-LOG.md](https://github.com/swalsh76/KAPS/blob/main/docs/BUILD-LOG.md).

## S0. The fork (2026-10-25)

### S0 The repository, the renamed packages and the founding documents (done, merged 2026-10-25 as PR #1)

**Why.** KAPS's K0.5 edge study closed with a NO-GO, and a project review the same day found the money on Kalshi going from takers to makers, with an exploratory lead for a maker that works at a seconds scale in second-tier markets (ASSESSMENT §7). The owner chose to pursue it as a new project: SPARQ, public under MIT, started fresh from KAPS's code without its git history, with KAPS frozen and its ingest kept running (PLAN Q1).

**Built.**
- The repository `github.com/swalsh76/SPARQ` (public). Its first commit on `main` holds only the licence (MIT, copied from KAPS), the README, `.gitattributes` (LF line endings) and `.editorconfig`, committed with the owner's GitHub no-reply address.
- On branch `s0/fork`: KAPS `main` at `6cd0083` (its merge of PR #42), tracked files only, through `git archive`. Left in KAPS by the owner's choice: the web-design mock-ups (`docs/design/`) and the K0.5 write-up (`docs/research/`, linked from ASSESSMENT §7). The server package (ingest, installer, watchdog) is carried, idle until SPARQ deploys its own services.
- The rename: the folders `packages/kaps-*` to `packages/sparq-*` and `src/kaps_*` to `src/sparq_*` (six in all, and no file name carried the name), and `kaps`, `Kaps` and `KAPS` to `sparq`, `Sparq` and `SPARQ` in 202 text files (928 occurrences): the packages `sparq-core`, `sparq-server` and `sparq-analytics`, the admin tool `sparq-admin`, the `SPARQ_*` environment variables, and the names a future SPARQ install would create (`sparq` account, `/opt/sparq`, the `sparq_ingest` role, `sparq_test` for tests). Before renaming, the KAPS tree was checked for "kaps" inside other words and for real host or account names: none.
- The lock re-resolved under the new names: 47 packages, **no third-party version changed** (the pinned lines compared before and after).
- The founding documents: CLAUDE.md (the owner's working rules carried, plus SPARQ's quoting-safety rules: an expiry on every quote, post-only maker quotes, a kill switch, genuine orders only, limits enforced before any order leaves), PLAN.md (the goal, SPARQ's decisions Q1 to Q7, KAPS's decisions carried by number, the quoter's design, the build order), this log, ASSESSMENT.md (KAPS's, plus §7: the 2026-10-25 review) and LESSONS.md (KalTerm3's and KAPS's).

**What was kept, changed and why.** Kept: all of KAPS's code and tests, unchanged except for the names, because they are the tested base the quoter builds on (the read client, Ed25519 signing, the WebSocket client, the fixed-point money and fee code, the ingest, the history downloader and the study harness). Changed: the names, so SPARQ can deploy beside KAPS later without a clash with KAPS's running `kaps` services. Left out: the web UI material, which is parked.

**Verified (local, on this workstation).** `ruff format --check` and `ruff check` clean after the fixes below; `mypy --strict` clean on 165 source files; `pytest` 1,608 passed and 197 skipped (the database and POSIX-only tests, which CI runs). The 31 install tests, which check the rendered access rules with the new names, ran here and pass.

**Problems and fixes.**
- Two import blocks were out of order and one test line grew to 101 characters, because `sparq_*` is one character longer than `kaps_*` and sorts after names `kaps_*` sorted before. Fixed with ruff's own fixer and formatter.
- The first commit warned about line endings because the new repository did not yet have KAPS's `.gitattributes`; it was added to that commit before anything was pushed.
- GitHub had not yet recognised the licence a minute after the first push; a few minutes later it reported MIT (a delay, no change needed).

**Verified (live).** `test_live_public` under the renamed client: 4 passed, about 16 public GET requests, no retries and no 429s. It read the exchange status (4 shards, 1 schedule row), 15,394 series in 20 categories with no fee type outside the modelled family, 107 fee changes, 2,000 historical markets (all with a yes/no result, rules text and a settlement time), 1,000 historical trades (yes plus no equals $1 on every one, newest first), 200 live settled markets, 200 live trades and the candles; the summary file was deleted after reading.

**Problems and fixes (continued).** Comments and guides referred to KAPS's plan sections and decision numbers, which mean something else in SPARQ's plan; 21 references in 14 files now name KAPS's plan explicitly (the parking-lot references stay with SPARQ's own §9), and three comment lines rewrapped to stay within 100 characters.

**Verified (CI, pull request #1).** `check` on Ubuntu and macOS and the database job on PostgreSQL 18 with TimescaleDB, including its coverage gate, all pass in the new repository.

- A re-run of CI on a docs-only commit failed in the database job's setup, twice: `pg_createcluster 18 main` found a cluster already present, because the PostgreSQL 18 package on the runner now creates its default cluster when it installs (the same workflow had passed 20 minutes earlier). The step now drops that cluster first, if there is one, and creates its own with data checksums. KAPS's CI has the same step and will need the same fix if KAPS's CI runs again.

**Done.** Merged by the owner on 2026-10-25 (PR #1).

## S1. Evidence for the quoter (approved 2026-10-25)

### S1.1 The pre-registered middle-tier test (2026-10-25, done: NO-GO)

**Built.** In `sparq_analytics.study`: `rules.py` (rule features with no look-ahead: a series' median contracts per market from the fit part only, seconds since the market's previous trade, first trade, New York hour; rules that filter on these and on the yes price, scored for the maker by K0.5's strict fill rule with the conservative fee or for the taker; a weighted evaluation; a rules pre-registration with its digest, the family tail over the deciding rules and exploratory rules); `stratified_bootstrap` (events resampled within their category, sharing one studentized core with the existing bootstrap); the ledger now takes any registration with a digest and a size; the session's `busy()`, `rules_on_validate` and `rules_verdict`; the commands `rules-explore`, `rules-register`, `rules-verdict` and `--study-dir`; a rules table in the report; the synthetic world can plant a spread in one busier series (`planted_series`, `spread`, `trades_by_series`).

**What was kept, changed and why.** Kept: K0.5's fee and fill machinery, split, sampling and ledger, unchanged in what they compute. Changed: the bootstrap's core is shared (proved identical to KAPS's on six random cases, bit for bit); the ledger accepts the new registration through a small interface instead of a second ledger.

**Verified (local).** 21 rules tests; the full suite 1,629 passed and 197 skipped; ruff and mypy clean; the new modules fully covered. Planted bugs: 18, of which 4 survived the first run (activity read from the scored part, weights dropped, makers scored by the optimistic fill, exploratory rules deciding); a test was added for each, and all 18 are now caught.

**Verified (analytics node, real data).** SPARQ's clone `~/sparq/check` reading KAPS's download, writing only to the external drive's `sparq/study` folder. The validate reading of seven candidates, the owner's approval of four (R1, R2, R4, R5), `rules-register` (digest `95eec43a8d2d…`, tail 0.00625) and `rules-verdict --open-test` once. **NO-GO**: R1 -0.09 cents (lower -2.00), R2 +2.71 (-3.66), R4 +2.51 (-1.30), R5 -1.29 (-3.98), every rule over 300 events. Write-up: `docs/research/middle-tier.md`.

**Problems and fixes.**
- The first synthetic check planted a favourite-longshot bias, but the planted shift saturates at the probability floor and mid-priced markets add outcome noise without edge, so detection depended on the seed; a sharper longshot rule was dominated by rare large losses, which the skew-proof interval rightly refuses to call positive. The test world now plants the mechanism SPARQ relies on, a spread paid by takers, which every one of six seeds detects with a lower bound of +1.9 cents or more.
- The test frame helper evaluated a constant rule as one value; a series missing from the fit part made a filter null rather than false. Fixed (the rule is null-safe).
- On the analytics node the login shell is zsh, which does not split an unquoted variable into arguments, so the first run was refused at the argument check before anything was read; the run now goes through a bash script.

### S1.1b R4 refined on old data, the forward test registered (2026-10-25, registered; the verdict waits for the window)

**Built.** Rules gain quiet bounds (`quiet_lo_s`, `quiet_hi_s`), left out of the digest while unset so that S1.1's locked digest still reproduces (a test checks it against the value recorded on the analytics node); `evaluate_rule` takes the settings and the tail, so a scan is not held to the twelve-rule limit of a registration; `scan.py` (the declared grid, its variants and the choosing rule); `forward.py` (the frozen activity file and its hash, the forward registration, and a forward study whose single part is reachable only through its ledger); the commands `rules-scan`, `forward-register` and `forward-verdict`; decision D10 in the plan; the grid `docs/research/r4-grid.json`.

**Verified (local).** 13 forward tests; the full suite 1,642 passed and 197 skipped; ruff and mypy clean; the new modules fully covered. Planted bugs: 13, of which 3 survived the first run (a scan reading every part, the activity frozen from the validate part, the forward command's own split check); a test was added for each, and all 13 are now caught.

**Verified (analytics node, real data).** The 36-variant scan on the fit and validate parts (the test part not read): the fixed rule chose **quiet 60 seconds to 1 hour, activity 1,000 to 100,000, yes price 20 to 80 cents** (fit +0.30 cents, lower -2.48, 4,420 events; validate +11.99, -4.47, 1,004 events). The frozen activity file hash `edf3189cf82a...`; the forward registration digest `0af9ea44be7f...` (one rule, tail 0.025), with no ledger yet: nothing is opened. **The scan's main finding:** on the fit part (7 August to 28 September) R4 is -0.86 cents and only two of the 36 variants are positive, so the human-paced maker's edge comes only from the later periods (late September on): a change of season or chance. S1.1's candidate list had been chosen from the validate part alone and did not show this. Pre-registration note: `docs/research/forward-test.md`.

**Problems and fixes.** In the first version of the scan test, two variants selected the same trades (the thin series' activity, 84, sits below both lower edges), so the simplest correctly won; the test now uses edges that differ. The scan of 72 scorings at 10,000 resamples takes over ten minutes on the analytics node.

**Dropped (owner, 2026-10-27; PLAN D13).** After S1.2a's reading the owner dropped the forward test. It was never opened: no ledger was written and no forward day was read, so the registration stands only as a record. The forward days are not downloaded.

### S1.2a What the live book shows (2026-10-25 to 2026-10-27, done: the stop rule says "go on")

**Decisions (owner).** D11, live-data work in parallel with the forward test, with a guard: no edge to settlement on forward-test events before S1.1b's verdict, and windows of ten minutes at most. **The stop rule**, fixed before any reading: among the forward rule's own trades, if more than 50% of the levels takers cleared are refilled at the same price or better within 1 s, S1.2 is not started; fewer than 300 cleared levels is "too few". It is read once, on the UTC days 2026-10-25 and 2026-10-26. Trades near a feed outage are left out (decided before any reading). The storage budget was raised from 500 to 700 GB. S1.2's plan (D12) was approved for the case "go on".

**Access.** The production database (the owner's choice over a nightly export) already accepted the analytics node over TLS with `scram-sha-256` for any role. The owner created a read-only role on the server host: forced read-only transactions, at most 2 connections, a 15-minute statement timeout, `SELECT` on the tables and on those made later. It can write 0 of 19 tables (public and TimescaleDB's internal schema) and has no special attribute. The password is in the agent account's `~/.pgpass` on the analytics node; the server's certificate is pinned there, and its name matches, so the client checks the server fully. The role is outside `kaps-admin install`'s management.

**Built.** `sparq_analytics.live`:
- `source.py`: the connection settings file (no password allowed), the TLS connection, and `extract`. It copies whole UTC hours of the ticker frames and trades, once each and at least 10 minutes old, with times as whole milliseconds and through `COPY` to CSV and then Parquet, under temporary names. It copies the market list (without results) and the ticker gaps afresh each run, and refuses a gap in the hours it is asked to read.
- `book.py`, the measures, with every window capped at ten minutes in the settings:
  - the book the taker met: the last frame strictly before the trade;
  - markouts against the later mid;
  - S1.1's strict fill rule against a queue rule, for an order joining 1 s after the trade behind the size shown at its price, with no credit for cancellations ahead;
  - refills after a taker order clears a level;
  - trades near a feed outage left out.

  The forward windows are computed on reversed time per market with probe rows, so they are exact.
- `summary.py`: four populations (the forward rule's own trades, and the middle, busy and thin tiers in the rule's price band), contract-weighted means with K0.5's clustered bootstrap, the stop rule, and the markdown.
- `cli.py`: `extract`, `measure` (one UTC day at a time, from an hour before) and `report`.

The registered forward rule and its frozen activity file are reused as they stand.

**What was kept, changed and why.** Kept: the study's `Rule` and its filter, the frozen activity file and its hash, and the bootstrap, so that the population measured is exactly the forward test's kind. New: nothing in KalshiTerm2 or KAPS measured the book, so there was nothing to port. Questioned and corrected: KalTerm3's figure of 25,000 ticker rows and 4,000 trades a minute (LESSONS). It came from its first minutes on 18 October, scaled to a day, and caught about half of that hour's trades, which history shows at 478,007 for 04:00 to 05:00 UTC on 19 October.

**Measured (the ingest's first day, before any reading).**
- **Volume.**
  - About 89 M ticker rows and 14 M non-combo trades a day; the last 24 hours to 23:03 UTC on 25 October held 14,117,468 trades.
  - Kalshi's history shows growth from about 7.8 M trades a day in mid-August to 12.95 M in the week to 18 October. Weekends run about 10 to 15% above weekdays.
  - *The first two whole UTC days, counted at the reading (2026-10-27):* 25 October (a Sunday) 74,332,325 ticker rows and 14,109,134 trades (about 51,600 and 9,800 a minute); 26 October 58,415,187 and 12,268,181 (about 40,600 and 8,500 a minute). The first day's 89 M ticker rows was scaled from busy hours and is too high. Against KalTerm3's 25,000 ticker rows and 4,000 trades a minute, trades run about 2 to 2.5 times higher and ticker rows about 1.6 to 2 times.
- **Compression** (24 October's 2.8 hours, compressed by hand by the owner): ticker 10.5 times (2,962 MB to 282 MB, about 24 bytes a row) and trades 6.6 times (625 MB to 95 MB, about 45 bytes a row). Both are floors, since each market had about 133 rows to compress together. Uncompressed, a day takes about 23.5 GB; compressed, about 2.8 GB. The drive has 847 GB free. The budget is enforced nowhere yet: the limits that act are the watchdog's alert at 80% of the drive and the ingest's stop at 90%.
- **The feed**, in middle-tier markets, 18:00 to 19:00 UTC on 25 October:
  - **timing:** a ticker frame follows 99.95% of trades within 1 s (median 156 ms), and 94.6% of frames change the book;
  - **spacing:** frames are spaced at a median of 2.0 s with a broad peak near 1 s and no hard floor (some markets look batched on half a second);
  - **lag:** the feed reaches the server about 0.55 s after Kalshi's timestamp (95th percentile 1.07 s).

  So the book resolves to about a second.
- **Copying:** an hour takes about 9 s and 82 MB, so a day takes about 3.5 minutes and 2 GB. The copied trade counts equal the database's.

**Verified (local).** 40 tests on hand-built books with answers worked out by hand. They pass on Windows and on the analytics node. Planted bugs: 22. Of the first 18, 6 survived the first run, because the scenarios had no frame at a trade's own millisecond, no later trade exactly at a joined order's price, no trade between a trade and the join, no series on the tier boundary, and no priced ask with zero size; a pattern was also ambiguous. The scenarios were extended, and all 22 are caught, including 4 for the outage rule. The full suite: 1,680 passed and 197 skipped. Ruff and mypy are clean. CI is green on draft PR #5 (Ubuntu, macOS and the database job).

**Problems and fixes.**
- Two of the first connection checks failed with "relation `_hyper_…` does not exist". One used `approximate_row_count`, and the other looked tables up by unqualified name across TimescaleDB's chunks. Plain time-bounded queries work, and every query in `extract` is bounded by time, which also keeps its load small.
- The first grant script put `\password` in the middle of a pasted block, so the lines after it were taken as password input and the grants did not run. The owner re-ran them as one command.
- `~/.pgpass` was first written in the owner's account, not the agent account the copies run as; the owner wrote it there instead, readable by that account only.
- SPARQ's CLAUDE.md carried the analytics node's real data path (from S1.1b); it was replaced by neutral wording, and the path is in the assistant's memory.
- The planted-bug script rewrote files with Windows line endings; they were converted back to LF before the commit.

**Code review fixes before the reading (owner asked and approved, 2026-10-26; review items 1 and 5).** A review of the codebase found that the live tagging computed the rule's features (`first`, `quiet_s`, `ny_hour`) with its own code instead of the study's, so the two definitions could drift and the live work score a different rule from the one registered; and that `measure` held every day's frames until the summary, which ten days (S1.2c) would not fit.
- *Built.* `add_trade_features` is split out of the study's `add_rule_features` with no change to the study's output, and the live `tag_trades` calls it after sorting trades of one millisecond by trade id. `measure` writes each day and lets it go, then reads back only the rows a population summarises (`in_any_population`). Two determinism gaps found on the way were fixed: the summary now sorts events before resampling (intervals repeat exactly), and frames of a market at one millisecond are ordered by arrival, so the as-of join takes the frame received last.
- *Verified.* New tests: the live features equal the study's on the same trades (time built apart from the live code); a tie at one millisecond is tagged the same in any input order; reading back only the populations gives the same summary; the summary does not depend on row order; of two frames at one millisecond the book is the later arrival. Six planted bugs, all caught. The full suite: 1,687 passed, 197 skipped; ruff and mypy clean. On the analytics node, 2026-10-25 measured with `measure_day` only (no summary, no share): the old code and the new gave 13,834,269 per-trade and 8,007,274 per-order rows and the same 274,865 left out; the first comparison differed in one `q_ahead_e2` value, traced to frames at one millisecond (47,444 market-milliseconds of 74.3 million frames); after the fix two runs were identical to each other and to the old run. One day still peaks at about 23 GB inside `measure_day`; the summary reads back 6,679,905 trade and 4,211,672 order rows a day. The comparison files were deleted.

**Second review's items 1 and 2, before the reading (owner asked, 2026-10-26).**
- *Built.* `extract` copies each gap's reason. An `all` gap (a channel that could not be resubscribed) counts as a ticker outage only when its reason names the ticker channel, and since the ingest that writes the log (KAPS's, frozen) never closes it, it ends at the next `ticker` gap, the reconnect or restart that subscribed again, or else at the end of the data. Before, one such gap would have dropped every later trade. `measure_day` measures the day in 16 groups of markets of about equal trade counts, tagging each group on its own; every step is per market (its previous trade, its book, its windows), so the result does not depend on the grouping.
- *Verified.* Tests: a failed resubscribe counts only for the ticker channel and ends at the next reconnect, or runs to the end of the data without one; any number of groups gives the same trade and order measures as one. Three planted bugs, all caught. The full suite: 1,689 passed. On the analytics node (after `extract` refreshed the gap list: 7 ticker gaps, none of kind `all`), 2026-10-25 by the old code and the new: identical per-trade and per-order outputs, the same 274,865 left out. Memory, by a step-by-step probe: tagging the whole day held 10.4 GB and the outage filter 4.4 GB before any grouping, and groups balanced by trades still differ in frames (5.5 to 28.6 million in four groups); tagging per group and 16 groups bring the peak from 20 to 23 GB down to 14.5 GB (8 groups 15.3 GB, 32 groups 13.4 GB), in 24 to 27 s. What remains is mostly the day's own output and memory the allocator keeps. The comparison files were deleted.

**The reading (2026-10-27 01:17 UTC, once, as approved).** On the analytics node, the check clone at `8d82def`: `extract` from 2026-10-24 21:00 copied the 14 new hours (to the end of 00:00 to 01:00 UTC on 27 October; 7 ticker gaps, none of kind `all`); `measure --start 2026-10-25 --end 2026-10-27` with `docs/research/forward-rule.json` and the frozen activity file took 52.8 s for both days with a peak of 14.3 GB; `report`. `book.json` records the activity hash `edf3189cf82aa9f340aaf3b9798c7a0465c17a643e64b5b9ab36e1b9b6e7abf8`, equal to the frozen one. Measured: 25 October 13,834,269 trades and 8,007,274 taker orders, 274,865 trades left out near feed outages (13,834,269 + 274,865 = the day's 14,109,134 copied); 26 October 12,268,181 trades and 6,803,744 taker orders, none left out. All figures are cents a contract, contract-weighted, **before fees**, with 95% intervals from the bootstrap clustered by event.

**Stop rule: go on.** Among the forward rule's own trades, 11.1% of 43,742 cleared levels over 2,491 events were refilled at the same price or better within 1 s (stop above 50%; at least 300 needed).

| Population | Trades | Events | Book shown | Median spread | Median size at level | Half-spread earned |
|---|---|---|---|---|---|---|
| forward rule | 226,563 | 2,872 | 99.9% | 1.0 | 693 | +0.98 (+0.91 to +1.05) |
| middle, 20-80c | 5,116,552 | 5,828 | 100.0% | 1.0 | 115 | +1.51 (+1.40 to +1.61) |
| busy, 20-80c | 7,017,144 | 1,027 | 100.0% | 1.0 | 4,228 | +0.76 (+0.72 to +0.80) |
| thin, 20-80c | 378,117 | 2,372 | 98.6% | 4.0 | 57 | +3.14 (+2.90 to +3.44) |

Markouts of the resting maker against the later mid:

| Population | 5 s | 60 s | 600 s |
|---|---|---|---|
| forward rule | +0.53 (+0.45 to +0.57) | +0.44 (+0.37 to +0.49) | +0.23 (-1.07 to +0.55) |
| middle, 20-80c | +0.36 (+0.31 to +0.41) | +0.28 (+0.22 to +0.34) | +0.41 (+0.23 to +0.58) |
| busy, 20-80c | +0.19 (+0.13 to +0.25) | +0.14 (+0.03 to +0.24) | +0.04 (-0.36 to +0.41) |
| thin, 20-80c | +0.91 (+0.53 to +1.27) | +0.78 (-0.04 to +1.26) | +1.05 (+0.25 to +1.77) |

An order joining 1 s after the trade, filled within 600 s, and the markouts of the filled ones (strict rule; the queue rule's markouts are within about 0.5 cents of these):

| Population | Joinable | Strict fills | Queue fills | 5 s | 60 s | 600 s |
|---|---|---|---|---|---|---|
| forward rule | 219,774 | 34.7% | 36.7% | +0.20 (-0.10 to +0.32) | -0.32 (-0.44 to -0.21) | -2.91 (-6.70 to -1.90) |
| middle, 20-80c | 4,337,687 | 84.1% | 85.4% | -0.40 (-0.49 to -0.31) | -0.81 (-0.92 to -0.70) | -1.42 (-1.71 to -1.13) |
| busy, 20-80c | 6,110,173 | 87.0% | 87.7% | -0.27 (-0.36 to -0.17) | -0.61 (-0.76 to -0.45) | -1.00 (-1.46 to -0.52) |
| thin, 20-80c | 344,364 | 69.3% | 72.4% | +0.01 (-0.52 to +0.45) | -0.81 (-2.10 to -0.11) | -1.98 (-3.30 to -0.74) |

Refills of the levels takers cleared (share refilled at the same price or better within the time):

| Population | Cleared | 0.5 s | 1 s | 2 s | 5 s | 10 s | 60 s | 600 s |
|---|---|---|---|---|---|---|---|---|
| forward rule | 43,742 | 8.6% | 11.1% | 13.3% | 18.4% | 22.8% | 38.3% | 62.2% |
| middle, 20-80c | 411,478 | 16.3% | 26.3% | 32.8% | 42.3% | 49.7% | 67.9% | 83.2% |
| busy, 20-80c | 135,011 | 19.1% | 33.1% | 41.2% | 51.0% | 57.5% | 74.4% | 88.0% |
| thin, 20-80c | 64,776 | 9.9% | 13.4% | 16.8% | 23.9% | 29.5% | 51.9% | 73.3% |

**What it says.**
- *Bots do not requote after every trade* in the forward rule's markets: a cleared level is back within a second one time in nine, and only 62% of the time within ten minutes, slower than the middle and busy tiers. By the stop rule, S1.2 goes ahead (D12).
- *The maker already resting earns:* a 1-cent spread with deep size, and a positive markout at 5 s and 60 s, before fees.
- *A maker who joins after the trade is adversely selected.* In the forward rule's markets it fills only about a third of the time (the middle and busy tiers about 85%), and the fills it gets lose 0.3 cents by 60 s and about 2.9 cents by 10 minutes, before fees; in every tier the joiner's 60 s and 600 s markouts are negative. S1.2's gate (D12) asks for a positive 10-minute realized spread after fees, so this is the main risk for S1.2: its simulator must quote before the trade, not chase it, and its fair-value filter has to keep it off the side that is about to be taken. The reading decides nothing about the edge itself; S1.2c's held-out run does.
- *For S1.2's settings:* the queue rule and the strict rule give fills within about 3 percentage points and markouts within about 0.5 cents of each other, so the fill model's choice is not what decides S1.2; the book is shown at 98.6% or more of trades, so the ticker's top of book suffices to cross-check the recorder.

**Still to do (after the reading).** Two small leftovers, `ONE_E6` in `live/book.py` and the live command line's command list, go into the next slice that touches the live code (S1.2r). The result files stay in the data folder's `results`.


### S1.2p Shared definitions (2026-10-26, done)

The owner asked for a review of the codebase on 2026-10-26 and approved fixing its first five items before S1.2a's reading. Items 1 and 5 touch S1.2a's code and went into that slice (BUILD-LOG S1.2a). Items 2 to 4 are this slice, on branch `s1/2p-shared-defs` from `main`; the owner allowed it to start before PR #5 merged. Nothing changes on any machine: SPARQ's server code is not deployed, and KAPS's ingest is frozen and untouched.

**Built.**
- *The stored market in one place (item 2).* `sparq_core.markets` holds the combo prefix, the settled statuses, the labels, the market columns in their order and `market_values`, the row builder. The downloader's Parquet schema and `markets_table`, the ingest's discovery and backfill, the event rows and `db/schema.py` (columns and the label check) all use it. Before, the helpers were written twice (`history/schemas.py`, `ingest/records.py`), the combo prefix three times and the column lists three times, held equal only by a test.
- *The WebSocket address (item 3).* `config.py` has `WS_URLS` beside `REST_URLS` and `KalshiConfig.ws_url`; the demo address is from Kalshi's demo-environment page, checked 2026-10-26. `FeedConfig` defaults to demo, as the REST client does, and a feed on production logs a warning. The ingest builds its feed through `make_feed` for its configured environment, which is production. Before, `ws.py` hard-coded production's address outside the configuration.
- *Names that several programs share (item 4).* `sparq_server.names` holds the unit, settings and credential names and the exit codes that `install` writes and the ingest, the watchdog, `verify` and `ingest-status` read. `ingest.state` gains `STOPPED_REASON`, and `ingest.gaps` gains the gap kinds. The watchdog passes the state keys and gap kinds as query parameters, and `ingest-status` writes them in from the constants. The watchdog's disk message names the ingest's own stop level, which `alerts.toml` now carries (`disk_stop_percent`), instead of a fixed 90%. The unused `LAST_SERIES` key is gone.

**Kept, changed and why.** The migrations stay frozen copies: a migration records what was run. The `gap_log` status check in `db/schema.py` keeps its text, because the database layer should not import the ingest's module. The SQL `ingest-status` runs is unchanged text, so its tests match it as before.

**Verified (local).** New tests: the market row builder (columns, derived values); the Parquet file and the table have the shared columns in order; the label check keeps the migration's text; each environment's WebSocket address; the feed defaults to demo and warns on production; the ingest's feed is production; the unit does not restart on the codes the service stops with; the watchdog reads the ingest's stop level from the rendered settings. The full suite: 1,649 passed, 197 skipped (the database tests run in CI). Ruff and mypy are clean, and the 95% gate on the money code passes. Planted bugs: 12. Eleven were caught locally. The twelfth (a trades gap opened as a ticker gap) is caught only by the pump's database tests, which assert the gap kinds and run in CI's database job.

**Problems and fixes.**
- Tests that imported a moved name from its old module failed mypy's explicit re-export rule; they now import it from where it lives.
- Two edit scripts written through a shell heredoc lost a backslash escape, so their patterns did not match. They stopped on their checks, and the edits were made with the editor instead.

### S1.2q Review clean-up (2026-10-26, done)

The owner asked on 2026-10-26 to fix every outstanding item of the two code reviews. The second review's items 1 and 2 touched the code S1.2a's reading runs and went into S1.2a (BUILD-LOG S1.2a); everything else is this slice, on branch `s1/2q-cleanup` from `main` (draft PR #7, opened early so CI's database job ran on every push). No result changes, and nothing is deployed: SPARQ's server code runs nowhere yet, and KAPS's ingest is frozen.

**Built.**
- *Core.* `KalshiConfig.at_rate` for a client paced at a rate with a second's burst of at least one request (the downloader, `from_env` and the ingest use it; the ingest gave a burst equal to the rate, which a rate below 1 made invalid). `MarketData._object` is generic and `_rows` reads a list of objects, so the readers return directly. `Trade` and `SeriesFeeChange` use the typed fixed-point fields, one `check_prices_add_to_a_dollar` serves REST and WebSocket trades, and the WebSocket messages share the REST models' base. `ratelimit.backoff` is the capped exponential wait of the REST client, the feed and the ingest writer.
- *Ingest.* `ingest/retry.py` (`RETRYABLE`, `retrying`) replaces the writer's two retry loops and the pump's. The writer builds its kind table once, keeps bounded sample deques, measures only the ticker lag it reports, and uses `sparq_server.stats.percentile` (shared with the durability writer). A reconnect or restart ends any `all` gap still open where its new window starts (`gaps.close_open`), so SPARQ's own ingest never leaves one open. The exchange poller's sleep is injected like its siblings'.
- *Install and admin.* The install's steps are one table, each with its plan lines and its run, so `--dry-run` is made from what runs and cannot drift; it no longer promises a storage governor, a web port or `/opt/sparq/<version>`, and it refuses what the install would: recovery from a backup and a TLS mode of `restore`, which `install` used to ignore silently, making a new certificate. `admin/pg.py` holds `psql`, `clusters`, `stanza_info`, `as_postgres` and `ensure_role`, `ensure_database`, `ensure_timescaledb` (four hand-written "create if missing" blocks before); `installed_version` and `write_if_different` are in `system.py`. `managed` gains `cluster_files` (install and the restore drill) and one `HARDENING` block for every unit; `data_directory` comes from `DATA_ROOT`. Preflight's package check reads the pins (it now reports pgBackRest too), and `alert-test`'s hint names the unit from its constant.
- *The power-cut test* is the workspace package `sparq-durability` (module `sparq_durability`), which `install` never builds; it used to ship in the production environment on the server host. Its tests moved with it and `DURABILITY.md` names the new package.
- *Study.* One `sha256_of` for every lock; one `outcome` (GO, NO-GO, INCONCLUSIVE) for the segments' verdict and S1.1's rules, without the dead fallback; `E6` from `fixedpoint` and the cent from the settings; one `ratio_and_se` (numpy for the bootstrap, polars aggregations for the cells); one `scan_markets` for the trades frame, the split proposal and the time reference, which now also keeps the historical row; `explore` tags the trades once and `build_snapshot` reads them twice instead of three times; the study command line takes its commands from its handlers.
- *History.* The verifier reconciles the markets as tables (each market's day coverage from a running count over the calendar, the trade sums from one scan), not a Python loop over millions of rows; `duplicate_ids` and `outside_day` are shared by the downloader and the verifier; `candles` reads with polars, types its folder and takes the series from `series_ticker_of`, with the same sample.

**Kept, changed and why.** The study's digests are untouched: S1.1b's forward test is registered by `0af9ea44be7f...` until its verdict, so a test now pins it (rebuilt from `forward-rule.json`, the registered settings and the frozen activity hash) beside S1.1's. The `gap_log` status check keeps its text, and the backfill's three attempts keep their uncapped doubling. Left for later because they sit in S1.2a's code, which the reading runs: `ONE_E6` in `live/book.py` and the live command line's command list.

**Verified (local).** The full suite: 1,658 passed, 198 skipped (the database tests run in CI and passed on every push). Ruff and mypy are clean, and the 95% gate on the money code passes. New tests: a client at a rate below one, the plan names exactly the steps that run, the dry run refuses recovery and a certificate restore, every unit runs in the full sandbox, a failed resubscribe ends at the next window (database), the forward digest, the polars standard error equals the bootstrap's, the market loader keeps the historical row, and a missing middle day leaves a market unreconciled. Planted bugs: 15. Fourteen were caught at once; the fifteenth (a sandbox line lost from a unit) survived, and the sandbox test was written for it, which now catches it.

**Problems and fixes.**
- Edits written to a file this branch had not read did not apply (the editor refuses until the file is read), and two edit scripts sent through a shell heredoc lost their backslash escapes; both were redone, and the later scripts were written to files.
- The first plan from the steps lacked the words two tests look for ("data_checksums on at initdb", "alerts"); the wording was kept.
- The polars standard-error test first gave both expressions the same column name; they are aliased.

## S2. Weather against station observations (approved 2026-10-27)

### S2.1 Sources, terms and the observation history (2026-10-27, in progress: the 24-hour delay run ends 2026-10-28 about 02:12 UTC)

**Built.** The package `sparq_analytics.weather`, run as `python -m sparq_analytics.weather stations|download|check|delay|delay-report`:
- `stations.py`: the map from climate site to station and zone (39 sites). Each market's site is read from its own rules (by code, or by station id). Markets naming no site take the one code their series and city use. Also the climate day (local standard time) with the civil day as a variant, and the scope: an explicit list of 24 daily-high series plus `KXRAIN`.
- `metar.py`: the parser.
  - Temperature from the tenths remark; otherwise from the body's whole degrees at the lower edge.
  - The six-hour maximum, and hourly precipitation, where a trace counts as 0.
  - Every temperature becomes its lowest possible whole °F (W4).
- `bounds.py`: each report's own readings and the day's running bound and rain flag, under both day definitions.
  - A six-hour maximum counts only when its six hours lie inside the day.
  - Rain counts only when the routine report it is measured from was inside the day.
- `archive.py`: the downloader for the Iowa Environmental Mesonet's archive. Routine and special reports, paced at 1.1 s, backing off on 429 and 5xx errors, resumable per station.
- `check.py`: the gate.
  - Each settled market's result against what its station's day settled, with W4's margins and, for information, without them.
  - It refuses any day in the test part.
- `live.py`: the delay recorder. It polls NOAA's aviation weather service once a minute; the first answer is the baseline.
- The two addresses and the user agent are in `sparq_core.config`; `httpx` is declared by `sparq-analytics` (already in the lock).
- The terms are in `docs/research/weather-sources.md`.

**What was kept, changed and why.** Nothing in KalshiTerm2 or KAPS read weather, so nothing was ported. Changed from the slice plan:
- The scope is an explicit series list, not the `KXHIGH` prefix. The prefix caught `KXHIGHINFLATION`, which the real data showed.
- The 24-hour maximum group is not parsed. It arrives at the end of the day, so it adds nothing within the day.
- West Palm Beach (`CLIDJT`) reports as `KDJT`, not `KPBI`: the archive has no `KPBI` reports since the airport was renamed.

**Verified (local).**
- 70 tests, with hand-built reports and answers worked out by hand: negative temperatures, a missing tenths remark, a trace, the six-hour maximum across the day start, the standard day across the autumn clock change, the Phoenix zone, retries and resuming, and the baseline poll.
- 16 planted bugs: 15 were caught on the first run. A special report that restarted the rain window survived, so a test was added, and all 16 are caught.
- The full suite: 1,775 passed and 198 skipped; ruff and mypy clean; the weather package fully covered.

**Verified (analytics node, real data).**
- *Stations.* Every in-scope market from 2026-08-07 to 2026-10-10 maps to a station: 62 series and city combinations.
  - Houston's rain moved from Bush Intercontinental (`CLIIAH`, to 2026-08-13) to Hobby (`CLIHOU`).
  - Chicago's rain is O'Hare, while Chicago's high is Midway.
  - The highs' rules switched from the NWS report to The Weather Company on 2026-08-15.
- *Download.* 2026-08-01 to 2026-10-23: 87,990 reports from 38 stations (`KPBI` had none), then 2,307 from `KDJT`. That is 90,297 in all, 2.2 MB, in under two minutes.
- *Check on the fit part* (2026-08-07 to 2026-09-28): 8,397 settled markets, all with a station and reports.
  - With W4's margins the bounds settle 2,243 under the standard day and 2,244 under the civil day, and **none is wrong**.
  - Without the 1 °F margin, one call is wrong under the standard day: `KXHIGHNY-26AUG27-T80`, "below 80", settled YES although an hourly report put Central Park at 80 °F or more. The civil day adds `KXHIGHTDC-26SEP22-T66`. The Weather Company's daily maximum can come in below an hourly reading, so the margin is needed.
  - Rain is the same under both day definitions: 913 NO with no rain reported, 271 YES with rain reported, and 7 YES the hourly reports did not count (misses, not wrong calls).
  - Coverage: 85 days per station. Most stations are short of 24 routine reports on 2 to 7 days, largely the partial first and last days of the download. Boston is short on 20 days and Providence on 11.

**Problems and fixes.**
- The first `stations` run stopped at `KXHIGHINFLATION-26DEC`: the prefix scope was replaced by the explicit list.
- `KPBI` returned no reports; the live feed and the archive both carry the station as `KDJT`.

**Still to do in S2.1.** After the delay run ends: `delay-report` (the delays and the comparison of live reports with the archive), the W4 delay for the history, and the PLAN row; then the PR.
