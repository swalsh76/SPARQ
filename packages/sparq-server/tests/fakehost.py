"""A machine that behaves like the database host for the install and verify tests.

It tracks what the commands change (packages, the account, files with their modes, clusters, roles,
databases and extensions) and records every command, so a test can run ``install`` for real, run it
again, and prove the second run changed nothing.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from fakesystem import SS_QUIET, healthy
from sparq_server.admin.system import CommandFailed

MUTATING = (
    "apt-get", "apt-mark", "useradd", "openssl req", "pg_createcluster", "pg_ctlcluster",
    "systemctl enable", "systemctl daemon-reload", "systemctl start", "systemctl restart",
    "mkdir", "pg_dropcluster", "env", "chmod",
)  # fmt: skip
PGBACKREST_CHANGES = ("stanza-create", "backup")
PIN_VERSIONS = {
    "postgresql-18": "18.6-1.pgdg26.04+1",
    "timescaledb-2-postgresql-18": "2.31.0-1",
    "pgbackrest": "2.59.3-1.pgdg26.04+1",
}
GOOD_SETTINGS = ["on", "on", "on", "on", "on", "timescaledb", "scram-sha-256"]


@dataclass
class Cluster:
    port: int
    status: str = "down"
    roles: set[str] = field(default_factory=lambda: {"postgres"})
    databases: set[str] = field(default_factory=lambda: {"postgres"})
    extensions: set[tuple[str, str]] = field(default_factory=set)
    settings: list[str] = field(default_factory=lambda: list(GOOD_SETTINGS))
    statements: list[str] = field(default_factory=list)
    tables: dict[str, int] = field(default_factory=dict)  # table -> exact row count
    recovering: int = 0  # polls of pg_is_in_recovery that still answer yes after a start
    grants: bool = False  # has the ingest role been given its privileges
    canned: dict[str, str] = field(default_factory=dict)  # SQL fragment -> the output to give


class FakeHost:
    def __init__(self, root: bool = True) -> None:
        base = healthy()
        self.files = dict(base.files)
        self.modes: dict[str, int] = {}
        self.owners: dict[str, tuple[str | None, str | None]] = {}
        self.paths = set(base.paths)
        self.commands: dict[tuple[str, ...], tuple[int, str]] = dict(base.commands)
        self.root = root
        self.free = base.free
        self.arch = base.arch
        self.log: list[str] = []
        self.installed: dict[str, str] = {}
        self.candidates: dict[str, str | None] = dict(PIN_VERSIONS)
        self.held: set[str] = set()
        self.users: set[str] = set()
        self.clusters: dict[str, Cluster] = {}
        self.cert_days_left = 3650
        self.fail_on: str | None = None
        self.next_main_port = 5432
        self.install_creates_main = True
        self.denied_paths: set[str] = set()
        self.extra_listeners: list[str] = []  # lines of `ss` output, for a stray listener
        self.share_writable = True  # can the postgres account write to the backup share?
        self.enabled: set[str] = set()  # systemd units that are enabled
        self.stanzas: dict[str, list[str]] = {}  # stanza -> the types of its backups
        self.now = 1_800_000_000  # the fake clock, in seconds
        self.backup_age = 3600  # how long ago the newest backup finished
        self.db_size = 50_000_000  # bytes, what pg_database_size adds up to
        self.restore_counts: dict[str, int] | None = None  # what a restore brings back (None: all)
        self.restore_fails = False
        self.restore_dies = False  # the restored cluster stops while it is recovering
        self.restore_recovering = 0
        self.restores: list[list[str]] = []  # every pgbackrest restore command
        self.archive_broken = False  # `pgbackrest check` fails: WAL does not reach the share
        self.active: set[str] = set()  # systemd units that are running
        self.unit_result = "success"  # how a oneshot unit's last run ended
        self.tpm_missing_library = False  # systemd reports the TPM 'partial' until it is installed
        self.tpm_unusable = False  # the chip cannot seal even with the library
        self.checkout_commit = "a" * 40  # what `git rev-parse HEAD` says in the checkout
        self.disk_percent = 40  # what `df` says about the data drive

    # --- System ------------------------------------------------------------------------------

    def read_text(self, path: str) -> str | None:
        text = self.files.get(path)
        if text is None:
            return None
        if not self.root and not self.modes.get(path, 0o644) & 0o004:
            return None  # the file is not readable by other accounts
        return text

    def exists(self, path: str) -> bool:
        if self.denied(path):
            return False
        return path in self.files or path in self.paths

    def denied(self, path: str) -> bool:
        return not self.root and any(path.startswith(p) for p in self.denied_paths)

    def free_bytes(self, path: str) -> int | None:
        return self.free

    def machine(self) -> str:
        return self.arch

    def is_root(self) -> bool:
        return self.root

    # --- Host --------------------------------------------------------------------------------

    def write_text(
        self, path: str, text: str, mode: int, owner: str | None = None, group: str | None = None
    ) -> None:
        self.files[path] = text
        self.modes[path] = mode
        self.owners[path] = (owner, group)

    def make_dir(
        self, path: str, mode: int, owner: str | None = None, group: str | None = None
    ) -> None:
        self.paths.add(path)
        self.modes[path] = mode
        self.owners[path] = (owner, group)

    def set_owner_mode(
        self, path: str, mode: int, owner: str | None = None, group: str | None = None
    ) -> None:
        self.modes[path] = mode
        self.owners[path] = (owner, group)

    def run_checked(
        self, argv: Sequence[str], input_text: str | None = None, timeout: float = 600.0
    ) -> str:
        code, out = self._dispatch(list(argv), input_text)
        if code != 0:
            raise CommandFailed(f"{' '.join(argv[:3])} exited {code}")
        return out

    def run(self, argv: Sequence[str], timeout: float = 10.0) -> tuple[int, str]:
        return self._dispatch(list(argv), None)

    # --- the commands ------------------------------------------------------------------------

    def mutating(self) -> list[str]:
        return [
            c
            for c in self.log
            if c.startswith(MUTATING)
            or "CREATE" in c
            or "ALTER" in c
            or (c.startswith("pgbackrest") and any(w in c for w in PGBACKREST_CHANGES))
        ]

    def _dispatch(self, argv: list[str], input_text: str | None) -> tuple[int, str]:
        line = " ".join(argv)
        sql = (input_text or "").strip()
        self.log.append(line + (f" <<< {sql}" if sql else ""))
        if self.fail_on is not None and self.fail_on in line:
            return 1, ""
        program = argv[0].rsplit("/", 1)[-1]
        handler = getattr(self, "_" + program.replace("-", "_"), None)
        if handler is not None:
            return handler(argv, input_text)  # type: ignore[no-any-return]
        return self.commands.get(tuple(argv), (-1, ""))

    def _dpkg_query(self, argv: list[str], _: str | None) -> tuple[int, str]:
        version = self.installed.get(argv[-1])
        return (0, version) if version else (1, "")

    def _apt_cache(self, argv: list[str], _: str | None) -> tuple[int, str]:
        candidate = self.candidates.get(argv[-1])
        return 0, f"{argv[-1]}:\n  Candidate: {candidate or '(none)'}\n"

    def _apt_get(self, argv: list[str], _: str | None) -> tuple[int, str]:
        for name in argv[2:]:  # a package with no version pin, such as the TPM library
            if not name.startswith("-") and "=" not in name:
                self.installed[name] = "1.0"
                if name == "libtss2-rc0t64":
                    self.tpm_missing_library = False
        for spec in argv:
            if "=" in spec:
                package, pin = spec.split("=", 1)
                candidate = self.candidates.get(package)
                if candidate is None or not candidate.startswith(pin.rstrip("*")):
                    return 100, ""
                self.installed[package] = candidate
                if package == "postgresql-18" and self.install_creates_main:
                    self.clusters.setdefault("main", Cluster(self.next_main_port, "online"))
        return 0, ""

    def _apt_mark(self, argv: list[str], _: str | None) -> tuple[int, str]:
        if argv[1] == "showhold":
            return 0, "\n".join(sorted(self.held)) + "\n"
        self.held.update(argv[2:])
        return 0, ""

    def _id(self, argv: list[str], _: str | None) -> tuple[int, str]:
        return (0, "999\n") if argv[-1] in self.users else (1, "")

    def _useradd(self, argv: list[str], _: str | None) -> tuple[int, str]:
        self.users.add(argv[-1])
        return 0, ""

    def _hostname(self, argv: list[str], _: str | None) -> tuple[int, str]:
        return 0, "sparq-test-host\n"

    def _openssl(self, argv: list[str], _: str | None) -> tuple[int, str]:
        if argv[1] == "req":
            key, cert = argv[argv.index("-keyout") + 1], argv[argv.index("-out") + 1]
            self.files[key], self.files[cert] = "KEY", "CERT"
            self.modes[key] = self.modes[cert] = 0o600
            return 0, ""
        if "-checkend" in argv:
            return (0, "") if self.cert_days_left >= int(argv[-1]) / 86400 else (1, "")
        return 0, "sha256 Fingerprint=AA:BB:CC\n"

    def _ss(self, argv: list[str], _: str | None) -> tuple[int, str]:
        """What is listening: SSH, plus every online cluster on the address its settings say."""
        lines = [SS_QUIET.rstrip("\n")]
        for name, cluster in self.clusters.items():
            if cluster.status != "online":
                continue
            conf = self.files.get(f"/etc/postgresql/18/{name}/conf.d/90-sparq.conf", "")
            address = "0.0.0.0" if "listen_addresses = '*'" in conf else "127.0.0.1"  # noqa: S104 (text the fake prints, not a bind)
            lines.append(f"LISTEN 0 244 {address}:{cluster.port} 0.0.0.0:*")
        return 0, "\n".join([*lines, *self.extra_listeners]) + "\n"

    def _pg_lsclusters(self, argv: list[str], _: str | None) -> tuple[int, str]:
        rows = [
            f"18 {n} {c.port} {c.status} postgres /data/{n} /log/{n}"
            for n, c in self.clusters.items()
        ]
        return 0, "\n".join(rows) + ("\n" if rows else "")

    def _pg_createcluster(self, argv: list[str], _: str | None) -> tuple[int, str]:
        name, port = argv[2], int(argv[argv.index("--port") + 1])
        self.clusters[name] = Cluster(port)
        self.paths.add(f"/etc/postgresql/18/{name}")
        return 0, ""

    def _pg_ctlcluster(self, argv: list[str], _: str | None) -> tuple[int, str]:
        cluster = self.clusters[argv[2]]
        if argv[3] == "stop":
            cluster.status = "down"
            return 0, ""
        conf = self.files.get(f"/etc/postgresql/18/{argv[2]}/conf.d/90-sparq.conf", "")
        match = re.search(r"^port = (\d+)$", conf, re.MULTILINE)
        if match:
            cluster.port = int(match.group(1))
        cluster.status = "online"
        if argv[3] == "start" and argv[2].startswith("drill_"):
            cluster.recovering = self.restore_recovering
            if self.restore_dies:
                cluster.status = "down"  # it started, then recovery ended before the target
        return 0, ""

    def _pg_dropcluster(self, argv: list[str], _: str | None) -> tuple[int, str]:
        self.clusters.pop(argv[-1], None)
        return 0, ""

    def _psql(self, argv: list[str], input_text: str | None) -> tuple[int, str]:
        port, database = int(argv[argv.index("-p") + 1]), argv[argv.index("-d") + 1]
        cluster = next((c for c in self.clusters.values() if c.port == port), None)
        if cluster is None or cluster.status != "online":
            return 2, ""
        sql = (input_text or "").strip()
        if "-c" in argv:
            sql = argv[argv.index("-c") + 1]
        if "pg_database_size" in sql:
            return 0, str(self.db_size)
        if "pg_is_in_recovery" in sql:
            left, cluster.recovering = cluster.recovering, max(0, cluster.recovering - 1)
            return 0, "t" if left else "f"
        if "pg_current_xact_id" in sql:
            return 0, "1"
        for fragment, output in cluster.canned.items():
            if fragment in sql:
                return 0, output
        if "has_database_privilege" in sql:
            return 0, "0" if cluster.grants else "6"
        if sql.startswith("GRANT CONNECT"):
            cluster.grants = True
            cluster.statements.append(sql)
            return 0, ""
        if "pg_switch_wal" in sql:
            return 0, ""
        counts = "\n".join(f"{t}|{n}" for t, n in sorted(cluster.tables.items()))
        if sql.startswith("BEGIN ISOLATION LEVEL REPEATABLE READ"):
            return 0, "2026-10-24 12:00:00.123456+00\n" + counts
        if "information_schema.tables" in sql:
            return 0, counts
        if not sql.startswith("SELECT"):
            cluster.statements.append(sql)
        if m := re.search(r"FROM pg_roles WHERE rolname = '(\w+)'", sql):
            return 0, "1\n" if m.group(1) in cluster.roles else ""
        if m := re.search(r"FROM pg_database WHERE datname = '(\w+)'", sql):
            return 0, "1\n" if m.group(1) in cluster.databases else ""
        if "FROM pg_extension" in sql:
            return 0, "2.31.0\n" if ("timescaledb", database) in cluster.extensions else ""
        if sql.startswith("SELECT current_setting"):
            return 0, "|".join(cluster.settings) + "\n"
        if m := re.match(r"CREATE ROLE (\w+)", sql):
            cluster.roles.add(m.group(1))
        elif m := re.match(r"CREATE DATABASE (\w+)", sql):
            cluster.databases.add(m.group(1))
        elif sql == "CREATE EXTENSION timescaledb":
            cluster.extensions.add(("timescaledb", database))
        return 0, ""

    # --- the backup tools ----------------------------------------------------------------------

    def _runuser(self, argv: list[str], input_text: str | None) -> tuple[int, str]:
        return self._dispatch(argv[argv.index("--") + 1 :], input_text)

    def _test(self, argv: list[str], _: str | None) -> tuple[int, str]:
        flag, path = argv[1], argv[2]
        known = path in self.paths or any(f.startswith(path + "/") for f in self.files)
        if flag == "-w":
            return (0, "") if self.share_writable and known else (1, "")
        return (0, "") if known else (1, "")

    def _mkdir(self, argv: list[str], _: str | None) -> tuple[int, str]:
        self.paths.add(argv[-1])
        return 0, ""

    def _systemctl(self, argv: list[str], _: str | None) -> tuple[int, str]:
        if argv[1] == "is-enabled":
            return (0, "enabled") if argv[2] in self.enabled else (1, "disabled")
        if argv[1] == "is-active":
            return (0, "active") if argv[-1] in self.active else (3, "inactive")
        if argv[1] == "show" and "Result" in argv:
            return 0, self.unit_result + chr(10)
        if argv[1] == "show":
            return 0, "Sat 2026-10-24 12:00:00 UTC\n"
        if argv[1] == "enable":
            self.enabled.add(argv[-1])
            if "--now" in argv:
                self.active.add(argv[-1])
        if argv[1] in ("start", "restart"):
            self.active.add(argv[-1])
        return 0, ""

    def _systemd_analyze(self, argv: list[str], _: str | None) -> tuple[int, str]:
        state = "partial" if self.tpm_missing_library or self.tpm_unusable else "yes"
        return (0 if state == "yes" else 1), state + chr(10)

    def _git(self, argv: list[str], _: str | None) -> tuple[int, str]:
        return (0, self.checkout_commit + "\n") if "rev-parse" in argv else (1, "")

    def _env(self, argv: list[str], input_text: str | None) -> tuple[int, str]:
        rest = [a for a in argv[1:]]
        while rest and "=" in rest[0]:
            rest.pop(0)
        return self._dispatch(rest, input_text)

    def _uv(self, argv: list[str], _: str | None) -> tuple[int, str]:
        return 0, ""

    def _chmod(self, argv: list[str], _: str | None) -> tuple[int, str]:
        return 0, ""

    def _df(self, argv: list[str], _: str | None) -> tuple[int, str]:
        return 0, f"Use%\n {self.disk_percent}%\n"

    def _pgbackrest(self, argv: list[str], _: str | None) -> tuple[int, str]:
        stanza = next(a.split("=", 1)[1] for a in argv if a.startswith("--stanza="))
        command = next(a for a in argv[1:] if not a.startswith("--"))
        if command == "stanza-create":
            self.stanzas.setdefault(stanza, [])
            return 0, ""
        if command == "info":
            if stanza not in self.stanzas:
                return 0, json.dumps([{"name": stanza, "status": {"code": 1}, "backup": []}])
            stop = self.now - self.backup_age
            backups = [{"type": k, "timestamp": {"stop": stop}} for k in self.stanzas[stanza]]
            code = 0 if backups else 2
            return 0, json.dumps([{"name": stanza, "status": {"code": code}, "backup": backups}])
        if stanza not in self.stanzas:
            return 55, "stanza does not exist"
        if command == "check":
            return (56, "archive timed out") if self.archive_broken else (0, "")
        if command == "backup":
            kind = next(a.split("=", 1)[1] for a in argv if a.startswith("--type="))
            self.stanzas[stanza].append(kind)
        if command == "restore":
            self.restores.append(argv)
            path = next(a.split("=", 1)[1] for a in argv if a.startswith("--pg1-path="))
            drill = self.clusters[path.rsplit("/", 1)[1]]
            if self.restore_fails:
                return 82, "restore failed"
            source = self.clusters[stanza]
            drill.tables = dict(
                source.tables if self.restore_counts is None else self.restore_counts
            )
            drill.databases = set(source.databases)
        return 0, ""

    def _date(self, argv: list[str], _: str | None) -> tuple[int, str]:
        return 0, str(self.now)
