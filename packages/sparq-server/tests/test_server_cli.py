import runpy
import sys
from pathlib import Path

import pytest
from fakehost import FakeHost
from fakesystem import healthy
from sparq_server.admin.cli import main

EXAMPLE = Path(__file__).resolve().parents[3] / "docs" / "deploy" / "install.example.toml"

STANDBY = 'role = "standby_database"\n'
ANALYTICS = """
role = "analytics_node"
[analytics_node]
database_address = "192.0.2.10"
pinned_certificate_sha256 = "%s"
external_ssd = "/Volumes/External"
share_jobs = "/Volumes/jobs"
share_models = "/Volumes/models"
""" % ("cd" * 32)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "install.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_preflight_without_answers_reports_the_machine(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["preflight"], healthy()) == 0
    assert "All checks passed" in capsys.readouterr().out


def test_preflight_with_the_example_answers_checks_the_share(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["preflight", "--answers", str(EXAMPLE)], healthy()) == 0
    assert "backup share" in capsys.readouterr().out


def test_preflight_refuses_with_exit_code_two(capsys: pytest.CaptureFixture[str]) -> None:
    system = healthy()
    system.files["/etc/os-release"] = "ID=debian\n"
    assert main(["preflight"], system) == 2
    assert "REFUSING" in capsys.readouterr().out


def test_every_answer_file_in_the_guides_passes_the_dry_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["install", "--answers", str(EXAMPLE), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "fsync = on" in out and "data_checksums on at initdb" in out
    assert "192.0.2.20" in out and "Dry run: nothing was changed." in out


def test_a_real_install_as_root_runs_every_step_and_a_second_run_changes_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    host = FakeHost()
    assert main(["install", "--answers", str(EXAMPLE)], host) == 0
    first = capsys.readouterr().out
    assert "changed " in first and "steps changed something" in first
    assert main(["install", "--answers", str(EXAMPLE)], host) == 0
    assert "0 of 13 steps changed something." in capsys.readouterr().out


def test_an_install_that_is_not_root_is_refused_with_exit_code_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    host = FakeHost(root=False)
    assert main(["install", "--answers", str(EXAMPLE)], host) == 2
    assert "REFUSING: install must run as root" in capsys.readouterr().err
    assert not host.mutating()


def test_verify_passes_on_an_installed_host_and_fails_on_a_blank_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    host = FakeHost()
    main(["install", "--answers", str(EXAMPLE)], host)
    capsys.readouterr()
    assert main(["verify", "--answers", str(EXAMPLE)], host) == 0
    assert "All checks passed" in capsys.readouterr().out
    assert main(["verify", "--answers", str(EXAMPLE)], FakeHost()) == 2
    assert "REFUSING" in capsys.readouterr().out


def test_migrate_connects_as_the_owner_over_the_local_socket(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        "sparq_server.db.migrate.upgrade", lambda url, revision="head": seen.append(url)
    )
    monkeypatch.setattr("sparq_server.db.migrate.current_revision", lambda url: "0003")
    assert main(["migrate", "--answers", str(EXAMPLE)]) == 0
    assert seen == ["postgresql+asyncpg://sparq_owner@/sparq?host=/var/run/postgresql&port=5432"]
    assert "revision 0003" in capsys.readouterr().out


def test_a_standby_answer_file_is_refused_with_exit_code_three(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install", "--answers", str(write(tmp_path, STANDBY)), "--dry-run"]) == 3
    assert "parked" in capsys.readouterr().err


def test_an_analytics_node_answer_file_is_valid_but_not_installable_yet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install", "--answers", str(write(tmp_path, ANALYTICS)), "--dry-run"]) == 3
    assert "K2" in capsys.readouterr().err


def test_a_bad_or_missing_answer_file_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install", "--answers", str(write(tmp_path, "role = 1")), "--dry-run"]) == 1
    assert main(["install", "--answers", str(tmp_path / "nope.toml"), "--dry-run"]) == 1
    assert main(["preflight", "--answers", str(tmp_path / "nope.toml")], healthy()) == 1
    assert capsys.readouterr().err.count("error:") == 3


def test_a_recovery_plan_restores_into_a_new_cluster(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = (
        'role = "database_host"\n[database_host.backup]\ntarget = "/mnt/sparq-backup"\n'
        '[database_host.tls]\nmode = "restore"\nrestore_from = "/mnt/sparq-backup/tls"\n'
        '[database_host.recovery]\nfrom_backup = true\nbackup_set = "20261023-020000F"\n'
    )
    assert main(["install", "--answers", str(write(tmp_path, text)), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "into a NEW cluster" in out and "restore the certificate from" in out
    assert "initialise a new cluster" not in out


def test_running_the_package_runs_the_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys, "argv", ["sparq-admin", "install", "--answers", str(EXAMPLE), "--dry-run"]
    )
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("sparq_server.admin", run_name="__main__")
    assert caught.value.code == 0
