"""
Generates the seed price dataset.

Deliberately produces INCONSISTENT product naming across stores, because
name normalisation is the single most likely source of wrong answers in this
system. If the fixtures were tidy, we would not discover that until we hit
real scraped data in week 3.

Run:  python scripts/generate_fixtures.py
Out:  fixtures/products.json
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

# --------------------------------------------------------------- stores

STORES = [
    ("paknsave", "Sylvia Park", -36.8912, 174.8437),
    ("paknsave", "Mangere", -36.9765, 174.7823),
    ("woolworths", "Mt Wellington", -36.9048, 174.8398),
    ("woolworths", "Ponsonby", -36.8545, 174.7442),
    ("new_world", "Newmarket", -36.8698, 174.7772),
    ("new_world", "Devonport", -36.8296, 174.7954),
]

# Rough relative pricing: Pak'nSave cheapest, New World dearest.
STORE_FACTOR = {"paknsave": 1.00, "woolworths": 1.12, "new_world": 1.18}

# --------------------------------------------------------------- catalogue
# (product_key, canonical, category, base_price_at_paknsave, unit, pack_grams)
# Naming variants per store are added below to force normalisation.

CATALOGUE = [
    ("butter-500g", "Butter 500g", "dairy", 3.49, "500g", 500),
    ("milk-2l", "Milk 2L", "dairy", 4.19, "2L", 2000),
    ("cheese-tasty-1kg", "Tasty Cheese 1kg", "dairy", 11.99, "1kg", 1000),
    ("yoghurt-plain-1kg", "Plain Yoghurt 1kg", "dairy", 4.99, "1kg", 1000),
    ("eggs-size7-dozen", "Size 7 Eggs Dozen", "chilled", 8.49, "12ea", 12),
    ("beef-mince-1kg", "Beef Mince 1kg", "meat", 11.99, "1kg", 1000),
    ("chicken-thigh-1kg", "Chicken Thighs 1kg", "meat", 9.99, "1kg", 1000),
    ("pork-sausages-500g", "Pork Sausages 500g", "meat", 5.49, "500g", 500),
    ("tuna-canned-185g", "Canned Tuna 185g", "seafood", 2.29, "185g", 185),
    ("salmon-fillet-300g", "Salmon Fillet 300g", "seafood", 14.99, "300g", 300),
    ("pasta-spirals-500g", "Pasta Spirals 500g", "pantry", 1.29, "500g", 500),
    ("rice-longgrain-1kg", "Long Grain Rice 1kg", "pantry", 2.79, "1kg", 1000),
    ("tomatoes-canned-400g", "Chopped Tomatoes 400g", "pantry", 1.10, "400g", 400),
    ("beans-baked-420g", "Baked Beans 420g", "pantry", 1.59, "420g", 420),
    ("lentils-dried-500g", "Dried Red Lentils 500g", "pantry", 3.29, "500g", 500),
    ("flour-plain-1-5kg", "Plain Flour 1.5kg", "pantry", 2.49, "1.5kg", 1500),
    ("oats-rolled-1kg", "Rolled Oats 1kg", "pantry", 3.19, "1kg", 1000),
    ("oil-canola-750ml", "Canola Oil 750ml", "pantry", 5.49, "750ml", 750),
    ("bread-white-700g", "White Bread 700g", "bakery", 2.19, "700g", 700),
    ("onions-brown-1-5kg", "Brown Onions 1.5kg", "produce", 2.50, "1.5kg", 1500),
    ("potatoes-washed-2kg", "Washed Potatoes 2kg", "produce", 4.49, "2kg", 2000),
    ("carrots-1kg", "Carrots 1kg", "produce", 1.99, "1kg", 1000),
    ("broccoli-each", "Broccoli Head", "produce", 2.49, "each", 1),
    ("bananas-1kg", "Bananas 1kg", "produce", 3.29, "1kg", 1000),
    ("frozen-mixed-veg-1kg", "Mixed Frozen Veg 1kg", "frozen", 3.49, "1kg", 1000),
    ("frozen-peas-1kg", "Frozen Peas 1kg", "frozen", 2.99, "1kg", 1000),
]

# How each chain writes the same product. This is the messiness that matters.
NAME_STYLE = {
    "paknsave": lambda c, u: f"Pams {c}",
    "woolworths": lambda c, u: f"{c.rsplit(' ', 1)[0]}, {u}".strip(),
    "new_world": lambda c, u: f"Value {c.upper()}",
}

# Items on special this week, by store chain.
SPECIALS = {
    "paknsave": {"beef-mince-1kg", "butter-500g", "pasta-spirals-500g"},
    "woolworths": {"onions-brown-1-5kg", "chicken-thigh-1kg", "frozen-peas-1kg"},
    "new_world": {"cheese-tasty-1kg", "bread-white-700g"},
}
SPECIAL_DISCOUNT = Decimal("0.85")

# Deliberate gaps — not every store stocks everything. Forces the no_data path.
NOT_STOCKED = {
    ("new_world", "Devonport"): {"lentils-dried-500g", "frozen-peas-1kg"},
    ("paknsave", "Mangere"): {"salmon-fillet-300g"},
    ("woolworths", "Ponsonby"): {"oats-rolled-1kg"},
}

VALID_DATE = "2026-07-31"


def money(value: Decimal) -> str:
    """Two-decimal string. Money never travels as a float."""
    return str(value.quantize(Decimal("0.01")))


def build() -> list[dict]:
    """Generate one price record per (store location x catalogue item), skipping
    deliberately-unstocked combinations, and return them as plain dicts ready for JSON."""
    records: list[dict] = []

    # Outer loop: every store location. Inner loop: every catalogue item.
    for chain, location, lat, lon in STORES:
        factor = Decimal(str(STORE_FACTOR[chain]))
        skip = NOT_STOCKED.get((chain, location), set())

        for key, canonical, category, base, unit, grams in CATALOGUE:
            if key in skip:
                continue

            # Scale the base (Pak'nSave) price by this chain's relative
            # pricing factor, then apply the special discount if applicable.
            price = Decimal(str(base)) * factor
            on_special = key in SPECIALS.get(chain, set())
            if on_special:
                price *= SPECIAL_DISCOUNT

            price = price.quantize(Decimal("0.01"))
            loc_slug = location.lower().replace(" ", "-")

            # unit price per kg / L / each, for honest comparison
            if grams > 1:
                unit_price = (price / Decimal(grams) * 1000).quantize(Decimal("0.01"))
            else:
                unit_price = price

            # Each chain renders the same canonical product name differently
            # (see NAME_STYLE), which is the naming inconsistency the
            # retrieval layer's normaliser must cope with.
            display_name = NAME_STYLE[chain](canonical, unit)

            records.append(
                {
                    # base table keys
                    "store_key": f"{chain}#{loc_slug}",
                    "product_key": key,
                    # GSI1: partition by product, sort by zero-padded price so the
                    # cheapest option is the FIRST result of a single query.
                    "gsi1_pk": key,
                    "gsi1_sk": f"{int(price * 100):09d}#{chain}#{loc_slug}",
                    # GSI2: partition by CATEGORY, sort by zero-padded price, so
                    # "cheapest things in this category" is a query rather than a
                    # full-table Scan. The product key is in the sort key because
                    # the caller wants distinct products, not one cheap product
                    # at every store. Partition key is `category` below.
                    "gsi2_sk": f"{int(price * 100):09d}#{key}#{chain}#{loc_slug}",
                    # attributes
                    "store": chain,
                    "store_location": location,
                    "lat": lat,
                    "lon": lon,
                    "display_name": display_name,
                    "canonical_name": canonical,
                    "category": category,
                    "price_nzd": money(price),
                    "unit": unit,
                    "unit_price_nzd": money(unit_price),
                    "pack_grams": grams,
                    "on_special": on_special,
                    "valid_date": VALID_DATE,
                }
            )

    return records


if __name__ == "__main__":
    records = build()
    out = Path(__file__).resolve().parent.parent / "fixtures" / "products.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(records, indent=2), encoding="utf-8")

    # Summary printout so a human running the script can sanity-check the output.
    stores = {(r["store"], r["store_location"]) for r in records}
    print(
        f"{len(records)} records, {len(stores)} stores, "
        f"{len({r['product_key'] for r in records})} distinct products"
    )
    print(f"wrote {out}")
