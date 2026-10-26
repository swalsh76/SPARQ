"""``sparq-admin install`` for the database host: the steps it runs, and the plan they print.

``--dry-run`` prints the plan and changes nothing. The plan is made from the same table of steps
the install runs, each describing itself, so it cannot promise what the install does not do. A
real run is idempotent: each step looks at the machine first, changes only what differs, and says
whether it did. A second run therefore changes nothing. It must run as root, never prompts, and
refuses (changing nothing further) when a prerequisite is missing; the dry run refuses what the
install would. The database settings follow CLAUDE.md: ``fsync``, ``synchronous_commit`` and
``full_page_writes`` stay on, data checksums are on, PostgreSQL accepts connections only from
localhost and the listed clients, and passwords use ``scram-sha-256``.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from sparq_server import names
from sparq_server.admin import managed
from sparq_server.admin.answers import Answers, DatabaseHost
from sparq_server.admin.pg import (
    as_postgres,
    clusters,
    ensure_database,
    ensure_role,
    ensure_timescaledb,
    psql,
    stanza_info,
)
from sparq_server.admin.preflight import refused, run_preflight, tpm_state
from sparq_server.admin.system import CommandFailed, Host, installed_version, write_if_different
from sparq_server.admin.versions import PINS, POSTGRES_MAJOR, TPM_LIBRARY


class InstallRefused(Exception):
    """The install cannot go ahead; the message says why and what to do."""


@dataclass(frozen=True, slots=True)
class Outcome:
    step: str
    changed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Step:
    """One line of the plan: the step it belongs to and what it will do."""

    area: str
    text: str


# --- helpers ------------------------------------------------------------------------------------


def _candidate_version(host: Host, package: str) -> str | None:
    _, out = host.run(["apt-cache", "policy", package])
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Candidate:"):
            value = stripped.split(":", 1)[1].strip()
            return None if value == "(none)" else value
    return None


def refusal(config: DatabaseHost) -> str | None:
    """Why the answers cannot be installed by this release, or None when they can."""
    if config.recovery.from_backup:
        return "recovery from a backup is built in K1b; use a fresh install for now"
    if config.tls.mode == "restore":
        return "restoring the certificate is built in K1b; use tls mode 'new' for now"
    return None


# --- the steps ----------------------------------------------------------------------------------


def step_repositories(host: Host) -> Outcome:
    for package, pin in PINS.items():
        installed = installed_version(host, package)
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
        installed = installed_version(host, package)
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
    versions = ", ".join(f"{p} {installed_version(host, p)}" for p in PINS)
    return Outcome("packages", changed, versions)


ACCOUNT_DIRECTORIES = (
    ("/opt/sparq", 0o755, "root"),
    # these hold no secrets (the key is 0640 and the test password 0600), so a plain account can
    # read the certificate and `verify` can check it
    ("/etc/sparq", 0o755, managed.SERVICE_USER),
    (managed.TLS_DIRECTORY, 0o755, "postgres"),
)


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
    for path, mode, group in ACCOUNT_DIRECTORIES:
        if not host.exists(path):
            host.make_dir(path, mode, "root", group)
            changed = True
    return Outcome("account and directories", changed, f"account {managed.SERVICE_USER}")


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
    for path, text, mode in managed.cluster_files(spec):
        changed |= write_if_different(host, path, text, mode, "postgres", "postgres")
    status = clusters(host).get(spec.name, (spec.port, "down"))[1]
    if changed or status != "online":
        host.run_checked(["pg_ctlcluster", str(POSTGRES_MAJOR), spec.name, "restart"])
        changed = True
    return changed


def step_main_database(host: Host, config: DatabaseHost) -> Outcome:
    spec = managed.main_cluster(config)
    changed = _ensure_cluster(host, spec)
    port, role, database = spec.port, managed.OWNER_ROLE, managed.DATABASE
    changed |= ensure_role(host, port, role)
    changed |= ensure_database(host, port, database, role, private=True)
    changed |= ensure_timescaledb(host, port, database)
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
    changed |= ensure_role(host, port, role, f"LOGIN SUPERUSER PASSWORD '{password.strip()}'")
    changed |= ensure_database(host, port, database, role)
    changed |= ensure_timescaledb(host, port, database)
    return Outcome("test cluster", changed, f"database {database}, port {port}")


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
    if installed_version(host, TPM_LIBRARY) is None:
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
    changed = ensure_role(host, port, role)
    if ingest_grants_missing(host, port):
        psql(host, port, database, managed.render_ingest_grants(role, database, owner))
        changed = True
    return Outcome("ingest role", changed, f"{role}: writes the ingest tables, nothing else")


def step_ingest_app(host: Host, config: DatabaseHost) -> Outcome:
    """Build the service's environment from the checkout, until release bundles exist (K1b).

    The environment lives in a root-owned directory the service can read and not write, with its
    own interpreter, and is rebuilt only when the checkout's commit differs from the built one."""
    checkout, uv = cast(str, config.ingest.checkout), cast(str, config.ingest.uv)
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


