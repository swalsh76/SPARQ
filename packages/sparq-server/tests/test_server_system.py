import sys
from pathlib import Path

import pytest
from sparq_server.admin.system import LocalSystem


def test_a_file_is_read_and_a_missing_one_is_none(tmp_path: Path) -> None:
    system = LocalSystem()
    path = tmp_path / "a.txt"
    path.write_text("hello", encoding="utf-8")
    assert system.read_text(str(path)) == "hello"
    assert system.read_text(str(tmp_path / "missing")) is None
    assert system.exists(str(path)) and not system.exists(str(tmp_path / "missing"))


def test_a_command_returns_its_exit_code_and_output() -> None:
    code, out = LocalSystem().run([sys.executable, "-c", "print('ok')"])
    assert (code, out.strip()) == (0, "ok")
    code, _ = LocalSystem().run([sys.executable, "-c", "raise SystemExit(3)"])
    assert code == 3


def test_a_command_that_cannot_run_or_takes_too_long_gives_minus_one() -> None:
    system = LocalSystem()
    assert system.run(["definitely-not-a-program-xyz"]) == (-1, "")
    assert system.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3) == (-1, "")


def test_free_space_is_a_number_and_an_unreadable_path_is_none(tmp_path: Path) -> None:
    system = LocalSystem()
    free = system.free_bytes(str(tmp_path))
    assert free is not None and free > 0
    assert system.free_bytes(str(tmp_path / "nowhere" / "deeper")) is None


def test_the_machine_name_is_reported() -> None:
    assert LocalSystem().machine()


def test_a_folder_this_account_may_not_enter_is_denied_and_does_not_crash(tmp_path: Path) -> None:
    import os

    if sys.platform == "win32" or os.geteuid() == 0:
        pytest.skip("needs a POSIX account that is not root")
    locked = tmp_path / "locked"
    locked.mkdir()
    inside = locked / "marker"
    inside.write_text("x", encoding="utf-8")
    locked.chmod(0o000)
    try:
        system = LocalSystem()
        assert system.exists(str(inside)) is False  # used to raise PermissionError
        assert system.denied(str(inside)) is True
    finally:
        locked.chmod(0o700)


def test_a_missing_path_is_not_denied(tmp_path: Path) -> None:
    system = LocalSystem()
    assert system.denied(str(tmp_path / "nowhere")) is False
    assert system.denied(str(tmp_path)) is False
