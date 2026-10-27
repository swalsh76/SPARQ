"""The files ``install`` writes and ``verify`` checks, rendered from the answers in one place.

``install`` writes exactly this text and ``verify`` compares the file with exactly this text, so a
file edited by hand shows up as a difference. Each file starts with a header saying so.
"""

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address

from sparq_server.admin.answers import DatabaseHost
from sparq_server.admin.versions import POSTGRES_MAJOR
from sparq_server.names import (
    ALERT_CREDENTIAL,
    ALERTS_CONFIG,
    EXIT_DISK_FULL,
    EXIT_REFUSED,
    INGEST_CONFIG,
    INGEST_CREDENTIALS,
)

HEADER = "# Managed by sparq-admin. Do not edit: the next install overwrites this file.\n"
SERVICE_USER = "sparq"
OWNER_ROLE = "sparq_owner"
DATABASE = "sparq"
INGEST_ROLE = "sparq_ingest"
TEST_ROLE = "sparq_test"
TEST_DATABASE = "sparq_test"
TLS_DIRECTORY = "/etc/sparq/tls"
TLS_CERT = f"{TLS_DIRECTORY}/server.crt"
TLS_KEY = f"{TLS_DIRECTORY}/server.key"
TEST_PASSWORD_FILE = "/etc/sparq/test-cluster.password"  # noqa: S105 (a file path)
SOCKET_DIRECTORY = "/var/run/postgresql"
DATA_ROOT = "/var/lib/postgresql"  # the clusters' parent: any account may stat it, not the clusters


@dataclass(frozen=True, slots=True)
class ClusterSpec:
    """One PostgreSQL cluster to run: name, port, memory and who may connect over the network."""

    name: str
    port: int
    shared_buffers: str
    clients: tuple[IPv4Address | IPv6Address, ...]
    database: str  # the one database remote clients may reach
    role: str | None  # a remote client's role, or None to allow any role (the main cluster)
    archive: bool = True  # False for a restored drill cluster, which must never archive


def main_cluster(config: DatabaseHost) -> ClusterSpec:
    return ClusterSpec(
        "main",
        config.postgres.port,
        "8GB",
        tuple(config.postgres.allowed_clients),
        DATABASE,
        None,
    )


def scratch_cluster(config: DatabaseHost) -> ClusterSpec:
    return ClusterSpec(
        "sparq_test",
        config.test_cluster.port,
        "1GB",
        tuple(config.test_cluster.allowed_clients),
        TEST_DATABASE,
        TEST_ROLE,
    )


def network_ports(config: DatabaseHost) -> frozenset[int]:
    """The ports that may listen on the network: the web port, and the port of each cluster that
    has listed clients. A cluster with none listens on localhost only. Preflight and verify both
    use this, so what install starts is never refused by the check that runs before its next run."""
    ports = {config.web.port}
    main = main_cluster(config)
    if main.clients:
        ports.add(main.port)
    if config.test_cluster.enabled:
        scratch = scratch_cluster(config)
        if scratch.clients:
            ports.add(scratch.port)
    return frozenset(ports)


def settings(spec: ClusterSpec) -> list[tuple[str, str]]:
    """The settings, in order. Durability stays on (CLAUDE.md): fsync, synchronous_commit and
    full_page_writes are never turned off here."""
    listen = "'*'" if spec.clients else "'localhost'"
    archiving = [
        ("archive_mode", "on" if spec.archive else "off"),
        *(
            [("archive_command", f"'pgbackrest --stanza={spec.name} archive-push %p'")]
            if spec.archive
            else []
        ),
        ("archive_timeout", "60"),  # a quiet cluster still ships its WAL within a minute
    ]
    return [
        ("port", str(spec.port)),
        ("listen_addresses", listen),
        ("fsync", "on"),
        ("synchronous_commit", "on"),
        ("full_page_writes", "on"),
        ("wal_level", "replica"),
        *archiving,
        ("password_encryption", "scram-sha-256"),
        ("ssl", "on"),
        ("ssl_cert_file", f"'{TLS_CERT}'"),
        ("ssl_key_file", f"'{TLS_KEY}'"),
        ("shared_buffers", spec.shared_buffers),
        ("shared_preload_libraries", "'timescaledb'"),
        ("timescaledb.telemetry_level", "off"),
    ]


