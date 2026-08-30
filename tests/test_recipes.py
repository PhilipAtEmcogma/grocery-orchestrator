"""
The curated recipe catalogue (Req 2.9, Pilot Task 15).

Two things are tested here and they pull in opposite directions. The catalogue
and its dietary classification are real and must be correct. The COVERAGE is a
blocker, and the test for it is written to fail when the blocker LIFTS -- the
same forcing shape Pilot Task 6b used for the Scan ceiling, pointed the other
way, so "not enough data yet" cannot quietly become permanent.
"""

from __future__ import annotations

import pytest

from src.recipes import (
    ASSUMED_ON_HAND,
    FixtureRecipeRepository,
    Recipe,
    RecipeIngredient,
    coverage,
    recipe_excluded_categories,
    usable_recipes,
)
from src.retrieval.memory import InMemoryPriceRepository

# What Req 2.9 needs before a plan can be composed from recipes: every
# ingredient priced, and enough recipes to choose between.
REQUIRED_RATIO = 1.0
RECIPES_NEEDED_TO_PLAN = 20


@pytest.fixture(scope="module")
def recipes() -> list[Recipe]:
    return FixtureRecipeRepository().all_recipes()


def _resolver():
    repo = InMemoryPriceRepository()

    def resolve(term: str) -> str | None:
        for candidate in (term, f"{term}s", term[:-1] if term.endswith("s") else term):
            key = repo.resolve_product_key(candidate)
            if key:
                return key
        return None

    return resolve


# ---------------------------------------------------------------- catalogue


def test_the_catalogue_loads(recipes: list[Recipe]) -> None:
    assert len(recipes) == 175
    assert all(r.recipe_id and r.name and r.ingredients for r in recipes)


def test_recipe_ids_are_unique(recipes: list[Recipe]) -> None:
    """A duplicate id makes `get()` ambiguous and a model's selection unresolvable."""
    ids = [r.recipe_id for r in recipes]
    assert len(ids) == len(set(ids))


def test_get_returns_the_named_recipe(recipes: list[Recipe]) -> None:
    repo = FixtureRecipeRepository()
    target = recipes[0]
    assert repo.get(target.recipe_id) == target
    assert repo.get("mealdb#does-not-exist") is None


def test_no_instructions_or_media_are_carried(recipes: list[Recipe]) -> None:
    """
    ACQUISITION-RISK.md 8 condition 7: store the facts a question needs.

    The source supplies instructions, a thumbnail and a YouTube link. None is
    needed to price a basket, and a field that does not exist cannot be
    published by mistake -- the rule `RawOffer` follows for prices.
    """
    assert not hasattr(recipes[0], "instructions")
    assert not hasattr(recipes[0], "thumbnail_url")


# ---------------------------------------------------------------- dietary


def test_a_vegetarian_labelled_recipe_with_fish_is_still_seafood() -> None:
    """
    The source category is a hint, not a control.

    `ingestion/lineage_b.py` learned this from the product data -- a
    `Frozen Foods` row was a whole chicken. A recipe FILED as Vegetarian that
    lists fish sauce is not vegetarian, and the label is the thing that would
    hide it. Category and ingredients are unioned; neither can clear the other.
    """
    recipe = Recipe(
        recipe_id="test#1",
        name="Suspect Stir Fry",
        category="Vegetarian",
        area="Thai",
        ingredients=(
            RecipeIngredient(key="fish sauce", name="Fish Sauce", measure="2 tbsp"),
            RecipeIngredient(key="rice", name="Rice", measure="200g"),
        ),
        attribution="",
    )
    assert "seafood" in recipe_excluded_categories(recipe)


def test_a_meat_category_restricts_even_when_no_ingredient_says_so() -> None:
    recipe = Recipe(
        recipe_id="test#2",
        name="Mystery Roast",
        category="Beef",
        area="British",
        ingredients=(RecipeIngredient(key="potatoes", name="Potatoes", measure="1kg"),),
        attribution="",
    )
    assert "meat" in recipe_excluded_categories(recipe)


