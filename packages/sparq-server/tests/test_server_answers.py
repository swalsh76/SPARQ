from pathlib import Path

import pytest
from sparq_server.admin.answers import (
    Answers,
    AnswersError,
    Role,
    RoleUnavailable,
    load_answers,
    parse_answers,
)

EXAMPLE = Path(__file__).resolve().parents[3] / "docs" / "deploy" / "install.example.toml"

MINIMAL = """
role = "database_host"
[database_host.backup]
target = "/mnt/sparq-backup"
"""

ANALYTICS = """
role = "analytics_node"
[analytics_node]
database_address = "192.0.2.10"
pinned_certificate_sha256 = "%s"
external_ssd = "/Volumes/External"
share_jobs = "/Volumes/jobs"
share_models = "/Volumes/models"
""" % ("ab" * 32)


def test_the_example_answer_file_in_the_guide_is_valid() -> None:
    answers = load_answers(EXAMPLE)
    assert answers.role is Role.DATABASE_HOST
    host = answers.database_host
    assert host is not None
    assert host.backup.target == "/mnt/sparq-backup"
    assert [str(a) for a in host.postgres.allowed_clients] == ["192.0.2.20"]
    assert host.tls.mode == "new" and not host.recovery.from_backup


def test_a_minimal_file_gets_the_documented_defaults() -> None:
    host = parse_answers(MINIMAL).database_host
    assert host is not None
    assert (host.storage.budget_gb, host.postgres.port, host.web.port) == (700, 5432, 443)
    assert host.backup.retention_full_weeks == 5
    assert host.postgres.allowed_clients == []


def test_text_that_is_not_toml_is_reported() -> None:
    with pytest.raises(AnswersError, match="not valid TOML"):
        parse_answers("role = ")


def test_a_missing_file_is_reported_by_name_only(tmp_path: Path) -> None:
    with pytest.raises(AnswersError, match="cannot read nowhere.toml"):
        load_answers(tmp_path / "nowhere.toml")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('role = "database_host"\n', r"\[database_host\] section is required"),
        (MINIMAL + ANALYTICS.split("\n", 2)[2], "does not belong"),
        ('role = "toaster"\n', "role"),
        (MINIMAL + "surprise = 1\n", "surprise"),
        (MINIMAL.replace("/mnt/sparq-backup", "relative/path"), "target"),
        (MINIMAL.replace("/mnt/sparq-backup", "/mnt/with space"), "target"),
        (MINIMAL + "[database_host.storage]\nbudget_gb = 10\n", "budget_gb"),
        (MINIMAL + "[database_host.storage]\nbudget_gb = 5000\n", "budget_gb"),
        (
            MINIMAL + '[database_host.postgres]\nallowed_clients = ["not-an-address"]\n',
            "allowed_clients",
        ),
        (MINIMAL + "[database_host.postgres]\nport = 80\n", "port"),
        (MINIMAL + '[database_host.tls]\nmode = "restore"\n', "restore_from"),
        (MINIMAL + '[database_host.tls]\nmode = "new"\nrestore_from = "/x"\n', "restore_from"),
        (MINIMAL + "[database_host.recovery]\nfrom_backup = true\n", "backup_set"),
        (MINIMAL + '[database_host.recovery]\nbackup_set = "x"\n', "backup_set"),
        (
            MINIMAL + '[database_host.recovery]\nfrom_backup = true\nbackup_set = "a b"\n',
            "backup_set",
        ),
    ],
)
def test_an_invalid_answer_file_is_refused_with_its_reason(text: str, message: str) -> None:
    with pytest.raises(AnswersError, match=message):
        parse_answers(text)


def test_a_recovery_and_a_restored_certificate_are_accepted() -> None:
    text = (
        MINIMAL
        + '[database_host.tls]\nmode = "restore"\nrestore_from = "/mnt/sparq-backup/tls"\n'
        + '[database_host.recovery]\nfrom_backup = true\nbackup_set = "20261023-020000F"\n'
    )
    host = parse_answers(text).database_host
    assert host is not None
    assert host.tls.restore_from == "/mnt/sparq-backup/tls"
    assert host.recovery.backup_set == "20261023-020000F"


def test_the_database_host_role_is_available() -> None:
    parse_answers(MINIMAL).installable_host()


def test_the_standby_role_is_in_the_model_but_refused_with_its_parking_lot_entry() -> None:
    answers = Answers(role=Role.STANDBY_DATABASE)
    with pytest.raises(RoleUnavailable, match=r"parked \(PLAN.md §9\)"):
        answers.installable_host()


def test_the_analytics_node_role_is_valid_but_not_installable_yet() -> None:
    answers = parse_answers(ANALYTICS)
    assert answers.analytics_node is not None
    assert answers.analytics_node.training_default == "analytics_node"
    with pytest.raises(RoleUnavailable, match="K2"):
        answers.installable_host()


def test_an_analytics_certificate_pin_must_be_a_sha256() -> None:
    with pytest.raises(AnswersError, match="pinned_certificate_sha256"):
        parse_answers(ANALYTICS.replace("ab" * 32, "xyz"))