def render_conf(spec: ClusterSpec) -> str:
    return HEADER + "".join(f"{key} = {value}\n" for key, value in settings(spec))


def _cidr(address: IPv4Address | IPv6Address) -> str:
    return f"{address}/{128 if address.version == 6 else 32}"


def render_hba(spec: ClusterSpec) -> str:
    """Local sockets use peer authentication through the ``sparq`` map; network clients must use TLS
    and ``scram-sha-256``; anything not listed is refused."""
    lines = [
        "local   all   all   peer map=sparq",
        "host    all   all   127.0.0.1/32   scram-sha-256",
        "host    all   all   ::1/128        scram-sha-256",
    ]
    role = spec.role or "all"
    lines += [
        f"hostssl {spec.database}   {role}   {_cidr(client)}   scram-sha-256"
        for client in spec.clients
    ]
    return HEADER + "\n".join(lines) + "\n"


def render_ident() -> str:
    """The service account may become the owner role (migrations) or the ingest role (the service);
    root and postgres become the superuser."""
    rows = [
        f"sparq   {SERVICE_USER}   {OWNER_ROLE}",
        f"sparq   {SERVICE_USER}   {INGEST_ROLE}",  # the ingest service, same account
        "sparq   postgres   postgres",
        "sparq   root       postgres",
    ]
    return HEADER + "# MAPNAME  SYSTEM-USERNAME  PG-USERNAME\n" + "\n".join(rows) + "\n"


def cluster_files(spec: ClusterSpec) -> tuple[tuple[str, str, int], ...]:
    """The files that make a cluster ours, as (path, text, mode): its settings, its access rules
    and its user map."""
    return (
        (conf_path(spec), render_conf(spec), 0o644),
        (hba_path(spec), render_hba(spec), 0o640),
        (ident_path(spec), render_ident(), 0o640),
    )


def config_directory(spec: ClusterSpec) -> str:
    return f"/etc/postgresql/{POSTGRES_MAJOR}/{spec.name}"


def conf_path(spec: ClusterSpec) -> str:
    return f"{config_directory(spec)}/conf.d/90-sparq.conf"


def hba_path(spec: ClusterSpec) -> str:
    return f"{config_directory(spec)}/pg_hba.conf"


def ident_path(spec: ClusterSpec) -> str:
    return f"{config_directory(spec)}/pg_ident.conf"


# --- backups (KAPS PLAN.md §6.1 and decisions B1 to B6) -----------------------------------------

PGBACKREST_DIRECTORY = "/etc/pgbackrest"
PGBACKREST_CONF = f"{PGBACKREST_DIRECTORY}/pgbackrest.conf"
CIPHER_FILE = (
    f"{PGBACKREST_DIRECTORY}/conf.d/10-cipher.conf"  # the passphrase; made once, root/postgres only
)
SPOOL_DIRECTORY = "/var/spool/pgbackrest"
LOG_DIRECTORY = "/var/log/pgbackrest"
LIB_DIRECTORY = "/usr/local/lib/sparq"
COUNTS_SQL = f"{LIB_DIRECTORY}/table-counts.sql"
SYSTEMD_DIRECTORY = "/etc/systemd/system"
DIFFERENTIALS_KEPT = 7
BACKUP_TIMES = ("01:30", "02:00")  # one slot per cluster, so two backups never overlap


def clusters_of(config: DatabaseHost) -> list[ClusterSpec]:
    """The clusters this host runs: production, and the test cluster when asked for."""
    specs = [main_cluster(config)]
    if config.test_cluster.enabled:
        specs.append(scratch_cluster(config))
    return specs


def data_directory(spec: ClusterSpec) -> str:
    return f"{DATA_ROOT}/{POSTGRES_MAJOR}/{spec.name}"


def repo_path(config: DatabaseHost) -> str:
    return f"{config.backup.target.rstrip('/')}/pgbackrest"


def manifest_directory(config: DatabaseHost) -> str:
    return f"{repo_path(config)}/manifests"


