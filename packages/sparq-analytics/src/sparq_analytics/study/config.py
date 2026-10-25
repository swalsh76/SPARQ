"""The study's settings. They are part of the pre-registration: a change changes its digest."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

CENT_E6 = 10_000
DEFAULT_PRICE_EDGES_E6 = tuple(
    cents * CENT_E6 for cents in (0, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100)
)
# a trade's yes price is 1 cent to 99 cents, so the last band is closed at a dollar
DEFAULT_TLEFT_EDGES_S = (0, 600, 3_600, 21_600, 86_400, 259_200, 604_800, 2_592_000)
# D10, measured on the real 2026-10-21 day: for markets that ended that day close_time sat nearer
# the last trade than expected_expiration_time in all 11 categories (sports 7 minutes against 117)
DEFAULT_REFERENCE = ("close_time", "expected_expiration_time")


@dataclass(frozen=True, slots=True)
class StudyConfig:
    """Price bands (yes price, millionths of a dollar, upper edge exclusive), time-to-expiry bands
    (seconds), the split (D12), the time reference per category (D10) and the verdict rules."""

    price_edges_e6: tuple[int, ...] = DEFAULT_PRICE_EDGES_E6
    tleft_edges_s: tuple[int, ...] = DEFAULT_TLEFT_EDGES_S
    fractions: tuple[float, float, float] = (0.70, 0.15, 0.15)
    reference_by_category: tuple[tuple[str, tuple[str, ...]], ...] = ()
    resamples: int = 10_000
    seed: int = 20_261_023
    min_events: int = 300  # independent events a segment needs for a GO
    max_segments: int = 12  # D11
    family_alpha: float = 0.025  # one-sided; a segment is tested at family_alpha / segments
    verdict_maker_share_e2: int = 50  # D13: the conservative maker share, in hundredths
    window_start: str | None = None  # UTC date: only events that opened on or after it (D17)
    window_end: str | None = None  # UTC date: only events that closed before it
    sample_share: float = 1.0  # D16: the share of events whose trades are loaded
    sample_seed: int = 1
    category_shares: tuple[tuple[str, float], ...] = ()  # D16 amended: a share per category
    fit_end: str | None = None  # UTC date: events closing before it are the fit part (D12)
    validate_end: str | None = None  # UTC date: ... before it, the validate part; the rest, test

    def __post_init__(self) -> None:
        if not 0.0 < self.sample_share <= 1.0:
            raise ValueError("the sample share is above zero and at most one")
        names = [name for name, _ in self.category_shares]
        if len(set(names)) != len(names):
            raise ValueError("a category is given two shares")
        if names != sorted(names):
            raise ValueError("category shares must be sorted by category, so the digest is stable")
        if any(not 0.0 < share <= 1.0 for _, share in self.category_shares):
            raise ValueError("a category share is above zero and at most one")
        start, end = self.window()
        if start is not None and end is not None and start >= end:
            raise ValueError("the window must start before it ends")
        if (self.fit_end is None) != (self.validate_end is None):
            raise ValueError("give both split dates or neither")
        dates = self.split_dates()
        if dates is not None:
            fit_end, validate_end = dates
            if fit_end >= validate_end:
                raise ValueError("the fit part must end before the validate part does")
            if start is not None and fit_end <= start:
                raise ValueError("the fit part must end after the window starts")
            if end is not None and validate_end >= end:
                raise ValueError("the validate part must end before the window does")
        if abs(sum(self.fractions) - 1.0) > 1e-9 or min(self.fractions) <= 0:
            raise ValueError("the split fractions must be positive and sum to one")
        for edges in (self.price_edges_e6, self.tleft_edges_s):
            if len(edges) < 2 or list(edges) != sorted(set(edges)):
                raise ValueError("band edges must be strictly increasing, at least two")
        if self.price_edges_e6[0] != 0 or self.price_edges_e6[-1] != 1_000_000:
            raise ValueError("price bands must cover 0 to one dollar")
        if self.tleft_edges_s[0] != 0:
            raise ValueError("time bands must start at zero")
        if not 0 <= self.verdict_maker_share_e2 <= 100:
            raise ValueError("the maker share is within 0 to 100 hundredths")

    def window(self) -> tuple[datetime | None, datetime | None]:
        """The window as UTC midnights (an unset end is None)."""

        def parse(text: str | None) -> datetime | None:
            return None if text is None else datetime.fromisoformat(text).replace(tzinfo=UTC)

        return parse(self.window_start), parse(self.window_end)

    def split_dates(self) -> tuple[datetime, datetime] | None:
        """The fixed fit and validate boundaries as UTC midnights, or None to use quantiles."""
        if self.fit_end is None or self.validate_end is None:
            return None
        fit = datetime.fromisoformat(self.fit_end).replace(tzinfo=UTC)
        validate = datetime.fromisoformat(self.validate_end).replace(tzinfo=UTC)
        return fit, validate

    def share_of(self, category: str) -> float:
        """The share of events sampled in this category: its own, or else the base share."""
        return dict(self.category_shares).get(category, self.sample_share)

    @property
    def sampled(self) -> bool:
        """Does the run load only some of the events?"""
        return self.sample_share < 1.0 or any(share < 1.0 for _, share in self.category_shares)

    @property
    def stratified(self) -> bool:
        """Do categories have shares of their own? Then pooled figures need weights (``w``)."""
        return bool(self.category_shares)

    def reference_for(self, category: str) -> tuple[str, ...]:
        """The market time fields tried in order for a category's time to expiry."""
        return dict(self.reference_by_category).get(category, DEFAULT_REFERENCE)

    def digest(self) -> str:
        """A stable hash of every setting (the verdict-relevant ones), for the pre-registration."""
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()
