"""
RawOffer -> DynamoDB item.

Two things happen here that are easy to get wrong.

MONEY IS A STRING IN STORAGE. DynamoDB's numeric type round-trips through
float in most client paths, and a float cent is a wrong cent. Prices are
`Decimal` in Python and strings on the wire and at rest -- the same rule the
orchestrator follows, applied at the write side so the read side never has to
repair anything.

THE SORT KEY CARRIES THE PRICE. GSI1 answers "cheapest X near me" in one query
by sorting on a zero-padded cent count embedded in the sort key, so the
cheapest option is literally the first item returned. Zero-padding is what
makes lexicographic order agree with numeric order: "297" < "391" is true by
luck at equal width and false the moment widths differ ("1000" < "297"). Nine
digits holds any grocery price in cents with room to spare.

The cost of this design is that a price change rewrites the GSI entry, which
is why DYNAMODB-SCHEMA.md calls a daily full refresh acceptable and a
continuous crawl not.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ingestion.sources import RawOffer

PRICE_KEY_WIDTH = 9
GRAMS_PER_KG = 1000

# pack_grams of 1 means 'sold each', not 'weighs one gram'. See unit_price().
UNIT_PRICED_GRAMS = 1


def _cents(price: Decimal) -> int:
    return int((price * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def store_key(store: str, store_location: str) -> str:
    """`paknsave#sylvia-park`. Location is slugged so the key is stable."""
    slug = store_location.strip().lower().replace(" ", "-")
    return f"{store}#{slug}"


def gsi1_sk(price: Decimal, store: str, store_location: str) -> str:
    """Zero-padded cents, then the store key, so ties resolve deterministically."""
    return (
        f"{_cents(price):0{PRICE_KEY_WIDTH}d}#{store_key(store, store_location)}"
    )


def unit_price(price: Decimal, pack_grams: int) -> Decimal:
    """
    Price per kilogram, to the cent -- or the price itself for unit-priced goods.

    Computed here rather than taken from the source: a retailer's own unit
    price is a marketing figure that may use a different basis, and a
    comparison built on two different bases is not a comparison.

    `pack_grams == 1` is the sentinel for "sold each, not by weight" --
    broccoli, a lettuce, a dozen eggs. Dividing by it yields a per-kilogram
    figure a thousand times the shelf price, and `unit_price_nzd` is read
    straight into the Citation the shopper sees. This mirrors
    scripts/generate_fixtures.py's `if grams > 1` guard; the first version of
    this function omitted it and wrote unit_price_nzd "2490.00" against a
    $2.49 broccoli into the live table.

    Rounding is the generator's default (ROUND_HALF_EVEN via bare `quantize`),
    NOT ROUND_HALF_UP. They disagree on exact halves -- 2.245 goes to 2.24 one
    way and 2.25 the other -- and a mismatch means a refresh rewrites unit
    prices that did not change, which would make `refresh()`'s idempotency
    claim false for four seeded records.
    """
    if pack_grams <= 0:
        raise ValueError(f"pack_grams must be positive, got {pack_grams}")
    if pack_grams <= UNIT_PRICED_GRAMS:
        return price
    per_kg = price * Decimal(GRAMS_PER_KG) / Decimal(pack_grams)
    return per_kg.quantize(Decimal("0.01"))


def to_item(offer: RawOffer) -> dict:
    """
    The DynamoDB item for one offer.

    Every field here is a fact the shopper's question needs. Nothing carries
    marketing copy, imagery, or anything resembling personal information
    (ACQUISITION-RISK.md 8 condition 7, Req 8.7).
    """
    if not offer.captured_at:
        raise ValueError(f"{offer.product_key} has no capture date")

    return {
        "store_key": store_key(offer.store, offer.store_location),
        "product_key": offer.product_key,
        "gsi1_pk": offer.product_key,
        "gsi1_sk": gsi1_sk(offer.price_nzd, offer.store, offer.store_location),
        "store": offer.store,
        "store_location": offer.store_location,
        "lat": Decimal(str(offer.lat)),
        "lon": Decimal(str(offer.lon)),
        "display_name": offer.display_name,
        "canonical_name": offer.canonical_name,
        "category": offer.category,
        "price_nzd": str(offer.price_nzd),
        "unit": offer.unit,
        "unit_price_nzd": str(unit_price(offer.price_nzd, offer.pack_grams)),
        "pack_grams": offer.pack_grams,
        "on_special": offer.on_special,
        # Named valid_date for continuity with the seeded records the
        # orchestrator already reads and surfaces as the citation's date.
        "valid_date": offer.captured_at,
    }