def render_pgbackrest(config: DatabaseHost) -> str:
    """The pgBackRest configuration. The passphrase is not in it: it lives in ``CIPHER_FILE``,
    which pgBackRest reads from ``conf.d``, so this file can be compared by ``verify``."""
    lines = [
        "[global]",
        "repo1-type=cifs",
        f"repo1-path={repo_path(config)}",
        "repo1-retention-full-type=count",
        f"repo1-retention-full={config.backup.retention_full_weeks}",
        f"repo1-retention-diff={DIFFERENTIALS_KEPT}",
    ]
    if config.backup.encrypt:
        lines.append("repo1-cipher-type=aes-256-cbc")
    lines += [
        "start-fast=y",
        "archive-async=y",
        f"spool-path={SPOOL_DIRECTORY}",
        f"lock-path={SPOOL_DIRECTORY}",  # not /tmp, where another account can already hold the name
        "compress-type=zst",
        "process-max=2",
        f"log-path={LOG_DIRECTORY}",
        "log-level-file=info",
    ]
    for spec in clusters_of(config):
        lines += [
            "",
            f"[{spec.name}]",
            f"pg1-path={data_directory(spec)}",
            f"pg1-port={spec.port}",
            f"pg1-socket-path={SOCKET_DIRECTORY}",
        ]
    return HEADER + "\n".join(lines) + "\n"


def render_counts_sql() -> str:
    """Rows per table, exactly (not the planner's estimate): the manifest written beside each
    backup, and the figures a restore is compared with."""
    return (
        "-- Managed by sparq-admin. Do not edit: the next install overwrites this file.\n"
        "SELECT quote_ident(table_schema) || '.' || quote_ident(table_name),\n"
        "  (xpath('/row/c/text()', query_to_xml('select count(*) as c from '\n"
        "    || quote_ident(table_schema) || '.' || quote_ident(table_name),\n"
        "    false, true, '')))[1]::text\n"
        "FROM information_schema.tables\n"
        "WHERE table_type = 'BASE TABLE'\n"
        "  AND table_schema NOT IN ('pg_catalog', 'information_schema')\n"
        "  AND table_schema NOT LIKE '%timescaledb%'\n"
        "ORDER BY 1;\n"
    )


def unit_name(spec: ClusterSpec, kind: str) -> str:
    return f"sparq-backup-{kind}-{spec.name.replace('_', '-')}"


def backup_units(config: DatabaseHost) -> dict[str, str]:
    """The systemd service and timer for each cluster's weekly full and daily differential backup,
    by path. The service runs as ``postgres`` and writes the row-count manifest after the backup."""
    units: dict[str, str] = {}
    calendars = {"full": "Sun", "diff": "Mon..Sat"}
    for spec, at in zip(clusters_of(config), BACKUP_TIMES, strict=False):
        for kind, days in calendars.items():
            name = unit_name(spec, kind)
            stamp = "$$(date -u +%%Y%%m%%dT%%H%%M%%SZ)"
            csv = f"{manifest_directory(config)}/{spec.name}-{stamp}-{kind}.csv"
            units[f"{SYSTEMD_DIRECTORY}/{name}.service"] = (
                HEADER
                + f"[Unit]\nDescription=SPARQ {kind} backup of the {spec.name} cluster\n\n"
                + "[Service]\nType=oneshot\nUser=postgres\n"
                + f"ExecStart=/usr/bin/pgbackrest --stanza={spec.name} --type={kind} backup\n"
                + f"ExecStartPost=/bin/sh -c '/usr/bin/psql -p {spec.port} -d {spec.database} "
                + f"-At -F, -f {COUNTS_SQL} > {csv}'\n"
            )
            units[f"{SYSTEMD_DIRECTORY}/{name}.timer"] = (
                HEADER
                + f"[Unit]\nDescription=SPARQ {kind} backup of {spec.name}, on a timer\n\n"
                + f"[Timer]\nOnCalendar={days} *-*-* {at}:00\nPersistent=true\n\n"
                + "[Install]\nWantedBy=timers.target\n"
            )
    return units


def timer_names(config: DatabaseHost) -> list[str]:
    return [
        f"{unit_name(spec, kind)}.timer"
        for spec in clusters_of(config)
        for kind in ("full", "diff")
    ]


# --- the ingest service ---------------------------------------------------------------------------

CREDENTIALS_DIRECTORY = "/etc/sparq/credentials"
APP_DIRECTORY = "/opt/sparq/app"
PYTHON_DIRECTORY = "/opt/sparq/python"  # the interpreter uv installs, readable by the service
APP_COMMIT_FILE = f"{APP_DIRECTORY}/COMMIT"


