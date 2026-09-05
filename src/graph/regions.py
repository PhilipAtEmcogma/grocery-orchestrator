"""
Named shopping regions, resolved to a store-location scope.

The teammate dataset's demo scenarios ask for "near Albany", "North Shore" and
"West Auckland" in four cases out of five, and `Location` could only express a
place as coordinates. Worse, the 3,000 records in `datasets/` carry no `lat` or
`lon` at all, so a radius filter cannot run against them however well specified.

A region therefore resolves to a SET OF STORE LOCATIONS rather than a centre and
a radius. That is also the better model of what someone means: "North Shore" is
the shops on the Shore, not everything within N kilometres of a midpoint, and a
radius drawn around Takapuna reaches across the harbour bridge.

The mapping is `config/regions.json` — data, so someone who knows Auckland can
correct it without reading Python. Same reasoning as the feasibility floor and
the freshness threshold.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "regions.json"


@dataclass(frozen=True, slots=True)
class Region:
    """A named area and the store locations that sit in it."""

    name: str
    store_locations: frozenset[str]


@lru_cache(maxsize=1)
def _regions() -> dict[str, Region]:
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["regions"]
    out: dict[str, Region] = {}
    for name, entry in raw.items():
        region = Region(name=name, store_locations=frozenset(entry["store_locations"]))
        # Every alias points at the same Region, and a region's own name is an
        # alias of itself, so "North Shore" and "near Albany" both land here.
        for alias in entry["aliases"]:
            out[alias.lower()] = region
    return out


def known_regions() -> list[str]:
    """Region names, for telling a user what we can actually honour."""
    return sorted({r.name for r in _regions().values()})


def resolve_region(text: str) -> Region | None:
    """
    The region a phrase names, or None if it names none that we know.

    Matched on word boundaries so "Albany" is found inside a sentence but
    "Albanyville" is not. Longest alias first, so "north shore" is preferred
    over a shorter alias that happens to be a substring of it.
    """
    if not text:
        return None
    lowered = text.lower()
    for alias in sorted(_regions(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            return _regions()[alias]
    return None


def locations_for(region_name: str) -> frozenset[str] | None:
    """
    The store-location scope for an explicitly supplied region name.

    Returns None for a region we cannot map, and the caller must NOT treat that
    as "no filter". Ignoring an unrecognised region would answer a request about
    Whangarei with Auckland prices and give no sign the location was dropped —
    the same silent widening that Req 1.5 forbids for a radius.
    """
    region = _regions().get(region_name.strip().lower())
    return region.store_locations if region else None


def strip_region(text: str) -> str:
    """
    The message with any region phrase removed, for item extraction.

    "cheapest butter near Albany" extracted the item as "butter albany", which
    resolves to nothing, so the turn returned `no_data` for a product we stock.
    The place is not part of the product name, and the extractor has no way to
    know that -- so the region is resolved separately from the ORIGINAL message
    and removed before the message reaches the classifier.

    Latent before regions existed: the query failed the same way, and nobody
    noticed because there was no way to ask for a region in the first place.
    """
    if not text:
        return text
    out = text
    for alias in sorted(_regions(), key=len, reverse=True):
        # Drop a leading preposition with it, so "butter near Albany"
        # becomes "butter" rather than "butter near". Word boundaries so
        # "Albany" is removed and "Albanyville" is left alone.
        pattern = r"\b(?:near|in|around|at|by)?\s*" + re.escape(alias) + r"\b"
        out = re.sub(pattern, " ", out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip()
