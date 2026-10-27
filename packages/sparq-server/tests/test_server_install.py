import re

import pytest
from fakehost import FakeHost
from sparq_server.admin import managed
from sparq_server.admin.answers import Answers, AnswersError, parse_answers
from sparq_server.admin.install import (
    InstallRefused,
    plan_install,
    render_outcomes,
    render_plan,
    run_install,
)
from sparq_server.admin.pg import clusters
from sparq_server.admin.versions import PINS

BASE = """
role = "database_host"
[database_host.backup]
target = "/mnt/sparq-backup"
[database_host.postgres]
allowed_clients = ["192.0.2.20"]
"""
TEST = (
    '[database_host.test_cluster]\nenabled = true\nport = 5433\nallowed_clients = ["192.0.2.30"]\n'
)


def answers(extra: str = "") -> Answers:
    return parse_answers(BASE + extra)


def installed(extra: str = "") -> tuple[FakeHost, Answers]:
    host, config = FakeHost(), answers(extra)
    run_install(host, config)
    return host, config


def test_a_fresh_install_makes_the_whole_database_host() -> None:
    host, config = FakeHost(), answers()
    outcomes = {o.step: o for o in run_install(host, config)}
    assert [o.changed for o in outcomes.values()] == [
        False,
        True,
        True,
        True,
        True,
        True,
        False,
        True,
    ]
    assert host.installed.keys() == PINS.keys()
    assert host.held == set(PINS)
    assert "sparq" in host.users
    main = managed.main_cluster(config.installable_host())
    assert host.files[managed.conf_path(main)] == managed.render_conf(main)
    assert host.files[managed.hba_path(main)] == managed.render_hba(main)
    assert host.files[managed.ident_path(main)] == managed.render_ident()
    assert host.modes[managed.hba_path(main)] == 0o640
    assert host.owners[managed.conf_path(main)] == ("postgres", "postgres")
    cluster = host.clusters["main"]
    assert cluster.status == "online" and cluster.port == 5432
    assert {managed.OWNER_ROLE} <= cluster.roles and managed.DATABASE in cluster.databases
    assert ("timescaledb", managed.DATABASE) in cluster.extensions
    # the package made the main cluster, so none was created by hand
    assert not [c for c in host.log if c.startswith("pg_createcluster")]


def test_the_certificate_is_made_once_with_the_key_kept_private() -> None:
    host, _ = installed()
    assert host.modes[managed.TLS_KEY] == 0o640
    assert host.owners[managed.TLS_KEY] == ("root", "postgres")
    assert host.modes[managed.TLS_CERT] == 0o644
    assert "sparq-test-host" in next(c for c in host.log if c.startswith("openssl req"))


def test_a_second_run_changes_nothing_and_runs_no_changing_command() -> None:
    host, config = installed(TEST)
    before = len(host.log)
    again = run_install(host, config)
    assert not any(o.changed for o in again)
    later = host.log[before:]
    assert not [
        c
        for c in later
        if c.startswith(
            ("apt-get", "useradd", "openssl req", "pg_create", "pg_ctl", "apt-mark hold")
        )
    ]
    assert not [c for c in later if "CREATE" in c or "ALTER" in c]
    assert render_outcomes(again).endswith("0 of 8 steps changed something.")


def test_the_packages_install_at_the_pinned_versions_and_are_held() -> None:
    host, _ = installed()
    calls = [c for c in host.log if c.startswith("apt-get install")]
    assert len(calls) == 3
    assert "postgresql-18=18.6*" in calls[0] and "timescaledb-2-postgresql-18=2.31.0*" in calls[1]
    assert any(c.startswith("apt-mark hold") for c in host.log)


def test_a_package_at_another_version_is_replaced_by_the_pinned_one() -> None:
    host, config = FakeHost(), answers()
    host.installed["postgresql-18"] = "18.5-1.pgdg26.04+1"
    run_install(host, config)
    assert host.installed["postgresql-18"].startswith("18.6")


def test_a_missing_repository_version_refuses_before_anything_changes() -> None:
    host = FakeHost()
    host.candidates["timescaledb-2-postgresql-18"] = "2.30.0-1"
    with pytest.raises(
        InstallRefused, match=r"timescaledb-2-postgresql-18 2.31.0\* is not available"
    ):
        run_install(host, answers())
    assert not host.mutating()


def test_no_candidate_at_all_is_reported_as_none() -> None:
    host = FakeHost()
    host.candidates["postgresql-18"] = None
    with pytest.raises(InstallRefused, match="candidate: none"):
        run_install(host, answers())


def test_install_needs_root() -> None:
    with pytest.raises(InstallRefused, match="must run as root"):
        run_install(FakeHost(root=False), answers())


