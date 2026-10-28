"""S3.1's combination: what a 99-cent bid would make a day, per category (PLAN §6.3, M4).

The live frames give the fills a day a bid of each size would have had, first in, first out; the
history gives the payout rate ``p`` (true regimes over all regimes) and the hours from a regime's
start to settlement. A contract filled makes 1 cent with probability ``p`` and loses 99 cents
otherwise, less the maker fee where the series charges one. The capital tied is the fills' cost
for the median hours to settlement. The bar is $25 a day within one category (M4).
"""

from typing import Any

from sparq_core.fixedpoint import E6

from sparq_analytics.survey.dump import CENTS
from sparq_analytics.survey.queue import BID_SIZES_E2

BAR_E6 = 25 * E6
MS_PER_DAY = 86_400_000
POSITIONS = ("onset", "hour_before")


def combine(history: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Per category and bid size: the dollars a day at each position, the capital tied, and
    whether the bar is cleared. A category without a history regime has no payout rate and no
    dollars."""
    out: dict[str, Any] = {}
    for category, frames in live["by_category"].items():
        past = history["by_category"].get(category)
        regimes = (past or {}).get("true_regimes", 0) + (past or {}).get("false_regimes", 0)
        row: dict[str, Any] = {
            "payout": None if not regimes else past["true_regimes"] / regimes,
            "regimes_in_history": regimes,
        }
        for size_e2 in BID_SIZES_E2:
            for position in POSITIONS:
                name = f"{position}_{size_e2}"
                fills = frames[f"fills_a_day_e2_{name}"]
                fee = frames[f"fee_a_day_e6_{name}"]
                if fills is None or not regimes or past["settle_ms_median"] is None:
                    row[f"dollars_a_day_e6_{name}"] = None
                    row[f"capital_e6_{name}"] = None
                    row[f"clears_{name}"] = False
                    continue
                # 1 cent won on p of the contracts, 99 cents lost on the rest, exactly
                gain = fills * (past["true_regimes"] * CENTS - past["false_regimes"] * (E6 - CENTS))
                dollars = gain // (regimes * 100) - fee
                capital = fills * (E6 - CENTS) // 100 * past["settle_ms_median"] // MS_PER_DAY
                row[f"dollars_a_day_e6_{name}"] = dollars
                row[f"capital_e6_{name}"] = capital
                row[f"clears_{name}"] = dollars >= BAR_E6
        out[category] = row
    return {
        "bar_e6": BAR_E6,
        "cleared": sorted(
            category
            for category, row in out.items()
            if any(row[f"clears_{p}_{s}"] for p in POSITIONS for s in BID_SIZES_E2)
        ),
        "by_category": out,
    }
