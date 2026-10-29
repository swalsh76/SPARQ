"""S3.1's combination: what a 99-cent bid would make a day, per category (PLAN §6.3, M4).

Two bidders, each resting a bid of ``S`` contracts in every episode the live frames show:

* **the reactive one** joins when the best bid first reaches 99. Its risk is every market whose
  side printed 99 and then lost (the any-time reversals), because it could not tell them apart;
* **the knowing one** joins an hour before, which only knowledge of the outcome allows. Its risk
  is the false regimes: a side that stayed at 99 to the end and still lost.

Each gains 1 cent on the contracts the queue lets it fill, first in, first out, in the markets
that pay. Each loses 99 cents, in the markets that reverse, on the contracts the reversal's dump
would have filled behind the same queue (the history's reversal dumps, paired with the live
queues): a panic usually runs through the whole queue, a deep queue sometimes shields the bid.
Both rates come from the history, by side (a market whose both sides printed 99 counts once on
each, as the bidder would have bid on both). The fee is
the maker fee where the series charges one. The capital tied is the fills' cost for the median
hours to settlement, and the collateral the resting bids hold at any moment, for the median length
of a closed episode. The bar is $25 a day within one category (M4).
"""

from typing import Any

from sparq_core.fixedpoint import E6

from sparq_analytics.survey.dump import BAR_E6, CENTS, MS_PER_DAY, MS_PER_HOUR
from sparq_analytics.survey.queue import BID_SIZES_E2

POSITIONS = ("onset", "hour_before")


def _ratio(wins: int, total: int) -> float | None:
    return None if not total else wins / total


def combine(history: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Per category and bid size: the gain, the loss, the dollars a day at each position, the
    capital tied and the collateral, and whether the bar is cleared. A category without a market
    in the history has no rate and no dollars."""
    out: dict[str, Any] = {}
    for category, frames in live["by_category"].items():
        past = history["by_category"].get(category) or {}
        winning = past.get("winning_99_markets", 0)
        true = past.get("true_regimes", 0)
        bases = {
            "onset": (winning, winning + past.get("reversal_markets", 0)),
            "hour_before": (true, true + past.get("false_regimes", 0)),
        }
        episodes = frames["episodes_a_day"]
        hours = frames["closed_hours_median"]
        hours_ms = None if hours is None else round(hours * MS_PER_HOUR)
        row: dict[str, Any] = {
            "reactive_payout": _ratio(*bases["onset"]),
            "knowing_payout": _ratio(*bases["hour_before"]),
            "episodes_a_day": episodes,
        }
        for size_e2 in BID_SIZES_E2:
            for position in POSITIONS:
                wins, total = bases[position]
                name = f"{position}_{size_e2}"
                fills = frames[f"fills_a_day_e2_{name}"]
                reversal_fills = frames.get(f"rev_fills_a_day_e2_{name}")
                fee = frames[f"fee_a_day_e6_{name}"]
                if fills is None or reversal_fills is None or not total:
                    for key in ("gain", "loss", "dollars_a_day", "capital", "collateral"):
                        row[f"{key}_e6_{name}"] = None
                    row[f"clears_{name}"] = False
                    continue
                # a cent on the fills in the markets that paid; 99 cents on the reversal's fills
                # in the markets that reversed; both exactly, in integers
                gain = fills * CENTS * wins // (total * 100)
                loss = reversal_fills * (total - wins) * (E6 - CENTS) // (total * 100)
                dollars = gain - loss - fee
                settle = past.get("settle_ms_median")
                row[f"gain_e6_{name}"] = gain
                row[f"loss_e6_{name}"] = loss
                row[f"dollars_a_day_e6_{name}"] = dollars
                row[f"capital_e6_{name}"] = (
                    None if settle is None else fills * (E6 - CENTS) // 100 * settle // MS_PER_DAY
                )
                row[f"collateral_e6_{name}"] = (
                    None
                    if hours_ms is None
                    else episodes * size_e2 * (E6 - CENTS) // 100 * hours_ms // MS_PER_DAY
                )
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
