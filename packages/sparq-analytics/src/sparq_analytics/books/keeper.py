"""S6.2: Kalshi's order books rebuilt from the book messages (PLAN §6.6, B1).

Both sides of a Kalshi book are bids: the yes bids, and the no bids, whose best sets the yes ask at
a dollar less. A snapshot replaces a market's book; every later change on the subscription is
applied in ``seq`` order, none left out by its ``clock`` (Kalshi's rule; live on 2026-11-02 a book
rebuilt so equaled a fresh snapshot after about 13,000 changes). A change that would leave a size
below zero cannot be right: the keeper says so and forgets the market until its next snapshot. A
book whose best yes and no bids add to a dollar or more is counted as crossed, not refused, since
the changes of one match may arrive one at a time.

Prices are millionths of a dollar, sizes hundredths of a contract. The best price of each side is
kept as changes arrive, so finding it is a dictionary scan only when the best level empties.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from sparq_core.fixedpoint import E6

YES, NO = "yes", "no"


@dataclass(frozen=True, slots=True)
class Top:
    """The best yes bid and yes ask with their sizes; None where a side is empty."""

    bid_e6: int | None
    bid_e2: int | None
    ask_e6: int | None
    ask_e2: int | None

    @property
    def crossed(self) -> bool:
        return self.bid_e6 is not None and self.ask_e6 is not None and self.bid_e6 >= self.ask_e6


@dataclass(slots=True)
class Book:
    """One market's bids, price to size, on both sides, with each side's best price."""

    yes: dict[int, int] = field(default_factory=dict)
    no: dict[int, int] = field(default_factory=dict)
    best_yes: int | None = None
    best_no: int | None = None

    def set_level(self, side: str, price_e6: int, size_e2: int) -> None:
        """Set one level outright (a snapshot's)."""
        levels = self.yes if side == YES else self.no
        if size_e2:
            levels[price_e6] = size_e2
        else:
            levels.pop(price_e6, None)
        self._rebest(side, price_e6, size_e2 > 0)

    def change(self, side: str, price_e6: int, delta_e2: int) -> bool:
        """Apply one change; False when it would leave a size below zero (the book is then
        unchanged)."""
        levels = self.yes if side == YES else self.no
        size = levels.get(price_e6, 0) + delta_e2
        if size < 0:
            return False
        if size:
            levels[price_e6] = size
        else:
            levels.pop(price_e6, None)
        self._rebest(side, price_e6, size > 0)
        return True

    def _rebest(self, side: str, price_e6: int, standing: bool) -> None:
        best = self.best_yes if side == YES else self.best_no
        if standing:
            if best is None or price_e6 > best:
                best = price_e6
        elif best == price_e6:
            levels = self.yes if side == YES else self.no
            best = max(levels) if levels else None
        if side == YES:
            self.best_yes = best
        else:
            self.best_no = best

    def top(self) -> Top:
        bid, no = self.best_yes, self.best_no
        return Top(
            bid,
            None if bid is None else self.yes[bid],
            None if no is None else E6 - no,
            None if no is None else self.no[no],
        )


class Keeper:
    """Every market's book, from its snapshot on; a market without one is unknown."""

    def __init__(self) -> None:
        self.books: dict[str, Book] = {}

    def replace(
        self, ticker: str, yes: Iterable[tuple[int, int]], no: Iterable[tuple[int, int]]
    ) -> None:
        book = Book()
        for price, size in yes:
            book.set_level(YES, price, size)
        for price, size in no:
            book.set_level(NO, price, size)
        self.books[ticker] = book

    def apply(self, ticker: str, side: str, price_e6: int, delta_e2: int) -> bool | None:
        """Apply a change: True when applied, None when the market's book is unknown (no snapshot
        yet), False when impossible, in which case the market is forgotten until its snapshot."""
        book = self.books.get(ticker)
        if book is None:
            return None
        if book.change(side, price_e6, delta_e2):
            return True
        del self.books[ticker]
        return False

    def forget(self, ticker: str | None = None) -> None:
        """Forget one market's book, or every book (``None``)."""
        if ticker is None:
            self.books.clear()
        else:
            self.books.pop(ticker, None)

    def top(self, ticker: str) -> Top | None:
        book = self.books.get(ticker)
        return None if book is None else book.top()
