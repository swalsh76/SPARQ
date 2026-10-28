# KAPS assessment: hardware, data, frameworks and literature

> **Carried from KAPS on 2026-10-25.** Sections 1 to 6 are KAPS's assessment as written for its milestone K0; "KAPS" in them means the predecessor project, and its plan references are to KAPS's plan. Section 7 is the review that led to SPARQ.

Written 2026-10-21 for milestone K0. It answers the owner's starting question: what can be done with the machines we have, how to store and process the data, which tools to use, and what the research literature says about predicting what ordinary Kalshi traders will do.

Figures marked *KalTerm3* were measured by the earlier .NET line. They are kept at the git tag `kalterm3-final`, and [LESSONS.md](LESSONS.md) says where each one came from.

## 1. Summary

- **Compute is not the bottleneck.** All four machines are more than enough for the analytics described here. A gradient-boosted model scores about 200 k markets in seconds, and training on 100 M rows fits in the workstation's memory.
- **Two kinds of data limit us:**
  - **Labelled history.** This is settled markets with their outcomes; we do not have it yet.
  - **Order flow.** The changes to each market's order book are not stored yet.
  - Kalshi offers the history through its `/historical/*` endpoints. The best bid and ask with their sizes, which the feed already carries, are enough for the first order-flow signals on every market.
- **The literature supports a careful plan:**
  - Kalshi prices are systematically miscalibrated, in ways that depend on price level, time to expiry, category and trade size.
  - Order-flow imbalance predicts short-term price moves.
  - Liquidity makers earn money and liquidity takers lose it.
  - These findings point to a **recalibration model** and an **order-flow model**, used mainly through **resting (post-only) orders**. Every result has to be tested out of sample, with fees included.
- **Roles:**
  - The server host is the **system of record**: PostgreSQL 18 + TimescaleDB on Ubuntu, the ingest, the API and the web front end.
  - The analytics node is the **production analytics node** and runs the **trading service**.
  - Training runs on the analytics node by default; the Windows 11 workstation's RTX 5070 Ti is a selectable offload, manually or on a schedule (PLAN decision 11). Everything else runs on Linux or macOS (decision 5).
  - The laptop is out of the rotation: it is used only to administer the servers remotely and for ad hoc work.

## 2. Machines and roles

| Machine | Facts | Role in KAPS |
|---|---|---|
| **Server host** | Dell OptiPlex 7060: Intel i7-8700 (6 cores, 12 threads), 32 GB RAM, one 1 TB NVMe drive with no power-loss protection, 1 Gbps network. The drive passed 3 real power cuts under SQL Server with its write cache on (*KalTerm3*). | Reinstalled as **Ubuntu 24.04 LTS**. **System of record:** PostgreSQL 18 + TimescaleDB, with `shared_buffers` about 8 GB and the rest left to the operating system's page cache. Also the **ingest**, beside the database, so that a Mac reboot or macOS update never leaves a gap in data that cannot be recovered. Also the **API and web front end**, and backups to the network share. |
| **Analytics node** | Mac Studio (2025, `Mac16,9`): **Apple M4 Max**: 14-core CPU (10 performance and 4 efficiency cores), 32-core GPU, 16-core Neural Engine, **36 GB** unified memory (about 410 GB/s). 512 GB internal SSD, about 184 GB free on 2026-10-21. An **external 2 TB PCIe SSD** on Thunderbolt 5 (80 Gb/s link), almost empty. macOS 26.5. Restart after power loss already on; never sleeps; no UPS. Figures from the owner's System Information report. | **Production analytics node:** scheduled feature jobs, model scoring and recommendations, written back to Postgres on the server host. Also the **trading service**: the risk layer, order execution, automated rules and synthetic orders. The trading key lives here only. It is also the **default training machine**, running training at background priority with a memory cap (PLAN §5.1). The working set (Parquet, DuckDB, model files) goes on the external SSD, not the database. Services check that the drive is mounted and refuse to start if it is not (a KalshiTerm2 lesson). |
| **Workstation** | AMD Ryzen 7 9800X3D (8 cores, 16 threads), 64 GB RAM, NVIDIA RTX 5070 Ti with 16 GB, 2 TB disk; Windows 11. | The owner's everyday machine and an **optional training offload** on the RTX 5070 Ti, chosen manually or on a schedule (PLAN §5.1). A worker that the owner starts for chosen hours picks jobs up from the NAS share and writes models and a manifest back; the analytics node verifies and promotes them. It holds no Kalshi key and no database credential. The training code is OS-neutral, so nothing in KAPS depends on Windows. |
| **Laptop** | MacBook Pro (16-inch, 2024, `Mac16,7`): **Apple M4 Pro**: 14-core CPU (10 performance and 4 efficiency cores), 20-core GPU, **48 GB** unified memory (about 273 GB/s), 512 GB SSD (about 382 GB free). macOS 26.6. Battery 91 cycles, 100% health. Figures from the owner's System Information report. | **Out of the rotation** (owner, 2026-10-21): used only to administer the servers remotely (SSH to the server host and the analytics node, the web UI) and for ad hoc work. No KAPS service, data or model depends on it. |

