# SPARQ's documents

## The map

| Document | What it holds |
|---|---|
| [PLAN.md](PLAN.md) | The source of truth: the goal, the decisions, the build order, the open milestones' plans and the parking lot (§9) |
| [BUILD-LOG.md](BUILD-LOG.md) | How each slice of an open milestone was built, verified and reviewed, with its approved plan once done |
| [research/](research/) | One write-up per study (its data snapshot, method, figures and limitations) and the registered rules files, whose digests tests pin |
| [history/](history/) | Each closed milestone's plan and build log, moved there verbatim |
| [LESSONS.md](LESSONS.md) | Measured facts and lessons carried forward (KalTerm3, KAPS, SPARQ) |
| [ASSESSMENT.md](ASSESSMENT.md) | Machines, data, frameworks, the literature, and the 2026-10-25 review that led to SPARQ |
| [deploy/](deploy/) | Each production role's install, recovery and durability guides |

**One home per fact.** A study's figures live in its write-up; a build's in the BUILD-LOG; a decision in the PLAN. Other documents give the outcome in a line and link to it, so a correction is made in one place.

## The evidence ledger

Every study and test SPARQ (and KAPS before it) ran, newest last. "Outcomes read" is what each one opened of the markets' results, so a later test knows what is still clean (PLAN M3). "Trust" is the fresh-eyes review's verdict (2026-11-02, BUILD-LOG).

| Study | The question | Kind | Outcomes read | Result | Trust | Where |
|---|---|---|---|---|---|---|
| K0.5 (KAPS) | Is the price's calibration an edge? | Pre-registered | History to 2026-10-22 | **NO-GO:** a band correction moved the Brier score by 0.0001; no segment had an after-fee edge | Trust | [KAPS edge-spike.md](https://github.com/swalsh76/KAPS/blob/main/docs/research/edge-spike.md) |
| S1.1 | Do four middle-tier rules pay? | Pre-registered (`95eec43a8d2d…`) | Events closing 2026-10-10 to 10-18, opened once | **NO-GO:** R1 −0.09 cents a contract (lower −2.00), R2 +2.71 (−3.66), R4 +2.51 (−1.30), R5 −1.29 (−3.98) | Trust the NO-GO; too weak to rule out R2 or R4 | [middle-tier.md](research/middle-tier.md) |
| S1.1b | Does R4 hold on new days? | Registered (`0af9ea44be7f…`) | None: never opened | **Dropped unopened** (D13) | Unsupported either way; still clean | [forward-test.md](research/forward-test.md) |
| S1.2a | Does the book refill after a trade, and how does a joiner fare? | Measurement with a stop rule | None (marks to 10 minutes) | 11.1% of cleared levels refilled within 1 s ("go on"); a joiner 1 s after a trade loses 0.32 cents by 60 s and 2.91 by 10 minutes | Trust the figures; they cover joiners, not resting makers | [history/S0-S1.md](history/S0-S1.md) |
| S2.1 | Can station reports call weather markets? | Measurement | Fit part (to 2026-10-10) | 0 wrong calls of 2,243; reports usable 10 minutes after the observation | Trust | [weather-sources.md](research/weather-sources.md) |
| S2.2 | Can a taker buy the certain side after the call? | Exploration | Fit and validate parts | $0.65 a day at the lower bound on fit, $0 on validate: **no rules** (W9) | Trust | [weather-explore.md](research/weather-explore.md) |
| S3.1 | Does a bid in the 99-cent queue pay? | Measurement against a bar | History to 2026-10-10 | Cleared as worded in Mentions alone (+$39 a day on 28 reversals), not pursued; the whole queue nets about $240,000 a day, 85% to crypto bots | Trust the direction | [certainty-dump.md](research/certainty-dump.md) |
| S3.2 | Does taking related markets' stale quotes pay? | Measurement against a bar | None | **Below the bar:** related markets answer within seconds; taking loses to fees | Trust for taking; a maker's use unmeasured | [book-lag.md](research/book-lag.md) |
| S3.3 | How much half-spread crosses thin books, and are their prices biased? | Measurement against two bars | History to 2026-10-10 (the surprise rate) | About $499,000 a day of gross half-spread in uncontested thin books (Sports $404,000), already earned by the wide makers; 14 of 313 series off their prices where 7.8 would be by chance | Trust as a description, not as an opportunity | [thin-tail.md](research/thin-tail.md) |
| S4.1 | What would a quote inside thin books earn? | Exploration, marked to the mid | None | $12,297 a day at the close mid on 10-contract quotes; four rules registered (`6c7a87357267…`) | Not an edge (mid marks) | [paper-quoter.md](research/paper-quoter.md) |
| S4.2 smoke | Does the test's code run? | Code check on three fit-part days | 2026-10-25 to 10-27 (a deviation from T5) | The uncontested rules lose at settlement: R1 −3.1 cents a contract | Trust the sign | BUILD-LOG S4.2 |
| S4.2 | Do the four registered rules pay at settlement? | Pre-registered | 2026-10-30 to 11-05, read once on or after 11-13 | Pending; what a NO-GO means is written first (PLAN S4.2) | Sound design | PLAN §6.4 |
| S5.1 | Can the model read a market's rules into fields? | Measurement against bars | None | Every bar cleared, no miss on 11,825 markets | Trust, for templated rules | [model-reading.md](research/model-reading.md) |
| S5.2 | Does the model add to the price? | Registered bars (N6) | History to 2026-10-10 | Run under way (due 2026-11-03); the model's knowledge cutoff to be probed first | Pending | PLAN §6.5 |
| S6.1 | What does the book channel carry? | Live measurement, one hour | None | One subscription per channel; `clock` on every message; 5,042 messages a second | Trust | BUILD-LOG S6.1 |
| S6.2 | Does the recorder rebuild the book? | B5's bar on one hour | None | Being checked | — | BUILD-LOG S6.2 |
