"""
Fixture-backed PriceRepository. No AWS required.

Used for all local development and all tests. The DynamoDB implementation must
satisfy the same tests — that is enforced, not merely stated, by
tests/test_price_repository_contract.py, which is parameterised over every
implementation of the protocol.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

from src.retrieval.base import PriceRecord, PriceRepository, cap_to_budget
from src.retrieval.filters import FreshnessFilter, NearFilter
from src.schemas.contract import Store

# ---------------------------------------------------------------- synonyms
# Free-text terms a user might type, mapped to canonical product keys.
# Deliberately explicit rather than fuzzy: a wrong match is worse than no
# match, because it produces a confidently incorrect price.
#
# The table itself lives in config/product-synonyms.json, config-as-data like
# regions.json and freshness.json, because which words mean which grocery item
# is knowledge about shopping rather than about Python. It used to be a literal
# dict here; that was fine for 26 fixture products and unworkable for the 528
# in the data team's catalogue, where the generated half is produced by
# scripts/generate_synonyms.py.

SYNONYMS_CONFIG = Path(__file__).resolve().parents[2] / "config" / "product-synonyms.json"


def load_synonyms(config_path: Path | None = None) -> dict[str, list[str]]:
    """
    Term -> the product keys it could mean, most-preferred first.

    A LIST, not a single key, because the file describes more than one
    catalogue and the same word names a different product in each: "butter" is
    `butter-500g` in the fixtures and `salted-butter-500g` in the data team's
    catalogue. The repository picks the first candidate that exists in the data
    actually loaded, so the table needs no knowledge of which catalogue it is
    serving and neither implementation has to be told.

    Head terms come before generated product names within a catalogue: a
    deliberate human choice outranks a mechanical restatement of a name.
    """
    raw = json.loads((config_path or SYNONYMS_CONFIG).read_text(encoding="utf-8"))
    candidates: dict[str, list[str]] = {}
    for catalogue in raw["catalogues"].values():
        for section in ("head_terms", "generated_product_names"):
            for phrase, key in catalogue.get(section, {}).items():
                if phrase.startswith("_"):
                    continue
                term = normalise_term(phrase)
                if not term:
                    continue
                keys = candidates.setdefault(term, [])
                if key not in keys:
                    keys.append(key)
    return candidates


# Words to strip before matching. "cheapest butter near me" -> "butter"
NOISE = {
    "cheapest",
    "cheap",
    "best",
    "price",
    "prices",
    "cost",
    "of",
    "the",
    "a",
    "some",
    "near",
    "me",
    "nearby",
    "around",
    "here",
    "what",
    "whats",
    "is",
    "how",
    "much",
    "for",
    "buy",
    "get",
    "find",
    "want",
    "need",
    "please",
}


def normalise_term(text: str) -> str:
    """
    Lowercase, strip punctuation and noise words. Order preserved.

    Single-character tokens are dropped: splitting "what's" on punctuation
    leaves a stray "s" that would otherwise block an exact match. No product
    term in the catalogue is one character long.
    """
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower())  # lowercase, punctuation -> spaces
    words = [w for w in cleaned.split() if len(w) > 1 and w not in NOISE]
    return " ".join(words)


class InMemoryPriceRepository(PriceRepository):
    def __init__(self, fixture_path: Path | None = None) -> None:
        # Default to the repo-level fixtures/products.json unless overridden.
        path = fixture_path or (Path(__file__).resolve().parents[2] / "fixtures" / "products.json")
        raw = json.loads(path.read_text(encoding="utf-8"))

        # Parse every raw JSON record into a typed, immutable PriceRecord.
        self._records: list[PriceRecord] = [
            PriceRecord(
                product_key=r["product_key"],
                store=Store(r["store"]),
                store_location=r["store_location"],
                display_name=r["display_name"],
                canonical_name=r["canonical_name"],
                category=r["category"],
                price_nzd=Decimal(r["price_nzd"]),
                unit=r["unit"],
                unit_price_nzd=Decimal(r["unit_price_nzd"]),
                pack_grams=r["pack_grams"],
                on_special=r["on_special"],
                valid_date=r["valid_date"],
                lat=r["lat"],
                lon=r["lon"],
                store_key=r["store_key"],
            )
            for r in raw
        ]

        # Mirrors the GSI1 access pattern: partition by product, sorted by price.
        # Built BEFORE the synonyms, which are filtered against it.
        self._by_product: dict[str, list[PriceRecord]] = {}
        for rec in self._records:
            self._by_product.setdefault(rec.product_key, []).append(rec)
        for recs in self._by_product.values():
            recs.sort(key=lambda r: (r.price_nzd, r.store.value, r.store_location))

        # Synonym phrases are already normalised by load_synonyms(), so "block
        # of butter" (where "of" is a noise word) matches once the user's term
        # is stripped the same way.
        #
        # Entries are filtered to keys this catalogue actually holds. The table
        # describes several catalogues and only one is loaded, so an entry for
        # the other simply does not apply -- and dropping it here means a
        # resolved term always has prices behind it, which is the guarantee the
        # DynamoDB implementation makes by querying. The two must agree: they
        # are held to it by tests/test_price_repository_contract.py.
        self._synonyms: dict[str, str] = {}
        for term, keys in load_synonyms().items():
            for key in keys:
                if key in self._by_product:
                    self._synonyms[term] = key
                    break

    # ------------------------------------------------------------ interface

    @property
    def table_name(self) -> str:
        return "grocery-products-dev"

    def cheapest_for_product(
        self,
        product_key: str,
        *,
        limit: int = 5,
        stores: list[Store] | None = None,
        near: NearFilter | None = None,
        locations: frozenset[str] | None = None,
        freshness: FreshnessFilter | None = None,
    ) -> list[PriceRecord]:
        # _by_product entries are pre-sorted cheapest-first, so every filter
        # applies to the full list and the slice happens LAST. Slicing first
        # would drop an in-radius, in-date price behind five that are neither.
        recs = self._by_product.get(product_key, [])
        # `is not None`, not truthiness: an explicit [] means nothing qualifies.
        # `if stores:` would treat it as "no filter" and return every store —
        # widening a constraint rather than honouring it. See base.py.
        if stores is not None:
            allowed = set(stores)
            recs = [r for r in recs if r.store in allowed]
        if near is not None:
            recs = [r for r in recs if near.covers(r.lat, r.lon)]
        if locations is not None:
            recs = [r for r in recs if r.store_location in locations]
        if freshness is not None:
            recs = [r for r in recs if freshness.is_fresh(r.valid_date)]
        return recs[:limit]

    def resolve_product_key(self, user_term: str) -> str | None:
        """
        Exact match after noise stripping. NO substring fallback.

        Substring matching is tempting and wrong: "truffle oil" contains "oil"
        and would resolve to canola oil, producing a confidently incorrect
        price. An unrecognised modifier word must yield None so the caller
        takes the no_data path. Under-matching is recoverable; mis-matching
        silently lies to the user.
        """
        term = normalise_term(user_term)
        if not term:
            return None

        # 1. Try the synonym table (free-text phrase -> canonical key).
        if term in self._synonyms:
            return self._synonyms[term]

        # 2. Direct product_key, e.g. from a UI chip rather than free text.
        as_key = term.replace(" ", "-")
        if as_key in self._by_product:
            return as_key

        # 3. No confident match: return None rather than guessing.
        return None

    def candidates_for_budget(
        self,
        *,
        categories: list[str],
        exclude_categories: list[str],
        limit_per_category: int = 3,
        budget_nzd: Decimal | None = None,
        near: NearFilter | None = None,
        locations: frozenset[str] | None = None,
        freshness: FreshnessFilter | None = None,
    ) -> list[PriceRecord]:
        excluded = set(exclude_categories)
        wanted = set(categories) - excluded

        # For each remaining category, take the cheapest distinct products
        # up to limit_per_category (a product may have multiple store
        # records; only the first, cheapest one per product is kept).
        # Filters apply to the CANDIDATE POOL, before per-category selection.
        # A plan built from out-of-radius or out-of-date prices is wrong in the
        # same way a comparison is: it sends the shopper somewhere they cannot
        # go, or quotes a price that has since moved.
        pool = self._records
        if near is not None:
            pool = [r for r in pool if near.covers(r.lat, r.lon)]
        if locations is not None:
            pool = [r for r in pool if r.store_location in locations]
        if freshness is not None:
            pool = [r for r in pool if freshness.is_fresh(r.valid_date)]

        out: list[PriceRecord] = []
        for category in sorted(wanted):
            seen_products: set[str] = set()
            for rec in sorted(pool, key=lambda r: r.price_nzd):
                if rec.category != category or rec.product_key in seen_products:
                    continue
                seen_products.add(rec.product_key)
                out.append(rec)
                if len(seen_products) >= limit_per_category:
                    break
        # Cap so that buying every candidate stays inside the budget; the
        # model cannot see prices and so cannot keep itself inside one.
        return cap_to_budget(out, budget_nzd)

    # ------------------------------------------------------------ helpers

    @property
    def all_categories(self) -> list[str]:
        return sorted({r.category for r in self._records})

    @property
    def all_records(self) -> list[PriceRecord]:
        """
        Every loaded record.

        For harnesses that must check a plan against the product data rather
        than against what a model claims. A Citation deliberately does not
        carry `category` -- it is a wire type for the frontend, which has no
        use for it -- so the eval needs a way back from a cited product to the
        record it came from. Reaching into `_records` for that, or re-reading
        the fixture file alongside the repository, both create a second source
        of truth that can drift from this one.
        """
        return list(self._records)
