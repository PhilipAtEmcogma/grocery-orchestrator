"""
Run the deterministic ingestion rules over a whole catalogue and report.

WHY THIS EXISTS AS A SCRIPT RATHER THAN AS A ONE-OFF. The rule that
`ingestion/handler.py` now applies row by row is only interesting in aggregate:
"what does the deterministic option catch, and what can it visibly not?" is a
question about a catalogue, not about a row. That question is the acceptance
evidence ADR 0002 gate 4 asks for, and it was being deferred behind a proposal
for an AgentCore Runtime reviewer whose stated value is "the anomalies nobody
thought to write a rule for" -- a claim that only becomes evidence once the
rules that WERE thought of are running and observably missing things.

So: run the rules, record the answer, and let the reviewer decision be made on
a measurement instead of on a belief. Either outcome is useful. Findings argue
that deterministic rules were worth switching on; no findings plus a list of
what the rules structurally cannot see argues for the reviewer far better than
an assertion does.

    python scripts/check_ingestion_anomalies.py
    python scripts/check_ingestion_anomalies.py --catalogue fixtures
    python scripts/check_ingestion_anomalies.py --json

NAMES THE CATALOGUE IT MEASURED, ON EVERY RUN. `AGENTS.md` has a rule about
this and it was written after a coverage number was quoted against the real
2,939-row catalogue while the instrument resolved against a 26-product fixture
file. A rejection count is exactly as misleading in the same way.

EXIT CODES
    0   the rules ran and found nothing
    1   the rules found rows they would refuse to write
    2   the catalogue could not be read, so nothing was measured -- distinct
        from "measured and clean", which an exit code alone cannot express
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingestion.handler import reject_implausible  # noqa: E402
from ingestion.lineage_b import transform  # noqa: E402
from ingestion.normalise import to_item  # noqa: E402
from ingestion.sources import KNOWN_RETAILERS, LineageBSource  # noqa: E402
from src.review.snapshot import IMPLAUSIBILITY_FACTOR  # noqa: E402

DATASET_DIR = ROOT / "datasets" / "data" / "dynamodb_products"
FIXTURES = ROOT / "fixtures" / "products.json"


def _dataset_items() -> tuple[list[dict], str]:
    """Every Lineage B row, transformed exactly as a real refresh would."""
    if not DATASET_DIR.exists():
        raise FileNotFoundError(
            f"{DATASET_DIR.relative_to(ROOT).as_posix()} is not checked out. "
            "Nothing was measured; that is not the same as nothing being wrong."
        )
    records: list[dict] = []
    for path in sorted(DATASET_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for entry in payload["SmartGroceryProducts"]:
            item = entry.get("PutRequest", {}).get("Item", entry)
            # The file is in DynamoDB wire form, so every value is wrapped in a
            # type tag: {"S": "Broccoli"} / {"N": "2.49"}. Unwrap to the plain
            # record shape `transform` expects, the same way
            # `tests/test_lineage_b.py::_load_dataset` does.
            records.append({k: (v.get("S") if "S" in v else v.get("N")) for k, v in item.items()})

    offers, report = transform(records, captured_at=LineageBSource.CAPTURED_AT)
    label = (
        f"datasets ({DATASET_DIR.relative_to(ROOT).as_posix()}) -- "
        f"{report.total} source rows, {len(offers)} after transform "
        f"({report.dropped_non_food} non-food dropped, "
        f"{report.collapsed_duplicates} duplicates collapsed)"
    )
    return [to_item(o) for o in offers], label


def _fixture_items() -> tuple[list[dict], str]:
    """
    The committed fixture catalogue, read as stored rows.

    Included so the script can run with no dataset checked out, and labelled so
    a clean result here is never mistaken for a clean result there: the fixtures
    are 152 rows regenerated to a fixed shape by `scripts/generate_fixtures.py`,
    so they cannot carry a surprise.
    """
    rows = json.loads(FIXTURES.read_text(encoding="utf-8"))
    items = [
        {
            "store_key": r["store_key"],
            "product_key": r["product_key"],
            "price_nzd": r["price_nzd"],
            "unit_price_nzd": r["unit_price_nzd"],
            "pack_grams": int(r["pack_grams"]),
            "display_name": r["display_name"],
        }
        for r in rows
    ]
    return items, f"fixtures ({FIXTURES.relative_to(ROOT).as_posix()}) -- {len(items)} rows"


def _derived_unit_price(item: dict) -> str:
    pack = int(item["pack_grams"])
    if pack <= 1:
        return item["price_nzd"]
    return str((Decimal(item["price_nzd"]) * 1000 / Decimal(pack)).quantize(Decimal("0.01")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", choices=("datasets", "fixtures"), default="datasets")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--limit", type=int, default=20, help="rejected rows to name")
    args = parser.parse_args()

    try:
        items, label = _dataset_items() if args.catalogue == "datasets" else _fixture_items()
    except (FileNotFoundError, KeyError) as exc:
        print(f"INCONCLUSIVE: {exc}", file=sys.stderr)
        return 2

    accepted, rejected = reject_implausible(items)

    findings = [
        {
            "store_key": r["store_key"],
            "product_key": r["product_key"],
            "display_name": r.get("display_name", ""),
            "price_nzd": str(r["price_nzd"]),
            "unit_price_nzd": str(r["unit_price_nzd"]),
            "derived_unit_price_nzd": _derived_unit_price(r),
            "pack_grams": int(r["pack_grams"]),
        }
        for r in rejected
    ]

    if args.json:
        print(json.dumps({"catalogue": label, "rows": len(items), "findings": findings}, indent=2))
        return 1 if findings else 0

    print(f"catalogue: {label}")
    print(f"retailers: {', '.join(sorted(KNOWN_RETAILERS))}")
    print(f"rule:      implausible_unit_price, factor {IMPLAUSIBILITY_FACTOR}x")
    print()
    print(f"  rows checked  {len(items)}")
    print(f"  accepted      {len(accepted)}")
    print(f"  REJECTED      {len(rejected)}")
    print()

    for finding in findings[: args.limit]:
        print(
            f"  {finding['store_key']}/{finding['product_key']}\n"
            f"      {finding['display_name']}\n"
            f"      price {finding['price_nzd']}  stored unit "
            f"{finding['unit_price_nzd']}  derived {finding['derived_unit_price_nzd']}  "
            f"pack_grams {finding['pack_grams']}"
        )
    if len(findings) > args.limit:
        print(f"  ... and {len(findings) - args.limit} more")

    print()
    print("What this rule CANNOT see, stated so the number is not over-read:")
    print("  - a price that is simply wrong but internally consistent -- $12.99")
    print("    for a $1.29 item with a matching unit price passes every check here")
    print("  - a product whose pack_grams is wrong in the SOURCE, since the unit")
    print("    price is then correctly derived from a wrong weight")
    print("  - a mis-categorised product (the vegan-safety class), which")
    print("    ingestion/lineage_b.py handles separately and fail-closed")
    print("  - a stale capture date, which src/retrieval/filters.py owns")
    print("  - anything requiring a BASELINE: 'this price doubled overnight'")
    print("    needs the price-history table, which does not exist yet")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
