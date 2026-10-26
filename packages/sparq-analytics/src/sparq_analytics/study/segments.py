"""Bands, segments, and the pre-registration that locks them before the test period is read.

A segment is a price band × time-to-expiry band × category (and optionally the taker's side). At
most ``max_segments`` may be named (D11). The list and every study setting are hashed; the hash is
written to a ledger file the first time the test period is opened, and a different hash at any
later opening is refused. So the segments cannot be changed after the test has been looked at.
"""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import polars as pl

from sparq_analytics.study.config import StudyConfig, sha256_of


class PreregistrationError(Exception):
    """The segments or settings differ from the ones locked when the test was first opened."""


def add_bands(frame: pl.LazyFrame, config: StudyConfig) -> pl.LazyFrame:
    """Add ``price_band`` and ``tleft_band``: band numbers from zero, the last time band open."""

    def index(column: str, edges: tuple[int, ...]) -> pl.Expr:
        inner = edges[1:-1] if column == "p" else edges[1:]
        return pl.sum_horizontal([(pl.col(column) >= e).cast(pl.Int8) for e in inner]).cast(pl.Int8)

    return frame.with_columns(
        index("p", config.price_edges_e6).alias("price_band"),
        index("tleft_s", config.tleft_edges_s).alias("tleft_band"),
    )


@dataclass(frozen=True, slots=True)
class Segment:
    """Yes prices in [price_lo_e6, price_hi_e6), seconds left in [tleft_lo_s, tleft_hi_s) (open
    when None), one category (or any), and optionally only trades whose taker bought ``side``.

    An exploratory segment is tested and reported like any other but cannot decide the verdict: it
    cannot make it GO and it cannot make it inconclusive. It is for a market set too small to reach
    ``min_events`` in the test part, which would otherwise block a clean NO-GO or be left out.
    """

    price_lo_e6: int = 0
    price_hi_e6: int = 1_000_001
    tleft_lo_s: int = 0
    tleft_hi_s: int | None = None
    category: str | None = None
    side: str | None = None
    exploratory: bool = False

    def __post_init__(self) -> None:
        if self.price_lo_e6 >= self.price_hi_e6:
            raise ValueError("an empty price range")
        if self.tleft_hi_s is not None and self.tleft_lo_s >= self.tleft_hi_s:
            raise ValueError("an empty time range")
        if self.side not in (None, "yes", "no"):
            raise ValueError("side is yes, no or None")

    def expr(self) -> pl.Expr:
        keep = (pl.col("p") >= self.price_lo_e6) & (pl.col("p") < self.price_hi_e6)
        keep &= pl.col("tleft_s") >= self.tleft_lo_s
        if self.tleft_hi_s is not None:
            keep &= pl.col("tleft_s") < self.tleft_hi_s
        if self.category is not None:
            keep &= pl.col("category") == self.category
        if self.side is not None:
            keep &= pl.col("taker_yes") == (self.side == "yes")
        return keep


@dataclass(frozen=True, slots=True)
class Prereg:
    """The named segments and the settings they were named under."""

    segments: tuple[Segment, ...]
    config: StudyConfig

    def __post_init__(self) -> None:
        if not self.segments:
            raise PreregistrationError("name at least one segment")
        if all(s.exploratory for s in self.segments):
            raise PreregistrationError("name at least one segment that is not exploratory")
        if len(self.segments) > self.config.max_segments:
            raise PreregistrationError(
                f"{len(self.segments)} segments named; at most {self.config.max_segments}"
            )
        if len(set(self.segments)) != len(self.segments):
            raise PreregistrationError("a segment is named twice")
        if self.config.stratified and any(s.category is None for s in self.segments):
            # a segment inside one category is one stratum, so its events are a simple random
            # sample of it and the clustered interval needs no weights
            raise PreregistrationError(
                "with a stratified sample every segment must name a category"
            )

    def digest(self) -> str:
        body = {
            "segments": [asdict(s) for s in self.segments],
            "config": self.config.digest(),
        }
        return sha256_of(body)

    def alpha(self) -> float:
        """The one-sided tail each segment is tested at (D11, Bonferroni over the deciding ones).

        An exploratory segment cannot decide the verdict, so it does not spend any of the family's
        error; it is reported at the same tail as the others.
        """
        return self.config.family_alpha / sum(1 for s in self.segments if not s.exploratory)

    @property
    def size(self) -> int:
        return len(self.segments)


class Registration(Protocol):
    """What the ledger needs from a pre-registration: its digest and how many entries it names."""

    def digest(self) -> str: ...

    @property
    def size(self) -> int: ...


class PreregLedger:
    """The file that remembers which pre-registration the test was first opened under."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def open_test(self, prereg: Registration) -> None:
        digest = prereg.digest()
        if self.path.exists():
            recorded = json.loads(self.path.read_text(encoding="utf-8"))["digest"]
            if recorded != digest:
                raise PreregistrationError(
                    "the test period was opened under a different pre-registration; "
                    "the segments and settings are locked"
                )
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "digest": digest,
            "segments": prereg.size,
            "opened_at": datetime.now(UTC).isoformat(),
        }
        self.path.write_text(json.dumps(record), encoding="utf-8")
