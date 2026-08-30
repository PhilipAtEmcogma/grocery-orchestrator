"""
The curated recipe catalogue (Req 2.9, Pilot Task 15b).

Separate from `test_recipes.py`, which is about the IMPORTED catalogue and the
coverage gate that proves it unusable. This file is about the recipes the
planner actually selects from.

Everything here resolves against the REAL catalogue in `datasets/`, not the 26
fixture products: curated recipes are written against the data the service
serves, and a test that resolved them against fixtures would measure the wrong
catalogue and pass for the wrong reason.
"""

from __future__ import annotations

import pytest

from src.graph.dietary import map_exclusions
from src.recipes import (
    CuratedRecipeRepository,
    Recipe,
    is_viable_for,
    recipe_categories,
    recipe_excluded_categories,
)
from src.recipes.catalogue import DATASET_DIR, load_dataset_catalogue
from src.retrieval.memory import load_synonyms
from src.schemas.contract import LITERAL_MONEY

needs_dataset = pytest.mark.skipif(not DATASET_DIR.exists(), reason="dataset not present")


@pytest.fixture(scope="module")
def curated() -> list[Recipe]:
    return CuratedRecipeRepository().all_recipes()


@pytest.fixture(scope="module")
def category_of():
    """
    Resolve an ingredient term to its product category, as the service would.

    Built from the real catalogue through the same synonym table
    `resolve_product_key` uses, so what this test resolves is what a turn
    resolves.

    The load lives in `src/recipes/catalogue.py` rather than here. It used to
    be a copy, byte-for-byte equivalent to the one in
    `Philip_demo/11_recipe_coverage_gate.py` apart from mapping to a category
    instead of a key -- and equivalent copies are the dangerous kind, because
    nothing is wrong and so nothing flags the day one of them is tuned.
    """
    catalogue = load_dataset_catalogue()
    assert catalogue is not None, "guarded by needs_dataset"
    synonyms = load_synonyms()

    return lambda term: catalogue.category_for(term, synonyms)


# ---------------------------------------------------------------- shape


def test_the_catalogue_loads_and_ids_are_unique(curated: list[Recipe]) -> None:
    assert len(curated) >= 20, "a planner needs enough recipes to choose between"
    ids = [r.recipe_id for r in curated]
    assert len(ids) == len(set(ids))
    assert all(r.serves >= 1 for r in curated)


def test_every_recipe_is_costable(curated: list[Recipe]) -> None:
    """Every ingredient carries a quantity code can scale — grams or a count."""
    for recipe in curated:
        assert recipe.is_costable, recipe.name
        for ingredient in recipe.ingredients:
            has_grams = ingredient.grams_per_serving is not None
            has_count = ingredient.count_per_serving is not None
            assert has_grams != has_count, f"{recipe.name}/{ingredient.key}: set exactly one"


def test_no_recipe_states_a_price(curated: list[Recipe]) -> None:
    """
    Invariant 1 reaches the recipe catalogue too.

    A recipe is data a human wrote, so nothing stops a well-meaning edit adding
    "serves 4 for about $12". Every figure is computed from retrieved prices; a
    number here would be a price no citation backs.
    """
    for recipe in curated:
        assert not LITERAL_MONEY.search(recipe.name), recipe.name
        for ingredient in recipe.ingredients:
            assert not LITERAL_MONEY.search(ingredient.name), ingredient.name


# ---------------------------------------------------------------- pricing


@needs_dataset
def test_every_ingredient_resolves(curated: list[Recipe], category_of) -> None:
    """
    THE PROPERTY THE WHOLE APPROACH RESTS ON.

    A recipe naming something the shops do not stock cannot be costed, and a
    plan containing it would state a payable total computed from part of the
    shopping list. That is exactly what made the 175 imported recipes unusable
    — zero of them fully priceable. Writing our own only helps while this stays
    true, so it is asserted rather than assumed.
    """
    unresolved = [
        (r.recipe_id, i.key) for r in curated for i in r.ingredients if category_of(i.key) is None
    ]
    assert not unresolved, f"ingredients the catalogue cannot price: {unresolved}"


# ---------------------------------------------------------------- dietary


