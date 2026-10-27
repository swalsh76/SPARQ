"""``sparq-admin restore-drill``: restore the latest backup into a NEW cluster and compare.

A backup nobody has restored is a hope. The drill restores a cluster's backup and WAL archive into
a separate, throwaway cluster (``drill_<name>``, on its own port, never archiving), brings it to a
chosen moment, and compares what it holds with what the live cluster held at that moment. The live
cluster is only read. The drill cluster is removed afterwards unless asked to stay.

With no ``--target-time`` the drill takes its own moment from the live database (a snapshot of the
row counts and the clock, in one transaction) and compares exactly; that is right for a database
nothing is writing to. With a ``--target-time`` (for the writer-and-ledger proof, which checks the
restored rows against the ledger) it restores to that time, leaves the comparison to the caller,
and keeps the cluster so the caller can look at it.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from sparq_server.admin import managed
from sparq_server.admin.answers import DatabaseHost
from sparq_server.admin.pg import as_postgres, clusters, psql, stanza_info
from sparq_server.admin.preflight import Check
from sparq_server.admin.system import CommandFailed, Host, write_if_different
from sparq_server.admin.versions import POSTGRES_MAJOR

DRILL_PREFIX = "drill_"
PORT_OFFSET = 10  # the drill cluster listens on the live port plus this, on localhost only
SPACE_FACTOR = 1.5  # free space wanted, as a multiple of the live cluster's size
RESTORE_TIMEOUT = 6 * 3600.0
RECOVERY_WAIT = 900.0


class DrillRefused(Exception):
    """The drill will not start; the message says why and what to do."""


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The live cluster's row counts and the clock, taken in one transaction."""

    at: str  # a UTC timestamp with microseconds, as pgBackRest wants it for --target
    counts: dict[str, int]


def pick_cluster(config: DatabaseHost, name: str) -> managed.ClusterSpec:
    by_name = {"main": managed.main_cluster(config)}
    if config.test_cluster.enabled:
        by_name["test"] = managed.scratch_cluster(config)
    if name not in by_name:
        raise DrillRefused(f"no cluster {name!r} here; choose from {', '.join(sorted(by_name))}")
    return by_name[name]


def drill_spec(spec: managed.ClusterSpec) -> managed.ClusterSpec:
    return managed.ClusterSpec(
        f"{DRILL_PREFIX}{spec.name}",
        spec.port + PORT_OFFSET,
        "1GB",
        (),
        spec.database,
        None,
        archive=False,
    )


def _parse_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in text.splitlines():
        table, _, number = line.rpartition("|")
        if table and number.strip().isdigit():
            counts[table] = int(number)
    return counts


def take_snapshot(host: Host, spec: managed.ClusterSpec) -> Snapshot:
    """The clock and the row counts of the live database, in one repeatable-read transaction."""
    script = (
        "BEGIN ISOLATION LEVEL REPEATABLE READ;\n"
        "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', "
        "'YYYY-MM-DD HH24:MI:SS.US') || '+00';\n"
        + (host.read_text(managed.COUNTS_SQL) or managed.render_counts_sql())
        + "COMMIT;\n"
    )
    lines = psql(host, spec.port, spec.database, script).strip().splitlines()
    return Snapshot(lines[0].strip(), _parse_counts("\n".join(lines[1:])))


def database_size(host: Host, spec: managed.ClusterSpec) -> int:
    out = psql(
        host, spec.port, "postgres", "SELECT sum(pg_database_size(datname)) FROM pg_database"
    )
    return int(out.strip() or 0)


def _wait_for_recovery(
    host: Host, drill: managed.ClusterSpec, sleep: Callable[[float], None], wait: float
) -> str | None:
    """Wait (bounded) until the restored cluster has finished replaying WAL and accepts writes.

    None means it did; otherwise the reason it did not: it stopped (see its log), or took too long.
    """
    waited = 0.0
    while waited <= wait:
        code, out = host.run(
            [
                "psql",
                "-p",
                str(drill.port),
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-At",
                "-c",
                "SELECT pg_is_in_recovery()",
            ]  # fmt: skip
        )
        if code == 0 and out.strip() == "f":
            return None
        if clusters(host).get(drill.name, (drill.port, "down"))[1] != "online":
            return f"the cluster stopped while recovering: {_log_tail(host, drill)}"
        sleep(5.0)
        waited += 5.0
    return f"still replaying WAL after {wait:g} s"


def cleanup_drills(host: Host) -> list[str]:
    """Remove every drill cluster (only names that start with ``drill_`` are ever touched)."""
    removed = []
    for name in sorted(clusters(host)):
        if name.startswith(DRILL_PREFIX):
            host.run_checked(["pg_dropcluster", "--stop", str(POSTGRES_MAJOR), name])
            removed.append(name)
    return removed


