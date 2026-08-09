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


class PriceRepository(Protocol):
    """Read-side interface over the price store."""

    def cheapest_for_product(
        self,
        product_key: str,
        *,
        limit: int = 5,
        stores: list[Store] | None = None,
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
    ) -> list[PriceRecord]:
        """
        Cheapest options across categories, used to build a meal-plan basket.

        Pre-filtering to affordable candidates before generation is one of the
        latency mitigations: the model assembles from a viable set rather than
        discovering mid-plan that it cannot afford things.
        """
        ...
