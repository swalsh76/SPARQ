"""The machine as the checks see it, behind one small interface so every check can be tested.

``LocalSystem`` is the real machine. Every command it runs has a fixed argument list (never a
shell) and a timeout, so a check cannot hang an unattended run.
"""

import os
import platform
import shutil
import subprocess  # noqa: S404 (fixed argument lists only, never a shell)
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol


class System(Protocol):
    def read_text(self, path: str) -> str | None:
        """The file's text, or None when it is missing or unreadable."""
        ...

    def exists(self, path: str) -> bool:
        """True when the path exists and this account may look at it (False otherwise)."""
        ...

    def denied(self, path: str) -> bool:
        """True when this account is not allowed to look at the path, which is not the same as the
        path being missing."""
        ...

    def run(self, argv: Sequence[str], timeout: float = 10.0) -> tuple[int, str]:
        """(exit code, standard output); (-1, "") when it cannot run or times out."""
        ...

    def free_bytes(self, path: str) -> int | None: ...

    def machine(self) -> str: ...

    def is_root(self) -> bool: ...


class CommandFailed(Exception):
    """A command that had to succeed did not; the message carries its output."""


class Host(System, Protocol):
    """A machine that can also be changed. Only ``install`` uses these methods."""

    def write_text(
        self, path: str, text: str, mode: int, owner: str | None = None, group: str | None = None
    ) -> None:
        """Replace the file atomically with this text, mode and ownership."""
        ...

    def make_dir(
        self, path: str, mode: int, owner: str | None = None, group: str | None = None
    ) -> None: ...

    def set_owner_mode(
        self, path: str, mode: int, owner: str | None = None, group: str | None = None
    ) -> None: ...

    def run_checked(
        self, argv: Sequence[str], input_text: str | None = None, timeout: float = 600.0
    ) -> str:
        """Run a command that must succeed; return its output or raise CommandFailed."""
        ...


class LocalSystem:
    def read_text(self, path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return None

    def exists(self, path: str) -> bool:
        try:
            return Path(path).exists()
        except OSError:  # for example a folder only root may enter
            return False

    def denied(self, path: str) -> bool:
        try:
            Path(path).stat()
        except PermissionError:
            return True
        except OSError:
            return False
        return False

    def run(self, argv: Sequence[str], timeout: float = 10.0) -> tuple[int, str]:
        try:
            done = subprocess.run(  # noqa: S603 (fixed argument list, no shell)
                list(argv), capture_output=True, text=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return -1, ""
        return done.returncode, done.stdout

    def free_bytes(self, path: str) -> int | None:
        try:
            return shutil.disk_usage(path).free
        except OSError:
            return None

    def machine(self) -> str:
        return platform.machine()

    def is_root(self) -> bool:
        geteuid = getattr(os, "geteuid", None)
        return geteuid is not None and geteuid() == 0


class LocalHost(LocalSystem):
    def write_text(
        self, path: str, text: str, mode: int, owner: str | None = None, group: str | None = None
    ) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".sparq-new")
        temporary.write_text(text, encoding="utf-8")
        self.set_owner_mode(str(temporary), mode, owner, group)
        os.replace(temporary, target)

    def make_dir(
        self, path: str, mode: int, owner: str | None = None, group: str | None = None
    ) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)
        self.set_owner_mode(path, mode, owner, group)

    def set_owner_mode(
        self, path: str, mode: int, owner: str | None = None, group: str | None = None
    ) -> None:
        os.chmod(path, mode)
        if owner is not None or group is not None:
            shutil.chown(path, user=owner, group=group)  # type: ignore[arg-type]

    def run_checked(
        self, argv: Sequence[str], input_text: str | None = None, timeout: float = 600.0
    ) -> str:
        try:
            done = subprocess.run(  # noqa: S603 (fixed argument list, no shell)
                list(argv),
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CommandFailed(f"{argv[0]}: {error}") from error
        if done.returncode != 0:
            said = (done.stderr.strip() + " " + done.stdout.strip()).strip()[-400:]
            raise CommandFailed(f"{' '.join(argv)[:160]} exited {done.returncode}: {said}")
        return done.stdout