def test_a_failed_preflight_refuses_and_names_the_check() -> None:
    host = FakeHost()
    host.arch = "arm64"
    with pytest.raises(InstallRefused, match="preflight refused: architecture"):
        run_install(host, answers())
    assert not host.mutating()


def test_a_recovery_from_a_backup_is_not_built_yet() -> None:
    config = answers(
        '[database_host.recovery]\nfrom_backup = true\nbackup_set = "20261023-020000F"\n'
    )
    with pytest.raises(InstallRefused, match="K1b"):
        run_install(FakeHost(), config)


def test_a_command_that_fails_stops_the_install_with_its_reason() -> None:
    host = FakeHost()
    host.fail_on = "apt-get install"
    with pytest.raises(InstallRefused, match="a step failed: apt-get install"):
        run_install(host, answers())


def test_a_hand_edited_managed_file_is_put_back_and_the_cluster_restarted() -> None:
    host, config = installed()
    main = managed.main_cluster(config.installable_host())
    host.files[managed.conf_path(main)] = "fsync = off\n"
    before = len(host.log)
    outcomes = {o.step: o for o in run_install(host, config)}
    assert outcomes["main cluster"].changed
    assert host.files[managed.conf_path(main)] == managed.render_conf(main)
    assert any(c.startswith("pg_ctlcluster 18 main restart") for c in host.log[before:])


def test_a_stopped_cluster_is_started_again() -> None:
    host, config = installed()
    host.clusters["main"].status = "down"
    run_install(host, config)
    assert host.clusters["main"].status == "online"


def test_a_cluster_the_package_did_not_create_is_created_with_checksums() -> None:
    host, config = FakeHost(), answers()
    host.install_creates_main = False
    run_install(host, config)
    created = [c for c in host.log if c.startswith("pg_createcluster")]
    assert created and "--data-checksums" in created[0] and "--port 5432" in created[0]


def test_the_test_cluster_has_its_own_port_role_database_and_a_private_password() -> None:
    host, config = installed(TEST)
    cluster = host.clusters["sparq_test"]
    assert cluster.port == 5433 and cluster.status == "online"
    assert managed.TEST_ROLE in cluster.roles and managed.TEST_DATABASE in cluster.databases
    assert ("timescaledb", managed.TEST_DATABASE) in cluster.extensions
    password = host.files[managed.TEST_PASSWORD_FILE].strip()
    assert re.fullmatch(r"[0-9a-f]{32}", password)
    assert host.modes[managed.TEST_PASSWORD_FILE] == 0o600
    assert host.owners[managed.TEST_PASSWORD_FILE] == ("root", "root")
    assert any(f"PASSWORD '{password}'" in s for s in cluster.statements)
    scratch = managed.scratch_cluster(config.installable_host())
    assert (
        "hostssl sparq_test   sparq_test   192.0.2.30/32" in host.files[managed.hba_path(scratch)]
    )


def test_the_test_password_is_kept_across_runs() -> None:
    host, config = installed(TEST)
    first = host.files[managed.TEST_PASSWORD_FILE]
    run_install(host, config)
    assert host.files[managed.TEST_PASSWORD_FILE] == first


def test_without_a_test_cluster_none_is_made() -> None:
    host, _ = installed()
    assert "sparq_test" not in host.clusters
    assert managed.TEST_PASSWORD_FILE not in host.files


def test_the_main_cluster_listens_on_the_network_only_when_clients_are_listed() -> None:
    config = answers()
    with_clients = managed.render_conf(managed.main_cluster(config.installable_host()))
    assert "listen_addresses = '*'" in with_clients
    alone = parse_answers(BASE.replace('allowed_clients = ["192.0.2.20"]', ""))
    assert "listen_addresses = 'localhost'" in managed.render_conf(
        managed.main_cluster(alone.installable_host())
    )


def test_durability_settings_are_never_turned_off() -> None:
    conf = managed.render_conf(managed.main_cluster(answers().installable_host()))
    for line in ("fsync = on", "synchronous_commit = on", "full_page_writes = on"):
        assert line in conf
    assert "= off" not in conf.replace("timescaledb.telemetry_level = off", "")
    assert "password_encryption = scram-sha-256" in conf and "ssl = on" in conf


def test_the_access_rules_refuse_everything_not_listed() -> None:
    hba = managed.render_hba(managed.main_cluster(answers().installable_host()))
    assert "local   all   all   peer map=sparq" in hba
    assert "hostssl sparq   all   192.0.2.20/32   scram-sha-256" in hba
    assert "trust" not in hba and "hostnossl" not in hba
    v6 = parse_answers(BASE.replace("192.0.2.20", "2001:db8::20"))
    assert "2001:db8::20/128" in managed.render_hba(managed.main_cluster(v6.installable_host()))


