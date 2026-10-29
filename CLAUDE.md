# CLAUDE.md

## Start here

**Read [docs/PLAN.md](docs/PLAN.md) before doing anything.** It is the source of truth for the direction, the architecture and the build order. Record how each slice was built and verified in [docs/BUILD-LOG.md](docs/BUILD-LOG.md), and update both when decisions change. The reasoning and sources are in [docs/ASSESSMENT.md](docs/ASSESSMENT.md); what the predecessors and SPARQ itself measured is in [docs/LESSONS.md](docs/LESSONS.md).

SPARQ (Systematic Prediction-market Automated Risk-managed Quoting) is written in Python. It is an **automated maker** for prediction markets: it quotes at a **seconds** scale in **second-tier** markets around a fair value taken from outside or related markets, never leaves a stale quote, and takes other traders' stale quotes when they are clearly wrong. It does **not** race professional firms in milliseconds: headline markets and news races are out of scope.

- **KAPS**, the direct predecessor, is **frozen** (owner, 2026-10-25). SPARQ started from its code as a fresh repository (no git history). KAPS lives at `github.com/swalsh76/KAPS` with a local clone at `%USERPROFILE%\dev\KAPS`; read it, but change it only in its own sessions and only for maintenance. **Its ingest, watchdog and backups keep running on the server host under the `kaps` names**, and its history download (paused) is on the analytics node: SPARQ reads that data until it deploys its own.
- **KalshiTerm2**, the owner's earlier Python project, is a **guide** to question and improve on, not copy: `%USERPROFILE%\dev\KalshiTerm2` (read only). **KalTerm3** (.NET) is retired; its code is at the git tag `kalterm3-final` in the KAPS repository.

## Status

