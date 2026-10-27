"""``sparq-admin verify``: is the installed database host what ``install`` would make?

Most checks need no privilege, so the agent account can run them from SSH. The checks that ask the
database itself need root and are reported as skipped otherwise, never silently passed. A file that
differs from what ``install`` renders is a failure: someone edited a managed file by hand.
"""

from sparq_server import names
from sparq_server.admin import managed
from sparq_server.admin.answers import DatabaseHost
from sparq_server.admin.install import ingest_grants_missing
from sparq_server.admin.pg import as_postgres, clusters, psql, stanza_info
from sparq_server.admin.preflight import Check, Status, check_listeners, tpm_state
from sparq_server.admin.system import CommandFailed, Host, System, installed_version
from sparq_server.admin.versions import PINS, POSTGRES_MAJOR

CERT_WARNING_SECONDS = 30 * 24 * 3600  # an alert goes out 30 days before the certificate expires
STALE_BACKUP_SECONDS = 40 * 3600  # a differential is due daily; a day and a half is late
EXPECTED_SETTINGS = {
    "fsync": "on",
    "synchronous_commit": "on",
    "full_page_writes": "on",
    "data_checksums": "on",
    "ssl": "on",
    "shared_preload_libraries": "timescaledb",
    "password_encryption": "scram-sha-256",
}


def _packages(system: System) -> list[Check]:
    code, held_text = system.run(["apt-mark", "showhold"])
    held = held_text.split() if code == 0 else []
    checks = []
    for package, pin in PINS.items():
        installed = installed_version(system, package) or ""
        if not installed:
            checks.append(Check(package, "fail", f"not installed (wanted {pin}*)"))
        elif not installed.startswith(pin):
            checks.append(Check(package, "fail", f"{installed}, but this release pins {pin}*"))
        elif package not in held:
            checks.append(Check(package, "warn", f"{installed}, not held against upgrades"))
        else:
            checks.append(Check(package, "ok", f"{installed}, held"))
    return checks


def _account(system: System) -> Check:
    code, _ = system.run(["id", "-u", managed.SERVICE_USER])
    if code == 0:
        return Check("service account", "ok", managed.SERVICE_USER)
    return Check("service account", "fail", f"no account named {managed.SERVICE_USER}")


def _managed_file(system: System, label: str, path: str, expected: str) -> Check:
    text = system.read_text(path)
    if text is None:
        # a file only its owner may read cannot be compared by another account
        status: Status = "warn" if system.exists(path) else "fail"
        why = "exists, but this account cannot read it" if status == "warn" else "missing"
        return Check(label, status, f"{path}: {why}")
    if text != expected:
        return Check(label, "fail", f"{path} differs from what install writes")
    return Check(label, "ok", path)


def _cluster(system: System, spec: managed.ClusterSpec, name: str) -> list[Check]:
    found = clusters(system)
    if spec.name not in found:
        return [Check(f"{name} cluster", "fail", f"no {POSTGRES_MAJOR}/{spec.name} cluster")]
    port, status = found[spec.name]
    state = (
        Check(f"{name} cluster", "ok", f"online on port {port}")
        if status == "online" and port == spec.port
        else Check(
            f"{name} cluster", "fail", f"{status} on port {port}, wanted online on {spec.port}"
        )
    )
    return [
        state,
        _managed_file(
            system, f"{name} settings", managed.conf_path(spec), managed.render_conf(spec)
        ),
        _managed_file(
            system, f"{name} access rules", managed.hba_path(spec), managed.render_hba(spec)
        ),
        _managed_file(system, f"{name} user map", managed.ident_path(spec), managed.render_ident()),
    ]


def _certificate(system: System) -> Check:
    if not system.exists(managed.TLS_CERT):
        return Check("certificate", "fail", f"{managed.TLS_CERT} is missing")
    code, _ = system.run(
        [
            "openssl",
            "x509",
            "-in",
            managed.TLS_CERT,
            "-noout",
            "-checkend",
            str(CERT_WARNING_SECONDS),
        ]
    )
    _, fingerprint = system.run(
        ["openssl", "x509", "-in", managed.TLS_CERT, "-noout", "-fingerprint", "-sha256"]
    )
    detail = fingerprint.strip() or "fingerprint unreadable"
    if code != 0:
        return Check("certificate", "fail", f"expires within 30 days or unreadable; {detail}")
    return Check("certificate", "ok", f"valid for 30 more days at least; {detail}")


