"""Where an analytics run puts its temporary files and polars' spills (S1.2r).

The whole-history passes spill tens of gigabytes, and the analytics node's internal disk is small:
a run once filled it. The settings are in a TOML file outside the repository
(``SPARQ_ANALYTICS_CONFIG``, default ``~/.config/sparq/analytics.toml``), since the data drive's
path is the machine's, not the code's::

    scratch = "/path/on/the/data/drive/tmp"
    spill_budget_mb = 60000

Every analytics command line calls :func:`configure` before it reads any data. A variable already
set in the environment wins, so a run can still say otherwise; with no settings file nothing
changes (the workstation); a file that names a folder that does not exist is refused, so a run
never falls back to the internal disk.
"""

import os
import tempfile
import tomllib
from pathlib import Path

DEFAULT_CONFIG = "~/.config/sparq/analytics.toml"
SCRATCH_VARIABLES = ("TMPDIR", "POLARS_TEMP_DIR", "POLARS_OOC_SPILL_DIR")
BUDGET_VARIABLE = "POLARS_OOC_DISK_BUDGET_MB"


class ScratchError(ValueError):
    """The analytics settings are unreadable or name a folder that does not exist."""


def config_path() -> Path:
    return Path(os.environ.get("SPARQ_ANALYTICS_CONFIG", DEFAULT_CONFIG)).expanduser()


def configure(path: Path | None = None) -> dict[str, str]:
    """Point the temporary and spill folders, and the spill budget, where the settings say; returns
    the variables it set (none it found already set)."""
    where = path or config_path()
    if not where.is_file():
        return {}
    try:
        data = tomllib.loads(where.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ScratchError(f"{where} is not valid TOML ({error})") from None
    wanted: dict[str, str] = {}
    if "scratch" in data:
        scratch = data["scratch"]
        if not isinstance(scratch, str) or not Path(scratch).is_dir():
            raise ScratchError(f"{where} names a scratch folder that does not exist: {scratch}")
        wanted |= dict.fromkeys(SCRATCH_VARIABLES, scratch)
    if "spill_budget_mb" in data:
        budget = data["spill_budget_mb"]
        if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
            raise ScratchError(f"{where} needs a whole number of megabytes for spill_budget_mb")
        wanted[BUDGET_VARIABLE] = str(budget)
    unknown = sorted(set(data) - {"scratch", "spill_budget_mb"})
    if unknown:
        raise ScratchError(f"{where} has unknown settings: {', '.join(unknown)}")
    set_here = {name: value for name, value in wanted.items() if name not in os.environ}
    os.environ.update(set_here)
    if "TMPDIR" in set_here:
        tempfile.tempdir = None  # Python's temporary folder is read again from the new TMPDIR
    return set_here
