"""
Lineage B -> Lineage A. The data team's collected catalogue, made servable.

`datasets/DATA_SCHEMA.md` describes the dataset the data sub-team collected:
3,000 offers across ten Auckland stores, live in DynamoDB as
`smart-grocery-products-dev`. `infra/docs/08-OPEN-DECISIONS.md` section 1 records
the decision that it is an INPUT to ingestion, never a serving table -- the
orchestrator reads Lineage A (`grocery-products-dev`), and this module is the
transform between them.

A Lineage B record carries nine fields:

    primary_key store_id store_name product_id product_name brand size price category

Seven things Lineage A needs are absent: `product_key`, `pack_grams`, `unit`,
`canonical_name`, `on_special`, `lat`/`lon`, and a capture date. Each is derived
or supplied below, and each derivation is a judgement recorded here rather than
buried in a comprehension. Two of the seven cannot be derived from the record at
all and are looked up or required from the caller: coordinates come from
`config/store-locations.json`, and `captured_at` is the data team's own stated
collection date. Neither has a default -- see `store_coordinates()` for what a
plausible-looking default cost the first time.

THE SOURCE `category` FIELD IS NOT A SAFETY CONTROL, AND MUST NOT BE TREATED AS
ONE. Reading the actual data before trusting it found two categories that would
have broken Invariant 3 (dietary exclusions are safety-critical) had the field
been mapped straight through:

* `Frozen Foods` contains `Frozen Whole Chicken`, `Frozen BBQ Lamb Chops` and
  `Raw Frozen Prawns Cutlet`. `dietary.py` maps vegetarian to {meat, seafood};
  it does not exclude `frozen`, because in the fixture catalogue `frozen` holds
  vegetables. A straight category map puts frozen chicken in a vegetarian meal
  plan.
* `Breakfast Cereals, Oats & Spreads` contains `Shaved Honey Baked Ham`,
  `Beef Manuka Honey & Hickory Sausages` and `Manuka Honey & Rosemary Chicken` --
  almost certainly a keyword match on "honey" upstream. Mapped to `pantry`, a
  vegan gets ham.

So classification here is category-mapped and then NAME-OVERRIDDEN, fail-closed:
`classify()` may only ever move a product to a MORE restricted category, never a
less restricted one. An over-match costs a vegetarian a product they could have
eaten; an under-match serves them chicken. Those are not symmetric, and the
asymmetry is the whole design.

This is the same reasoning `resolve_product_key` uses to refuse substring
matching, pointed the other way. There, a loose match INVENTS a fact and is
forbidden. Here, a loose match WITHHOLDS a product and is required.
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from ingestion.sources import RawOffer

# --------------------------------------------------------------------------
# Categories
# --------------------------------------------------------------------------

# Lineage B's 17 categories onto the eight the fixture catalogue uses, which is
# the vocabulary `src/graph/dietary.py` maps exclusion terms against. Mapping
# here rather than widening dietary.py keeps ONE category vocabulary in the
# serving table: the safety table stays exactly as reviewed, and the messy
# upstream vocabulary stops at the ingestion boundary.
CATEGORY_MAP: dict[str, str] = {
    "Fresh Vegetables": "produce",
    "Fresh Fruit": "produce",
    "Cheese, Butter & Yoghurt": "dairy",
    # Plant milks live in this category too, and are mapped to `dairy` with
    # them. That is deliberate and it is the fail-closed direction: a vegan
    # loses oat milk (annoying, safe) rather than being served cow's milk
    # (unsafe). Per-product tagging -- the legacy 11.7 item -- is what fixes it
    # properly; a second category would need dietary.py to learn about it, and
    # widening a safety table to improve a suggestion is the wrong trade.
    "Fresh Milk & Plant Milk": "dairy",
    "Bread, Wraps & Bakery": "bakery",
    "Rice, Pasta & Noodles": "pantry",
    "Beef & Lamb": "meat",
    "Chicken & Poultry": "meat",
    "Pork & Ham": "meat",
    "Fresh Seafood": "seafood",
    # The fixture keeps eggs in `chilled`, and dietary.py maps both "no eggs"
    # and "vegan" onto that category. Anything else here breaks both.
    "Eggs": "chilled",
    "Breakfast Cereals, Oats & Spreads": "pantry",
    "Flour, Sugar & Baking": "pantry",
    "Cooking Oils & Vinegars": "pantry",
    "Canned Goods": "pantry",
    "Pantry Staples": "pantry",
    "Frozen Foods": "frozen",
}

# Categories that already restrict at least as much as any override could, so
# the override is skipped rather than applied. `meat` and `seafood` are the two
# dietary.py actually excludes for the vegetarian/vegan/pescatarian terms.
_ALREADY_RESTRICTED = frozenset({"meat", "seafood"})

# Word-boundary matched against the lowercased product name. These force a
# product into `meat` regardless of where the source filed it.
MEAT_TERMS: frozenset[str] = frozenset(
    {
        "bacon",
        "beef",
        "brisket",
        "chicken",
        "chorizo",
        "drumstick",
        "duck",
        "gammon",
        "ham",
        "kransky",
        "lamb",
        "meat",
        "meatball",
        "mince",
        "mutton",
        "nibbles",
        "pancetta",
        "pastrami",
        "pepperoni",
        "pork",
        "poultry",
        "prosciutto",
        "rasher",
        "ribs",
        "salami",
        "sausage",
        "schnitzel",
        "steak",
        "turkey",
        "veal",
        "venison",
        "wings",
    }
)

# Same, forcing `seafood`. Checked before meat so "seafood flavour" wins over a
# stray meat word in the same name.
SEAFOOD_TERMS: frozenset[str] = frozenset(
    {
        "anchovy",
        "calamari",
        "clam",
        "cod",
        "crab",
        "fish",
        "hoki",
        "kahawai",
        "lobster",
        "mussel",
        "octopus",
        "oyster",
        "prawn",
        "salmon",
        "sardine",
        "scallop",
        "seafood",
        "shellfish",
        "shrimp",
        "snapper",
        "squid",
        "terakihi",
        "trevally",
        "tuna",
        "whitefish",
    }
)

# Not food for people. The dataset carries pet food, which a meal planner must
# never put in a basket -- it would satisfy every arithmetic and dietary check
# in the system while being obviously wrong to any human. Dropped at ingestion
# rather than filtered later: a record that is never stored cannot be retrieved.
NON_FOOD_TERMS: frozenset[str] = frozenset({"cat", "dog", "kitten", "puppy", "pet", "litter"})


def _words(text: str) -> set[str]:
    """
    Lowercased word set, punctuation split out, plus singular forms.

    Plurals are folded in because the real catalogue writes `Raw Frozen Prawns
    Cutlet` and `Chicken Drumsticks`, and a term list of singular nouns misses
    both. Found by running the terms against the actual product names rather
    than against invented ones -- `prawns` was silently classified `frozen`,
    which is the exact under-match this module exists to prevent.

    Both forms are kept, so a word that is genuinely plural-only still matches.
    De-pluralising can only ADD candidates, and every candidate can only make
    the classification more restrictive, so a wrong guess here costs a product
    rather than a shopper's trust. That asymmetry is why three cheap rules are
    preferable to one clever one: a spurious candidate is nearly free, and a
    missing one is a vegetarian being served fish.

    THE `ies` RULE IS NOT OPTIONAL, AND WAS MISSING FOR A WHILE. Stripping `s`
    and `es` turns `anchovies` into `anchovie` and `anchovi`, neither of which
    is `anchovy`, so `Anchovies In Olive Oil` filed under `Pantry Staples`
    classified as `pantry` and reached a vegan's basket. The dataset in
    `datasets/` happens to carry no anchovies today, which is exactly why the
    first fix looked complete: the near-miss audit over 3,000 real rows came
    back clean while the hole was still open. Task 13 refreshes this catalogue
    daily, so "not in today's data" is not a property worth relying on.
    `test_animal_plurals_are_all_caught` pins the rule against a list of forms
    the catalogue could plausibly acquire tomorrow.
    """
    words = set(re.split(r"[^a-z0-9]+", text.lower())) - {""}
    # anchovies -> anchovy, berries -> berry
    singulars = {f"{w[:-3]}y" for w in words if w.endswith("ies") and len(w) > 4}
    # octopuses -> octopus, fishes -> fish
    singulars |= {w[:-2] for w in words if w.endswith("es") and len(w) > 3}
    # prawns -> prawn, wings -> wing
    singulars |= {w[:-1] for w in words if w.endswith("s") and len(w) > 2}
    return words | singulars


def is_non_food(product_name: str) -> bool:
    """Pet food and the like. See NON_FOOD_TERMS."""
    return bool(_words(product_name) & NON_FOOD_TERMS)


def classify(product_name: str, source_category: str) -> tuple[str, bool]:
    """
    Return `(category, was_overridden)` in the Lineage A vocabulary.

    The source category is a starting position, not an answer. The product name
    can only ever move the result to a MORE restricted category -- see the
    module docstring for the two real cases that forced this.

    An unrecognised source category raises rather than defaulting. A default
    would silently file tomorrow's new upstream category under `pantry`, which
    is the least restricted bucket and therefore the worst possible guess.
    """
    if source_category not in CATEGORY_MAP:
        raise ValueError(
            f"unmapped Lineage B category {source_category!r}; add it to "
            "CATEGORY_MAP deliberately rather than defaulting it"
        )
    base = CATEGORY_MAP[source_category]
    if base in _ALREADY_RESTRICTED:
        return base, False

    words = _words(product_name)
    if words & SEAFOOD_TERMS:
        return "seafood", True
    if words & MEAT_TERMS:
        return "meat", True
    return base, False


# --------------------------------------------------------------------------
# Sizes
# --------------------------------------------------------------------------

# `pack_grams == 1` is normalise.unit_price()'s sentinel for "sold each, not by
# weight", where the shelf price IS the unit price. It is not a claim that the
# item weighs a gram.
SOLD_EACH = 1
_ML_PER_L = 1000
_G_PER_KG = 1000

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(g|kg|ml|l)\s*$", re.IGNORECASE)
_PACK_RE = re.compile(r"^\s*(\d+)\s*(pk|pack)\s*$", re.IGNORECASE)


def parse_size(size: str) -> tuple[int, str]:
    """
    Return `(pack_grams, unit)` from Lineage B's free-text `size`.

    Only 68% of the 3,000 rows carry a parseable mass or volume; the rest are
    `kg`, `ea` and `6pk`. None of those is a gap -- each is a real way groceries
    are sold, and each has a defined answer:

    * `500g`, `1.5kg`   -> that mass.
    * `1l`, `750ml`     -> that volume, at 1 ml = 1 g. Milk is nearer 1.03 and
                           oil nearer 0.92, so this is an approximation. It is
                           used only for `unit_price_nzd` (a comparison aid) and
                           the planner's gram budget, never for a published
                           price, and a per-item density table is not knowledge
                           this dataset carries.
    * `kg`              -> priced PER KILOGRAM. 1000 g makes unit_price return
                           the shelf price unchanged, which is what a per-kg
                           price already is.
    * `ea`, `6pk`, `12pk` -> SOLD_EACH. A pack of six has no mass we know, and
                           inventing one would put a fabricated number into the
                           planner's arithmetic.

    An unrecognised size falls back to SOLD_EACH rather than raising: the price
    and the comparison are still true, and only the per-kilogram figure is
    unavailable. Refusing the row would discard a real offer over a missing
    convenience.
    """
    raw = (size or "").strip()
    if not raw:
        return SOLD_EACH, "ea"

    lowered = raw.lower()
    if lowered in {"kg", "per kg", "/kg"}:
        return _G_PER_KG, "kg"
    if lowered in {"ea", "each", "unit"}:
        return SOLD_EACH, "ea"

    if m := _SIZE_RE.match(raw):
        amount, suffix = Decimal(m.group(1)), m.group(2).lower()
        grams = {
            "g": amount,
            "kg": amount * _G_PER_KG,
            "ml": amount,
            "l": amount * _ML_PER_L,
        }[suffix]
        # int() truncates; a 0.5g pack is not a thing, but a 1.5kg one is.
        return max(int(grams), SOLD_EACH), raw

    if _PACK_RE.match(raw):
        return SOLD_EACH, lowered

    return SOLD_EACH, raw


# --------------------------------------------------------------------------
# Stores and keys
# --------------------------------------------------------------------------

# store_name -> the `store` value KNOWN_RETAILERS uses. Matched on a prefix
# because the remainder is the location.
_CHAIN_PREFIXES: tuple[tuple[str, str], ...] = (
    ("PAK'nSAVE", "paknsave"),
    ("PAKnSAVE", "paknsave"),
    ("New World", "new_world"),
    ("Woolworths", "woolworths"),
    ("Countdown", "woolworths"),  # renamed to Woolworths NZ in 2023
)


def parse_store(store_name: str) -> tuple[str, str]:
    """
    `"PAK'nSAVE Albany"` -> `("paknsave", "Albany")`.

    The location half is kept in its display form, not slugged: `store_key()`
    slugs it downstream, and `config/regions.json` matches region membership on
    these same human-readable names.
    """
    name = (store_name or "").strip()
    for prefix, chain in _CHAIN_PREFIXES:
        if name.lower().startswith(prefix.lower()):
            location = name[len(prefix) :].strip()
            if not location:
                raise ValueError(f"store name {store_name!r} has no location")
            return chain, location
    raise ValueError(
        f"unrecognised store {store_name!r}; add its chain to _CHAIN_PREFIXES "
        "rather than letting it default"
    )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def derive_product_key(product_name: str, size: str) -> str:
    """
    `("Brown Onions", "kg")` -> `"brown-onions-kg"`.

    The key must be IDENTICAL for the same product at different stores, because
    GSI1 partitions by it and that is what makes "cheapest X" one query. Name
    and size are the only fields both chains express the same way -- `product_id`
    is retailer-specific and `primary_key` embeds the store.

    251 of roughly 400 distinct (name, size) pairs occur in both chains, so most
    products compare across retailers and the rest are single-chain. That is a
    property of the dataset, not of this function: a product only one chain
    stocks legitimately has one price.
    """
    name = _slug(product_name)
    if not name:
        raise ValueError("product name slugs to nothing")
    suffix = _slug(size)
    return f"{name}-{suffix}" if suffix else name


# --------------------------------------------------------------------------
# The source
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransformReport:
    """
    What the transform did, so a human can audit it.

    `overridden` is the count that matters: it is the number of rows whose
    source category would have mis-stated a dietary fact. A sharp change in it
    between runs means the upstream categorisation moved, and the safety table
    here should be re-read before the load is trusted.
    """

    total: int
    kept: int
    dropped_non_food: int
    overridden: int
    unmapped_categories: tuple[str, ...]


STORE_LOCATIONS_CONFIG = Path(__file__).resolve().parent.parent / "config" / "store-locations.json"


@functools.cache
def _store_coordinates() -> dict[str, tuple[float, float]]:
    """Suburb -> (lat, lon), from config/store-locations.json. Cached; the file is static."""
    raw = json.loads(STORE_LOCATIONS_CONFIG.read_text(encoding="utf-8"))
    return {name: (e["lat"], e["lon"]) for name, e in raw["stores"].items()}


def store_coordinates(store_location: str) -> tuple[float, float]:
    """
    Where this store is, or an error naming the store.

    THIS USED TO RETURN 0.0/0.0 AND THAT WAS A BUG, not a safe default. Lineage
    B carries no coordinates, and the first version wrote a zero sentinel with a
    comment calling it fail-closed. 0.0/0.0 is a real position in the Atlantic,
    so `NearFilter.covers()` computed an enormous distance and excluded every
    record -- and the graph reads an empty result as `no_data`, telling a
    shopper "I don't have price data near you" about the supermarket down the
    road. That is precisely the silent-exclusion defect Pilot Task 5a fixed for
    the store filter, reintroduced through a different door.

    The lesson is the one this repository keeps relearning: a wrong value that
    happens to produce plausible behaviour is worse than a missing one, because
    nothing distinguishes it from the value being right. So an unknown store
    raises, the way `parse_store` raises on an unknown chain and `RawOffer`
    refuses an undated price.
    """
    coordinates = _store_coordinates()
    if store_location not in coordinates:
        raise ValueError(
            f"no coordinates for store location {store_location!r}. Add it to "
            "config/store-locations.json -- a missing position must not become "
            "a default one, or a radius filter silently excludes the store."
        )
    return coordinates[store_location]


def to_offer(record: dict, *, captured_at: str) -> RawOffer:
    """One Lineage B record as a `RawOffer`. Raises on anything unmappable."""
    chain, location = parse_store(record["store_name"])
    lat, lon = store_coordinates(location)
    pack_grams, unit = parse_size(record.get("size", ""))
    category, _ = classify(record["product_name"], record["category"])
    brand = (record.get("brand") or "").strip()
    display = f"{brand} {record['product_name']}".strip() if brand else record["product_name"]

    return RawOffer(
        product_key=derive_product_key(record["product_name"], record.get("size", "")),
        store=chain,
        store_location=location,
        display_name=display,
        canonical_name=record["product_name"],
        category=category,
        # Lineage B stores money as a DynamoDB Number. str() before Decimal is
        # the whole point: Decimal(float) carries the binary error into a cent.
        price_nzd=Decimal(str(record["price"])),
        unit=unit,
        pack_grams=pack_grams,
        # Absent upstream. False is the honest answer -- claiming a special we
        # cannot see would put a marketing claim in a citation.
        on_special=False,
        captured_at=captured_at,
        lat=lat,
        lon=lon,
    )


def transform(records: list[dict], *, captured_at: str) -> tuple[list[RawOffer], TransformReport]:
    """
    Lineage B records -> `RawOffer`s, with an audit of what happened.

    `captured_at` is required and has no default, for the reason `RawOffer`
    gives: a price the shopper cannot date is a price they cannot evaluate.
    Lineage B carries no date, so the caller must supply the data team's stated
    collection date -- and it is THEIR provenance claim, recorded as such, not a
    date this code invents.
    """
    if not captured_at:
        raise ValueError("captured_at is required; Lineage B carries no date of its own")

    offers: list[RawOffer] = []
    dropped = overridden = 0
    unmapped: set[str] = set()

    for record in records:
        name = record["product_name"]
        if is_non_food(name):
            dropped += 1
            continue
        try:
            _, was_overridden = classify(name, record["category"])
        except ValueError:
            unmapped.add(record["category"])
            continue
        overridden += was_overridden
        offers.append(to_offer(record, captured_at=captured_at))

    return offers, TransformReport(
        total=len(records),
        kept=len(offers),
        dropped_non_food=dropped,
        overridden=overridden,
        unmapped_categories=tuple(sorted(unmapped)),
    )
