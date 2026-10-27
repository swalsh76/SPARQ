# SPARQ plan

**Status:** S0 is done. S1 was approved (2026-10-25); its first slice, **S1.1, returned a NO-GO** (`docs/research/middle-tier.md`), so under D3 S1 stopped for a rethink. S1.1b's forward test was registered and then **dropped unopened (D13, 2026-10-27)** after S1.2a's live reading, and S1.2 is on hold: the direction was reassessed from §9 and **S2, weather against station observations (§6.2), was approved on 2026-10-27**. This document is the source of truth for the direction, the architecture and the build order. [ASSESSMENT.md](ASSESSMENT.md) holds the reasoning and the sources; [LESSONS.md](LESSONS.md) holds what the predecessors measured. SPARQ carries much of KAPS's design by reference: where this plan says "KAPS PLAN §x", read [KAPS's plan](https://github.com/swalsh76/KAPS/blob/main/docs/PLAN.md) at that section.

## 1. Goal

SPARQ (Systematic Prediction-market Automated Risk-managed Quoting) makes money from prediction markets **as a maker**. On Kalshi the money flows from takers to makers (the 2026 makers-and-takers paper: takers -31.5% and makers -9.6% on average; ASSESSMENT §7), and the price itself is already close to the probability (KAPS's K0.5 study: NO-GO). So SPARQ does not try to out-predict the market. It:

- **quotes** at a **seconds** scale in **second-tier** markets, busy enough to trade but not the headline markets where professional firms quote in milliseconds;
- prices its quotes around a **fair value** from related markets (brackets, spreads against moneylines, one underlying across series) and from free or cheap outside anchors (spot prices, station observations, other venues);
- **never leaves a stale quote**: it refreshes, widens or pulls when its fair value or a related market moves, or when a market goes quiet;
- **takes other traders' stale quotes** when they are clearly wrong (at listing, after quiet spells, after a related market moves);
- stays **out** of headline markets and news races, which it would lose.

It anticipates what ordinary traders will do in the narrow sense that matters for a maker: when human-paced takers will arrive, and when an informed one is about to pick a quote off.

## 2. Decisions

### SPARQ's own decisions

| # | Decision | Date |
|---|---|---|
| Q1 | **The fork.** A new repository started fresh from KAPS `main` at `6cd0083` (no git history), **public under MIT**. Packages renamed `sparq-core`, `sparq-server`, `sparq-analytics`; the admin tool `sparq-admin`. The server package is carried, idle until SPARQ deploys its own services. KAPS's web-design mock-ups and its K0.5 write-up stay in KAPS. **KAPS is frozen**: maintenance only, its ingest, watchdog and backups keep running under the `kaps` names. | 2026-10-25 |
| Q2 | **Direction: an automated maker at a seconds scale in second-tier markets** (§1, §5). Not high-frequency trading: no co-location, no millisecond races. | 2026-10-25 |
| Q3 | **Evidence gates, in order:** (1) a pre-registered test of the middle-tier rules on held-out historical trades; (2) a shadow quoter that runs live against the real books and places no order; (3) the write path and risk layer on demo; (4) a tiny live pilot with the owner's go-ahead; (5) growth only while markouts and profit stay positive after fees. Each gate is set in advance, as in KAPS's K0.5 (decision 15 carried). | 2026-10-25 |
| Q4 | **Nothing relies on a limited-term program.** Kalshi's Liquidity Incentive Program (and any like it) is excluded from every test and every go/no-go; if it continues, it is extra income, never the basis. | 2026-10-25 |
| Q5 | **Venues: Kalshi first.** Polymarket US is the candidate second venue (an official API signed with Ed25519, makers pay no fee and earn a rebate). Interactive Brokers (ForecastEx, Kalshi and CME event contracts through one API, with the hedges in the same account) is the candidate for an outside-fair-value strand. Venues without a trading API are price signals only. Each venue's developer terms are read before any use, as Kalshi's were (decision 34 carried). | 2026-10-25 |
| Q6 | **Data: KAPS's data is SPARQ's source until SPARQ deploys its own.** The history download (77 trade days, paused) on the analytics node and KAPS's live ingest on the server host. SPARQ never writes to KAPS's database. | 2026-10-25 |
| Q7 | **No web UI in scope** (parked, §9). SPARQ is run from the command line, its logs and push alerts. | 2026-10-25 |

### Carried from KAPS (KAPS PLAN §2, by number)

**Kept as they stand:** 1 (Python only; KalshiTerm2 is a guide), 2 (PostgreSQL 18 and TimescaleDB, native), 3 (the server host), 4 (the analytics node runs the analytics and the trading service and holds the trading key), 5 (Linux and macOS only, with the training offload as the exception), 7 (portable model formats), 8 (MIT, public), 9 (**demo by default; no real order without the owner's go-ahead**), 10 (money is fixed-point), 12 (deployable without the repository), 15 (realistic evaluation: explicit targets, pre-registered gates, fill and queue modelling), 18 (push alerts), 19 (operations), 21 (built for expansion: the `venue` column, rules as data, versioned contracts), 22 (rate-limit shares, with a reserve for trading), 23 to 26 (market-detail history, the exchange schedule, daily reconciliation, the raw-feed spool), 28 (**one engine for backtest, paper and live**: the shadow quoter, the demo run and the pilot share the same code), 30 (reproducible research), 32 (secrets), 33 (databases for development and CI), 34 (Kalshi's terms read and recorded), 36 (Linux and macOS code tested from the workstation over SSH), 38 (one user, the owner), 39 (the ingest, as KAPS runs it).

**Adapted:** 16 (sizing): limits per market, event, series, category and day stay; quarter-Kelly sizing gives way to a fixed small quote size per market until the pilot measures fills. 27 (benchmark ladder): a quoting rule is judged against a naive maker under the strict fill rule and a random-entry baseline with the same timing and fees. 31 (service targets): set in the milestone that runs the quoter unattended.

**Not carried:** 6, 17, 29 and 35 (the web UI, the per-order passkey from the browser, explanations shown in the UI, the UI design gate: parked with the UI); 11 and 13, 14, 20, 37 (training-offload scheduling, KAPS's milestone order and its K1 split, the cold archive and KAPS's old data: KAPS history, revisited only if SPARQ needs them).

## 3. Topology

As KAPS (KAPS PLAN §3), with one change of role: the **trading service on the analytics node is the quoter**. It holds the trading key, opens its own WebSocket connection for the markets it quotes, and reads related markets and outside anchors itself, so a database hop never sits between a price move and a quote change. The server host keeps KAPS's database and ingest; SPARQ reads from them and does not write to them (Q6).

## 4. Stack

As KAPS (KAPS PLAN §4): Python 3.13, a `uv` workspace, `ruff`, `mypy --strict`, `pytest`; `httpx`, `websockets`, `cryptography` (Ed25519), `pydantic` v2; Polars, DuckDB and Parquet for research; PostgreSQL 18 and TimescaleDB when SPARQ stores data of its own. No new package is added without a slice plan that names it and its licence.

## 5. The quoter's design (to be refined by S1's slices)

| Part | What it does |
|---|---|
| **Fair value** | From related Kalshi markets (bracket consistency, spreads against moneylines, one underlying across series), the book's size-weighted mid, and free or cheap outside anchors where a market has one. Each market type gets the simplest fair value that the evidence supports. |
| **Quoting** | Post-only quotes around the fair value in 100 to 150 second-tier markets, refreshed every 5 to 10 seconds; the price leans against the inventory it holds (Avellaneda and Stoikov's approach); quotes widen when a market goes quiet and are pulled when the fair value or a related market moves. |
| **Picking off** | Takes another trader's resting quote when it is off the fair value by more than the fee and a margin. |
| **Risk** | Limits per market, event, category and day; hedges in related markets (and perps where they exist); an expiry on every quote; a kill switch; an unclear order is looked up by `client_order_id`, never resent. |
| **Research loop** | Markouts after every fill (the price 5 seconds, 1 minute and 10 minutes later), profit split into spread earned, adverse selection, fees and inventory, and KAPS's study harness for offline tests. |

**Constraints known so far:** Kalshi's Advanced API tier allows about 30 writes a second (Basic about 10), enough for about 150 two-sided markets at a 10-second refresh; the order API supports post-only, `expiration_ts`, order groups, amend and batch (to be confirmed against the current API in S1). `/account/limits` and `/account/endpoint_costs` are read before the write path is designed.

## 6. Build order

Each milestone is proposed as a set of decisions and slices and approved before any code; each slice gets its own plan, branch and BUILD-LOG entry (CLAUDE.md, "Workflow").

| # | Milestone | Gate | Status |
|---|---|---|---|
| **S0** | **The fork:** the repository, the renamed packages, the founding documents, CI in the new repository, one live check | CI green on Ubuntu and macOS and the database job; `test_live_public` passes; the owner merges | done (BUILD-LOG S0) |
| **S1** | **Evidence for the quoter** (§6.1): S1.1 the pre-registered middle-tier test; S1.2 the shadow quoter; S1.3 the write path, risk layer and kill switch on demo; S1.4 the tiny live pilot | Each slice's gate in §6.1; a slice starts only when the one before it passes | approved 2026-10-25; **S1.1 NO-GO**; S1.2a done (go on); **S1.1b's forward test dropped and S1.2 on hold (D13, 2026-10-27)**; the direction is reassessed from §9 |
| **S2** | **Weather against station observations** (§6.2): S2.1 sources, terms and the observation history; S2.2 exploration on the fit and validate parts; S2.3 the pre-registered test on the held-out part; S2.4 a live shadow run against the real books | Each slice's gate in §6.2 | **approved 2026-10-27**; S2.1 in progress |

### 6.1 S1 Evidence for the quoter: decisions and slices (approved 2026-10-25)

**Decisions (approved by the owner, 2026-10-25).**

| # | Decision |
|---|---|
| D1 | **The test data for S1.1** is KAPS's 77-day download with K0.5's window, split dates and stratified sample. K0.5's test part (events closing 10 to 19 October) was read only for price-band segments, so it is fair for these new rules; S1.1 keeps its own pre-registration ledger, separate from K0.5's. |
| D2 | **S1.1's rules are fixed on the fit and validate parts only**, at most 12, each with its reason, and **the owner approves the list before the test part is read**. The candidates come from ASSESSMENT §7.4: the maker under strict fills in series of middle activity (median 10k to 100k contracts a market, measured on the fit part); the maker on human-paced trades (1 second to 1 hour after the previous trade); the same maker that never stands on a stale quote (it skips a market's first trade and trades after more than an hour of quiet); the taker that picks off a market's first trade; and per-category versions where the validate part has 300 events or more. |
| D3 | **S1.1's gate is K0.5's:** GO if at least one rule has a lower bound above zero, after fees and under the strict fill rule, at a one-sided tail of 0.025 divided by the rules named, over at least 300 events. Otherwise NO-GO, which stops S1 for a rethink (no widening of the same rules). |
| D4 | **The shadow quoter (S1.2) places no order.** It runs on the analytics node from a SPARQ clone, opens its own authenticated WebSocket for about 50 second-tier markets chosen by S1.1's winning rule, keeps their order books from the `orderbook_delta` channel, computes the quotes it would post (fair value v0: the book's size-weighted mid, with bracket consistency where it is simple), and counts its fills from the live trades by the strict rule, with markouts at 5 seconds, 1 minute and 10 minutes. It runs for 10 trading days. |
| D5 | **S1.2's gate:** the simulated profit after fees is positive with a lower bound above zero over events, and the 1-minute markout of its fills is not worse than the half-spread it earns. Fixed in S1.2's plan before it runs. |
| D6 | **The key for S1.2 and S1.3** is a separate Kalshi key on the analytics node: read-only for S1.2, the trading key from S1.3, stored where only the quoter's account can read it (the custody design is part of S1.3's plan). It never leaves the analytics node. |
| D7 | **The write path (S1.3) is built and proven on demo only:** a fixed list of order routes (create, cancel, amend, batch, and reads of orders, fills and positions), post-only quotes, an expiry on every quote, look-up by `client_order_id` instead of a resend, limits per market, event, category and day enforced before an order leaves, and a kill switch that cancels everything. It first reads `/account/limits` and `/account/endpoint_costs` (GET only). |
| D8 | **The pilot (S1.4) is tiny and bounded**, and needs the owner's go-ahead before it starts: at most 10 markets, a fixed small size, a total exposure cap and a daily loss stop (amounts set in S1.4's plan with the owner), for 10 trading days. Within those bounds the owner's go-ahead covers the pilot's quotes as a whole; anything outside them is refused by the risk layer. The pilot's gate: profit and markouts positive after fees, and the simulator's fills match the real ones closely enough to trust it. |
| D9 | **Kalshi only in S1.** Polymarket US waits for a passed S1. |
| D10 | **D3 amended (owner, 2026-10-25): one forward test after S1.1's NO-GO.** R4 (the human-paced maker) is refined on the fit and validate parts only, from a fixed grid of 36 variants (quiet window x activity range x price band) by a choosing rule fixed in advance, and the one chosen rule is tested **forward** on events that open on or after 2026-10-26 and close before 2026-11-23, at a one-sided tail of 0.025 over at least 300 events, with series activity frozen from the original fit part. GO resumes S1 at S1.2; NO-GO stops the maker direction. The forward days are added by re-running KAPS's downloader for them only. |
| D11 | **D4 and D10 amended (owner, 2026-10-25): live-data work runs in parallel with the forward test.** S1.2a reads what KAPS's ingest records (top of book with sizes, trades, lifecycle) from the production database through a read-only role, from the analytics node over TLS. **Guard:** until S1.1b's verdict, no live-data work computes an edge to settlement on events the forward test scores (opened on or after 2026-10-26 and closing before 2026-11-23); book measures and markouts of 10 minutes or less are allowed anywhere. The forward test is not changed, stopped early or re-run because of anything the live data shows. **S1.2** (the shadow quoter) follows S1.2a only if S1.2a's quote-refresh check does not show bots already requoting after every trade. **S1.4** (real money) needs both a passed S1.2 and a passed forward test. |
| D12 | **S1.2's plan (owner approved 2026-10-26; it applies only if S1.2a's stop rule says "go on").** S1.2 is split: **S1.2b** records the live order books, **S1.2c** simulates the quoter by replaying the recording. The first 2 recorded days are for developing and tuning; the simulator's settings and the gate are then locked by hash, and the next 10 days are read once as the held-out test. **Markets:** about 50 at a time from middle-activity series (the frozen activity file), open with at least an hour to close, last price 20 to 80 cents, traded in the last 24 hours, ranked by human-paced trades, at most 3 per series, redrawn daily at 00:00 UTC. **Simulator v0:** the size-weighted mid as fair value; it joins the best bid and ask only where the fair value is at least 1 cent away, 10 contracts a quote, inventory skew, at most 50 contracts a market, quotes acting 0.8 s after a book change, at most 5 order writes a second across all markets, S1.2a's queue rule for fills (the deciding variant credits no cancellation ahead), the conservative maker fee. **D5 amended (because of D11):** GO for S1.3 needs the realized spread at 10 minutes after fees, `m * (fill price - mid 10 minutes later) - fee`, to have a lower bound above zero (one-sided 0.025, over events), and the average realized spread at 1 minute to be at least zero, over at least 300 fills and 50 events (fewer: the run is extended once, by up to 10 days). Profit to settlement is reported after S1.1b's verdict, for information. **The recorder runs under a `launchd` daemon** as the agent account on the analytics node (the owner installs it once with `sudo`), from a clone pinned to a commit, with the read-only key (D6), subscriptions and GET only. |
| D13 | **The forward test is dropped and S1.2 is on hold (owner, 2026-10-27, after S1.2a's reading).** S1.2a showed that an order joining the book after a trade is adversely selected (its fills lose 2.9 cents by 10 minutes before fees in the forward rule's markets, and lose at 60 s and 10 minutes in every tier), so S1.2c's simulator as designed in D12 would very likely fail its gate, and S1.1b's forward test (prior evidence: R4 negative on the fit part, +2.51 with a lower bound of -1.30 on the test part) would most likely end NO-GO and, even with a GO, would not show that SPARQ can be the resting maker. S1.1b is closed unopened: its ledger is never written and its window is never read; the registration stays in the repository as a record. D11's guard lapses with it. S1.2b and S1.2c are on hold, not started; S1.2r stands. The direction is reassessed from §9, and the next strand is proposed as decisions and slices for the owner's approval. KAPS's ingest keeps recording. |

