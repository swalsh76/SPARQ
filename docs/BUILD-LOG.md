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

### S1.c Close-out (2026-10-28 UTC, done)

**Built (documents only).**
- PLAN: **D14** closes S1. S1.2b and S1.2c close unbuilt, with their plans kept as a record; S1.3 and S1.4 are not run under S1; the S1 row, the status line and the slice table say so.
- Parked in §9: S1.2r, S1.2b's order-book recorder, and the write path (D7) with the pilot (D8).
- LESSONS.md gains "What SPARQ measured", with S1.1's and S1.2a's results. ASSESSMENT §7.4's leads get their tested outcome.
- The CLAUDE.md status and resume point.

**Gate evidence.**

| Slice | Outcome | PR |
|---|---|---|
| Proposal (D1 to D9) | approved 2026-10-25 | #2 |
| S1.1 the pre-registered middle-tier test | **NO-GO** (D3): no rule's lower bound above zero (BUILD-LOG S1.1) | #3 |
| S1.1b R4 refined, forward test registered | registered, then **dropped unopened** (D13) | #4 (registration), #8 (D13) |
| S1.2a what the live book shows | **done, go on** (11.1% of 43,742 cleared levels refilled within 1 s), but a joining order is adversely selected (2.9 cents lost by 10 minutes before fees) | #5 |
| S1.2p shared definitions | done, behaviour unchanged | #6 |
| S1.2q review clean-up | done, behaviour unchanged | #7 |
| S1.2b the order-book recorder | **closed unbuilt** (D14) | |
| S1.2c the shadow quoter's simulator | **closed unbuilt** (D14) | |
| S1.2r analysis efficiency | **moved to §9** (D14), not started | |
| S1.3 the write path on demo, S1.4 the pilot | **not run under S1** (D14; D7 and D8 parked) | |

No real order was sent in S1. Every key stayed where D6 puts it.

**What stays.**
- The code S1 built stays on `main`, tested:
  - the rules harness and stratified bootstrap (S1.1);
  - the live-book reader (S1.2a);
  - the shared definitions (S1.2p).
- On the analytics node: S1.1's study outputs and rules ledger, S1.1b's registration files, S1.2a's live copies and results, and the read-only key (D6). Nothing was deleted or changed on any machine.

**Verified.** Documents only: no code changed, so no test was run for this slice. CI runs on the PR.

**Review (PLAN Q8).** Each figure was checked against the BUILD-LOG entries of S1.1 and S1.2a and against D13. The merge review, at medium effort over the branch against `main`, found 8 documentation issues and no runtime bugs, all fixed:
1. The resume point's S1.2a line still gave the node's check clone as `8d82def`. **Fixed:** removed; the clone's state is in the "Now" lines.
2. The S1.2q row still said its two leftovers waited in S1.2a's code. **Fixed:** they moved with S1.2r to §9.
3. The entry first said the documents-only diff needed no review, against Q8. **Fixed:** this review is the record.
4. The entry lacked the "Problems and fixes" heading. **Fixed.**
5. LESSONS.md's intro, PLAN's status line and CLAUDE.md described the file as the predecessors' measurements only. **Fixed.**
6. ASSESSMENT §7.4's heading still called the section validate-only. **Fixed:** it names the tested outcome.
7. CLAUDE.md's status still read "S1.2 on hold". **Fixed:** it states D14's outcome per slice.
8. D14 parked D7 and D8 but left D6's trading key and its custody design without a home. **Fixed:** they go with D7, in D14 and in §9.

**Problems and fixes.** Only the review's findings above.

**Decisions (owner, 2026-10-28, as recommended).**
- S1.2r goes to §9.
- The read-only key stays on the analytics node.
- S1.c goes first, then S2.c, as separate PRs.

## S2. Weather against station observations (approved 2026-10-27)

### S2.1 Sources, terms and the observation history (2026-10-27 to 2026-10-28, done)

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
- *Publication delay* (`delay`, 2026-10-27 02:12 UTC to 2026-10-28 02:12 UTC, detached under `caffeinate` from the check clone; then `download` for 2026-10-26 to 2026-10-28 and `delay-report`):
  - 1,143 reports from 38 stations: 76 in the first poll (the baseline) and 1,067 that arrived during the run. About 1,440 polls a minute apart; 8 failed with an HTTP error and were retried the next minute.
  - Seconds after the observation time:

    | | median | p95 | max |
    |---|---|---|---|
    | First seen by the poll | 195 | 312 | 1,866 |
    | Received by the service (its own time) | 188 | 265 | 1,865 |
    | First seen, routine reports (998) | 195 | 312 | 1,866 |
    | First seen, special reports (69) | 193 | 260 | 313 |

  - First seen later than 5, 6, 7, 10 and 15 minutes: 90, 24, 12, 7 and 4 of the 1,067. The slowest were routine reports: Providence 31 minutes, Chicago O'Hare 29, New Orleans 16, Tampa 15.
  - Against the archive: all 1,143 found; 1,109 with the same text and 1,142 with the same readings. The one exception, College Station at 10:53 UTC on 2026-10-27, is archived without its remarks, so the archive reads the body's lower edge (16.5 °C) instead of the tenths (17.2 °C): the archive can only understate, never overstate, a bound.

**Review (PLAN Q8).** Each commit from 2026-10-28 was reviewed at low effort, with no findings. The merge review, at medium effort over the branch against `main`, found 10 issues:
1. `check` crashed on a window with no settled market (polars error on an empty frame). **Fixed:** it is refused with a message; test added.
2. Network errors (`httpx`) and polars errors escaped the command line's "exit 1 with one error line". **Fixed;** test added.
3. A rain market was called YES on any rain, whatever its strike. All 2,357 `KXRAIN` markets in the history are "more than 0", so no past call changes. **Fixed:** only that strike is called; tests added.
4. A restarted delay recorder wrote the last two hours' reports again. The run measured had no restart. **Fixed:** it reads what the file holds first; test added.
5. The archive comparison could count one live report twice where the archive holds two reports in the same minute. The run measured found 1,143 of 1,143, so it was not affected. **Fixed:** each live report is matched once, against any of them; test added.
6. The `stations` docstring had Houston's rain codes in the wrong order. **Fixed.**
7. The PLAN said W4 counts from the "time of issue" while the decision is the observation time; S2.2's row lacks the added measure. **Fixed:** W4 reworded; the S2.2 row is updated in PR #15, which already changes that row.
8. The archive fetch waited up to a minute after its last failed try. **Fixed;** test asserts 5 waits for 6 tries.
9. `in_scope` duplicated the scope filter and was used only by a test. **Fixed:** removed, and the test reads `SCOPE`. `day_bounds` is also unused by the code, but kept: S2.2's timeline needs it.
10. The parser's docstring said whole degrees are "at least" half a degree high; it is "up to", and the code takes the lower edge. **Fixed.**

