"""Request signing for Kalshi's data key. Ed25519 only: RSA-PSS is deliberately unsupported.

Kalshi signs a request as ``timestamp + METHOD + path`` (the query string is not part of it).
The data key lives on the server host, so what it can sign is limited here and not left to the
caller: a data signer signs only a GET to a read route in the allow-list below, or the WebSocket
handshake. It can never sign a POST, a DELETE, an order route or any account read (balances,
positions, orders, fills, the account's limits, and their ``/historical`` archives), so even a bug
elsewhere cannot make the data key place an order or read an account value. The trading client, when
it exists (S1.3), is a separate class with its own key.

Every key file must be readable by its owner alone on a POSIX machine, not only the trading key.
No error names the key file's path or shows the key id (CLAUDE.md): only what is wrong with them.
"""

import base64
import os
import re
import stat
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

HEADER_KEY = "KALSHI-ACCESS-KEY"
HEADER_TIMESTAMP = "KALSHI-ACCESS-TIMESTAMP"
HEADER_SIGNATURE = "KALSHI-ACCESS-SIGNATURE"

API_PREFIX = "/trade-api/v2"
WS_PATH = "/trade-api/ws/v2"

# A path segment may not start with a dot, so ``.`` and ``..`` can never appear in a signed path.
_SEGMENT = r"[A-Za-z0-9_\-][A-Za-z0-9_.\-]*"
# The one table of public market-data reads: the data signer and the public client's guard both
# use it. Under /historical only the cutoff, markets and trades are market data: /historical/fills,
# /orders and /positions are account archives (found by IKARS's review of this code, 2026-10-26).
_PUBLIC_ROUTES = re.compile(
    rf"{re.escape(API_PREFIX)}/(?:markets|events|series|exchange"
    rf"|historical/(?:cutoff|markets|trades))(?:/{_SEGMENT})*"
)
# A Kalshi API key id is a UUID; a pasted value often keeps its quotes (a 401 with no reason).
_KEY_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_QUOTES_OR_SPACE = re.compile(r"[\"'`\s]")
KEY_ID_VARIABLE = "SPARQ_KALSHI_KEY_ID"
KEY_PATH_VARIABLE = "SPARQ_KALSHI_PRIVATE_KEY_PATH"


class AuthError(Exception):
    """Credentials are missing or unusable."""


class SigningRefused(AuthError):
    """The data key was asked to sign something it must never sign."""


def public_route(path: str) -> bool:
    """Is ``path`` (a full URL path, query string removed) a public market-data read?"""
    return _PUBLIC_ROUTES.fullmatch(path) is not None


def read_route(path: str) -> bool:
    """Is ``path`` (query string removed) a route the data key may sign for a GET?"""
    return path == WS_PATH or public_route(path)


def key_id_problem(key_id: str) -> str | None:
    """What is wrong with a key id, in words that never show it; None for a Kalshi key id."""
    if _KEY_ID.fullmatch(key_id):
        return None
    if _QUOTES_OR_SPACE.search(key_id):
        return "it has quotes or spaces in it (was it pasted with its quotes?)"
    if len(key_id) != 36:
        return f"it is {len(key_id)} characters, not 36"
    return "it is 36 characters but not in the 8-4-4-4-12 hexadecimal form"


def parse_private_key(data: bytes, source: str) -> Ed25519PrivateKey:
    """An unencrypted PEM Ed25519 private key from bytes; ``source`` names it in an error.

    Where the bytes came from decides whether their file mode matters: a key file in a home
    directory must be private to its owner (``load_private_key``); a ``systemd`` credential sits in
    a directory only the service can enter, so the service reads it and calls this directly."""
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except (ValueError, TypeError) as exc:
        raise AuthError(f"{source} is not an unencrypted PEM private key") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise AuthError(
            f"unsupported key type {type(key).__name__}: only Ed25519 keys are supported "
            "(create an Ed25519 key; RSA is intentionally not implemented)"
        )
    return key


def load_private_key(path: Path) -> Ed25519PrivateKey:
    """Load an unencrypted PEM Ed25519 private key from a file only its owner can read."""
    target = path.expanduser()
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
        data = target.read_bytes()
    except OSError as exc:
        # strerror only: the exception's own text would carry the path
        raise AuthError(f"cannot read the private key file: {exc.strerror}") from None
    if os.name == "posix" and mode & 0o077:
        raise AuthError(
            f"the private key file is readable by other users (mode {mode:03o}); "
            f'run: chmod 600 "${KEY_PATH_VARIABLE}"'
        )
    return parse_private_key(data, "the private key file")


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


class DataSigner:
    """Builds the signed headers for read-only requests and the WebSocket handshake.

    ``offset_ms`` is how far the local clock is ahead of Kalshi's (negative: behind), from
    :mod:`sparq_core.clock`; it is subtracted so that the signed timestamp is Kalshi's time.
    """

    def __init__(
        self,
        key_id: str,
        private_key: Ed25519PrivateKey,
        *,
        now_ms: Callable[[], int] = _now_ms,
        offset_ms: int = 0,
    ) -> None:
        if not key_id.strip():
            raise AuthError("the key id is empty")
        if _QUOTES_OR_SPACE.search(key_id.strip()):
            raise AuthError(
                "the key id has quotes or spaces in it (was it pasted with its quotes?)"
            )
        self._key_id = key_id.strip()
        self._key = private_key
        self._now_ms = now_ms
        self.offset_ms = offset_ms

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "DataSigner":
        """From ``SPARQ_KALSHI_KEY_ID`` and ``SPARQ_KALSHI_PRIVATE_KEY_PATH`` (SPARQ's own names, so
        another project's ``KALSHI_*`` variables on the same machine never reach it)."""
        env = os.environ if environ is None else environ
        key_id, path = env.get(KEY_ID_VARIABLE, ""), env.get(KEY_PATH_VARIABLE, "")
        if not key_id or not path:
            raise AuthError(f"{KEY_ID_VARIABLE} and {KEY_PATH_VARIABLE} must both be set")
        if problem := key_id_problem(key_id.strip()):
            raise AuthError(f"{KEY_ID_VARIABLE} is not a Kalshi key id: {problem}")
        return cls(key_id, load_private_key(Path(path)))

    def headers(self, method: str, path: str, *, timestamp_ms: int | None = None) -> dict[str, str]:
        """Signed headers for a GET of ``path`` (a URL path with or without a query string)."""
        bare = path.split("?", 1)[0]
        if method.upper() != "GET":
            raise SigningRefused(f"the data key signs GET requests only, not {method.upper()}")
        if not read_route(bare):
            raise SigningRefused(f"the data key does not sign requests to {bare}")
        stamp = self._now_ms() - self.offset_ms if timestamp_ms is None else timestamp_ms
        signature = self._key.sign(f"{stamp}GET{bare}".encode())
        return {
            HEADER_KEY: self._key_id,
            HEADER_TIMESTAMP: str(stamp),
            HEADER_SIGNATURE: base64.b64encode(signature).decode(),
        }