def credential_file(name: str) -> str:
    """Where the encrypted credential is kept; the unit loads it with LoadCredentialEncrypted."""
    return f"{CREDENTIALS_DIRECTORY}/{name}.cred"


def render_ingest_grants(role: str, database: str, owner: str) -> str:
    """What the ingest role may do: write the ingest tables and nothing else. Tables created later
    by ``owner`` (the migrations) are covered by the default privileges."""
    return (  # noqa: S608 (fixed names)
        f"GRANT CONNECT, TEMPORARY ON DATABASE {database} TO {role};\n"  # noqa: S608 (fixed names)
        f"GRANT USAGE ON SCHEMA public TO {role};\n"
        f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {role};\n"
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role};\n"
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE ON TABLES TO {role};\n"
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {role};\n"
        # the migrations' own bookkeeping is read-only to the service; the table may not exist yet,
        # and when the first migration creates it the default privileges above open it again, which
        # is why install is run once more after migrate (verify says so)
        "DO $$ BEGIN IF to_regclass('public.alembic_version') IS NOT NULL THEN "
        f"REVOKE INSERT, UPDATE ON public.alembic_version FROM {role}; END IF; END $$;"
    )  # noqa: S608 (fixed names)


def render_ingest_grants_check(role: str, database: str, owner: str) -> str:
    """One query: how many privileges ``render_ingest_grants`` gives are missing (0: none)."""
    # CASE, not AND: PostgreSQL may test a privilege on a row the kind filter would have removed,
    # and has_sequence_privilege on a table is an error
    tables = (
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "  # noqa: S608
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'S') "
        "AND c.relname <> 'alembic_version' AND CASE "
        "WHEN c.relkind = 'S' "
        f"THEN NOT has_sequence_privilege('{role}', c.oid, 'USAGE') "
        f"ELSE NOT (has_table_privilege('{role}', c.oid, 'SELECT') AND "
        f"has_table_privilege('{role}', c.oid, 'INSERT') AND "
        f"has_table_privilege('{role}', c.oid, 'UPDATE')) END"
    )  # noqa: S608 (fixed names)
    bookkeeping = (  # noqa: S608 (fixed names)
        "SELECT CASE WHEN to_regclass('public.alembic_version') IS NOT NULL AND ("
        f"has_table_privilege('{role}', 'public.alembic_version', 'INSERT') OR "
        f"has_table_privilege('{role}', 'public.alembic_version', 'UPDATE')) THEN 1 ELSE 0 END"
    )
    defaults = (
        "SELECT count(*) FROM pg_default_acl d JOIN pg_namespace n ON n.oid = d.defaclnamespace "  # noqa: S608 (fixed names)
        f"WHERE n.nspname = 'public' AND d.defaclrole = '{owner}'::regrole "
        f"AND d.defaclobjtype IN ('r', 'S') AND d.defaclacl::text LIKE '%{role}=%'"
    )  # noqa: S608 (fixed names)
    return (  # noqa: S608 (fixed names)
        f"SELECT (NOT has_database_privilege('{role}', '{database}', 'CONNECT'))::int "
        f"+ (NOT has_database_privilege('{role}', '{database}', 'TEMPORARY'))::int "
        f"+ (NOT has_schema_privilege('{role}', 'public', 'USAGE'))::int "
        f"+ ({tables}) + (2 - ({defaults})) + ({bookkeeping})"
    )


# --- the watchdog ---------------------------------------------------------------------------------

WATCHDOG_SERVICE = "sparq-watchdog.service"
WATCHDOG_TIMER = "sparq-watchdog.timer"
ALERT_TEST_SERVICE = "sparq-alert-test.service"


def render_alerts_config(config: DatabaseHost) -> str:
    main = main_cluster(config)
    return (
        HEADER
        + f'server = "{config.alerts.server}"\n'
        + f'socket_directory = "{SOCKET_DIRECTORY}"\n'
        + f"port = {main.port}\n"
        + f'database = "{DATABASE}"\n'
        + f'role = "{INGEST_ROLE}"\n'
        + f'data_directory = "{DATA_ROOT}"\n'
        + f"silence_seconds = {config.alerts.silence_seconds}\n"
        + f"disk_warn_percent = {config.alerts.disk_warn_percent}\n"
        + f"disk_stop_percent = {config.ingest.disk_stop_percent}\n"
    )


