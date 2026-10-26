"""``sparq-admin install`` for the database host: the plan it prints, and the steps it runs.

``--dry-run`` prints the plan and changes nothing. A real run is idempotent: each step looks at the
machine first, changes only what differs, and says whether it did. A second run therefore changes
nothing. It must run as root, never prompts, and refuses (changing nothing further) when a
prerequisite is missing. The database settings follow CLAUDE.md: ``fsync``, ``synchronous_commit``
and ``full_page_writes`` stay on, data checksums are on, PostgreSQL accepts connections only from
localhost and the listed clients, and passwords use ``scram-sha-256``.
"""

import json
import secrets
from dataclasses import dataclass
from typing import Any, cast

from sparq_server import names
from sparq_server.admin import managed
from sparq_server.admin.answers import Answers, DatabaseHost, Ingest
from sparq_server.admin.preflight import refused, run_preflight, tpm_state
from sparq_server.admin.system import CommandFailed, Host, System
from sparq_server.admin.versions import PINS, POSTGRES_MAJOR, TPM_LIBRARY

DIRECTORIES = ("/opt/sparq/<version>", "/etc/sparq", "/var/lib/sparq")


class InstallRefused(Exception):
    """The install cannot go ahead; the message says why and what to do."""


@dataclass(frozen=True, slots=True)
class Outcome:
    step: str
    changed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Step:
    area: str
    text: str


# --- the plan (what --dry-run prints) -----------------------------------------------------------


def plan_install(answers: Answers) -> list[Step]:
    """The steps for the database host from ``answers`` (raises if the role is not this one)."""
    host = answers.installable_host()
    main = managed.main_cluster(host)
    steps = [Step("packages", f"install {pkg} {pin}* and hold it") for pkg, pin in PINS.items()]
    steps += [Step("directories", f"create {d}") for d in DIRECTORIES]
    steps.append(Step("account", f"create the system account {managed.SERVICE_USER}"))
    steps += [Step("postgres", f"set {key} = {value}") for key, value in managed.settings(main)]
    steps.append(Step("postgres", "data_checksums on at initdb"))
    clients = ", ".join(str(a) for a in host.postgres.allowed_clients) or "none"
    steps += [
        Step("postgres", f"accept connections from localhost and from: {clients}"),
        Step("storage", f"enforce a budget of {host.storage.budget_gb} GB (the governor, K1b)"),
        Step(
            "backup",
            f"back up each cluster to {host.backup.target}: a full backup weekly, a differential "
            f"daily, WAL archived continuously, {host.backup.retention_full_weeks} fulls kept"
            + (", encrypted" if host.backup.encrypt else ""),
        ),
        Step(
            "tls",
            "create a new self-signed certificate"
            if host.tls.mode == "new"
            else f"restore the certificate from {host.tls.restore_from}",
        ),
        Step("web", f"serve the web and API on port {host.web.port} (K6)"),
    ]
    if host.test_cluster.enabled:
        steps.append(
            Step("test cluster", f"a second cluster on port {host.test_cluster.port} for the tests")
        )
    if host.ingest.enabled:
        steps += [
            Step(
                "ingest", f"create the role {managed.INGEST_ROLE} (writes the ingest tables only)"
            ),
            Step(
                "ingest",
                f"build the program from {host.ingest.checkout} into {managed.APP_DIRECTORY}",
            ),
            Step("ingest", f"write {names.INGEST_CONFIG} and the unit {names.INGEST_UNIT}"),
            Step(
                "ingest",
                "start the service once its data key is stored "
                f"(it stops by itself at {host.ingest.disk_stop_percent}% disk use)",
            ),
        ]
    if host.alerts.enabled:
        steps.append(
            Step(
                "alerts",
                f"every minute check the ingest and send a message through {host.alerts.server} "
                f"if it is silent for {host.alerts.silence_seconds // 60} minutes, stopped, "
                f"short of disk ({host.alerts.disk_warn_percent}%) or its gaps are stuck",
            )
        )
    if host.recovery.from_backup:
        steps.append(
            Step(
                "recovery",
                f"restore backup set {host.recovery.backup_set} into a NEW cluster "
                "and compare it with its manifest",
            )
        )
    else:
        steps.append(Step("database", "create the database and its owner; run the migrations"))
    return steps


