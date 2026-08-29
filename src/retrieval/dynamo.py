"""
DynamoDB-backed PriceRepository.

Schema is specified in DYNAMODB-SCHEMA.md. Acceptance criteria live in
tests/test_price_repository_contract.py — run against a real table with:

    PRICE_REPO_DYNAMO_TABLE=grocery-products-dev python -m pytest \
        tests/test_price_repository_contract.py

The GSI1 index (PK=product_key, SK=gsi1_sk with zero-padded price) means
`cheapest_for_product` is a single query already sorted — no application-side
sorting needed. `candidates_for_budget` scans the base table because it needs
cross-product, cross-store coverage filtered by category; this is acceptable
at seed-data scale (~150 items) and will need a category GSI if the catalogue
grows past ~5,000 items.

`resolve_product_key` reuses the same synonym table as the in-memory
implementation. The mapping is application logic (noisy free-text -> canonical
key), not storage logic, so it belongs in the same place regardless of backend.
"""

from __future__ import annotations

from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from src.retrieval.base import PriceRecord, PriceRepository, cap_to_budget
from src.retrieval.filters import FreshnessFilter, NearFilter
from src.retrieval.memory import SYNONYMS, normalise_term
from src.schemas.contract import Store

REGION = "ap-southeast-2"

# Worst-case pages followed for one product lookup. Bounds latency against the
# gateway's 29-second ceiling; GSI1 is price-ordered so the useful results are
# in the earliest pages.
MAX_QUERY_PAGES = 5


def _to_record(item: dict) -> PriceRecord:
    """Convert a DynamoDB item (resource API, already deserialized) to PriceRecord."""
    return PriceRecord(
        product_key=item["product_key"],
        store=Store(item["store"]),
        store_location=item["store_location"],
        display_name=item["display_name"],
        canonical_name=item["canonical_name"],
        category=item["category"],
        price_nzd=Decimal(item["price_nzd"]),
        unit=item["unit"],
        unit_price_nzd=Decimal(item["unit_price_nzd"]),
        pack_grams=int(item["pack_grams"]),
        on_special=bool(item["on_special"]),
        valid_date=item["valid_date"],
        lat=float(item["lat"]),
        lon=float(item["lon"]),
        store_key=item["store_key"],
    )


