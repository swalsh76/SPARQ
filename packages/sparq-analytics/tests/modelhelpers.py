"""A hand-built world for S5.1's tests (the model node's reading check), with the answers worked out
by hand. The model node is a fake transport that answers each rules text with a canned reply.

**History** (``kalshi/markets/historical``), weather markets by day:

* S2's window (2026-08-01 to 2026-10-10), highs (``KXHIGHNY``) in three events:
  ``H1`` between 85 and 86 (names CLINYC); ``H2`` greater than 86 (CLINYC); ``H3`` less than 79
  (CLINYC): the event of 08-21. ``H4`` greater than 80 (names the station KNYC); ``H5`` greater
  than 70 (names no site); ``H6`` has no strike type (CLINYC): the event of 08-22. ``H7`` and ``H8``
  greater than 81 and 82 (CLINYC): the event of 08-23.
* Rain (``KXRAIN``, strike greater than 0): ``R1`` at CLISFO (08-21), ``R2`` at CLIMIA (08-22).
* ``X1``, on 2026-10-11, is in S2's test part: never read. ``O1`` and ``O2`` (2026-07-15, greater
  than 75 and 76, CLINYC) are the earlier sample.

**The model's replies** (``reply`` below): ``H1`` right; ``H2`` right inside a code fence; ``H3``
says upper 78 for a cap of 79 (bounds disagree); ``H4`` answers ``knyc`` in lower case with the
number as text (agrees); ``H5`` names CLIMIA where the text names none and its series' code is
CLINYC (a wrong inference, bounds right); ``H6`` right (no strike type, so no bounds to compare);
``H7`` answers "I cannot tell" (unparsed); ``H8`` returns an empty content with ``finish_reason``
``length`` (the reasoning spent the budget); ``R1`` right; ``R2`` names CLINYC for a CLIMIA text
(site disagrees, bounds right); ``O1`` right; ``O2`` says lower 77 for a floor of 76 (bounds
disagree).

**Expected figures (S2's window):** highs: 8 markets, 6 parsed (0.75), 1 truncated; the site over
the 7 that name one (H1, H2, H3, H4, H6, H7, H8): 5 agree; the bounds over the 7 with a strike type
(all but H6): 4 agree (H1, H2, H4, H5); 1 market names no site and the model answered the wrong
code (H5); 3 disagreements to show (H3, H7, H8). Rain: 2 markets, both parsed, the site 1 of 2,
the bounds 2 of 2, 1 disagreement (R2). All 10 parse at 0.8, below the 0.995 bar, so nothing
passes. The earlier sample: 2 markets, the site 2 of 2, the bounds 1 of 2.
"""

from pathlib import Path
from typing import Any

import httpx
import polars as pl
from sparq_analytics.weather.stations import SITES

NYC = "CLINYC"
KNYC = SITES[NYC].station


def _text(place: str, site: str, day: str, phrase: str) -> str:
    return (
        f"If the maximum temperature recorded at {place} ({site}) for {day}, is {phrase} "
        "according to The Weather Company, then the market resolves to Yes."
    )


def _rain(site: str, place: str, day: str) -> str:
    return (
        f"If the total precipitation at {site} in {place} in {day} is strictly greater than 0 "
        "inches, then the market resolves to Yes."
    )


def _json(site: str | None, direction: str, lower: Any, upper: Any) -> str:
    import json

    return json.dumps({"site": site, "direction": direction, "lower": lower, "upper": upper})


def _m(
    ticker: str,
    event: str,
    strike: tuple[str | None, str | None, str | None],
    rules: str,
    reply: str,
    finish: str = "stop",
    series: str = "KXHIGHNY",
) -> dict[str, Any]:
    kind, floor, cap = strike
    return {
        "ticker": ticker,
        "series_ticker": series,
        "event_ticker": event,
        "strike_type": kind,
        "floor_strike": floor,
        "cap_strike": cap,
        "rules_primary": rules,
        "reply": reply,
        "finish": finish,
    }


AUG21, AUG22, AUG23 = "KXHIGHNY-26AUG21", "KXHIGHNY-26AUG22", "KXHIGHNY-26AUG23"
NYC_PLACE = "New York City"
FENCE = "```json\n"
BETWEEN, GREATER, LESS = ("between", "85", "86"), ("greater", "86", None), ("less", None, "79")


