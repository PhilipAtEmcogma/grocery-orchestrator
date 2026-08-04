"""
DynamoDB-backed PriceRepository.

NOT YET IMPLEMENTED — the AWS account is not provisioned. This module exists
so the import in handler.py resolves and the wiring is proven; every method
raises rather than returning plausible-looking empty results, because a
silently empty price list would look like "no data" instead of "misconfigured".

Schema is specified in DYNAMODB-SCHEMA.md.
"""

from __future__ import annotations

from src.retrieval.base import PriceRecord, PriceRepository
from src.schemas.contract import Store


class DynamoPriceRepository(PriceRepository):
    def __init__(self, table_name: str = "grocery-products-dev") -> None:
        self.table_name = table_name
        raise NotImplementedError(
            "DynamoPriceRepository is not implemented yet. "
            "Unset USE_DYNAMODB to run against fixtures."
        )

    def cheapest_for_product(
        self, product_key: str, *, limit: int = 5, stores: list[Store] | None = None
    ) -> list[PriceRecord]:
        raise NotImplementedError

    def resolve_product_key(self, user_term: str) -> str | None:
        raise NotImplementedError

    def candidates_for_budget(
        self,
        *,
        categories: list[str],
        exclude_categories: list[str],
        limit_per_category: int = 3,
    ) -> list[PriceRecord]:
        raise NotImplementedError
