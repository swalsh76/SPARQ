"""The ingest service's configuration, credentials, disk guard and start-up refusals (no DB)."""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sparq_server.ingest import service
from sparq_server.ingest.service import (
    EXIT_REFUSED,
    DiskFull,
    ServiceConfig,
    ServiceError,
    disk_guard,
    load_signer,
    parse_config,
    run_service,
    used_percent,
)

GOOD = """
environment = "production"
socket_directory = "/var/run/postgresql"
port = 5432
database = "sparq"
role = "sparq_ingest"
data_directory = "/var/lib/postgresql/18/main"
"""


def write_key(directory: Path, mode: int = 0o600) -> None:
    key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    (directory / "kalshi-key-id").write_text("00000000-key-id\n", encoding="utf-8")
    target = directory / "kalshi-private-key"
    target.write_bytes(key)
    target.chmod(mode)


def usage(percent: float) -> SimpleNamespace:
    return SimpleNamespace(used=int(percent * 10), total=1000)


# --- configuration --------------------------------------------------------------------------------


def test_a_complete_configuration_is_read_with_the_documented_defaults() -> None:
    config = parse_config(GOOD)
    assert (config.role, config.port, config.disk_stop_percent) == ("sparq_ingest", 5432, 90)
    assert (config.backfill_rate_per_second, config.discovery_rate_per_second) == (2.0, 1.5)


@pytest.mark.parametrize(
    "change",
    [
        ('environment = "production"', 'environment = "demo"'),
        ('role = "sparq_ingest"', 'role = "sparq_ingest"\nsurprise = 1'),
        ('socket_directory = "/var/run/postgresql"', 'socket_directory = "run/postgresql"'),
        ('role = "sparq_ingest"', 'role = "sparq; DROP TABLE trade"'),
        ("port = 5432", "port = 80"),
        ("port = 5432", "port = 5432\ndisk_stop_percent = 100"),
        ("port = 5432", "port = 5432\ndisk_stop_percent = 20"),
    ],
)
def test_a_configuration_that_could_do_harm_or_is_misspelt_is_refused(
    change: tuple[str, str],
) -> None:
    with pytest.raises(ServiceError, match="not valid"):
        parse_config(GOOD.replace(*change))


def test_text_that_is_not_toml_is_refused_with_a_service_error() -> None:
    with pytest.raises(ServiceError):
        parse_config("this = is = not toml")


def test_a_missing_field_is_named() -> None:
    with pytest.raises(ServiceError, match="data_directory"):
        parse_config(GOOD.replace('data_directory = "/var/lib/postgresql/18/main"', ""))


# --- credentials ----------------------------------------------------------------------------------


def test_the_key_is_loaded_from_the_credentials_directory(tmp_path: Path) -> None:
    write_key(tmp_path)
    signer = load_signer(tmp_path)
    headers = signer.headers("GET", "/trade-api/ws/v2")
    assert headers["KALSHI-ACCESS-KEY"] == "00000000-key-id"


def test_with_no_credentials_directory_the_service_will_not_start() -> None:
    with pytest.raises(ServiceError, match="systemd unit"):
        load_signer(None)


def test_a_missing_key_file_is_refused_without_naming_a_path(tmp_path: Path) -> None:
    (tmp_path / "kalshi-key-id").write_text("an-id", encoding="utf-8")
    with pytest.raises(ServiceError) as caught:
        load_signer(tmp_path)
    assert str(tmp_path) not in str(caught.value) and caught.value.__cause__ is None
    assert "an-id" not in str(caught.value)


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX")
@pytest.mark.parametrize("mode", [0o400, 0o440, 0o444, 0o644])
def test_a_credential_is_accepted_whatever_its_file_mode(tmp_path: Path, mode: int) -> None:
    """A systemd credential sits in a directory only the service can enter, so its own mode is
    not the protection (the first real start refused a credential for being 'too open')."""
    write_key(tmp_path, mode=mode)
    assert load_signer(tmp_path).headers("GET", "/trade-api/ws/v2")


