"""
Load seed fixture data into the DynamoDB products table.

Usage:
    python scripts/load_seed_data.py
    python scripts/load_seed_data.py --table grocery-products-prod
    python scripts/load_seed_data.py --force             # load even over the real catalogue
    python scripts/load_seed_data.py --remove            # take the fixtures back out
    python scripts/load_seed_data.py --remove --dry-run  # report, delete nothing

Reads fixtures/products.json and batch-writes all records. Idempotent —
re-running overwrites with the same data (put_item replaces on key match).

GUARDED SINCE 2026-09-01. The default action loads fixtures, and a plain run
used to silently re-add them on top of the real catalogue — where they SHADOW
the real prices (see `_REAL_ONLY_STORE_KEYS` and
docs/OPEN-REVIEW-near-filter-drift.md). `load()` now refuses when the real
catalogue is already present, unless `--force` is passed. This does not touch
the live table by itself; it stops the loader being the thing that quietly
undoes a fixture removal.

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

#: Store keys that exist ONLY in the data team's real Lineage B catalogue and
#: never in the fixtures. Their presence in the table is a reliable, cheap
#: signal that the real catalogue is loaded — a signal that needs one GetItem
#: rather than a full Scan (which the orchestrator role deliberately cannot do).
#:
#: WHY A GUARD IS NEEDED. `load()` batch-writes the fixtures unconditionally, and
#: its default (no flag) is to LOAD. So a plain `python scripts/load_seed_data.py`
#: run during demo prep, a redeploy, or a smoke test silently re-adds all 152
#: fixture rows on top of the real catalogue. The fixtures then SHADOW the real
#: data: "milk" resolves to the fixture-only `milk-2l` before reaching the real
#: `standard-milk-2l`, so `cheapest milk near Albany` serves a fabricated
#: Devonport price instead of the real Albany one. This happened — it is the
#: 2026-09-01 finding in docs/OPEN-REVIEW-near-filter-drift.md, and it undid the
#: 2026-08-30 fixture removal (ARCHITECTURE.md §3j) with no signal at all.
#:
#: These keys are asserted disjoint-from-fixtures and present-in-Lineage-B by
#: tests/test_ingestion.py, so a future catalogue change that invalidated them
#: fails the build rather than silently disarming the guard.
_REAL_ONLY_STORE_KEYS = ("paknsave#albany", "new_world#albany")


def real_catalogue_present(table_name: str) -> str | None:
    """
    A real-only store key that is present in the table, or None.

    Cheap by design: one GetItem per probe key against the base table, stopping
    at the first hit. No Scan, so it runs under the orchestrator's least-privilege
    role. Returns the key it found so the caller can name it in the refusal.

    A probe needs BOTH keys of the base table (partition `store_key`, sort
    `product_key`). We do not know a real product_key without reading the
    catalogue, so this queries by partition key alone via the base table's
    Query and takes Count — one row is enough to know the store is stocked.
    """
    from boto3.dynamodb.conditions import Key

    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(table_name)  # type: ignore[union-attr]
    for store_key in _REAL_ONLY_STORE_KEYS:
        resp = table.query(
            KeyConditionExpression=Key("store_key").eq(store_key),
            Select="COUNT",
            Limit=1,
        )
        if resp.get("Count", 0) > 0:
            return store_key
    return None


def load(table_name: str, *, force: bool = False) -> int:
    # GUARD: refuse to add fixtures on top of the real catalogue unless forced.
    # The fixtures shadow the real data (see _REAL_ONLY_STORE_KEYS), so a silent
    # re-add is a correctness regression, not a harmless duplicate.
    if not force:
        found = real_catalogue_present(table_name)
        if found is not None:
            raise SystemExit(
                f"REFUSING to load fixtures into {table_name}: it already holds the real "
                f"catalogue (found rows at {found!r}, which exists only in Lineage B).\n"
                "Loading the fixtures now would SHADOW the real prices — "
                "'cheapest milk near Albany' would serve a fixture Devonport price "
                "instead of the real Albany one. See docs/OPEN-REVIEW-near-filter-drift.md.\n"
                "If you really mean to load fixtures alongside real data, pass --force. "
                "To refresh the real catalogue instead, use the ingestion path "
                "(PRICE_SOURCE=lineage_b), not this loader."
            )

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
    parser.add_argument(
        "--force",
        action="store_true",
        help="load fixtures even if the real catalogue is already present (they will shadow it)",
    )
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
    if args.force:
        print("  --force: loading even if the real catalogue is present (fixtures will shadow it).")
    count = load(args.table, force=args.force)
    print(f"  {count} records written.")
    verify(args.table)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
