# Recovering the database host

This guide gets the database back from the backups on the share: after a mistake (a bad migration, a deleted table), after a damaged data directory, or onto a replacement machine.

**What has been proven, and what has not.** K1b.1 proves a restore of a backup and its WAL into a **new cluster on the same host**, to a chosen moment, with the result compared with what the live cluster held (`restore-drill`, SERVER.md step 12, and the writer-and-ledger proof in BUILD-LOG K1b.1). Rebuilding a **blank replacement machine** end to end, with its time measured against the 4-hour recovery target, is the K8 drill. Until then, the replacement-host section below is a plan to be followed carefully and corrected by that drill, not a tested procedure.

## What you need

- The backup share and its credential (SERVER.md step 4).
- The backup **passphrase**, from where you keep it offline. Without it, nothing can be restored.
- The answer file the host was installed with (`~/install.toml`; keep a copy off the machine).
- The Kalshi data-only key, re-entered by hand: keys are never in the backups.

## A. Undo a mistake: restore to a moment, into a new cluster

This leaves the live cluster alone. It makes a second cluster that you can look at, and take rows or tables from.

```sh
sudo env "PATH=$PATH" uv run --package sparq-server sparq-admin restore-drill --answers ~/install.toml \
  --cluster main --target-time '2026-10-24 11:30:00+00' --keep
```

`--target-time` is the moment you want, in UTC. The drill cluster is `drill_main` on port 5442 (localhost only; it never archives). Look at it, copy what you need back:

```sh
sudo -u postgres psql -p 5442 -d sparq
```

When you are done, `sudo env "PATH=$PATH" uv run --package sparq-server sparq-admin restore-drill --answers ~/install.toml --cleanup`.

The restore stops at the last commit at or before the moment. Anything after it is not there, so choose a moment just before the mistake.

## B. Replace the damaged data directory in place

Use this only when the live cluster is damaged beyond use. It destroys the live data directory's contents and replaces them with the restore, so be sure.

1. Stop the cluster: `sudo pg_ctlcluster 18 main stop`.
2. Restore the newest backup and replay all archived WAL:
   `sudo -u postgres pgbackrest --stanza=main --delta --type=default restore`
3. Start it: `sudo pg_ctlcluster 18 main start`, wait until `pg_isready` says it accepts connections, and run `sparq-admin verify`.
4. Take a new full backup at once: `sudo -u postgres pgbackrest --stanza=main --type=full backup`.

The data lost is whatever was committed after the last archived WAL segment: at most about a minute on a busy cluster, longer if the share was down (see SERVER.md step 12).

## C. A replacement machine (to be proven in K8)

1. Install Ubuntu and follow SERVER.md steps 1 to 7 on the new machine, with the same answer file. Mount the share (step 4).
2. Install the packages only, and put the **old** passphrase back before anything touches the repository, because the new install would otherwise make a different one:
   `sudo install -d -m 0750 -o postgres -g postgres /etc/pgbackrest/conf.d` then write `/etc/pgbackrest/conf.d/10-cipher.conf` with `[global]` and `repo1-cipher-pass=<the old passphrase>` (mode 0600, owner `postgres`).
3. Do not run `install` yet: its first full backup would collide with the old stanza (a different database identity). First restore as in section B with `pgbackrest --stanza=main --delta restore` into a cluster created with `pg_createcluster 18 main --port 5432`, then run `install` (it will find the cluster and settings and, after `stanza-upgrade`, carry on) and `verify`.
4. **The certificate is new.** The private key never leaves the host (PLAN decision B3), so the replacement makes a fresh certificate; the analytics node pins it again on its first connection. Record the new fingerprint that `install` prints.
5. Re-enter the Kalshi data-only key. The ingest records the downtime as a gap, and trades are backfilled (KAPS PLAN §6.4); ticker and order-book data for the gap cannot be recovered.

Steps 2 and 3 are where a real drill is most likely to find something wrong; that is what K8 is for.
