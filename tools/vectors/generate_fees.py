"""Generate golden vectors for sparq_core.fees.

* **Taker fee.** The reference (``kalshi_core.fees``) is the oracle. An independent exact-fraction
  calculation checks it; any disagreement stops the generator.
* **Maker fee.** The reference has none, so the oracle is that exact-fraction calculation of the
  documented rule: the taker coefficient times the maker share, rounded up to a millionth.
* **Schedules.** The reference's ``taker_multiplier`` is the oracle for which (fee type, multiplier)
  pairs are modelled; the maker share per fee type is the specification table below.

    python tools/vectors/generate_fees.py --reference %USERPROFILE%/dev/KalshiTerm2

Expectations: an integer, ``refuse`` (outside the domain), or ``null`` (not modelled). sparq_core is
deliberately stricter than the reference in one way: counts, multipliers and the price must fit a
signed 64-bit integer (database ``bigint``), so such inputs are ``refuse`` even where the
reference computes an answer (``reference`` records what it returned).
"""

import argparse
import math
import random
from fractions import Fraction
from pathlib import Path
from typing import Any

from common import INT64_MAX, default_output, load_reference_module, outcome, write_vectors

E6 = 10**6
# specification: fee type -> maker coefficient as a share of the taker one, in hundredths
MAKER_SHARE = {
    "quadratic": 0,
    "quadratic_with_maker_fees": 25,
    "quadratic_with_combo_maker_fees": 50,
}

MULTIPLIERS = [0, 1, 7, 1_000, 500_000, 1_000_000, 1_234_567, 2_000_000, 10_000_000]
COUNTS = [0, 1, 2, 3, 7, 99, 100, 101, 777, 2_550, 10_000, 1_000_000, 10**9]
PRICES = [0, 1, 2, 10_000, 55_000, 123_456, 333_333, 500_000, 666_667, 999_998, 999_999, 1_000_000]
SHARES = [0, 25, 50, 100]

REFUSED = [  # outside the domain: a negative anything, a price above one dollar
    (1_000_000, 100, -1),
    (1_000_000, 100, 1_000_001),
    (1_000_000, -1, 5),
    (-1, 1, 5),
    (-1, -1, -1),
    (1_000_000, 100, -1_000_000),
    (1_000_000, 100, 2_000_000),
]
EXTREMES = [  # beyond a signed 64-bit integer (or at its edge)
    (10**12, 10**12, 500_000),
    (10**9, 10**9, 500_000),
    (10**13, 10**9, 500_000),
    (INT64_MAX, INT64_MAX, 500_000),
    (INT64_MAX, 1, 500_000),
    (1, INT64_MAX, 500_000),
    (INT64_MAX + 1, 1, 500_000),
    (1, INT64_MAX + 1, 500_000),
    (10**18, 10**18, 250_000),
]

FEE_TYPES = [
    "quadratic",
    "quadratic_with_maker_fees",
    "quadratic_with_combo_maker_fees",
    "flat",
    "margin_market_maker_program_fees",
    "mystery",
    "",
    "Quadratic",
    None,
]
MULTIPLIER_VALUES = [None, -1, 0, 1, 500_000, 1_000_000, 123_456_789, INT64_MAX, INT64_MAX + 1]

ROUND_VALUES = [
    0,
    1,
    99,
    100,
    101,
    3_639,
    17_500,
    20_000,
    999_999,
    1_000_000,
    10**9,
    INT64_MAX,
    -1,
    -99,
    -100,
    -101,
    -17_500,
]
ROUND_UNITS = [1, 2, 3, 100, 10_000, 1_000_000, 0, -1, -10_000]


def exact_fee(multiplier: int, share: int, count: int, price: int) -> int:
    """ceil(M x 7/100 x share/100 x count/100 x price x (1e6 - price) / 1e12), exactly.
    A share of 100 is the taker fee."""
    return math.ceil(
        Fraction(multiplier * 7 * share * count * price * (E6 - price), E6 * 100 * 100 * 100 * E6)
    )


def fits(*values: int) -> bool:
    return all(0 <= v <= INT64_MAX for v in values)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True, type=Path, help="local clone of KalshiTerm2")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    fees = load_reference_module(args.reference, "fees")

    triples = [(m, c, p) for m in MULTIPLIERS for c in COUNTS for p in PRICES]
    rng = random.Random(8032)  # noqa: S311  # fixed seed: the output must be reproducible
    triples += [
        (rng.randrange(0, 5_000_000), rng.randrange(0, 2_000_000), rng.randrange(0, 1_000_001))
        for _ in range(400)
    ]
    triples += REFUSED + EXTREMES

    taker: list[dict[str, Any]] = []
    for m, c, p in triples:
        reference = outcome(fees.taker_fee_e6, m, c, p)
        in_domain = 0 <= p <= E6 and fits(m, c)
        if in_domain and reference != exact_fee(m, 100, c, p):
            raise SystemExit(
                f"the reference disagrees with exact arithmetic at {(m, c, p)}: {reference}"
            )
        expect = exact_fee(m, 100, c, p) if in_domain else "refuse"
        taker.append(
            {"multiplier": m, "count": c, "price": p, "reference": reference, "expect": expect}
        )

    maker: list[dict[str, Any]] = []
    for share in SHARES:
        for m, c, p in triples:
            in_domain = 0 <= p <= E6 and fits(m, c)
            maker.append(
                {
                    "multiplier": m,
                    "share": share,
                    "count": c,
                    "price": p,
                    "expect": exact_fee(m, share, c, p) if in_domain else "refuse",
                }
            )
    maker += [
        {"multiplier": 1_000_000, "share": s, "count": 100, "price": 500_000, "expect": "refuse"}
        for s in (-1, 101)
    ]

    round_up = [
        {"value": v, "unit": u, "expect": outcome(fees.round_up, v, u)}
        for v in ROUND_VALUES
        for u in ROUND_UNITS
    ]

    schedules: list[dict[str, Any]] = []
    for fee_type in FEE_TYPES:
        for value in MULTIPLIER_VALUES:
            reference = fees.taker_multiplier(fee_type, value)
            expect = None if reference is not None and not fits(reference) else reference
            schedules.append(
                {
                    "feeType": fee_type,
                    "multiplier": value,
                    "reference": reference,
                    "expect": None
                    if expect is None
                    else {
                        "multiplier": expect,
                        "makerShare": MAKER_SHARE[fee_type],
                    },
                }
            )

    out = args.out or default_output(__file__, "fees.json")
    write_vectors(
        out,
        "tools/vectors/generate_fees.py",
        args.reference,
        {
            "makerShare": MAKER_SHARE,
            "takerFee": taker,
            "makerFee": maker,
            "roundUp": round_up,
            "schedules": schedules,
        },
    )
    refused = sum(1 for t in taker if t["expect"] == "refuse")
    print(
        f"wrote {out}: {len(taker)} taker cases ({refused} refused), {len(maker)} maker cases, "
        f"{len(round_up)} round-up cases, {len(schedules)} schedule cases"
    )


if __name__ == "__main__":
    main()
