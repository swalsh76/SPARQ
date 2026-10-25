"""The restore drill against a scripted machine: it restores into a new cluster and never touches
the live one, whatever goes wrong."""

import pytest
from fakehost import FakeHost
from sparq_server.admin import managed
from sparq_server.admin.answers import Answers, parse_answers
from sparq_server.admin.cli import main
from sparq_server.admin.install import run_install
from sparq_server.admin.preflight import Check
from sparq_server.admin.restore import (
    DrillRefused,
    _parse_counts,
    cleanup_drills,
    drill_spec,
    run_drill,
)

BASE = """
role = "database_host"
[database_host.backup]
target = "/mnt/sparq-backup"
[database_host.test_cluster]
enabled = true
"""
TABLES = {"public.market": 5, "public.trade": 1200}


def ready() -> tuple[FakeHost, Answers]:
    host, answers = FakeHost(), parse_answers(BASE)
    run_install(host, answers)
    host.clusters["main"].tables = dict(TABLES)
    return host, answers


def drill(host: FakeHost, answers: Answers, **kwargs: object) -> dict[str, Check]:
    config = answers.installable_host()
    return {c.name: c for c in run_drill(host, config, "main", sleep=lambda _: None, **kwargs)}  # type: ignore[arg-type]


def test_a_restore_into_a_new_cluster_matches_the_live_row_counts_and_cleans_up() -> None:
    host, answers = ready()
    checks = drill(host, answers)
    assert checks["row counts"].status == "ok" and "1205 rows" in checks["row counts"].detail
    assert set(host.clusters) == {"main", "sparq_test"}  # the drill cluster is gone
    assert host.clusters["main"].tables == TABLES  # the live cluster was only read


def test_the_restore_targets_the_moment_of_the_snapshot_inside_a_drill_directory_only() -> None:
    host, answers = ready()
    drill(host, answers)
    (command,) = host.restores
    assert "--target=2026-10-24 12:00:00.123456+00" in command
    assert "--type=time" in command and "--target-action=promote" in command
    path = next(a for a in command if a.startswith("--pg1-path="))
    assert path == "--pg1-path=/var/lib/postgresql/18/drill_main"
    assert "--stanza=main" in command


def test_wal_is_switched_and_checked_before_the_restore_so_the_target_is_on_the_share() -> None:
    host, answers = ready()
    drill(host, answers)
    log = host.log
    switch = next(i for i, c in enumerate(log) if "pg_switch_wal" in c)
    check = next(i for i, c in enumerate(log) if c.startswith("pgbackrest --stanza=main check"))
    restore = next(i for i, c in enumerate(log) if " restore" in c and c.startswith("pgbackrest"))
    assert switch < restore and check < restore


def test_a_commit_is_made_after_the_snapshot_so_a_quiet_database_can_reach_its_target() -> None:
    host, answers = ready()
    drill(host, answers)
    log = host.log
    snapshot = next(i for i, c in enumerate(log) if "BEGIN ISOLATION LEVEL REPEATABLE READ" in c)
    marker = next(i for i, c in enumerate(log) if "pg_current_xact_id" in c)
    switch = next(i for i, c in enumerate(log) if "pg_switch_wal" in c)
    assert snapshot < marker < switch  # the commit record must be in the segment that is shipped


def test_a_cluster_that_will_not_start_reports_its_own_log() -> None:
    host, answers = ready()
    host.files["/var/log/postgresql/postgresql-18-drill_main.log"] = (
        "FATAL:  recovery ended before configured recovery target was reached\n"
    )
    original = host._pg_ctlcluster

    def failing(argv: list[str], text: str | None) -> tuple[int, str]:
        return (
            (1, "") if argv[3] == "start" and argv[2].startswith("drill_") else original(argv, text)
        )

    host._pg_ctlcluster = failing  # type: ignore[method-assign,assignment]
    check = drill(host, answers)["restore"]
    assert (
        check.status == "fail"
        and "recovery ended before configured recovery target" in check.detail
    )


def test_the_drill_cluster_listens_on_localhost_only_and_never_archives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, answers = ready()
    seen: dict[str, str] = {}
    original = host._pg_ctlcluster

    def spy(argv: list[str], text: str | None) -> tuple[int, str]:
        if argv[2].startswith("drill_") and argv[3] == "start":
            seen["conf"] = host.files[f"/etc/postgresql/18/{argv[2]}/conf.d/90-sparq.conf"]
        return original(argv, text)

    monkeypatch.setattr(host, "_pg_ctlcluster", spy)
    drill(host, answers)
    assert "archive_mode = off" in seen["conf"] and "archive_command" not in seen["conf"]
    assert "listen_addresses = 'localhost'" in seen["conf"] and "port = 5442" in seen["conf"]


def test_rows_lost_in_the_restore_fail_the_comparison_and_name_the_table() -> None:
    host, answers = ready()
    host.restore_counts = {"public.market": 5, "public.trade": 1100}
    check = drill(host, answers)["row counts"]
    assert check.status == "fail" and "public.trade: live 1200, restored 1100" in check.detail
    assert "drill_main" not in host.clusters


