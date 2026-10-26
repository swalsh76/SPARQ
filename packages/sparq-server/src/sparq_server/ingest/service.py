"""The ingest service: the one process that records Kalshi's live data into the database.

``python -m sparq_server.ingest`` runs it under ``systemd`` as the ``sparq`` account. It joins the
pieces built in K1a.5 to K1a.7: the signed feed, the batched writer, discovery, the exchange
poller, the trade backfill and the event pump. Any of them failing for good ends the process with
a non-zero status and ``systemd`` starts it again; the pump then records the outage as gaps from the
last heartbeat, so a crash costs a gap row and not silence.

* **Key.** The data-only key reaches the process only as ``systemd`` credentials (decrypted by the
  machine's TPM into a private directory); no environment variable and no file in ``/etc`` holds it.
* **Database.** The service connects over the local socket as a role of its own that can write the
  ingest tables and change nothing else; peer authentication means it keeps no password.
* **Disk.** There is no retention or governor yet (K1b), so above ``disk_stop_percent`` on the data
  drive the service stops and is *not* restarted (exit status ``EXIT_DISK_FULL``), rather than fill
  the drive under PostgreSQL. The reason is stored in ``ingest_state`` for ``ingest-status``.
"""

import asyncio
import contextlib
import logging
import os
import re
import shutil
import signal
import tomllib
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sparq_core.auth import AuthError, DataSigner, parse_private_key
from sparq_core.config import Environment, KalshiConfig
from sparq_core.endpoints import MarketData
from sparq_core.rest import KalshiPublicClient
from sparq_core.ws import FeedConfig, KalshiFeed
from sparq_core.ws_models import MARKET_LIFECYCLE, TICKER, TRADE

from sparq_server.db.migrate import HEAD
from sparq_server.ingest import state
from sparq_server.ingest.backfill import Backfiller
from sparq_server.ingest.discovery import Discovery, DiscoveryRunner
from sparq_server.ingest.exchange import ExchangePoller
from sparq_server.ingest.pump import EventPump
from sparq_server.ingest.writer import IngestWriter, WriterConfig
from sparq_server.names import (
    EXIT_DISK_FULL,
    EXIT_REFUSED,
    INGEST_CONFIG,
    KEY_CREDENTIAL,
    KEY_ID_CREDENTIAL,
)

log = logging.getLogger(__name__)

CHANNELS = (TICKER, TRADE, MARKET_LIFECYCLE)  # every non-combo market, no order-book deltas


class ServiceError(Exception):
    """The service cannot start; the message says what to fix."""


class DiskFull(Exception):
    """The data drive is above the stop level."""


