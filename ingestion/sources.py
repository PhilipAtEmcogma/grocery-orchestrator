"""
Price sources.

A source answers one question: what offers does this retailer currently list?
It returns FACTS ONLY -- key, name, size, retailer, price, capture date --
because ACQUISITION-RISK.md 8 condition 7 says so, and because a field that
does not exist cannot be published by mistake. No images, no marketing copy,
no descriptions, no reviews, no personal information.

LIVE ACQUISITION IS NOT IMPLEMENTED AND MUST NOT BE ADDED HERE CASUALLY.
`FixtureSource` reads recorded responses. A live source is Task 11.4, gated on
all thirteen conditions in ACQUISITION-RISK.md 8 -- condition 1 alone (a human
reading the three unretrieved sources) is not met. `resolve_source` refuses to
return anything but a fixture source unless LIVE_ACQUISITION is explicitly set,
and nothing in this repo sets it. That check is a tripwire, not a feature
flag: it exists so that adding a live adapter requires deleting a line that
says why it is there.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "products.json"

# The retailers in scope. Pak'nSave and New World are both Foodstuffs, whose
# search endpoints are robots.txt-disallowed -- the sanctioned traversal is the
# published product sitemaps (8 condition 3).
KNOWN_RETAILERS = ("paknsave", "woolworths", "new_world")


@dataclass(frozen=True, slots=True)
class RawOffer:
    """
    One product at one store, as a source reports it.

    `captured_at` is not optional and has no default. A price the shopper
    cannot date is a price they cannot evaluate (8 condition 9), and a default
    would let an undated offer through by omission.
    """

    product_key: str
    store: str
    store_location: str
    display_name: str
    canonical_name: str
    category: str
    price_nzd: Decimal
    unit: str
    pack_grams: int
    on_special: bool
    captured_at: str
    lat: float
    lon: float


class PriceSource(Protocol):
    """Fetches current offers for one retailer."""

    @property
    def retailer(self) -> str: ...

    def fetch(self) -> list[RawOffer]: ...


class FixtureSource:
    """
    Recorded responses, filtered to one retailer.

    This is the only source that exists. It reads the same fixture the seed
    loader and the offline tests use, so ingestion is exercised end to end
    without a single request leaving the account.
    """

    def __init__(self, retailer: str, path: Path | None = None) -> None:
        if retailer not in KNOWN_RETAILERS:
            raise ValueError(f"unknown retailer {retailer!r}")
        self._retailer = retailer
        self._path = path or FIXTURES

    @property
    def retailer(self) -> str:
        return self._retailer

    def fetch(self) -> list[RawOffer]:
        records = json.loads(self._path.read_text(encoding="utf-8"))
        return [
            RawOffer(
                product_key=r["product_key"],
                store=r["store"],
                store_location=r["store_location"],
                display_name=r["display_name"],
                canonical_name=r["canonical_name"],
                category=r["category"],
                price_nzd=Decimal(str(r["price_nzd"])),
                unit=r["unit"],
                pack_grams=int(r["pack_grams"]),
                on_special=bool(r["on_special"]),
                # The fixture's valid_date is the recorded capture date. A live
                # source stamps this at fetch time; it is never inferred later,
                # because a date applied downstream is a date we made up.
                captured_at=r["valid_date"],
                lat=float(r["lat"]),
                lon=float(r["lon"]),
            )
            for r in records
            if r["store"] == self._retailer
        ]


def resolve_source(retailer: str) -> PriceSource:
    """
    The only way ingestion obtains a source.

    Refuses live acquisition rather than falling back to it quietly: a
    misconfiguration that silently starts requesting retailer sites is exactly
    the 4.2 exposure this project decided against.
    """
    if os.environ.get("LIVE_ACQUISITION") == "1":
        raise NotImplementedError(
            "Live acquisition is gated on ACQUISITION-RISK.md 8 and has no "
            "implementation. Task 11.4 is not started; condition 1 (a human "
            "reading the three unretrieved sources) is not met."
        )
    return FixtureSource(retailer)
