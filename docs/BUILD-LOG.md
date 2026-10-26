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

**Still to do in S1.1b.** After the window closes on 2026-11-23 and its markets settle (allow a few days): re-run KAPS's downloader for the forward days (trades, then markets; owner approved, D10), then `s11b_run.sh verdict` once, and the write-up.

### S1.2a What the live book shows (2026-10-25 to 2026-10-27, in progress; the reading is on 2026-10-25 and 2026-10-26)

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

**Still to do in S1.2a.** After 2026-10-27 01:10 UTC: `extract`, `measure --start 2026-10-25 --end 2026-10-27` with `docs/research/forward-rule.json` and the frozen activity file, check the activity hash, `report`, add the reading here, and close the slice (the PLAN row, the merge of PR #5).

