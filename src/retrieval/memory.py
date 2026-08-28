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

from src.retrieval.base import PriceRecord, PriceRepository
from src.schemas.contract import Store

# ---------------------------------------------------------------- synonyms
# Free-text terms a user might type, mapped to canonical product keys.
# Deliberately explicit rather than fuzzy: a wrong match is worse than no
# match, because it produces a confidently incorrect price.

SYNONYMS: dict[str, str] = {
    "butter": "butter-500g",
    "block of butter": "butter-500g",
    "milk": "milk-2l",
    "cheese": "cheese-tasty-1kg",
    "tasty cheese": "cheese-tasty-1kg",
    "yoghurt": "yoghurt-plain-1kg",
    "yogurt": "yoghurt-plain-1kg",
    "eggs": "eggs-size7-dozen",
    "dozen eggs": "eggs-size7-dozen",
    "mince": "beef-mince-1kg",
    "beef mince": "beef-mince-1kg",
    "ground beef": "beef-mince-1kg",
    "chicken": "chicken-thigh-1kg",
    "chicken thighs": "chicken-thigh-1kg",
    "sausages": "pork-sausages-500g",
    "tuna": "tuna-canned-185g",
    "canned tuna": "tuna-canned-185g",
    "salmon": "salmon-fillet-300g",
    "pasta": "pasta-spirals-500g",
    "rice": "rice-longgrain-1kg",
    "canned tomatoes": "tomatoes-canned-400g",
    "tinned tomatoes": "tomatoes-canned-400g",
    "chopped tomatoes": "tomatoes-canned-400g",
    "baked beans": "beans-baked-420g",
    "lentils": "lentils-dried-500g",
    "flour": "flour-plain-1-5kg",
    "oats": "oats-rolled-1kg",
    "porridge": "oats-rolled-1kg",
    "oil": "oil-canola-750ml",
    "cooking oil": "oil-canola-750ml",
    "bread": "bread-white-700g",
    "onions": "onions-brown-1-5kg",
    "potatoes": "potatoes-washed-2kg",
    "spuds": "potatoes-washed-2kg",
    "carrots": "carrots-1kg",
    "broccoli": "broccoli-each",
    "bananas": "bananas-1kg",
    "frozen vegetables": "frozen-mixed-veg-1kg",
    "frozen veg": "frozen-mixed-veg-1kg",
    "mixed vegetables": "frozen-mixed-veg-1kg",
    "peas": "frozen-peas-1kg",
    "frozen peas": "frozen-peas-1kg",
}

# Words to strip before matching. "cheapest butter near me" -> "butter"
NOISE = {
    "cheapest", "cheap", "best", "price", "prices", "cost", "of", "the", "a",
    "some", "near", "me", "nearby", "around", "here", "what", "whats", "is",
    "how", "much", "for", "buy", "get", "find", "want", "need", "please",
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
        path = fixture_path or (
            Path(__file__).resolve().parents[2] / "fixtures" / "products.json"
        )
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

        # Synonym keys are normalised too, so "block of butter" (where "of" is
        # a noise word) still matches once the user's term is stripped.
        self._synonyms: dict[str, str] = {
            normalise_term(phrase): key for phrase, key in SYNONYMS.items()
        }

        # Mirrors the GSI1 access pattern: partition by product, sorted by price.
        self._by_product: dict[str, list[PriceRecord]] = {}
        for rec in self._records:
            self._by_product.setdefault(rec.product_key, []).append(rec)
        for recs in self._by_product.values():
            recs.sort(key=lambda r: (r.price_nzd, r.store.value, r.store_location))

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
    ) -> list[PriceRecord]:
        # _by_product entries are pre-sorted cheapest-first, so filtering by
        # store and slicing to `limit` is all that's needed here.
        recs = self._by_product.get(product_key, [])
        # `is not None`, not truthiness: an explicit [] means nothing qualifies.
        # `if stores:` would treat it as "no filter" and return every store —
        # widening a constraint rather than honouring it. See base.py.
        if stores is not None:
            allowed = set(stores)
            recs = [r for r in recs if r.store in allowed]
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
    ) -> list[PriceRecord]:
        excluded = set(exclude_categories)
        wanted = set(categories) - excluded

        # For each remaining category, take the cheapest distinct products
        # up to limit_per_category (a product may have multiple store
        # records; only the first, cheapest one per product is kept).
        out: list[PriceRecord] = []
        for category in sorted(wanted):
            seen_products: set[str] = set()
            for rec in sorted(self._records, key=lambda r: r.price_nzd):
                if rec.category != category or rec.product_key in seen_products:
                    continue
                seen_products.add(rec.product_key)
                out.append(rec)
                if len(seen_products) >= limit_per_category:
                    break
        return out

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
