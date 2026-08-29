"""
The retrieval boundary.

The graph depends on this Protocol, never on boto3. That is what lets the
entire orchestrator be built and tested with no AWS account, and it is what
makes CI runnable without credentials.

Two implementations:
  memory.py  — fixtures, used now and in all tests
  dynamo.py  — thin adapter, written when the AWS account lands
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from src.retrieval.filters import FreshnessFilter, NearFilter
from src.schemas.contract import Store


# frozen=True makes instances immutable/hashable; slots=True avoids a per-instance
# __dict__ since these are created in bulk from fixture/DB data.
@dataclass(frozen=True, slots=True)
class PriceRecord:
    """
    One product at one store. The retrieval layer's return type.

    Deliberately NOT the Citation model from the contract: retrieval is an
    internal concern and should not be coupled to the wire format. The
    generation node converts these into Citations.
    """

    product_key: str
    store: Store
    store_location: str
    display_name: str
    canonical_name: str
    category: str
    price_nzd: Decimal
    unit: str
    unit_price_nzd: Decimal
    pack_grams: int
    on_special: bool
    valid_date: str
    lat: float
    lon: float
    store_key: str  # DynamoDB base-table PK, e.g. "paknsave#sylvia-park"


class PriceRepository(Protocol):
    """Read-side interface over the price store."""

    @property
    def table_name(self) -> str:
        """The configured physical DynamoDB table name for citation provenance."""
        ...

    def cheapest_for_product(
        self,
        product_key: str,
        *,
        limit: int = 5,
        stores: list[Store] | None = None,
        near: NearFilter | None = None,
        freshness: FreshnessFilter | None = None,
    ) -> list[PriceRecord]:
        """
        All stores' prices for one product, CHEAPEST FIRST.

        Maps to a single DynamoDB GSI1 query with ScanIndexForward=True.
        Returns [] when the product is not stocked anywhere — the caller must
        treat that as the `no_data` path, never as licence to guess.

        `stores` distinguishes None from an empty list, and implementations
        must not collapse the two:

            None  — no constraint; every store is eligible
            []    — nothing is eligible; return []

        Spelled out because `if stores:` treats both as "no constraint", which
        silently WIDENS a filter. Widening is the dangerous direction: an empty
        intersection ("preferred AND nearby" matching nothing) would return the
        very stores the user ruled out. Callers wanting "any store" pass None
        explicitly — see the retrieval node.

        `near` and `freshness` are applied BEFORE `limit`, and that ordering is
        the requirement rather than an optimisation. Filtering after the limit
        would return nothing for a product whose five cheapest rows all happen
        to be out of radius or out of date, and the graph reads nothing as
        `no_data` — telling a shopper we have no price for something stocked
        fresh at the shop down the road. Implementations that cannot push these
        down to the query must over-fetch and page until the limit is satisfied
        from records that pass both.
        """
        ...

    def resolve_product_key(self, user_term: str) -> str | None:
        """
        Map free-text ("butter", "cheap mince") to a canonical product_key.

        Returns None when there is no confident match. This is the highest-risk
        function in the retrieval layer: a wrong match produces a confidently
        wrong price, which is worse than saying "I don't know".
        """
        ...

    def candidates_for_budget(
        self,
        *,
        categories: list[str],
        exclude_categories: list[str],
        limit_per_category: int = 3,
        budget_nzd: Decimal | None = None,
        near: NearFilter | None = None,
        freshness: FreshnessFilter | None = None,
    ) -> list[PriceRecord]:
        """
        Affordable options across categories, used to build a meal-plan basket.

        When `budget_nzd` is given, the returned set is capped by
        `cap_to_budget` so that buying EVERY product in it stays within the
        budget. Any selection the model then makes is affordable, whatever it
        picks.

        That guarantee is the only one available, because the model never sees
        a price -- the products table it is given carries refs, names, stores
        and pack sizes, and no money at all. It therefore cannot choose cheaper
        items or count the cost of what it has chosen. Constraining the set it
        chooses FROM is how a price-blind model is kept inside a budget.

        Passing None returns the unfiltered set, which is what a price check
        wants.
        """
        ...


def cap_to_budget(records: list[PriceRecord], budget_nzd: Decimal | None) -> list[PriceRecord]:
    """
    Trim a candidate set so buying ALL of it stays within budget.

    The guarantee is deliberately about the whole set rather than about any
    particular plan. A meal-plan draft may reference every candidate it is
    shown, so the only way to promise the result is affordable -- without
    letting the model see prices, which would break the grounding invariant --
    is to make every possible selection affordable.

    Selection is round-robin across categories, cheapest first within each, so
    a tight budget yields a spread of cheap products rather than four kinds of
    pantry staple. Ties break on product_key to keep the result deterministic:
    a candidate set that varied run to run would make plans unreproducible and
    the evals unrepeatable.

    A pack is counted once. The basket total does the same, since buying a
    pack twice is not how using it in two meals works.

    Returns everything when no budget is given, and an empty list when even the
    cheapest single product is unaffordable -- which is a true answer, and one
    the caller turns into an honest refusal rather than a plan nobody can buy.
    """
    if budget_nzd is None:
        return list(records)

    by_category: dict[str, list[PriceRecord]] = {}
    for rec in records:
        by_category.setdefault(rec.category, []).append(rec)
    for recs in by_category.values():
        recs.sort(key=lambda r: (r.price_nzd, r.product_key))

    chosen: list[PriceRecord] = []
    running = Decimal("0")
    categories = sorted(by_category)
    depth = 0
    while True:
        added_this_round = False
        for category in categories:
            bucket = by_category[category]
            if depth >= len(bucket):
                continue
            rec = bucket[depth]
            if running + rec.price_nzd <= budget_nzd:
                chosen.append(rec)
                running += rec.price_nzd
                added_this_round = True
        if not added_this_round:
            break
        depth += 1

    # Preserve the caller's original ordering, which groups by category and is
    # what the products table renders.
    keep = {id(r) for r in chosen}
    return [r for r in records if id(r) in keep]