class ServiceConfig(BaseModel):
    """``/etc/sparq/ingest.toml``, rendered by ``sparq-admin install`` from the answer file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: Literal["production"]  # the demo site has no live market data worth recording
    socket_directory: str = Field(pattern=r"^/[^\s]*$")
    port: int = Field(ge=1024, le=65535)
    database: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    role: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    data_directory: str = Field(pattern=r"^/[^\s]*$")  # the drive the disk guard watches
    disk_stop_percent: int = Field(default=90, ge=50, le=99)
    disk_check_seconds: float = Field(default=60.0, gt=0)
    backfill_rate_per_second: float = Field(default=2.0, gt=0)  # the shares of KAPS decision 22
    discovery_rate_per_second: float = Field(default=1.5, gt=0)


def parse_config(text: str) -> ServiceConfig:
    try:
        return ServiceConfig.model_validate(tomllib.loads(text))
    except (tomllib.TOMLDecodeError, ValidationError) as error:
        raise ServiceError(f"{INGEST_CONFIG} is not valid: {error}") from error


def load_signer(credentials: Path | None) -> DataSigner:
    """The data key from the credentials directory ``systemd`` provides (never printed)."""
    if credentials is None:
        raise ServiceError("no credentials directory: run the service from its systemd unit")
    try:
        key_id = (credentials / KEY_ID_CREDENTIAL).read_text(encoding="utf-8").strip()
        # not load_private_key: its file-mode rule is for key files in home directories, and a
        # systemd credential's protection is the private directory it sits in
        pem = (credentials / KEY_CREDENTIAL).read_bytes()
        return DataSigner(key_id, parse_private_key(pem, "the key credential"))
    except (OSError, AuthError) as error:
        # only a category reaches the message: the cause itself could name the key or its path
        raise ServiceError(
            f"the data key credentials ({KEY_ID_CREDENTIAL}, {KEY_CREDENTIAL}) are unusable: "
            f"{_why(error)}; see the server guide, step 'The ingest service'"
        ) from None


def _why(error: Exception) -> str:
    """What was wrong with the key, in words that carry no path, id or key material."""
    if isinstance(error, FileNotFoundError):
        return "a credential file is missing"
    if isinstance(error, OSError):
        return "a credential file cannot be read"
    text = str(error)
    if match := re.search(r"unsupported key type (\w+)", text):
        return f"the key is a {match.group(1)}, and only Ed25519 keys are supported"
    if "not an unencrypted PEM" in text:
        return "the key file is not an unencrypted PEM private key"
    if "key id is empty" in text:
        return "the key id is empty"
    return "the key could not be loaded"


def used_percent(path: str, usage: Callable[[str], Any] = shutil.disk_usage) -> float:
    total = usage(path)
    return float(100.0 * total.used / total.total)


async def disk_guard(
    config: ServiceConfig,
    *,
    usage: Callable[[str], Any] = shutil.disk_usage,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Raise ``DiskFull`` once the data drive is at or above the stop level."""
    while True:
        percent = used_percent(config.data_directory, usage)
        if percent >= config.disk_stop_percent:
            raise DiskFull(
                f"{config.data_directory} is {percent:.1f}% full, at or above "
                f"{config.disk_stop_percent}%"
            )
        await sleep(config.disk_check_seconds)


Connect = Callable[[], Awaitable[asyncpg.Connection]]


def connector(config: ServiceConfig) -> Connect:
    async def connect() -> asyncpg.Connection:
        return await asyncpg.connect(
            host=config.socket_directory,
            port=config.port,
            user=config.role,
            database=config.database,
        )

    return connect


async def check_schema(connect: Connect) -> None:
    """Refuse to run against a database the migrations have not brought to this release."""
    conn = await connect()
    try:
        found = await conn.fetchval("SELECT version_num FROM alembic_version")
    finally:
        await conn.close()
    if found != HEAD:
        raise ServiceError(
            f"the database is at revision {found!r} but this release needs {HEAD!r}: "
            "run sparq-admin migrate first"
        )


async def record_stop(connect: Connect, reason: str) -> None:
    with contextlib.suppress(Exception):  # the reason is a courtesy; never mask the real exit
        conn = await connect()
        try:
            await state.put(conn, state.STOPPED_REASON, reason[:300])
        finally:
            await conn.close()


def make_feed(config: ServiceConfig, signer: DataSigner) -> KalshiFeed:
    """The feed for the configured environment (always production: see ``ServiceConfig``)."""
    url = KalshiConfig(environment=Environment(config.environment)).ws_url
    return KalshiFeed(signer, FeedConfig(url=url))