### Why the database is on the server host

- **KalshiTerm2's failure.** KalshiTerm2 ran Postgres in Docker on the analytics node. A power cut corrupted the database, because the Docker VM's flushes reached only the drive's cache.
- **The server host's record.** Its drive showed no sign of acknowledging writes it had not stored, and it survived 3 cord pulls with zero lost commits (*KalTerm3*).
- **What still has to be proven.** The result was for SQL Server on Windows. **It must be measured again for PostgreSQL on Linux** (K1): an fsync latency test, then 3 cord pulls under write load, checked against a commit ledger kept on another machine.

### How much compute the work needs

- **Scoring:** a few hundred trees over about 200 k markets takes seconds on one analytics node core. A 1 to 5 minute recommendation cycle uses a small fraction of the machine.
- **Training on the analytics node (the default):** gradient-boosted trees and calibration fits are CPU work, because LightGBM and XGBoost have no Apple GPU support. The M4 Max's 10 performance cores should be at least a match for the 9800X3D here (an estimate, to be measured in K3). The limit is 36 GB of memory, so training reads sampled extracts from Parquet rather than whole tables, and it shares the machine with the trading service.
- **Training on the workstation (the offload):**
  - LightGBM on 16 threads handles about 100 M rows by tens of features in minutes. XGBoost with CUDA is faster.
  - On the GPU, XGBoost's `QuantileDMatrix` stores about 1 byte per feature bin, so 100 M rows by 50 features need about 5 GB, which fits in 16 GB.
  - PyTorch needs a build for CUDA 12.8 or later to drive the RTX 50 series (Blackwell, `sm_120`). Check it with `torch.cuda.get_arch_list()`. Reports of stable-build support on Windows disagree ([PyTorch forum](https://discuss.pytorch.org/t/cuda-support-for-rtx-50x-on-windows/221309)), so this is the first thing to verify when GPU work starts.
- **On the Macs:** PyTorch's MPS backend and Apple's MLX run small neural networks, and LightGBM and XGBoost run on the CPU there. The analytics node's 36 GB holds tens of millions of rows in Polars, which is ample for scoring and daily features. A run that needs more memory or the CUDA GPU is offloaded to the workstation (64 GB).
- **Memory on the server host:** 32 GB is the tightest budget. Postgres, the ingest and the web server share it. Measure in K1.

## 3. Data and storage

### What the feed produces (*KalTerm3*, production, 2026-10-18 to 10-19)

- About **25,000 ticker rows and 4,000 trade rows a minute**, which is about 36 M ticker rows and 5.8 M trades a day.
- About **120,000 open non-combo markets** in about 12,800 events. About a third of feed messages are for combo markets (`KXMVE…`), which are skipped.
- About **200,000 markets** had a ticker update or a trade in a 24-hour window.
- Raw ticker data in SQL Server's ordered columnstore: about **2.2 GB a day**.

### Storage budget on the server host (1 TB drive; a governed budget of about 700 GB, raised from 500 GB on 2026-10-25)

| Item | Estimate | Note |
|---|---|---|
| Ticker, compressed | About 2 to 3 GB a day | TimescaleDB columnar compression, segmented by market and ordered by time, should approach the columnstore figure. **Measure in K1.** |
| Ticker, hot (uncompressed) chunks | 5 to 10 times larger, for 1 to 2 days | Chunks are compressed once they are no longer written to. |
| Trades | Well under 1 GB a day compressed | |
| Settled history (markets, outcomes, trades, candles) | Tens of GB | Kalshi Research counts about 2.24 M markets since 2021. Kalshi's [historical endpoints](https://docs.kalshi.com/getting_started/historical_data) serve settled markets, candlesticks and trades with cursor paging; an older-than cutoff moves forward over time. |
| Order-book deltas | About 2 GB a day per 50 busy markets (KalshiTerm2) | Not affordable for every market. See below. |

At about 3 GB a day, 500 GB keeps about **5 months** of raw ticker data in the database, plus the history and candles for good. Older raw days are not lost: each day is written to a Parquet cold archive on the NAS share and verified before the database drops it, and the archive keeps 3 years (PLAN.md §6.3). Retention is set from the K1b measurement.

**Measured 2026-10-25 (the ingest's first closed chunks, 2.8 hours of 24 October, compressed by hand):** the ticker compressed 10.5 times (2,962 MB to 282 MB for 11.65 M rows over 87,805 markets, about 24 bytes a row) and trades 6.6 times (625 MB to 95 MB for 2.13 M rows, about 45 bytes a row). These are floors: each market had about 133 rows to compress together, against about 1,000 in a full day. Volumes are far above the estimates above: about 89 M ticker rows and 14 M non-combo trades a day (KalTerm3's 25,000 and 4,000 a minute came from its first minutes and caught about half of that hour's trades; Kalshi's trade count also grew about 65% from mid-August to mid-October). Uncompressed, a day takes about 23.5 GB with indexes; compressed, about 2.8 GB. The drive is 937 GB with 847 GB free, so the owner raised the budget to 700 GB, which lasts about 7.5 months at today's volume and about 4.5 to 5 months if the growth continues. The budget is not enforced yet (the governor was never built): until a retention rule exists, the limits that act are the watchdog's alert at 80% of the drive and the ingest's stop at 90%. The full-day ratio is read after the policy compresses 25 October's chunk (27 October, 08:48 UTC).

### Order-book data

- **Level 1 is already there.** The ticker feed carries the best bid and ask **with their sizes**. That is everything Cont, Kukanov and Stoikov's level-1 order-flow imbalance needs, so it can be computed for **every** market without storing deltas.
- **Deeper data, proposed:**
  - Compute deeper features in the stream for a focus universe, such as the busiest few hundred markets: queue imbalance at several levels, depth, and cancellation rates.
  - Keep raw deltas only for that universe, with short retention.
  - A K2 spike measures the cost first.

### What the old data model lacked (fixed in K1 and K2)

1. **Outcome labels.** KalTerm3 recorded a settlement only for markets it had seen open, so there is no outcome history. The fix is a backfill from `/historical/markets`.
2. **Category.** It was parsed but not stored. The literature shows that calibration depends on category.
3. **Order-book deltas.** These were not persisted.

## 4. Frameworks and libraries

Everything is Python (3.12 or later; the repo pins 3.13). [KalshiTerm2](https://github.com/swalsh76/KalshiTerm2) is the guide: reuse its approach and code where sound, and improve where possible.

| Area | Choice | Note |
|---|---|---|
| Workspace | `uv` workspace, `ruff`, `mypy --strict`, `pytest`, `pytest-asyncio`, `pytest-cov` | As in KalshiTerm2 |
| Kalshi client | `httpx`, `websockets`, `cryptography` (Ed25519), `pydantic` v2 | From KalshiTerm2 `kalshi-core`: fixed-point money, fees, rate limiter, clock skew, REST, WebSocket, order book |
| Database | PostgreSQL 18 + TimescaleDB, `asyncpg` (`COPY` for bulk writes), SQLAlchemy Core, Alembic | Hypertables, columnar compression, continuous aggregates for candles, retention policies. Native packages on Ubuntu; [TimescaleDB supports PG18](https://www.tigerdata.com/docs/get-started/timescaledb-supported-platforms). Its Windows packaging looked stale, which is one reason for Linux. |
| Durability settings | `fsync`, `synchronous_commit`, `full_page_writes` on; data checksums on (the PG18 default) | Proven by power cuts in K1 |
| API and web | FastAPI and uvicorn; self-signed TLS with trust-on-first-use pinning; one owner credential | The front-end framework is chosen in the K6 plan. TradingView Lightweight Charts (Apache-2.0) is a candidate. |
| Research data | Polars, DuckDB, PyArrow/Parquet | A nightly Parquet export through a read-only `research` role |
| Models | scikit-learn (isotonic and Platt calibration), LightGBM, XGBoost, statsmodels; PyTorch (CUDA on the workstation, MPS on the Macs) | Served natively in Python on the analytics node, so no ONNX conversion is needed |
| Backups | pgBackRest (MIT): physical full and differential backups plus continuous WAL archiving to the NAS share | A physical backup copies TimescaleDB's internal chunks as they are. Restore is into a new cluster. Running its repository over an SMB mount is checked in K1 |
| Packaging | `uv build` wheels plus a per-platform wheelhouse and a hash-pinned requirements file, bundled with the standalone `uv` binary | Offline install on a blank machine (PLAN §6.1) |
| Services | `systemd` on the server host; `launchd` on the analytics node | Analytics node: restart after power loss is already on. A UPS is recommended (none today), and the services check that the external SSD is mounted |

**Why not ClickHouse:**
- It is faster for very large scans ([comparison](https://oneuptime.com/blog/post/2026-01-21-clickhouse-vs-timescaledb/view)).
- It would need a second database for users, conditions and orders.
- Our data volume (millions of rows a day) is within TimescaleDB's comfortable range.

**A risk to design for (K7).**
- On macOS, the login Keychain stays locked for a background daemon until a user logs in.
- FileVault blocks an unattended boot.
- So the custody of the trading key on the analytics node needs its own design: the System keychain, or a file readable only by the service account, with the disk-encryption trade-off stated.

## 5. Literature: what predicts what traders will do

### 5.1 Prices are miscalibrated in predictable ways

- **Favourite–longshot bias on Kalshi.** Bürgi, Deng and Whelan, ["Makers and Takers: The Economics of the Kalshi Prediction Market"](https://www.ucd.ie/economics/t4media/WP2025_19.pdf) (UCD WP2025/19; also [CESifo](https://www.ifo.de/en/cesifo/publications/2025/working-paper/makers-and-takers-economics-kalshi-prediction-market)), more than 300 k contracts:
  - Contracts priced under 10¢ lose more than 60% of the money put in. The average return before fees is about −20%. Contracts above 50¢ earn a small positive return.
  - **Makers earn and takers lose.** The bias holds across categories and is shrinking over time.
- **Calibration worsens with time to expiry.** Page and Clemen, ["Do Prediction Markets Produce Well-Calibrated Probability Forecasts?"](https://people.duke.edu/~clemen/bio/Published%20Papers/45.PredictionMarkets-Page&Clemen-EJ-2013.pdf) (Economic Journal, 2013).
- **The final minutes behave differently.** Moshrefi, ["Prices, Probabilities, and Parlays"](https://arxiv.org/abs/2607.14430) (arXiv, 2026), 23 M Kalshi sports moneyline trades:
  - Calibration is near perfect in mid-life, then becomes step-like in the last ten minutes.
  - Cross-game parlays are priced above the product of their legs.
- **Calibration depends on domain and trade size.** Le, ["Decomposing Crowd Wisdom"](https://arxiv.org/abs/2602.19520) (arXiv, 2026), 353 M trades on Kalshi and Polymarket:
  - Political markets are persistently underconfident (prices compressed toward 50%), more so for large trades.
  - About half of the raw variation is estimation noise.
- **Kalshi's own measurement.** [Kalshi Research, calibration study](https://kalshi.com/research/publications/calibration): 2.24 M markets; the Brier score falls from about 0.08 three months out to about 0.02 at close.
- **Why the bias exists.** Snowberg and Wolfers, ["Explaining the Favorite-Longshot Bias"](https://www.nber.org/papers/w15923) (JPE, 2010): misperceived probabilities rather than a taste for risk. That suggests a stable correction that can be learned.

**What this means for KAPS:** estimate the true probability as a function of the price, the time to expiry, the category, liquidity and trade size, learned from settled history. The **edge** is that estimate minus the price minus the fee. Favour resting post-only orders, which collect the makers' side of the trade.

### 5.2 Order flow predicts the next move

- **Order-flow imbalance.** Cont, Kukanov and Stoikov, ["The Price Impact of Order Book Events"](https://arxiv.org/abs/1011.6402) (J. Financial Econometrics, 2014): the imbalance between supply and demand at the best bid and ask explains most short-horizon price changes, with a slope inversely proportional to depth.
- **Queue imbalance.** Gould and Bonart, ["Queue Imbalance as a One-Tick-Ahead Price Predictor"](https://arxiv.org/pdf/1512.03492) (2016): queue imbalance predicts the direction of the next mid-price move, most strongly in large-tick books. Kalshi's 1¢ tick on a 0 to 100¢ price is a large tick.
- **Large trades and cross-venue lead.** Ng, Peng, Tao and Zhou, ["Price Discovery and Trading in Modern Prediction Markets"](https://papers.ssrn.com/abstract=5331995) (SSRN, 2025–26): imbalance among large trades predicts returns, and Polymarket leads Kalshi in price discovery.

**What this means for KAPS:** level-1 order-flow imbalance, queue imbalance, trade aggression (`taker_side`) and large or block-trade imbalance are the first short-horizon features.

### 5.3 Who moves prices

- **An informed minority.** Gómez-Cram, Guo, Jensen and Kung, "Prediction Market Accuracy: Crowd Wisdom or Informed Minority?" (2026; [summary](https://insights.som.yale.edu/insights/wisdom-of-the-few-prediction-markets-are-driven-by-small-number-of-skilled-traders)):
  - About 3% of Polymarket accounts carry order flow that predicts both price moves and outcomes, and the skill persists.
  - Kalshi's public trades are anonymous, so only proxies are available: size, aggression, block trades and timing.
- **Trader types.** Rothschild and Sethi, ["Trading Strategies and Market Microstructure"](https://researchdmr.com/files/JournalofPredictionMarkets2016WP.pdf) (J. Prediction Markets, 2016): a taxonomy of trader types, ranging from arbitrageurs to traders who hold one direction, plus evidence of manipulation.

### 5.4 Reaction to news

- **The theory.** Ottaviani and Sørensen ([paper](https://web.econ.ku.dk/sorensen/papers/pricereactiontoinformation.pdf)) model markets that underreact, then show momentum, then reverse.
- **The evidence.** Direct evidence in prediction markets is thin. Treat it as a hypothesis to test on our own data, not as a signal to rely on.

### 5.5 Where the volume is

- Sports were roughly **45 to 85%** of Kalshi volume in 2026, depending on the week and the source; Blockworks puts it at about **68%** ([Blockworks](https://app.blockworksresearch.com/research/from-betting-to-trading-how-kalshi-is-reshaping-sports-markets)).
- So most labelled data will be sports, and per-category models are needed.

### 5.6 Method guardrails (what keeps us honest)

- **Splits.** Walk-forward splits clustered by event, with purging and an embargo, so information from the same event never leaks across a split (López de Prado, *Advances in Financial Machine Learning*, 2018).
- **Probability scores.** Brier score, log-loss and reliability diagrams for every probability model, always compared with the raw price as the baseline.
- **Fees.** Kalshi's fee model is applied in every backtest.
- **Many strategies tried.** Deflated Sharpe ratios when many strategies have been tried (Bailey and López de Prado, 2014).
- **Paper trading.** A paper-trading record comes before any automated order.
- **The stop rule.** If the calibration baseline (K3) cannot beat the raw price out of sample after fees, we stop and rethink, rather than build further on it.

## 6. Out of scope for now (parking lot)

- Polymarket or sportsbook odds as outside anchors. The literature says Polymarket leads Kalshi, but this means new data sources.
- Language-model features from market rules or news, which could run on the Mac.
- Combo and parlay markets.
- Importing KalshiTerm2's 28 GB of history.
- A second, redundant ingest.

## 7. The 2026-10-25 review: why SPARQ is a maker

Written at the end of KAPS, after its K0.5 study returned a NO-GO, when the owner asked whether the project was on course to profit. All live reads were public GET requests; nothing was placed.

### 7.1 What the evidence says about where the money is

- **The price is already close to the probability.** KAPS's K0.5 study (77 trade days, 7 August to 22 October 2026, stratified to 40 million trades; the write-up is in [KAPS docs/research/edge-spike.md](https://github.com/swalsh76/KAPS/blob/main/docs/research/edge-spike.md)): a bias correction by price band changed the Brier score by 0.0001 on the held-out part; no pre-registered segment had an after-fee edge above zero; a naive maker made +0.91 cents a contract under the optimistic fill rule and lost 0.32 under the strict one.
- **Takers pay makers.** Burgi, Deng and Whelan's 2026 version ("Makers or Takers"): the average return is -31.46% for takers and -9.64% for makers; makers buying contracts at 50 cents or more earn about +2.6%; the authors expect the biases to fade now that they are published. Kalshi's own figures: over 70% of traders were unprofitable over six months (CNBC, May 2026); the Roosevelt Institute puts retail losses at about $583.5 M net to May 2026.
- **The professional firms** (Susquehanna, and Jump with more than 20 event-contract traders) quote the headline markets; on Kalshi's RFQ layer, 13 quoters answer sports requests, almost all within 200 milliseconds (Oddpool's census).

### 7.2 Checked and closed

- **Bracket-sum arbitrage inside events** (a live scan of 14,312 open events): of 5,611 mutually exclusive sets with two or more live markets, one priced to a riskless profit after fees, by 0.12 cents a set.
- **Parlays:** two- to four-leg cross-game parlays are not overpriced (median 0.991 to 0.999 of the product of their legs; Moshrefi 2026); the markup appears only at six legs and above, on small samples. Quoting combos by RFQ is a race between fast bots in which only the best quote is accepted.
- **Simple forecast models for weather:** a public backtest over 1,506 New York events found the market already priced the National Weather Service forecasts; prices move 1 to 3 cents within 10 to 30 minutes of an update, less than a 3 to 3.5 cent round trip (OddsReference, 2026).
- **Millisecond speed races** (spot jumps, live games, data releases): the professional firms are faster than a home connection running Python.

### 7.3 Measured, not relied on

- **Kalshi's Liquidity Incentive Program** (terms in Kalshi's CFTC filing of 2026-07-15; `GET /incentive_programs`): 6,560 markets carried rewards totalling about $293,000 a day on 2026-10-25 (median $14 a day a market). It ends on 2027-01-01 at the latest, Kalshi may end it at any time, Market Maker Agreement holders are excluded, and Kalshi ended its Volume Incentive Program early (no earlier than 2026-10-13). The owner chose not to rely on it (PLAN Q4).
- **Perpetual funding** (`GET /margin/funding_rates/historical`, 6,535 records): BTC funding was positive in 51.6% of 8-hour intervals since 2026-06-03, about 9% a year on average and 14.5% over the last 30 days; ETH about zero. Perps now include metals, oil, the S&P 500 and the Nasdaq 100. As a carry trade, a modest yield; as a hedge for binary positions in the same account, potentially useful.

### 7.4 The middle tier (exploratory: the validate part only, about 25 slices looked at)

From the K0.5 trade tape, with K0.5's fees, sample weights and strict fill rule, and intervals by event:

| Slice | Takers (cents a contract, 95%) | Makers, strict fills |
|---|---|---|
| Busiest series (median market over 100k contracts: BTC 15-minute, MLB and NFL games) | -1.18 (-1.49 to -0.82) | -1.04 (-1.57 to -0.47) |
| **Middle series** (10k to 100k: ITF tennis, college-football and MLB spreads and totals, NFL scorers, 15-minute oil, Trump mentions, rain) | **-4.44 (-10.81 to -1.55)** | **+2.34 (-0.78 to +7.92)** |
| Trades 1 second to 1 hour after the market's previous trade (human pace) | -4.42 to -6.82 | +2.62 to +5.37 |
| Sub-second bursts | -1.37 | -0.96 |
| A market's first trade | +4.66 (+0.03 to +14.37) | -11.48 (-26.06 to -3.60) |
| After more than an hour of quiet | +0.69 | -4.32 (-9.08 to -0.04) |

A live snapshot of 148,971 open non-combo markets: the 209 busiest (over 100,000 contracts in 24 hours) carry 64.6% of the volume at a median spread of 1 cent; spreads widen below them; takers pay about $1.65 M a day in half-spreads across all markets (an upper bound before adverse selection), $0.92 M of it in markets trading 1,000 to 100,000 contracts a day.

**Reading:** a gap exists between human-paced traders and the professional firms, in second-tier markets and in stale quotes; a seconds-scale maker could live in it. These are leads for a pre-registered test, not results: two of the three key intervals cross zero, and KAPS's validate leads all failed on its test part.

**Tested (SPARQ S1.1, 2026-10-25): NO-GO.** The four rules built from these leads, tested once on the held-out part, all had lower bounds below zero: the middle-activity maker -0.09 cents a contract (lower -2.00), the human-paced maker +2.71 (-3.66), the same below the busiest series +2.51 (-1.30), and the first-trade taker -1.29 (-3.98). S1.2a then found that an order joining after a trade is adversely selected, losing 2.9 cents by 10 minutes before fees (SPARQ's LESSONS.md, PLAN D13 and D14).

### 7.5 Venues (US-accessible, October 2026)

| Venue | Automated trading | Fees | Fit |
|---|---|---|---|
| Kalshi | Full API, Ed25519 | Takers about 7% x p(1-p); makers free on most series | The main venue |
| Polymarket US | Official API, Ed25519, 25 requests a second, identity check and approval | Takers 6.95% x p(1-p); makers free with a rebate | A second venue for the maker; cross-venue value with Kalshi |
| Interactive Brokers (ForecastEx, Kalshi and CME event contracts through the TWS API) | Yes | ForecastEx: 1 cent a contract, about 3 to 4% a year paid on positions | Outside fair values with hedges in one account |
| Novig, ProphetX, FanDuel Predicts | No public API | Novig $0 before the game; others about 2% | Price signals only |

Robinhood and Coinbase sell Kalshi's prediction contracts as brokers (not their crypto trading); Robinhood is building its own exchange with Susquehanna, which could move retail flow away from Kalshi.

### 7.6 Sources for this section

- [Burgi, Deng and Whelan, Makers or Takers (GWU, 2026)](https://www2.gwu.edu/~forcpgm/2026-001.pdf)
- [CNBC, May 2026](https://www.cnbc.com/2026/05/05/gen-z-millennials-prediction-markets.html); [Roosevelt Institute](https://rooseveltinstitute.org/blog/since-kalshis-launch-ordinary-users-have-lost-half-a-billion-dollars/)
- [Oddpool: Kalshi RFQ market makers](https://www.oddpool.com/research/kalshi-rfq-market-makers); [Kalshi RFQ documentation](https://docs.kalshi.com/getting_started/rfqs)
- [Moshrefi, Prices, Probabilities, and Parlays (arXiv 2607.14430)](https://arxiv.org/html/2607.14430v1)
- [OddsReference: Kalshi weather model](https://oddsreference.com/predictions/research/kalshi-weather-model)
- [Kalshi: Liquidity Incentive Program](https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program); [CFTC filing, 2026-07-15](https://www.cftc.gov/filings/orgrules/rules07152610358.pdf); [Kalshi API: incentives](https://docs.kalshi.com/api-reference/incentive-programs/get-incentives)
- [Kalshi: perpetual futures](https://help.kalshi.com/en/articles/14679970-what-are-perpetual-futures); [Kalshi API: historical funding rates](https://docs.kalshi.com/margin-rest/funding/get-historical-funding-rates.md)
- [Polymarket fees](https://docs.polymarket.com/trading/fees); [Predictefy: Polymarket US API](https://blog.predictefy.com/polymarket-us-api)
- [Interactive Brokers: event contracts in the TWS API](https://www.interactivebrokers.com/docs/tws-api/contracts/event-contracts.md); [IBKR prediction-market commissions](https://www.interactivebrokers.com/en/pricing/commissions-events.php)
- [Kalshi API: rate limits and tiers](https://kalshi-b198743e.mintlify.app/getting_started/rate_limits); [Kalshi API: create order](https://docs.kalshi.com/api-reference/portfolio/create-order)
- [Avellaneda and Stoikov, High-frequency trading in a limit order book (2008)](https://math.nyu.edu/%7Eavellane/HighFrequencyTrading.pdf)
