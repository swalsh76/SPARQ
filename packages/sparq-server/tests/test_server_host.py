import sys
from pathlib import Path

import pytest
from sparq_server.admin.system import CommandFailed, LocalHost


def test_a_file_is_written_whole_with_its_parent_folders(tmp_path: Path) -> None:
    host = LocalHost()
    target = tmp_path / "a" / "b" / "file.conf"
    host.write_text(str(target), "one\n", 0o640)
    assert target.read_text(encoding="utf-8") == "one\n"
    host.write_text(str(target), "two\n", 0o640)  # replaced, never appended
    assert target.read_text(encoding="utf-8") == "two\n"
    assert not list(target.parent.glob("*.sparq-new"))  # no half-written leftover


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_a_file_gets_the_requested_mode(tmp_path: Path) -> None:
    host = LocalHost()
    target = tmp_path / "secret"
    host.write_text(str(target), "x", 0o600)
    assert target.stat().st_mode & 0o777 == 0o600
    host.set_owner_mode(str(target), 0o640)
    assert target.stat().st_mode & 0o777 == 0o640


def test_a_directory_is_made_with_its_parents_and_may_exist(tmp_path: Path) -> None:
    host = LocalHost()
    path = tmp_path / "x" / "y"
    host.make_dir(str(path), 0o755)
    host.make_dir(str(path), 0o755)
    assert path.is_dir()


def test_the_owner_and_group_can_be_set_to_the_current_ones(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("chown is POSIX")
    import grp
    import os
    import pwd

    path = tmp_path / "owned"
    LocalHost().write_text(
        str(path), "x", 0o644, pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name
    )
    assert path.read_text(encoding="utf-8") == "x"


def test_a_command_that_must_succeed_returns_its_output() -> None:
    assert LocalHost().run_checked([sys.executable, "-c", "print('done')"]).strip() == "done"


def test_a_command_can_be_given_input_on_standard_input() -> None:
    code = "import sys; print(sys.stdin.read().upper())"
    assert LocalHost().run_checked([sys.executable, "-c", code], "abc").strip() == "ABC"


def test_a_failing_command_raises_with_what_it_said() -> None:
    code = "import sys; sys.stderr.write('it broke'); sys.exit(4)"
    with pytest.raises(CommandFailed, match="exited 4: it broke"):
        LocalHost().run_checked([sys.executable, "-c", code])


def test_a_failure_names_the_whole_command_and_shows_what_it_wrote_to_both_streams() -> None:
    code = "import sys; print('on stdout'); sys.stderr.write('on stderr'); sys.exit(50)"
    with pytest.raises(CommandFailed) as caught:
        LocalHost().run_checked([sys.executable, "-c", code])
    text = str(caught.value)
    assert text.startswith(sys.executable) and " -c import sys" in text and "exited 50" in text
    assert "on stdout" in text and "on stderr" in text


def test_a_command_that_cannot_start_or_takes_too_long_raises() -> None:
    with pytest.raises(CommandFailed):
        LocalHost().run_checked(["definitely-not-a-program-xyz"])
    with pytest.raises(CommandFailed):
        LocalHost().run_checked([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)


def test_the_root_question_has_a_boolean_answer() -> None:
    assert isinstance(LocalHost().is_root(), bool)
