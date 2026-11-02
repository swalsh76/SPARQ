# Lessons kept from KalTerm3

> **Carried from KAPS on 2026-10-25.** The sections up to "Running unattended" are KalTerm3's lessons as KAPS kept them (with facts SPARQ measured live added to "Kalshi API facts"); "What KAPS measured" adds what KAPS itself measured before it was frozen; "What SPARQ learned" keeps SPARQ's lessons, each in a line with the study that holds its figures. SPARQ's results themselves are in [the evidence ledger](README.md#the-evidence-ledger).

KalTerm3 (.NET 10, SQL Server 2025, WPF) was built through its milestones M0 to M7 in this repository and then retired by the owner on 2026-10-21. Its code is not used by KAPS. These are the measured facts and hard-won rules worth keeping.

The whole tree is at the git tag **`kalterm3-final`**. A citation such as `BUILD-LOG.md:1213` means `git show kalterm3-final:docs/BUILD-LOG.md`, at that line.

## Durability

- **Consumer drives can acknowledge writes they have not stored.**
  - On the workstation's WD SN580 with its write cache on, write-through writes were acknowledged in about 20 µs, while the flash needs about 200 µs to commit one.
  - An explicit flush (about 220 µs) does reach the flash. A UPS does not close this gap, because a crash or reset still loses the cache.
  - Source: BUILD-LOG.md:58-94.
- **The server host's drive did not show that signature.** It took about 0.3 ms per write either way, which is the speed of a real flash commit. An explicit flush took 1.1 to 1.9 ms. Source: BUILD-LOG.md:296-313.
- **Power-cut protocol (S-1d), to be repeated for PostgreSQL in K1:**
  - **Writer.** It runs on the machine under test. Each row is `(id, SHA-256 of the id)`, written as a mix of single-row and 100-row transactions, with frequent checkpoints.
  - **Ledger.** Every acknowledged commit id goes immediately to a ledger **on another machine**.
  - **Detection.** The ledger detects a silent power loss by a read timeout, not by a connection event.
  - **Verification after each cut.** An integrity check; every acknowledged id is present with a valid checksum, checked against every ledger so far.
  - **Tests of the test.** A self-test first: the verifier must catch a deleted row, a corrupted payload, a fallen row count and a zeroed page. Plus a negative control: a forged ledger must fail.
  - Source: BUILD-LOG.md:319-367.
- **Result on the server host under SQL Server.** Three cord pulls lost 0 of 42.8 M acknowledged rows, with clean integrity checks; the machine was usable again about 30 s after power-on. The limits: three cuts is evidence, not proof; one unbranded drive; operating system and data on the same drive. Source: BUILD-LOG.md:339-362.
- **BIOS setting.** Set the server host BIOS `AC Recovery = Power On`, so it comes back by itself. Source: BUILD-LOG.md:286.

## Kalshi API facts (measured live)

- **Trades endpoint time bounds.** `max_ts` on `GET /markets/trades` is **exclusive** and `min_ts` is inclusive. Trade times carry milliseconds, but the filter takes whole seconds. Slicing at `end - 1 s` loses a second of trades at every boundary. Source: BUILD-LOG.md:1213-1218.
- **Backfill duplicates.** A backfill must never duplicate trades. Skip any `(market, trade_id)` that is already stored or was returned by the previous slice; duplicates were the reference's worst bug. Source: BUILD-LOG.md:1200-1205.
- **Listing markets:**
  - Open non-combo markets: `status=open&mve_filter=exclude`, 1,000 a page; about 122 requests and 18 to 32 s for about 122 k markets.
  - Incremental passes: `min_updated_ts`, with a 2-minute overlap. Adding `status=open` to it gives **HTTP 400**.
  - Statuses seen: `active`, `closed`, `determined`, `finalized`, `inactive`, `initialized`.
  - Source: BUILD-LOG.md:944-964.
