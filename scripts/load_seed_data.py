"""
Load seed fixture data into the DynamoDB products table.

Usage:
    python scripts/load_seed_data.py
    python scripts/load_seed_data.py --table grocery-products-prod
    python scripts/load_seed_data.py --remove            # take the fixtures back out
    python scripts/load_seed_data.py --remove --dry-run  # report, delete nothing

Reads fixtures/products.json and batch-writes all records. Idempotent —
re-running overwrites with the same data (put_item replaces on key match).

`--remove` is the inverse, and exists because the fixtures stopped being the
only catalogue. Once the data team's real rows were loaded alongside them
(`ingestion/lineage_b.py`), the table held two catalogues and answered
inconsistently: head-term queries hit the fixtures while meal plans drew on the
real data — `cheapest milk near Albany` returned a Devonport fixture price
though Albany had real data. The synonym table's candidate ordering is built to
fall through to the next catalogue when a key has no rows, so removing the
fixture rows is what makes it work.

It deletes ONLY the exact `(store_key, product_key)` pairs the fixture file
names, never a scan-and-filter. Every other row is untouched by construction
rather than by a predicate someone has to get right, and re-running the loader
puts all of them back in seconds. That symmetry is the point: an operation you
can undo is one you can afford to try.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

import boto3

REGION = "ap-southeast-2"
DEFAULT_TABLE = "grocery-products-dev"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "products.json"


def load(table_name: str) -> int:
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(table_name)  # type: ignore[union-attr]

    with table.batch_writer() as batch:
        for record in data:
            item = {
                "store_key": record["store_key"],
                "product_key": record["product_key"],
                "gsi1_sk": record["gsi1_sk"],
                # Omitting this makes the row invisible to GSI2 -- a sparse
                # index is silent, so meal-plan candidates would simply miss
                # every seeded product with no error anywhere.
                "gsi2_sk": record["gsi2_sk"],
                "store": record["store"],
                "store_location": record["store_location"],
                "lat": Decimal(str(record["lat"])),
                "lon": Decimal(str(record["lon"])),
                "display_name": record["display_name"],
                "canonical_name": record["canonical_name"],
                "category": record["category"],
                "price_nzd": record["price_nzd"],
                "unit": record["unit"],
                "unit_price_nzd": record["unit_price_nzd"],
                "pack_grams": record["pack_grams"],
                "on_special": record["on_special"],
                "valid_date": record["valid_date"],
            }
            batch.put_item(Item=item)

    return len(data)


def remove(table_name: str, *, dry_run: bool = False) -> tuple[int, int]:
    """
    Delete exactly the fixture rows. Returns (present, deleted).

    Reports what it found before it acts, because "deleted 0 rows" and "the
    table was already clean" are different facts and only one of them means the
    operation did what you thought.
    """
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    keys = [{"store_key": r["store_key"], "product_key": r["product_key"]} for r in data]

    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(table_name)  # type: ignore[union-attr]

    present = 0
    for key in keys:
        if "Item" in table.get_item(Key=key):
            present += 1

    if dry_run:
        return present, 0

    with table.batch_writer() as batch:
        for key in keys:
            batch.delete_item(Key=key)
    return present, present


def verify(table_name: str) -> None:
    """Quick GSI1 query to prove the data is queryable in the expected order."""
    client = boto3.client("dynamodb", region_name=REGION)
    resp = client.query(
        TableName=table_name,
        IndexName="GSI1",
        KeyConditionExpression="product_key = :pk",
        ExpressionAttributeValues={":pk": {"S": "butter-500g"}},
        ScanIndexForward=True,
        Limit=5,
    )
    count = resp["Count"]
    print(f"  GSI1 verification: butter-500g -> {count} results, cheapest first:")
    for item in resp["Items"]:
        price_cents = item["gsi1_sk"]["S"].split("#")[0]
        location = item["store_location"]["S"]
        store = item["store"]["S"]
        print(f"    ${int(price_cents) / 100:.2f} @ {store} {location}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Load seed data into DynamoDB")
    parser.add_argument("--table", default=DEFAULT_TABLE, help="Table name")
    parser.add_argument(
        "--remove", action="store_true", help="delete the fixture rows instead of loading them"
    )
    parser.add_argument("--dry-run", action="store_true", help="with --remove: report only")
    args = parser.parse_args()

    if args.remove:
        verb = "Would remove" if args.dry_run else "Removing"
        print(f"{verb} {FIXTURES.name} rows from {args.table} ({REGION})...")
        present, deleted = remove(args.table, dry_run=args.dry_run)
        print(f"  {present} of {len(json.loads(FIXTURES.read_text(encoding='utf-8')))} present")
        print(f"  {deleted} deleted.")
        print("  Reverse with: python scripts/load_seed_data.py")
        print("Done.")
        return 0

    print(f"Loading {FIXTURES.name} into {args.table} ({REGION})...")
    count = load(args.table)
    print(f"  {count} records written.")
    verify(args.table)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
