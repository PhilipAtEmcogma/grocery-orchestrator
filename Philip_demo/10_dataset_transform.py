r"""
DEMO 10 - Lineage B -> Lineage A: making a collected catalogue servable
=======================================================================

HOW TO RUN
----------
    python Philip_demo/10_dataset_transform.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/10_dataset_transform.py

MODES
-----
    local  (default and only)  reads datasets/data/dynamodb_products/, which is
                               committed. No AWS, no credentials, no network.

WHAT THIS DEMONSTRATES
----------------------
  1. The nine fields Lineage B has, and the seven Lineage A needs
  2. Category mapping, and why the source category is NOT a safety control
  3. The name override - fail-closed, and only ever MORE restrictive
  4. Size parsing, and the 'sold each' sentinel
  5. Store parsing, and the coordinate lookup that must not have a default
  6. Duplicate collapse, found by loading rather than by reading
  7. Conservation: kept + dropped + collapsed == input, asserted

THE FINDING THIS FILE IS REALLY ABOUT
-------------------------------------
Reading the actual data before trusting it found two upstream categories that
would have broken Invariant 3 (dietary exclusions are safety-critical):

  * `Frozen Foods` contains frozen chicken, lamb chops and prawns. A straight
    category map puts frozen chicken in a vegetarian meal plan.
  * `Breakfast Cereals, Oats & Spreads` contains shaved honey baked ham and
    beef sausages - almost certainly a keyword match on "honey" upstream.
    Mapped to `pantry`, a vegan gets ham.

So classification is category-mapped and then NAME-OVERRIDDEN, fail-closed. An
over-match costs a vegetarian a product they could have eaten; an under-match
serves them chicken. Those are not symmetric.

ARCHITECTURE
------------
    datasets/data/dynamodb_products/*.json   (the data team's collection)
        v
    LineageBSource.fetch()
        v
    ingestion.lineage_b.transform()      classify -> parse -> dedupe -> report
        v
    RawOffer                             the same type FixtureSource returns
        v
    ingestion.normalise.to_item()        (demo 9 continues from here)
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

from _demo_support import (
    LOCAL,
    ModeUnavailable,
    heading,
    mode_banner,
    note,
    resolve_mode,
    section,
    step,
)

from ingestion.lineage_b import (
    CATEGORY_MAP,
    classify,
    derive_product_key,
    is_non_food,
    parse_size,
    parse_store,
    store_coordinates,
    transform,
)
from ingestion.sources import LINEAGE_B_DIR, LineageBSource

try:
    mode = resolve_mode(supports=(LOCAL,))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

heading("DEMO 10 - Lineage B -> Lineage A: making a collected catalogue servable")
mode_banner(
    mode,
    requires="nothing - the dataset is committed under datasets/",
    mocked="nothing. This is the real transform over the real collected data.",
)


def load_records() -> list[dict]:
    """The raw DynamoDB-export envelope, flattened to plain dicts."""
    records: list[dict] = []
    for path in sorted(Path(LINEAGE_B_DIR).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for entry in payload["SmartGroceryProducts"]:
            item = entry.get("PutRequest", {}).get("Item", entry)
            records.append({k: (v.get("S") if "S" in v else v.get("N")) for k, v in item.items()})
    return records


records = load_records()

# ------------------------------------------------------------- what is there
section("1. What a Lineage B record carries, and what is missing")
sample = records[0]
print(f"  {len(records)} records in {Path(LINEAGE_B_DIR).name}/. One of them:\n")
for key, value in sample.items():
    print(f"    {key:<14} {value!r}")
print("\n  Seven things Lineage A needs are absent from every one of them:")
for field, how in (
    ("product_key", "derived from name + size"),
    ("pack_grams", "parsed from `size`"),
    ("unit", "parsed from `size`"),
    ("canonical_name", "the product name, brand stripped"),
    ("on_special", "not observable -> False, honestly"),
    ("lat / lon", "looked up in config/store-locations.json"),
    ("captured_at", "REQUIRED from the caller - the data team's own claim"),
):
    print(f"    {field:<16} {how}")
note("")
note("Two of the seven cannot be derived from the record at all, and neither")
note("has a default. `captured_at` is the data team's stated collection date,")
note("passed in explicitly so nobody can later mistake it for something this")
note("code observed.")

# ------------------------------------------------------- categories are data
section("2. The source category is NOT a safety control")
source_categories = collections.Counter(r["category"] for r in records)
print(
    f"  {len(source_categories)} upstream categories -> "
    f"{len(set(CATEGORY_MAP.values()))} serving categories.\n"
)
print(f"  {'upstream':<40} {'serving':<12} rows")
print(f"  {'-' * 40} {'-' * 12} ----")
for name, count in source_categories.most_common(8):
    print(f"  {name[:40]:<40} {CATEGORY_MAP.get(name, '(unmapped)'):<12} {count}")

print("\n  Two of those categories lie about what is in them:\n")
for product, upstream in (
    ("Frozen Whole Chicken", "Frozen Foods"),
    ("Raw Frozen Prawns Cutlet", "Frozen Foods"),
    ("Shaved Honey Baked Ham", "Breakfast Cereals, Oats & Spreads"),
    ("Pams Rolled Oats", "Breakfast Cereals, Oats & Spreads"),
):
    mapped = CATEGORY_MAP.get(upstream, "?")
    final, overridden = classify(product, upstream)
    flag = "OVERRIDDEN" if overridden else ""
    print(f"    {product:<28} {mapped:<10} -> {final:<10} {flag}")
note("")
note("dietary.py maps `vegetarian` to {meat, seafood} and does not exclude")
note("`frozen`, because in the fixture catalogue `frozen` holds vegetables.")
note("A straight category map puts frozen chicken in a vegetarian meal plan.")

# ------------------------------------------------------------- fail closed
section("3. The override may only ever RESTRICT")
print("  classify() is allowed to move a product to a more restricted")
print("  category and never to a less restricted one:\n")
print(f"  {'product':<34} {'upstream says':<14} {'we serve as':<12} moved?")
print(f"  {'-' * 34} {'-' * 14} {'-' * 12} ------")
for product, upstream in (
    ("Beef Manuka Honey Sausages", "Breakfast Cereals, Oats & Spreads"),
    ("Fresh Broccoli", "Fresh Vegetables"),
    ("Frozen BBQ Lamb Chops", "Frozen Foods"),
    ("Pams Value Standard Milk", "Fresh Milk & Plant Milk"),
):
    final, overridden = classify(product, upstream)
    print(
        f"  {product:<34} {CATEGORY_MAP.get(upstream, '?'):<14} {final:<12} "
        f"{'yes' if overridden else 'no'}"
    )
note("")
note("An over-match costs a vegetarian a product they could have eaten. An")
note("under-match serves them chicken. The asymmetry is the whole design, and")
note("it is the same reasoning resolve_product_key uses to refuse substring")
note("matching - pointed the other way. There, a loose match INVENTS a fact")
note("and is forbidden. Here, a loose match WITHHOLDS a product and is required.")

print("\n  Non-food is dropped outright rather than categorised:")
for name in ("Purina Cat Chow", "Fresh Broccoli", "Chicken Breast Fillets"):
    print(f"    {name:<28} is_non_food={is_non_food(name)}")

# ------------------------------------------------------------ derived fields
section("4. Size, key and store, each derived and each fallible")
print(f"  {'size string':<16} {'pack_grams':>11}  unit")
print(f"  {'-' * 16} {'-' * 11}  ----")
for size in ("500g", "1kg", "2L", "each", "6 pack", ""):
    grams, unit = parse_size(size)
    print(f"  {size!r:<16} {grams:>11}  {unit!r}")
note("pack_grams == 1 is the 'sold each' sentinel demo 9 shows the cost of")
note("getting wrong. It is set here, at the parse, not guessed later.")

print("\n  product_key ignores brand deliberately, so the same product")
print("  compares across chains:")
for name, size in (("Mixed Berries", "500g"), ("Standard Milk", "2L")):
    print(f"    {name!r} + {size!r}  ->  {derive_product_key(name, size)!r}")

print("\n  Store name -> (chain, suburb) -> coordinates:")
for store_name in ("PAK'nSAVE Albany", "New World Remuera", "PAK'nSAVE Lincoln Road"):
    chain, location = parse_store(store_name)
    lat, lon = store_coordinates(location)
    print(f"    {store_name:<24} {chain:<12} {location:<12} ({lat}, {lon})")

try:
    store_coordinates("Wellington Central")
    print("\n  ...an unknown store returned coordinates, which would be wrong.")
except ValueError as exc:
    print(f"\n  An unknown store: ValueError: {str(exc)[:100]}...")
note("")
note("This used to return 0.0/0.0 with a comment calling it fail-closed.")
note("0.0/0.0 is a real position in the Atlantic, so the radius filter")
note("computed a genuine ~18,000km distance, excluded every record, and the")
note("graph told a shopper 'I don't have price data near you' about the")
note("supermarket down the road. A wrong value that produces plausible")
note("behaviour is worse than a missing one - nothing distinguishes it from")
note("the value being right.")

# ------------------------------------------------------------ the transform
section("5. The whole transform, over the whole collected catalogue")
step(1, f"reading {len(records)} raw records")
step(2, "dropping non-food")
step(3, "classifying, with the name override")
step(4, "parsing size, store and coordinates")
step(5, "collapsing duplicates to the cheapest per (store, product)")

offers, report = transform(records, captured_at=LineageBSource.CAPTURED_AT)
print()
print(f"    total              {report.total}")
print(f"    dropped_non_food   {report.dropped_non_food}")
print(f"    collapsed_dupes    {report.collapsed_duplicates}")
print(f"    overridden         {report.overridden}   <- rows whose source")
print("                              category would have mis-stated a dietary fact")
print(f"    unmapped_cats      {report.unmapped_categories or '()'}")
print(f"    kept               {report.kept}")

conserved = report.kept + report.dropped_non_food + report.collapsed_duplicates
print(
    f"\n  Conservation: {report.kept} + {report.dropped_non_food} + "
    f"{report.collapsed_duplicates} = {conserved}, input {report.total}"
)
# An explicit raise rather than `assert`: a bare assert disappears under
# `python -O`, and a conservation check that can be optimised away is not a
# check. Same reason the contract's own invariants raise.
if conserved != report.total:
    raise RuntimeError(f"conservation failed: {conserved} accounted for, {report.total} input")
print("  Conservation holds. A row that vanishes unaccounted for is a product")
print("  nobody can be shown and nobody is told about.")
note("")
note("`overridden` is the count that matters. A sharp change in it between")
note("runs means the upstream categorisation moved, and the safety table")
note("should be re-read before the load is trusted.")

# --------------------------------------------------------------- duplicates
section("6. The duplicate collision, found by loading rather than by reading")
print("  BatchWriteItem refused the first load outright:")
print("    'Provided list of item keys contains duplicates'\n")
print("  The base table key is (store_key, product_key) and one store stocks")
print("  two BRANDS of the same product at the same size:\n")
for name in ("Pams Mixed Berries", "Frozen Harvest Mixed Berries"):
    print(f"    {name:<32} -> product_key {derive_product_key('Mixed Berries', '500g')!r}")
print("\n  derive_product_key ignores brand so the same product compares")
print("  across Pak'nSave and New World. The cost is that it also collapses")
print("  two brands within one store.\n")
print(f"  {report.collapsed_duplicates} rows collapsed in this catalogue.")
note("")
note("Resolved by keeping the CHEAPEST per (store, product), which is the")
note("answer the product already gives: the dearer brand of an identical")
note("product at the same store is never the answer to 'what is the cheapest")
note("X'. Ties break on display name, so a re-run cannot report a change on a")
note("day nothing changed.")
note("")
note("Nothing offline had exercised it. The fixtures carry exactly one")
note("product per key by construction - a shape the real catalogue does not have.")

# ------------------------------------------------------------------- output
section("7. What comes out is the same type the fixtures produce")
per_store = collections.Counter(f"{o.store}/{o.store_location}" for o in offers)
per_category = collections.Counter(o.category for o in offers)
print(f"  {len(offers)} offers across {len(per_store)} stores.\n")
print("  by serving category:")
for category, count in per_category.most_common():
    print(f"    {category:<12} {count}")
print("\n  by store:")
for store, count in sorted(per_store.items()):
    print(f"    {store:<28} {count}")

example = next(o for o in offers if o.category == "meat")
print("\n  One offer, ready for ingestion.normalise.to_item():")
for field in (
    "product_key",
    "store",
    "store_location",
    "display_name",
    "category",
    "price_nzd",
    "pack_grams",
    "on_special",
    "captured_at",
):
    print(f"    {field:<16} {getattr(example, field)!r}")
note("")
note(f"captured_at is {LineageBSource.CAPTURED_AT!r} - the data team's stated")
note("collection date from datasets/DATA_SCHEMA.md, recorded as THEIR claim.")
note("Lineage B records carry no date at all, and RawOffer refuses an undated")
note("offer, so one had to be supplied rather than invented downstream.")

print("\nDone.")
