"""Where an analytics run puts its temporary files and polars' spills (S1.2r).

The whole-history passes spill tens of gigabytes, and the analytics node's internal disk is small:
a run once filled it. The settings are in a TOML file outside the repository
(``SPARQ_ANALYTICS_CONFIG``, default ``~/.config/sparq/analytics.toml``), since the data drive's
path is the machine's, not the code's::

    scratch = "/absolute/path/on/the/data/drive/tmp"
    spill_budget_mb = 60000

Every analytics command line calls :func:`ready` before it reads any data. **The settings file wins
over the environment**: macOS sets ``TMPDIR`` to the internal disk in every Terminal and launchd
session, so an inherited value cannot be trusted to mean a choice; to run with other folders, name
another settings file in ``SPARQ_ANALYTICS_CONFIG``. With no file at the default path nothing
changes (the workstation); a file named in ``SPARQ_ANALYTICS_CONFIG`` that does not exist, a file
that cannot be read, a scratch folder that is not an absolute, writable folder, or a setting that
is not understood is refused, so a run never falls back to the internal disk.
"""

import os
import sys
import tempfile
import tomllib
from pathlib import Path

CONFIG_VARIABLE = "SPARQ_ANALYTICS_CONFIG"
DEFAULT_CONFIG = "~/.config/sparq/analytics.toml"
SCRATCH_VARIABLES = ("TMPDIR", "POLARS_TEMP_DIR", "POLARS_OOC_SPILL_DIR")
BUDGET_VARIABLE = "POLARS_OOC_DISK_BUDGET_MB"
SETTINGS = ("scratch", "spill_budget_mb")


class ScratchError(ValueError):
    """The analytics settings are missing where named, unreadable, or name an unusable folder."""


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_VARIABLE, DEFAULT_CONFIG)).expanduser()


def _read(where: Path, named: bool) -> dict[str, object] | None:
    """The settings, or None when there is no file at the default path."""
    if not where.exists():
        if named:
            raise ScratchError(f"{CONFIG_VARIABLE} names {where}, which does not exist")
        return None
    try:
        return tomllib.loads(where.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise ScratchError(f"{where} cannot be read ({error})") from None
    except tomllib.TOMLDecodeError as error:
        raise ScratchError(f"{where} is not valid TOML ({error})") from None


def configure(path: Path | None = None) -> dict[str, str]:
    """Point the temporary and spill folders, and the spill budget, where the settings say; returns
    the variables it set."""
    named = path is not None or CONFIG_VARIABLE in os.environ
    where = path or config_path()
    data = _read(where, named)
    if data is None:
        return {}
    unknown = sorted(set(data) - set(SETTINGS))
    if unknown:
        raise ScratchError(f"{where} has unknown settings: {', '.join(unknown)}")
    wanted: dict[str, str] = {}
    if "scratch" in data:
        raw = data["scratch"]
        folder = Path(raw).expanduser() if isinstance(raw, str) else None
        if folder is None or not folder.is_absolute() or not folder.is_dir():
            raise ScratchError(f"{where} needs an absolute, existing scratch folder, not {raw}")
        if not os.access(folder, os.W_OK | os.X_OK):
            raise ScratchError(f"{where} names a scratch folder this account cannot write: {raw}")
        wanted |= dict.fromkeys(SCRATCH_VARIABLES, str(folder.resolve()))
    if "spill_budget_mb" in data:
        budget = data["spill_budget_mb"]
        if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
            raise ScratchError(f"{where} needs a whole number of megabytes for spill_budget_mb")
        wanted[BUDGET_VARIABLE] = str(budget)
    os.environ.update(wanted)
    if "TMPDIR" in wanted:
        tempfile.tempdir = None  # Python's temporary folder is read again from the new TMPDIR
    return wanted


def ready(path: Path | None = None) -> bool:
    """:func:`configure` for a command line: False, with the reason on stderr, when refused."""
    try:
        configure(path)
    except ScratchError as error:
        print(f"error: {error}", file=sys.stderr)
        return False
    return True