# the sandbox every unit of ours runs in: no new privileges, a read-only system, no home, no devices
HARDENING = (
    "NoNewPrivileges=yes\nProtectSystem=strict\nProtectHome=yes\nPrivateTmp=yes\n"
    "PrivateDevices=yes\nProtectKernelTunables=yes\nProtectControlGroups=yes\n"
    "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\nLockPersonality=yes\n"
)


def _watchdog_service(description: str, exec_flag: str) -> str:
    """A oneshot run of the watchdog as the service account, with the topic as a credential."""
    start = f"{APP_DIRECTORY}/.venv/bin/python -m sparq_server.watchdog {ALERTS_CONFIG}{exec_flag}"
    return (
        HEADER
        + f"[Unit]\nDescription={description}\nAfter=network-online.target\n\n"
        + "[Service]\nType=oneshot\n"
        + f"User={SERVICE_USER}\nGroup={SERVICE_USER}\n"
        + f"ExecStart={start}\n"
        + f"LoadCredentialEncrypted={ALERT_CREDENTIAL}:{credential_file(ALERT_CREDENTIAL)}\n"
        + "StateDirectory=sparq-watchdog\n"
        + "TimeoutStartSec=60\nMemoryMax=256M\n"
        + HARDENING
    )


def watchdog_units() -> dict[str, str]:
    """The watchdog's service, its timer (every minute) and the service that sends a test message,
    by path. The test service shares the credential and the state directory and is never enabled."""
    timer = (
        HEADER
        + "[Unit]\nDescription=SPARQ ingest watchdog, every minute\n\n"
        + "[Timer]\nOnBootSec=3min\nOnUnitActiveSec=60s\nAccuracySec=5s\n"
        + f"Unit={WATCHDOG_SERVICE}\n\n"
        + "[Install]\nWantedBy=timers.target\n"
    )
    return {
        f"{SYSTEMD_DIRECTORY}/{WATCHDOG_SERVICE}": _watchdog_service(
            "SPARQ ingest watchdog: check the ingest and send an alert if it needs you", ""
        ),
        f"{SYSTEMD_DIRECTORY}/{WATCHDOG_TIMER}": timer,
        f"{SYSTEMD_DIRECTORY}/{ALERT_TEST_SERVICE}": _watchdog_service(
            "SPARQ alert test: send one test message", " --test"
        ),
    }


def render_ingest_config(config: DatabaseHost) -> str:
    main = main_cluster(config)
    return (
        HEADER
        + 'environment = "production"\n'
        + f'socket_directory = "{SOCKET_DIRECTORY}"\n'
        + f"port = {main.port}\n"
        + f'database = "{DATABASE}"\n'
        + f'role = "{INGEST_ROLE}"\n'
        + f'data_directory = "{DATA_ROOT}"\n'
        + f"disk_stop_percent = {config.ingest.disk_stop_percent}\n"
    )


def render_ingest_unit() -> str:
    """The service unit: the service account, the key only as a credential, no place to write,
    and no restart after it stopped on purpose (disk full, bad configuration)."""
    loads = "".join(
        f"LoadCredentialEncrypted={name}:{credential_file(name)}\n" for name in INGEST_CREDENTIALS
    )
    return (
        HEADER
        + "[Unit]\n"
        + "Description=SPARQ ingest: Kalshi live data into PostgreSQL\n"
        + f"After=postgresql@{POSTGRES_MAJOR}-main.service network-online.target\n"
        + f"Wants=postgresql@{POSTGRES_MAJOR}-main.service network-online.target\n"
        + "StartLimitIntervalSec=0\n\n"
        + "[Service]\n"
        + "Type=exec\n"
        + f"User={SERVICE_USER}\nGroup={SERVICE_USER}\n"
        + f"ExecStart={APP_DIRECTORY}/.venv/bin/python -m sparq_server.ingest {INGEST_CONFIG}\n"
        + loads
        + "Restart=always\nRestartSec=5\n"
        + f"RestartPreventExitStatus={EXIT_DISK_FULL} {EXIT_REFUSED}\n"
        + "TimeoutStopSec=90\nMemoryMax=4G\n"
        + HARDENING
        + "\n[Install]\nWantedBy=multi-user.target\n"
    )