def _database(host: Host, spec: managed.ClusterSpec, name: str) -> list[Check]:
    """Ask the database: the settings that matter for durability, and the extension."""
    keys = list(EXPECTED_SETTINGS)
    query = "SELECT " + ", ".join(f"current_setting('{k}')" for k in keys)
    try:
        row = psql(host, spec.port, "postgres", query).strip().split("|")
        extension = psql(
            host, spec.port, spec.database,
            "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'",
        ).strip()  # fmt: skip
    except CommandFailed as error:
        return [Check(f"{name} database", "fail", str(error))]
    wrong = [f"{k}={v}" for k, v in zip(keys, row, strict=False) if EXPECTED_SETTINGS[k] != v]
    checks = [
        Check(f"{name} durability and security settings", "fail", "wrong: " + ", ".join(wrong))
        if wrong or len(row) != len(keys)
        else Check(
            f"{name} durability and security settings",
            "ok",
            "fsync, synchronous commit, full-page writes, checksums, TLS on",
        )
    ]
    checks.append(
        Check(f"{name} TimescaleDB extension", "ok", extension)
        if extension
        else Check(f"{name} TimescaleDB extension", "fail", f"not created in {spec.database}")
    )
    return checks


def _present(system: System, label: str, path: str) -> Check:
    """A file whose content is not compared (a secret), or that this account may not look at."""
    if system.exists(path):
        return Check(label, "ok", path)
    status: Status = "warn" if system.denied(path) else "fail"
    why = "exists, but this account cannot look there" if status == "warn" else "missing"
    return Check(label, status, f"{path}: {why}")


def _backup_files(system: System, config: DatabaseHost) -> list[Check]:
    """The backup configuration, the manifest query, the units and timers, and the passphrase."""
    checks = [
        _managed_file(
            system,
            "backup configuration",
            managed.PGBACKREST_CONF,
            managed.render_pgbackrest(config),
        ),
        _managed_file(system, "table-count query", managed.COUNTS_SQL, managed.render_counts_sql()),
    ]
    differ = [
        path.rsplit("/", 1)[1]
        for path, text in managed.backup_units(config).items()
        if system.read_text(path) != text
    ]
    checks.append(
        Check("backup units", "fail", "missing or edited: " + ", ".join(differ))
        if differ
        else Check("backup units", "ok", f"{len(managed.timer_names(config))} timers defined")
    )
    off = [t for t in managed.timer_names(config) if system.run(["systemctl", "is-enabled", t])[0]]
    checks.append(
        Check("backup timers", "fail", "not enabled: " + ", ".join(off))
        if off
        else Check("backup timers", "ok", "weekly full and daily differential, enabled")
    )
    if config.backup.encrypt:
        checks.append(_present(system, "backup passphrase", managed.CIPHER_FILE))
    return checks


def _backup_state(host: Host, spec: managed.ClusterSpec, name: str) -> list[Check]:
    """Ask pgBackRest (root only): does WAL reach the share, and is the newest backup recent?"""
    code, out = host.run(as_postgres(["pgbackrest", f"--stanza={spec.name}", "check"]))
    checks = [
        Check(f"{name} WAL archive", "ok", "WAL reaches the share (pgbackrest check)")
        if code == 0
        else Check(f"{name} WAL archive", "fail", f"pgbackrest check failed: {out.strip()[-150:]}")
    ]
    backups = (stanza_info(host, spec.name) or {}).get("backup") or []
    _, clock = host.run(["date", "+%s"])
    if not backups or not clock.strip().isdigit():
        checks.append(Check(f"{name} backup", "fail", "no backup found"))
        return checks
    age = int(clock) - int(backups[-1]["timestamp"]["stop"])
    detail = f"newest is a {backups[-1]['type']} backup, {age // 3600} hours old"
    stale = age > STALE_BACKUP_SECONDS
    checks.append(Check(f"{name} backup", "fail" if stale else "ok", detail))
    return checks