def _high(day: str, phrase: str, site: str = NYC) -> str:
    return _text(NYC_PLACE, site, f"{day}, 2026", phrase)


def _greater(floor: str) -> tuple[str, str, None]:
    return ("greater", floor, None)


MARKETS = [
    _m("H1", AUG21, BETWEEN, _high("Aug 21", "between 85-86°"), _json(NYC, "between", 85, 86)),
    _m(
        "H2", AUG21, GREATER, _high("Aug 21", "greater than 86°"),
        FENCE + _json(NYC, "greater", 86, None) + "\n```",
    ),
    _m("H3", AUG21, LESS, _high("Aug 21", "less than 79°"), _json(NYC, "less", None, 78)),
    _m(
        "H4", AUG22, _greater("80"), _high("Aug 22", "greater than 80°", KNYC),
        "Here it is: " + _json(KNYC.lower(), "greater", "80", None),
    ),
    _m(
        "H5", AUG22, _greater("70"),
        "If the maximum temperature in New York City for Aug 22, 2026, is above 70° then Yes.",
        _json("CLIMIA", "greater", 70, None),
    ),
    _m(
        "H6", AUG22, (None, None, None), _high("Aug 22", "above the line"),
        _json(NYC, "greater", 70, None),
    ),
    _m("H7", AUG23, _greater("81"), _high("Aug 23", "greater than 81°"), "I cannot tell."),
    _m("H8", AUG23, _greater("82"), _high("Aug 23", "greater than 82°"), "", finish="length"),
    _m(
        "R1", "KXRAIN-26AUG21", _greater("0"), _rain("CLISFO", "San Francisco", "Aug 21, 2026"),
        _json("CLISFO", "greater", 0, None), series="KXRAIN",
    ),
    _m(
        "R2", "KXRAIN-26AUG22", _greater("0"), _rain("CLIMIA", "Miami", "Aug 22, 2026"),
        _json(NYC, "greater", 0, None), series="KXRAIN",
    ),
    _m(
        "X1", "KXHIGHNY-26OCT11", _greater("70"), _high("Oct 11", "greater than 70°"),
        _json(NYC, "greater", 70, None),
    ),
    _m(
        "O1", "KXHIGHNY-26JUL15", _greater("75"), _high("Jul 15", "greater than 75°"),
        _json(NYC, "greater", 75, None),
    ),
    _m(
        "O2", "KXHIGHNY-26JUL15", _greater("76"), _high("Jul 15", "greater than 76°"),
        _json(NYC, "greater", 77, None),
    ),
]  # fmt: skip


def write_reading_kalshi(root: Path) -> Path:
    """The history world under ``root / "kalshi"``."""
    kalshi = root / "kalshi"
    historical = kalshi / "markets" / "historical"
    historical.mkdir(parents=True)
    columns = [
        "ticker",
        "series_ticker",
        "event_ticker",
        "strike_type",
        "floor_strike",
        "cap_strike",
        "rules_primary",
    ]
    frame = pl.DataFrame(
        {c: [m[c] for m in MARKETS] for c in columns}, schema={c: pl.String for c in columns}
    )
    frame.write_parquet(historical / "part-0.parquet")
    return kalshi


def model_transport(calls: list[httpx.Request], status: int = 200) -> httpx.MockTransport:
    """A fake model node: answers each market's rules text with its canned reply (``status`` for
    every call when it is not 200); every request is appended to ``calls``."""

    def answer(request: httpx.Request) -> httpx.Response:
        import json

        calls.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": "no"})
        prompt = json.loads(request.content)["messages"][0]["content"]
        for market in MARKETS:
            if market["rules_primary"] in prompt:
                message = {
                    "role": "assistant",
                    "content": market["reply"],
                    "reasoning_content": "hmm",
                }
                body = {
                    "choices": [{"message": message, "finish_reason": market["finish"]}],
                    "usage": {"completion_tokens": 40},
                }
                return httpx.Response(200, json=body)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "?"}, "finish_reason": "stop"}]}
        )

    return httpx.MockTransport(answer)
