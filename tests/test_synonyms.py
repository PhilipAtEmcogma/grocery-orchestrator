"""
The product synonym table.

`resolve_product_key` is exact-match with no substring fallback, so this table
is the entire vocabulary the assistant understands. A wrong entry produces a
confidently incorrect price, which is the failure this project treats as worse
than no answer -- so the entries are checked here rather than trusted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingestion.lineage_b import classify, derive_product_key, is_non_food
from src.retrieval.memory import (
    SYNONYMS_CONFIG,
    InMemoryPriceRepository,
    load_synonyms,
    normalise_term,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets" / "data" / "dynamodb_products"


@pytest.fixture(scope="module")
def config() -> dict:
    return json.loads(SYNONYMS_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def lineage_b_catalogue() -> dict[str, str]:
    """Every real Lineage B product key, mapped to its resolved category."""
    if not DATASET.exists():
        pytest.skip("dataset not present")
    catalogue: dict[str, str] = {}
    for path in sorted(DATASET.glob("*.json")):
        for entry in json.loads(path.read_text(encoding="utf-8"))["SmartGroceryProducts"]:
            item = entry.get("PutRequest", {}).get("Item", entry)
            record = {k: (v.get("S") if "S" in v else v.get("N")) for k, v in item.items()}
            name = record["product_name"]
            if is_non_food(name):
                continue
            key = derive_product_key(name, record.get("size", ""))
            catalogue[key] = classify(name, record["category"])[0]
    return catalogue


# ---------------------------------------------------------------- structure


def test_every_term_survives_normalisation(config: dict) -> None:
    """
    A term the matcher can never produce is an entry that can never fire.

    `normalise_term` strips punctuation, noise words and single characters, and
    the lookup happens AFTER it runs. So an entry containing a noise word --
    "block of butter" -- must be stored in its stripped form or it is dead
    weight that looks like coverage.
    """
    for name, catalogue in config["catalogues"].items():
        for section in ("head_terms", "generated_product_names"):
            for phrase in catalogue.get(section, {}):
                if phrase.startswith("_"):
                    continue
                assert normalise_term(phrase), f"{name}/{section}: {phrase!r} normalises to nothing"


def test_load_synonyms_prefers_head_terms_over_generated_names() -> None:
    """
    A human's choice outranks a mechanical restatement of a product name.

    Both can supply the same term -- "bananas" is a curated head term AND the
    name of a product -- and the curated one must win.
    """
    synonyms = load_synonyms()
    assert synonyms["butter"][0] == "butter-500g"  # fixture head term, listed first
    assert "salted-butter-500g" in synonyms["butter"]  # lineage_b head term, also offered


def test_a_term_may_name_a_product_in_more_than_one_catalogue() -> None:
    """
    The table describes several catalogues; only one is ever loaded.

    "butter" means a different key in each, which is why load_synonyms returns
    a LIST. Collapsing it to one key would make the table catalogue-specific
    and force the repository to know which data it holds.
    """
    synonyms = load_synonyms()
    assert len(synonyms["butter"]) >= 2
    assert all(isinstance(keys, list) for keys in synonyms.values())


# ---------------------------------------------------------------- the fixtures


def test_the_fixture_vocabulary_did_not_change_when_it_moved_to_config() -> None:
    """
    Migrating the table out of Python must be behaviour-preserving.

    These are the terms the demos, samples and eval cases rely on. If moving
    them into JSON silently dropped one, the failure would show up as a
    `no_data` in a demo rather than as a test failure here.
    """
    repo = InMemoryPriceRepository()
    expected = {
        "butter": "butter-500g",
        "block of butter": "butter-500g",
        "milk": "milk-2l",
        "cheese": "cheese-tasty-1kg",
        "eggs": "eggs-size7-dozen",
        "mince": "beef-mince-1kg",
        "ground beef": "beef-mince-1kg",
        "chicken": "chicken-thigh-1kg",
        "spuds": "potatoes-washed-2kg",
        "porridge": "oats-rolled-1kg",
        "frozen veg": "frozen-mixed-veg-1kg",
    }
    for term, key in expected.items():
        assert repo.resolve_product_key(term) == key, term


def test_noise_words_are_still_stripped_before_lookup() -> None:
    repo = InMemoryPriceRepository()
    for phrase in ("cheapest butter", "what is the price of butter", "butter near me"):
        assert repo.resolve_product_key(phrase) == "butter-500g", phrase


def test_an_unknown_term_still_refuses() -> None:
    """Under-matching is recoverable; mis-matching is not. See resolve_product_key."""
    repo = InMemoryPriceRepository()
    assert repo.resolve_product_key("truffle oil") is None
    assert repo.resolve_product_key("caviar") is None


def test_only_keys_present_in_the_loaded_catalogue_resolve() -> None:
    """
    A synonym for a product this catalogue does not hold must not resolve.

    The table covers several catalogues, so the fixture repository is offered
    Lineage B keys it has never heard of. Returning one would hand the graph a
    key with no prices, which reads as `no_data` for a product that was never
    in this catalogue at all -- a confusing answer to a question nobody asked.
    """
    repo = InMemoryPriceRepository()
    assert repo.resolve_product_key("brown onions") is None  # lineage_b only
    assert repo.resolve_product_key("onions") == "onions-brown-1-5kg"  # fixture wins


# ---------------------------------------------------------------- lineage B


def test_every_curated_head_term_points_at_a_real_product(
    config: dict, lineage_b_catalogue: dict[str, str]
) -> None:
    """
    A head term naming a key the catalogue does not hold is a dead entry.

    Checked against every derived key, NOT against generated_product_names --
    that set excludes ambiguous names, so a perfectly real key like
    `bananas-kg` is absent from it. Checking the wrong set reported 18 false
    failures the first time this ran.
    """
    for term, key in config["catalogues"]["lineage_b"]["head_terms"].items():
        if term.startswith("_"):
            continue
        assert key in lineage_b_catalogue, f"head term {term!r} -> {key!r} is not a product"


@pytest.mark.parametrize(
    ("term", "expected_category"),
    [
        ("butter", "dairy"),
        ("milk", "dairy"),
        ("yoghurt", "dairy"),
        ("bread", "bakery"),
        ("eggs", "chilled"),
        ("chicken", "meat"),
        ("mince", "meat"),
        ("sausages", "meat"),
        ("rice", "pantry"),
        ("pasta", "pantry"),
        ("flour", "pantry"),
        ("oats", "pantry"),
        ("oil", "pantry"),
        ("bananas", "produce"),
        ("apples", "produce"),
        ("onions", "produce"),
        ("potatoes", "produce"),
        ("carrots", "produce"),
        ("broccoli", "produce"),
        ("tomatoes", "produce"),
    ],
)
def test_a_head_term_resolves_to_something_of_the_right_kind(
    config: dict, lineage_b_catalogue: dict[str, str], term: str, expected_category: str
) -> None:
    """
    The check that catches a mis-curation.

    "butter" matches 14 products, one of which is `Salted Butter Frozen
    Dessert`; "cheese" matches 22, one of which is `Chunky Cheese Sausages`.
    Asserting the CATEGORY of the chosen key is what stops a plausible-looking
    entry pointing at a novelty product -- a reviewer editing this table by
    hand gets told immediately rather than at demo time.
    """
    key = config["catalogues"]["lineage_b"]["head_terms"][term]
    assert lineage_b_catalogue[key] == expected_category, (
        f"head term {term!r} -> {key!r} is {lineage_b_catalogue[key]!r}, "
        f"expected {expected_category!r}"
    )


def test_omitted_terms_are_recorded_with_a_reason(config: dict) -> None:
    """
    An omission nobody wrote down is indistinguishable from an oversight.

    These terms have no staple in the catalogue and deliberately return
    no_data. If one is later added as a head term, its note must go -- that is
    what keeps the record honest rather than decorative.
    """
    lineage_b = config["catalogues"]["lineage_b"]
    omitted = lineage_b["_deliberately_omitted"]
    placeholders = {"", "tbd", "todo", "n/a", "na", "-", "?"}
    for term, reason in omitted.items():
        if term.startswith("_"):
            continue
        assert term not in lineage_b["head_terms"], f"{term!r} is both omitted and mapped"
        # A written reason, not a length. The first version of this assertion
        # required 40 characters and rejected "The catalogue holds none at
        # all." -- a complete reason that happens to be short. Measuring prose
        # by the yard tests the wrong property and pressures the author to pad
        # the config to satisfy the test.
        assert reason.strip().lower() not in placeholders, f"{term!r} omitted without a reason"
        assert reason.strip().endswith("."), f"{term!r}: reason is not a sentence"


def test_generated_names_hold_no_ambiguous_entry(
    config: dict, lineage_b_catalogue: dict[str, str]
) -> None:
    """
    A name meaning two products at different sizes must answer for neither.

    `scripts/generate_synonyms.py` drops those. Returning one at random is the
    confident-wrong-answer failure the whole exact-match design refuses, and it
    would be invisible: the shopper gets a real price for a real product, just
    not the one they meant.
    """
    generated = config["catalogues"]["lineage_b"]["generated_product_names"]
    assert generated, "the generated block is empty; run scripts/generate_synonyms.py"
    for term, key in generated.items():
        assert key in lineage_b_catalogue, f"{term!r} -> {key!r} is not a product"
    # 'bananas' means both bananas-130g and bananas-kg, so it must not be here.
    assert "bananas" not in generated


@pytest.mark.skipif(not DATASET.exists(), reason="dataset not present")
def test_the_generated_block_is_current() -> None:
    """
    Regenerating must be a no-op, or the committed table describes stale data.

    This is the same shape as the fixture-drift check in the pre-commit hook:
    a generated artefact that nobody regenerates silently stops matching the
    thing it was generated from.
    """
    import subprocess
    import sys

    result = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "generate_synonyms.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
