"""``sparq-admin preflight`` for the database host: read-only checks that make no change.

A check is ``ok``, ``warn`` (the install may go on; the report says what to look at) or ``fail``.
Any failure means the install is refused and nothing is changed (KAPS PLAN.md §6.1). Most checks
need no privilege, so the agent account can run them from SSH.
"""

from dataclasses import dataclass
from typing import Literal

from sparq_server.admin.answers import DatabaseHost
from sparq_server.admin.managed import network_ports
from sparq_server.admin.system import System, installed_version
from sparq_server.admin.versions import PINS, POSTGRES_MAJOR

Status = Literal["ok", "warn", "fail"]
PACKAGE_LABELS = {
    f"postgresql-{POSTGRES_MAJOR}": f"PostgreSQL {POSTGRES_MAJOR}",
    f"timescaledb-2-postgresql-{POSTGRES_MAJOR}": "TimescaleDB",
}

SUPPORTED_UBUNTU = ("24.04", "26.04")
MIN_MEMORY_GIB = 16
HEADROOM_GB = 100  # free space beyond the storage budget
GIB = 1024**3
MARKER_FILE = ".sparq-backup-marker"
NVME_DEVICE = "nvme0n1"
ALWAYS_ALLOWED_PORTS = frozenset({22})  # ssh


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: Status
    detail: str


def _os_release(system: System) -> Check:
    text = system.read_text("/etc/os-release") or ""
    fields = dict(
        line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#")
    )
    ident = fields.get("ID", "").strip('"')
    version = fields.get("VERSION_ID", "").strip('"')
    if ident == "ubuntu" and version in SUPPORTED_UBUNTU:
        return Check("operating system", "ok", f"Ubuntu {version}")
    found = f"{ident or 'unknown'} {version}".strip()
    return Check(
        "operating system", "fail", f"{found}; supported: Ubuntu {', '.join(SUPPORTED_UBUNTU)}"
    )


def _architecture(system: System) -> Check:
    machine = system.machine()
    if machine in ("x86_64", "AMD64"):
        return Check("architecture", "ok", machine)
    return Check("architecture", "fail", f"{machine}; the server bundle is x86_64")


def _memory(system: System) -> Check:
    for line in (system.read_text("/proc/meminfo") or "").splitlines():
        if line.startswith("MemTotal:"):
            gib = int(line.split()[1]) * 1024 / GIB
            status: Status = "ok" if gib >= MIN_MEMORY_GIB else "fail"
            return Check("memory", status, f"{gib:.0f} GiB (at least {MIN_MEMORY_GIB})")
    return Check("memory", "fail", "cannot read /proc/meminfo")


def _free_space(system: System, budget_gb: int) -> Check:
    free = system.free_bytes("/")
    need = budget_gb + HEADROOM_GB
    if free is None:
        return Check("free space", "fail", "cannot read the free space of /")
    gb = free / 1e9
    status: Status = "ok" if gb >= need else "fail"
    return Check(
        "free space", status, f"{gb:.0f} GB free; the budget of {budget_gb} GB needs {need}"
    )


def _drive(system: System) -> list[Check]:
    base = f"/sys/block/{NVME_DEVICE}"
    if not system.exists(base):
        return [Check("drive", "warn", "the system drive is not NVMe; durability must be measured")]
    cache = (system.read_text(f"{base}/queue/write_cache") or "").strip()
    note = "NVMe"
    if cache == "write through":
        return [Check("drive", "ok", f"{note}, write-through cache")]
    if cache == "write back":
        return [
            Check(
                "drive",
                "warn",
                f"{note} with a volatile write cache; K1a.3 must prove flushes reach the media",
            )
        ]
    return [Check("drive", "warn", f"{note}; the write-cache mode is not readable")]


def _time(system: System) -> list[Check]:
    code, synced = system.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    clock = (
        Check("time sync", "ok", "clock synchronised")
        if code == 0 and synced.strip() == "yes"
        else Check("time sync", "fail", "the clock is not synchronised (timedatectl)")
    )
    _, zone = system.run(["timedatectl", "show", "-p", "Timezone", "--value"])
    zone_check = (
        Check("time zone", "ok", "UTC")
        if zone.strip() in ("UTC", "Etc/UTC")
        else Check("time zone", "warn", f"{zone.strip() or 'unknown'}; the servers keep UTC")
    )
    return [clock, zone_check]


