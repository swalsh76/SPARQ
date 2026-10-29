"""S5.1: the client of the model node's OpenAI-style endpoint (PLAN §6.5, N2 to N4).

The node's address, the model's name and the key are in a TOML file outside the repository
(``SPARQ_MODEL_CONFIG``, default ``~/.config/sparq/model.toml``, mode 600)::

    url = "http://model-node:8080/v1"
    key = "sk-..."
    model = "name-the-endpoint-serves"

The key goes nowhere but the request's header: it is not in the settings' ``repr``, any error
message, the cache or a log. A reply is cached by the hash of its request (model, prompt, token
budget, temperature), so a rerun costs nothing and gives the same figures. The endpoint serves a
**reasoning model**: its thinking is spent from the same token budget as its answer, so a budget
that is too small returns an empty ``content`` (``truncated``), which the caller treats as no
answer.
A refused connection is retried a few times; a slow or failed reply is not (the caller
records it and moves on).
"""

import hashlib
import json
import os
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx

DEFAULT_CONFIG = "~/.config/sparq/model.toml"
RETRIES = 3  # connection attempts
RETRY_WAIT_S = 2.0
TIMEOUT_S = 300.0
MAX_TOKENS = 2_000
SETTINGS_KEYS = ("url", "key", "model")


class ModelError(Exception):
    """The settings are missing or loose, or the model node did not answer."""


@dataclass(frozen=True)
class ModelSettings:
    url: str
    key: str = field(repr=False)
    model: str


@dataclass(frozen=True)
class Reply:
    """One answer: its text, the model's thinking, the tokens it spent, the seconds it took, whether
    the budget ran out before the answer, and whether it came from the cache."""

    content: str
    reasoning: str
    completion_tokens: int
    seconds: float
    truncated: bool
    cached: bool = False


def loose(path: Path) -> bool:
    """Whether others can read the file (POSIX only: the file's mode says nothing on Windows)."""
    return os.name == "posix" and bool(path.stat().st_mode & 0o077)


def load_settings(path: Path | None = None) -> ModelSettings:
    """The model node's settings, refused when missing, unreadable by design or incomplete."""
    where = Path(path or os.environ.get("SPARQ_MODEL_CONFIG", DEFAULT_CONFIG)).expanduser()
    if not where.is_file():
        raise ModelError(f"no model settings at {where}")
    if loose(where):
        raise ModelError(f"{where} can be read by others; run chmod 600 on it")
    try:
        data = tomllib.loads(where.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ModelError(f"{where} is not valid TOML ({error})") from None
    missing = [name for name in SETTINGS_KEYS if not data.get(name)]
    if missing:
        raise ModelError(f"{where} lacks {', '.join(missing)}")
    return ModelSettings(
        url=str(data["url"]).rstrip("/"), key=str(data["key"]), model=data["model"]
    )


class ModelClient:
    """Chat calls to the model node, cached by request."""

    def __init__(
        self,
        settings: ModelSettings,
        cache: Path | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.sleep = sleep
        self._http = httpx.Client(timeout=TIMEOUT_S, transport=transport)

    def chat(self, prompt: str, max_tokens: int = MAX_TOKENS) -> Reply:
        """The model's answer to ``prompt`` at temperature 0, from the cache when asked before."""
        request = {
            "model": self.settings.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        if (found := self._read(digest)) is not None:
            return replace(found, cached=True)
        reply = self._ask(request)
        self._write(digest, request, reply)
        return reply

    def close(self) -> None:
        self._http.close()

    def _path(self, digest: str) -> Path | None:
        return None if self.cache is None else self.cache / digest[:2] / f"{digest}.json"

    def _read(self, digest: str) -> Reply | None:
        path = self._path(digest)
        if path is None or not path.exists():
            return None
        saved = json.loads(path.read_text(encoding="utf-8"))["reply"]
        return Reply(**saved)

    def _write(self, digest: str, request: dict[str, Any], reply: Reply) -> None:
        path = self._path(digest)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        saved = {k: v for k, v in reply.__dict__.items() if k != "cached"}
        staging = path.with_suffix(".part")
        staging.write_text(json.dumps({"request": request, "reply": saved}), encoding="utf-8")
        staging.replace(path)

    def _send(self, request: dict[str, Any]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.settings.key}"}
        url = f"{self.settings.url}/chat/completions"
        for attempt in range(1, RETRIES + 1):
            try:
                return self._http.post(url, json=request, headers=headers)
            except httpx.ConnectError:
                if attempt == RETRIES:
                    raise ModelError(f"the model node refused {RETRIES} connections") from None
                self.sleep(RETRY_WAIT_S)
            except httpx.HTTPError as error:
                kind = type(error).__name__
                raise ModelError(f"the model node did not answer ({kind})") from None
        raise ModelError("no attempt was made")  # pragma: no cover - RETRIES is at least 1

    def _ask(self, request: dict[str, Any]) -> Reply:
        started = time.monotonic()
        response = self._send(request)
        if response.status_code != 200:
            raise ModelError(f"the model node answered {response.status_code}")
        try:
            body = response.json()
            choice = body["choices"][0]
            message = choice["message"]
            tokens = int(body.get("usage", {}).get("completion_tokens", 0))
            return Reply(
                content=message.get("content") or "",
                reasoning=message.get("reasoning_content") or "",
                completion_tokens=tokens,
                seconds=time.monotonic() - started,
                truncated=choice.get("finish_reason") == "length",
            )
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ModelError("the model node's reply was not a chat completion") from None
