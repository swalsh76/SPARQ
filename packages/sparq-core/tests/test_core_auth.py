"""Request signing: identical to the reference where it signs, stricter about what it signs."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sparq_core.auth import (
    HEADER_KEY,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    AuthError,
    DataSigner,
    SigningRefused,
    key_id_problem,
    load_private_key,
    parse_private_key,
    public_route,
    read_route,
)

VECTORS: dict[str, Any] = json.loads(
    (Path(__file__).parent / "vectors" / "signing.json").read_text(encoding="utf-8")
)
SEED = bytes.fromhex(VECTORS["seedHex"])
KEY_ID = "a952bcbe-ec3b-4b5b-b8f9-11dae589608c"  # made up, in Kalshi's form
posix_only = pytest.mark.skipif(os.name != "posix", reason="file modes are a POSIX matter")


def signer(**kwargs: Any) -> DataSigner:
    return DataSigner(VECTORS["keyId"], Ed25519PrivateKey.from_private_bytes(SEED), **kwargs)


def pem(key: Any, encryption: Any = None) -> bytes:
    data: bytes = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        encryption or serialization.NoEncryption(),
    )
    return data


def write_key(tmp_path: Path, data: bytes, mode: int = 0o600) -> Path:
    path = tmp_path / "key.pem"
    path.write_bytes(data)
    path.chmod(mode)
    return path


# --- the golden vectors from the reference ---------------------------------------------------


@pytest.mark.parametrize(
    "case", VECTORS["cases"], ids=lambda c: f"{c['method']} {c['path']}@{c['timestampMs']}"
)
def test_signatures_match_the_reference_and_everything_else_is_refused(
    case: dict[str, Any],
) -> None:
    if case["expect"] == "refuse":
        with pytest.raises(SigningRefused):
            signer().headers(case["method"], case["path"], timestamp_ms=case["timestampMs"])
        return
    headers = signer().headers(case["method"], case["path"], timestamp_ms=case["timestampMs"])
    assert headers[HEADER_SIGNATURE] == case["expect"] == case["reference"]
    assert headers[HEADER_TIMESTAMP] == str(case["timestampMs"])
    assert headers[HEADER_KEY] == VECTORS["keyId"]


def test_the_vectors_cover_both_outcomes_in_quantity() -> None:
    outcomes = [c["expect"] == "refuse" for c in VECTORS["cases"]]
    assert sum(outcomes) > 50 and not all(outcomes) and len(outcomes) - sum(outcomes) > 50


# --- the allow-list ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/trade-api/v2/markets", "/trade-api/v2/historical/markets/ABC-1/candlesticks",
     "/trade-api/v2/historical/cutoff", "/trade-api/v2/historical/trades", "/trade-api/ws/v2"],
)  # fmt: skip
def test_read_routes_are_allowed(path: str) -> None:
    assert read_route(path)


@pytest.mark.parametrize(
    "path",
    ["/trade-api/v2/account/limits", "/trade-api/v2/historical/fills",
     "/trade-api/v2/historical/orders", "/trade-api/v2/historical/positions",
     "/trade-api/v2/historical/fills/x", "/trade-api/v2/historical", "/trade-api/v2/historical/x"],
)  # fmt: skip
def test_account_data_is_never_a_read_route(path: str) -> None:
    # the account archives under /historical and the account's limits are account data (S2.h)
    assert not read_route(path) and not public_route(path)
    with pytest.raises(SigningRefused):
        signer().headers("GET", path)


def test_the_websocket_handshake_is_signed_but_is_not_a_public_route() -> None:
    assert read_route("/trade-api/ws/v2") and not public_route("/trade-api/ws/v2")


@pytest.mark.parametrize(
    "path",
    ["/trade-api/v2/portfolio/orders", "/trade-api/v2/account", "/trade-api/v2/account/limits/x",
     "/trade-api/v2/markets/../portfolio", "/trade-api/v2/markets/.hidden",
     "/trade-api/v2/markets/", "/trade-api/v2/markets//x", "/trade-api/v2/markets/a b",
     "/trade-api/v2/markets/a%2Fb", ""],
)  # fmt: skip
def test_other_routes_and_tricks_are_refused(path: str) -> None:
    assert not read_route(path)


def test_the_query_string_is_not_part_of_the_signed_path_or_the_check() -> None:
    plain = signer().headers("GET", "/trade-api/v2/markets", timestamp_ms=5)
    queried = signer().headers("GET", "/trade-api/v2/markets?limit=5&status=open", timestamp_ms=5)
    assert plain[HEADER_SIGNATURE] == queried[HEADER_SIGNATURE]
    with pytest.raises(SigningRefused):
        signer().headers("GET", "/trade-api/v2/portfolio/balance?x=/trade-api/v2/markets")


def test_a_refusal_names_the_method_or_the_path_and_signs_nothing() -> None:
    with pytest.raises(SigningRefused, match="GET requests only, not POST"):
        signer().headers("POST", "/trade-api/v2/markets")
    with pytest.raises(SigningRefused, match="portfolio/orders"):
        signer().headers("GET", "/trade-api/v2/portfolio/orders")


# --- the clock ---------------------------------------------------------------------------------


def test_the_timestamp_is_kalshis_time_when_the_local_clock_is_off() -> None:
    ahead = signer(now_ms=lambda: 1_000_000, offset_ms=1500)  # local is 1.5 s ahead
    assert ahead.headers("GET", "/trade-api/v2/exchange/status")[HEADER_TIMESTAMP] == "998500"
    behind = signer(now_ms=lambda: 1_000_000, offset_ms=-2000)
    assert behind.headers("GET", "/trade-api/v2/exchange/status")[HEADER_TIMESTAMP] == "1002000"


def test_the_offset_can_be_updated_while_running() -> None:
    s = signer(now_ms=lambda: 10_000)
    s.offset_ms = 400
    assert s.headers("GET", "/trade-api/v2/exchange/status")[HEADER_TIMESTAMP] == "9600"


def test_the_default_clock_is_the_current_time_in_milliseconds() -> None:
    import time

    before = time.time_ns() // 1_000_000
    stamp = int(
        DataSigner("k", Ed25519PrivateKey.generate()).headers("GET", "/trade-api/ws/v2")[
            HEADER_TIMESTAMP
        ]
    )
    assert before <= stamp <= time.time_ns() // 1_000_000


def test_an_empty_key_id_is_refused() -> None:
    with pytest.raises(AuthError, match="key id is empty"):
        DataSigner("  ", Ed25519PrivateKey.generate())


def test_a_key_id_pasted_with_quotes_is_refused_without_showing_it() -> None:
    with pytest.raises(AuthError, match="quotes or spaces") as caught:
        DataSigner('"secret-id-value"', Ed25519PrivateKey.generate())
    assert "secret-id-value" not in str(caught.value)


@pytest.mark.parametrize(
    ("key_id", "reason"),
    [
        (KEY_ID, None),
        (KEY_ID.upper(), None),
        (f'"{KEY_ID}"', "quotes or spaces"),
        (f"{KEY_ID[:8]} {KEY_ID[9:]}", "quotes or spaces"),
        ("abc", "3 characters, not 36"),
        ("g" * 8 + KEY_ID[8:], "8-4-4-4-12"),
        (KEY_ID.replace("-", "x"), "8-4-4-4-12"),
    ],
)
def test_what_is_wrong_with_a_key_id_is_said_without_the_id(
    key_id: str, reason: str | None
) -> None:
    problem = key_id_problem(key_id)
    if reason is None:
        assert problem is None
    else:
        assert problem is not None and reason in problem and key_id not in problem


# --- key files ---------------------------------------------------------------------------------


def test_an_ed25519_pem_key_loads_and_signs_like_the_one_it_came_from(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.from_private_bytes(SEED)
    path = write_key(tmp_path, pem(key))
    loaded = DataSigner("k", load_private_key(path)).headers(
        "GET", "/trade-api/ws/v2", timestamp_ms=7
    )
    original = DataSigner("k", key).headers("GET", "/trade-api/ws/v2", timestamp_ms=7)
    assert loaded == original


@posix_only
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o666, 0o700 | 0o010])
def test_a_key_file_other_users_can_read_is_refused_with_the_fix(tmp_path: Path, mode: int) -> None:
    path = write_key(tmp_path, pem(Ed25519PrivateKey.generate()), mode)
    with pytest.raises(AuthError, match=r"readable by other users.*chmod 600"):
        load_private_key(path)


@posix_only
def test_an_owner_only_key_file_is_accepted(tmp_path: Path) -> None:
    load_private_key(write_key(tmp_path, pem(Ed25519PrivateKey.generate()), 0o400))


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda: pem(rsa.generate_private_key(65537, 2048)), "only Ed25519 keys are supported"),
        (lambda: pem(ec.generate_private_key(ec.SECP256R1())), "only Ed25519 keys are supported"),
        (lambda: b"not a key", "not an unencrypted PEM private key"),
        (
            lambda: pem(Ed25519PrivateKey.generate(), serialization.BestAvailableEncryption(b"pw")),
            "not an unencrypted PEM private key",
        ),
    ],
)
def test_a_key_that_is_not_an_unencrypted_ed25519_pem_is_refused(
    tmp_path: Path, make: Any, message: str
) -> None:
    with pytest.raises(AuthError, match=message):
        load_private_key(write_key(tmp_path, make()))


def test_a_missing_key_file_is_reported_without_its_path(tmp_path: Path) -> None:
    folder = tmp_path / "secret-folder-name"
    with pytest.raises(AuthError, match="cannot read the private key file") as caught:
        load_private_key(folder / "absent.pem")
    assert "secret-folder-name" not in str(caught.value) and caught.value.__cause__ is None


@posix_only
def test_an_open_key_file_is_refused_without_its_path(tmp_path: Path) -> None:
    folder = tmp_path / "secret-folder-name"
    folder.mkdir()
    path = write_key(folder, pem(Ed25519PrivateKey.generate()), 0o644)
    with pytest.raises(AuthError, match=r"\$SPARQ_KALSHI_PRIVATE_KEY_PATH") as caught:
        load_private_key(path)
    assert "secret-folder-name" not in str(caught.value)


def test_a_bad_key_file_is_named_without_its_path(tmp_path: Path) -> None:
    folder = tmp_path / "secret-folder-name"
    folder.mkdir()
    with pytest.raises(AuthError, match="the private key file is not") as caught:
        load_private_key(write_key(folder, b"not a key"))
    assert "secret-folder-name" not in str(caught.value)


def test_the_environment_supplies_the_key_id_and_the_path(tmp_path: Path) -> None:
    path = write_key(tmp_path, pem(Ed25519PrivateKey.from_private_bytes(SEED)))
    s = DataSigner.from_environment(
        {"SPARQ_KALSHI_KEY_ID": f" {KEY_ID} ", "SPARQ_KALSHI_PRIVATE_KEY_PATH": str(path)}
    )
    assert s.headers("GET", "/trade-api/ws/v2", timestamp_ms=1)[HEADER_KEY] == KEY_ID


def test_the_environment_refuses_a_key_id_that_is_not_kalshis(tmp_path: Path) -> None:
    path = write_key(tmp_path, pem(Ed25519PrivateKey.generate()))
    with pytest.raises(AuthError, match="SPARQ_KALSHI_KEY_ID is not a Kalshi key id: it is 3") as e:
        DataSigner.from_environment(
            {"SPARQ_KALSHI_KEY_ID": "abc", "SPARQ_KALSHI_PRIVATE_KEY_PATH": str(path)}
        )
    assert "abc" not in str(e.value).split(":", 1)[1]


def test_kaps_variables_never_reach_sparq(tmp_path: Path) -> None:
    path = write_key(tmp_path, pem(Ed25519PrivateKey.generate()))
    with pytest.raises(AuthError, match="must both be set"):
        DataSigner.from_environment({"KALSHI_KEY_ID": KEY_ID, "KALSHI_PRIVATE_KEY_PATH": str(path)})


@pytest.mark.parametrize(
    "env", [{}, {"SPARQ_KALSHI_KEY_ID": "a"}, {"SPARQ_KALSHI_PRIVATE_KEY_PATH": "/x"}]
)
def test_the_environment_needs_both_variables(env: dict[str, str]) -> None:
    with pytest.raises(AuthError, match="must both be set"):
        DataSigner.from_environment(env)


def test_the_key_never_appears_in_a_repr_or_an_error(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.from_private_bytes(SEED)
    s = DataSigner("k", key)
    assert SEED.hex() not in repr(s) and "PRIVATE" not in repr(s)
    with pytest.raises(SigningRefused) as caught:
        s.headers("POST", "/trade-api/v2/markets")
    assert SEED.hex() not in str(caught.value)


def test_a_key_parsed_from_bytes_is_judged_on_its_content_alone() -> None:
    key = Ed25519PrivateKey.generate()
    parsed = parse_private_key(pem(key), "the credential")
    assert DataSigner("k", parsed).headers("GET", "/trade-api/ws/v2")[HEADER_KEY] == "k"
    with pytest.raises(AuthError, match="^the credential is not an unencrypted PEM"):
        parse_private_key(b"not a key", "the credential")
    with pytest.raises(AuthError, match="only Ed25519"):
        parse_private_key(pem(rsa.generate_private_key(65537, 2048)), "the credential")
