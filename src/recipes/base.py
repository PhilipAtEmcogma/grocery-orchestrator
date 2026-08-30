"""
The curated recipe catalogue (Req 2.9, Pilot Task 15).

Req 2.9 says a meal plan SHALL select meals from a curated catalogue rather
than composing them freely, with the model choosing recipe ids and
deterministic code owning scaling, dietary verification and totals.

THE CATALOGUE IS NOT WIRED INTO THE GRAPH, AND THIS MODULE EXPLAINS WHY.
A recipe is only usable for planning if its ingredients can be PRICED, and
pricing needs every ingredient to resolve to a product the retrieval layer
actually returns. Measured against the two datasets this project holds:

    175 recipes, 451 distinct ingredients
    best recipe:   75% of ingredients costable
    median recipe: 15%
    recipes at 90% or better: ZERO -- under any staples assumption

The two datasets were built for different jobs and do not meet. TheMealDB
recipes are international home cooking with a median of 11 ingredients each,
reaching for soy sauce, fish sauce, ginger, coriander, cumin and paprika. The
product catalogue is 300 items per store across 17 categories, weighted to
fresh produce, meat and dairy, with no spice rack, no condiments and no long
tail. `water` is in 42 recipes and is not a grocery product at all.

WHY THAT BLOCKS THE FEATURE RATHER THAN MERELY DEGRADING IT. A plan built from
a recipe whose ingredients are 15% priced would state a payable total computed
from a sixth of what the shopper has to buy. That number is not an estimate,
it is wrong, and `within_budget` derived from it is a false promise -- the
single failure mode this codebase is built to prevent. Refusing to plan is the
honest outcome, and shipping the planner without the data would replace an
honest refusal with a confident lie.

So this module delivers the catalogue, the dietary classification and the
COVERAGE MEASUREMENT, and stops there. `scripts/check_recipe_coverage.py`
reports the number and `tests/test_recipes.py` fails the build if it silently
becomes good enough to proceed without anyone noticing -- the same forcing
shape the Scan ceiling used for Pilot Task 6b, pointed the other way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RecipeIngredient:
    """
    One line of a recipe.

    `key` is the source's own normalised form ("plain flour"), `name` is what it
    displays ("Plain Flour"), and `measure` is free text ("120g", "1 tbsp").
    The measure is deliberately NOT parsed here: scaling a recipe is Req 2.9
    work that only matters once ingredients can be priced, and a parser written
    against measures nobody can cost would be untested speculation.
    """

    key: str
    name: str
    measure: str


@dataclass(frozen=True, slots=True)
class Recipe:
    """
    One recipe, as the catalogue holds it.

    `instructions`, images and video links from the source are deliberately NOT
    carried. ACQUISITION-RISK.md 8 condition 7 keeps stored data to the facts a
    question needs, and a field that does not exist cannot be published by
    mistake -- the same rule `RawOffer` follows for prices.
    """

    recipe_id: str
    name: str
    category: str
    area: str
    ingredients: tuple[RecipeIngredient, ...]
    attribution: str

    @property
    def ingredient_keys(self) -> tuple[str, ...]:
        return tuple(i.key for i in self.ingredients)


class RecipeRepository(Protocol):
    """
    Where recipes come from.

    A Protocol for the same reason `PriceRepository` is one: the fixture-backed
    implementation must be able to run the whole thing with no AWS account, and
    a DynamoDB implementation must satisfy identical tests.
    """

    def all_recipes(self) -> list[Recipe]: ...

    def get(self, recipe_id: str) -> Recipe | None: ...


# --------------------------------------------------------------------------
# Dietary classification
# --------------------------------------------------------------------------

# Source category -> the Lineage A product categories a recipe of that kind
# contains. Used to answer "can a vegetarian eat this" WITHOUT trusting the
# label, in the same fail-closed direction ingestion/lineage_b.py uses.
#
# `Vegetarian` and `Vegan` are the source's own claims and are NOT trusted as
# safe on their own -- see recipe_excluded_categories().
CATEGORY_IMPLIES: dict[str, frozenset[str]] = {
    "Beef": frozenset({"meat"}),
    "Chicken": frozenset({"meat"}),
    "Lamb": frozenset({"meat"}),
    "Pork": frozenset({"meat"}),
    "Goat": frozenset({"meat"}),
    "Seafood": frozenset({"seafood"}),
    "Vegetarian": frozenset(),
    "Vegan": frozenset(),
    "Dessert": frozenset(),
    "Starter": frozenset(),
    "Breakfast": frozenset(),
    "Side": frozenset(),
    "Miscellaneous": frozenset(),
    "Pasta": frozenset(),
}


def recipe_excluded_categories(recipe: Recipe) -> frozenset[str]:
    """
    Which dietary categories this recipe contains, judged from its INGREDIENTS.

    THE SOURCE CATEGORY IS A HINT, NOT A CONTROL, and this is the same lesson
    `ingestion/lineage_b.py` learned from the product data: a `Frozen Foods`
    row was a whole chicken, and a `Breakfast Cereals` row was baked ham. A
    recipe filed as `Vegetarian` that lists fish sauce is not vegetarian, and
    the label is exactly the thing that would hide it.

    So the category and the ingredient names are UNIONED, never traded off. The
    category can only add restrictions; the ingredients can only add
    restrictions; neither can clear one. An over-restriction costs a shopper a
    recipe they could have eaten, and an under-restriction serves a vegetarian
    chicken -- not symmetric, so the union is the only safe combination.
    """
    from ingestion.lineage_b import MEAT_TERMS, SEAFOOD_TERMS, _words

    excluded = set(CATEGORY_IMPLIES.get(recipe.category, frozenset()))
    for ingredient in recipe.ingredients:
        words = _words(f"{ingredient.key} {ingredient.name}")
        if words & SEAFOOD_TERMS:
            excluded.add("seafood")
        if words & MEAT_TERMS:
            excluded.add("meat")
    return frozenset(excluded)
