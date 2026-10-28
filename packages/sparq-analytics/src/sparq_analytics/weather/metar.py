"""The parts of a station report (METAR or SPECI) that bound a day's high and say it rained.

* **Temperature:** the remark ``TsTTTsTTT`` gives it in tenths of a degree Celsius; without it, the
  body's ``TT/DD`` gives whole degrees (``M`` for minus), so the true value can be up to half a
  degree lower than written, and the parser takes that lower edge.
* **Six-hour maximum:** the remark ``1sTTT`` (tenths of a degree Celsius), sent with the reports
  near 00, 06, 12 and 18 UTC, is the highest temperature of the six hours before the report.
* **Precipitation:** the remark ``Prrrr`` is the rain since the last routine report in hundredths
  of an inch; ``P0000`` is a trace, which the markets count as no rain.

A climate value is a whole degree Fahrenheit, so every temperature here becomes the lowest whole
°F it can be (W4: rounded down): that keeps a bound from ever claiming more than the report shows.
Groups the parser cannot read are ignored, never guessed.
"""

import re
from dataclasses import dataclass

BODY_TEMP = re.compile(r"^(M?)(\d{2})/(M?\d{2})?$")
T_GROUP = re.compile(r"^T([01])(\d{3})(?:[01]\d{3})?$")
MAX6_GROUP = re.compile(r"^1([01])(\d{3})$")
PRECIP_GROUP = re.compile(r"^P(\d{4})$")


@dataclass(frozen=True)
class Report:
    temp_c10: int | None  # the temperature's lower edge in tenths of a degree Celsius
    exact: bool  # from the tenths remark (True) or the body's whole degrees (False)
    max6_c10: int | None  # the six-hour maximum in tenths of a degree Celsius
    precip_e2: int | None  # hundredths of an inch since the last routine report (a trace is 0)


def lowest_f(c10: int) -> int:
    """The lowest whole °F a temperature of at least ``c10`` tenths of a degree Celsius can be:
    F = C * 9 / 5 + 32, in integers, rounded down."""
    return (9 * c10 + 1600) // 50


def _signed(sign: str, digits: str) -> int:
    return -int(digits) if sign == "1" else int(digits)


def parse(text: str) -> Report:
    tokens = text.split()
    split = tokens.index("RMK") if "RMK" in tokens else len(tokens)
    body, remarks = tokens[:split], tokens[split + 1 :]
    temp_c10: int | None = None
    exact = False
    max6_c10: int | None = None
    precip_e2: int | None = None
    for token in remarks:
        if found := T_GROUP.match(token):
            temp_c10, exact = _signed(*found.groups()), True
        elif found := MAX6_GROUP.match(token):
            max6_c10 = _signed(*found.groups())
        elif found := PRECIP_GROUP.match(token):
            precip_e2 = int(found.group(1))
    if temp_c10 is None:
        for token in body:
            if found := BODY_TEMP.match(token):
                whole = int(found.group(2))
                whole = -whole if found.group(1) else whole
                temp_c10 = 10 * whole - 5  # written whole degrees: at least half a degree lower
                break
    return Report(temp_c10, exact, max6_c10, precip_e2)
