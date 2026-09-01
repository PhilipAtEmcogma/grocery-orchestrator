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

import functools
import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "products.json"

# The data team's collected catalogue. See LineageBSource.
LINEAGE_B_DIR = Path(__file__).resolve().parent.parent / "datasets" / "data" / "dynamodb_products"

# Source priority as first-class, reviewable data. See config/data-sources.json
# for the full reasoning; the short version is that "the data team's catalogue
# is the primary ingestion input" is a project decision, not an env-var default.
DATA_SOURCES_CONFIG = Path(__file__).resolve().parent.parent / "config" / "data-sources.json"

# The source names the config is allowed to name, mapped to their constructors.
# A name the config uses that is not here is a config error, not a silent
# fallback -- the same fail-closed stance parse_store and classify take.
_SOURCE_BY_NAME = {
    "fixtures": "FixtureSource",
    "lineage_b": "LineageBSource",
}

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


class LineageBSource:
    """
    The data team's collected catalogue, filtered to one retailer.

    A RECORDED SOURCE, not a live one. It reads JSON the data sub-team
    collected and committed under `datasets/` -- no request leaves the account,
    and ACQUISITION-RISK.md 8 is untouched by it. `datasets/DATA_SCHEMA.md`
    describes the collection; `ingestion/lineage_b.py` does the transform and
    carries the reasoning for every derived field.

    THE CAPTURE DATE IS THE DATA TEAM'S CLAIM, NOT OURS. Lineage B records
    carry no date at all, and `RawOffer` refuses an undated offer, so one must
    be supplied. `DATA_SCHEMA.md` documents the dataset as an August 28 2026
    snapshot -- that is their stated collection date, recorded as such and
    passed in explicitly rather than defaulted, so nobody can later mistake it
    for something this code observed.
    """

    #: The data team's stated collection date (datasets/DATA_SCHEMA.md).
    CAPTURED_AT = "2026-08-28"

    def __init__(self, retailer: str, path: Path | None = None, captured_at: str | None = None):
        if retailer not in KNOWN_RETAILERS:
            raise ValueError(f"unknown retailer {retailer!r}")
        self._retailer = retailer
        self._path = path or LINEAGE_B_DIR
        self._captured_at = captured_at or self.CAPTURED_AT

    @property
    def retailer(self) -> str:
        return self._retailer

    def fetch(self) -> list[RawOffer]:
        from ingestion.lineage_b import transform

        records = []
        for path in sorted(self._path.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for entry in payload["SmartGroceryProducts"]:
                item = entry.get("PutRequest", {}).get("Item", entry)
                records.append(
                    {k: (v.get("S") if "S" in v else v.get("N")) for k, v in item.items()}
                )

        offers, _report = transform(records, captured_at=self._captured_at)
        # Filtered AFTER the transform, because the retailer is derived from
        # `store_name` and the raw record has no field this could filter on.
        return [offer for offer in offers if offer.store == self._retailer]


@functools.cache
def _default_source_name(config_path: Path | None = None) -> str:
    """
    The default source name from config/data-sources.json.

    Cached: the file is static and ships in the archive. Falls back to
    "fixtures" only if the file is missing entirely -- a deployment without the
    config should still refresh from the safe, hand-verified catalogue rather
    than fail, and the fixtures are the one source guaranteed present.

    A `default_source` naming something `_SOURCE_BY_NAME` does not know is a
    config bug and RAISES, rather than defaulting past it -- the same reason
    classify() raises on an unmapped category. A typo that silently selected
    fixtures would hide that the intended source was never chosen.
    """
    path = config_path or DATA_SOURCES_CONFIG
    if not path.exists():
        return "fixtures"
    raw = json.loads(path.read_text(encoding="utf-8"))
    name = raw.get("default_source", "fixtures")
    if name not in _SOURCE_BY_NAME:
        raise ValueError(
            f"config/data-sources.json default_source={name!r} is not a known "
            f"source; expected one of {sorted(_SOURCE_BY_NAME)}"
        )
    return name


def resolve_source(retailer: str) -> PriceSource:
    """
    The only way ingestion obtains a source.

    Precedence, highest first (config/data-sources.json documents this too):

      1. LIVE_ACQUISITION=1  -> REFUSE. The acquisition tripwire wins over
         everything. A misconfiguration that silently starts requesting
         retailer sites is exactly the §4.2 exposure this project decided
         against, so it raises rather than falling back.
      2. PRICE_SOURCE env    -> explicit one-off override, for a load that wants
         a specific source without editing config.
      3. default_source in config/data-sources.json -> the reviewed default.

    Both non-live sources are RECORDED data on disk, so neither touches the
    acquisition gate; the choice is which catalogue the serving table is
    refreshed FROM, a data decision rather than a permission one. The data
    team's catalogue (lineage_b) is the recorded PRIMARY input; see the config
    file for why it is not yet the automatic runtime default.
    """
    if os.environ.get("LIVE_ACQUISITION") == "1":
        raise NotImplementedError(
            "Live acquisition is gated on ACQUISITION-RISK.md 8 and has no "
            "implementation. Task 11.4 is not started; condition 1 (a human "
            "reading the three unretrieved sources) is not met."
        )

    # Env override, then the config default. Names validated against the same
    # allowlist either way, so an unknown PRICE_SOURCE is a clear error rather
    # than a silent fall-through to fixtures.
    name = os.environ.get("PRICE_SOURCE") or _default_source_name()
    if name not in _SOURCE_BY_NAME:
        raise ValueError(
            f"PRICE_SOURCE={name!r} is not a known source; expected one of "
            f"{sorted(_SOURCE_BY_NAME)}"
        )

    if name == "lineage_b":
        return LineageBSource(retailer)
    return FixtureSource(retailer)