def test_a_file_that_is_not_a_key_is_refused(tmp_path: Path) -> None:
    (tmp_path / "kalshi-key-id").write_text("an-id", encoding="utf-8")
    key = tmp_path / "kalshi-private-key"
    key.write_text("not a key", encoding="utf-8")
    key.chmod(0o600)
    with pytest.raises(ServiceError, match="not an unencrypted PEM private key"):
        load_signer(tmp_path)


# --- the disk guard -------------------------------------------------------------------------------


def test_the_used_share_of_the_drive_is_measured_from_used_over_total() -> None:
    assert used_percent("/x", lambda _: usage(42.5)) == pytest.approx(42.5)


async def test_the_guard_stops_the_service_at_the_limit_and_not_before() -> None:
    config = parse_config(GOOD)
    readings = iter([50, 89.9, 90.0])
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with pytest.raises(DiskFull, match="90.0% full"):
        await disk_guard(config, usage=lambda _: usage(next(readings)), sleep=sleep)
    assert sleeps == [config.disk_check_seconds, config.disk_check_seconds]


async def test_the_guard_keeps_watching_while_there_is_room() -> None:
    config = parse_config(GOOD)
    task = asyncio.create_task(disk_guard(config, usage=lambda _: usage(10), sleep=asyncio.sleep))
    await asyncio.sleep(0.05)
    assert not task.done()
    task.cancel()


# --- starting up ----------------------------------------------------------------------------------


def test_a_missing_configuration_file_ends_the_process_without_a_restart(tmp_path: Path) -> None:
    assert service.main([str(tmp_path / "absent.toml")]) == EXIT_REFUSED


def test_missing_credentials_end_the_process_without_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "ingest.toml"
    config.write_text(GOOD, encoding="utf-8")
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    assert service.main([str(config)]) == EXIT_REFUSED


def test_the_exit_codes_the_unit_refuses_to_restart_on_match_the_service() -> None:
    from sparq_server.admin import managed

    assert (managed.EXIT_DISK_FULL, managed.EXIT_REFUSED) == (
        service.EXIT_DISK_FULL,
        service.EXIT_REFUSED,
    )
    assert isinstance(parse_config(GOOD), ServiceConfig)


async def test_a_data_drive_that_cannot_be_measured_is_refused_before_anything_starts() -> None:
    config = parse_config(GOOD.replace("/var/lib/postgresql/18/main", "/no/such/directory"))

    async def must_not_connect() -> None:
        raise AssertionError("the database was touched before the refusal")

    with pytest.raises(ServiceError, match="cannot measure the data drive at /no/such/directory"):
        await run_service(
            config, None, asyncio.Event(),  # type: ignore[arg-type]
            connect=must_not_connect,
        )  # fmt: skip


def test_the_reason_a_key_was_refused_is_named_without_naming_the_key(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric import rsa

    (tmp_path / "kalshi-key-id").write_text("secret-key-id", encoding="utf-8")
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    key = tmp_path / "kalshi-private-key"
    key.write_bytes(rsa_key)
    key.chmod(0o600)
    with pytest.raises(ServiceError) as caught:
        load_signer(tmp_path)
    message = str(caught.value)
    assert "RSAPrivateKey" in message and "only Ed25519" in message
    assert str(tmp_path) not in message and "secret-key-id" not in message
    (tmp_path / "kalshi-key-id").unlink()
    with pytest.raises(ServiceError, match="a credential file is missing"):
        load_signer(tmp_path)
    write_key(tmp_path)  # a good key, so the id is the only fault
    (tmp_path / "kalshi-key-id").write_text("   ", encoding="utf-8")
    with pytest.raises(ServiceError, match="the key id is empty"):
        load_signer(tmp_path)


def test_a_first_full_discovery_cycle_fits_inside_the_wait_for_a_lifecycle_gap() -> None:
    """The first real cycle took about 14 minutes; the old 15-minute wait failed gaps for it."""
    import inspect

    from sparq_server.ingest.pump import EventPump

    default = inspect.signature(EventPump.__init__).parameters["lifecycle_wait"].default
    assert default >= 3000


def test_the_http_library_is_kept_quiet_in_the_journal(tmp_path: Path) -> None:
    import logging

    service.main([str(tmp_path / "absent.toml")])  # logging is configured before anything fails
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
