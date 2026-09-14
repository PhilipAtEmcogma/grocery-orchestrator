"""
The curated recipe catalogue (Req 2.9, Pilot Task 15).

Req 2.9 says a meal plan SHALL select meals from a curated catalogue rather
than composing them freely, with the model choosing recipe ids and
deterministic code owning scaling, dietary verification and totals.

THE CATALOGUE IS NOT WIRED INTO THE GRAPH, AND THIS MODULE EXPLAINS WHY.
A recipe is only usable for planning if its ingredients can be PRICED, and
pricing needs every ingredient to resolve to a product the retrieval layer
actually returns. Measured 2026-08-31 over 175 recipes and 451 distinct
ingredients, **against BOTH product catalogues, because a coverage number is
only worth something if it does not depend on which file was open**:

    catalogue                              best   median   at 100%
    datasets/  (528 products, 2,939 rows)   75%     17%      ZERO
    fixtures/  ( 26 products,   152 rows)   75%     12%      ZERO

Zero at 90% or better either way, under any staples assumption.

THE FIGURES USED TO COME FROM THE FIXTURE CATALOGUE ALONE, while this
docstring described the real one -- `scripts/check_recipe_coverage.py`
resolved through `InMemoryPriceRepository()`, which defaults to the 26-product
seed file, and its output named neither. A blocking decision on Req 2.9 rested
on an instrument pointed at the wrong data and survived only because the
answer happened to be the same. That is luck, not evidence. The script now
names its catalogue in every run and refuses to gate from the fixture one;
`tests/test_recipes.py` asserts both catalogues agree. (Audit finding D3,
2026-08-30.)

The two datasets were built for different jobs and do not meet. TheMealDB
recipes are international home cooking with a median of 11 ingredients each,
reaching for soy sauce, fish sauce, ginger, coriander, cumin and paprika. The
product catalogue is weighted to fresh produce, meat and dairy, with no spice
rack, no condiments and no long tail. `water` is in 42 recipes and is not a
grocery product at all.

WHY THAT BLOCKS THE FEATURE RATHER THAN MERELY DEGRADING IT. A plan built from
a recipe whose ingredients are 17% priced would state a payable total computed
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

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RecipeIngredient:
    """
    One line of a recipe.

    `key` is the lookup term -- what `resolve_product_key` is asked for. `name`
    is what it displays and `measure` is free text.

    `grams_per_serving` and `count_per_serving` are what make a CURATED recipe
    costable where an imported one is not. Exactly one is set: grams for things
    sold by weight, a count for things bought whole (eggs, a loaf) where grams
    would be a fiction. Code multiplies by servings; a recipe never states a
    total and never states a price, which is Req 2.9's whole division of labour.

    Both are None for an imported recipe (TheMealDB carries free-text measures
    like "1 tbsp"), which is one of several reasons those cannot be planned
    from -- see the module docstring.
    """

    key: str
    name: str
    measure: str
    grams_per_serving: int | None = None
    count_per_serving: int | None = None

    @property
    def is_costable(self) -> bool:
        """A quantity code can scale, as opposed to prose a human would read."""
        return self.grams_per_serving is not None or self.count_per_serving is not None


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
    #: Servings the quantities are stated for. Code scales from here to the
    #: household size; Req 2.6 requires every meal to serve the household.
    serves: int = 1

    @property
    def ingredient_keys(self) -> tuple[str, ...]:
        return tuple(i.key for i in self.ingredients)

    @property
    def is_costable(self) -> bool:
        """
        Every ingredient carries a scalable quantity.

        A recipe that is only PARTLY costable is not partly usable: its payable
        total would be computed from part of the shopping list, and
        `within_budget` derived from that is a false promise. So this is all or
        nothing, deliberately.
        """
        return bool(self.ingredients) and all(i.is_costable for i in self.ingredients)


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


def recipe_categories(recipe: Recipe, category_of: Callable[[str], str | None]) -> frozenset[str]:
    """
    The product categories this recipe actually requires.

    DERIVED FROM THE PRODUCTS, NOT FROM THE RECIPE'S NAME OR LABEL, and that is
    the whole point. `recipe_excluded_categories` reads the source category and
    scans ingredient names for meat and seafood words, which is the right tool
    for an IMPORTED recipe whose ingredients cannot be resolved. For a curated
    recipe every ingredient resolves to a real product with a real category, so
    the honest answer is available and a guess is not needed.

    It also catches what the name scan cannot. "Scrambled Eggs on Toast" and
    "Broccoli and Cheese Pasta" contain no meat or seafood word, so the name
    scan reports them as excluding nothing — and a vegan would be served both.
    Resolving `eggs` to `chilled` and `grated cheddar cheese` to `dairy` is what
    makes the dietary filter true rather than approximately true.

    An ingredient that does not resolve yields `None` and is IGNORED here, not
    treated as safe: `is_viable_for` refuses such a recipe outright, because a
    recipe with an unpriceable ingredient cannot be planned at all.
    """
    found: set[str] = set()
    for ingredient in recipe.ingredients:
        category = category_of(ingredient.key)
        if category:
            found.add(category)
    return frozenset(found)


def is_viable_for(
    recipe: Recipe,
    excluded: frozenset[str] | set[str],
    category_of: Callable[[str], str | None],
) -> bool:
    """
    Can this recipe be made at all, under these dietary exclusions?

    Two ways to fail, and both are refusals rather than substitutions:

    - an ingredient does not resolve, so the recipe cannot be costed and a plan
      containing it would state a total computed from part of the shopping list;
    - an ingredient's category is excluded, so making it would breach the
      exclusion. Swapping the ingredient is NOT an option here: a recipe with a
      substituted ingredient is a different recipe, and Req 2.9 exists so that
      the model picks from a catalogue rather than composing freely.
    """
    for ingredient in recipe.ingredients:
        category = category_of(ingredient.key)
        if category is None or category in excluded:
            return False
    return True


# --------------------------------------------------------------------------
# Preference matching
# --------------------------------------------------------------------------

# Preference term -> the product categories it NAMES.
#
# Only words that name a category belong here. A term for a specific food --
# "chicken", "tuna", "prawns" -- needs no entry, because it is matched against
# the recipe's own words instead, and adding it would be a second place to
# maintain the same fact.
#
# THIS IS NOT `SUPPORTED_EXCLUSIONS` AND MUST NOT BE MERGED WITH IT, however
# similar the two tables look. There, "vegetarian" -> {meat, seafood} means
# REMOVE those categories. A preference inverts the operation but not the
# mapping, so reusing that table would read "vegetarian" as a request to build
# the plan around meat and fish. Two tables that differ in one entry's meaning
# are worse than two tables.
#
# There is also no fail-closed obligation here, which is the deeper reason they
# stay apart. `map_exclusions` reports what it could not map and the graph
# refuses the turn on a SAFETY ground; an unmatched preference term simply falls
# through this table to word matching. A preference can still cause a refusal --
# a preferred food we cannot match to any product refuses with
# PREFERENCE_UNAVAILABLE (retrieval, checked in the graph, not here) -- but that
# is an AVAILABILITY refusal, never a safety one. This matcher's own answer for
# an unmatched term is "no", reported as an unmet preference; it cannot make a
# plan unsafe and does not itself refuse.
PREFERENCE_CATEGORIES: dict[str, frozenset[str]] = {
    "seafood": frozenset({"seafood"}),
    "fish": frozenset({"seafood"}),
    "shellfish": frozenset({"seafood"}),
    "meat": frozenset({"meat"}),
    "dairy": frozenset({"dairy"}),
    "produce": frozenset({"produce"}),
    "vegetables": frozenset({"produce"}),
    "vegetable": frozenset({"produce"}),
    "veg": frozenset({"produce"}),
    "veggies": frozenset({"produce"}),
    "fruit": frozenset({"produce"}),
    "frozen": frozenset({"frozen"}),
    "bakery": frozenset({"bakery"}),
    "bread": frozenset({"bakery"}),
    "pantry": frozenset({"pantry"}),
}


def recipe_matches_preference(recipe: Recipe, term: str, categories: frozenset[str]) -> bool:
    """
    Would this recipe satisfy a shopper who asked for `term`?

    `categories` is what the recipe's ingredients ACTUALLY resolve to, from
    `recipe_categories` — the same product-derived answer the dietary filter
    uses, for the same reason. "Prawn and Rice Stir Fry" carries no ingredient
    called "seafood" and no such word in its name, so a request for seafood
    reaches it only through the category its prawns resolve to.

    Then, and only then, the words. "chicken" names a product rather than a
    category and is matched against the recipe's name and ingredient terms,
    which is how "Chicken and Rice Bake" answers it. `_words` is reused so
    plurals fold the same way they do in classification: "prawns" matching
    `cooked peeled prawns` is the case that motivated that helper.

    MATCHING IS DELIBERATELY LOOSE, AND THE ASYMMETRY RUNS THE OPPOSITE WAY TO
    `is_viable_for`. Any word of the term may match. An over-match ranks a
    recipe the shopper did not quite ask for slightly too high; an under-match
    tells them their request could not be met when it could. Neither is a
    safety outcome — a preference cannot put food in a basket that the dietary
    filter has already excluded, because that filter runs first and removes the
    recipe outright. So the cheap-failure direction here is to match more, which
    is the reverse of every other classifier in this package, and the reason is
    that this one guards disappointment rather than harm.
    """
    from ingestion.lineage_b import _words

    wanted = _words(term)
    if not wanted:
        return False

    for word in wanted:
        if PREFERENCE_CATEGORIES.get(word, frozenset()) & categories:
            return True

    haystack = _words(recipe.name)
    for ingredient in recipe.ingredients:
        haystack |= _words(f"{ingredient.key} {ingredient.name}")
    return bool(wanted & haystack)


def preference_terms_met(terms: list[str], products: list[tuple[str, str]]) -> set[str]:
    """
    Which of `terms` the products in a FINISHED plan actually answer.

    `products` is (name, category) per cited product.

    WHY THIS EXISTS ALONGSIDE `recipe_matches_preference`, WHICH IS A FAIR
    QUESTION given that file's own warning about two matchers disagreeing. They
    answer different questions at different granularities, and the plan needs
    both:

      * `recipe_matches_preference` asks "would this RECIPE satisfy the
        shopper", and is what orders the shortlist before anything is chosen.
      * this asks "does the BASKET they are actually being handed contain it",
        which is the only question worth asking once the model has selected and
        the budget trim has run.

    The shortlist answer is not a safe proxy for the plan answer, and assuming
    it was is how the first version of the unmet-preference notice got this
    wrong: `_cost_within_budget` drops the meal with the HIGHEST MARGINAL COST,
    which is very often the preferred one, so a chicken recipe could be offered,
    reported as met, and then trimmed out of the plan the shopper read. Silence
    about a dropped request, one layer below where it was first fixed.

    It also covers the FREE-COMPOSITION path, which has no recipes at all. A
    recipe-id check there would report every preference unmet even when the
    composed basket does contain the food, which would be a false claim in the
    opposite direction.

    What must NOT diverge is the vocabulary, and it does not: both read
    `PREFERENCE_CATEGORIES` and both fold plurals through `_words`.
    """
    from ingestion.lineage_b import _words

    met: set[str] = set()
    for term in terms:
        wanted = _words(term)
        if not wanted:
            continue
        for name, category in products:
            named_category = any(
                category in PREFERENCE_CATEGORIES.get(word, frozenset()) for word in wanted
            )
            if named_category or (wanted & _words(name)):
                met.add(term)
                break
    return met
