"""Generate golden vectors for sparq_core.fixedpoint from the Python reference.

The reference's own ``fixedpoint.py`` is the oracle for the cases both implementations agree on.
An independent exact-fraction calculation (the standard library's ``Fraction``) checks the
reference itself, and wherever they disagree the vector records both and the exact answer wins:

    python tools/vectors/generate_fixedpoint.py --reference %USERPROFILE%/dev/KalshiTerm2

Case fields
  text, scale   the input text and the scale (``e6`` or ``e2``)
  reference     what the reference returned: an integer, or ``refuse``
  expect        what sparq_core must return; differs from ``reference`` only where sparq_core is
                deliberately stricter or exact, and then ``reason`` says why:
                  grammar    the text is not plain ASCII decimal (sparq_core refuses it)
                  inexact    the reference's 28-digit decimal context rounded an inexact value
                             into looking exact
                  bigint     the exact value does not fit a signed 64-bit integer
"""

import argparse
import re
from fractions import Fraction
from pathlib import Path
from typing import Any

from common import INT64_MAX, default_output, load_reference_module, outcome, write_vectors

STRICT = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)", re.ASCII)
SCALES = {"e6": 10**6, "e2": 10**2}

TEXT_INPUTS = [
    # plain values, signs, zeros
    "0",
    "1",
    "-1",
    "+1",
    "0.0",
    "00.00",
    "-0",
    "-0.000000",
    "+0.5",
    "0.4100",
    "0.41",
    ".5",
    "1.",
    "-.5",
    "000123.4500",
    "7",
    "104.24",
    "0.03",
    "118.7499",
    "-3.5",
    "100",
    "99.99",
    "0.99",
    "0.01",
    # the finest ticks
    "0.0001",
    "0.000001",
    "0.01",
    "0.000010",
    "0.010000",
    # finer than either scale
    "0.1234567",
    "0.0000001",
    "0.001",
    "0.123",
    "1.0000001",
    "-0.0000001",
    # integer boundaries at e6 and e2
    "9223372036854.775807",
    "9223372036854.775808",
    "-9223372036854.775807",
    "-9223372036854.775808",
    "92233720368547758.07",
    "92233720368547758.08",
    "-92233720368547758.07",
    "-92233720368547758.08",
    "9000000000000",
    "10000000000000",
    "9223372036854775807",
    "99999999999999999999",
    # many digits, all trailing zeros
    "1.000000000000000000000000000",
    "0.100000000000000000000000000",
    "123456789012345678.00",
    "0.000000000000000000000000000000000001",
    # more than 28 significant digits, exact and inexact (the reference's decimal context rounds)
    "1.0000000000000000000000000001",
    "0.4100000000000000000000000000001",
    "1234567890123456789012345.67",
    "0.00000000000000000000000000000001",
    # not plain ASCII decimal text
    "1e6",
    "1E-6",
    "2.5e-1",
    "1e400",
    " 1",
    "1 ",
    "\t1",
    "1_000",
    "1,000",
    "0x10",
    "--1",
    "+-1",
    "1.2.3",
    ".",
    "+",
    "-",
    "",
    "NaN",
    "Infinity",
    "-Infinity",
    "nan",
    "inf",
    "١٢",
    "１",
    "1 ",
    "1/2",
    "0.5%",
    "$1",
]


def exact(text: str, factor: int) -> int | str:
    """The scaled integer from exact rational arithmetic, or ``refuse``."""
    if not STRICT.fullmatch(text):
        return "refuse"
    scaled = Fraction(text) * factor
    if scaled.denominator != 1:
        return "refuse"
    return int(scaled)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True, type=Path, help="local clone of KalshiTerm2")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    fixedpoint = load_reference_module(args.reference, "fixedpoint")
    reference_fn = {"e6": fixedpoint.to_e6, "e2": fixedpoint.to_e2}

    cases: list[dict[str, Any]] = []
    for scale, factor in SCALES.items():
        for text in TEXT_INPUTS:
            reference = outcome(reference_fn[scale], text)
            truth = exact(text, factor)
            expect, reason = truth, None
            if not STRICT.fullmatch(text):
                reason = "grammar"
            elif isinstance(truth, int) and abs(truth) > INT64_MAX:
                expect, reason = "refuse", "bigint"
            if reference != expect and reason is None:
                reason = "inexact"
            if reference == expect:
                reason = None
            case: dict[str, Any] = {
                "text": text,
                "scale": scale,
                "reference": reference,
                "expect": expect,
            }
            if reason:
                case["reason"] = reason
            cases.append(case)

    round_trips = []
    for value in [
        0,
        1,
        -1,
        99,
        100,
        101,
        410_000,
        -3_500_000,
        118_749_900,
        10_424,
        INT64_MAX,
        -INT64_MAX,
    ]:
        round_trips.append(
            {
                "value": value,
                "e6": str(fixedpoint.from_e6(value)) if abs(value) < 10**15 else None,
                "e2": str(fixedpoint.from_e2(value)) if abs(value) < 10**15 else None,
            }
        )

    out = args.out or default_output(__file__, "fixedpoint.json")
    write_vectors(
        out,
        "tools/vectors/generate_fixedpoint.py",
        args.reference,
        {
            "cases": cases,
            "roundTrips": round_trips,
        },
    )
    stricter = {
        k: sum(1 for c in cases if c.get("reason") == k) for k in ("grammar", "inexact", "bigint")
    }
    print(
        f"wrote {out}: {len(cases)} cases, deliberately different from the reference: {stricter}, "
        f"{len(round_trips)} round trips"
    )


if __name__ == "__main__":
    main()