def run_drill(
    host: Host,
    config: DatabaseHost,
    cluster: str,
    target: str | None = None,
    keep: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    wait: float = RECOVERY_WAIT,
) -> list[Check]:
    if not host.is_root():
        raise DrillRefused("the restore drill must run as root (use sudo)")
    spec = pick_cluster(config, cluster)
    drill = drill_spec(spec)
    if drill.name in clusters(host):
        raise DrillRefused(f"{drill.name} already exists; remove it with: restore-drill --cleanup")
    if not (stanza_info(host, spec.name) or {}).get("backup"):
        raise DrillRefused(f"the {spec.name} stanza has no backup to restore")
    size, free = database_size(host, spec), host.free_bytes("/var/lib/postgresql")
    if free is None or free < size * SPACE_FACTOR:
        raise DrillRefused(
            f"not enough free space: the live cluster is {size // 10**6} MB and the drill wants "
            f"{int(SPACE_FACTOR * size) // 10**6} MB free in /var/lib/postgresql "
            "(KAPS PLAN.md §6.2: near the budget the drill needs another drive)"
        )
    checks = [Check("disk space", "ok", f"{size // 10**6} MB live, {(free or 0) // 10**9} GB free")]
    snapshot = take_snapshot(host, spec) if target is None else None
    moment = target or (snapshot.at if snapshot else "")
    # PostgreSQL knows a recovery target time has been reached only when it meets a commit made
    # AFTER it, so a quiet database needs one: a transaction that takes an id commits with a record.
    # Then make sure every WAL segment up to that commit is on the share before restoring.
    psql(host, spec.port, "postgres", "SELECT pg_current_xact_id()")
    psql(host, spec.port, "postgres", "SELECT pg_switch_wal()")
    host.run_checked(as_postgres(["pgbackrest", f"--stanza={spec.name}", "check"]))
    data = managed.data_directory(drill)
    if not data.startswith(f"/var/lib/postgresql/{POSTGRES_MAJOR}/{DRILL_PREFIX}"):
        raise DrillRefused(f"refusing to restore into {data}: not a drill directory")
    try:
        host.run_checked(
            [
                "pg_createcluster",
                str(POSTGRES_MAJOR),
                drill.name,
                "--port",
                str(drill.port),
                "--",
                "--data-checksums",
            ]  # fmt: skip
        )
        for path, text, mode in managed.cluster_files(drill):
            write_if_different(host, path, text, mode, "postgres", "postgres")
        host.run(["pg_ctlcluster", str(POSTGRES_MAJOR), drill.name, "stop"])
        host.run_checked(
            as_postgres(
                [
                    "pgbackrest",
                    f"--stanza={spec.name}",
                    f"--pg1-path={data}",
                    "--delta",
                    "--type=time",
                    f"--target={moment}",
                    "--target-action=promote",
                    "restore",
                ]  # fmt: skip
            ),
            timeout=RESTORE_TIMEOUT,
        )
        checks.append(Check("restore", "ok", f"backup and WAL restored to {moment}"))
        host.run_checked(["pg_ctlcluster", str(POSTGRES_MAJOR), drill.name, "start"])
        problem = _wait_for_recovery(host, drill, sleep, wait)
        if problem is not None:
            checks.append(Check("recovery", "fail", problem))
            return checks
        checks.append(
            Check("recovery", "ok", f"WAL replayed; the drill cluster is on {drill.port}")
        )
        if snapshot is not None:
            restored = _parse_counts(
                psql(
                    host,
                    drill.port,
                    spec.database,
                    host.read_text(managed.COUNTS_SQL) or managed.render_counts_sql(),
                )
            )
            checks.append(_compare(snapshot.counts, restored))
        else:
            checks.append(
                Check(
                    "comparison", "ok", f"left to the caller: the drill cluster is on {drill.port}"
                )
            )
    except CommandFailed as error:
        checks.append(
            Check("restore", "fail", f"{error}; the cluster's log says: {_log_tail(host, drill)}")
        )
    finally:
        if not keep:
            cleanup_drills(host)
    return checks


def _log_tail(host: Host, drill: managed.ClusterSpec) -> str:
    """The end of the drill cluster's own log, where the reason a start failed is written."""
    text = host.read_text(f"/var/log/postgresql/postgresql-{POSTGRES_MAJOR}-{drill.name}.log")
    return " ".join((text or "no log").split())[-500:]


def _compare(live: dict[str, int], restored: dict[str, int]) -> Check:
    differ = [
        f"{table}: live {live.get(table)}, restored {restored.get(table)}"
        for table in sorted(live.keys() | restored.keys())
        if live.get(table) != restored.get(table)
    ]
    if differ:
        return Check("row counts", "fail", "differ: " + "; ".join(differ[:5]))
    return Check("row counts", "ok", f"{len(live)} tables, {sum(live.values())} rows, all equal")