def test_a_genuinely_vegetarian_recipe_excludes_nothing() -> None:
    recipe = Recipe(
        recipe_id="test#3",
        name="Tomato Pasta",
        category="Vegetarian",
        area="Italian",
        ingredients=(
            RecipeIngredient(key="pasta", name="Pasta", measure="400g"),
            RecipeIngredient(key="tomatoes", name="Tomatoes", measure="400g"),
        ),
        attribution="",
    )
    assert recipe_excluded_categories(recipe) == frozenset()


def test_every_real_recipe_classifies_without_error(recipes: list[Recipe]) -> None:
    """
    Run it over all 175, not a sample.

    A classifier is only as good as its coverage of the data it will meet, and
    an unexpected category must not raise in the middle of a turn.
    """
    known = {"meat", "seafood", "dairy", "chilled"}
    for recipe in recipes:
        assert recipe_excluded_categories(recipe) <= known, recipe.name


def test_the_meat_categories_are_all_caught(recipes: list[Recipe]) -> None:
    """Every Beef/Chicken/Lamb/Pork recipe must restrict for a vegetarian."""
    for recipe in recipes:
        if recipe.category in {"Beef", "Chicken", "Lamb", "Pork"}:
            assert "meat" in recipe_excluded_categories(recipe), recipe.name
        if recipe.category == "Seafood":
            assert "seafood" in recipe_excluded_categories(recipe), recipe.name


# ---------------------------------------------------------------- coverage


def test_assumed_on_hand_stays_tiny() -> None:
    """
    Every entry is a cost the plan does not show the shopper.

    A generous list is a way of making a budget look achievable by ignoring
    what it leaves out. It was measured before being trusted: widening it to a
    full spice rack and pantry moved the count of usable recipes from zero to
    zero, so the gap is not staples and pretending otherwise would hide that.
    """
    assert len(ASSUMED_ON_HAND) <= 10
    assert {"water", "salt", "pepper"} <= ASSUMED_ON_HAND
    for banned in ("sugar", "butter", "oil", "flour", "milk", "soy sauce"):
        assert banned not in ASSUMED_ON_HAND, f"{banned!r} is a real cost, not a given"


def test_coverage_is_measured_against_the_catalogue_that_is_loaded(
    recipes: list[Recipe],
) -> None:
    covs = coverage(recipes, _resolver())
    assert len(covs) == len(recipes)
    assert all(0.0 <= c.ratio <= 1.0 for c in covs)
    # Missing ingredients are named, not just counted: a number cannot be acted
    # on and a list can.
    assert any(c.missing for c in covs)


def test_task_15_is_blocked_by_data_and_will_say_when_it_is_not(
    recipes: list[Recipe],
) -> None:
    """
    THE FORCING TEST. It fails when the blocker LIFTS, not when it persists.

    Req 2.9 needs plans composed from recipes with provable payable totals, and
    a recipe is only usable if EVERY ingredient can be priced -- a total
    computed from part of a shopping list is a number the shopper cannot spend
    to, and `within_budget` derived from it is a false promise.

    Measured today: 175 recipes, best 75% costable, median ~12%, and ZERO at
    100% under any staples assumption. The datasets were built for different
    jobs: TheMealDB is international home cooking reaching for soy sauce, fish
    sauce, ginger and coriander; the product catalogue is 300 fresh-weighted
    items per store with no spice rack and no long tail.

    So the planner is deliberately NOT wired to the catalogue. When the product
    data grows enough to price whole recipes, this test fails and says so --
    which is the moment to build it, and the moment somebody would otherwise
    have to notice by chance.
    """
    covs = coverage(recipes, _resolver())
    usable = usable_recipes(covs, minimum_ratio=REQUIRED_RATIO)

    assert len(usable) < RECIPES_NEEDED_TO_PLAN, (
        f"{len(usable)} recipes are now fully costable, at or past the "
        f"{RECIPES_NEEDED_TO_PLAN} a planner needs to choose between. The data "
        "blocker on Pilot Task 15 has lifted: wire recipe-constrained planning "
        "(Req 2.9) and delete this assertion. See src/recipes/base.py."
    )
