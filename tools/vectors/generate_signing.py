"""Generate golden vectors for sparq_core.auth.

* **Signature.** Ed25519 is deterministic, so the reference (``kalshi_core.auth.KalshiSigner``) is
  an exact oracle for the signature of ``timestamp + METHOD + path``, query string excluded.
* **What may be signed.** sparq_core is deliberately stricter than the reference: its data signer
  signs only a GET to a read route (or the WebSocket handshake) and refuses everything else, which
  the reference would sign. The expectation for each case is therefore the specification below,
  and ``reference`` records what the reference signed.

    python tools/vectors/generate_signing.py --reference %USERPROFILE%/dev/KalshiTerm2

The key is a test key made from a fixed seed. It protects nothing.
"""

import argparse
import sys
import types
from pathlib import Path
from typing import Any

from common import default_output, load_reference_module, write_vectors
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SEED = bytes(range(32))
KEY_ID = "test-key-id"
TIMESTAMPS = [0, 1, 1_700_000_000_000, 1_792_785_174_669, 9_999_999_999_999]
API = "/trade-api/v2"

ALLOWED = [  # read routes, with and without a query string
    f"{API}/markets",
    f"{API}/markets?limit=1000&status=open",
    f"{API}/markets/KXHIGHNY-26OCT24-T70",
    f"{API}/markets/trades?min_ts=1&max_ts=2",
    f"{API}/events/KXHIGHNY-26OCT24",
    f"{API}/series",
    f"{API}/series/KXHIGHNY/fee_changes",
    f"{API}/exchange/status",
    f"{API}/exchange/schedule",
    f"{API}/historical/cutoff",
    f"{API}/historical/trades?limit=1000",
    f"{API}/historical/markets/KXHIGHNY-26OCT24-T70/candlesticks",
    "/trade-api/ws/v2",
]
REFUSED_PATHS = [  # a data key must never sign these, even for a GET
    f"{API}/portfolio/balance",
    f"{API}/portfolio/orders",
    f"{API}/portfolio/positions",
    f"{API}/portfolio/fills",
    f"{API}/account",
    f"{API}/account/limits",  # account data: refused since S2.h (IKARS's review)
    f"{API}/historical/fills",  # the account archives under /historical
    f"{API}/historical/orders",
    f"{API}/historical/positions",
    f"{API}/markets/../portfolio/orders",
    f"{API}/markets/./x",
    f"{API}/marketsX",
    f"{API}/exchange/status/",
    "/markets",
    "/trade-api/v1/markets",
    "/trade-api/ws/v2/extra",
    "",
    "markets",
]
REFUSED_METHODS = ["POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


def load_reference_auth(reference: Path) -> Any:
    """The reference's auth module. Its settings class (which needs pydantic-settings) is only a
    type annotation there, so a stand-in is enough and the signing code is untouched."""
    stub = types.ModuleType("kalshi_core.config")
    stub.KalshiSettings = object  # type: ignore[attr-defined]
    sys.modules.setdefault("kalshi_core.config", stub)
    return load_reference_module(reference, "auth")


def build(reference: Path) -> dict[str, Any]:
    auth = load_reference_auth(reference)
    signer = auth.KalshiSigner(KEY_ID, Ed25519PrivateKey.from_private_bytes(SEED))
    cases: list[dict[str, Any]] = []

    def add(method: str, path: str, allowed: bool) -> None:
        for ts in TIMESTAMPS:
            signature = signer.sign(ts, method, path)
            cases.append(
                {
                    "timestampMs": ts,
                    "method": method,
                    "path": path,
                    "reference": signature,
                    "expect": signature if allowed else "refuse",
                }
            )

    for path in ALLOWED:
        add("GET", path, True)
        add("get", path, True)  # the method's case does not matter
    for path in REFUSED_PATHS:
        add("GET", path, False)
    for method in REFUSED_METHODS:
        add(method, f"{API}/markets", False)
        add(method, f"{API}/portfolio/orders", False)
    return {"keyId": KEY_ID, "seedHex": SEED.hex(), "cases": cases}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True, help="the KalshiTerm2 clone")
    parser.add_argument("--output", type=Path, default=default_output(__file__, "signing.json"))
    args = parser.parse_args()
    body = build(args.reference)
    write_vectors(args.output, "tools/vectors/generate_signing.py", args.reference, body)
    refused = sum(c["expect"] == "refuse" for c in body["cases"])
    print(f"wrote {len(body['cases'])} cases ({refused} refused) to {args.output}")


if __name__ == "__main__":
    main()
