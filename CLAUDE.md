# CLAUDE.md

## Start here

**Read [docs/PLAN.md](docs/PLAN.md) before doing anything.** It is the source of truth for the direction, the architecture and the build order. Record how each slice was built and verified in [docs/BUILD-LOG.md](docs/BUILD-LOG.md), and update both when decisions change. The reasoning and sources are in [docs/ASSESSMENT.md](docs/ASSESSMENT.md); what the predecessors measured is in [docs/LESSONS.md](docs/LESSONS.md).

SPARQ (Systematic Prediction-market Automated Risk-managed Quoting) is written in Python. It is an **automated maker** for prediction markets: it quotes at a **seconds** scale in **second-tier** markets around a fair value taken from outside or related markets, never leaves a stale quote, and takes other traders' stale quotes when they are clearly wrong. It does **not** race professional firms in milliseconds: headline markets and news races are out of scope.

- **KAPS**, the direct predecessor, is **frozen** (owner, 2026-10-25). SPARQ started from its code as a fresh repository (no git history). KAPS lives at `github.com/swalsh76/KAPS` with a local clone at `%USERPROFILE%\dev\KAPS`; read it, but change it only in its own sessions and only for maintenance. **Its ingest, watchdog and backups keep running on the server host under the `kaps` names**, and its history download (paused) is on the analytics node: SPARQ reads that data until it deploys its own.
- **KalshiTerm2**, the owner's earlier Python project, is a **guide** to question and improve on, not copy: `%USERPROFILE%\dev\KalshiTerm2` (read only). **KalTerm3** (.NET) is retired; its code is at the git tag `kalterm3-final` in the KAPS repository.

## Status

