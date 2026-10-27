"""``sparq-admin ingest-status``: is the ingest recording, and what has it lost?

A narrow, read-only report on the ingest service alone; the full ``status`` (K1b.2) will absorb it.
The database parts need root (they ask PostgreSQL as the superuser); other accounts get the service
state only. Each line is a ``Check``, so the report reads like ``verify`` and the exit status says
whether anything needs attention.
"""

from sparq_server import names
from sparq_server.admin import managed
from sparq_server.admin.answers import DatabaseHost
from sparq_server.admin.pg import psql
from sparq_server.admin.preflight import Check, Status
from sparq_server.admin.system import CommandFailed, Host
from sparq_server.ingest import gaps, state

HEARTBEAT_WARN_SECONDS = 30  # the feed delivers many frames a second; silence this long is odd
HEARTBEAT_FAIL_SECONDS = 180


def _service(host: Host) -> Check:
    code, _ = host.run(["systemctl", "is-active", "--quiet", names.INGEST_UNIT])
    if code == 0:
        _, since = host.run(
            ["systemctl", "show", names.INGEST_UNIT, "--property=ActiveEnterTimestamp", "--value"]
        )
        return Check("service", "ok", f"running since {since.strip() or 'unknown'}")
    return Check("service", "fail", f"{names.INGEST_UNIT} is not running")


def _rows(host: Host, port: int, sql: str) -> list[list[str]]:
    out = psql(host, port, managed.DATABASE, sql).strip()
    return [line.split("|") for line in out.splitlines() if line]


def _listed(values: tuple[str, ...]) -> str:
    """Fixed names as an SQL list: ('a', 'b')."""
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def _megabytes(value: int) -> str:
    return f"{value / 1_000_000:,.0f} MB"


def database_checks(host: Host, config: DatabaseHost) -> list[Check]:
    port = managed.main_cluster(config).port
    checks: list[Check] = []

    def first(sql: str) -> list[str]:
        rows = _rows(host, port, sql)
        return rows[0] if rows else []

    beat = first(
        "SELECT (extract(epoch FROM now()) * 1000 - value::numeric) / 1000 "  # noqa: S608 (fixed names)
        f"FROM ingest_state WHERE key = '{state.HEARTBEAT}'"
    )
    if not beat:
        checks.append(Check("last frame", "warn", "no frame has ever been recorded"))
    else:
        age = float(beat[0])
        status: Status = (
            "fail"
            if age > HEARTBEAT_FAIL_SECONDS
            else "warn"
            if age > HEARTBEAT_WARN_SECONDS
            else "ok"
        )
        checks.append(Check("last frame", status, f"{age:.0f} s ago"))

    ticker, trades = first(
        "SELECT (SELECT count(*) FROM ticker WHERE ts > now() - interval '1 hour'), "
        "(SELECT count(*) FROM trade WHERE created_time > now() - interval '1 hour')"
    )
    checks.append(
        Check(
            "rows in the last hour",
            "ok" if int(ticker) and int(trades) else "warn",
            f"{int(ticker):,} ticker, {int(trades):,} trades",
        )
    )

    gap_rows = _rows(
        host,
        port,
        "SELECT kind, status, count(*) FROM gap_log "
        "WHERE started_at > now() - interval '24 hours' GROUP BY 1, 2 ORDER BY 1, 2",
    )
    unfinished_sql = f"SELECT count(*) FROM gap_log WHERE status IN {_listed(gaps.UNFINISHED)}"  # noqa: S608 (fixed names)
    unfinished = int(first(unfinished_sql)[0])
    summary = ", ".join(f"{k} {s} {n}" for k, s, n in gap_rows) or "none"
    checks.append(
        Check(
            "gaps, last 24 hours",
            "warn" if unfinished else "ok",
            f"{summary}; {unfinished} unfinished",
        )
    )

    for table in ("ticker", "trade"):
        size, before, after = first(
            f"SELECT hypertable_size('{table}'), "  # noqa: S608 (fixed names)
            "coalesce(before_compression_total_bytes, 0), "
            "coalesce(after_compression_total_bytes, 0) "
            f"FROM (SELECT 1) s LEFT JOIN hypertable_compression_stats('{table}') ON true"
        )
        ratio = f", compressed chunks {int(before) / int(after):.1f}x smaller" if int(after) else ""
        checks.append(Check(f"{table} size", "ok", f"{_megabytes(int(size))}{ratio}"))

    stored = dict(
        _rows(
            host,
            port,
            "SELECT key, value FROM ingest_state "  # noqa: S608 (fixed names)
            f"WHERE key IN {_listed((state.STOPPED_REASON, state.LAST_FULL))}",
        )
    )
    reason = stored.get(state.STOPPED_REASON, "never started")
    checks.append(
        Check("last stop", "fail" if reason.startswith(("disk full", "failed")) else "ok", reason)
    )
    full = stored.get(state.LAST_FULL)
    checks.append(
        Check("last full discovery", "ok", f"epoch {full}")
        if full
        else Check("last full discovery", "warn", "none yet")
    )
    return checks


def disk_check(host: Host, config: DatabaseHost) -> Check:
    path = managed.data_directory(managed.main_cluster(config))
    code, out = host.run(["df", "--output=pcent", path])
    digits = out.split()[-1].rstrip("%") if code == 0 and out.split() else ""
    if not digits.isdigit():
        return Check("data drive", "warn", "usage unreadable")
    used, stop = int(digits), config.ingest.disk_stop_percent
    status: Status = "fail" if used >= stop else "warn" if used >= stop - 5 else "ok"
    return Check("data drive", status, f"{used}% used; the service stops at {stop}%")


def run_ingest_status(host: Host, config: DatabaseHost) -> list[Check]:
    checks = [_service(host), disk_check(host, config)]
    if not host.is_root():
        return [*checks, Check("database", "warn", "skipped: asking the database needs root")]
    try:
        return [*checks, *database_checks(host, config)]
    except (CommandFailed, ValueError) as error:
        return [*checks, Check("database", "fail", str(error))]