**S0 is done; S1 is closed** (PLAN D14, 2026-10-28: S1.1 NO-GO, S1.1b dropped unopened, S1.2b and S1.2c closed unbuilt, S1.3 and S1.4 not run). **S2, weather against station observations, is closed** (PLAN §6.2: S2.1 done; S2.2 done with no rules, W9; S2.3 and S2.4 not run). **S3, three measurements of where the money is, is closed** (PLAN §6.3, approved 2026-10-28 as PR #19: S3.1 (PR #20) and S3.2 (PR #21) below their bars; S3.3 (PR #22) clears both of its bars; S3.c, the close-out, BUILD-LOG S3.c). **S4, a paper quoter in the thin tail, is approved (PLAN §6.4, 2026-10-29) and is the active strand: S4.1 is done (PR #24) with its four rules registered; S4.2, the registered test, is merged (PR #25) for a run on 2026-11-13, a smoke run over fit-part days already marks the uncontested rules negative at settlement, and **work is paused on the owner's word (2026-10-29)**; the model node's own proposal (M6) is still owed.** **No real order is sent without the owner's go-ahead.**

### Resume point (keep this current: update it at every step that changes it, so a lost session loses nothing)

*Last updated 2026-10-29 UTC (S4.2 merged as PR #25; work paused by the owner; the registered test's run is due 2026-11-13). Dates here are UTC.*

- **Where the work happens:** open Claude Code in this repository's folder on the workstation (`%USERPROFILE%\dev\SPARQ`), not in KAPS's. This project's assistant memory (real host names, the analytics node's paths and account, the owner's preferences) is in the memory folder Claude Code keeps for this folder.
- **Now (2026-10-29 UTC):**
  - **PR #10, S2.1**: merged (`abe14da`). W4's history delay is **10 minutes** after the observation time (owner, 2026-10-28).
  - **PR #15**: merged (`c58b2ab`): the mini code review rule (PLAN Q8, "Workflow" below), the PR checklist, and S2.2's approved plan in its PLAN row.
  - **S2.2, exploration on the fit and validate parts: done, no rules (W9, owner, 2026-10-28)** on branch `s2/2-explore` (BUILD-LOG S2.2, `docs/research/weather-explore.md`). Takers could have taken stale quotes in 19 of 2,240 called fit-part markets ($0.65 a day at the lower bound) and in 1 on validate ($0): the book priced the bound a median 4.6 hours (highs) and 41 minutes (rain) before the call (fit part). S2.3 and S2.4 are not run and **the test part (2026-10-11 to 2026-10-22) stays unopened**. Parked in PLAN §9: resting bids at 99 cents for a certain side (8.7 M contracts on the fit part, no maker fee); the 5-minute readings entry gained S2.2's gap figures.
  - **PR #16, S2.2**: merged (`3ba4cde`).
  - **PR #17, S1.c (S1's close-out)**: merged (`98ece15`). D14 closes S1; S1.2r, the order-book recorder, D7 with D6's trading key, and D8 are parked in PLAN §9.
  - **PR #18, S2.c (S2's close-out)**: merged (`af280ec`). S1 and S2 are closed. The weather data stays on the node (owner).
  - **PR #19, the S3 proposal**: merged (`136c502`; PLAN §6.3, M1 to M7): three measurements in turn from data already held, with bars set before reading.
  - **S3.1, the certainty dump and the 99-cent queue: done, merged as PR #20** (`54178e0`; BUILD-LOG S3.1, `docs/research/certainty-dump.md`). A 100-contract bid joining when the price reaches 99 loses in every category but Mentions (+$39 a day, thin: 28 reversals in 65 days, 3.3 days of frames); Sports and Crypto about −$12,800 a day; the whole 99-cent queue nets about $240,000 a day, 85% to crypto bots. Mentions is a thin lead to re-read once more live days accrue. **The owner chose S3.2 (M1, 2026-10-28, as recommended).**
  - **S3.2, where the book is slow: done, merged as PR #21** (`da170b2`; BUILD-LOG S3.2, `docs/research/book-lag.md`, PLAN S3.2 row and §9). **No relation type clears M4's bar:** related markets answer a 2-cent move within seconds (34% of watched thresholds moved within 1 s, 88% within 10 minutes), the 10-minute pass-through is 0.16 to 0.60 cents, agreement 51 to 59%, and hitting stale quotes loses millions a day to fees; the quotes that stood a second move against the signal. **The owner chose S3.3 (M1, 2026-10-29, as recommended).**
  - **S3.3, the thin tail and the surprise rate: done, merged as PR #22** (`6193cfc`; BUILD-LOG S3.3, `docs/research/thin-tail.md`, PLAN S3.3 row). **Both bars cleared:** thin markets' uncontested half-spread $499,284 a day (Sports $403,571) against $250; 14 series of 313 judged clear the surprise bar (7.8 by chance) and the Sports category barely; the tail overall slightly kind to the underdog. **S3.3 corrected S3.2:** the feed shows an empty side as a 0-cent bid or 100-cent ask with no size, so the shared frame reader now drops a side of size zero; S3.2's write-up, PLAN row and §9 entry carry the corrected figures (conclusions unchanged). The merge review's 10 findings are fixed (`6a6d9c5`).
  - **S3.c, S3's close-out: done, merged as PR #23** (`6191368`; BUILD-LOG S3.c). **S4 approved (owner, 2026-10-29: "merge PR#23 and begin S4"); S4.1 done on branch `s4/1-paper` (BUILD-LOG S4.1; `docs/research/paper-quoter.md`): on paper the 5-cent quote in uncontested thin books nets $12,297 a day at the close mark on 10-contract quotes (lower bound $10,972; $4,385 in markets closing within 48 hours), one-sided books $871; four rules proposed for registration (uncontested 5 cents inside at 10 and 50 contracts, every category; the same at 10 in Sports alone; one-sided at 10; all in markets closing within 7 days) **approved and registered 2026-10-29 (`docs/research/paper-rules.json`, digest `6c7a87357267…`); PR #24 merged on the owner's word; S4.2 built on branch `s4/2-test` (BUILD-LOG S4.2): `paper test` replays the registered rules with settlement marks; **a smoke run over three fit-part days marks the uncontested rules negative at settlement (R1 −$1,588 a day, −3.1 cents a contract) while the mid marks stay positive**; the registered test's window is 2026-10-30 to 2026-11-05, to be run once on 2026-11-13 after a fresh extract (`paper test --from 2026-10-30 --to 2026-11-05`); **merged as PR #25 (owner, 2026-10-29: "merge #25 and pause")**. **Work is paused on the owner's word: nothing starts until the owner says so.** The node: the check clone is on `main`; the live copies reach 2026-10-29 06:00 with `settlements.parquet`; the smoke's outputs are in the data drive's `sparq/paper/smoke-test` folder; the run scripts `s41_run.sh` and `s42_smoke.sh` beside the others**. The S4.1 outputs (`paper.json`, `paper-fills.parquet`) are in the data drive's `sparq/paper` folder.** The whole-codebase review's ten findings are fixed or answered (`937fadc`), the frame sorts are stable (`cf5308f`) and the merge review's six findings are fixed or answered (`7122e6d`); `survey lag` reran twice at `cf5308f` and `survey tail` twice at `7122e6d`, each identical to every reported digit (lag: 658,325 more event rows left as in an outage, no figure moved by more than a point or one percent; tail: the thin bar figure unchanged). PLAN §6.3 carries S3's verdict and §6.4 the S4 proposal, **approved 2026-10-29** with the owner's answers to its three questions (the fit part stays mid-marked, every category is registered, the sizes stand); the model node's own proposal (M6) is still owed. The S3 outputs (`dump.json`, `lag.json`, `tail.json` and the rest) are in the node's survey folder with the run scripts (`s3_rerun.sh`, `s3c_rerun.sh`, `s3c_repro.sh`) beside them in the data drive's `sparq` folder. The model node is online since 2026-10-29 with a Strata endpoint (its address and key are in the assistant's memory, never here).
  - The analytics node's weather folder holds the reports of 39 stations from 2026-08-01 to 2026-10-28, the delay run's files and `explore-fit|validate.json` with their trades files (the check clone's branch is in the S3.1 line above).
- **Standing rules from the owner:**
  - Announce each slice before anything else, and narrate each step (each tool call) in one plain line first.
  - Plan before code; one branch per slice. **Merge a PR only when the owner explicitly approves that PR.**
  - The mini code review (Q8): `/code-review` at low effort on each commit's diff and at medium effort over the branch before each merge; findings under **Review** in the BUILD-LOG; merge reviews reported to the owner as an itemized list.
  - Name scope creep, and park ideas in PLAN §9.
  - Commit as the owner's GitHub no-reply address, ending the message with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` (the assistant model's current name); end PR descriptions with "🤖 Generated with [Claude Code](https://claude.com/claude-code)".
- **Safety limits:**
  - GET only, never an order.
  - The Kalshi key: SPARQ reads only `SPARQ_KALSHI_KEY_ID` and `SPARQ_KALSHI_PRIVATE_KEY_PATH` (User variables), loaded into each command's process and removed afterwards; report only "set" or "not set"; never read, print or copy the key, its id or its path. The analytics node's read-only key: check its presence and mode only.
  - Never read a database password or `.pgpass`.
  - Delete live-test summary files after reading them. The rate-limit ramp is never run.
- **Running things:**
  - On this workstation `uv` is `%APPDATA%\Python\Python314\Scripts\uv.exe` (or `python -m uv`). In Git Bash, clear `VIRTUAL_ENV` first.
  - On the analytics node, `uv` is in `~/.local/bin`. Runs there set `TMPDIR` and `POLARS_OOC_DISK_BUDGET_MB=60000` on the data drive (S1.2r, which would make that the default, is parked in PLAN §9). A long run is detached under `caffeinate`, and its clone is never switched while it runs.
- **S2.h, core hardening from IKARS's review:** done, merged as PR #11 (`fb95046`, 2026-10-27; BUILD-LOG S2.h). SPARQ reads only `SPARQ_KALSHI_ENV`, `SPARQ_LIVE`, `SPARQ_KALSHI_KEY_ID` and `SPARQ_KALSHI_PRIVATE_KEY_PATH`. The signed WebSocket check passed with the owner's `SPARQ_*` variables (2026-10-27); PR #14 (`1586ecc`) makes the live test prove the key without account data.
- **S2's decisions (owner, 2026-10-27):** W1 to W8 in PLAN §6.2; the $25-a-day floor stands for now; the lows wait for the highs. KAPS's ingest keeps recording.
- **S1.2a done: go on (read once 2026-10-27 01:17 UTC on 2026-10-25 and 2026-10-26; BUILD-LOG S1.2a, PLAN S1.2a row).** 11.1% of 43,742 cleared levels in the forward rule's trades refilled within 1 s (stop above 50%); the activity hash matched `edf3189cf82a…`. The main risk for S1.2: an order joining 1 s after a trade fills about a third of the time and its fills lose 0.32 cents by 60 s and 2.91 by 10 minutes, before fees. Results stay in the live data folder's `results` on the analytics node. PR #5 (S1.2a) is merged. S1.2b and S1.2c closed unbuilt (D14); S1.2r, with S1.2a's two leftovers (`ONE_E6` in `live/book.py`, the live command line's command list), is parked in PLAN §9. The read-only key for S1.2 (D6) is on the analytics node (location in the assistant's memory; check presence and mode only). Run analysis on the node with `TMPDIR` and `POLARS_OOC_DISK_BUDGET_MB` on the data drive.

- **S1.1b dropped, unopened (owner, 2026-10-27; PLAN D13):** the registered forward test (digest `0af9ea44be7f…`) is never opened and its forward days are not downloaded. Do not run the S1.1b run script's `verdict` step. D11's guard lapses with it.

- **S1.1 done, NO-GO (PR #3, merged 2026-10-25):** the four approved rules on the test part (digest `95eec43a8d2d…`, opened once 2026-10-25 20:49 UTC): R1 -0.09 cents (lower -2.00), R2 +2.71 (-3.66), R4 +2.51 (-1.30), R5 -1.29 (-3.98). Write-up `docs/research/middle-tier.md`. Under D3 S1 stopped; the rethink led to S1.1b (then dropped, D13) and to S2. The rules ledger on the analytics node locks this test part.

- **S1.2p, shared definitions (PR #6, merged 2026-10-26; BUILD-LOG S1.2p):** done: `sparq_core.markets`, the WebSocket address per environment (demo default), and `sparq_server.names`. Behaviour is unchanged and nothing is deployed.

- **S1.2q, review clean-up (PR #7, merged 2026-10-26; BUILD-LOG S1.2q):** done; no result changes and nothing is deployed; the forward test's digest is pinned by a test. S1.2a's two leftovers (`ONE_E6` in `live/book.py`, the live command line's command list) moved to S1.2r.

- **S0 (done, PR #1):** seeded from KAPS `main` at its merge of PR #42 (`6cd0083`); 202 text files renamed (928 occurrences of `kaps`), six folders moved, the web-design mock-ups and the K0.5 write-up left in KAPS (owner); the server package carried (owner); the lock re-resolved with no version change; locally ruff, mypy (165 files) and pytest (1,608 passed, 197 skipped) are green. The live check (`test_live_public`) passed and CI is green in the new repository (PR #1). Merged by the owner; CI also needed a fix for the runner's new default cluster (BUILD-LOG S0). The memory files with the real host names and the owner's preferences are copied to this project's memory folder.
- **Where the direction came from (KAPS, 2026-10-25):** K0.5's price-only edge study closed NO-GO; the project review and the exploratory middle-tier reading are summarised in ASSESSMENT.md §7 and LESSONS.md. The middle-tier numbers are **leads for a pre-registered test, not results**.
- **Owner decisions so far:** the name SPARQ; public under MIT; a fresh start without history; KAPS frozen with its ingest kept running; the server package carried; the mock-ups left behind. **Not relied on:** Kalshi's Liquidity Incentive Program (limited in time; the owner set it aside).
- **How the owner wants the work run:** announce each slice first; narrate each step in one line; plan before code; explain a question when asked, then record the answer; machine names by role in the repository (the real host names, share path and data paths are in the assistant's memory, never here).

## Non-negotiables

- **Python only.** PostgreSQL 18 + TimescaleDB, installed natively. No Docker in the product.
- **Roles:**
  - **Server host** (Linux, Ubuntu): the database, the ingest, the API and the web front end. It holds only the **data-only** key.
  - **Analytics node** (macOS): production analytics and the **trading service**. The trading key lives there and nowhere else.
  - The Windows 11 workstation is the owner's everyday machine and an optional training offload (its RTX 5070 Ti); it holds no Kalshi key and no database credential. The laptop is out of the rotation: it is used only to administer the servers remotely and for ad hoc work, and nothing in SPARQ depends on it.
- **Platforms.**
  - Everything targets **Linux** (the server host) or **macOS** (the analytics node) only: no Windows builds, no Windows CI, no Windows shims. POSIX features and platform tools (`systemd`, `launchd`) are fine.
  - **Training:** the analytics node is the default trainer; the workstation's RTX 5070 Ti is a selectable offload, manually or on a schedule (KAPS decision 11 (carried; see PLAN.md §2), §5.1). The training code (`sparq-training`, from K3) therefore also runs on the workstation's Windows 11, but depends on nothing Windows-only: `pathlib` paths, no `pywin32`, registry, PowerShell or Credential Manager, CUDA through the cross-platform wheels. It is tested on CPU on Linux and macOS in CI.
  - Models can cross operating systems, so they are saved in portable formats (never pickles) with the same library versions pinned on both machines (KAPS PLAN.md §5.1).
- **Trading safety:**
  - **Demo is the default** in code and config; production is an explicit opt-in.
  - The read client is GET-only, with a guard and a test.
  - The write path is a separate trading client using the separate trading key. It is limited to a fixed list of order routes, and every order passes the risk layer.
  - **Get the user's go-ahead before sending any real order.**
  - **Quoting safety (SPARQ):** every resting quote carries an expiry (`expiration_ts`), so quotes die by themselves if the quoter stalls; maker quotes are post-only; a kill switch cancels every open order; every quote is a genuine order the quoter is willing to have filled (never placed with the intent to cancel before it can execute, which would be spoofing); limits per market, event, category and day are enforced before any order leaves.
- **Network exposure:**
  - **One user, the owner** (PLAN decision 38). No named users, roles, per-user tokens or per-user settings; the web/API is protected by one owner credential. Kalshi's Developer Agreement limits API use to the member's own trading, and a single user keeps SPARQ within that.
  - The web/API port is the only port exposed (to the owner's browser). PostgreSQL accepts connections only from localhost and the analytics node, using `scram-sha-256` and least-privilege roles.
  - Self-signed TLS with trust-on-first-use pinning.
- **Storage:** a governed budget of about **700 GB** on the server host (raised from 500 GB by the owner on 2026-10-25, after the first compression measurement; ASSESSMENT.md "Storage budget").
- **Deployable without the repository.**
  - Each production role installs, upgrades and recovers from its release bundle and the guides in `docs/deploy/` alone (KAPS PLAN.md §6.1).
  - Backups go to the NAS share named in the server's local configuration (`backup.target`); its real path is never written in the repository.
  - Any change that adds or alters a service updates its bundle and guides in the same branch.
  - The install wizard (`sparq-admin setup`) is the only interactive part. It refuses to run without a terminal and only writes an answer file; `install` always runs unattended from that file. A parked role (the standby database) stays listed in the wizard as not yet available until it is built.
- **Money is fixed-point `int`:** dollars at 10^-6 and counts at 10^-2. Never `float` or `Decimal` arithmetic on money. Probabilities and model features may be floats; a stored probability is fixed-point (`prob_e6`).
- **Public repository:**
  - MIT license.
  - Never commit secrets, keys, `.env` files, data extracts (`*.parquet`, `*.duckdb`) or personal data. Never commit real host names, addresses or share names either; use neutral ones in docs and tests.
  - Commit with the GitHub no-reply address.
- **No web UI is in SPARQ's scope** (parked; KAPS's approved design system and mock-ups stay in KAPS). If one is added later: **it must be functional and visually pleasing, never merely utilitarian.** It follows the approved design system and mock-ups (KAPS PLAN.md §6.8). A new screen or a visual change is designed and approved before it is built, and checked by visual regression and accessibility tests.
- **Configuration:** never hard-code hosts, `localhost` or Kalshi URLs outside the core configuration.
- **The owner's name is Steven Walsh.**

## Scope discipline

KalshiTerm 1 died of feature creep and changes of direction. Guard against both:

- **Build only the current milestone** in PLAN.md. Anything else is out of scope until the user says so.
- **Name scope creep.** If a request or idea is not in the plan, **say so explicitly** ("this is scope creep: ...") and ask whether to add it to the plan, defer it or drop it.
- **Plan before code.** Architecture or direction changes go into PLAN.md first, with approval, then into code.
- **Park ideas in PLAN.md §9** (Parking lot), never in code or TODO comments.
- **Keep changes small.** Prefer the smallest thing that satisfies the milestone. Work on a branch; merge when the user approves.
- **Describe before asking.** Describe an action and its effects before asking for approval. When live-testing against market data, repeat the data in the visible reply.

## Workflow: milestones and slices (as in KalTerm3)

- **Milestone first.** A milestone (K0.5, K1a, ...) starts with a proposal of its decisions (numbered D1, D2, ...) and its list of slices. Both go into PLAN.md once the owner approves them.
- **Slices.** The work is done in slices, numbered within the milestone: K1a.1, K1a.2 and so on. A slice that proves too big is split (K1a.4a, K1a.4b), with the owner's approval.
- **Each slice gets its own plan before any code.** It covers:
  - what the slice builds;
  - what it changes on any machine;
  - any new package, named, with its licence;
  - the live checks it needs;
  - the open questions.

  The owner approves it first.
- **Each slice gets its own branch.** For example `k1a/1a.3-ingest`. It is merged only when the owner approves.
- **A slice is done when:**
  - format, lint, types and tests are green, with the coverage gate;
  - live checks have run wherever a fake cannot show real behaviour;
  - its bundle and guides are current;
  - its commit and merge reviews are done;
  - its BUILD-LOG entry is written.
- **Mini code review on every commit and merge (owner, 2026-10-27; PLAN Q8, as IKARS's R14).** Small, regular reviews, so that large whole-codebase reviews are rarely needed.
  - **Before each commit,** review the commit's diff for:
    - correctness and edge cases;
    - money in fixed-point integers;
    - reuse: no duplicate of existing code, and shared code in the shared module;
    - efficiency on hot paths;
    - simplification;
    - tests that cover the change;
    - no secrets, keys, real host names, paths or Kalshi data.
  - **Before each merge,** review the whole branch diff against `main`, plus the modules it touches and their callers, for:
    - consistency across the slice;
    - drift from the PLAN decisions;
    - docs and guides being current.
  - **How:** the `/code-review` skill on the diff (low effort per commit, medium per merge), plus a reuse and simplification pass.
  - **Findings:** fix each in the same branch, or park it in PLAN §9 with its reason. List them under **Review** in the slice's BUILD-LOG entry.
  - **Reporting to the owner:** show a merge review as an itemized list, one finding per line: what was wrong and what was done (fixed, with the commit; parked; or no change, with why). Never only a count.
  - A whole-codebase review still closes each milestone, and should then be short.
- **The BUILD-LOG entry** has one section per milestone and one subsection per slice, with the headings **Built**, **Verified** (local, on the server, live), **Review**, **Problems and fixes**, and any decisions taken. The slice table in PLAN.md marks the slice "done (BUILD-LOG x.y)".
- **Rollouts are asked for separately.** A change that must reach a production machine (the server host or the analytics node) gets its own rollout, asked for at the end of the slice. It is never folded silently into the merge.
- **Close-out.** Every milestone ends with a close-out slice: gate evidence, docs brought up to date, and anything left over parked in PLAN.md §9.

## Announce every new slice

Whenever work begins on a new slice (sub-slices such as K0.5.3b included), say so explicitly **before anything else**: the slice id and name, and the branch about to be created ("Starting slice K0.5.3b: models and endpoint methods, on branch `k0.5/3b-models`"). A slice starts only after the owner has merged the previous one and approved the plan.

## Narrate every step

Even in auto mode, **every operation (each tool call or command) is prefaced with one short, plain line saying what that step is doing**, for example "Running the tests" or "Reading the ingest module". The line comes before the step, every time, so the owner can follow the work as it happens.

## Lessons carried over (details in docs/LESSONS.md)

- **Durability first.**
  - A hard power cut corrupted Postgres running in Docker on macOS, because flushes reached only the drive cache.
  - Measure the flush path, then cut power to the real hardware under write load and check integrity against a ledger kept on another machine.
  - Keep `fsync`, `synchronous_commit`, `full_page_writes` and data checksums on.
- **Unattended operation.** Never design a step that can prompt on an unwatched machine. Bound every wait, log what was skipped, and refuse to proceed when a prerequisite is missing. Test services as their own accounts, non-interactively and after a reboot.
- **Backups.** The backup target carries a marker file. A restore is not proven until it is restored into a new database and compared with a row-count manifest.
- **Downtime is lost data** for tickers and order books, which cannot be recovered. Trades are backfilled and recorded in a gap log, and `status` must say so.
- **Kalshi rate limits differ per endpoint** (unauthenticated): historical trades hold 10 requests a second; the live trades endpoint and the markets listings allow about 4 after a burst of about 12. The cutoff between the live and historical stores moves forward a day every day. The downloader backs off on any 429.
- **Kalshi trade queries.** On both the live and the historical trades endpoints `min_ts` is inclusive and `max_ts` exclusive, judged on the trade's whole second (measured 2026-10-22); the largest page is 1,000 (1,001 is an HTTP 400). A backfill never duplicates a `(market, trade_id)`.
- **Writes are never resent blindly.**
  - An order write is never retried after an unclear answer; look it up by `client_order_id` instead.
  - A transfer is never resent.
- **Ed25519 keys only.** Combo markets (`KXMVE…`) are skipped and counted.
- **Port tests.** When porting behaviour from KalshiTerm2, differential-test it against the reference. Write contract tests for every model that crosses the API.
- **From KAPS's edge study (K0.5, 2026-10-25):** the price-only calibration edge was a NO-GO; every validate-part lead fell back to noise on the held-out part, so a lead is never a result until a pre-registered test passes; spectacular cells on small samples were artefacts (quote nothing on fewer than about 300 events); polars spills to the system temp directory, so big runs set `TMPDIR` on the external drive and cap the spill; a one-moment snapshot of yields or rewards is an upper bound, never a forecast.

## Conventions

- **Layout.** A `uv` workspace: packages under `packages/<name>/src/<module>`, with tests in `packages/<name>/tests`.
  - `sparq-core` is the Kalshi client and the money and fee logic.
  - `sparq-server` is the ingest, database, API and web code, and runs on the server host.
  - `sparq-analytics` is the features, models, recommendations and trading service, and runs on the analytics node.
- **Checks.** `ruff` (format and lint), `mypy --strict`, Python 3.12 or later (`.python-version` pins 3.13). Line endings are LF. CI runs on Ubuntu and macOS only and is the authority; POSIX-only tests skip on this Windows machine, and the assistant runs the real tests on a Mac or Linux dev box over SSH (KAPS decision 36 (carried; see PLAN.md §2)).
- **Tests.** `pytest` with `pytest-asyncio`. Test functions are named `test_<behaviour>` in snake_case. Test module names are unique across packages, such as `test_core_version.py`.
- **Live tests.** Live tests against the real Kalshi API are run by the assistant without asking (standing permission; limits under Dev commands). Also write and run them wherever a fake cannot show real behaviour: boundaries, units, precision, ordering, pagination, rate limits and timestamps. Design each slice around the question "what would the real service do that my fake cannot?"
- **Database tests.**
  - They run in CI against a real PostgreSQL 18 + TimescaleDB on every pull request.
  - Locally, they run only against the **development cluster** on the server host (its own port, roles and data directory), never the production cluster. The fixtures refuse a database that matches production. SPARQ's test database names (`sparq_test`, `sparq_test_owner`) do not exist on that cluster yet, so until they are created SPARQ's database tests run in CI only.
- **Research.**
  - Notebooks live in `research/` and are committed **without outputs** (a pre-commit hook strips them).
  - Each study ends with a write-up in `docs/research/<study>.md` naming its data snapshot and reporting the benchmark ladder: the raw price, a bias correction by price bucket, and a naive maker strategy.
  - Data and outputs are never committed.
- **Machine names.** Docs and code name machines by role: the **server host** (Linux), the **analytics node** (macOS), the **laptop** and the **workstation**. Hardware model names appear only in the inventory table in docs/ASSESSMENT.md.
- **No real resources in tests.** Tests never use a real host, share or account name. Use `\\nas\backups\...` as text only, and `\\127.0.0.1\sparq-no-such-share\...` where a path is really touched.

## Dev commands

`uv` is installed for the user on this workstation (through pip), so here it runs as `python -m uv`; on the Macs and the server host plain `uv` works. The commands in this section are written for this workstation's PowerShell; they move to POSIX shell once the code runs only on Linux and macOS (KAPS decision 5 (carried; see PLAN.md §2)).

```powershell
python -m uv sync
python -m uv run ruff format --check .
python -m uv run ruff check .
python -m uv run mypy packages
python -m uv run pytest --cov
```

The coverage gate is 90% line and branch coverage overall (`[tool.coverage.report] fail_under` in `pyproject.toml`), **measured in the database job over the whole suite**, because code that talks to PostgreSQL is exercised only where there is a database (the macOS and Ubuntu `check` jobs skip those tests and run with `--cov-fail-under=0`; run locally without a test database the same way). It is **95% for the money and fee modules** (`fixedpoint.py`, `fees.py`), checked by their own CI step:

```powershell
python -m uv run coverage report --fail-under=95 --include="*/sparq_core/fixedpoint.py,*/sparq_core/fees.py"
```

**Download Kalshi's history** (public data, GET only, no key; the data goes in `data/kalshi`, which git ignores; run from the repository root). It needs `--production` because the demo site has no history. Ctrl+C is safe and the next run resumes; see KAPS PLAN.md §6.9 for the rates and the 50 GB cap:

```powershell
python -m uv run python -m sparq_analytics.history --production plan      # what would happen (one request)
python -m uv run python -m sparq_analytics.history --production series
python -m uv run python -m sparq_analytics.history --production markets
python -m uv run python -m sparq_analytics.history --production trades    # newest complete day first; --days N to limit
python -m uv run python -m sparq_analytics.history status                 # offline
python -m uv run python -m sparq_analytics.history verify                 # offline: integrity and the volume reconciliation
```

Regenerate golden vectors from the Python reference (only when the reference changes or a case is added; the output is committed and CI never runs the reference; run these with plain `python`, not `-I`):

```powershell
python tools/vectors/generate_fixedpoint.py --reference %USERPROFILE%/dev/KalshiTerm2
python tools/vectors/generate_fees.py --reference %USERPROFILE%/dev/KalshiTerm2
```

**Live tests against the real Kalshi API** (read-only key, GET only, skipped by default and in CI). The owner has given standing permission for the assistant to run them itself without asking (2026-10-16, re-confirmed 2026-10-19: "always run them"), within these limits:
- **GET only, never an order.**
- **The key.** The key id and path come from the owner's User environment variables `SPARQ_KALSHI_KEY_ID` and `SPARQ_KALSHI_PRIVATE_KEY_PATH` (SPARQ's own names since S2.h; KAPS's `KALSHI_*` variables never reach SPARQ), loaded into each command's own process and removed afterwards. Never print or read the key id, the key path or the key file; check that the file exists with `Test-Path` only.
- **The summary file.** Delete any summary file after reading it.
- **Report back.** Repeat what the run read in the visible reply: public market data and status codes, never an account value.

The live tests are marked `integration` and skipped unless `SPARQ_LIVE=1`; they use the production environment explicitly, since the demo site has no history. The public ones need no key:

```powershell
$env:SPARQ_LIVE = '1'; $env:SPARQ_LIVE_SUMMARY = "$env:TEMP\sparq-live-summary.txt"
python -m uv run pytest packages/sparq-core/tests/test_live_public.py -q   # about 15 GET requests; counts only in the summary
Remove-Item Env:SPARQ_LIVE, Env:SPARQ_LIVE_SUMMARY; Get-Content $env:TEMP\sparq-live-summary.txt; Remove-Item $env:TEMP\sparq-live-summary.txt
```

`packages/sparq-analytics/tests/test_live_history.py` (about 1,000 requests, three real windows of trades) proves the downloader: an interrupt and resume gives identical data, combo trades are dropped, and every market inside the window has a volume equal to the sum of its trades. `test_live_bounds.py` (about 40 requests) re-measures the time bounds, page sizes, cursor stability and ordering and **asserts the rules found**, so a change in Kalshi's behaviour fails loudly. The rate-limit ramp test was deleted in S2.h (2026-10-27): the ramp is never run.