- **Settlement gap.** KalTerm3 ignored closed or settled markets it had never seen open, so it holds **no outcome history**. KAPS must backfill outcomes from `/historical/markets`. Source: BUILD-LOG.md:959-961.
- **Category not stored.** The event and series category was parsed but never stored; only `series_ticker` reached the database. Source: `src/KalTerm.Kalshi/Models/EventAndSeries.cs:31-41`.
- **Order-book deltas were not persisted.** Only the latest book per market was kept. A book rebuilt from deltas matched REST 3 of 3 times. Source: PLAN.md:167, BUILD-LOG.md:1138-1160.
- **Row times.** A WebSocket row's time is the server's `ts_ms` when it is positive and plausible; otherwise it is the receive time. Source: BUILD-LOG.md:978.
- **Downtime is lost data** for ticker updates and order books. Trades can be backfilled, and each gap is logged. Source: BUILD-LOG.md:1196-1197.
- **The order-book channel (SPARQ S6, measured live 2026-11-02):**
  - Kalshi keeps **one subscription per channel and link**: a later subscribe answers `ok` with the existing `sid` and joins its markets to it (no refusal up to 50,000 markets a command and 110,000 in all); the reply lists every market of the channel, about 3 MB, so the client's frame limit must allow it.
  - Every book message carries `clock`, but `clock` is shared and steps unevenly, so it cannot place a lost change: a gap is found by `seq` alone, and **every change is applied in `seq` order, none left out by its `clock`** (Kalshi's rule). A book rebuilt so equals a fresh snapshot exactly.
  - **`get_snapshot` gets no reply**; the snapshots follow on the subscription and continue its `seq`. **Adding markets to a subscription sends their snapshots by itself.**
  - A change's `ts` is an RFC 3339 text (a trade's is whole seconds); a reconnect restores in under a second with every snapshot sent again and `seq` starting over.
  - About 5,000 to 7,500 book messages a second over about 100,000 thin markets, concentrated in a few hundred in-play books.
- **An empty side of the book is a 0-cent bid or a 100-cent ask with no size, never a null,** in the ticker channel's frames (SPARQ S3.3, 2026-10-29: 9% of frames carry an empty bid and 3% an empty ask, mostly in thin ladders). Read a side of size zero as missing, or every one-sided book becomes a 100-cent spread with a mid.

## Data volumes (production feed, 2026-10-18 to 10-20)

- About 25,000 ticker rows and 4,000 trade rows a minute, 0 dropped; the process used about 180 MB. About a third of feed messages are combo markets (`KXMVE…`). Source: BUILD-LOG.md:1043-1045.
- 121,880 open non-combo markets in 12,847 events. Source: BUILD-LOG.md:946.
- In one hour there were 2.27 M ticker rows over 45 k markets, which is about 465 k market-minutes with ticks and 30 k with trades. Raw ticker grew about 2.2 GB a day in an ordered columnstore. Source: PLAN.md:201-202.
- About 200 k markets had a ticker update or trade in 24 hours, and 34 k of them traded. Source: PLAN.md:246-248.
- **Retention:**
  - Drop whole days (partitions or chunks); never `DELETE`.
  - Measure the space actually **used**, because dropping frees pages without shrinking files.
  - Never drop the newest 3 days.
  - Source: BUILD-LOG.md:1176-1189.

## Trading (measured on the real account under tiny limits)

- **Create orders** with `POST /portfolio/events/orders`, where `side` is `bid` or `ask` on the YES book. `time_in_force` and `self_trade_prevention_type` are required. `GET /portfolio/events/orders` returns **404**, so list orders with `GET /portfolio/orders`. Source: BUILD-LOG.md:1461-1486.
- **Collateral sits on exchange shards.** An order does not borrow from another shard: an unfunded shard gives `400 insufficient_balance`. A resting order does not reduce a shard's balance. Source: BUILD-LOG.md:1633-1640.
- **Kalshi's resting-order list can lag a cancel by seconds.** A kill switch must re-check until the list is empty on two looks. Source: BUILD-LOG.md:1634.
- **The real taker fee** is the modelled fee rounded up to $0.0001. Source: BUILD-LOG.md:1635.
- **Never resend a transfer.** Kalshi does not de-duplicate on `client_transfer_id`; the same request sent twice moved the money twice. After an unclear answer, read the balance first. Source: PLAN.md:277-278.
- **Never retry an order write after an unclear answer.** Look the order up by its `client_order_id`; if that is still unclear, report "outcome unknown" and stop. Retry only a 429. Source: PLAN.md:275-276.

