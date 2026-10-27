"""Work against the database, tried again while the database is away.

The writer and the pump both keep going through a lost connection or a restarting server: the work
is repeated on a fresh connection after a pause, and nothing is lost or written twice (the batch is
kept; a trade that did land is skipped by its unique key). Only a problem with the link or the
server's state is retried; a problem with the data itself is the caller's to handle.
"""

import logging
from collections.abc import Awaitable, Callable

import asyncpg

log = logging.getLogger("sparq_server")

# a problem with the link or the server's state: wait and try again on a fresh connection
RETRYABLE = (
    OSError,
    TimeoutError,
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    asyncpg.TooManyConnectionsError,
    asyncpg.CannotConnectNowError,
    asyncpg.AdminShutdownError,
    asyncpg.CrashShutdownError,
    asyncpg.DeadlockDetectedError,
    asyncpg.SerializationError,
)


async def retrying[T](
    work: Callable[[], Awaitable[T]],
    *,
    reset: Callable[[], Awaitable[None]],
    delay: Callable[[int], float],
    sleep: Callable[[float], Awaitable[None]],
    what: str,
) -> T:
    """``work()``, repeated until it succeeds. After a failure ``reset()`` drops the connection
    (and may count the retry), then the caller's ``delay(attempt)`` is slept, attempt 1 first."""
    attempt = 0
    while True:
        try:
            return await work()
        except RETRYABLE as exc:
            attempt += 1
            await reset()
            wait = delay(attempt)
            log.warning("%s failed (%s); retrying in %.1f s", what, type(exc).__name__, wait)
            await sleep(wait)