def test_a_table_missing_after_the_restore_fails_too() -> None:
    host, answers = ready()
    host.restore_counts = {"public.market": 5}
    assert drill(host, answers)["row counts"].status == "fail"


def test_a_restore_that_fails_is_reported_and_the_drill_cluster_is_removed() -> None:
    host, answers = ready()
    host.restore_fails = True
    checks = drill(host, answers)
    assert checks["restore"].status == "fail" and "drill_main" not in host.clusters


def test_a_cluster_that_never_finishes_replaying_is_reported_not_waited_on_forever() -> None:
    host, answers = ready()
    host.restore_recovering = 10**6
    checks = {
        c.name: c
        for c in run_drill(
            host, answers.installable_host(), "main", sleep=lambda _: None, wait=20.0
        )
    }
    assert checks["recovery"].status == "fail" and "still replaying" in checks["recovery"].detail
    assert "drill_main" not in host.clusters


def test_a_recovery_that_takes_a_few_polls_is_waited_for() -> None:
    host, answers = ready()
    host.restore_recovering = 3
    assert drill(host, answers)["recovery"].status == "ok"


def test_a_target_time_is_used_as_given_and_the_cluster_is_kept_when_asked() -> None:
    host, answers = ready()
    checks = drill(host, answers, target="2026-10-24 11:00:00+00", keep=True)
    assert "--target=2026-10-24 11:00:00+00" in host.restores[0]
    assert "left to the caller" in checks["comparison"].detail
    assert "drill_main" in host.clusters
    assert cleanup_drills(host) == ["drill_main"]
    assert set(host.clusters) == {"main", "sparq_test"}  # cleanup touches only drill clusters


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda h: setattr(h, "root", False), "must run as root"),
        (lambda h: setattr(h, "free", 10_000_000), "not enough free space"),
        (lambda h: h.stanzas["main"].clear(), "has no backup to restore"),
        (lambda h: h.clusters.setdefault("drill_main", h.clusters["main"]), "already exists"),
    ],
)
def test_the_drill_refuses_before_changing_anything(setup, message: str) -> None:  # type: ignore[no-untyped-def]
    host, answers = ready()
    setup(host)
    before = len(host.log)
    with pytest.raises(DrillRefused, match=message):
        run_drill(host, answers.installable_host(), "main")
    assert not [c for c in host.log[before:] if "restore" in c or "createcluster" in c]


def test_an_unknown_cluster_is_refused() -> None:
    host, answers = ready()
    with pytest.raises(DrillRefused, match="no cluster 'prod'"):
        run_drill(host, answers.installable_host(), "prod")


def test_the_test_cluster_can_be_drilled_too() -> None:
    host, answers = ready()
    host.clusters["sparq_test"].tables = {"public.durability_rows": 7}
    checks = {
        c.name: c for c in run_drill(host, answers.installable_host(), "test", sleep=lambda _: None)
    }
    assert checks["row counts"].status == "ok"
    assert "--stanza=sparq_test" in host.restores[0]
    assert drill_spec(managed.scratch_cluster(answers.installable_host())).port == 5443


def test_counts_are_read_from_the_query_output() -> None:
    assert _parse_counts("public.a|3\npublic.b|0\nnoise\n") == {"public.a": 3, "public.b": 0}


# --- the command line -------------------------------------------------------------------------


def write_answers(tmp_path) -> str:  # type: ignore[no-untyped-def]
    path = tmp_path / "install.toml"
    path.write_text(BASE, encoding="utf-8")
    return str(path)


def test_the_command_runs_the_drill_and_prints_the_checks(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    host, _ = ready()
    assert main(["restore-drill", "--answers", write_answers(tmp_path)], host) == 0
    out = capsys.readouterr().out
    assert "OK    row counts" in out and "All checks passed" in out


def test_the_command_exits_2_when_the_comparison_fails(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    host, _ = ready()
    host.restore_counts = {}
    assert main(["restore-drill", "--answers", write_answers(tmp_path)], host) == 2
    assert "REFUSING: row counts" in capsys.readouterr().out


def test_the_command_refuses_with_a_message_and_cleanup_removes_drills(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    host, _ = ready()
    host.root = False
    assert main(["restore-drill", "--answers", write_answers(tmp_path)], host) == 2
    assert "REFUSING: the restore drill must run as root" in capsys.readouterr().err
    host.root = True
    host.clusters["drill_main"] = host.clusters["main"]
    assert main(["restore-drill", "--answers", write_answers(tmp_path), "--cleanup"], host) == 0
    assert "removed: drill_main" in capsys.readouterr().out


def test_a_restored_cluster_that_dies_while_recovering_is_reported_at_once_with_its_log() -> None:
    host, answers = ready()
    host.restore_dies = True
    host.files["/var/log/postgresql/postgresql-18-drill_main.log"] = (
        "FATAL:  recovery ended before configured recovery target was reached\n"
    )
    waits: list[float] = []
    checks = {
        c.name: c for c in run_drill(host, answers.installable_host(), "main", sleep=waits.append)
    }
    assert checks["recovery"].status == "fail"
    assert "stopped while recovering" in checks["recovery"].detail
    assert "recovery ended before configured recovery target" in checks["recovery"].detail
    assert waits == []  # it did not wait out the clock
    assert "drill_main" not in host.clusters
