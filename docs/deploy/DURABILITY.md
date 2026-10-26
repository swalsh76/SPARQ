# The power-cut test (K1a.3)

This proves, on the server host's own drive, that PostgreSQL loses no commit it has acknowledged when the power is cut under write load. It repeats the protocol that SQL Server passed (docs/LESSONS.md, S-1d): a writer commits rows, a **ledger on another machine** records each acknowledged commit, and after every cut a verifier checks that the database is sound and that every acknowledged row is there.

It writes only to the **test cluster** (`sparq_test`, port 5433). That cluster has the same drive, the same managed settings (`fsync`, `synchronous_commit`, `full_page_writes`, data checksums, all on) and the same PostgreSQL build as production, and the code refuses any database that is not a test database.

**Pass rule.** Zero acknowledged rows lost across all cuts, every integrity check clean after each cut, and both clusters back online by themselves each time. A single lost row is a stop: nothing else is built on this drive until it is understood.

## What you need

- The server host, with you at it (to pull the cord) and a second terminal on it (SSH from the workstation is fine).
- The analytics node, running the ledger. It stays up while the server host loses power.
- Firmware **AC power recovery = power on** (SERVER.md step 1), or you press the power button after each cut.
- The repository checked out on the server host at the branch or release being tested, with `uv sync --package sparq-durability` run once (the test is a package of its own, `sparq-durability`, which `install` never puts on the server).

Words in angle brackets are values to replace. The writer and the verifier connect over the local socket as `root`, which the test cluster's user map turns into its superuser; so the commands below are run with `sudo`.

## 0. Before the first cut

**Measure the flush path** (no root needed). The numbers go in the BUILD-LOG:

```sh
cat /sys/block/nvme0n1/queue/write_cache             # what the kernel believes about the drive's cache
/usr/lib/postgresql/18/bin/pg_test_fsync -f /var/tmp/fsync.test -s 5; rm /var/tmp/fsync.test
```

Read it this way. A drive that acknowledges writes without storing them shows synchronous writes far faster than flash can commit (the workstation's drive: 20 µs against about 200 µs). A drive that honours its flushes shows `open_datasync` near flash speed (about 0.2 to 0.3 ms) and `fdatasync`/`fsync` slower still (about 1 ms). This is evidence, not proof; the cuts are the proof.

**Run the self-tests** on a machine with a scratch PostgreSQL (CI does this on every change; they are the `db` tests whose names begin `test_durability`). They show that the verifier catches a deleted row, a corrupted payload, a fallen row count, a zeroed page, a flipped byte in a page, and a forged ledger.

## 1. Start the ledger (on the analytics node)

The ledger needs only Python 3.12 or later and the standard library. It listens on one address, writes one file per run into its data directory, and notes in the file when a connection goes quiet.

```sh
PYTHONPATH=<checkout>/packages/sparq-durability/src python -m sparq_durability ledger --directory <ledger-data> --host <analytics-node-address> --port <port>
```

Leave it running for the whole test. Keep the data directory: it is the evidence.

## 2. One cut (repeat for runs 1 to 5)

On the server host, in the repository. Use a **new run number each time** (the writer refuses one it has seen):

```sh
sudo env "PATH=$PATH" uv run --package sparq-durability python -m sparq_durability writer \
  --url 'postgresql+asyncpg://postgres@/sparq_test?host=/var/run/postgresql&port=5433' \
  --run <N> --ledger-host <analytics-node-address> --ledger-port <port>
```

It prints a line every five seconds with the commit count and the commit latency. Let it run for a minute or so (long enough for several checkpoints), then **pull the power cord**. Do not shut down. Wait ten seconds, reconnect the cord, and let the machine start.

When it is back (the guide's `verify` and `sparq-admin install` need no help; the clusters start by themselves):

1. Check the machine came back alone: `pg_lsclusters` shows `main` and `sparq_test` online; `sudo env "PATH=$PATH" uv run --package sparq-server sparq-admin verify --answers ~/install.toml` passes.
2. Copy the ledger directory to the server host (from the analytics node: `tar -C <ledger-data>/.. -c <dir-name> | ssh <server> tar -x -C ~/ledger-copy`), or read it in place.
3. Verify against **every ledger so far**:

```sh
sudo env "PATH=$PATH" uv run --package sparq-durability python -m sparq_durability verify \
  --url 'postgresql+asyncpg://postgres@/sparq_test?host=/var/run/postgresql&port=5433' \
  --ledger-directory <copy-of-ledger-data>
```

It must end `All checks passed.` with `0 missing, 0 with a wrong checksum`. Note, for the record: the number of acknowledged rows, the number of rows committed after the ledger's last entry (allowed: the cut fell between a commit and its report), the time from power-on until the clusters were online, and the silent-connection note in the ledger (it shows the ledger saw the loss).

Then go back to the start of this step with the next run number.

## 3. Afterwards

Keep the ledger files with the results. Remove the scratch table with `DROP TABLE durability_rows` in `sparq_test` when the test is over. The BUILD-LOG records each run: its number, acknowledged rows, rows lost (must be 0), the time to recover, and anything unusual.

## Record of the first series

The first series (2026-10-23) was four power cuts under write load and two clean stops, 0 acknowledged rows lost; see BUILD-LOG K1a.3. A run stopped with Ctrl+C is a clean stop, not a cut: the ledger says "closed by the writer" instead of "silent".

## What a failure means

| Result | Meaning |
|---|---|
| `acknowledged rows: N missing` | A commit was acknowledged and then lost. The drive or the settings do not keep the promise. Stop; do not rely on this drive. |
| `with a wrong checksum` | The row is there but its content changed. Treat like a loss. |
| `invalid page` / `page checksums` / `table and index structure` | A page is damaged. The data checksums caught it. Stop. |
| `database: cannot connect` | The cluster did not come back by itself. That is a failure of unattended operation: read the PostgreSQL log, fix it, and note it. |
| `ledgers: no acknowledged commits` | The ledger was not running or got nothing; the cut proved nothing. Run it again with a new run number. |
| `REFUSING: ... is not a test database` | The URL names a production database. Nothing was done. |
