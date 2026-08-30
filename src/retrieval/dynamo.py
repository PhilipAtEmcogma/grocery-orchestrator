"""
DynamoDB-backed PriceRepository.

Schema is specified in DYNAMODB-SCHEMA.md. Acceptance criteria live in
tests/test_price_repository_contract.py — run against a real table with:

    PRICE_REPO_DYNAMO_TABLE=grocery-products-dev python -m pytest \
        tests/test_price_repository_contract.py

The GSI1 index (PK=product_key, SK=gsi1_sk with zero-padded price) means
`cheapest_for_product` is a single query already sorted — no application-side
sorting needed. `candidates_for_budget` queries GSI2 (PK=category,
SK=gsi2_sk with zero-padded price), which is likewise sorted at the source.

**It used to Scan, and this header said so two commits after it stopped.**
Pilot Task 6b replaced the Scan with GSI2 on 2026-08-30 and the `Scan`
permission was revoked from the orchestrator role, so the description here
outlived the behaviour it described — while the method's own docstring was
correct the whole time. Recorded because the correction is the point: a
module header is the least-read and most-quoted piece of documentation in a
file, and it is where a stale claim survives longest.

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
from src.retrieval.memory import load_synonyms, normalise_term
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
        #
        # Term -> candidate keys, because config/product-synonyms.json covers
        # more than one catalogue. The in-memory version can filter these at
        # construction because it holds the whole catalogue; this one cannot,
        # so it filters at resolve time by querying GSI1. Same answer, reached
        # differently, which is what the shared contract suite exists to check.
        self._synonyms: dict[str, list[str]] = load_synonyms()

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
        locations: frozenset[str] | None = None,
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
        filtering = (
            allowed is not None
            or near is not None
            or locations is not None
            or freshness is not None
        )
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
            if locations is not None:
                page = [r for r in page if r.store_location in locations]
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

        # 1. Synonym table, in preference order. The first candidate with rows
        #    behind it wins; one that names a product from a catalogue this
        #    table does not hold is skipped rather than returned empty.
        for candidate in self._synonyms.get(term, []):
            if self._has_rows(candidate):
                return candidate

        # 2. Direct product_key, e.g. from a UI chip rather than free text.
        as_key = term.replace(" ", "-")
        if self._has_rows(as_key):
            return as_key

        # 3. No confident match: return None rather than guessing.
        return None

    def _has_rows(self, product_key: str) -> bool:
        """
        Does this key have any prices? One GSI1 item is enough to know.

        Every synonym is checked through here, so a stale table entry can never
        produce a key with no prices -- the in-memory implementation gets the
        same guarantee by filtering against the catalogue it holds.
        """
        response = self._table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("product_key").eq(product_key),
            Limit=1,
            Select="COUNT",
        )
        return response.get("Count", 0) > 0

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
        """
        Cheapest distinct products per category, excluding dietary categories.

        ONE QUERY PER WANTED CATEGORY against GSI2, which partitions by
        `category` and sorts by zero-padded price, so the cheapest rows are the
        first ones read and the query stops early.

        This replaced a full-table `Scan`, and the replacement was deferred
        deliberately until there was evidence to choose it on (Pilot Task 6b).
        There now is, and it points one way:

        * **The access pattern** is literally partition-by-category,
          sort-by-price -- `limit_per_category` cheapest distinct products in
          each of about eight categories.
        * **The load**: the catalogue is 2,939 rows rather than the 152 seeded
          ones. A Scan reads every row on every meal-plan turn to return roughly
          two dozen, and DynamoDB charges for rows read, not rows returned.
        * **Independent corroboration**: the data team reached the same shape
          without consulting us -- `smart-grocery-products-dev` carries a
          `CategoryPriceIndex` on exactly `category`/`price`.

        The filters stay application-side. Distance is geometry, freshness is a
        non-key attribute, and neither is expressible as a key condition, so
        this over-fetches and pages exactly as `cheapest_for_product` does --
        and for the same reason: filtering one page and returning the survivors
        is the truncation defect that reported `no_data` for a stocked product.
        """
        excluded = set(exclude_categories)
        wanted = set(categories) - excluded

        if not wanted:
            return []

        filtering = near is not None or locations is not None or freshness is not None
        # Distinct PRODUCTS are wanted, but the index holds one row per store,
        # so a popular product can occupy many consecutive rows. Over-fetch
        # enough to see past that, and more again when a filter may discard
        # most of what comes back.
        page_size = limit_per_category * (20 if filtering else 5)

        out: list[PriceRecord] = []
        for category in sorted(wanted):
            seen_products: set[str] = set()
            chosen: list[PriceRecord] = []
            start_key: dict | None = None

            for _page in range(MAX_QUERY_PAGES):
                kwargs: dict = {
                    "IndexName": "GSI2",
                    "KeyConditionExpression": Key("category").eq(category),
                    "ScanIndexForward": True,  # zero-padded price: cheapest first
                    "Limit": page_size,
                }
                if start_key is not None:
                    kwargs["ExclusiveStartKey"] = start_key

                response = self._table.query(**kwargs)
                for item in response.get("Items", []):
                    record = _to_record(item)
                    if near is not None and not near.covers(record.lat, record.lon):
                        continue
                    if locations is not None and record.store_location not in locations:
                        continue
                    if freshness is not None and not freshness.is_fresh(record.valid_date):
                        continue
                    if record.product_key in seen_products:
                        continue
                    seen_products.add(record.product_key)
                    chosen.append(record)
                    if len(chosen) >= limit_per_category:
                        break

                start_key = response.get("LastEvaluatedKey")
                if len(chosen) >= limit_per_category or start_key is None:
                    break

            out.extend(chosen)

        # Same cap as the fixture implementation, from the same helper: the
        # contract suite runs over both, so a divergence here would be a
        # different affordability guarantee depending on where prices live.
        return cap_to_budget(out, budget_nzd)