def render_plan(steps: list[Step]) -> str:
    return "\n".join(f"{s.area:<12} {s.text}" for s in steps)


# --- helpers ------------------------------------------------------------------------------------


def _installed_version(host: Host, package: str) -> str | None:
    code, out = host.run(["dpkg-query", "-W", "-f=${Version}", package])
    return out.strip() if code == 0 and out.strip() else None


def _candidate_version(host: Host, package: str) -> str | None:
    _, out = host.run(["apt-cache", "policy", package])
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Candidate:"):
            value = stripped.split(":", 1)[1].strip()
            return None if value == "(none)" else value
    return None


def write_if_different(host: Host, path: str, text: str, mode: int, owner: str, group: str) -> bool:
    if host.read_text(path) == text:
        return False
    host.write_text(path, text, mode, owner, group)
    return True


def clusters(host: System) -> dict[str, tuple[int, str]]:
    """The PostgreSQL clusters of this major version: name -> (port, status)."""
    _, out = host.run(["pg_lsclusters", "-h"])
    found: dict[str, tuple[int, str]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == str(POSTGRES_MAJOR) and parts[2].isdigit():
            found[parts[1]] = (int(parts[2]), parts[3])
    return found


def psql(host: Host, port: int, database: str, sql: str) -> str:
    """Run SQL as the superuser over the local socket (root maps to ``postgres``)."""
    argv = ["psql", "-p", str(port), "-U", "postgres", "-d", database]
    return host.run_checked([*argv, "-v", "ON_ERROR_STOP=1", "-At", "-q", "-f", "-"], sql)


def _exists(host: Host, port: int, database: str, sql: str) -> bool:
    return bool(psql(host, port, database, sql).strip())


# --- the steps ----------------------------------------------------------------------------------


def step_repositories(host: Host) -> Outcome:
    for package, pin in PINS.items():
        installed = _installed_version(host, package)
        if installed is not None and installed.startswith(pin):
            continue
        candidate = _candidate_version(host, package)
        if candidate is None or not candidate.startswith(pin):
            raise InstallRefused(
                f"{package} {pin}* is not available from the apt repositories "
                f"(candidate: {candidate or 'none'}); set them up as SERVER.md step 5 says"
            )
    return Outcome("repositories", False, "the pinned versions are available")


def step_packages(host: Host) -> Outcome:
    changed = False
    for package, pin in PINS.items():
        installed = _installed_version(host, package)
        if installed is not None and installed.startswith(pin):
            continue
        host.run_checked(
            ["apt-get", "install", "-y", "--no-install-recommends", f"{package}={pin}*"]
        )
        changed = True
    _, held = host.run(["apt-mark", "showhold"])
    unheld = [p for p in PINS if p not in held.split()]
    if unheld:
        host.run_checked(["apt-mark", "hold", *unheld])
        changed = True
    versions = ", ".join(f"{p} {_installed_version(host, p)}" for p in PINS)
    return Outcome("packages", changed, versions)


def step_account(host: Host) -> Outcome:
    changed = False
    code, _ = host.run(["id", "-u", managed.SERVICE_USER])
    if code != 0:
        host.run_checked(
            [
                "useradd", "--system", "--home-dir", "/var/lib/sparq", "--create-home",
                "--shell", "/usr/sbin/nologin", managed.SERVICE_USER,
            ]
        )  # fmt: skip
        changed = True
    for path, mode, group in (
        ("/opt/sparq", 0o755, "root"),
        # these hold no secrets (the key is 0640 and the test password 0600), so a plain account
        # can read the certificate and `verify` can check it
        ("/etc/sparq", 0o755, managed.SERVICE_USER),
        (managed.TLS_DIRECTORY, 0o755, "postgres"),
    ):
        if not host.exists(path):
            host.make_dir(path, mode, "root", group)
            changed = True
    return Outcome("account and directories", changed, f"account {managed.SERVICE_USER}")


def as_postgres(argv: list[str]) -> list[str]:
    """Run a command as the ``postgres`` account (pgBackRest and the share are its)."""
    return ["runuser", "-u", "postgres", "--", *argv]


def stanza_info(host: System, name: str) -> dict[str, Any] | None:
    """What pgBackRest says about one stanza, or None if it cannot be asked."""
    code, out = host.run(as_postgres(["pgbackrest", f"--stanza={name}", "info", "--output=json"]))
    try:
        found = json.loads(out) if code == 0 else []
    except json.JSONDecodeError:
        return None
    return cast(dict[str, Any], found[0]) if found else None


def step_backup_files(host: Host, config: DatabaseHost) -> Outcome:
    """The share's folders, pgBackRest's configuration and passphrase, the manifest query, and the
    timers. Nothing here needs the clusters to be running."""
    target = config.backup.target
    if host.run(as_postgres(["test", "-w", target]))[0] != 0:
        raise InstallRefused(
            f"the backup share at {target} is not writable by the postgres account; mount it "
            "owned by postgres (SERVER.md step 4)"
        )
    changed = False
    for path in (managed.repo_path(config), managed.manifest_directory(config)):
        if host.run(as_postgres(["test", "-d", path]))[0] != 0:
            host.run_checked(as_postgres(["mkdir", "-p", path]))
            changed = True
    for path, mode, owner, group in (
        (managed.PGBACKREST_DIRECTORY, 0o755, "root", "root"),
        (f"{managed.PGBACKREST_DIRECTORY}/conf.d", 0o750, "postgres", "postgres"),
        (managed.SPOOL_DIRECTORY, 0o750, "postgres", "postgres"),
        (managed.LOG_DIRECTORY, 0o750, "postgres", "postgres"),
        (managed.LIB_DIRECTORY, 0o755, "root", "root"),
    ):
        if not host.exists(path):
            host.make_dir(path, mode, owner, group)
            changed = True
    changed |= write_if_different(
        host,
        managed.PGBACKREST_CONF,
        managed.render_pgbackrest(config),
        0o640,
        "postgres",
        "postgres",
    )
    changed |= write_if_different(
        host, managed.COUNTS_SQL, managed.render_counts_sql(), 0o644, "root", "root"
    )
    if config.backup.encrypt and not host.exists(managed.CIPHER_FILE):
        passphrase = secrets.token_urlsafe(48)
        text = f"[global]\nrepo1-cipher-pass={passphrase}\n"
        host.write_text(managed.CIPHER_FILE, text, 0o600, "postgres", "postgres")
        changed = True
    units = managed.backup_units(config)
    unit_changed = False
    for path, text in units.items():
        unit_changed |= write_if_different(host, path, text, 0o644, "root", "root")
    if unit_changed:
        host.run_checked(["systemctl", "daemon-reload"])
    for timer in managed.timer_names(config):
        if host.run(["systemctl", "is-enabled", timer])[0] != 0:
            host.run_checked(["systemctl", "enable", "--now", timer])
            changed = True
    return Outcome(
        "backup files", changed or unit_changed, f"repository {managed.repo_path(config)}"
    )


def step_backups(host: Host, config: DatabaseHost) -> Outcome:
    """For each cluster: its stanza, a check that WAL reaches the share, and a first full backup."""
    changed = False
    notes = []
    for spec in managed.clusters_of(config):
        info = stanza_info(host, spec.name)
        status = (info or {}).get("status", {}).get("code", 1)
        if info is None or status in (1, 3):
            host.run_checked(as_postgres(["pgbackrest", f"--stanza={spec.name}", "stanza-create"]))
            changed = True
        host.run_checked(as_postgres(["pgbackrest", f"--stanza={spec.name}", "check"]))
        info = stanza_info(host, spec.name)
        if not (info or {}).get("backup"):
            host.run_checked(
                as_postgres(["pgbackrest", f"--stanza={spec.name}", "--type=full", "backup"]),
                timeout=3600.0,
            )
            changed = True
        notes.append(spec.name)
    return Outcome("backups", changed, "stanzas " + ", ".join(notes))


def step_tls(host: Host) -> Outcome:
    changed = False
    if not host.exists(managed.TLS_CERT):
        _, name = host.run(["hostname"])
        subject = f"/CN={name.strip() or 'sparq-server'}"
        host.run_checked(
            [
                "openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt",
                "ec_paramgen_curve:prime256v1", "-nodes", "-days", "3650", "-subj", subject,
                "-keyout", managed.TLS_KEY, "-out", managed.TLS_CERT,
            ]
        )  # fmt: skip
        host.set_owner_mode(managed.TLS_KEY, 0o640, "root", "postgres")
        host.set_owner_mode(managed.TLS_CERT, 0o644, "root", "postgres")
        changed = True
    fingerprint = host.run_checked(
        ["openssl", "x509", "-in", managed.TLS_CERT, "-noout", "-fingerprint", "-sha256"]
    ).strip()
    return Outcome("certificate", changed, fingerprint)


def _ensure_cluster(host: Host, spec: managed.ClusterSpec) -> bool:
    """Create the cluster if it is missing, write its managed files, restart it if they changed."""
    changed = False
    if spec.name not in clusters(host):
        host.run_checked(
            [
                "pg_createcluster", str(POSTGRES_MAJOR), spec.name, "--port", str(spec.port),
                "--", "--data-checksums",
            ]
        )  # fmt: skip
        changed = True
    files = (
        (managed.conf_path(spec), managed.render_conf(spec), 0o644),
        (managed.hba_path(spec), managed.render_hba(spec), 0o640),
        (managed.ident_path(spec), managed.render_ident(), 0o640),
    )
    for path, text, mode in files:
        changed |= write_if_different(host, path, text, mode, "postgres", "postgres")
    status = clusters(host).get(spec.name, (spec.port, "down"))[1]
    if changed or status != "online":
        host.run_checked(["pg_ctlcluster", str(POSTGRES_MAJOR), spec.name, "restart"])
        changed = True
    return changed


def _ensure_extension(host: Host, port: int, database: str) -> bool:
    found = "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"
    if _exists(host, port, database, found):
        return False
    psql(host, port, database, "CREATE EXTENSION timescaledb")
    return True


def step_main_database(host: Host, config: DatabaseHost) -> Outcome:
    spec = managed.main_cluster(config)
    changed = _ensure_cluster(host, spec)
    port, role, database = spec.port, managed.OWNER_ROLE, managed.DATABASE
    if not _exists(host, port, "postgres", f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'"):  # noqa: S608 (fixed names)
        psql(host, port, "postgres", f"CREATE ROLE {role} LOGIN")  # noqa: S608 (fixed names)
        changed = True
    if not _exists(
        host,
        port,
        "postgres",
        f"SELECT 1 FROM pg_database WHERE datname = '{database}'",  # noqa: S608 (fixed names)
    ):
        psql(host, port, "postgres", f"CREATE DATABASE {database} OWNER {role}")  # noqa: S608 (fixed names)
        psql(host, port, "postgres", f"REVOKE CONNECT ON DATABASE {database} FROM PUBLIC")  # noqa: S608 (fixed names)
        changed = True
    changed |= _ensure_extension(host, port, database)
    return Outcome("main cluster", changed, f"database {database}, owner {role}, port {port}")


def step_test_cluster(host: Host, config: DatabaseHost) -> Outcome:
    if not config.test_cluster.enabled:
        return Outcome("test cluster", False, "not requested")
    spec = managed.scratch_cluster(config)
    changed = _ensure_cluster(host, spec)
    port, role, database = spec.port, managed.TEST_ROLE, managed.TEST_DATABASE
    password = host.read_text(managed.TEST_PASSWORD_FILE)
    if password is None:
        password = secrets.token_hex(16)
        host.write_text(managed.TEST_PASSWORD_FILE, password + "\n", 0o600, "root", "root")
        changed = True
    password = password.strip()
    if not _exists(host, port, "postgres", f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'"):  # noqa: S608 (fixed names)
        psql(host, port, "postgres", f"CREATE ROLE {role} LOGIN SUPERUSER PASSWORD '{password}'")  # noqa: S608 (fixed names)
        changed = True
    if not _exists(
        host,
        port,
        "postgres",
        f"SELECT 1 FROM pg_database WHERE datname = '{database}'",  # noqa: S608 (fixed names)
    ):
        psql(host, port, "postgres", f"CREATE DATABASE {database} OWNER {role}")  # noqa: S608 (fixed names)
        changed = True
    changed |= _ensure_extension(host, port, database)
    return Outcome("test cluster", changed, f"database {database}, port {port}")


# --- the install --------------------------------------------------------------------------------


def ingest_grants_missing(host: Host, port: int) -> bool:
    """Does the ingest role lack any privilege it is meant to have? One query, so the check costs
    the same as the grant would and a second ``install`` can say it changed nothing."""
    sql = managed.render_ingest_grants_check(
        managed.INGEST_ROLE, managed.DATABASE, managed.OWNER_ROLE
    )
    return psql(host, port, managed.DATABASE, sql).strip() not in ("", "0")


def step_tpm_support(host: Host) -> Outcome:
    """Make sure systemd can seal credentials with the TPM, which the ingest's key needs.

    It installs the library systemd lacks when the TPM reports itself only partly usable. It never
    refuses: a chip that still cannot seal is reported, and ``verify`` fails until that is dealt
    with (the key is then stored another way, by the owner's decision)."""
    changed = False
    if _installed_version(host, TPM_LIBRARY) is None:
        host.run_checked(["apt-get", "install", "-y", "--no-install-recommends", TPM_LIBRARY])
        changed = True
    state = tpm_state(host)
    detail = "sealing works" if state == "yes" else f"systemd still reports the TPM as {state}"
    return Outcome("tpm support", changed, f"{TPM_LIBRARY}: {detail}")


def step_ingest_role(host: Host, config: DatabaseHost) -> Outcome:
    """The ingest service's own role: it writes the ingest tables and can change nothing else.

    Tables that do not exist yet are covered by default privileges for the owner role, so the
    migrations that create them need no second step."""
    port, role = managed.main_cluster(config).port, managed.INGEST_ROLE
    database, owner = managed.DATABASE, managed.OWNER_ROLE
    changed = False
    if not _exists(host, port, "postgres", f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'"):  # noqa: S608 (fixed names)
        psql(host, port, "postgres", f"CREATE ROLE {role} LOGIN")  # noqa: S608 (fixed names)
        changed = True
    if ingest_grants_missing(host, port):
        psql(host, port, database, managed.render_ingest_grants(role, database, owner))
        changed = True
    return Outcome("ingest role", changed, f"{role}: writes the ingest tables, nothing else")


def step_ingest_app(host: Host, ingest: Ingest) -> Outcome:
    """Build the service's environment from the checkout, until release bundles exist (K1b).

    The environment lives in a root-owned directory the service can read and not write, with its
    own interpreter, and is rebuilt only when the checkout's commit differs from the built one."""
    checkout, uv = cast(str, ingest.checkout), cast(str, ingest.uv)
    commit = host.run_checked(
        ["git", "-c", "safe.directory=*", "-C", checkout, "rev-parse", "HEAD"]
    )
    commit = commit.strip()
    if host.read_text(managed.APP_COMMIT_FILE) == commit + "\n":
        return Outcome("ingest program", False, f"commit {commit[:12]} is built")
    environment = [
        "env",
        f"UV_PYTHON_INSTALL_DIR={managed.PYTHON_DIRECTORY}",
        f"UV_PROJECT_ENVIRONMENT={managed.APP_DIRECTORY}/.venv",
    ]
    host.run_checked([*environment, uv, "python", "install", "3.13"], timeout=1800.0)
    host.run_checked(
        [
            *environment, uv, "--directory", checkout, "sync", "--frozen", "--no-dev",
            "--no-editable", "--package", "sparq-server", "--python", "3.13",
            # uv keeps an installed local package whose pyproject.toml is unchanged, even when its
            # source is not: without these a rebuild would leave the old code running
            "--reinstall-package", "sparq-server", "--reinstall-package", "sparq-core",
        ],
        timeout=1800.0,
    )  # fmt: skip
    host.run_checked(["chmod", "-R", "a+rX", managed.APP_DIRECTORY, managed.PYTHON_DIRECTORY])
    host.write_text(managed.APP_COMMIT_FILE, commit + "\n", 0o644, "root", "root")
    return Outcome("ingest program", True, f"built commit {commit[:12]}")


def step_ingest_service(host: Host, config: DatabaseHost, *, rebuilt: bool) -> Outcome:
    """The credential directory, the configuration and the unit; start it once the key is there.

    Without the credentials the unit is enabled and left stopped, and the outcome says so: an
    unattended install never waits for the owner to supply a key."""
    changed = False
    if not host.exists(managed.CREDENTIALS_DIRECTORY):
        host.make_dir(managed.CREDENTIALS_DIRECTORY, 0o700, "root", "root")
        changed = True
    changed |= write_if_different(
        host, names.INGEST_CONFIG, managed.render_ingest_config(config), 0o644, "root", "root"
    )
    unit = f"{managed.SYSTEMD_DIRECTORY}/{names.INGEST_UNIT}"
    unit_changed = write_if_different(
        host, unit, managed.render_ingest_unit(), 0o644, "root", "root"
    )
    if unit_changed:
        host.run_checked(["systemctl", "daemon-reload"])
    if host.run(["systemctl", "is-enabled", names.INGEST_UNIT])[0] != 0:
        host.run_checked(["systemctl", "enable", names.INGEST_UNIT])
        changed = True
    changed |= unit_changed
    ready = all(host.exists(managed.credential_file(n)) for n in names.INGEST_CREDENTIALS)
    active = host.run(["systemctl", "is-active", "--quiet", names.INGEST_UNIT])[0] == 0
    if not ready:
        return Outcome(
            "ingest service", changed, "enabled, not started: the data key is not stored yet"
        )
    if not active:
        host.run_checked(["systemctl", "start", names.INGEST_UNIT])
        return Outcome("ingest service", True, "started")
    if changed or rebuilt:
        host.run_checked(["systemctl", "restart", names.INGEST_UNIT])
        return Outcome("ingest service", True, "restarted with the new program or settings")
    return Outcome("ingest service", False, "running")


def step_watchdog(host: Host, config: DatabaseHost) -> Outcome:
    """The ingest watchdog: its settings and units, and its timer once the alert topic is stored.

    The timer is not enabled before the topic exists, because every run would fail; the outcome
    says so, and an unattended install never waits for the owner."""
    changed = write_if_different(
        host, names.ALERTS_CONFIG, managed.render_alerts_config(config), 0o644, "root", "root"
    )
    unit_changed = False
    for path, text in managed.watchdog_units().items():
        unit_changed |= write_if_different(host, path, text, 0o644, "root", "root")
    if unit_changed:
        host.run_checked(["systemctl", "daemon-reload"])
    ready = host.exists(managed.credential_file(names.ALERT_CREDENTIAL))
    if not ready:
        return Outcome(
            "watchdog",
            changed or unit_changed,
            "installed, not started: the alert topic is not stored yet",
        )
    started = host.run(["systemctl", "is-active", "--quiet", managed.WATCHDOG_TIMER])[0] == 0
    if not started or host.run(["systemctl", "is-enabled", managed.WATCHDOG_TIMER])[0] != 0:
        host.run_checked(["systemctl", "enable", "--now", managed.WATCHDOG_TIMER])
        return Outcome("watchdog", True, "timer started: a check every minute")
    return Outcome("watchdog", changed or unit_changed, "checking every minute")


def run_install(host: Host, answers: Answers) -> list[Outcome]:
    """Run every step for the database host; raises InstallRefused, with its reason, to refuse."""
    config = answers.installable_host()
    if config.recovery.from_backup:
        raise InstallRefused("recovery from a backup is built in K1b; use a fresh install for now")
    if not host.is_root():
        raise InstallRefused("install must run as root (use sudo)")
    checks = run_preflight(host, config)
    if refused(checks):
        failed = ", ".join(c.name for c in checks if c.status == "fail")
        raise InstallRefused(f"preflight refused: {failed}")
    try:
        outcomes = [
            step_repositories(host),
            step_packages(host),
            step_account(host),
            step_tls(host),
            step_backup_files(host, config),
            step_main_database(host, config),
            step_test_cluster(host, config),
            step_backups(host, config),
        ]
        if config.ingest.enabled:
            outcomes += [step_tpm_support(host), step_ingest_role(host, config)]
            built = step_ingest_app(host, config.ingest)
            outcomes += [built, step_ingest_service(host, config, rebuilt=built.changed)]
            if config.alerts.enabled:
                outcomes += [step_watchdog(host, config)]
        return outcomes
    except CommandFailed as error:
        raise InstallRefused(f"a step failed: {error}") from error


def render_outcomes(outcomes: list[Outcome]) -> str:
    lines = [f"{'changed' if o.changed else 'ok':<8}{o.step}: {o.detail}" for o in outcomes]
    changed = sum(o.changed for o in outcomes)
    lines.append(f"{changed} of {len(outcomes)} steps changed something.")
    return "\n".join(lines)
