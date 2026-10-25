"""A scripted machine for the preflight tests: files, commands, free space and architecture."""

from collections.abc import Sequence
from dataclasses import dataclass, field

OS_RELEASE = 'PRETTY_NAME="Ubuntu 26.04.1 LTS"\nID=ubuntu\nVERSION_ID="26.04"\n'
MEMINFO = "MemTotal:       31457280 kB\nMemFree:        30000000 kB\n"
SS_QUIET = (
    "LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*\n"
    "LISTEN 0 4096 127.0.0.54:53 0.0.0.0:*\n"
    "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n"
    "LISTEN 0 128 [::]:22 [::]:*\n"
    "LISTEN 0 244 127.0.0.1:5432 0.0.0.0:*\n"
)


@dataclass
class FakeSystem:
    files: dict[str, str] = field(default_factory=dict)
    paths: set[str] = field(default_factory=set)
    commands: dict[tuple[str, ...], tuple[int, str]] = field(default_factory=dict)
    free: int | None = 878 * 10**9
    arch: str = "x86_64"
    root: bool = False
    denied_paths: set[str] = field(default_factory=set)  # paths only root may look at

    def read_text(self, path: str) -> str | None:
        return self.files.get(path)

    def exists(self, path: str) -> bool:
        if self.denied(path):
            return False
        return path in self.paths or path in self.files

    def denied(self, path: str) -> bool:
        return not self.root and any(path.startswith(p) for p in self.denied_paths)

    def run(self, argv: Sequence[str], timeout: float = 10.0) -> tuple[int, str]:
        return self.commands.get(tuple(argv), (-1, ""))

    def free_bytes(self, path: str) -> int | None:
        return self.free

    def machine(self) -> str:
        return self.arch

    def is_root(self) -> bool:
        return self.root


def healthy() -> FakeSystem:
    """A machine like the real server host as it stands before the install."""
    return FakeSystem(
        files={
            "/etc/os-release": OS_RELEASE,
            "/proc/meminfo": MEMINFO,
            "/sys/block/nvme0n1/queue/write_cache": "write back\n",
            "/mnt/sparq-backup/.sparq-backup-marker": "",
        },
        paths={"/sys/block/nvme0n1", "/sys/class/tpm/tpm0"},
        commands={
            ("timedatectl", "show", "-p", "NTPSynchronized", "--value"): (0, "yes\n"),
            ("timedatectl", "show", "-p", "Timezone", "--value"): (0, "Etc/UTC\n"),
            ("ss", "-H", "-ltn"): (0, SS_QUIET),
            ("systemd-analyze", "has-tpm2"): (0, "yes" + chr(10)),
            ("dpkg-query", "-W", "-f=${Version}", "postgresql-18"): (1, ""),
            ("dpkg-query", "-W", "-f=${Version}", "timescaledb-2-postgresql-18"): (1, ""),
        },
    )