def test_the_user_map_gives_root_the_superuser_and_the_service_account_the_owner_role() -> None:
    ident = managed.render_ident()
    assert "sparq   root       postgres" in ident
    assert f"sparq   {managed.SERVICE_USER}   {managed.OWNER_ROLE}" in ident


def test_clusters_are_read_from_pg_lsclusters() -> None:
    host, _ = installed(TEST)
    assert clusters(host) == {"main": (5432, "online"), "sparq_test": (5433, "online")}


def test_the_plan_names_exactly_the_steps_the_install_runs() -> None:
    for text in (TEST, ""):
        config = answers(text)
        planned = list(dict.fromkeys(s.area for s in plan_install(config)))
        ran = [o.step for o in run_install(FakeHost(), config)]
        # a step with nothing to say (the test cluster when it is off) runs and is not planned
        assert planned == [step for step in ran if step in planned]
        assert set(ran) - set(planned) <= {"test cluster"}


def test_the_plan_names_each_area_and_the_pinned_versions() -> None:
    text = render_plan(plan_install(answers(TEST)))
    assert "install postgresql-18 18.6* and hold it" in text
    assert "second cluster on port 5433" in text
    assert "create a new self-signed certificate" in text
    assert "install pgbackrest 2.59* and hold it" in text


def test_the_service_account_is_a_system_account_that_cannot_log_in() -> None:
    host, _ = installed()
    command = next(c for c in host.log if c.startswith("useradd"))
    assert "--system" in command and "--shell /usr/sbin/nologin" in command
    assert "--home-dir /var/lib/sparq" in command and command.endswith(" sparq")


def test_the_certificate_lasts_ten_years_and_is_an_elliptic_curve_key() -> None:
    host, _ = installed()
    command = next(c for c in host.log if c.startswith("openssl req"))
    assert "-days 3650" in command and "ec_paramgen_curve:prime256v1" in command
    assert "-nodes" in command  # the key is protected by file permissions, not a passphrase


def test_the_test_role_is_a_superuser_so_the_tests_can_create_the_extension() -> None:
    host, _ = installed(TEST)
    create = next(s for s in host.clusters["sparq_test"].statements if s.startswith("CREATE ROLE"))
    assert "LOGIN SUPERUSER PASSWORD" in create


def test_public_connect_to_the_main_database_is_revoked() -> None:
    host, _ = installed()
    statements = host.clusters["main"].statements
    assert "REVOKE CONNECT ON DATABASE sparq FROM PUBLIC" in statements
    owner = next(s for s in statements if s.startswith("CREATE ROLE"))
    assert owner == "CREATE ROLE sparq_owner LOGIN"  # an ordinary role, not a superuser


def test_the_two_clusters_cannot_share_a_port() -> None:
    with pytest.raises(AnswersError, match="a port of its own"):
        parse_answers(BASE + "[database_host.test_cluster]\nenabled = true\nport = 5432\n")
    parse_answers(BASE + "[database_host.test_cluster]\nenabled = false\nport = 5432\n")


def test_a_second_run_is_not_refused_by_the_ports_the_first_run_opened() -> None:
    """Found on the real server host: the first install started the production cluster (5432) and
    the test cluster (5433), then the second run's own preflight refused them as unexpected
    listeners because it forgot the test cluster's port."""
    host, config = installed(TEST)
    listening = host.run(["ss", "-H", "-ltn"])[1]
    assert "0.0.0.0:5432" in listening and "0.0.0.0:5433" in listening
    run_install(host, config)  # must not raise


def test_the_ports_allowed_to_listen_are_the_web_port_and_each_cluster_with_clients() -> None:
    from sparq_server.admin.managed import network_ports

    both = answers(TEST).installable_host()
    assert network_ports(both) == {443, 5432, 5433}
    alone = parse_answers(BASE.replace('allowed_clients = ["192.0.2.20"]', "")).installable_host()
    assert network_ports(alone) == {443}  # no listed clients: PostgreSQL stays on localhost
    quiet = answers(
        "[database_host.test_cluster]\nenabled = true\nport = 5433\n"
    ).installable_host()
    assert network_ports(quiet) == {443, 5432}  # a test cluster with no clients is local only
    off = answers(
        '[database_host.test_cluster]\nenabled = false\nallowed_clients = ["192.0.2.30"]\n'
    )
    assert network_ports(off.installable_host()) == {443, 5432}


def test_an_unlisted_port_is_still_refused_after_an_install() -> None:
    host, config = installed(TEST)
    host.extra_listeners.append("LISTEN 0 128 0.0.0.0:8080 0.0.0.0:*")
    with pytest.raises(InstallRefused, match="preflight refused: listeners"):
        run_install(host, config)