async def run_service(
    config: ServiceConfig,
    signer: DataSigner,
    stop: asyncio.Event,
    *,
    connect: Connect | None = None,
    feed: Any | None = None,
    market_data: tuple[Any, Any] | None = None,
    writer_config: WriterConfig | None = None,
    guard: Callable[[], Coroutine[Any, Any, None]] | None = None,
) -> None:
    """Run until ``stop`` is set (a clean stop) or a part fails for good (raises).

    The keyword arguments let the tests supply fakes; ``systemd`` runs it with none of them.
    """
    connect = connect or connector(config)
    if guard is None:
        try:
            used_percent(config.data_directory)  # fail now, clearly, not as a crash loop later
        except OSError as error:
            raise ServiceError(
                f"cannot measure the data drive at {config.data_directory}: {error.strerror}"
            ) from None
    await check_schema(connect)
    await record_stop(connect, "running")
    environment = Environment(config.environment)
    async with contextlib.AsyncExitStack() as stack:
        if market_data is None:
            clients = []
            for rate in (config.backfill_rate_per_second, config.discovery_rate_per_second):
                settings = KalshiConfig(
                    environment=environment, rate_limit_per_second=rate, burst_requests=rate
                )
                clients.append(await stack.enter_async_context(KalshiPublicClient(settings)))
            market_data = (MarketData(clients[0]), MarketData(clients[1]))
        backfill_data, other_data = market_data
        feed = feed or make_feed(config, signer)
        writer = await stack.enter_async_context(IngestWriter(connect, writer_config))
        backfiller = Backfiller(backfill_data, writer, connect)
        discovery = DiscoveryRunner(Discovery(other_data, connect))
        poller = ExchangePoller(other_data, connect)
        pump = EventPump(feed, writer, connect, backfiller, discovery)
        await feed.start()
        for channel in CHANNELS:
            await feed.subscribe(channel)
        pump_task = asyncio.create_task(pump.run(), name="pump")
        helpers = [
            asyncio.create_task(backfiller.run(), name="backfill"),
            asyncio.create_task(discovery.run(), name="discovery"),
            asyncio.create_task(poller.run(), name="exchange"),
            asyncio.create_task((guard or (lambda: disk_guard(config)))(), name="disk"),
        ]
        waiter = asyncio.create_task(stop.wait(), name="stop")
        everything = {pump_task, waiter, *helpers}
        failure: BaseException | None = None
        try:
            done, _ = await asyncio.wait(everything, return_when=asyncio.FIRST_COMPLETED)
            for task in done - {waiter}:
                failure = failure or task.exception() or ServiceError(f"{task.get_name()} ended")
        finally:
            # the feed closes first, so the pump drains what it already holds and saves the
            # heartbeat; then the helpers stop; the writer flushes as the stack unwinds
            await feed.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(pump_task), 30)
            for task in [*helpers, waiter, pump_task]:
                task.cancel()
            await asyncio.gather(*helpers, waiter, pump_task, return_exceptions=True)
        if failure is not None:
            raise failure


async def supervise(
    config: ServiceConfig, signer: DataSigner, stop: asyncio.Event, **parts: Any
) -> int:
    """Run the service and turn how it ended into the process's exit status and its stored reason.

    ``parts`` are the fakes ``run_service`` accepts; the process passes none."""
    connect = parts.pop("connect", None) or connector(config)
    try:
        await run_service(config, signer, stop, connect=connect, **parts)
    except DiskFull as error:
        log.error("stopping, and not restarting: %s", error)
        await record_stop(connect, f"disk full: {error}")
        return EXIT_DISK_FULL
    except ServiceError as error:
        log.error("%s", error)
        return EXIT_REFUSED
    except Exception as error:
        log.exception("the ingest failed")
        await record_stop(connect, f"failed: {type(error).__name__}: {error}")
        return 1
    await record_stop(connect, "stopped cleanly")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )  # journald adds the time
    for noisy in ("httpx", "httpcore"):  # a line per request: hundreds a cycle, none of it news
        logging.getLogger(noisy).setLevel(logging.WARNING)
    config_path = Path((argv or [INGEST_CONFIG])[0])
    try:
        config = parse_config(config_path.read_text(encoding="utf-8"))
        credentials = os.environ.get("CREDENTIALS_DIRECTORY")
        signer = load_signer(Path(credentials) if credentials else None)
    except (OSError, ServiceError) as error:
        log.error("%s", error)
        return EXIT_REFUSED

    async def run() -> int:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stop.set)
        return await supervise(config, signer, stop)

    return asyncio.run(run())