def check_listeners(system: System, extra_ports: frozenset[int]) -> Check:
    code, out = system.run(["ss", "-H", "-ltn"])
    if code != 0:
        return Check("listeners", "fail", "cannot list the listening ports (ss)")
    allowed = ALWAYS_ALLOWED_PORTS | extra_ports
    unexpected = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        address, _, port = parts[3].rpartition(":")
        loopback = address.startswith(("127.", "[::1]"))
        if not loopback and port.isdigit() and int(port) not in allowed:
            unexpected.append(parts[3])
    if unexpected:
        return Check(
            "listeners", "fail", "unexpected on the network: " + ", ".join(sorted(set(unexpected)))
        )
    return Check("listeners", "ok", "only SSH (and the configured ports) listen on the network")


def _packages(system: System) -> list[Check]:
    checks = []
    for package in PINS:  # the packages this release pins, which the install adds
        label = PACKAGE_LABELS.get(package, package)
        version = installed_version(system, package)
        detail = f"installed, {version}" if version else "not installed yet (the install adds it)"
        checks.append(Check(label, "ok", detail))
    return checks


def tpm_state(system: System) -> str:
    """What ``systemd-analyze has-tpm2`` says: ``yes``, ``partial``, ``no``, or ``unknown``."""
    _, out = system.run(["systemd-analyze", "has-tpm2"])
    words = out.split()
    first = words[0].lower() if words else ""
    return first if first in ("yes", "partial", "no") else "unknown"


def _tpm(system: System) -> Check:
    """Is there a TPM, and can systemd actually seal with it? A device that exists is not enough:
    the first real rollout found one that systemd called 'partial' for want of a library."""
    if not system.exists("/sys/class/tpm/tpm0"):
        return Check("TPM 2.0", "warn", "absent; secrets would need another store")
    state = tpm_state(system)
    if state == "yes":
        return Check("TPM 2.0", "ok", "present and usable, so service secrets can be sealed")
    return Check(
        "TPM 2.0",
        "warn",
        f"present, but systemd reports {state}: install adds the library it usually lacks "
        "(libtss2-rc0t64); verify fails if sealing still does not work",
    )


def _share(system: System, backup_target: str) -> Check:
    marker = f"{backup_target.rstrip('/')}/{MARKER_FILE}"
    if system.exists(marker):
        return Check("backup share", "ok", f"mounted, marker file present at {backup_target}")
    if system.denied(marker):
        # the share is mounted for root only, so a plain account cannot look inside it
        return Check(
            "backup share",
            "warn",
            f"this account cannot look inside {backup_target}; run preflight with sudo "
            "(install checks it as root)",
        )
    return Check("backup share", "fail", f"no marker file at {marker}; the share is not mounted")


def run_preflight(system: System, config: DatabaseHost | None = None) -> list[Check]:
    """All checks for the database host. With the answers, the budget, ports and share are checked
    too; without them the report covers the machine alone."""
    budget = config.storage.budget_gb if config is not None else 500
    ports = network_ports(config) if config is not None else frozenset()
    checks = [
        _os_release(system),
        _architecture(system),
        _memory(system),
        _free_space(system, budget),
        *_drive(system),
        *_time(system),
        check_listeners(system, ports),
        *_packages(system),
        _tpm(system),
    ]
    if config is not None:
        checks.append(_share(system, config.backup.target))
    return checks


def refused(checks: list[Check]) -> bool:
    return any(c.status == "fail" for c in checks)


def render(checks: list[Check]) -> str:
    lines = [f"{c.status.upper():<5} {c.name}: {c.detail}" for c in checks]
    failed = [c for c in checks if c.status == "fail"]
    if failed:
        lines.append("REFUSING: " + "; ".join(c.name for c in failed) + ". Nothing was changed.")
    else:
        lines.append("All checks passed. Nothing was changed.")
    return "\n".join(lines)
