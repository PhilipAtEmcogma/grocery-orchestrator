"""
Recipes -> PlanDraft (Req 2.9, Pilot Task 15b).

THIS IS THE HALF REQ 2.9 IS ACTUALLY ABOUT. The requirement says the model
selects recipe ids and product citations while **deterministic code owns
scaling, dietary verification, arithmetic, and payable totals**. Everything
below is that code. The model's contribution is a list of recipe ids, and it
cannot state a quantity, a pack count or a price -- there is no field for one.

IT PRODUCES A `PlanDraft`, THE SAME SHAPE THE FREE-COMPOSITION PATH PRODUCES.
That is the design, not a shortcut: `assemble_plan`, `validate_plan`,
`assert_arithmetic`, `assert_grounded` and the bounded repair loop all already
operate on a draft, and every one of them was hardened by a defect this project
found the hard way. Recipe planning that emitted its own plan type would need
its own versions of all of it, and the second copy is the one that goes wrong.

So the only new arithmetic here is the conversion from a recipe's grams to a
pack multiplier, and it is the one thing this module has to get right.
"""

from __future__ import annotations

from decimal import Decimal

from src.prompts.meal_plan import DraftIngredient, DraftMeal, PlanDraft
from src.recipes.base import Recipe
from src.retrieval.base import PriceRecord

#: A count-based ingredient (three eggs) against a pack sold by count. The
#: catalogue records those with `pack_grams == 1`, the "sold each" sentinel, so
#: a count converts to packs directly rather than through a weight.
SOLD_EACH = 1


class RecipeNotCostable(ValueError):
    """An ingredient has no retrieved record, so the plan would have a hole in it."""


def recipe_to_meal(
    recipe: Recipe,
    *,
    household_size: int,
    refs: dict[str, str],
    records: dict[str, PriceRecord],
) -> DraftMeal:
    """
    One recipe, scaled to the household, as a price-free draft meal.

    `refs` maps an ingredient term to the citation ref chosen for it; `records`
    maps that ref to the retrieved record. Both are supplied by the caller
    because retrieval is the only thing allowed to produce either -- this module
    never reaches storage, which is what keeps the single path into generation
    intact.

    SERVES IS THE HOUSEHOLD, NOT THE RECIPE'S OWN. Req 2.6 requires every meal
    to serve the stated household, so a recipe written for 2 feeding a flat of 5
    is scaled by 5, not by 2. The recipe's `serves` is the basis the quantities
    are stated against, and dividing by it is what makes the scaling correct
    rather than merely proportional.
    """
    if household_size < 1:
        raise ValueError(f"household_size must be >= 1, got {household_size}")

    ingredients: list[DraftIngredient] = []
    for ingredient in recipe.ingredients:
        ref = refs.get(ingredient.key)
        record = records.get(ref) if ref else None
        if ref is None or record is None:
            raise RecipeNotCostable(
                f"{recipe.recipe_id}: no retrieved record for {ingredient.key!r}. "
                "A plan missing a price for one ingredient states a total the "
                "shopper cannot spend to."
            )

        packs = _packs_for(ingredient, record, household_size=household_size)
        ingredients.append(
            DraftIngredient(
                citation_ref=ref,
                packs=packs,
                # Display text only. Deliberately carries no money: it is
                # model-visible free text in the free-composition path and a
                # price here would be one no citation backs (Req 3.7).
                qty_display=f"{ingredient.measure} per serving",
                item=record.display_name,
            )
        )

    return DraftMeal(name=recipe.name, serves=household_size, ingredients=ingredients)


def _packs_for(ingredient, record: PriceRecord, *, household_size: int) -> Decimal:
    """
    How much of the cited pack this line uses.

    THE PACK MULTIPLIER IS THE ONE NUMBER THIS MODULE INVENTS, and everything
    downstream trusts it: `assemble_plan` multiplies it by the cited price to
    get a line cost, then aggregates and rounds up to whole packs. Getting it
    wrong produces a plan whose arithmetic is internally consistent and wrong
    against reality, which is the failure `assert_arithmetic` was rewritten to
    catch.

    Two cases, and conflating them is the trap:

    - By WEIGHT: total grams over the pack's grams. 500g of a 1kg pack is 0.5.
    - SOLD EACH (`pack_grams == 1`): the pack IS one unit, so three eggs is
      three packs. Dividing by a gram figure here would produce a multiplier a
      thousand times too small -- the same sentinel confusion that once wrote
      `unit_price_nzd` of "2490.00" against a $2.49 broccoli.
    """
    if ingredient.count_per_serving is not None:
        return Decimal(ingredient.count_per_serving * household_size)

    grams = Decimal(ingredient.grams_per_serving * household_size)
    if record.pack_grams <= SOLD_EACH:
        # A weight-based ingredient against a pack sold by count. One pack is
        # the honest answer: we know how much is wanted and not what a unit
        # weighs, and guessing would put a fabricated weight into the totals.
        return Decimal(1)
    return grams / Decimal(record.pack_grams)


def recipes_to_draft(
    recipes: list[Recipe],
    *,
    household_size: int,
    refs: dict[str, str],
    records: dict[str, PriceRecord],
) -> PlanDraft:
    """
    Selected recipes as a draft the existing pipeline can cost and validate.

    Raises rather than dropping a recipe it cannot cost. A silently shortened
    plan is the worst outcome available here: it satisfies every arithmetic
    check, fits the budget comfortably, and feeds the household less than it
    was asked to -- which `min_budget_used` exists to catch precisely because
    under-feeding passes a budget test that over-spending fails.
    """
    if not recipes:
        raise ValueError("no recipes selected")

    return PlanDraft(
        meals=[
            recipe_to_meal(r, household_size=household_size, refs=refs, records=records)
            for r in recipes
        ],
        reasoning=(
            f"Composed from {len(recipes)} curated recipes, scaled to "
            f"{household_size}. Quantities and totals computed in code."
        ),
    )
