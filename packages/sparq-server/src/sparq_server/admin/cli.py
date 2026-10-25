"""``sparq-admin``: the command line for a production role.

    sparq-admin preflight [--answers install.toml]
    sparq-admin install --answers install.toml [--dry-run]     (as root)
    sparq-admin verify --answers install.toml
    sparq-admin migrate --answers install.toml                 (as the service account)
    sparq-admin restore-drill --answers install.toml [--cluster main|test] [--keep]   (as root)

``preflight`` and ``verify`` are read-only. ``install`` runs only from an answer file, never
prompts, and is idempotent: a second run changes nothing. ``migrate`` brings the ``sparq``
database to the newest schema over the local socket. Exit codes: 0 fine, 1 bad command line,
answer file or a step that failed, 2 a check refused or failed, 3 the role is not available yet.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from sparq_server.admin import managed
from sparq_server.admin.answers import Answers, AnswersError, RoleUnavailable, load_answers
from sparq_server.admin.install import (
    InstallRefused,
    plan_install,
    render_outcomes,
    render_plan,
    run_install,
)
from sparq_server.admin.preflight import refused, render, run_preflight
from sparq_server.admin.system import Host, LocalHost, LocalSystem, System
from sparq_server.admin.verify import run_verify


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sparq-admin", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight", help="read-only checks; changes nothing")
    pre.add_argument("--answers", type=Path, help="also check the budget, ports and share")
    inst = sub.add_parser("install", help="install from an answer file (as root)")
    inst.add_argument("--answers", type=Path, required=True)
    inst.add_argument("--dry-run", action="store_true", help="validate and print the plan only")
    ver = sub.add_parser("verify", help="read-only: is the machine what install would make")
    ver.add_argument("--answers", type=Path, required=True)
    rd = sub.add_parser("restore-drill", help="restore the latest backup into a new cluster (root)")
    rd.add_argument("--answers", type=Path, required=True)
    rd.add_argument("--cluster", default="main", help="main or test")
    rd.add_argument("--target-time", help="restore to this UTC time instead of the live moment")
    rd.add_argument("--keep", action="store_true", help="leave the drill cluster for inspection")
    rd.add_argument("--cleanup", action="store_true", help="remove every drill cluster and stop")
    ist = sub.add_parser("ingest-status", help="read-only: is the ingest recording (root for all)")
    ist.add_argument("--answers", type=Path, required=True)
    alt = sub.add_parser("alert-test", help="send one test message to the alert topic (root)")
    alt.add_argument("--answers", type=Path, required=True)
    mig = sub.add_parser("migrate", help="bring the database to the newest schema")
    mig.add_argument("--answers", type=Path, required=True)
    return parser


def _preflight(answers: Answers | None, system: System) -> int:
    answers_for_host = answers.database_host if answers is not None else None
    checks = run_preflight(system, answers_for_host)
    print(render(checks))
    return 2 if refused(checks) else 0


def _install(answers: Answers, dry_run: bool, host: Host) -> int:
    steps = plan_install(answers)  # raises RoleUnavailable for a role that cannot be installed
    if dry_run:
        print(render_plan(steps))
        print("Dry run: nothing was changed.")
        return 0
    try:
        print(render_outcomes(run_install(host, answers)))
    except InstallRefused as error:
        print(f"REFUSING: {error}", file=sys.stderr)
        return 2
    return 0


def _verify(answers: Answers, host: Host) -> int:
    checks = run_verify(host, answers.installable_host())
    print(render(checks))
    return 2 if refused(checks) else 0


def _ingest_status(answers: Answers, host: Host) -> int:
    from sparq_server.admin.ingest_status import run_ingest_status

    checks = run_ingest_status(host, answers.installable_host())
    print(render(checks))
    return 2 if any(c.status == "fail" for c in checks) else 0


def _alert_test(answers: Answers, host: Host) -> int:
    from sparq_server.admin.alerts import AlertTestRefused, run_alert_test

    try:
        print(run_alert_test(host, answers.installable_host()))
    except AlertTestRefused as error:
        print(f"REFUSING: {error}", file=sys.stderr)
        return 2
    return 0


def _restore_drill(answers: Answers, args: argparse.Namespace, host: Host) -> int:
    from sparq_server.admin.restore import DrillRefused, cleanup_drills, run_drill

    try:
        if args.cleanup:
            removed = cleanup_drills(host)
            print("removed: " + (", ".join(removed) or "no drill clusters"))
            return 0
        checks = run_drill(
            host, answers.installable_host(), args.cluster, args.target_time, args.keep
        )
    except DrillRefused as error:
        print(f"REFUSING: {error}", file=sys.stderr)
        return 2
    print(render(checks))
    return 2 if refused(checks) else 0


def _migrate(answers: Answers) -> int:
    from sparq_server.db.migrate import current_revision, upgrade

    config = answers.installable_host()
    url = (
        f"postgresql+asyncpg://{managed.OWNER_ROLE}@/{managed.DATABASE}"
        f"?host={managed.SOCKET_DIRECTORY}&port={config.postgres.port}"
    )
    upgrade(url)
    print(f"the {managed.DATABASE} database is at revision {current_revision(url)}")
    return 0


def main(argv: Sequence[str] | None = None, system: System | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preflight":
            answers = load_answers(args.answers) if args.answers is not None else None
            return _preflight(answers, system or LocalSystem())
        answers = load_answers(args.answers)
        host = cast(Host, system) if system is not None else LocalHost()
        if args.command == "install":
            return _install(answers, args.dry_run, host)
        if args.command == "verify":
            return _verify(answers, host)
        if args.command == "restore-drill":
            return _restore_drill(answers, args, host)
        if args.command == "ingest-status":
            return _ingest_status(answers, host)
        if args.command == "alert-test":
            return _alert_test(answers, host)
        return _migrate(answers)
    except AnswersError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except RoleUnavailable as error:
        print(f"error: {error}", file=sys.stderr)
        return 3
