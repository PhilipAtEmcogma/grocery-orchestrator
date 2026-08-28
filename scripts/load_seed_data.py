"""
Load seed fixture data into the DynamoDB products table.

Usage:
    python scripts/load_seed_data.py
    python scripts/load_seed_data.py --table grocery-products-prod

Reads fixtures/products.json and batch-writes all records. Idempotent —
re-running overwrites with the same data (put_item replaces on key match).
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
    args = parser.parse_args()

    print(f"Loading {FIXTURES.name} into {args.table} ({REGION})...")
    count = load(args.table)
    print(f"  {count} records written.")
    verify(args.table)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