**S0, the fork, is done** (merged 2026-10-25, SPARQ PR #1). **S1, evidence for the quoter, is proposed** (PLAN.md §6.1: decisions D1 to D9, slices S1.1 to S1.4) on branch `s1/proposal` and waits for the owner's approval; no S1 code starts before it. **No real order is sent without the owner's go-ahead.**

### Resume point (keep this current: update it at every step that changes it, so a lost session loses nothing)

*Last updated 2026-10-25 (S1.1 NO-GO; S1.1b registered, the forward verdict waits for the window to close on 2026-11-23; S1.2a started in parallel, D11).*

- **S1.2a, what the live book shows (branch `s1/2a-live-book`; owner approved 2026-10-25, D11):** read-only measurement over the ingest's data in KAPS's production database (top of book with sizes, trades, lifecycle; recording since 2026-10-24 21:13 UTC). **Access (owner chose the production database):** the main cluster already accepts the analytics node over TLS with `scram-sha-256`; the missing piece is a read-only role `sparq_reader`, created by the owner on the server host (privileged; the agent account has no `sudo`), with its password in `~/.pgpass` on the analytics node and the server's certificate pinned there. The role is outside `kaps-admin install`'s management. **Guard (D11):** no edge to settlement on forward-test events (opened from 2026-10-26, closing before 2026-11-23) until S1.1b's verdict; book measures and markouts up to 10 minutes are allowed. S1.2 follows only if the quote-refresh check does not show bots already requoting after every trade. *Progress 2026-10-25:* the code is on the branch (`python -m sparq_analytics.live extract|measure|report`; 38 tests, 18 planted bugs caught); the settings file and pinned certificate are on the analytics node; everything from 2026-10-24 21:00 UTC is copied (1.4 GB for 26 hours; about 2.2 minutes and 2 GB a day). Feed facts: a ticker frame follows 99.95% of trades within 1 s (median 156 ms); frames are about a second apart (no hard floor); the feed reaches the server about 0.55 s after Kalshi's timestamp. **The owner approved the stop rule and its days on 2026-10-25** (PLAN §6.1, S1.2a row): read once on 2026-10-25 and 2026-10-26 together, after 2026-10-27 01:10 UTC (`extract` to that time, then `measure --start 2026-10-25 --end 2026-10-27` with `docs/research/forward-rule.json` and the frozen activity file, then `report`). Do not read the forward rule's refill share before then; a timing run on one day may report only row counts and run time. *Added 2026-10-26, owner approved, before any reading:* `measure` leaves out (and counts) trades whose windows reach into a ticker outage in the gap log, or that come in the minute after one, because their book would be missing or stale (a missed refill leans the stop rule toward going on); `extract` copies the gap list.

- **S1.1b (branch `s1/1b-forward`, stacked on `s1/1-middle-tier`; owner approved 2026-10-25, D10):** R4 refined by the 36-variant scan on the fit and validate parts; the chosen rule (quiet 60 s to 1 h, activity 1k to 100k, price 20 to 80 cents; `docs/research/forward-rule.json`) is **registered** (digest `0af9ea44be7fa121f36db9d5d52629e2768da910532eea390a06c9a9b4859b01`, frozen activity hash `edf3189cf82aa9f340aaf3b9798c7a0465c17a643e64b5b9ab36e1b9b6e7abf8`); nothing is opened. The scan showed R4 negative on the fit part (-0.86): the edge appears only from late September. **To finish (from about 2026-11-26):** on the analytics node re-run KAPS's downloader for the forward days only (`run-download.sh` resumes newest-first; stop once 2026-10-26 to 2026-11-22 are complete, then the `markets` step), then the S1.1b run script's `verdict` step (the script sits beside the study folder on the analytics node; its path is in the assistant's memory) once (it opens the window through `forward-ledger.json` in `sparq/study/s1.1b`), and the write-up. GO resumes S1 at S1.2; NO-GO stops the maker direction.

- **S1.1 done, NO-GO (branch `s1/1-middle-tier`, PR open):** the four approved rules on the test part (digest `95eec43a8d2d…`, opened once 2026-10-25 20:49 UTC): R1 -0.09 cents (lower -2.00), R2 +2.71 (-3.66), R4 +2.51 (-1.30), R5 -1.29 (-3.98). Write-up `docs/research/middle-tier.md`. Under D3 S1 stops: S1.2 to S1.4 are not started. **Next: the owner's rethink** (options in the write-up: stop; a forward test of the human-paced maker on trades recorded after 22 October; another strand from PLAN §9). The rules ledger on the analytics node locks this test part.

- **S1 proposal (branch `s1/proposal`):** awaiting the owner. Open points for the owner inside it: the S1.1 rule list (approved before the test part is read), a separate read-only key on the analytics node for S1.2, the trading key's custody (S1.3), and the pilot's caps (S1.4). KAPS's hourly download check was removed (KAPS frozen, download paused).

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
  - its BUILD-LOG entry is written.
- **The BUILD-LOG entry** has one section per milestone and one subsection per slice, with the headings **Built**, **Verified** (local, on the server, live), **Problems and fixes**, and any decisions taken. The slice table in PLAN.md marks the slice "done (BUILD-LOG x.y)".
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
- **The key.** The key id and path come from the owner's User environment variables `KALSHI_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH`, loaded into each command's own process and removed afterwards. Never print or read the key id, the key path or the key file; check that the file exists with `Test-Path` only.
- **The summary file.** Delete any summary file after reading it.
- **Report back.** Repeat what the run read in the visible reply: public market data and status codes, never an account value.

The live tests are marked `integration` and skipped unless `KALSHI_INTEGRATION=1`; they use the production environment explicitly, since the demo site has no history. The public ones need no key:

```powershell
$env:KALSHI_INTEGRATION = '1'; $env:SPARQ_LIVE_SUMMARY = "$env:TEMP\sparq-live-summary.txt"
python -m uv run pytest packages/sparq-core/tests/test_live_public.py -q   # about 15 GET requests; counts only in the summary
Remove-Item Env:KALSHI_INTEGRATION, Env:SPARQ_LIVE_SUMMARY; Get-Content $env:TEMP\sparq-live-summary.txt; Remove-Item $env:TEMP\sparq-live-summary.txt
```

`packages/sparq-analytics/tests/test_live_history.py` (about 1,000 requests, three real windows of trades) proves the downloader: an interrupt and resume gives identical data, combo trades are dropped, and every market inside the window has a volume equal to the sum of its trades. `test_live_bounds.py` (about 40 requests) re-measures the time bounds, page sizes, cursor stability and ordering and **asserts the rules found**, so a change in Kalshi's behaviour fails loudly. `test_live_ratelimit.py` ramps the request rate and needs `SPARQ_LIVE_RATE_PROBE=1` as well; run it only for a deliberate measurement.
