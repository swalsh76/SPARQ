import dataclasses

import pytest
from fakesystem import MEMINFO, OS_RELEASE, SS_QUIET, FakeSystem, healthy
from sparq_server.admin.answers import DatabaseHost, parse_answers
from sparq_server.admin.preflight import Check, refused, render, run_preflight

CONFIG_TEXT = """
role = "database_host"
[database_host.backup]
target = "/mnt/sparq-backup"
[database_host.postgres]
allowed_clients = ["192.0.2.20"]
"""


def config() -> DatabaseHost:
    host = parse_answers(CONFIG_TEXT).database_host
    assert host is not None
    return host


def by_name(checks: list[Check]) -> dict[str, Check]:
    return {c.name: c for c in checks}


def test_the_machine_alone_passes_before_the_install() -> None:
    checks = run_preflight(healthy())
    assert not refused(checks)
    named = by_name(checks)
    assert named["operating system"].detail == "Ubuntu 26.04"
    assert "not installed yet" in named["PostgreSQL 18"].detail
    assert "backup share" not in named  # no answers, so no share to check


def test_a_volatile_write_cache_is_a_warning_that_points_at_the_cord_pulls() -> None:
    drive = by_name(run_preflight(healthy()))["drive"]
    assert drive.status == "warn" and "K1a.3" in drive.detail


@pytest.mark.parametrize(("cache", "status"), [("write through\n", "ok"), ("weird\n", "warn")])
def test_the_other_write_cache_modes(cache: str, status: str) -> None:
    system = healthy()
    system.files["/sys/block/nvme0n1/queue/write_cache"] = cache
    assert by_name(run_preflight(system))["drive"].status == status


def test_a_drive_that_is_not_nvme_is_a_warning() -> None:
    system = healthy()
    system.paths.discard("/sys/block/nvme0n1")
    assert by_name(run_preflight(system))["drive"].status == "warn"


def test_a_checked_machine_with_answers_also_checks_the_share() -> None:
    checks = run_preflight(healthy(), config())
    assert by_name(checks)["backup share"].status == "ok"
    system = healthy()
    del system.files["/mnt/sparq-backup/.sparq-backup-marker"]
    missing = by_name(run_preflight(system, config()))["backup share"]
    assert missing.status == "fail" and ".sparq-backup-marker" in missing.detail


@pytest.mark.parametrize(
    ("version", "ident", "status"),
    [("24.04", "ubuntu", "ok"), ("22.04", "ubuntu", "fail"), ("12", "debian", "fail")],
)
def test_only_supported_ubuntu_releases_pass(version: str, ident: str, status: str) -> None:
    system = healthy()
    system.files["/etc/os-release"] = f'ID={ident}\nVERSION_ID="{version}"\n'
    assert by_name(run_preflight(system))["operating system"].status == status


def test_a_missing_os_release_fails() -> None:
    system = healthy()
    del system.files["/etc/os-release"]
    assert by_name(run_preflight(system))["operating system"].status == "fail"


@pytest.mark.parametrize(("arch", "status"), [("x86_64", "ok"), ("AMD64", "ok"), ("arm64", "fail")])
def test_the_architecture_must_be_x86_64(arch: str, status: str) -> None:
    system = dataclasses.replace(healthy(), arch=arch)
    assert by_name(run_preflight(system))["architecture"].status == status


def test_memory_must_reach_the_minimum_and_be_readable() -> None:
    small = healthy()
    small.files["/proc/meminfo"] = "MemTotal: 8388608 kB\n"
    assert by_name(run_preflight(small))["memory"].status == "fail"
    gone = healthy()
    del gone.files["/proc/meminfo"]
    assert "cannot read" in by_name(run_preflight(gone))["memory"].detail
    assert MEMINFO and OS_RELEASE  # the healthy fixtures use these


def test_free_space_must_cover_the_budget_plus_headroom() -> None:
    tight = dataclasses.replace(healthy(), free=500 * 10**9)
    assert by_name(run_preflight(tight))["free space"].status == "fail"
    assert (
        by_name(run_preflight(dataclasses.replace(healthy(), free=None)))["free space"].status
        == "fail"
    )
    # a smaller budget in the answers needs less room
    small_budget = parse_answers(
        CONFIG_TEXT + "[database_host.storage]\nbudget_gb = 300\n"
    ).database_host
    assert small_budget is not None
    assert by_name(run_preflight(tight, small_budget))["free space"].status == "ok"


def test_the_clock_must_be_synchronised_and_the_zone_should_be_utc() -> None:
    system = healthy()
    system.commands[("timedatectl", "show", "-p", "NTPSynchronized", "--value")] = (0, "no\n")
    system.commands[("timedatectl", "show", "-p", "Timezone", "--value")] = (0, "America/Denver\n")
    named = by_name(run_preflight(system))
    assert named["time sync"].status == "fail"
    assert named["time zone"].status == "warn"
    cannot = healthy()
    del cannot.commands[("timedatectl", "show", "-p", "NTPSynchronized", "--value")]
    assert by_name(run_preflight(cannot))["time sync"].status == "fail"