def _ingest(host: Host, config: DatabaseHost) -> list[Check]:
    """The ingest service: its role, files, program and credentials, and whether it is running.

    A missing key is a warning, because an install cannot supply it; a key that is stored while
    the service is not running is a failure."""
    unit = f"{managed.SYSTEMD_DIRECTORY}/{names.INGEST_UNIT}"
    checks = [
        _managed_file(
            host, "ingest settings", names.INGEST_CONFIG, managed.render_ingest_config(config)
        ),
        _managed_file(host, "ingest unit", unit, managed.render_ingest_unit()),
    ]
    sealing = tpm_state(host)
    checks.append(
        Check("ingest TPM sealing", "ok", "systemd can seal credentials with the TPM")
        if sealing == "yes"
        else Check(
            "ingest TPM sealing",
            "fail",
            f"systemd reports the TPM as {sealing}; the stored key cannot be unsealed "
            "(see the server guide, step 13)",
        )
    )
    built = host.read_text(managed.APP_COMMIT_FILE)
    checks.append(
        Check("ingest program", "ok", f"commit {built.strip()[:12]}")
        if built
        else Check("ingest program", "fail", f"not built ({managed.APP_COMMIT_FILE} is missing)")
    )
    enabled = host.run(["systemctl", "is-enabled", names.INGEST_UNIT])[0] == 0
    checks.append(
        Check("ingest enabled", "ok", "starts at boot")
        if enabled
        else Check("ingest enabled", "fail", f"{names.INGEST_UNIT} is not enabled")
    )
    stored = all(host.exists(managed.credential_file(n)) for n in names.INGEST_CREDENTIALS)
    running = host.run(["systemctl", "is-active", "--quiet", names.INGEST_UNIT])[0] == 0
    if not stored:
        checks.append(
            Check("ingest data key", "warn", f"not stored yet in {managed.CREDENTIALS_DIRECTORY}")
        )
    else:
        checks.append(Check("ingest data key", "ok", "stored encrypted"))
        checks.append(
            Check("ingest service", "ok", "running")
            if running
            else Check("ingest service", "fail", "not running, though its key is stored")
        )
    if host.is_root():
        try:
            missing = ingest_grants_missing(host, managed.main_cluster(config).port)
        except CommandFailed as error:
            return [*checks, Check("ingest role", "fail", str(error))]
        checks.append(
            Check("ingest role", "fail", f"{managed.INGEST_ROLE} lacks privileges install grants")
            if missing
            else Check("ingest role", "ok", f"{managed.INGEST_ROLE} can write the ingest tables")
        )
    else:
        checks.append(Check("ingest role", "warn", "skipped: asking the database needs root"))
    return checks


def _alerts(host: Host, config: DatabaseHost) -> list[Check]:
    """The watchdog: its files, its topic, its timer, and how its last run ended."""
    checks = [
        _managed_file(
            host, "alert settings", names.ALERTS_CONFIG, managed.render_alerts_config(config)
        )
    ]
    for path, text in managed.watchdog_units().items():
        checks.append(_managed_file(host, f"alert unit {path.rsplit('/', 1)[-1]}", path, text))
    stored = host.exists(managed.credential_file(names.ALERT_CREDENTIAL))
    if not stored:
        checks.append(
            Check("alert topic", "warn", f"not stored yet in {managed.CREDENTIALS_DIRECTORY}")
        )
        return checks
    checks.append(Check("alert topic", "ok", "stored encrypted"))
    running = host.run(["systemctl", "is-active", "--quiet", managed.WATCHDOG_TIMER])[0] == 0
    checks.append(
        Check("alert timer", "ok", "checking every minute")
        if running
        else Check("alert timer", "fail", "not running, though the topic is stored")
    )
    code, result = host.run(
        ["systemctl", "show", managed.WATCHDOG_SERVICE, "-p", "Result", "--value"]
    )
    outcome = result.strip()
    if running and code == 0 and outcome not in ("success", ""):
        checks.append(Check("alert last run", "fail", f"the last check ended with: {outcome}"))
    return checks


def run_verify(host: Host, config: DatabaseHost) -> list[Check]:
    main = managed.main_cluster(config)
    checks = [*_packages(host), _account(host), *_cluster(host, main, "main"), _certificate(host)]
    if config.test_cluster.enabled:
        scratch = managed.scratch_cluster(config)
        checks += _cluster(host, scratch, "test")
        checks.append(
            Check("test cluster password", "ok", managed.TEST_PASSWORD_FILE)
            if host.exists(managed.TEST_PASSWORD_FILE)
            else Check("test cluster password", "fail", f"{managed.TEST_PASSWORD_FILE} is missing")
        )
    checks += _backup_files(host, config)
    checks.append(check_listeners(host, managed.network_ports(config)))
    if config.ingest.enabled:
        checks += _ingest(host, config)
    if config.alerts.enabled:
        checks += _alerts(host, config)
    if host.is_root():
        checks += _database(host, main, "main")
        checks += _backup_state(host, main, "main")
        if config.test_cluster.enabled:
            checks += _database(host, managed.scratch_cluster(config), "test")
            checks += _backup_state(host, managed.scratch_cluster(config), "test")
    else:
        checks.append(Check("database settings", "warn", "skipped: asking the database needs root"))
        checks.append(Check("backup state", "warn", "skipped: asking pgbackrest needs root"))
    return checks