class DynamoPriceRepository(PriceRepository):
    def __init__(self, table_name: str = "grocery-products-dev") -> None:
        self._table_name = table_name
        dynamodb = boto3.resource(
            "dynamodb",
            region_name=REGION,
            config=Config(
                retries={"max_attempts": 2, "mode": "standard"},
                read_timeout=10,
                connect_timeout=5,
            ),
        )
        self._table = dynamodb.Table(table_name)  # type: ignore[union-attr]

        # Synonym table is the same application-level mapping as the in-memory
        # implementation. It's free-text interpretation, not storage concern.
        self._synonyms: dict[str, str] = {
            normalise_term(phrase): key for phrase, key in SYNONYMS.items()
        }

    # ------------------------------------------------------------ interface

    @property
    def table_name(self) -> str:
        return self._table_name

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
        GSI1 query: partition by product_key, sorted by gsi1_sk (price first).

        ScanIndexForward=True gives cheapest first — the GSI sort key is
        zero-padded price cents, so lexicographic order is price order.
        """
        # Explicit empty list means "no store qualifies" — return nothing.
        # None means "any store". See base.py for the reasoning.
        if stores is not None and len(stores) == 0:
            return []

        # Query GSI1, following pages until `limit` MATCHING records are in
        # hand or the index is exhausted.
        #
        # The previous version issued one query with `Limit=limit * 5` and
        # ignored `LastEvaluatedKey`. DynamoDB applies `Limit` to items READ,
        # before any application-side filter, so when `stores` was set and none
        # of the first page happened to be at those stores, this returned an
        # empty list -- and the graph reads an empty list as `no_data`, telling
        # a shopper "I don't have price data for butter" about a product that
        # store stocks. A short page is also normal at a 1MB boundary, with
        # `LastEvaluatedKey` set and more results waiting.
        #
        # It cannot fire on the fixtures: six records per product is one page.
        # It fires at real scale, where a popular product spans three chains
        # and many stores.
        allowed = set(stores) if stores is not None else None
        # None of these is expressible as a GSI1 key condition -- distance is
        # geometry and freshness is a non-key attribute -- so all three are
        # applied after the query returns, which means over-fetching and paging
        # exactly as the store filter already does. Filtering one page and
        # returning what survives is the truncation defect that reported
        # `no_data` for a stocked product.
        filtering = allowed is not None or near is not None or freshness is not None
        fetch_limit = limit * 5 if filtering else limit

        records: list[PriceRecord] = []
        start_key: dict | None = None
        # GSI1 is price-ordered, so the pages we want are the first ones. The
        # cap bounds worst-case latency against the gateway ceiling rather than
        # correctness: exhausting it means this store genuinely has nothing
        # near the cheapest end, which is the honest `no_data` case.
        for _page in range(MAX_QUERY_PAGES):
            kwargs: dict = {
                "IndexName": "GSI1",
                "KeyConditionExpression": Key("product_key").eq(product_key),
                "ScanIndexForward": True,
                "Limit": fetch_limit,
            }
            if start_key is not None:
                kwargs["ExclusiveStartKey"] = start_key

            response = self._table.query(**kwargs)
            page = [_to_record(item) for item in response.get("Items", [])]
            if allowed is not None:
                page = [r for r in page if r.store in allowed]
            if near is not None:
                page = [r for r in page if near.covers(r.lat, r.lon)]
            if freshness is not None:
                page = [r for r in page if freshness.is_fresh(r.valid_date)]
            records.extend(page)

            start_key = response.get("LastEvaluatedKey")
            if len(records) >= limit or start_key is None:
                break

        return records[:limit]

    def resolve_product_key(self, user_term: str) -> str | None:
        """
        Exact match after noise stripping. Same logic as the in-memory version.

        Verifies the resolved key actually exists in DynamoDB before returning
        it, so a stale synonym table cannot produce a key with no prices.
        """
        term = normalise_term(user_term)
        if not term:
            return None

        # 1. Synonym table
        candidate = self._synonyms.get(term)

        # 2. Direct product_key
        if candidate is None:
            as_key = term.replace(" ", "-")
            # Check if this key exists by querying GSI1 (one item is enough)
            response = self._table.query(
                IndexName="GSI1",
                KeyConditionExpression=Key("product_key").eq(as_key),
                Limit=1,
                Select="COUNT",
            )
            if response.get("Count", 0) > 0:
                candidate = as_key

        # 3. Verify the synonym-resolved key actually has records
        if candidate and candidate not in self._synonyms.values():
            # Already verified via the GSI1 query above
            pass
        elif candidate:
            # Synonym-resolved — verify it exists
            response = self._table.query(
                IndexName="GSI1",
                KeyConditionExpression=Key("product_key").eq(candidate),
                Limit=1,
                Select="COUNT",
            )
            if response.get("Count", 0) == 0:
                return None

        return candidate

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
        Cheapest distinct products per category, excluding dietary categories.

        Scans the base table and filters in application code. At ~150 seed
        records this is a single page; at scale this would need a category GSI.
        The contract suite verifies the invariants regardless of implementation.
        """
        excluded = set(exclude_categories)
        wanted = set(categories) - excluded

        if not wanted:
            return []

        # Scan the full table. At seed scale (~150 items) this is one page.
        all_items: list[dict] = []
        response = self._table.scan()
        all_items.extend(response.get("Items", []))
        while "LastEvaluatedKey" in response:
            response = self._table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            all_items.extend(response.get("Items", []))

        # Convert and sort by price
        all_records = [_to_record(item) for item in all_items]
        # Same reasoning as the in-memory implementation: filter the pool before
        # per-category selection, never after.
        if near is not None:
            all_records = [r for r in all_records if near.covers(r.lat, r.lon)]
        if freshness is not None:
            all_records = [r for r in all_records if freshness.is_fresh(r.valid_date)]
        all_records.sort(key=lambda r: r.price_nzd)

        # Pick cheapest distinct products per category
        out: list[PriceRecord] = []
        for category in sorted(wanted):
            seen_products: set[str] = set()
            for rec in all_records:
                if rec.category != category or rec.product_key in seen_products:
                    continue
                seen_products.add(rec.product_key)
                out.append(rec)
                if len(seen_products) >= limit_per_category:
                    break

        # Same cap as the fixture implementation, from the same helper: the
        # contract suite runs over both, so a divergence here would be a
        # different affordability guarantee depending on where prices live.
        return cap_to_budget(out, budget_nzd)
