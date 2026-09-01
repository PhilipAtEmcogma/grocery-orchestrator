"""
DynamoDB-backed read/write for the append-only price history.

The boto3-touching half of `src/history`. The pure logic -- `to_history_item`,
`summarise`, `PriceBaseline` -- lives in `__init__.py` and imports no AWS, so it
stays testable with no account, the same seam `src/retrieval` and `src/store`
use.

WRITE is append-only at daily granularity: `put_item` on (history_pk,
valid_date), so a new capture date appends and a same-day re-run overwrites an
identical row. There is no update path and no delete path here on purpose -- a
baseline you can rewrite is not a baseline.

READ windows by `valid_date` and hands the rows to `summarise`. The window is a
key-condition range on the sort key, so DynamoDB does the date filtering and the
average is computed over exactly the rows in range.
"""

from __future__ import annotations

from datetime import date, timedelta

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from src.history import (
    HISTORY_TABLE,
    PriceBaseline,
    _record_from_item,
    summarise,
    to_history_item,
)

REGION = "ap-southeast-2"

# Bounds a single product's history read. A daily refresh over a year is ~365
# rows; this caps a pathological window rather than a normal one.
MAX_HISTORY_PAGES = 5


class DynamoPriceHistory:
    """Read/write the price-history table. Ops and reviewer use only."""

    def __init__(self, table_name: str = HISTORY_TABLE) -> None:
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

    @property
    def table_name(self) -> str:
        return self._table_name

    def append(self, product_items: list[dict]) -> int:
        """
        Append history rows for a batch of accepted products-table items.

        Called by ingestion after the products write, on the same accepted
        `items`. `batch_writer` overwrites on key match, which at daily
        granularity means a same-day re-run is idempotent and a new capture date
        is a new row -- exactly the append semantics a baseline wants.
        """
        with self._table.batch_writer() as batch:
            for item in product_items:
                batch.put_item(Item=to_history_item(item))
        return len(product_items)

    def baseline(
        self,
        store_key: str,
        product_key: str,
        *,
        window_days: int = 90,
        as_of: date | None = None,
    ) -> PriceBaseline:
        """
        The windowed price baseline for one product at one store.

        `as_of` defaults to today; pass it for tests and for evaluating a
        snapshot against the window that ended on its capture date. The window
        is `[as_of - window_days, as_of]`, applied as a sort-key range so the
        database does the filtering.
        """
        end = as_of or date.today()
        start = end - timedelta(days=window_days)
        history_pk = f"{store_key}#{product_key}"

        records = []
        start_key: dict | None = None
        for _page in range(MAX_HISTORY_PAGES):
            kwargs: dict = {
                "KeyConditionExpression": (
                    Key("history_pk").eq(history_pk)
                    & Key("valid_date").between(start.isoformat(), end.isoformat())
                ),
            }
            if start_key is not None:
                kwargs["ExclusiveStartKey"] = start_key
            response = self._table.query(**kwargs)
            records.extend(_record_from_item(i) for i in response.get("Items", []))
            start_key = response.get("LastEvaluatedKey")
            if start_key is None:
                break

        return summarise(records, window_days=window_days)