The six code fixes were undone one at a time: the tests caught all six. Weather tests: 75 passed, the package 100% covered; ruff and mypy clean.

**Decisions.**
- **W4's history delay is 10 minutes after the observation time** (owner, 2026-10-28, as recommended). The p95 rounded up (6 minutes) would leave 2.2% of reports counted before they were public, each one a chance to overstate the edge; 10 minutes leaves 0.7%.
- **Added to S2.2** (owner, 2026-10-28): measure, from the data already held, how often and by how much the settled high is above the highest hourly report available at the time, and how many hours later the reports show it. It sizes the follow-up below.
- **Parked in PLAN §9** (owner, 2026-10-28; named as scope creep): the station's own 5-minute readings for earlier certainty, checked only if S2.2 finds an edge limited by when the outcome is known; and other weather networks for the direction and speed of temperature change, a forecasting strategy, low priority.

**Problems and fixes.**
- The first `stations` run stopped at `KXHIGHINFLATION-26DEC`: the prefix scope was replaced by the explicit list.
- `KPBI` returned no reports; the live feed and the archive both carry the station as `KDJT`.
- The delay run was started before the `KDJT` fix, so it polled `KPBI` and has no West Palm Beach reports. The 38 other stations agree closely, so the delay was not re-measured.

### S2.h Core hardening from IKARS's review (2026-10-27, owner approved; done)