# --- the table: what each step does, in order, and what it says it will do ---------------------


Run = Callable[[Host, DatabaseHost, dict[str, Outcome]], Outcome]
Describe = Callable[[DatabaseHost], list[str]]


@dataclass(frozen=True, slots=True)
class InstallStep:
    name: str  # the outcome's step name, and the plan's area
    describe: Describe
    run: Run
    wanted: Callable[[DatabaseHost], bool] = lambda config: True


def _ingest(config: DatabaseHost) -> bool:
    return config.ingest.enabled


def _alerts(config: DatabaseHost) -> bool:
    return config.ingest.enabled and config.alerts.enabled


def _describe_main(config: DatabaseHost) -> list[str]:
    main = managed.main_cluster(config)
    clients = ", ".join(str(a) for a in config.postgres.allowed_clients) or "none"
    return [
        f"the cluster on port {main.port}, data_checksums on at initdb",
        *[f"set {key} = {value}" for key, value in managed.settings(main)],
        f"accept connections from localhost and from: {clients}",
        f"create the database {managed.DATABASE}, its owner {managed.OWNER_ROLE} and TimescaleDB "
        "(the schema is `sparq-admin migrate`)",
    ]


def _describe_backup_files(config: DatabaseHost) -> list[str]:
    backup = config.backup
    return [
        f"back up each cluster to {backup.target}: a full backup weekly, a differential daily, "
        f"WAL archived continuously, {backup.retention_full_weeks} fulls kept"
        + (", encrypted with a passphrase made once" if backup.encrypt else ""),
        "write pgBackRest's configuration, the table-count query and the backup timers",
    ]


def _describe_alerts(config: DatabaseHost) -> list[str]:
    alerts = config.alerts
    return [
        f"alerts: every minute check the ingest and send a message through {alerts.server} if it "
        f"is silent for {alerts.silence_seconds // 60} minutes, stopped, short of disk "
        f"({alerts.disk_warn_percent}%) or its gaps are stuck; the timer starts once the topic "
        "is stored"
    ]