## Running unattended

- **Test every service after a real reboot.** KalTerm3's ingest did not come back after the first reboot test. Source: BUILD-LOG.md:1050-1060.
- **Bound every wait, log what was skipped, and refuse to start when a prerequisite is missing.**
- **Sync deployed files by hash, not by size.** Copying by size missed changed files of equal size. Source: BUILD-LOG.md:994.

## What KAPS measured (2026-10-21 to 2026-10-25)

- **Durability on the server host:** four real cord pulls under write load with PostgreSQL 18 lost 0 of about 14.2 million acknowledged rows (KAPS K1a.3). Backups and a point-in-time restore into a new cluster were proven (K1b.1).
- **Kalshi trade queries:** `min_ts` is inclusive and `max_ts` exclusive on both the live and historical trade endpoints, judged on the trade's whole second; the largest page is 1,000. Unauthenticated, historical trades hold 10 requests a second; the live trades and market listings about 4 after a burst of about 12.
- **The WebSocket feed:** ticker frames carry no `seq`, trade and lifecycle frames do, and `sid` and `seq` restart on every reconnect. REST trade times have microseconds and the stream's `ts_ms` is that time cut to the millisecond, so stored trade times are cut to the millisecond from both sources (verified on 1,433 of 1,433 trades).
- **The ingest** wrote about 967 rows a second over all non-combo markets with about 40 times headroom, and through restarts, `kill -9`, a database restart and three real network outages it backfilled exactly Kalshi's trades for every gap.
- **History:** at 18 GB on disk for 77 trade days (773.5 million non-combo trades; 113 million combo trades skipped), a full join of the trades to the market table costs about 71 GB of temporary spill. Polars spills to the system temp directory, which once filled the analytics node's internal disk: big runs set `TMPDIR` on the external drive and cap the spill.
- **Studies:** about 1.1% of every day's trades are in markets still open, so the study window rule is "the baseline share plus half a point", not a fixed share. A uniform small sample makes cells of a few dozen events look spectacular; a stratified sample with about 300 events per segment showed them to be noise. Every lead chosen on the validate part fell back to noise on the held-out part.
- **Listings:** Kalshi's market listing is dominated by combo markets, so a full scan goes through the event listing with nested markets (about 80 requests for all open events), not the market listing.

## What SPARQ learned (from 2026-10-25)

Each lesson in a line; the figures are in the study named.

- **A lead chosen on the validate part is not a result.** All four middle-tier rules failed the pre-registered test (S1.1, [middle-tier.md](research/middle-tier.md)), as KAPS's leads had.
- **A maker that joins after a trade is picked off.** Its fills lose 0.32 cents by 60 seconds and 2.91 by 10 minutes before fees, in every activity tier (S1.2a, [history/S0-S1.md](history/S0-S1.md)); an edge needs a fair value better than the book's.
- **Public observations come after the book has priced them.** Weather books priced a bound hours before the station reports proved it (S2.2, [weather-explore.md](research/weather-explore.md)).
- **A queue at a certain price is rationed on the way up and filled whole on a reversal** (S3.1, [certainty-dump.md](research/certainty-dump.md)).
- **Related markets answer within seconds, and the quotes that lag are right;** taking them loses to fees (S3.2, [book-lag.md](research/book-lag.md)). A maker's use of related markets is unmeasured.
- **A wide spread is revenue to the makers already at the touch, not money left unclaimed.** A screen on gross half-spread passes easily and says nothing of edge (S3.3, [thin-tail.md](research/thin-tail.md); the fresh-eyes review).
- **Marks to the mid flatter a maker; settlement is the reading.** S4.1's paper quote looked rich at the mid and its smoke run at settlement lost 3.1 cents a contract (S4, [paper-quoter.md](research/paper-quoter.md), BUILD-LOG S4.2).
- **A prompt can leak the outcome.** "The day before the market closes", with the real close time, hinted the result for markets that close when their event happens; the prompt gives the date alone (S5.2, BUILD-LOG S5.2).
- **Method:** read an empty side of size zero as missing; treat a quote seen before a feed outage as unknown until its next frame; sort stably wherever "previous" decides a result, or two runs differ (S3.2, S3.3).
