import json
import os
from pathlib import Path

import httpx
import pytest
from sparq_analytics.model import client as model
from sparq_analytics.model.client import ModelClient, ModelError, ModelSettings, load_settings

KEY = "sk-test-0123456789"
SETTINGS = ModelSettings(url="http://model-node.test/v1", key=KEY, model="small-model")


def write_settings(path: Path, body: str | None = None) -> Path:
    path.write_text(
        body or f'url = "http://model-node.test:8080/v1/"\nkey = "{KEY}"\nmodel = "small-model"\n',
        encoding="utf-8",
    )
    if os.name == "posix":
        path.chmod(0o600)
    return path


def answering(calls: list[httpx.Request], **choice: object) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        message = {"role": "assistant", "content": "pong", "reasoning_content": "thinking"}
        body = {
            "choices": [{"message": message, "finish_reason": "stop"} | choice],
            "usage": {"completion_tokens": 7},
        }
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handle)


def test_settings_come_from_a_file_and_the_key_stays_out_of_sight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_settings(tmp_path / "model.toml")
    settings = load_settings(path)
    assert (settings.url, settings.key, settings.model) == (
        "http://model-node.test:8080/v1", KEY, "small-model",
    )  # fmt: skip
    assert KEY not in repr(settings) and KEY not in str(settings)
    monkeypatch.setenv("SPARQ_MODEL_CONFIG", str(path))
    assert load_settings().model == "small-model"
    with pytest.raises(ModelError, match="no model settings"):
        load_settings(tmp_path / "missing.toml")
    with pytest.raises(ModelError, match="lacks key, model"):
        load_settings(write_settings(tmp_path / "bare.toml", 'url = "http://x/v1"\n'))
    with pytest.raises(ModelError, match="not valid TOML") as bad:
        load_settings(write_settings(tmp_path / "bad.toml", f'key = "{KEY}\n'))
    assert KEY not in str(bad.value)


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX")
def test_settings_readable_by_others_are_refused(tmp_path: Path) -> None:
    path = write_settings(tmp_path / "model.toml")
    path.chmod(0o644)
    assert model.loose(path)
    with pytest.raises(ModelError, match="can be read by others"):
        load_settings(path)
    path.chmod(0o600)
    assert not model.loose(path)


def test_chat_sends_the_request_and_returns_the_reply() -> None:
    calls: list[httpx.Request] = []
    client = ModelClient(SETTINGS, transport=answering(calls))
    reply = client.chat("say pong", max_tokens=123)
    [request] = calls
    assert str(request.url) == "http://model-node.test/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == {
        "model": "small-model",
        "messages": [{"role": "user", "content": "say pong"}],
        "max_tokens": 123,
        "temperature": 0,
    }
    assert (reply.content, reply.reasoning, reply.completion_tokens) == ("pong", "thinking", 7)
    assert not reply.truncated and not reply.cached and reply.seconds >= 0
    client.close()


def test_a_reply_is_cached_by_its_request(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    cache = tmp_path / "cache"
    client = ModelClient(SETTINGS, cache=cache, transport=answering(calls))
    first = client.chat("say pong", 100)
    again = client.chat("say pong", 100)
    assert len(calls) == 1 and again.cached and not first.cached
    assert (again.content, again.completion_tokens) == (first.content, first.completion_tokens)
    client.chat("say pong", 200)  # another budget is another request
    client.chat("say ping", 100)
    assert len(calls) == 3
    # a new client on the same folder (a restart) answers from the files
    ModelClient(SETTINGS, cache=cache, transport=answering(calls)).chat("say pong", 100)
    assert len(calls) == 3
    files = list(cache.rglob("*.json"))
    assert len(files) == 3 and not list(cache.rglob("*.part"))
    assert all(KEY not in f.read_text(encoding="utf-8") for f in files)  # no key in the cache
    ModelClient(SETTINGS, transport=answering(calls)).chat("say pong", 100)  # no cache: asks again
    assert len(calls) == 4


def test_a_budget_spent_on_thinking_returns_an_empty_truncated_reply() -> None:
    def spent(request: httpx.Request) -> httpx.Response:
        message = {"role": "assistant", "content": None, "reasoning_content": "hmm" * 50}
        return httpx.Response(
            200, json={"choices": [{"message": message, "finish_reason": "length"}]}
        )

    reply = ModelClient(SETTINGS, transport=httpx.MockTransport(spent)).chat("q", 50)
    assert (reply.content, reply.truncated, reply.completion_tokens) == ("", True, 0)


def test_failures_say_what_happened_without_the_key() -> None:
    def fail(status: int) -> ModelClient:
        transport = httpx.MockTransport(lambda request: httpx.Response(status, text=KEY))
        return ModelClient(SETTINGS, transport=transport)

    with pytest.raises(ModelError, match="answered 500") as refused:
        fail(500).chat("q")
    assert KEY not in str(refused.value)
    for body in ("not json", '{"choices": []}', '{"choices": [{"message": 5}]}'):
        transport = httpx.MockTransport(lambda request, b=body: httpx.Response(200, text=b))
        with pytest.raises(ModelError, match="not a chat completion"):
            ModelClient(SETTINGS, transport=transport).chat("q")


def test_a_refused_connection_is_retried_and_a_slow_reply_is_not() -> None:
    attempts: list[int] = []
    waits: list[float] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    client = ModelClient(SETTINGS, transport=httpx.MockTransport(flaky), sleep=waits.append)
    assert client.chat("q").content == "ok" and len(attempts) == 3
    assert waits == [model.RETRY_WAIT_S] * 2

    attempts.clear()

    def down(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ModelError, match="refused 3 connections"):
        ModelClient(SETTINGS, transport=httpx.MockTransport(down), sleep=waits.append).chat("q")
    assert len(attempts) == 3

    attempts.clear()

    def slow(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ModelError, match="did not answer \\(ReadTimeout\\)"):
        ModelClient(SETTINGS, transport=httpx.MockTransport(slow)).chat("q")
    assert len(attempts) == 1  # a slow reply is not sent again
