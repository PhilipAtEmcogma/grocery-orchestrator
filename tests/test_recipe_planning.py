"""
Recipes -> PlanDraft -> costed MealPlan (Req 2.9, Pilot Task 15b).

The pack multiplier is the only number this path invents, and everything
downstream trusts it: `assemble_plan` multiplies it by the cited price, then
aggregates across meals and rounds up to whole packs. A wrong multiplier
produces a plan whose arithmetic is internally consistent and wrong against
reality — which is the exact failure `assert_arithmetic` was rewritten to catch.
So most of this file is about that one conversion.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.graph.nodes.plan import assemble_plan
from src.recipes import CuratedRecipeRepository, Recipe, RecipeIngredient
from src.recipes.planning import RecipeNotCostable, recipes_to_draft
from src.retrieval.filters import pin_to_fixture_snapshot
from src.retrieval.memory import InMemoryPriceRepository
from src.schemas.contract import Citation, SourceRef


def _recipe(*ingredients: RecipeIngredient, serves: int = 2, name: str = "Test Dish") -> Recipe:
    return Recipe(
        recipe_id="test#1",
        name=name,
        category="Vegetarian",
        area="NZ",
        ingredients=ingredients,
        attribution="",
        serves=serves,
    )


def _wire(repo: InMemoryPriceRepository, recipe: Recipe):
    """Resolve every ingredient to a cheapest record, as retrieval would."""
    refs: dict[str, str] = {}
    records: dict = {}
    citations: dict[str, Citation] = {}
    for index, ingredient in enumerate(recipe.ingredients, start=1):
        key = repo.resolve_product_key(ingredient.key)
        assert key, f"fixture catalogue has no {ingredient.key!r}"
        record = repo.cheapest_for_product(key, limit=1)[0]
        ref = f"c{index}"
        refs[ingredient.key] = ref
        records[ref] = record
        citations[ref] = Citation(
            ref=ref,
            store=record.store,
            store_location=record.store_location,
            product_name=record.display_name,
            price_nzd=record.price_nzd,
            unit=record.unit,
            unit_price_nzd=record.unit_price_nzd,
            on_special=record.on_special,
            valid_date=date.fromisoformat(record.valid_date),
            source=SourceRef(
                table="grocery-products-dev",
                pk=record.store_key,
                sk=record.product_key,
            ),
        )
    return refs, records, citations


# ---------------------------------------------------------------- scaling


def test_a_recipe_is_scaled_to_the_household_not_to_its_own_serves() -> None:
    """
    Req 2.6: every meal serves the stated household.

    A recipe written for 2 feeding a flat of 5 is scaled by 5. The recipe's own
    `serves` is the basis its quantities are stated against — per-serving — so
    the multiplier is the household, and using the ratio instead would
    double-count it.
    """
    pin_to_fixture_snapshot()
    repo = InMemoryPriceRepository()
    recipe = _recipe(RecipeIngredient("butter", "Butter", "20g", grams_per_serving=20), serves=2)
    refs, records, _ = _wire(repo, recipe)

    for household in (1, 3, 5):
        draft = recipes_to_draft([recipe], household_size=household, refs=refs, records=records)
        meal = draft.meals[0]
        assert meal.serves == household
        # 20g per serving against a 500g pack.
        assert meal.ingredients[0].packs == Decimal(20 * household) / Decimal(500)


def test_a_count_ingredient_converts_to_packs_directly() -> None:
    """
    Three eggs is three packs, not three grams' worth.

    The catalogue records sold-each goods with `pack_grams == 1`, a sentinel
    meaning "one unit", not "weighs a gram". Dividing a count by it would be
    right by accident; dividing a WEIGHT by it is the mistake that once wrote
    `unit_price_nzd` of "2490.00" against a $2.49 broccoli.
    """
    pin_to_fixture_snapshot()
    repo = InMemoryPriceRepository()
    recipe = _recipe(RecipeIngredient("eggs", "Eggs", "x3", count_per_serving=3), serves=2)
    refs, records, _ = _wire(repo, recipe)

    draft = recipes_to_draft([recipe], household_size=2, refs=refs, records=records)
    assert draft.meals[0].ingredients[0].packs == Decimal(6)


def test_a_weight_ingredient_against_a_sold_each_pack_takes_one_pack() -> None:
    """
    We know how much is wanted and not what a unit weighs.

    One pack is the honest answer; deriving a fraction would require inventing
    a weight, and an invented number in the multiplier reaches the shopper as a
    price.
    """
    pin_to_fixture_snapshot()
    repo = InMemoryPriceRepository()
    recipe = _recipe(
        RecipeIngredient("broccoli", "Broccoli", "150g", grams_per_serving=150), serves=2
    )
    refs, records, _ = _wire(repo, recipe)
    assert records["c1"].pack_grams == 1, "broccoli is sold each in the fixtures"

    draft = recipes_to_draft([recipe], household_size=4, refs=refs, records=records)
    assert draft.meals[0].ingredients[0].packs == Decimal(1)


# ---------------------------------------------------------------- refusal


def test_an_uncostable_ingredient_raises_rather_than_being_dropped() -> None:
    """
    A silently shortened plan is the worst outcome available here.

    It satisfies every arithmetic check, fits the budget comfortably, and feeds
    the household less than it was asked to. `min_budget_used` exists because
    under-feeding passes a budget test that over-spending fails, so a dropped
    ingredient would look like a particularly good plan.
    """
    recipe = _recipe(RecipeIngredient("saffron", "Saffron", "1g", grams_per_serving=1), serves=2)
    with pytest.raises(RecipeNotCostable, match="saffron"):
        recipes_to_draft([recipe], household_size=2, refs={}, records={})


def test_no_recipes_is_an_error_not_an_empty_plan() -> None:
    with pytest.raises(ValueError, match="no recipes"):
        recipes_to_draft([], household_size=2, refs={}, records={})


# ---------------------------------------------------------------- end to end


def test_a_curated_recipe_costs_through_the_existing_pipeline() -> None:
    """
    THE POINT OF PRODUCING A `PlanDraft` RATHER THAN A NEW PLAN TYPE.

    `assemble_plan`, `validate_plan`, `assert_arithmetic` and the bounded repair
    loop were each hardened by a real defect. Recipe planning reuses all of it
    by speaking the same shape, so there is no second implementation to drift —
    and the two totals still mean what they mean: `total_nzd` is value consumed
    at fractional packs, `payable_total_nzd` is whole packs at shelf price.
    """
    pin_to_fixture_snapshot()
    repo = InMemoryPriceRepository()
    recipe = next(
        r
        for r in CuratedRecipeRepository().all_recipes()
        if all(repo.resolve_product_key(i.key) for i in r.ingredients)
    )
    refs, records, citations = _wire(repo, recipe)

    draft = recipes_to_draft([recipe], household_size=3, refs=refs, records=records)
    plan = assemble_plan(
        draft,
        citations,
        household_size=3,
        days=1,
        budget_nzd=Decimal("60"),
        exclusions=[],
        repair_attempts=0,
    )

    assert plan.meals[0].name == recipe.name
    assert plan.meals[0].serves == 3
    # Payable is whole packs, so it is never below value consumed.
    assert plan.payable_total_nzd >= plan.total_nzd
    assert plan.within_budget is True


def test_the_draft_carries_no_price_field_at_all() -> None:
    """
    Invariant 1 by construction, restated for this path.

    The model's contribution to a recipe plan is a list of ids; there is no
    field it could put a number in even if it tried.
    """
    from src.prompts.meal_plan import DraftIngredient, DraftMeal, PlanDraft

    for model in (DraftIngredient, DraftMeal, PlanDraft):
        for field in model.model_fields:
            assert "price" not in field.lower()
            assert "cost" not in field.lower()
            assert "total" not in field.lower()