STEPS = (
    InstallStep(
        "repositories",
        lambda c: ["check that the apt repositories offer the pinned versions"],
        lambda host, c, _: step_repositories(host),
    ),
    InstallStep(
        "packages",
        lambda c: [f"install {pkg} {pin}* and hold it" for pkg, pin in PINS.items()],
        lambda host, c, _: step_packages(host),
    ),
    InstallStep(
        "account and directories",
        lambda c: [
            f"create the system account {managed.SERVICE_USER}",
            *[f"create {path}" for path, _, _ in ACCOUNT_DIRECTORIES],
        ],
        lambda host, c, _: step_account(host),
    ),
    InstallStep(
        "certificate",
        lambda c: ["create a new self-signed certificate, unless there is one"],
        lambda host, c, _: step_tls(host),
    ),
    InstallStep(
        "backup files", _describe_backup_files, lambda host, c, _: step_backup_files(host, c)
    ),
    InstallStep("main cluster", _describe_main, lambda host, c, _: step_main_database(host, c)),
    InstallStep(
        "test cluster",
        lambda c: (
            [f"a second cluster on port {c.test_cluster.port} for the tests"]
            if c.test_cluster.enabled
            else []
        ),
        lambda host, c, _: step_test_cluster(host, c),
    ),
    InstallStep(
        "backups",
        lambda c: [
            "for each cluster: its stanza, a check that WAL reaches the share, and a "
            "first full backup"
        ],  # fmt: skip
        lambda host, c, _: step_backups(host, c),
    ),
    InstallStep(
        "tpm support",
        lambda c: [f"make sure systemd can seal credentials with the TPM ({TPM_LIBRARY})"],
        lambda host, c, _: step_tpm_support(host),
        _ingest,
    ),
    InstallStep(
        "ingest role",
        lambda c: [f"create the role {managed.INGEST_ROLE} (writes the ingest tables only)"],
        lambda host, c, _: step_ingest_role(host, c),
        _ingest,
    ),
    InstallStep(
        "ingest program",
        lambda c: [f"build the program from {c.ingest.checkout} into {managed.APP_DIRECTORY}"],
        lambda host, c, _: step_ingest_app(host, c),
        _ingest,
    ),
    InstallStep(
        "ingest service",
        lambda c: [
            f"write {names.INGEST_CONFIG} and the unit {names.INGEST_UNIT}",
            "start the service once its data key is stored "
            f"(it stops by itself at {c.ingest.disk_stop_percent}% disk use)",
        ],
        lambda host, c, done: step_ingest_service(host, c, rebuilt=done["ingest program"].changed),
        _ingest,
    ),
    InstallStep("watchdog", _describe_alerts, lambda host, c, _: step_watchdog(host, c), _alerts),
)


def _installable(answers: Answers) -> DatabaseHost:
    """The database host's answers, refused when this release cannot install them."""
    config = answers.installable_host()  # raises RoleUnavailable for another role
    reason = refusal(config)
    if reason is not None:
        raise InstallRefused(reason)
    return config


def plan_install(answers: Answers) -> list[Step]:
    """What ``run_install`` would do with ``answers``, step by step (it refuses what it would)."""
    config = _installable(answers)
    return [
        Step(step.name, text)
        for step in STEPS
        if step.wanted(config)
        for text in step.describe(config)
    ]


def render_plan(steps: list[Step]) -> str:
    width = max((len(s.area) for s in steps), default=0)
    return "\n".join(f"{s.area:<{width}}  {s.text}" for s in steps)


def run_install(host: Host, answers: Answers) -> list[Outcome]:
    """Run every step for the database host; raises InstallRefused, with its reason, to refuse."""
    config = _installable(answers)
    if not host.is_root():
        raise InstallRefused("install must run as root (use sudo)")
    checks = run_preflight(host, config)
    if refused(checks):
        failed = ", ".join(c.name for c in checks if c.status == "fail")
        raise InstallRefused(f"preflight refused: {failed}")
    done: dict[str, Outcome] = {}
    try:
        for step in STEPS:
            if step.wanted(config):
                done[step.name] = step.run(host, config, done)
    except CommandFailed as error:
        raise InstallRefused(f"a step failed: {error}") from error
    return list(done.values())


def render_outcomes(outcomes: list[Outcome]) -> str:
    lines = [f"{'changed' if o.changed else 'ok':<8}{o.step}: {o.detail}" for o in outcomes]
    changed = sum(o.changed for o in outcomes)
    lines.append(f"{changed} of {len(outcomes)} steps changed something.")
    return "\n".join(lines)
