"""
Price ingestion Lambda.

One invocation refreshes ONE retailer. Step Functions Inline Map fans the three
out, which is what tech.md and design.md 33 specify and what the reviewed
architecture diagram omitted: a single Lambda fanning out to three retailers
couples them into one failure domain, so one retailer being slow or broken
takes the whole refresh with it. Per-source invocations fail independently,
retry independently, and report per-source counts.

Event shape:  {"retailer": "paknsave"}                 -- refresh
              {"retailer": "paknsave", "dry_run": true} -- report, write nothing

EVERY RUN DIFFS BEFORE IT WRITES. The first version of this module did not, and
a defect in `unit_price()` wrote unit_price_nzd "2490.00" against a $2.49
broccoli into the live products table -- a shopper-facing figure, a thousand
times over, across six rows, with no signal that anything had changed. The
tests were green; the write was simply believed.

The diff is not a safety interlock, and deliberately does not block on a
threshold: with live acquisition, a genuine special can move a real
proportion of a retailer's catalogue, so a percentage gate would either be too
loose to catch a defect or would refuse legitimate refreshes. What it does is
make the change VISIBLE -- the counts and a sample land in the Step Functions
execution history and CloudWatch, so "150 rows changed on a day nothing was on
special" is answerable after the fact instead of undiscoverable. Run with
`dry_run` first when the normaliser has changed.
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from ingestion.normalise import to_item
from ingestion.sources import KNOWN_RETAILERS, resolve_source

REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
TABLE = os.environ.get("PRODUCTS_TABLE", "grocery-products-dev")

# Fields whose change is worth reporting. Excludes nothing meaningful today;
# named explicitly so adding a field to to_item() is a decision about whether
# a change in it should be visible, not a silent omission.
DIFFED_FIELDS = (
    "price_nzd",
    "unit_price_nzd",
    "gsi1_sk",
    "valid_date",
    "on_special",
    "display_name",
    "pack_grams",
)

# How many changed keys to name in the result. The execution history is not a
# place to dump a catalogue; a handful is enough to recognise a wrong shape.
SAMPLE_LIMIT = 5


def _existing(table: Any, items: list[dict]) -> dict[tuple[str, str], dict]:
    """
    Current rows for the store keys this refresh touches.

    Queried per store_key rather than scanned: the base table partitions by
    store, so this is one query per store the retailer has, and it stays one
    query per store as the catalogue grows.
    """
    out: dict[tuple[str, str], dict] = {}
    for store_key in sorted({item["store_key"] for item in items}):
        response = table.query(KeyConditionExpression=Key("store_key").eq(store_key))
        rows = list(response.get("Items", []))
        while "LastEvaluatedKey" in response:
            response = table.query(
                KeyConditionExpression=Key("store_key").eq(store_key),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            rows.extend(response.get("Items", []))
        for row in rows:
            out[(row["store_key"], row["product_key"])] = row
    return out


def diff_items(existing: dict[tuple[str, str], dict], items: list[dict]) -> dict:
    """
    What this refresh would change. Pure -- no AWS, exhaustively testable.

    Values are compared as strings because that is how they are stored: money
    is a string at rest (a float cent is a wrong cent), and a Decimal read back
    from DynamoDB compares unequal to the str it was written from.
    """
    added: list[str] = []
    changed: list[dict] = []

    for item in items:
        key = (item["store_key"], item["product_key"])
        before = existing.get(key)
        if before is None:
            added.append(f"{key[0]}/{key[1]}")
            continue
        fields = [
            {
                "field": field,
                "from": str(before.get(field)),
                "to": str(item[field]),
            }
            for field in DIFFED_FIELDS
            if str(before.get(field)) != str(item[field])
        ]
        if fields:
            changed.append({"key": f"{key[0]}/{key[1]}", "fields": fields})

    return {
        "added": len(added),
        "changed": len(changed),
        "unchanged": len(items) - len(added) - len(changed),
        "sample_added": added[:SAMPLE_LIMIT],
        "sample_changed": changed[:SAMPLE_LIMIT],
    }


def refresh(
    retailer: str, table_name: str = TABLE, *, dry_run: bool = False
) -> dict[str, Any]:
    """
    Fetch one retailer, report what would change, and write unless dry_run.

    `batch_writer` overwrites on key match, so a re-run is idempotent: the same
    fixture produces the same items, and the diff proves it rather than
    asserting it -- `unchanged` equal to `fetched` is what idempotent looks
    like from the outside.
    """
    source = resolve_source(retailer)
    offers = source.fetch()
    items = [to_item(offer) for offer in offers]

    table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)  # type: ignore[union-attr]
    delta = diff_items(_existing(table, items), items)

    if not dry_run:
        with table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)

    dates = sorted({item["valid_date"] for item in items})
    return {
        "retailer": retailer,
        "fetched": len(offers),
        "written": 0 if dry_run else len(items),
        "dry_run": dry_run,
        # Reported so a stale refresh is visible in the execution history
        # rather than only discoverable by querying the table.
        "captured_at": dates[-1] if dates else None,
        "table": table_name,
        **delta,
    }


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    event = event or {}
    retailer = event.get("retailer")
    if retailer not in KNOWN_RETAILERS:
        # Raise rather than return an error shape: an unknown retailer is a
        # state-machine definition bug, and Step Functions should surface it as
        # a failed execution rather than a successful one with a sad payload.
        raise ValueError(
            f"retailer must be one of {KNOWN_RETAILERS}, got {retailer!r}"
        )
    return refresh(retailer, dry_run=bool(event.get("dry_run")))
