# Lessons kept from KalTerm3

> **Carried from KAPS on 2026-10-25.** The sections up to "Running unattended" are KalTerm3's lessons as KAPS kept them; "What KAPS measured" adds what KAPS itself measured before it was frozen, and the last section, "What SPARQ measured", what SPARQ has measured since.

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

## What SPARQ measured (from 2026-10-25)

- **The middle tier does not pay a resting maker (S1.1, pre-registered, NO-GO).** Four rules were chosen on the validate part and tested once on the held-out part, each over at least 300 events. The middle-activity maker (R1) made -0.09 cents a contract (lower bound -2.00). The human-paced maker (R2) made +2.71 (-3.66). The same below the busiest series (R4) made +2.51 (-1.30). The first-trade taker (R5) made -1.29 (-3.98). The validate-part leads of ASSESSMENT §7.4 did not survive, as KAPS's had not.
- **An order that joins after a trade is adversely selected (S1.2a, the live book).** Among the forward rule's trades, only 11.1% of the 43,742 levels takers cleared were refilled within 1 s, so bots do not requote after every trade. But an order joining 1 s after a trade fills about a third of the time. Its fills lose 0.32 cents by 60 s and 2.91 cents by 10 minutes, before fees, and they lose at 60 s and 10 minutes in every activity tier. A late joiner is filled mostly when the price is about to move against it, so plain spread capture is closed to SPARQ. An edge needs a fair value better than the book's.
- **Public observations come too late to take stale quotes on weather (S2.2, exploration).** On Kalshi's daily-high and rain markets, the station's hourly and special reports settle a market only after the book has priced it. The certain side last traded below 95 cents a median 4.6 hours before the report-based call for the highs, and 40 minutes before it for rain. Takers could have bought the certain side after the call in 19 of 2,240 called markets on the fit part ($0.65 a day at the lower bound) and in 1 of 532 on validate. What trades after the call is holders selling certain wins at 99 cents to resting bids (8.7 M contracts in 53 days). The settled high is above the highest hourly report on 63% of fit-part city-days (54% on validate), mostly by 1 or 2 °F, so the hourly reports also understate the climate value.