**Slices.**

| Slice | What it builds | Gate | Status |
|---|---|---|---|
| S1.1 | The pre-registered middle-tier test: the rules from D2 coded on KAPS's study harness, the rule list for the owner's approval, the test part opened once, the write-up in `docs/research/` | D3 | **done: NO-GO** (BUILD-LOG S1.1, `docs/research/middle-tier.md`) |
| S1.2a | What the live book shows (D11): read-only queries over the ingest's data in the forward rule's markets and the busy ones for comparison: quoted spreads and top sizes, a queue-aware fill rule against S1.1's strict rule, markouts at 5 s, 1 min and 10 min by taker side, and how fast quotes refresh after a trade | Decides nothing alone; fixes S1.2's fill model and markout settings, and stops before S1.2 if bots already requote after every trade. **Stop rule (owner approved 2026-10-25, before any reading):** among the forward rule's own trades, if more than 50% of the levels takers cleared (took at least the size shown at the best price, or traded beyond it) are shown refilled at the same price or better within 1 s of the trade, S1.2 is not started; fewer than 300 cleared levels is "too few" and decides nothing. Read once, on the UTC days 2026-10-25 and 2026-10-26 together | **done: go on** (BUILD-LOG S1.2a; read 2026-10-27 01:17 UTC: 11.1% of 43,742 cleared levels refilled within 1 s; an order joining after the trade fills a third of the time and its fills lose 2.9 cents by 10 minutes before fees, the main risk for S1.2c) |
| S1.2b | The order-book recorder (D12): book models and a book keeper on SPARQ's WebSocket client, the daily market choice, hourly Parquet of the raw book messages and trades with a gap log, the `launchd` daemon | Runs 24 hours unattended; the rebuilt best bid and ask match the ingest's ticker feed at least 99% of the time; gaps logged | **on hold** (D13; S1.2a said "go on" on 2026-10-27). **Slice plan approved 2026-10-26:** book models in `sparq_core.ws_models` on the existing WebSocket client; a book keeper per market that resyncs with `get_snapshot` after a gap or reconnect; the daily choice from `extract` and the forward rule's filter, swapped in place at 00:00 UTC; hourly Parquet of the book messages, **the trade channel recorded and also checked against the ingest's trades**, the daily lists and its own gap log; `run`, `status` and `check`; refusals with named exit codes (drive missing, key or settings missing, drive 90% full); **above 5 GB a day only a warning in `status`**; the `launchd` daemon `local.sparq.book-recorder` as the agent account from a pinned clone, installed by the owner with `sudo`. *Added 2026-10-26 (owner approved):* analysis jobs on the node cap their threads (`POLARS_MAX_THREADS`) so the recorder always has cores free |
| S1.2c | The shadow quoter's simulator (D12): replay of the recording, fair value v0, quotes, queue fills, fees, realized spreads; tuned on the first 2 days, locked by hash, read once on the next 10. *The second code review's two items for this slice were done early, in S1.2a, at the owner's request (2026-10-26; BUILD-LOG S1.2a): a failed ticker resubscribe ends at the next reconnect, and a day is measured in 16 groups of markets (peak 14.5 GB of 36 GB)* | D5 as amended in D12 | **on hold** (D13) |
| S1.2q | Review clean-up (owner approved 2026-10-26; nothing in it changes a result): *from the first review* (6) one retry-with-backoff helper for the writer's two loops and the pump's; (7) `install --dry-run` built from the steps that run, not a separate list that has drifted (it promises a governor and a web port, and names `/opt/sparq/<version>`); (8) a `pg.py` for the helpers `verify`, `restore` and `ingest-status` import from `install.py`, and one "create the role if missing"; (9) `explore` builds the trade frame once, and `build_snapshot` makes two full passes instead of three; (10) one `KalshiConfig` helper for a rate-limited client (the ingest accepts a rate below 1 that the client refuses). *From the second review* (3) the history verifier in polars instead of a Python loop over millions of markets; (4) one market-file loader for the study (three copies; `reference.py` without the historical-row precedence); (5) one GO/NO-GO/INCONCLUSIVE and `decide` for `verdict.py` and `rules.py`, and the dead fallback in `evaluate_segment` removed; (6) `cells.py` uses the bootstrap's standard-error formula; (7) `candles.py` uses `series_ticker_of`, a typed folder, and the day checks shared with `_finish_day`; (8) `alert-test`'s hint from the unit constant, one percentile helper, and the durability tool out of the production package; and SPARQ's own pump closes an `all` gap once its subscriptions are restored (the live code no longer depends on it, but SPARQ's ingest should not leave one open). The first review's low items (one `E6`, the generic `_object`, one model base class, the shared unit hardening, and the rest) are folded in where a file is touched | Behaviour unchanged: format, lint, types, tests and coverage; planted bugs caught | **done** (BUILD-LOG S1.2q; PR #7 merged 2026-10-26). Two small items wait in S1.2a's code until after its reading: `ONE_E6` in `live/book.py` and the live command line's command list |
| S1.2r | Analysis efficiency on the analytics node (owner approved 2026-10-26, before S1.2c; no result may change): (1) the temporary and spill folder defaults to the data drive in the code, so no run depends on `TMPDIR` and `POLARS_OOC_DISK_BUDGET_MB` typed by hand (one once filled the internal disk); (2) `measure` skips a day whose result file was made by the same code and settings; (3) the live copies stored per day and sorted by market, so each group of markets, and S1.2c's replay, reads only its own part; (4) a measurement of polars' streaming engine on the study's whole-history passes (the snapshot spilled about 71 GB), adopted only if the outputs are identical; also S1.2a's two leftovers (`ONE_E6` in `live/book.py`, the live command line's command list) | Results identical to the present code; day time and peak memory measured before and after | not started (after S1.2a's reading and the merge of PR #5: items 2 and 3 change the code the reading runs) |
| S1.3 | The write path, risk layer and kill switch on demo, with the key custody design | Proven on demo: every route, every limit refusal, the kill switch, an unclear-order look-up | not started |
| S1.1b | R4 refined on the fit and validate parts (D10), the chosen rule pre-registered, the forward days downloaded after the window closes, the forward test opened once, the write-up | D10 | **dropped 2026-10-27, unopened** (D13; registered 2026-10-25, digest `0af9ea44be7f...`, `docs/research/forward-test.md`) |
| S1.4 | The tiny live pilot | D8 | not started |
| S1.2p | Shared definitions (owner approved 2026-10-26, from a code review; items 1 and 5 of the review went into S1.2a): the stored market in `sparq_core.markets` (combo prefix, labels, columns, the row builder) for the downloader, the ingest and the schema; the WebSocket address per environment in `config.py`, demo by default, the ingest asking for production; `sparq_server.names` for the names install, the ingest and the watchdog share, with the state keys and gap kinds imported, not spelled out | Behaviour unchanged: format, lint, types, tests and coverage; planted bugs caught | **done** (BUILD-LOG S1.2p) |

### 6.2 S2 Weather against station observations (approved 2026-10-27)

**Why this strand (the §9 reassessment, 2026-10-27).**
- K0.5 showed Kalshi's prices are close to the true probability.
- S1 showed that plain spread capture is closed to a late joiner: in S1.2a, a joining order's fills lose 2.9 cents by 10 minutes before fees.
- So an edge needs a fair value better than the book's, from information the book is slow to price.

Kalshi's daily temperature and rain markets settle on a station's measurement, and the station's own reports during the day put **hard bounds** on that measurement:
- once the reported temperature has passed a bracket's top, that bracket cannot win;
- once measurable rain is reported, "rain today" is settled in practice.

The question is whether the book still offers prices on the wrong side of a bound after the report is public: how often, for how long, in what size, and what that is worth after fees. It is the cheapest strand to test, because the 77 days of trades are already on the analytics node and the observation archives are free.

**What the history holds** (2026-08-07 to 2026-10-22, read on 2026-10-27 for this proposal; no outcome was measured). Climate and Weather has 458 series and 11.1 M trades, 1.4% of all trades. The 25 busiest series hold 89% of the weather contracts.
- **Rain.** `KXRAIN` is the busiest weather series, with 2.4 M trades (about 31,000 a day). It is one event a day with about 30 city markets. A market settles YES when precipitation at the climate station is strictly greater than 0 inches; a trace counts as 0.
- **Daily highs.** About 20 cities, each with six markets a city-day (two tails and four 2-degree brackets):
  - the busiest: `KXHIGHLAX` (1.1 M trades), `KXHIGHNY` (0.6 M), `KXHIGHMIA`, `KXHIGHCHI` and `KXHIGHAUS`;
  - then `KXHIGHTPHX`, `KXHIGHTSFO`, `KXHIGHTDAL`, `KXHIGHTSEA`, `KXHIGHTATL`, `KXHIGHPHIL`, `KXHIGHDEN`, `KXHIGHTHOU`, `KXHIGHTLV`, `KXHIGHTBOS`, `KXHIGHTOKC`, `KXHIGHTMIN`, `KXHIGHTDC` and smaller ones.

  A few cities also have daily lows.
- **Settlement.** The highs and rain now settle on **The Weather Company's** value for a named NWS climate station (`CLINYC`, `CLIMDW`, `CLILAX` and so on). Markets of December 2025 named the NWS Climatological Report (Daily) instead. The **hourly** temperature series (`KXTEMP...H`, `...HS`) settle on Synoptic Data's reading at a set hour.
- **Times.** A daily market opens the day before and closes at about 01:00 local time after its day, so it trades through the whole observed day.

**Decisions proposed (W1 to W8).**

| # | Decision |
|---|---|
| W1 | **Scope: the daily-high markets and `KXRAIN`.** Daily lows follow only if the highs show an edge. The hourly temperature series are **out**: their value is one reading at a set hour, so any edge is a race to see the reading first, which a feed arriving 0.55 s late (S1.2a) would lose. Taking stale quotes is tested first. Quoting around an observation-based fair value is a later slice, only if S2.3 passes. |
| W2 | **Ground truth is Kalshi's settled result,** not a weather source. Each test is scored on the market's own result, so a disagreement between the observations and The Weather Company shows up as a loss, not as an excluded case. |
| W3 | **Observations: the NWS's hourly and special station reports (METAR)** for each climate station. The history comes from the Iowa Environmental Mesonet's archive, and live use from NOAA's aviation weather service; both are free. Each source's terms are read and recorded before use (decision 34 carried). The station map (series, climate code, station identifier, time zone) is stored as data and checked against each series' rules text. |
| W4 | **When a report counts: from its time of issue plus a publication delay.** The delay is set from a live measurement in S2.1, and is never below 5 minutes for the history. **The bound is conservative:**<ul><li>reported temperatures, converted from tenths of a degree Celsius, are rounded down;</li><li>a bracket is ruled out only when the bound clears its top by at least 1 °F (the climate value is a whole °F from finer data, and its maximum can only be higher than any hourly report);</li><li>rain counts only from a report of at least 0.01 inch.</li></ul>Both margins are fixed before S2.2's results are seen; afterwards they may be widened, never narrowed. |
| W5 | **Fills.** The history shows only trades, so a rule may take at most the size that actually traded on the wrong side of the bound after the report counted, at that trade's price or one worse for us. In the live run, at most the size shown. Taker fees follow Kalshi's schedule (KAPS D13), rounded up per order. |
| W6 | **Parts:**<ul><li>fit, 2026-08-07 to 2026-09-28, and validate, 2026-09-29 to 2026-10-10: explored freely;</li><li>test, 2026-10-11 to 2026-10-22: opened once.</li></ul>The unit, and the bootstrap's cluster, is the **city-day**, since a `KXRAIN` event spans 30 cities. *Caveat:* K0.5's Weather segments were scored on these test days, but as price-only rules; nothing about observations was read there. |
| W7 | **Gate for S2.3 (GO).** The chosen rules (at most 4, approved by the owner from the validate part and locked by hash) must meet two conditions:<ul><li>their profit to settlement after fees has a lower bound above zero (one-sided 0.025, clustered by city-day), over at least 100 city-days with a signal;</li><li>they average at least **$25 a day** at the size the fill rule allows; below that the strand does not pay for its build.</li></ul>Fewer than 100 signalled city-days is INCONCLUSIVE, and the test is extended once with the live recording's days. |
| W8 | **Reported for every rule:**<ul><li>how long after a report counts the book moves past the bound (the competition's speed);</li><li>how often the ruled-out side traded after the bound (it should never happen; any case is studied);</li><li>the profit by hour of the local day.</li></ul>**No real order in S2.** A GO leads to a live shadow run (S2.4), then S1.3's write path on demo. |

**Slices.**

| Slice | What it builds | Gate | Status |
|---|---|---|---|
| S2.1 | **Sources and terms:**<ul><li>the station map, from the series' rules;</li><li>the observation sources' terms, read and recorded;</li><li>`sparq_analytics.weather`, with a downloader of the archived reports for the 77 days (resumable, GET only) and a parser for temperature and precipitation;</li><li>a check of the observed bounds against Kalshi's results on the fit part;</li><li>a live measurement, over one day, of the aviation weather service's publication delay.</li></ul> | The map covers every in-scope series; on the fit part the bound is never on the wrong side of the result (every exception explained); the delay is measured | **in progress** (branch `s2/1-sources`; map and check pass: 0 wrong of 2,243 calls on 8,397 fit-part markets; the 24-hour delay run ends 2026-10-28) |
| S2.h | **Core hardening from IKARS's review** (owner added 2026-10-27; scope creep, approved): one table of public routes with no account archive or limits for the data key; market-data channels only; no key path in errors; a key-id check; SPARQ's own environment variables; real test markers; text-only dollar prices; the rate-limit ramp test deleted; `pip-audit` and Dependabot | No result changes; every guard refuses in a test; planted bugs caught | **done** (BUILD-LOG S2.h) |
| S2.2 | **Exploration on the fit and validate parts:**<ul><li>how often and by how much trades crossed a bound after the report counted, by series, hour and side, with fees;</li><li>how fast the book reprices;</li><li>a proposal of at most 4 rules, with reasons, for the owner.</li></ul> | The owner approves the rule list | not started |
| S2.3 | **The pre-registered test:** rules and settings locked by hash, the test part opened once, the write-up in `docs/research/weather.md` | W7 | not started |
| S2.4 | **A live shadow run (only on GO), for two weeks:** the observation feed and the ingest's books in real time, with the would-be orders logged against the live book and no order sent | Its own plan, set before it starts | not started |

**What it changes on machines:** the analytics node only. The data goes under the SPARQ data folder on the external drive, a few MB of reports a day.

**New packages:** none expected. The parser is a few lines, and downloads use `httpx`.

**The owner's answers (2026-10-27):**
- the $25-a-day floor in W7 stands for now;
- S2 goes before S1.2r;
- the daily lows join only if the highs show an edge (W1 as written).

## 7. Risks

- **The middle-tier lead may not hold.** It is exploratory, from the validate part, with intervals that cross zero; KAPS's validate leads all failed on the test part. S1.1 decides.
- **Professional firms may move into second-tier markets,** and our place in the queue is unknown until real orders exist.
- **Retail flow may move.** Robinhood, reported to carry over half of Kalshi's volume, is building its own exchange with Susquehanna; if its flow leaves Kalshi, the spread paid in second-tier markets could shrink.
- **Rate limits and order capacity** are not yet measured for writes.
- **Adverse selection** (being filled just before the price moves against the quote) is the main cost; markouts measure it from the first shadow run.
- **Kalshi's Developer Agreement** (KAPS PLAN §7): own trading only; single user; storing and sharing data and public statements restricted; the points the owner set aside stay set aside.
- **This repository is public:** its quoting rules and any measured edge are visible to competitors (owner's choice, 2026-10-25).
- **Regulation:** prediction markets face state challenges, and the CFTC has scrutinised incentive programs; sports contracts are blocked in some states.
- **Key custody on macOS** for an unattended trading service (KAPS PLAN §7, designed in S1.3).

## 8. References

- KAPS: [github.com/swalsh76/KAPS](https://github.com/swalsh76/KAPS) (frozen predecessor; its PLAN §5 and §6 are carried by reference where cited). Local clone `%USERPROFILE%\dev\KAPS`.
- KalshiTerm2: `%USERPROFILE%\dev\KalshiTerm2` (read only). KalTerm3: the tag `kalterm3-final` in the KAPS repository.
- Literature, data sources and the 2026-10-25 review: ASSESSMENT.md.

## 9. Parking lot

- **The web UI** (KAPS's approved design system and mock-ups stay in KAPS; decisions 6, 17, 29 and 35 return with it).
- **Kalshi's Liquidity Incentive Program**: measured (6,560 markets, about $293,000 a day, ends 2027-01-01 at the latest), not relied on (Q4).
- **Perpetual futures:** as a carry trade they are a modest yield (BTC funding about 9% a year since June 2026, ETH about zero); as a **hedge** for a binary quoter in the same account they may matter (crypto, the S&P 500, the Nasdaq 100, metals, oil).
- **Outside fair values with hedges** through Interactive Brokers (Fed meetings against Fed funds futures, index ranges against options).
- **Weather brackets against live station observations:** proposed as **S2** (§6.2, 2026-10-27).
- **Late-game sports mispricing** (Moshrefi, 2026).
- **Language-model forecasts for the thin long tail**, forward-tested.
- **KAPS's proposed K0.6 flow spike** (signals from the trade tape).
- From KAPS's parking lot, still relevant: a redundant ingest, a standby database, secure remote access, MLflow, the ingest storage ideas, and the Developer Agreement points the owner set aside.
- **Closed, with evidence (ASSESSMENT §7):** bracket-sum arbitrage inside events, parlays of 2 to 4 legs, quoting combos by RFQ, millisecond speed races.