**Why.** IKARS (the owner's sibling project) seeded its core from SPARQ's `sparq-core` at `c017add`. Its live checks and merge review (IKARS BUILD-LOG M1.2) found weaknesses that were still in SPARQ. Each was checked against SPARQ's code before the slice, and the owner added the slice ahead of S2.2.

**Built.**
1. **One table of public market-data routes** (`auth.public_route`), used by both the data signer and the public client's guard; before, there were two tables with different rules.
   - Under `/historical`, only `cutoff`, `markets` and `trades` (and their sub-paths) are allowed. `/historical/fills`, `/orders` and `/positions` are account archives, and the old patterns allowed anything under `/historical`.
   - `/account/limits` came off the data key, which can now sign no account read at all.
   - The signing vectors' specification (`tools/vectors/generate_signing.py`) moves `/account/limits` to the refused cases and adds the three archives. It was regenerated from the KalshiTerm2 reference: 280 cases, 150 refused.
2. **Market-data channels only on the WebSocket** (`ws_models.MARKET_DATA_CHANNELS`: ticker, trade, market and event lifecycle, the combo lifecycle, `orderbook_delta`). `subscribe` refuses any other channel before sending anything.
3. **No key path in any error.** A missing, unreadable, too-open or malformed key file is "the private key file", and the `chmod` hint names `$SPARQ_KALSHI_PRIVATE_KEY_PATH`. The underlying exception is not chained, since its text carries the path.
4. **Key-id checks.** The constructor refuses quotes and spaces, and the environment loader requires Kalshi's UUID form. The reason (`key_id_problem`) never contains the id.
5. **SPARQ's own variables:** `SPARQ_KALSHI_ENV`, `SPARQ_LIVE` (exactly `1`), `SPARQ_KALSHI_KEY_ID` and `SPARQ_KALSHI_PRIVATE_KEY_PATH`. KAPS's `KALSHI_*` variables no longer reach SPARQ. CLAUDE.md's commands follow.
6. **Real markers only** for the opt-in tests (`get_closest_marker`), in all four conftests.
7. **Dollar prices take text only** (`OptDollarsE6`, `RequiredDollarsE6`). Numbers Kalshi really sends as numbers use the new `OptAmountE6` and `RequiredAmountE6`: the fee multiplier, and the ticker's whole-dollar `dollar_volume` and `dollar_open_interest`.
8. **The rate-limit ramp test is deleted.**
9. **CI:** a `pip-audit` step on the Ubuntu job (`pip-audit` 2.10.1, Apache-2.0, released 2026-06-10, dev only), and `.github/dependabot.yml` (weekly, GitHub Actions and the uv lock; pull requests only).

**What was kept, changed and why.** Kept from IKARS: the route table, the channel set and the key-id check, nearly as written. Changed:
- SPARQ has no account reader, so the account routes are simply refused rather than moved to a second signer.
- The text-only rule covers dollar prices, not just account amounts as in IKARS, because the real data supports it (below).
- Fields that legitimately arrive as numbers keep a separate type.

Not taken: IKARS's Windows ACL check (SPARQ runs on Linux and macOS only), its account models and its book code (none in SPARQ yet; lessons for S1.3 and S1.2b).

**Verified (local).**
- New tests: account archives and limits refused by the signer and the client; private channels refused with nothing sent; no path in four key errors; key-id reasons that never show the id; KAPS's variables ignored for the environment and the key; bare numbers refused in market and trade prices, while multipliers still accept them.
- 14 planted bugs, all caught.
- The full suite: 1,770 passed and 195 skipped. Ruff and mypy are clean; money and fees are at 100%.
- `pip-audit`: no known vulnerabilities.

**Verified (real data and live).**
- All 18,847,879 market records of the history download on the analytics node send their `*_dollars` fields as text, none as a bare number.
- Live, under `SPARQ_LIVE=1`:
  - `test_live_public`: 5 passed. Of 11,000 dollar prices read raw from an open-markets page and a trades page, none was a bare number.
  - `test_live_bounds`: 4 passed; the time bounds, page sizes and cursor rules are unchanged.
- **The signed WebSocket check (`test_live_ws`), after the owner set `SPARQ_KALSHI_KEY_ID` and `SPARQ_KALSHI_PRIVATE_KEY_PATH` (2026-10-27):** 4 passed.
  - A signed market read returned HTTP 200, and the data key refused to sign `/account/limits`.
  - 30 s of ticker, trade and lifecycle frames, with no parse errors, no unknown fields and no sequence gaps.
  - Frames arrived 36 to 39 ms after `sending_ts_ms`.
  - A forced reconnect reported no false gaps, and adding and removing markets is not a gap.
  - *Fix found by this run:* the live test had proved the key by signing `/account/limits`, which S2.h refuses. It now checks that refusal offline and proves the key with a signed market read (follow-up PR). CI does not run live tests, so this showed only here.

**Live facts carried from IKARS, for later slices.**
- Production book messages have no `clock` yet (Kalshi's changelog dates it 2026-10-29).
- Both sides of the book are bids.
- Book deltas run at up to 783 a second on 15-minute crypto markets.
- Messages arrive about 46 ms after `sending_ts_ms`.
- The Basic API tier allows reads at 200 tokens a second (burst 600) and writes at 100 (burst 100).

**Problems and fixes.** The private-channel test first expected no commands at all; the fake server records commands per connection, so "nothing sent" is `[[]]`.

### S2.2 Exploration on the fit and validate parts (2026-10-28 UTC, done: no rules)

**Built.** `python -m sparq_analytics.weather explore --part fit|validate` (`weather/explore.py`). It refuses the test part by name and writes `explore-PART.json` and `explore-PART-trades.parquet` (the trades after each call) to the weather folder.
- *Timeline.* A report counts 10 minutes after its observation time (W4). A bound counts only where **both** kinds of climate day allow it: the lower of the standard and the civil day's running bound, and rain once both have it. A report near midnight can then never settle the wrong day. *Chosen in building (not in the slice plan):* S2.1 found no wrong call under either day, and this is the conservative way to use both.
- *The call:* the first counted moment the bound reaches the market's threshold (`check.threshold`, split out of `implied` so S2.1's and S2.2's calls cannot drift apart).
- *Ours:* a trade after the call whose taker bought the certain side, as approved. It is priced one cent worse (W5) with the taker fee rounded up to a cent, each trade one order, and paid on Kalshi's result (W2).
- *Capacity:* an upper bound (every such trade) and a lower bound (the first per market that was worth taking when it came: price and fee below a dollar), each with the profit per contract and a studentized bootstrap clustered by city-day. Chicago's O'Hare and Midway count as one city, and so do Houston's two stations. The dollars a day are over the part's calendar days.
- *W8:* the minutes from the call to the first trade of ours and to the last profitable one; the trades on the ruled-out side after the call, and those at 99 cents or more for the certain side; how long before the call the certain side last traded below 95 cents (the book's lead); the calls Kalshi's result contradicted.
- *The gap* (owner, 2026-10-28): per city-day of highs, the settled high (at least the winning bracket's lowest degree) against the highest hourly temperature, and the hours from the hourly peak until the bound (with the six-hour maximum) reached it (0 when it was no later).
- `study.dataset.with_fees` made public (it was `_with_fees`; one caller and one test).

**Verified (local).** 20 tests in `test_weather_explore.py`, on one hand-worked Central Park day (six markets, twelve trades; prices, fees, profits, timings and the gap computed by hand). 12 planted bugs: 11 were caught on the first run. Rain counted on only one kind of day survived, so the timeline test gained rain on the clock's day only, and all 12 are caught. The weather package is 100% covered; the full suite has 1,862 passed and 196 skipped; ruff and mypy are clean.

**Verified (analytics node, real data; 3 s a part; at `ff195cc`, two runs of each part give byte-identical output).**

| | fit (53 days) | validate (12 days) |
|---|---|---|
| Settled markets in scope | 8,397 | 2,088 |
| Called (highs / rain) | 2,240 (1,972 / 268) | 532 (461 / 71) |
| Wrong calls | 0 | 0 |
| Trades after the call | 41,731 | 9,653 |
| Ours: trades, markets, city-days | 1,572, 19, 19 | 1, 1, 1 |
| Upper bound: profit, a day | $2,173.60, $41.01 | $0.00 |
| Upper bound: cents a contract (lower 2.5%) | 2.03 (0.72) | 0 |
| Lower bound (first worth taking per market): trades, profit, a day | 14, $34.23, $0.65 | 0, $0.00 |
| Ruled-out side after the call: trades, contracts | 40,159, 8.8 M | 9,652, 2.1 M |
| of which at 99 cents for the certain side | 38,381, 8.7 M | 9,652, 2.1 M |

- *Where ours were.* 16 of the 19 markets are rain. The three highs rank 13th, 15th and 18th by profit; the five most profitable markets are rain (Atlanta 5 Sep, Houston 17 Sep, San Francisco 15 Aug, Boston 21 Sep, Washington 27 Aug).
- *The book is ahead of the reports.* In the called markets, the certain side last traded below 95 cents a median **276 minutes before the call** for the highs (10% of markets under 70) and **41 minutes before** for rain (10% under 10). The validate part gives 325 and 40. Traders price the bound from forecasts, radar and the trend, hours before an hourly report proves it.
- *The ruled-out side.* 38,381 of the fit part's 40,159 trades were at 99 cents for the certain side: holders sold a certain win at 99 cents (or a taker bought the dead side at 1 cent), and the maker bought it.
- *The gap.* The settled high was above the highest hourly report on 679 of 1,086 city-days in the fit part (440 by 1 °F, 202 by 2, 32 by 3, 5 by 4 or more) and 137 of 253 in validate. On 426 of the 679 the six-hour maximum later reached it (12 of them by the hourly peak), a median of 4 hours after the hourly peak (p90 6); on 253 it never did.

**Review (PLAN Q8).** `3e45c8b` (resume point; docs only, no code). `a28ec77` reviewed at low effort: no findings. The merge review, at medium effort over the branch against `main`, found 10 issues:
1. The lead before the call and the 99-cent count, cited for W9, came from a one-off script, not from `explore`. **Fixed:** `explore` measures both (`lead`, `ruled_out_at_99_*`) and reproduces the script's figures exactly; tests added.
2. The validate run opens the trade files of 2026-10-11 and 10-12, inside the test part, while the write-up said the test part was not read. A market trades until about 01:00 local after its day, so those files hold the validate part's last trades. **Fixed:** only the called markets' rows are read from any file; the write-up and the module say exactly what is opened. No test-part market's trade or result is read.
3. The gap's hours could be negative when the six-hour maximum showed the settled high before the hourly peak. **Fixed:** the lag is 0 then, counted as "reached by the peak" (12 on the fit part); test added. The median and p90 did not change.
4. The ruled-out counts dropped trades whose fee was unknown. None was in the real data. **Fixed:** they count every trade; test added.
5. A trade with no taker side was counted nowhere. None was in the real data. **Fixed:** counted apart; test added.
6. The lower bound took the first trade of ours even when it could not pay (at a dollar). **Fixed:** it takes the first trade worth taking when it came; the fit part's lower bound moves from $0.23 to $0.65 a day; test added.
7. The write-up skipped the benchmark ladder that CLAUDE.md asks of every study. **Fixed:** it reports the raw price and the naive maker, and says why the bias correction has nothing to correct.
8. The local hour converts each trade once per time zone. **Not changed:** about 42,000 trades and six zones take well under a second; the run takes 3 s.
9. `settled_low` encoded the strike rules away from `threshold`. **Fixed:** moved next to it in `check.py`.
10. `_quantiles` turned a missing quantile into 0. **Fixed.**

The behaviour fixes (1, 3, 4, 6, and the lead's "before the call") were undone one at a time: the tests caught all five. Rerunning the real data also showed the results were not repeatable: the bootstrap's clusters came in no fixed order, and trades of one market in one millisecond (common) had no fixed order, so "the first trade" changed between runs (the lower bound's point moved from 21.7 to 6.1 cents a contract). **Fixed:** the clusters are sorted and the trade id breaks ties (which of such trades came first is not recorded). Weather tests: 97 passed, the package 100% covered; the full suite 1,865 passed and 196 skipped; ruff and mypy clean.

The fix commits (`f5f4219`, `3df6de3`, `ff195cc`) were reviewed together at low effort: one finding, that `lead` would count markets never called. **No change needed:** `priced_trades` keeps only called markets (the lead test's uncalled T85, with a trade at 50 cents, is not counted).

**Decisions.**
- **W9: no rules (owner, 2026-10-28, as recommended).** No rule could reach W7's 100 signalled city-days or its $25 floor, so S2.3 and S2.4 are not run and the test part stays unopened (its first two days' trade files were opened only for the validate markets' last trades). `docs/research/weather-rules.json` holds the empty list; the write-up is `docs/research/weather-explore.md`.
- **Parked in PLAN §9** (named as scope creep): resting bids at 99 cents for an already-certain side. All 25 series in scope are plain `quadratic`, with no maker fee and no recorded fee change (read on the node, 2026-10-28). The 5-minute readings entry gains S2.2's gap figures.

### S2.c Close-out (2026-10-28 UTC, done)

**Built (documents only).**
- PLAN §6.2: an S2.c row and S2's verdict. The S2 row in §6, the status line and the §9 entry say S2 is closed.
- LESSONS.md's "What SPARQ measured" gains S2.2's lesson. ASSESSMENT §7.2 ("Checked and closed") gains a line for taking stale weather quotes.
- The CLAUDE.md status and resume point.

**Gate evidence.**

| Slice | Outcome | PR |
|---|---|---|
| Proposal (W1 to W8) | approved 2026-10-27 | #9 |
| S2.1 sources, terms and the observation history | **done:** the map covers every market in scope; 0 wrong calls of 2,243 on 8,397 fit-part markets; the delay measured and W4's history delay set to 10 minutes | #10 |
| S2.h core hardening from IKARS's review | **done:** no result changed; every guard refuses in a test | #11, #14 |
| The mini code review rule (Q8), S2.2's plan | approved | #15 |
| S2.2 exploration on the fit and validate parts | **done, no rules** (W9): $0.65 a day at the lower bound on the fit part and $0 on validate; the book leads the reports by a median 4.6 hours (highs) and 41 minutes (rain) on the fit part, 5.4 hours and 40 minutes on validate | #16 |
| S2.3 the pre-registered test | **not run** (W9); the test part stays unopened | |
| S2.4 the live shadow run | **not run** (W9) | |

No order was sent in S2, and the trading key was never needed. Every code slice passed format, lint, types, tests and the coverage gate in CI, and the weather package is 100% covered.

**What stays.** On `main`: the tested `sparq_analytics.weather` package (`stations`, `download`, `check`, `delay`, `delay-report`, `explore`). On the analytics node:
- the station reports (39 stations, 2026-08-01 to 2026-10-28);
- `check.json` and the delay run's files;
- the `explore` outputs of both parts.

The owner chose to keep them (2026-10-28).

**Verified.** Documents only, so no test was run for this slice; CI runs on the PR. On the analytics node, the check clone was moved from `s2/2-explore` back to `main` with nothing running from it.

**Review (PLAN Q8).** Each figure was checked against BUILD-LOG S2.1 and S2.2. The merge review, at medium effort over the branch against `main`, found 9 documentation issues and no runtime bugs, all fixed:
1. This section pointed to a list of findings that was not there. **Fixed:** this list.
2. to 4. The rain lead of 40 minutes (the validate figure) was paired with the fit part's 4.6 hours, in the new lesson, this entry's gate table and ASSESSMENT §7.2. W9, the rules file and the resume point had the same mix. **Fixed:** the fit part's 41 minutes, with the part named, everywhere.
5. The resume point and §9 gave 8.8 M contracts for the 99-cent bids. That is every ruled-out contract; the 99-cent subset is 8.7 M. **Fixed** in the resume point, §9 and the write-up.
6. The resume point did not name S2.c's PR. **Fixed.**
7. The lesson said the reports "settle" a market. They make its outcome certain, and the climate value settles it. **Fixed.**
8. The lesson called every post-call trade a holder selling at 99 cents. 38,381 of 40,159 were at 99 cents, and some may be takers buying the dead side. **Fixed.**
9. The verdict sat before §6.2's planning paragraphs, which read as if S2 were still to be built. **Fixed:** the verdict closes §6.2, and the planning paragraphs are marked as the plan of 2026-10-27.

**Problems and fixes.** Only the review's findings above. They also corrected W9 and the S2.2 write-up, both already merged.

**Decisions (owner, 2026-10-28, as recommended).**
- Keep the weather data on the analytics node.
- S1's close-out is its own slice (S1.c, merged first).
- A close-out records and does not propose the next direction.

## S3. Three measurements of where the money is (approved 2026-10-28)

### S3 proposal (PR #19, 2026-10-28 UTC)

**Built (documents only).** PLAN §6.3 (M1 to M7, S3.1 to S3.c), the S3 row in §6, the status line, the model node parked in §9, and the CLAUDE.md status and resume point.

**Review (PLAN Q8).** The merge review, at medium effort over the branch against `main`, found 8 documentation issues and no runtime bugs, all fixed:
1. S3.1's "bid that knew an hour earlier" would have read weather outcomes on live-frame days inside the held-out part. **Fixed:** the implied dollars combine structural live-frame measures (no outcome) with the history's payout rate to the cut; the knowledge case uses S2.2's call times on the fit and validate parts only.
2. The new module was named `scan`, which `study.scan` (S1.1b's grid scan) already uses. **Fixed:** `sparq_analytics.survey`, commands `survey dump|lag|tail`.
3. Fees were said to come from `study.dataset`; the expressions live in `study.feecalc`. **Fixed.**
4. The §9 99-cent entry said only recorded order books show the queue, while S3.1 reads it from ticker frames. **Fixed:** the best-bid size is named as the proxy, with what it misses.
5. M3's cut was loose about trades near the boundary. **Fixed:** the cut is on the market's close time, so every trade read precedes 2026-10-11.
6. M4's S3.1 bar did not say whether $25 a day is summed over categories. **Fixed:** within one category; the total is reported only.
7. The model node entry stated Strata's capabilities as fact. **Fixed:** marked as the project's own description, unchecked until the machine is online.
8. The CLAUDE.md status line called S3 active with S3.1 next while the proposal was unmerged. **Fixed:** approved, PR unmerged, S3.1 starts after the merge and its slice plan.

**Problems and fixes.** Only the review's findings above.

### S3.1 The certainty dump and the 99-cent queue (2026-10-28 UTC, done; the owner chooses what follows)

**Built.** `python -m sparq_analytics.survey dump --kalshi DIR --live DIR --out DIR [--cut DAY] [--only history|live]` (`survey/dump.py`, `queue.py`, `implied.py`, `cli.py`).
- *History* (markets closing on or before the cut, M3; `--cut` after 2026-10-10 is refused): every trade at 97 cents or more for either side, priced on that side, with who took it; each side's 99 regime (the first 99 after which it never printed below 97), true or false; the dump into resting 99-cent bids; the any-time reversals; per category the two rates (by market), the dumps per reversal and per false regime, the hours to the end and to settlement; the winning side's trades by level; the series with the biggest dump.
- *Live* (the ingest's frames, any day, no outcome): the hour files reduced to the frames in a regime, those right after one and each market's first and last of the hour, which finds every onset and end exactly; episodes with the size shown at 99 at the onset and an hour before (from the last frame in the two hours before, else unknown); the dump during each; first-in-first-out fills for bids of 100 and 1,000 contracts; the fill a reversal would have given behind the same queue, from the history's dumps; the maker fee where the series charges one.
- *The combination:* two bidders, reactive (at the onset, judged on the any-time reversals) and knowing (an hour before, judged on the false regimes); gains on the fills in the markets that paid, losses on the reversal's fills in the markets that reversed; the capital tied and the collateral held; M4's bar per category.
- `--only` runs one reading; a live-only reading reports no loss.

**Verified (local).** 19 tests on a hand-built world (six markets and eighteen trades in the history, five markets with frames and trades over four live hours, with an empty hour before), every answer worked out by hand: the cut and the early settlement, the two sides' last print below 97, who took, the regime's start, the false regime and the any-time reversal, the episodes and their censoring, the queue an hour before (shown, none, or unknown), the dump inside an episode, the capped fills, the maker fee at 99 cents, the expected fill of a reversal, the scaling to a day, and the combination's dollars, capital and collateral. 19 planted bugs, all caught (one first survived because the frame it concerned happened to be a market's last of the hour, so the world gained a later frame). The survey package is 100% covered; the full suite 1,884 passed, 196 skipped; ruff and mypy clean.

**Verified (analytics node, real data).** `live extract` from 2026-10-18: the ingest's tables hold nothing before 2026-10-24 21:00 UTC, so the live span is 80 hours (386 hour files, 75.5 M frames, 15.4 M trades, 3.0 GB; 7 ticker gaps logged). `survey dump` runs in about ten minutes; two runs give the same figures. Results in `docs/research/certainty-dump.md`:

| | |
|---|---|
| History | 17,538,187 markets; 75.9 M trades at 97 cents or more before the market's end; 935,895 regimes, 718 false; 2,775 any-time reversals (by side) |
| Live | 638,318 episodes in 80 hours; 9,897 left-censored and 14,045 overlapping a logged ticker gap take no part |
| Reactive bidder, 100 contracts, a day | Sports −$12,809; Crypto −$12,830; nine more categories negative; **Mentions +$39** (+$313 at 1,000) |
| Knowing bidder, 100 contracts, a day | Sports +$7,166; a ceiling nothing reaches |
| The whole 99-cent queue | about +$240,000 a day, 85% in crypto 15-minute markets; Sports +$5,904 with 97% of the gross taken back by reversals |

**Against M4's bar:** Mentions clears it, on 28 reversals in 65 days and 3.3 days of frames; at the one-sided 0.025 bound of the reversal count alone the net is about $25. The figures are the run at `ea133c5`; the two later commits change tests only. Every other category is below it. Per M1 the owner chooses: go on to S3.2 (the default) or hear a strand proposal.

**Review (PLAN Q8).** Commit reviews at low effort: `16de8e1` found that a category with only false regimes got a capital of 0 instead of none (**fixed**, `a539d7d`); `a539d7d` none; `4a75e63` found a 0/0 that became NaN in the JSON and a theoretical float path for the episodes a day (**fixed**, `3ee249e`: null, and a guard on an empty span); `3ee249e` none; `2a69a94` found that a category the history never saw read as "never reversed" (**fixed**, `4d22c1b`: unknown). The model itself changed twice on the real data, each time because the first output could not be right:
1. The first run applied the payout rate per filled contract, so Sports showed +$1,980 a day. A reversal's panic is not rationed as the gains are: the loss falls on the bid's size, and the reactive bidder's risk is every 99 print that lost, not the hindsight regimes. **Fixed** (`4a75e63`): Sports −$24,480.
2. That charged the whole bid in every reversal, which overstates where the queue is deep (Crypto's median queue is 14,000 contracts). **Fixed** (`2a69a94`): the loss is the fill a reversal's dump would have given behind the same queue, paired across the history's reversal dumps and the live queues, symmetric with the gains: Sports −$12,741.
The merge review, at medium effort over the branch against `main`, found 10 issues:
1. The dump rate per regime-hour divided by zero where a category's closed episodes all lasted under a second, writing infinity into the JSON. **Fixed** (`ea133c5`): null.
2. Trades after a market's end (an early settlement) could start or void a regime. **Fixed:** nothing after the end counts, in both passes; test added (B's trade after its settlement). 3,771 regimes and 0.2 M trades left the counts.
3. The fee terms are each series' current ones, not the terms in force at each trade, and an unknown fee type read as free. **Partly fixed:** the live span is four days old, so the current terms are the terms in force there, and `with_fees` is not reused; a type outside the quadratic family now leaves the fee unknown and is counted (`fee_unknown_episodes`; 18 of 638,318 in the run, in series the history's table lacks). Test added.
4. The reactive rate was said to be "by market" while a market whose both sides printed 99 counts once on each. **No change to the count, wording fixed:** the unit is the side, which is what the bidder bids on; the live episodes are per side too.
5. The ingest's logged ticker gaps were ignored, so a regime spanning an outage read as one episode with backfilled trades. **Fixed:** episodes overlapping a gap take no part (14,045 in the run); unit test of the overlap, and a gap in the test world.
6. Each ticker hour file was read twice for the hour-before lookup, and the trades scanned twice. **Half fixed:** a one-slot cache reads each hour once; the two trade passes stay (the run takes ten minutes).
7. The makers' gain a day divided by calendar days while missing trade files were skipped silently. **Fixed:** by the days of trades read (65 of 65 here), reported as `trade_days`.
8. The resume point gave two branches for the node's check clone. **Fixed.**
9. Two locals named `hours` shadowed the helper, and a dead `.get` default. **Fixed.**
10. The `hours` helper's docstring claimed more exactness than it has. **Fixed:** it says what it does (whole hours exactly, fractions to a second).

The code fixes were planted one at a time: the tests caught all 21 plants (two added for the end bound and the gap). After the fixes the real figures moved a little (Sports −$12,741 to −$12,809; Mentions +$45 to +$39).

**Problems and fixes.**
- The ingest's tables hold no frame before 2026-10-24 21:00, so the plan's "about ten days" of frames became 3.3; the figures a day scale with the span, the thin side of the Mentions lead.
- polars divides an integer column by a literal through the reciprocal (2 hours read as 1.9999999999999998); the hours are whole seconds over 3,600.
- A lazy join does not keep row order: one test read values unsorted and failed once in three runs; sorted.
- The "unknown" piece of the hour-before lookup kept a column the others lacked and broke the concatenation, found by the test that first reached it.
- Any episode still open when a ticker gap begins overlaps it, so the test world's gap sits where it excludes one episode that nothing else excludes; and a `when/then/otherwise` names its result after the `then` branch, so a test's replaced column was added beside the original until aliased.
- A `tail` at the end of a `&&` chain hid a failing exit code and let one commit (`ea133c5`) in with a failing test (test-only; fixed in the next commit); chains are gated with `pipefail` since.

**Decisions.** None taken in the slice; the owner's choice under M1 is open.

### S3.2 Where the book is slow (2026-10-28 UTC, done; the owner chooses what follows)

**Built.** `python -m sparq_analytics.survey lag --kalshi DIR --live DIR --out DIR [--hours N]` (`survey/relations.py`, `lag.py`, `cli.py`); `live extract` copies four more market columns (`strike_type`, `floor_strike`, `cap_strike`, and the rules text of weather markets flattened to one line, because the station a weather market reports is named nowhere else).
- *Relation groups* from Kalshi's own fields, each pair ordered (`a` moves, `b` is watched) with its expected sign and the window in which both are open: adjacent thresholds of one subject in one event (the subject is the ticker without its trailing line; same sign, opposite for a greater against a less); the two or three outcomes of one structured event (opposite); the same team or total across a sports game's series at the central line (same); thresholds of one underlying across horizons, the partner being the nearest level in the soonest-closing open event of the other series (same; the stem is the series ticker without `15M` or a final D, H, U or E when another series of the category shares it); adjacent brackets of one event and weather series of one station and day (no sign). The coverage is written out: pairs, markets and groups by kind, the horizon stems, the active markets no kind relates.
- *The event study*, hour by hour on the live frames with the next hour loaded for the horizons and each market's last frame carried across: a move is a mid change of 2 cents or more between two-sided frames with bid and ask not moving against each other; for each pair valid at the move, b's quote strictly before (unknown, one-sided or extreme quotes, at 3 cents or less or 97 or more, take no further part), its response at 1 s, 10 s, 1 min, 10 min and 1 h in the expected direction (null past b's close, past the copied frames, or where an outage overlaps the hour), its first mid change within an hour and whether it agreed, the trades in b while the quote stood by the taker's side (with or against the expectation), and for a signed kind the stale quote's capacity: the shown size on the side a taker would hit, times the 10-minute response, less the taker fee from the series' terms; each stale quote counted once (same b, side and first move). Events whose first ten minutes overlap a logged outage are excluded.
- *The summary* by kind, by kind and b's activity tier (contracts a day in the span), by kind and category, by kind and the size of a's move, the twenty series pairs with the largest reachable capacity, M4's bar, and cluster-bootstrap intervals (the 10-minute response over group-hour clusters; the capacity a day over the span's hours). The trades through a stale quote are counted once a quote, and the capacity is read twice: for every stale quote, as M4 words it, and for the **reachable** ones that stood at least a second before b moved.

**Verified (local).** 22 tests on a hand-built two-hour world of 26 markets, 17 moves and 26 event rows (`laghelpers.py`), every figure worked out by hand in its docstring: the six kinds' pairs, signs and windows (the horizon windows switching at the 15-minute event's close), the stems, a station-day group; the moves (a one-cent change, an opposed widening and a first frame are not moves; a move across the hour boundary needs the carried frame); the quote strictly before a move (a frame in the same millisecond is after it); the responses at each horizon with their null cases (b's close, the span's end, the gap); the first move and its agreement; the trades through the stale quote on either side; the capacity, the fee and an unknown fee type; the tiers, the gaps and the dedup of a stale quote seen from two moves in two hours; the figures a day, by tier, by category and the slow spots; the CLI's files and `--hours`. 21 planted bugs, all caught. The survey package and the live source are at 100% coverage; the full suite 1,907 passed, 196 skipped; ruff and mypy clean.

**Verified (analytics node, real data).** `live extract` added the hours from 2026-10-28 05:00 to 2026-10-29 00:00 (38 hour files, 33.2 M frames, 6.7 M trades; the ingest's gap log holds 22 outages, the longest 17:36 to 21:27 UTC on 2026-10-28), so the span is 2026-10-24 21:00 to 2026-10-29 00:00 UTC, 99 hours, about 109 M frames and 22 M trades. `survey lag` ran in about four minutes at `0dc9c9b`, `090c4a5`, `de421fc` and `5268e7e` (the figures below are the last of those; the earlier ones differ only by the summary fixes listed under Review and by the three hours the span gained). **These are the first measurement's figures; S3.3 corrected the frame reader and the write-up holds the corrected ones (see the end of this entry).** Results in `docs/research/book-lag.md`:

| | |
|---|---|
| Groups | 541,994 markets with frames, 443,132 in a pair: 529,540 threshold pairs, 44,032 outcome, 425,210 game, 1,275,314 horizon (34 stems), 234,820 bracket, 13,966 weather |
| Events | 21.7 M moves, 18.9 M with a partner; 74.1 M event rows, 63.8 M usable (7.8 M with an extreme quote, 1.7 M in an outage, 0.8 M with no quote seen; 0.4 M usable with an unknown lag) |
| Speed | moved within 1 s: thresholds 34%, outcomes 34%, game 21%, horizons 35%; within 10 min: 85 to 88% (weather 52%); the first move agreed with the expected direction 51 to 59% of the time |
| Response at 10 min | thresholds 0.60 cents (0.58 to 0.62), outcomes 0.33, game 0.16, horizons 0.30; brackets 2.41 and weather 0.53 in the mover's direction |
| Capacity a day, every stale quote | thresholds −$40.8 M, game −$32.1 M, horizons −$1.71 M, outcomes −$1.46 M (fees of $1.9 M to $53 M a day a kind) |
| Capacity a day, quotes that stood a second | thresholds −$40.7 M, game −$28.3 M, outcomes −$1.75 M, horizons −$1.28 M; the gross before fees is negative for thresholds, horizons and game |
| Anyone taking them? | the stale quote was hit on the expected side in 1.9% (thresholds) to 17% (horizons) of events, and on the other side as often |

**Against M4's bar:** no relation type clears it, in aggregate, by tier, by category or by the size of the mover's change; the one positive cell under the every-quote reading (Elections thresholds, +$59,258 a day) is −$45,525 for the quotes that stood a second. The slow-spots ranking's top (the MLB spread against the MLB moneyline, +$115,995 a day on 163 reachable quotes a day over four days) is a lead for a pre-registered test at most. Per M4 the direction is parked in PLAN §9 with its numbers; per M1 the owner chooses: go on to S3.3 (the default) or hear a strand proposal.

**Review (PLAN Q8).** `0b153c7` (resume point; docs only). `0dc9c9b` reviewed at low effort: two findings, fixed in the same commit: the horizon filter was stated twice (once for the pairs, once for the coverage's stems) and could drift (one helper now); the capacity interval's hour index assumed an hour-aligned span (bounded now). `6e7af4a` (docs and a coverage note). `090c4a5` reviewed at low effort: one remark, that the move buckets should use the absolute move (**no change**: the column is the absolute move already, set so in `moves`). The merge review, at medium effort over the branch against `main`, found 7 issues, all fixed in `de421fc` (reviewed with `5268e7e` at low effort: no findings):
1. `moves` took the mid against the immediately previous frame, so a move whose previous frame was one-sided was lost, against the docstring. **Fixed:** the differences are between two-sided frames; test added (a one-sided frame in between).
2. The stale-quote spell key merged every censored event of one market and side across the span into one counted quote. **Fixed:** a quote that never answered within the hour is counted once an hour it stood (`5268e7e`; keying it by its frame, the first fix, split a standing price at every size refresh and took the game kind's fees from $19 M to $125 M a day on the real frames); the test world gained a re-quoted size and a second move.
3. In the span's last hour the next hour's trades were read while its frames were not, and a first move past the copied frames read as "never moved", so the quote counted as reachable and traded through. **Fixed:** the lag is unknown when no move was seen and the hour ran past the copied frames or through an outage (`lag_known`; 377,578 usable events in the run); such quotes are not reachable, and only the loaded hours' trades are read; tests on the one-hour run.
4. The quotes counted and the capacity sums disagreed where the 10-minute response or the fee was unknown. **Fixed:** a counted quote needs both known; fee-unknown quotes are reported apart (8,184 a day in thresholds).
5. Block trades counted as takers hitting the stale quote. **Fixed:** left out; a block trade in the test world.
6. Each hour's frames were read and decorated twice (as the following hour, then as the current one). **Fixed:** the following hour's frames become the next hour's current ones. `active_tickers` keeps its own one-column scan (about twenty seconds).
7. The hour file name's parser was written in three places. **Fixed:** `hour_of` beside `hour_name` in `live/source.py`, used by all three.
Also from the review's second pass: series pairs with no reachable quote leave the slow-spots ranking.

**Problems and fixes.**
- The first smoke run on the test world had every response null: `moves` took the frame differences after filtering to the moves, so the shift saw the previous move, not the previous frame. The differences are taken first; the test world's responses caught it on the first run.
- A censored stale quote (no first move within the hour) was never counted for capacity: the dedup join did not match null keys (`nulls_equal`).
- Two hand figures in the tests were wrong, not the code: the 1-hour mean ignores the event whose hour met the gap; the second slow spot is the game pair, not the thresholds pair.
- The Git Bash heredoc halves backslashes even with a quoted delimiter; files with backslashes are written with the editor tool.
- The first real run summed the trades through a stale quote over every move that left it stale, so one trade counted from each overlapping event (billions of contracts a day); they are counted once a stale quote now, for every kind. The same run's slow spots were led by the two outcomes of one match whose quotes moved in the same second as each other, which no taker could have hit; the capacity is read a second time for the quotes that stood at least a second, and the spots rank by it.
- A `&&` chain in the shell is one line: a second line runs regardless, which restarted a node run at the old commit once (stopped and restarted at the right one).
- **The server host was unreachable from both the node and the workstation (no ping, no ssh, no PostgreSQL) from before 20:36 UTC to about 21:30 UTC on 2026-10-28** (a network outage, the owner says): the first run's `live extract` failed with a connection timeout, and KAPS's ingest recorded nothing it could not reach (lost frames; the gap is the ingest's to log). A fallback on the frames already copied was checked and dropped: only 2.4% of the span's 466,019 active markets have strike rows in the history download (it paused on 2026-10-24, and most markets are created daily).

**Decisions.** None taken in the slice; per M1 the owner chooses after S3.2: go on to S3.3 (the default) or hear a strand proposal.

**Corrected (S3.3, 2026-10-29; commit `5af8248`, rerun at `d8a2d28`).** The feed shows an empty side of the book as a 0-cent bid or a 100-cent ask with no size, never as a null, and this slice's frame reader took such frames as two-sided quotes with a mid (9% of frames carry an empty bid, 3% an empty ask, mostly in thin ladders). With an empty side read as missing and the span grown to 100 hours: 19.1 M moves, 59.7 M usable events (6.6 M rows now one-sided and left out); the 10-minute pass-through is thresholds 0.46 cents (was 0.60), outcomes 0.31, game 0.15, horizons 0.59 (was 0.30; agreement 65%, was 59%); the brackets' 2.4 cents in the mover's direction becomes −0.10 (neighbours sharing probability mass); the capacity is thresholds −$31.1 M a day (reachable −$32.5 M), game −$31.9 M (−$28.1 M), outcomes −$1.54 M (−$1.78 M), horizons −$1.29 M (−$0.99 M); nothing clears, the conclusions stand. The write-up and the PLAN carry the corrected figures; the table above is the first run's.

### S3.3 The thin tail and the surprise rate (2026-10-29 UTC, done; S3.c next)

**Built.** `python -m sparq_analytics.survey tail --kalshi DIR --live DIR --out DIR [--cut DAY]` (`survey/tail.py`, `cli.py`), writing `tail-volume.json`, `tail-live.json`, `tail-surprise.json` and `tail.json`.
- *Volume* (the history's trade files, any day): each traded market's contracts and dollars a day over the days it was open inside the window (rounded up to whole days, at least one), placed in S3.2's four tiers; by tier and by tier and category the markets, trades, contracts and dollars a day; the markets open in the window with no trade, by category.
- *Spreads and the half-spread* (the live frames and trades): each market's time-weighted mean quoted spread while two-sided and the share of time a side was missing, with each hour's frames preceded by the market's carried last frame so the durations are exact; by tier (the live span's own contracts a day, S3.2's `tiers_of`) and category, the median of the markets' mean spreads and the mean two-sided share. For every live trade (block trades left out), half the quoted spread just before it times its contracts, by tier, category and the competition at that moment: a spread of 20 cents or less (**contested**), wider (**uncontested**) or a side missing (**one-sided**, no half-spread to read); gross and net of the maker fee where the series charges one (S3.1's expression). The bar: $250 a day of half-spread in the thin tiers' uncontested trades.
- *The surprise rate* (the history to the cut, M3): each cut market's last trade from 36 to 12 hours before its close (its day-before price); where a side then stood at 90 cents or more, whether the market settled against it. The rate in total, by price bucket (90 to 95, 95 to 99, 99 and more), by category and by series with 50 or more such markets, each with a cluster-bootstrap interval over events (2.5% each side) against the price-implied rate, the mean of one minus the price; a cell clears when its lower bound exceeds the implied rate. The clearing series are listed with their title and the rules text of one of their markets, beside the number of tested series that would clear by chance (2.5% of them).

**Verified (local).** 12 tests on a hand-built world (`tailhelpers.py`: 70 settled markets with trades over three days, among them a series of 60 markets that all settled against a 95-cent side; three live markets over two hours with narrow, wide and one-sided books), every figure worked out by hand in its docstring: the window's days and the life of each market, the tiers, the dollars, the silent market and the one closed before the window; the time-weighted spread and the two-sided share with a carried frame; the half-spread by competition, the maker fee and the thin bar; the day-before price (the last trade in the window, not the ones six or forty hours out), the sides, the buckets and the counts of unpriced and out-of-tail markets; the rates by category, bucket and series, the width-zero interval that clears, a tested series that does not clear, the title and rules of the clearing one, the chance level; the command's files and exit codes. 14 planted bugs, all caught. The module and the command line are at 100% coverage; the full suite 1,917 passed, 196 skipped; ruff and mypy clean.

**Verified (analytics node, real data).** `live extract` added the hours to 2026-10-29 01:00 (the span is 100 hours, about 114 M frames and 23 M trades; the gap log holds 31 outages). `survey tail` runs in about a minute; it ran at `c6c52e8`, `5af8248` and `d8a2d28`, the last being the figures (the first run read empty sides as 100-cent spreads: its thin uncontested half-spread was $967,530 a day and its spreads nonsense; the second lacked the sorted bootstrap, so two borderline series differed). `survey lag` reran on the same code at `d8a2d28` for S3.2's correction. Results in `docs/research/thin-tail.md`:

| | |
|---|---|
| Volume (77 days) | 6,092,206 markets open, 1,595,147 traded, 4,497,059 silent (3.9 M of them Crypto); the two thin tiers hold 956,079 markets and $2.84 M a day of the $385 M a day traded |
| Spreads (100 hours) | thin markets two-sided 21% of the time, median spread 10.8 cents while two-sided; thin Crypto two-sided 0.3%, thin Sports 45% at 16 cents |
| Half-spread, thin tiers | uncontested trades: 38,363 markets, 66,062 trades a day, 1.70 M contracts a day, **$494,893 a day** (Sports $397,886; a tenth $49,489) against the $250 bar: **clears**; contested $132,333; one-sided trades 21,319 a day with nothing to read |
| Surprise (to 2026-10-10) | 111,333 markets at 90 cents or more the day before close; 3.54% (3.37 to 3.72) settled against the side, 3.86% implied: the favourite slightly under-priced; Sports 5.46% against 5.20% **clears**; 12 series of 312 judged clear, 7.8 expected by chance: leads for the held-out test |

**Against M4's bars:** both clear; the half-spread by a factor of about 2,000, the surprise rate as worded, with the reservations in the write-up. Per M7 S3.c weighs the one strand proposal.

**Review (PLAN Q8).** `f6ce1c1` (resume point; docs only). `c6c52e8` reviewed at low effort: no findings. `9a7cac8` (docs). `5af8248` reviewed at low effort: three findings, fixed in the same commit: a market without an open or close time fell through the bucket helper into the highest tier (and `min_horizontal` skips nulls, so its life had to be caught first); a phrase duplicated in the lag docstring; a sum computed twice. `d8a2d28` reviewed at low effort: no findings.

**Problems and fixes.**
- The carried frames came out of a group-by with their columns reordered and would not stack onto the hour's frames; they are selected in the frames' order.
- **The feed shows an empty side as a 0-cent bid or a 100-cent ask with no size, never as a null.** Found when the first run's spreads came out at a median of 97 cents with every frame two-sided: the shared frame reader (`lag._with_mid`) now drops a side of size zero, which corrects S3.2's merged figures too (both studies reran; S3.2's entry above carries the correction).
- The live span's tiers followed S3.2's rule (contracts over the span) while the history's followed the days a market was open; a short-lived market landed in different tiers in the two readings. Both follow the days-open rule now (`tiered`).
- A one-event series cleared the surprise bar on an interval of width zero; a cell is judged only with ten events or more (`MIN_EVENTS`), and the unjudged series are counted apart.
- A group-by returns its groups in no fixed order, so the bootstrap's clusters changed order between runs and two borderline series cleared in one run and not the next; the clusters are sorted first (S3.2's intervals too).
- `min_horizontal` and `max_horizontal` skip nulls: a market without an open or close time got a two-day life and the top tier; the missing time is caught first.

**Decisions.** None taken in the slice; S3.c weighs the strand proposal (M7).
