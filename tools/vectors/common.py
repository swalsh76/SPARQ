"""Helpers shared by the vector generators: load the Python reference and write a vector file.

The reference is KalshiTerm2, a local read-only clone. Its modules are loaded by importing them
from the clone's source folder; the generators use only the standard library. The output is
committed, so CI never needs the reference or this folder; rerun a generator only when the
reference changes or a new case is added.
"""

import importlib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

INT64_MAX = 2**63 - 1


def load_reference_module(reference: Path, name: str) -> ModuleType:
    """Import ``kalshi_core.<name>`` from the local KalshiTerm2 clone."""
    source = reference / "packages" / "kalshi-core" / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    return importlib.import_module(f"kalshi_core.{name}")


def reference_commit(reference: Path) -> str:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(reference), "rev-parse", "HEAD"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def default_output(script: str, filename: str, project: str = "sparq-core") -> Path:
    """packages/<project>/tests/vectors/<filename>, relative to the repository root."""
    root = Path(script).resolve().parents[2]
    return root / "packages" / project / "tests" / "vectors" / filename


def write_vectors(path: Path, generator: str, reference: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "generator": generator,
        "referenceCommit": reference_commit(reference),
        "python": sys.version.split()[0],
        **body,
    }
    text = json.dumps(document, indent=1, ensure_ascii=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")  # LF on every platform


def outcome(fn: Callable[..., Any], *args: Any) -> int | str:
    """The reference's answer: the integer, or ``refuse`` for a ValueError (out of its domain)."""
    try:
        result = fn(*args)
    except ValueError:
        return "refuse"
    if not isinstance(result, int):
        raise TypeError(f"expected an integer from the reference, got {result!r}")
    return result
