from pathlib import Path

import pytest
from sparq_durability.cli import build_parser, main

PRODUCTION = "postgresql+asyncpg://sparq_owner@db.example/sparq?port=5432"


def test_the_writer_refuses_a_production_database(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["writer", "--url", PRODUCTION, "--run", "1", "--ledger-host", "x", "--ledger-port", "1"]
    )
    assert code == 1
    assert "REFUSING" in capsys.readouterr().err


def test_verify_refuses_a_production_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["verify", "--url", PRODUCTION, "--ledger-directory", str(tmp_path)]) == 1
    assert "REFUSING" in capsys.readouterr().err


def test_verify_fails_when_there_is_no_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url = "postgresql+asyncpg://t@127.0.0.1:5433/sparq_test"
    assert main(["verify", "--url", url, "--ledger-directory", str(tmp_path)]) == 2
    assert "no acknowledged commits" in capsys.readouterr().out


def test_the_ledger_needs_an_explicit_address() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["ledger", "--directory", "d", "--port", "1"])
