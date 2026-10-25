# SPARQ plan

**Status:** S0 is done. S1 was approved (2026-10-25); its first slice, **S1.1, returned a NO-GO** (`docs/research/middle-tier.md`), so under D3 S1 stops for a rethink, which waits for the owner. This document is the source of truth for the direction, the architecture and the build order. [ASSESSMENT.md](ASSESSMENT.md) holds the reasoning and the sources; [LESSONS.md](LESSONS.md) holds what the predecessors measured. SPARQ carries much of KAPS's design by reference: where this plan says "KAPS PLAN §x", read [KAPS's plan](https://github.com/swalsh76/KAPS/blob/main/docs/PLAN.md) at that section.

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
| **S1** | **Evidence for the quoter** (§6.1): S1.1 the pre-registered middle-tier test; S1.2 the shadow quoter; S1.3 the write path, risk layer and kill switch on demo; S1.4 the tiny live pilot | Each slice's gate in §6.1; a slice starts only when the one before it passes | approved 2026-10-25; **S1.1 NO-GO**; S1.1b's forward test registered; S1.2a under way in parallel (D11) |

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

**Slices.**

| Slice | What it builds | Gate | Status |
|---|---|---|---|
| S1.1 | The pre-registered middle-tier test: the rules from D2 coded on KAPS's study harness, the rule list for the owner's approval, the test part opened once, the write-up in `docs/research/` | D3 | **done: NO-GO** (BUILD-LOG S1.1, `docs/research/middle-tier.md`) |
| S1.2a | What the live book shows (D11): read-only queries over the ingest's data in the forward rule's markets and the busy ones for comparison: quoted spreads and top sizes, a queue-aware fill rule against S1.1's strict rule, markouts at 5 s, 1 min and 10 min by taker side, and how fast quotes refresh after a trade | Decides nothing alone; fixes S1.2's fill model and markout settings, and stops before S1.2 if bots already requote after every trade. **Stop rule (owner approved 2026-10-25, before any reading):** among the forward rule's own trades, if more than 50% of the levels takers cleared (took at least the size shown at the best price, or traded beyond it) are shown refilled at the same price or better within 1 s of the trade, S1.2 is not started; fewer than 300 cleared levels is "too few" and decides nothing. Read once, on the UTC days 2026-10-25 and 2026-10-26 together | **in progress** (branch `s1/2a-live-book`) |
| S1.2 | The shadow quoter: the order-book feed for chosen markets, fair value v0, simulated quotes and strict fills, markouts, a 10-day run | D5 | not started (after S1.2a, D11) |
| S1.3 | The write path, risk layer and kill switch on demo, with the key custody design | Proven on demo: every route, every limit refusal, the kill switch, an unclear-order look-up | not started |
| S1.1b | R4 refined on the fit and validate parts (D10), the chosen rule pre-registered, the forward days downloaded after the window closes, the forward test opened once, the write-up | D10 | **registered 2026-10-25** (digest `0af9ea44be7f...`, `docs/research/forward-test.md`); the verdict after the window closes on 2026-11-23 |
| S1.4 | The tiny live pilot | D8 | not started |

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
- **Weather brackets against live station observations** (they settle on The Weather Company's station value).
- **Late-game sports mispricing** (Moshrefi, 2026).
- **Language-model forecasts for the thin long tail**, forward-tested.
- **KAPS's proposed K0.6 flow spike** (signals from the trade tape).
- From KAPS's parking lot, still relevant: a redundant ingest, a standby database, secure remote access, MLflow, the ingest storage ideas, and the Developer Agreement points the owner set aside.
- **Closed, with evidence (ASSESSMENT §7):** bracket-sum arbitrage inside events, parlays of 2 to 4 legs, quoting combos by RFQ, millisecond speed races.