def test_an_unexpected_listener_on_the_network_fails_and_loopback_ones_do_not() -> None:
    system = healthy()
    system.commands[("ss", "-H", "-ltn")] = (0, SS_QUIET + "LISTEN 0 128 0.0.0.0:8080 0.0.0.0:*\n")
    check = by_name(run_preflight(system))["listeners"]
    assert check.status == "fail" and "0.0.0.0:8080" in check.detail
    assert by_name(run_preflight(healthy()))["listeners"].status == "ok"


def test_the_configured_web_and_database_ports_are_allowed_to_listen() -> None:
    system = healthy()
    system.commands[("ss", "-H", "-ltn")] = (
        0,
        SS_QUIET + "LISTEN 0 128 0.0.0.0:443 0.0.0.0:*\nLISTEN 0 128 192.0.2.5:5432 0.0.0.0:*\n",
    )
    assert by_name(run_preflight(system, config()))["listeners"].status == "ok"
    # without a listed client, PostgreSQL has no reason to face the network
    local_only = parse_answers(
        CONFIG_TEXT.replace('allowed_clients = ["192.0.2.20"]', "")
    ).database_host
    assert local_only is not None
    assert by_name(run_preflight(system, local_only))["listeners"].status == "fail"


def test_a_failed_port_listing_fails_the_check() -> None:
    system = healthy()
    system.commands[("ss", "-H", "-ltn")] = (1, "")
    assert by_name(run_preflight(system))["listeners"].status == "fail"


def test_short_or_odd_lines_in_the_port_listing_are_skipped() -> None:
    system = healthy()
    system.commands[("ss", "-H", "-ltn")] = (0, "\nLISTEN 0\nLISTEN 0 1 weird 0.0.0.0:*\n")
    assert by_name(run_preflight(system))["listeners"].status == "ok"


def test_installed_database_packages_are_reported_with_their_versions() -> None:
    system = healthy()
    system.commands[("dpkg-query", "-W", "-f=${Version}", "postgresql-18")] = (0, "18.3-1\n")
    named = by_name(run_preflight(system))
    assert named["PostgreSQL 18"].detail == "installed, 18.3-1"


def test_a_missing_tpm_is_a_warning() -> None:
    system = healthy()
    system.paths.discard("/sys/class/tpm/tpm0")
    assert by_name(run_preflight(system))["TPM 2.0"].status == "warn"
    assert by_name(run_preflight(healthy()))["TPM 2.0"].status == "ok"


def test_a_refused_report_names_the_failures_and_says_nothing_changed() -> None:
    system = healthy()
    system.files["/etc/os-release"] = "ID=debian\n"
    checks = run_preflight(system)
    assert refused(checks)
    text = render(checks)
    assert "REFUSING: operating system" in text and "Nothing was changed" in text
    assert "FAIL  operating system" in text


def test_a_passing_report_says_so() -> None:
    text = render(run_preflight(healthy()))
    assert text.endswith("All checks passed. Nothing was changed.")
    assert "REFUSING" not in text


def test_the_fake_machine_stays_independent_between_tests() -> None:
    assert healthy() is not healthy()
    assert FakeSystem().machine() == "x86_64"


def test_a_share_this_account_may_not_enter_is_a_warning_that_says_to_use_sudo() -> None:
    """The share is mounted for root only (SERVER.md step 4), so a plain account cannot even ask
    whether the marker exists. That used to crash preflight with a PermissionError."""
    system = healthy()
    system.denied_paths = {"/mnt/sparq-backup"}
    check = by_name(run_preflight(system, config()))["backup share"]
    assert check.status == "warn" and "sudo" in check.detail
    assert not refused(run_preflight(system, config()))


def test_root_sees_the_share_that_a_plain_account_cannot() -> None:
    system = healthy()
    system.denied_paths = {"/mnt/sparq-backup"}
    system.root = True
    assert by_name(run_preflight(system, config()))["backup share"].status == "ok"


def test_a_share_that_is_truly_missing_still_fails() -> None:
    system = healthy()
    del system.files["/mnt/sparq-backup/.sparq-backup-marker"]
    assert by_name(run_preflight(system, config()))["backup share"].status == "fail"


def test_a_tpm_systemd_cannot_use_is_a_warning_that_names_the_cause() -> None:
    system = healthy()
    system.commands[("systemd-analyze", "has-tpm2")] = (1, "partial" + chr(10) + "-libraries")
    check = by_name(run_preflight(system))["TPM 2.0"]
    assert check.status == "warn" and "partial" in check.detail and "libtss2-rc0t64" in check.detail


def test_output_that_is_not_an_answer_is_reported_as_unknown() -> None:
    system = healthy()
    system.commands[("systemd-analyze", "has-tpm2")] = (127, "")
    assert "unknown" in by_name(run_preflight(system))["TPM 2.0"].detail
