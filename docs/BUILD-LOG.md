# BUILD-LOG

How each SPARQ slice was built and verified. One section per milestone, one subsection per slice, with the headings **Built**, **Verified** (local, on the server, live) and **Problems and fixes**, and any decisions taken. KAPS's build log (the predecessor's, up to its freeze on 2026-10-25) is at [github.com/swalsh76/KAPS/blob/main/docs/BUILD-LOG.md](https://github.com/swalsh76/KAPS/blob/main/docs/BUILD-LOG.md).

## S0. The fork (2026-10-25)

### S0 The repository, the renamed packages and the founding documents (in progress)

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

**Still to do in S0.** The owner's merge.
