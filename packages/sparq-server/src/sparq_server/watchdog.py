"""The ingest watchdog: tells the owner's phone when the ingest needs attention.

``python -m sparq_server.watchdog`` runs once each minute from a ``systemd`` timer, as the ``sparq``
account, in a process of its own so that it can report on an ingest that is dead. It looks at the
service, the feed's heartbeat, the gap log, the data drive and the database, and sends a message to
a private ntfy topic:

* when a problem has held for ``confirm_checks`` checks in a row (one blip never alerts);
* again every ``reminder_hours`` while it lasts; and
* once when it clears.

Its memory of what it has reported is a small file in its own state directory, not the database,
so it still works, and does not repeat itself, while the database is down. A message that cannot be
delivered is not counted as sent: it is tried again at the next check.

Messages carry status text only (no market data, no account value, no key). The topic is the
secret that lets a phone subscribe; it comes from a ``systemd`` credential and is never logged,
including inside error messages, which would otherwise carry the URL that contains it.

What it cannot do: report that the whole server host or its network is gone. That needs a check
from outside (parked, PLAN.md section 9).
"""

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import tomllib
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sparq_server.ingest import gaps
from sparq_server.ingest.state import HEARTBEAT, STOPPED_REASON
from sparq_server.names import ALERT_CREDENTIAL, ALERTS_CONFIG, EXIT_REFUSED, INGEST_UNIT

log = logging.getLogger(__name__)

STATE_NAME = "state.json"
SEND_TIMEOUT = 15.0
PROBE_TIMEOUT = 10.0


class WatchdogError(Exception):
    """The watchdog cannot run; the message says what to fix and never carries the topic."""


class WatchdogConfig(BaseModel):
    """``/etc/sparq/alerts.toml``, rendered by ``sparq-admin install`` from the answer file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    server: str = Field(pattern=r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$")
    socket_directory: str = Field(pattern=r"^/[^\s]*$")
    port: int = Field(ge=1024, le=65535)
    database: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    role: str = Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    data_directory: str = Field(pattern=r"^/[^\s]*$")
    silence_seconds: int = Field(default=600, ge=60, le=86_400)
    disk_warn_percent: int = Field(default=80, ge=50, le=99)
    disk_stop_percent: int = Field(default=90, ge=50, le=99)  # the ingest's own stop level
    reminder_hours: int = Field(default=6, ge=1, le=168)
    confirm_checks: int = Field(default=2, ge=1, le=10)
    stuck_gap_minutes: int = Field(default=60, ge=5, le=1440)
    restart_limit: int = Field(default=3, ge=2, le=50)  # restarts within restart_window checks
    restart_window: int = Field(default=15, ge=3, le=120)


def parse_config(text: str) -> WatchdogConfig:
    try:
        return WatchdogConfig.model_validate(tomllib.loads(text))
    except (tomllib.TOMLDecodeError, ValidationError) as error:
        raise WatchdogError(f"{ALERTS_CONFIG} is not valid: {error}") from error


# --- what is seen -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    """One look at the ingest. A field is None when it could not be read."""

    active: bool
    state_text: str  # systemd's ActiveState, e.g. active, failed, inactive
    restarts: int | None  # systemd's NRestarts
    db_ok: bool
    heartbeat_age: float | None  # seconds since the feed's last frame
    stopped_reason: str | None  # why the last run ended, as the service stored it
    failed_gaps: int  # trades gaps that ended failed in the last 6 hours
    stuck_gaps: int  # non-lifecycle gaps unfinished for too long
    disk_percent: float | None


@dataclass(frozen=True, slots=True)
class Problem:
    name: str
    text: str
    urgent: bool = False


def evaluate(obs: Observation, config: WatchdogConfig, restart_history: list[int]) -> list[Problem]:
    """The problems this look shows. ``restart_history`` holds the restart count of earlier checks,
    oldest first, so a service that keeps restarting is seen even though each start looks fine."""
    problems: list[Problem] = []
    if not obs.active:
        why = ""
        if obs.stopped_reason and obs.stopped_reason.startswith(("disk full", "failed")):
            why = f"; last stop: {obs.stopped_reason}"
        problems.append(
            Problem("service", f"the ingest is not running ({obs.state_text}){why}", urgent=True)
        )
    elif obs.heartbeat_age is not None and obs.heartbeat_age > config.silence_seconds:
        minutes = obs.heartbeat_age / 60
        problems.append(
            Problem(
                "silent", f"the ingest is running but no frame has arrived for {minutes:.0f} min"
            )
        )
    if obs.restarts is not None and restart_history:
        recent = obs.restarts - restart_history[0]
        if recent >= config.restart_limit:
            problems.append(Problem("restarting", f"the ingest restarted {recent} times recently"))
    if obs.disk_percent is not None and obs.disk_percent >= config.disk_warn_percent:
        problems.append(
            Problem(
                "disk",
                f"the data drive is {obs.disk_percent:.0f}% full "
                f"(the ingest stops at {config.disk_stop_percent}%)",
            )
        )
    if not obs.db_ok:
        problems.append(Problem("database", "PostgreSQL cannot be reached", urgent=True))
    if obs.failed_gaps:
        problems.append(
            Problem("gap-failed", f"{obs.failed_gaps} trades gap(s) failed to backfill (last 6 h)")
        )
    if obs.stuck_gaps:
        problems.append(
            Problem(
                "gap-stuck",
                f"{obs.stuck_gaps} gap(s) unfinished for over {config.stuck_gap_minutes} min",
            )
        )
    return problems


# --- what has been told -----------------------------------------------------------------------


@dataclass
class ProblemState:
    seen: int = 0  # consecutive checks that showed it
    alerted_at: float | None = None
    last_sent: float | None = None
    text: str = ""
    urgent: bool = False


@dataclass
class State:
    problems: dict[str, ProblemState] = field(default_factory=dict)
    restarts: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Message:
    kind: str  # problem, reminder, recovered, test
    name: str
    text: str
    urgent: bool = False


def plan(state: State, current: list[Problem], now: float, config: WatchdogConfig) -> list[Message]:
    """The messages due now. It changes only the counts; ``committed`` records a message once it
    has really been delivered, so a failed delivery is simply due again at the next check."""
    messages: list[Message] = []
    present = {p.name: p for p in current}
    for name, problem in present.items():
        known = state.problems.setdefault(name, ProblemState())
        known.seen += 1
        known.text, known.urgent = problem.text, problem.urgent
        if known.alerted_at is None:
            if known.seen >= config.confirm_checks:
                messages.append(Message("problem", name, problem.text, problem.urgent))
        elif now - (known.last_sent or known.alerted_at) >= config.reminder_hours * 3600:
            messages.append(Message("reminder", name, problem.text, problem.urgent))
    for name, known in list(state.problems.items()):
        if name in present:
            continue
        if known.alerted_at is None:
            del state.problems[name]  # a blip that never reached the owner is forgotten
        else:
            known.seen = 0
            messages.append(Message("recovered", name, f"recovered: {known.text}"))
    return messages


def committed(state: State, message: Message, now: float) -> None:
    known = state.problems[message.name]
    if message.kind == "recovered":
        del state.problems[message.name]
        return
    known.alerted_at = known.alerted_at or now
    known.last_sent = now


def load_state(path: Path) -> State:
    """The saved state, or a fresh one when there is none or it cannot be read (never an error:
    a damaged file must not stop the alerts)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        problems = {k: ProblemState(**v) for k, v in raw["problems"].items()}
        return State(problems, [int(n) for n in raw["restarts"]])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return State()


