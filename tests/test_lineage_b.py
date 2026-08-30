"""
Lineage B -> Lineage A transform.

The tests that matter here are the dietary ones. Everything else in this file
is a mapping check; those are the reason the module exists, and each one is
built from a product that is really in `datasets/data/dynamodb_products/`,
filed under the category the data team really filed it under.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from ingestion.lineage_b import (
    CATEGORY_MAP,
    SOLD_EACH,
    classify,
    derive_product_key,
    is_non_food,
    parse_size,
    parse_store,
    store_coordinates,
    to_offer,
    transform,
)
from ingestion.normalise import to_item, unit_price
from src.graph.dietary import SUPPORTED_EXCLUSIONS, map_exclusions
from src.retrieval.filters import NearFilter, haversine_km

CAPTURED_AT = "2026-08-28"
DATASET = Path(__file__).resolve().parents[1] / "datasets" / "data" / "dynamodb_products"


# ---------------------------------------------------------------- dietary


@pytest.mark.parametrize(
    ("product_name", "source_category"),
    [
        # Every one of these is a real row. Source category on the right is
        # what the data team assigned, not what we wish they had.
        ("Frozen Whole Chicken", "Frozen Foods"),
        ("Frozen BBQ Lamb Chops", "Frozen Foods"),
        ("Roast Beef & Veggies Frozen Meal", "Frozen Foods"),
        ("Shaved Honey Baked Ham", "Breakfast Cereals, Oats & Spreads"),
        ("Beef Manuka Honey & Hickory Sausages", "Breakfast Cereals, Oats & Spreads"),
        ("Manuka Honey & Rosemary Chicken", "Breakfast Cereals, Oats & Spreads"),
    ],
)
def test_meat_is_never_left_in_a_category_a_vegetarian_would_be_served(
    product_name: str, source_category: str
) -> None:
    """
    The defect this module exists to prevent.

    `dietary.py` excludes {meat, seafood} for "vegetarian". It does NOT exclude
    `frozen` or `pantry`, because in the fixture catalogue those hold
    vegetables and rice. Mapping the source category straight through would put
    frozen chicken and baked ham into a vegetarian meal plan -- a silent breach
    of a safety-critical invariant, which is the worst shape of bug here.
    """
    category, overridden = classify(product_name, source_category)

    assert category == "meat", f"{product_name!r} classified {category!r}"
    assert overridden is True

    excluded, unsupported = map_exclusions(["vegetarian"])
    assert not unsupported
    assert category in excluded


@pytest.mark.parametrize(
    ("product_name", "source_category"),
    [
        ("Raw Frozen Prawns Cutlet", "Frozen Foods"),
        ("Adult Grilled Ocean Whitefish Tuna Feast Gravy", "Frozen Foods"),
    ],
)
def test_seafood_is_caught_even_when_filed_as_frozen(
    product_name: str, source_category: str
) -> None:
    category, overridden = classify(product_name, source_category)
    assert category == "seafood"
    assert overridden is True
    excluded, _ = map_exclusions(["pescatarian"])
    assert "meat" in excluded and "seafood" not in excluded  # pescatarian keeps fish
    excluded_vegan, _ = map_exclusions(["vegan"])
    assert category in excluded_vegan


@pytest.mark.parametrize(
    "plural",
    [
        # Regular -s
        "Prawns",
        "Sausages",
        "Drumsticks",
        "Wings",
        "Ribs",
        "Mussels",
        "Oysters",
        "Scallops",
        "Rashers",
        "Meatballs",
        # -es
        "Fishes",
        "Octopuses",
        # -ies, the form the first fix missed entirely
        "Anchovies",
    ],
)
def test_animal_plurals_are_all_caught(plural: str) -> None:
    """
    Every plural form must restrict, filed in the loosest category there is.

    This is pinned as a PROPERTY rather than by adding plurals to the term
    lists, because a term list is something a future edit can forget to update
    and a rule is not.

    `Anchovies` is the case that matters. Stripping `s`/`es` yields `anchovie`
    and `anchovi`, never `anchovy`, so anchovies in a `Pantry Staples` tin
    classified as `pantry` and reached a vegan basket. The near-miss audit over
    all 3,000 real rows came back clean at the time, because this dataset
    carries no anchovies — a hole that today's data does not happen to probe is
    still a hole, and Task 13 refreshes this catalogue daily.
    """
    category, overridden = classify(f"{plural} In Olive Oil", "Pantry Staples")
    assert category in {"meat", "seafood"}, f"{plural!r} classified {category!r}"
    assert overridden is True


def test_word_boundaries_still_hold_after_depluralising() -> None:
    """
    The plural rules must not turn into substring matching.

    `Hamburger Buns` and `Packham Pears` both contain "ham"; both are correctly
    left alone. If the matcher ever loosened to prefixes or substrings, bread
    and pears would be classified as meat — an over-match is safe for a
    shopper but would make the override meaningless as a signal.
    """
    assert classify("Hamburger Buns", "Bread, Wraps & Bakery") == ("bakery", False)
    assert classify("Packham Pears", "Fresh Fruit") == ("produce", False)
    assert classify("Ribbon Cut Egg Noodles", "Rice, Pasta & Noodles") == ("pantry", False)


def test_the_override_only_ever_restricts_further() -> None:
    """
    A name match must never move a product OUT of meat or seafood.

    "Chicken" appears in `Chicken & Poultry`; the override is skipped there
    rather than re-deriving a category that is already correct. If this ever
    inverted, a seafood term in a meat product's name could reclassify it and
    a pescatarian would be served beef.
    """
    assert classify("Beef Mince", "Beef & Lamb") == ("meat", False)
    assert classify("Fish Fingers", "Fresh Seafood") == ("seafood", False)
    # A meat product whose name also mentions fish stays restricted.
    assert classify("Surf And Turf Steak", "Beef & Lamb")[0] == "meat"


def test_plant_milk_is_over_excluded_rather_than_under_excluded() -> None:
    """
    Oat milk maps to `dairy`, and that is the intended, documented trade.

    Lineage B files plant and cow's milk in one category, so a vegan either
    loses oat milk or gains cow's milk. The first is safe. This test exists so
    that anyone "fixing" it has to read the reasoning first.
    """
    assert classify("Barista Edition Oat Milk", "Fresh Milk & Plant Milk") == ("dairy", False)
    assert classify("Standard Milk", "Fresh Milk & Plant Milk") == ("dairy", False)
    excluded, _ = map_exclusions(["vegan"])
    assert "dairy" in excluded


def test_eggs_land_in_the_category_dietary_actually_checks() -> None:
    assert classify("Free Range Size 7 Eggs", "Eggs") == ("chilled", False)
    for term in ("no eggs", "vegan"):
        excluded, _ = map_exclusions([term])
        assert "chilled" in excluded


def test_every_mapped_category_is_one_dietary_understands() -> None:
    """
    The two tables must share a vocabulary.

    `dietary.py` maps exclusion terms onto fixture category names. If this
    module ever emits a category outside that set, the exclusion silently
    matches nothing -- the failure mode is invisible, which is why it is
    asserted rather than trusted.
    """
    fixture = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "products.json").read_text(
            encoding="utf-8"
        )
    )
    known = {r["category"] for r in fixture}
    assert set(CATEGORY_MAP.values()) <= known

    dietary_categories = set().union(*SUPPORTED_EXCLUSIONS.values())
    assert dietary_categories <= known


def test_an_unmapped_source_category_raises_rather_than_defaulting() -> None:
    """A new upstream category must not land silently in the loosest bucket."""
    with pytest.raises(ValueError, match="unmapped"):
        classify("Something New", "Confectionery & Snacks")


def test_pet_food_is_dropped_not_categorised() -> None:
    """
    The dataset carries cat food. It would pass every check in the system.

    Arithmetic, grounding and dietary rules are all satisfied by a tin of cat
    food; nothing in the graph knows it is not dinner. Dropping it at ingestion
    is the only place the fact is available.
    """
    assert is_non_food("Adult Gravy Lovers Ocean Whitefish Tuna Feast Wet Cat Food")
    assert not is_non_food("Whitefish Fillets")

    records = [
        _record("Adult Chicken Feast Wet Cat Food", "Frozen Foods", "85g"),
        _record("Chicken Drumsticks", "Chicken & Poultry", "1kg"),
    ]
    offers, report = transform(records, captured_at=CAPTURED_AT)
    assert report.dropped_non_food == 1
    assert [o.canonical_name for o in offers] == ["Chicken Drumsticks"]


# ---------------------------------------------------------------- sizes


@pytest.mark.parametrize(
    ("size", "expected_grams"),
    [
        ("500g", 500),
        ("1kg", 1000),
        ("1.5kg", 1500),
        ("100g", 100),
        ("1l", 1000),
        ("2l", 2000),
        ("750ml", 750),
        ("kg", 1000),  # priced per kilogram
        ("ea", SOLD_EACH),
        ("6pk", SOLD_EACH),
        ("12pk", SOLD_EACH),
        ("", SOLD_EACH),
        ("punnet", SOLD_EACH),  # unrecognised, but the price is still true
    ],
)
def test_parse_size(size: str, expected_grams: int) -> None:
    grams, _ = parse_size(size)
    assert grams == expected_grams


def test_per_kilogram_sizing_leaves_the_shelf_price_alone() -> None:
    """
    `size: "kg"` means the price IS the per-kg price.

    Mapping it to 1000g makes `unit_price` return it unchanged. Getting this
    wrong is how `unit_price_nzd` became "2490.00" against a $2.49 item in the
    live table once already -- see docs/ARCHITECTURE.md section 8.
    """
    grams, _ = parse_size("kg")
    assert unit_price(Decimal("2.49"), grams) == Decimal("2.49")


def test_sold_each_does_not_produce_a_per_kilogram_fiction() -> None:
    grams, _ = parse_size("ea")
    assert unit_price(Decimal("2.49"), grams) == Decimal("2.49")


# ---------------------------------------------------------------- stores, keys


@pytest.mark.parametrize(
    ("store_name", "expected"),
    [
        ("PAK'nSAVE Albany", ("paknsave", "Albany")),
        ("PAK'nSAVE Lincoln Road", ("paknsave", "Lincoln Road")),
        ("PAK'nSAVE Mt Albert", ("paknsave", "Mt Albert")),
        ("New World Newmarket", ("new_world", "Newmarket")),
        ("Woolworths Ponsonby", ("woolworths", "Ponsonby")),
    ],
)
def test_parse_store(store_name: str, expected: tuple[str, str]) -> None:
    assert parse_store(store_name) == expected


def test_an_unknown_chain_raises() -> None:
    with pytest.raises(ValueError, match="unrecognised store"):
        parse_store("Four Square Devonport")


def test_location_keeps_the_form_regions_json_matches_on() -> None:
    """
    `config/regions.json` lists store locations as display names.

    Slugging here would stop "North Shore" resolving to Albany, which is the
    location path this dataset supports -- it carries no coordinates.
    """
    regions = json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "regions.json").read_text(
            encoding="utf-8"
        )
    )
    known = {loc for r in regions["regions"].values() for loc in r["store_locations"]}
    for store in ("PAK'nSAVE Albany", "New World Papakura", "PAK'nSAVE Manukau"):
        _, location = parse_store(store)
        assert location in known, f"{location!r} is in no region"


def test_derive_product_key() -> None:
    assert derive_product_key("Brown Onions", "kg") == "brown-onions-kg"
    assert (
        derive_product_key("PAK'nSAVE Free Range Eggs", "12pk") == "pak-nsave-free-range-eggs-12pk"
    )
    assert derive_product_key("Milk", "") == "milk"
    with pytest.raises(ValueError, match="slugs to nothing"):
        derive_product_key("!!!", "kg")


def test_the_same_product_at_two_chains_shares_one_key() -> None:
    """
    GSI1 partitions by product_key, so a shared key IS the comparison.

    If this drifted per retailer, every product would compare against only
    itself and "cheapest X" would always return one row.
    """
    paknsave = _record("Baby Cos Lettuce", "Fresh Vegetables", "ea", store="PAK'nSAVE Albany")
    new_world = _record("Baby Cos Lettuce", "Fresh Vegetables", "ea", store="New World Albany")
    a = to_offer(paknsave, captured_at=CAPTURED_AT)
    b = to_offer(new_world, captured_at=CAPTURED_AT)
    assert a.product_key == b.product_key == "baby-cos-lettuce-ea"
    assert a.store != b.store


# ---------------------------------------------------------------- coordinates


def test_every_store_in_the_dataset_has_coordinates() -> None:
    """
    Ingestion must not be able to produce a positionless record.

    Lineage B carries no geography, so every store name in it has to be in
    config/store-locations.json before the transform can run. A missing entry
    raises rather than defaulting, which is what stops the sentinel bug below
    from coming back.
    """
    for store in (
        "PAK'nSAVE Albany",
        "PAK'nSAVE Lincoln Road",
        "PAK'nSAVE Manukau",
        "PAK'nSAVE Mt Albert",
        "PAK'nSAVE Sylvia Park",
        "New World Albany",
        "New World New Lynn",
        "New World Newmarket",
        "New World Papakura",
        "New World Remuera",
    ):
        _, location = parse_store(store)
        lat, lon = store_coordinates(location)
        # Auckland. A transposed pair or a stray zero lands outside this box.
        assert -37.5 < lat < -36.5, f"{location}: latitude {lat} is not in Auckland"
        assert 174.0 < lon < 175.5, f"{location}: longitude {lon} is not in Auckland"


def test_an_unknown_store_raises_rather_than_defaulting() -> None:
    """
    The regression test for the sentinel.

    This returned 0.0/0.0 once, described in the code as fail-closed. It is not
    a sentinel, it is the Atlantic: NearFilter computed a real distance of
    ~18,000km and excluded the record, so a radius query over the whole
    catalogue returned nothing and the graph said "I don't have price data near
    you" about a supermarket in the same suburb.
    """
    with pytest.raises(ValueError, match="no coordinates for store location"):
        store_coordinates("Springfield")


def test_a_radius_filter_now_includes_and_excludes_on_real_distance() -> None:
    """
    The behaviour the sentinel silently prevented.

    With 0.0/0.0 the first assertion here failed and the second passed, so a
    test that only checked exclusion would have looked green.
    """
    offer = to_offer(
        _record("Brown Onions", "Fresh Vegetables", "kg", store="PAK'nSAVE Albany"),
        captured_at=CAPTURED_AT,
    )
    albany = NearFilter(lat=-36.7280, lon=174.7000, radius_km=5)
    papakura = NearFilter(lat=-37.0660, lon=174.9440, radius_km=5)
    assert albany.covers(offer.lat, offer.lon) is True
    assert papakura.covers(offer.lat, offer.lon) is False


def test_store_coordinates_agree_with_the_fixture_catalogue() -> None:
    """
    Two catalogues must not disagree about where the same suburb is.

    The fixtures carry their own lat/lon per record and do not read the config;
    if the two drift, the same suburb sits in two places and a radius result
    depends on which catalogue is loaded.
    """
    fixture = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "products.json").read_text(
            encoding="utf-8"
        )
    )
    for record in fixture:
        lat, lon = store_coordinates(record["store_location"])
        # Same suburb, within a couple of kilometres -- these are centroids,
        # not surveyed positions, so exact equality is the wrong assertion.
        assert haversine_km(lat, lon, record["lat"], record["lon"]) < 3.0, record["store_location"]


# ---------------------------------------------------------------- end to end


def test_a_transformed_offer_survives_the_existing_normaliser() -> None:
    """
    The transform's whole value is that nothing downstream changes.

    `to_item` is the existing writer; if a RawOffer built here does not satisfy
    it, the seam is in the wrong place.
    """
    offer = to_offer(
        _record("Brown Onions", "Fresh Vegetables", "kg", price="2.49"),
        captured_at=CAPTURED_AT,
    )
    item = to_item(offer)

    assert item["product_key"] == "brown-onions-kg"
    assert item["store_key"] == "paknsave#albany"
    assert item["category"] == "produce"
    assert item["valid_date"] == CAPTURED_AT
    # Money is a string at rest, never a float.
    assert item["price_nzd"] == "2.49"
    assert isinstance(item["price_nzd"], str)
    assert item["unit_price_nzd"] == "2.49"
    # Zero-padded cents lead the sort key, so cheapest sorts first.
    assert item["gsi1_sk"].startswith("000000249#")


def test_money_never_becomes_a_float() -> None:
    """Lineage B stores price as a DynamoDB Number; a float cent is a wrong cent."""
    offer = to_offer(
        _record("Rolled Oats", "Pantry Staples", "1kg", price=3.7), captured_at=CAPTURED_AT
    )
    assert offer.price_nzd == Decimal("3.7")
    assert to_item(offer)["price_nzd"] == "3.7"


def test_captured_at_is_required() -> None:
    """
    Lineage B carries no date. The caller must supply the data team's.

    A default here would let an undated price through by omission, which is
    exactly what RawOffer's own docstring refuses.
    """
    with pytest.raises(ValueError, match="captured_at is required"):
        transform([_record("Rolled Oats", "Pantry Staples", "1kg")], captured_at="")


@pytest.mark.skipif(not DATASET.exists(), reason="dataset not present")
def test_the_whole_real_dataset_transforms_without_a_single_unmapped_category() -> None:
    """
    Run it over all 3,000 real rows, not a sample.

    A mapping table is only as good as its coverage of the data it will meet,
    and the categories were read off this dataset -- so a miss here means the
    dataset moved.
    """
    records = _load_dataset()
    assert len(records) == 3000
    offers, report = transform(records, captured_at=CAPTURED_AT)

    assert report.unmapped_categories == ()
    # Conservation: every input row is kept, dropped as non-food, or collapsed
    # into a cheaper twin. Nothing may vanish unaccounted for -- a row that
    # disappears silently is a product the shopper cannot be shown and cannot
    # be told about.
    assert report.kept + report.dropped_non_food + report.collapsed_duplicates == report.total
    assert report.overridden > 0, "the safety override caught nothing; check the terms"
    # Every offer must be writable and carry a category dietary.py understands.
    fixture_categories = {
        r["category"]
        for r in json.loads(
            (Path(__file__).resolve().parents[1] / "fixtures" / "products.json").read_text(
                encoding="utf-8"
            )
        )
    }
    for offer in offers:
        assert offer.category in fixture_categories
        assert offer.pack_grams >= SOLD_EACH
        to_item(offer)


@pytest.mark.skipif(not DATASET.exists(), reason="dataset not present")
def test_no_animal_product_escapes_into_a_category_a_vegetarian_would_eat() -> None:
    """
    The audit that found the `anchovies` class of bug, kept as a gate.

    `classify()` matches whole words. This sweeps all 3,000 real rows with a
    DELIBERATELY LOOSER, INDEPENDENT detector -- substring stems -- and requires
    every disagreement to be a known false positive. A second implementation
    checking the first is the only way a missing-plural or missing-synonym gap
    surfaces without someone happening to look.

    Task 13 refreshes this catalogue, so this is not a one-off audit: a new
    product whose name says "anchovies" while its category says pantry fails
    here rather than reaching a vegan.

    Growing ALLOWED means a human read the name and decided it is genuinely not
    an animal product. Anything else is a bug in the term lists or the plural
    rules, and the fix belongs there.
    """
    stems = (
        "anchov prawn shrimp salmon tuna mussel oyster squid calamar crab lobster "
        "scallop sardine snapper hoki cod fish seafood clam octopus beef lamb pork "
        "ham bacon chick poultry turkey duck sausag salami chorizo mince steak veal "
        "venison mutton brisket pepperoni prosciutto meat rib drumstick wing "
        "schnitzel pastrami kransky gammon rasher patt"
    ).split()

    # Substring hits that are NOT animal products. Each read and judged by hand.
    allowed = {
        "Ribbon Cut Egg Noodles",  # "rib" inside "ribbon"
        "Hamburger Buns",  # "ham" inside "hamburger" -- bread
        "Packham Pears",  # "ham" inside a pear cultivar
    }

    escaped: list[tuple[str, str, str]] = []
    for record in _load_dataset():
        name = record["product_name"]
        if is_non_food(name) or name in allowed:
            continue
        category, _ = classify(name, record["category"])
        if category in {"meat", "seafood"}:
            continue
        lowered = name.lower()
        if any(stem in lowered for stem in stems):
            escaped.append((name, record["category"], category))

    assert not escaped, (
        "animal-product names classified into a category a vegetarian would be "
        f"served: {sorted(set(escaped))}"
    )


# ---------------------------------------------------------------- duplicates


def test_two_brands_of_one_product_at_one_store_collapse_to_the_cheapest() -> None:
    """
    The base table key is (store_key, product_key) and must be unique.

    `derive_product_key` ignores brand on purpose, so the same product compares
    across chains. The cost is that one store stocking two brands of the same
    thing at the same size produces two rows with one key -- and `BatchWriteItem`
    refuses a batch containing duplicate keys.

    Found by loading, not by reading: the first real load failed with "Provided
    list of item keys contains duplicates" after 96 collisions in Pak'nSave
    alone. The fixtures carry exactly one product per key by construction, so
    nothing offline had ever exercised this.
    """
    records = [
        _record("Mixed Berries", "Frozen Foods", "500g", price="7.09", store="PAK'nSAVE Albany"),
        _record("Mixed Berries", "Frozen Foods", "500g", price="6.99", store="PAK'nSAVE Albany"),
    ]
    records[0]["brand"] = "Frozen Harvest"
    records[1]["brand"] = "Pams"

    offers, report = transform(records, captured_at=CAPTURED_AT)

    assert len(offers) == 1
    assert report.collapsed_duplicates == 1
    # Cheapest wins: it is the only answer the product ever gives for a
    # same-product, same-store, same-size pair.
    assert offers[0].price_nzd == Decimal("6.99")
    assert offers[0].display_name == "Pams Mixed Berries"


def test_the_same_product_at_two_stores_is_not_collapsed() -> None:
    """Deduplication is per store. Two stores is the comparison, not a duplicate."""
    records = [
        _record("Brown Onions", "Fresh Vegetables", "kg", price="2.49", store="PAK'nSAVE Albany"),
        _record("Brown Onions", "Fresh Vegetables", "kg", price="2.79", store="New World Albany"),
    ]
    offers, report = transform(records, captured_at=CAPTURED_AT)
    assert len(offers) == 2
    assert report.collapsed_duplicates == 0
    assert {o.store for o in offers} == {"paknsave", "new_world"}


def test_an_equal_priced_tie_breaks_deterministically() -> None:
    """
    Without a tiebreak, two equally priced brands swap between runs and
    `refresh()` reports `changed` on a day nothing changed -- destroying the
    signal the diff exists to provide.
    """

    def build(order: list[str]) -> list:
        out = []
        for brand in order:
            r = _record(
                "Tuna Loins", "Fresh Seafood", "kg", price="39.99", store="New World Albany"
            )
            r["brand"] = brand
            out.append(r)
        return out

    first, _ = transform(build(["Leigh Fish", "Ocean Blue"]), captured_at=CAPTURED_AT)
    second, _ = transform(build(["Ocean Blue", "Leigh Fish"]), captured_at=CAPTURED_AT)
    assert first[0].display_name == second[0].display_name


@pytest.mark.skipif(not DATASET.exists(), reason="dataset not present")
def test_the_whole_dataset_yields_unique_table_keys() -> None:
    """
    The property the load actually needs, asserted over all 3,000 real rows.

    A unit test with two invented brands proves the rule; only the real
    catalogue proves the rule covers the real collisions.
    """
    from ingestion.normalise import store_key

    offers, report = transform(_load_dataset(), captured_at=CAPTURED_AT)
    keys = [(store_key(o.store, o.store_location), o.product_key) for o in offers]
    assert len(keys) == len(set(keys)), "duplicate (store_key, product_key) would fail the write"
    assert report.collapsed_duplicates > 0, "no collisions collapsed; has the dataset changed?"


# ---------------------------------------------------------------- helpers


def _load_dataset() -> list[dict]:
    """Every real Lineage B record, flattened out of its batch-write envelope."""
    records: list[dict] = []
    for path in sorted(DATASET.glob("*.json")):
        for entry in json.loads(path.read_text(encoding="utf-8"))["SmartGroceryProducts"]:
            item = entry.get("PutRequest", {}).get("Item", entry)
            records.append({k: (v.get("S") if "S" in v else v.get("N")) for k, v in item.items()})
    return records


def _record(
    name: str,
    category: str,
    size: str,
    *,
    price: str | float = "1.00",
    store: str = "PAK'nSAVE Albany",
) -> dict:
    """A Lineage B record, in the shape `smart-grocery-products-dev` holds."""
    return {
        "primary_key": f"{store}_{name}",
        "store_id": "00000000-0000-0000-0000-000000000000",
        "store_name": store,
        "product_id": "0000000-EA-000",
        "product_name": name,
        "brand": None,
        "size": size,
        "price": price,
        "category": category,
    }