@needs_dataset
def test_dietary_content_comes_from_products_not_from_names(
    curated: list[Recipe], category_of
) -> None:
    """
    THE GAP THE NAME SCAN LEAVES, and why `recipe_categories` exists.

    `recipe_excluded_categories` scans ingredient NAMES for meat and seafood
    words — the right tool for an imported recipe whose ingredients cannot be
    resolved. It reports "Scrambled Eggs on Toast" and "Broccoli and Cheese
    Pasta" as excluding nothing, because no meat word appears in either, and a
    vegan would be served both.

    Resolving the ingredients to real products gives the honest answer, and for
    a curated recipe that answer is always available.
    """
    by_name = {r.name: r for r in curated}

    eggs = by_name["Scrambled Eggs on Toast"]
    assert recipe_excluded_categories(eggs) == frozenset(), "the name scan sees nothing"
    assert "chilled" in recipe_categories(eggs, category_of), "the products say otherwise"

    cheese = by_name["Broccoli and Cheese Pasta"]
    assert recipe_excluded_categories(cheese) == frozenset()
    assert "dairy" in recipe_categories(cheese, category_of)


@needs_dataset
@pytest.mark.parametrize(
    ("term", "must_not_contain"),
    [
        ("vegetarian", {"meat", "seafood"}),
        ("vegan", {"meat", "seafood", "dairy", "chilled"}),
        ("dairy-free", {"dairy"}),
        ("no eggs", {"chilled"}),
        ("pescatarian", {"meat"}),
    ],
)
def test_a_viable_recipe_never_contains_an_excluded_category(
    curated: list[Recipe], category_of, term: str, must_not_contain: set[str]
) -> None:
    """
    Invariant 3, at the point where a recipe is CHOSEN.

    Selection has to honour the exclusion, not only the shopping list built
    afterwards: a recipe whose ingredient is excluded cannot be made, and
    substituting the ingredient would make it a different recipe — which is
    precisely what Req 2.9 stops the model doing.
    """
    excluded, unsupported = map_exclusions([term])
    assert not unsupported

    viable = [r for r in curated if is_viable_for(r, set(excluded), category_of)]
    assert viable, f"no recipe is viable for {term!r}"
    for recipe in viable:
        assert not (recipe_categories(recipe, category_of) & must_not_contain), recipe.name


@needs_dataset
def test_an_unresolvable_ingredient_makes_a_recipe_non_viable(category_of) -> None:
    """
    Two ways to be unusable, and both refuse rather than substitute.

    An ingredient that does not resolve is not "safe by default": the recipe
    cannot be costed, so a plan containing it would state a total derived from
    part of the shopping list.
    """
    from src.recipes import RecipeIngredient

    phantom = Recipe(
        recipe_id="test#1",
        name="Soy Braised Something",
        category="Vegan",
        area="NZ",
        ingredients=(RecipeIngredient("soy sauce", "Soy Sauce", "20g", grams_per_serving=20),),
        attribution="",
        serves=2,
    )
    assert category_of("soy sauce") is None, "the catalogue has no soy sauce; that is the premise"
    assert is_viable_for(phantom, set(), category_of) is False


@needs_dataset
def test_enough_recipes_survive_each_diet_to_build_a_varied_plan(
    curated: list[Recipe], category_of
) -> None:
    """
    A plan needs DISTINCT meals, so a diet with two viable recipes cannot make
    one — the planner would repeat itself or refuse.

    Vegan is the tight one, and deliberately so: `ingestion/lineage_b.py` maps
    the catalogue's combined "Fresh Milk & Plant Milk" category to `dairy`
    wholesale, because the source does not separate oat milk from cow's milk and
    over-excluding is the safe direction for a vegan. Vegan recipes are
    therefore built from produce, pantry and grains only. Per-product allergen
    tagging (legacy 11.7) is what lifts it; widening the category map would
    trade a safety property for a menu, which is the wrong way round.
    """
    for term in ("vegetarian", "vegan", "dairy-free", "pescatarian"):
        excluded, _ = map_exclusions([term])
        viable = sum(1 for r in curated if is_viable_for(r, set(excluded), category_of))
        assert viable >= 4, f"{term}: only {viable} viable recipes, too few for a varied plan"