def save_state(path: Path, state: State) -> None:
    text = json.dumps(
        {"problems": {k: asdict(v) for k, v in state.problems.items()}, "restarts": state.restarts}
    )
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(text)
    os.replace(temp, path)  # whole or not at all; mkstemp made the file private (0600)


# --- delivery ---------------------------------------------------------------------------------

TITLES = {
    "problem": "SPARQ ingest: problem",
    "reminder": "SPARQ ingest: still a problem",
    "recovered": "SPARQ ingest: recovered",
    "test": "SPARQ ingest: test message",
}


class Notifier:
    """Sends a message to an ntfy topic. The topic is part of the URL, so no error from here ever
    carries the underlying exception text: it would contain the topic."""

    def __init__(self, server: str, topic: str, client: httpx.AsyncClient) -> None:
        for noisy in ("httpx", "httpcore"):  # their request lines carry the URL, so the topic
            logging.getLogger(noisy).setLevel(logging.CRITICAL)
        self._url = f"{server}/{topic}"
        self._client = client

    async def send(self, message: Message) -> None:
        tags = {"problem": "rotating_light", "reminder": "warning", "recovered": "white_check_mark"}
        headers = {
            "Title": TITLES[message.kind],
            "Priority": "high" if message.urgent else "default",
            "Tags": tags.get(message.kind, "bell"),
        }
        try:
            response = await self._client.post(
                self._url, content=message.text.encode("utf-8"), headers=headers
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise WatchdogError(f"the alert server answered {error.response.status_code}") from None
        except httpx.HTTPError as error:
            raise WatchdogError(
                f"the alert server could not be reached ({type(error).__name__})"
            ) from None


# --- looking ----------------------------------------------------------------------------------

Run = Callable[[list[str]], Awaitable[str]]
Connect = Callable[[], Awaitable[asyncpg.Connection]]


async def run_command(argv: list[str]) -> str:
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out, _ = await asyncio.wait_for(process.communicate(), PROBE_TIMEOUT)
    return out.decode("utf-8", "replace")


def connector(config: WatchdogConfig) -> Connect:
    async def connect() -> asyncpg.Connection:
        return await asyncpg.connect(
            host=config.socket_directory,
            port=config.port,
            user=config.role,
            database=config.database,
            timeout=PROBE_TIMEOUT,
        )

    return connect


def _properties(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


async def observe(
    config: WatchdogConfig,
    connect: Connect,
    run: Run = run_command,
    usage: Callable[[str], Any] = shutil.disk_usage,
) -> Observation:
    shown = _properties(
        await run(["systemctl", "show", INGEST_UNIT, "-p", "ActiveState", "-p", "NRestarts"])
    )
    state_text = shown.get("ActiveState", "unknown")
    restarts = int(shown["NRestarts"]) if shown.get("NRestarts", "").isdigit() else None
    try:
        total = usage(config.data_directory)
        disk: float | None = 100.0 * total.used / total.total
    except OSError:
        disk = None
    age: float | None = None
    reason: str | None = None
    failed = stuck = 0
    db_ok = True
    try:
        conn = await asyncio.wait_for(connect(), PROBE_TIMEOUT)
        try:
            beat = await conn.fetchval(
                "SELECT extract(epoch FROM now()) - value::numeric / 1000 "
                "FROM ingest_state WHERE key = $1",
                HEARTBEAT,
            )
            age = None if beat is None else float(beat)
            reason = await conn.fetchval(
                "SELECT value FROM ingest_state WHERE key = $1", STOPPED_REASON
            )
            failed, stuck = await conn.fetchrow(
                "SELECT count(*) FILTER (WHERE kind = $2 AND status = $3 "
                "AND started_at > now() - interval '6 hours'), "
                "count(*) FILTER (WHERE status = ANY($4::text[]) AND kind <> $5 "
                "AND started_at < now() - $1 * interval '1 minute') FROM gap_log",
                config.stuck_gap_minutes,
                gaps.TRADES,
                gaps.FAILED,
                list(gaps.UNFINISHED),
                gaps.LIFECYCLE,
            )
        finally:
            await conn.close()
    except (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError):
        db_ok = False
    return Observation(
        active=state_text == "active",
        state_text=state_text,
        restarts=restarts,
        db_ok=db_ok,
        heartbeat_age=age,
        stopped_reason=reason,
        failed_gaps=int(failed),
        stuck_gaps=int(stuck),
        disk_percent=disk,
    )


# --- one check --------------------------------------------------------------------------------


async def check(
    config: WatchdogConfig,
    state_path: Path,
    notifier: Notifier,
    *,
    connect: Connect | None = None,
    run: Run = run_command,
    usage: Callable[[str], Any] = shutil.disk_usage,
    clock: Callable[[], float] = time.time,
) -> int:
    """Look once, send what is due, save what was told. Returns 1 if a delivery failed."""
    state = load_state(state_path)
    obs = await observe(config, connect or connector(config), run, usage)
    now = clock()
    problems = evaluate(obs, config, state.restarts[-config.restart_window :])
    if obs.restarts is not None:
        state.restarts = [*state.restarts, obs.restarts][-config.restart_window :]
    failed = 0
    for message in plan(state, problems, now, config):
        try:
            await notifier.send(message)
        except WatchdogError as error:
            failed += 1
            log.warning(
                "could not deliver the %s message for %s: %s", message.kind, message.name, error
            )
        else:
            committed(state, message, now)
            log.info("sent the %s message for %s", message.kind, message.name)
    save_state(state_path, state)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = list(sys.argv[1:] if argv is None else argv)
    test = "--test" in args
    paths = [a for a in args if not a.startswith("--")]
    try:
        config = parse_config(
            Path(paths[0] if paths else ALERTS_CONFIG).read_text(encoding="utf-8")
        )
        credentials, state_dir = (
            os.environ.get("CREDENTIALS_DIRECTORY"),
            os.environ.get("STATE_DIRECTORY"),
        )
        if not credentials or not state_dir:
            raise WatchdogError("run the watchdog from its systemd unit")
        topic = (Path(credentials) / ALERT_CREDENTIAL).read_text(encoding="utf-8").strip()
        if not topic:
            raise WatchdogError("the alert topic credential is empty")
    except (OSError, WatchdogError) as error:
        # an OSError names a path under the credentials directory, never the topic itself
        log.error(
            "%s",
            error
            if isinstance(error, WatchdogError)
            else "a settings or credential file is unreadable",
        )
        return EXIT_REFUSED

    async def run() -> int:
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
            notifier = Notifier(config.server, topic, client)
            if test:
                try:
                    await notifier.send(Message("test", "test", "the watchdog can reach you"))
                except WatchdogError as error:
                    log.error("%s", error)
                    return 1
                log.info("test message sent")
                return 0
            return await check(config, Path(state_dir) / STATE_NAME, notifier)

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
