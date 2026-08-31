"""
Recipe retrieval and shortlisting (Req 2.9, Pilot Task 15c).

THE HALF THAT RUNS BEFORE THE MODEL. `retrieve_prices` calls `shortlist()` on a
meal_plan turn; `select_recipes` then offers the model only what survives it.

WHY THE SHORTLIST IS BUILT BEFORE SELECTION, NOT CHECKED AFTER IT. A model that
cannot see an uncostable recipe cannot select one. Validating a selection
afterwards would leave a failure to handle on every turn, and the handling would
be a repair loop over a problem code can simply remove. It is the same argument
`candidates_for_budget` makes for capping the candidate set rather than checking
a plan's total afterwards: constraining what a price-blind model chooses FROM is
the only way to keep its choice inside a budget.

THREE FILTERS, IN THIS ORDER, AND EACH IS A REFUSAL RATHER THAN A SUBSTITUTION.

1. COSTABLE. Every ingredient must resolve to a product retrieval returned. A
   recipe priced from four of its five ingredients states a payable total the
   shopper cannot spend to, and `within_budget` derived from it is a false
   promise -- the single failure this codebase exists to prevent. `is_costable`
   on the recipe is all-or-nothing for the same reason.

2. DIETARY-VIABLE, JUDGED FROM THE RESOLVED PRODUCTS. Not from the recipe's
   name, and not from its source category. `recipe_excluded_categories` scans
   ingredient names for meat and seafood words, which is right for an imported
   recipe whose ingredients cannot be resolved -- but it reports "Scrambled Eggs
   on Toast" as excluding nothing, and a vegan would be served it. Here every
   ingredient resolves to a real product with a real category, so the honest
   answer is available and a guess is not needed.

3. COSTED, AND ORDERED CHEAPEST FIRST -- BUT NOT FILTERED ON PRICE.

   THE FIRST VERSION FILTERED HERE, AT `budget / meals`, AND IT WAS WRONG.
   `assemble_plan` aggregates packs across meals and rounds up ONCE, so four
   recipes sharing onions and rice cost far less together than the sum of the
   four costed alone. A per-recipe cap therefore rejects on a number no plan
   ever pays: against the 26-product fixture catalogue it collapsed a 29-recipe
   shortlist to one, and the meal-plan eval read that as under-feeding, which is
   what it was.

   So the budget is enforced where it is actually knowable -- after the model has
   chosen, on the REAL aggregated total, by `select_recipes` trimming meals off
   the end of the selection until the plan fits. That is deterministic code
   owning the budget, which is what Req 2.9 asks for, and it is the same
   correction the free-composition path already made when it moved the budget
   check from consumption to money payable.

   `payable_nzd` is still computed and carried, because ordering the shortlist
   cheapest-first gives the trim something better than arbitrary to remove and
   gives the model a sensible default order.

WHAT THIS COSTS. 27 distinct ingredient keys across the 29 curated recipes, so
one `cheapest_for_product` per key -- bounded, and small next to the eight
category queries the free-composition path already makes. `MAX_INGREDIENT_LOOKUPS`
bounds it against a catalogue that grows, and the bound REFUSES rather than
truncating: a shortlist silently built from the first N keys would offer
recipes whose costability depends on dictionary ordering.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.prompts.meal_plan import PlanDraft
from src.recipes import CuratedRecipeRepository, Recipe
from src.recipes.planning import RecipeNotCostable, recipe_to_meal, recipes_to_draft
from src.retrieval.base import PriceRecord, PriceRepository
from src.retrieval.filters import FreshnessFilter, NearFilter
from src.schemas.contract import Citation

#: Ingredient terms one turn will resolve. A cap, not a page size -- see the
#: module docstring on why it raises rather than truncating.
MAX_INGREDIENT_LOOKUPS = 120


def meals_needed(recipes: list[Recipe], *, household_size: int, days: int) -> int:
    """
    How many recipes it takes to FEED this household, not how many days long it is.

    THE FIRST VERSION OF THIS PATH ASKED FOR ONE RECIPE PER DAY, and the
    meal-plan eval caught it immediately: 100% invariants became 45%, budget
    used fell from 69% to 24%, and `min_budget_used` fired on four cases.
    A day is not a meal. One porridge does not feed a person for a day, and a
    plan that says it does passes every arithmetic check while under-feeding the
    household -- which is precisely the failure `min_budget_used` was added to
    catch, because under-feeding passes a budget test that over-spending fails.

    So the count comes from the same place the feasibility refusal does:
    `min_grams_per_person_day` in `config/feasibility.json`, the one judgement in
    the planning path, reviewable by somebody who knows about food
    (`docs/OPEN-REVIEW-min-grams-per-person-day.md`). Required food is
    `household x days x that`; a recipe scaled to the household provides its
    per-serving grams times the household; the count is the ratio, rounded up.

    Using the SAME number as `minimum_spend` is the point. If it is wrong, it is
    wrong in one place and in one direction for both the refusal and the plan,
    rather than the two disagreeing about how much a household eats.

    Count-based ingredients (three eggs) contribute no grams, so a recipe made
    entirely of them reads as weightless and would ask for an unbounded number
    of meals. `MAX_SELECTED_RECIPES` bounds the answer, and the floor of 1 keeps
    a plan from being empty.
    """
    from src.graph.feasibility import min_grams_per_person_day
    from src.prompts.recipe_select import MAX_SELECTED_RECIPES

    required = household_size * max(days, 1) * min_grams_per_person_day()
    per_meal = [
        sum(i.grams_per_serving or 0 for i in r.ingredients) * household_size for r in recipes
    ]
    typical = max((g for g in per_meal if g > 0), default=0)
    if typical <= 0:
        return min(max(days, 1), MAX_SELECTED_RECIPES)
    return max(1, min(-(-required // typical), MAX_SELECTED_RECIPES))


class TooManyIngredients(RuntimeError):
    """The catalogue outgrew the per-turn lookup bound."""


@dataclass(frozen=True, slots=True)
class RecipeOffer:
    """One shortlisted recipe, and everything needed to cost it."""

    recipe: Recipe
    #: ingredient key -> citation ref, all produced by retrieval.
    refs: dict[str, str]
    #: What one serving-set for this household would cost, payable.
    payable_nzd: Decimal

    @property
    def product_names(self) -> str:
        """Resolved product names, for the prompt. Not the recipe's own terms."""
        return ", ".join(self.refs)


_repository: CuratedRecipeRepository | None = None


def curated_recipes() -> list[Recipe]:
    """
    The curated catalogue, loaded once.

    Module-level like `src/graph/dietary.py` and `src/graph/feasibility.py`
    rather than injected through the graph: `config/recipes.json` is
    config-as-data that ships inside the Lambda archive, and it is not a
    dependency two turns could disagree about. Adding it to `compiled_graph`'s
    cache key would be the alternative, and a cache key that grows for static
    data is a key somebody eventually gets wrong.
    """
    global _repository
    if _repository is None:
        _repository = CuratedRecipeRepository()
    return _repository.all_recipes()


def resolve_ingredients(
    repo: PriceRepository,
    recipes: list[Recipe],
    *,
    near: NearFilter | None,
    locations: frozenset[str] | None,
    freshness: FreshnessFilter | None,
) -> dict[str, PriceRecord]:
    """
    Ingredient term -> the cheapest retrieved record for it.

    ONE LOOKUP PER DISTINCT TERM, not per recipe line. 29 recipes carry 27
    distinct ingredient keys between them, so resolving per recipe would make
    roughly 120 queries to answer the same question.

    `near`, `locations` and `freshness` are passed to the REPOSITORY, exactly as
    the rest of retrieval does. Filtering the returned list instead would drop an
    in-radius, in-date price behind five that are neither, and the graph reads an
    empty list as no_data.
    """
    keys = sorted({i.key for r in recipes for i in r.ingredients})
    if len(keys) > MAX_INGREDIENT_LOOKUPS:
        raise TooManyIngredients(
            f"{len(keys)} distinct ingredient terms across {len(recipes)} recipes, "
            f"cap is {MAX_INGREDIENT_LOOKUPS}. Narrow the catalogue deliberately: "
            f"a shortlist built from the first {MAX_INGREDIENT_LOOKUPS} would offer "
            f"recipes whose costability depends on iteration order."
        )

    found: dict[str, PriceRecord] = {}
    for term in keys:
        product_key = repo.resolve_product_key(term)
        if product_key is None:
            continue
        records = repo.cheapest_for_product(
            product_key, limit=1, near=near, locations=locations, freshness=freshness
        )
        if records:
            found[term] = records[0]
    return found


def shortlist(
    recipes: list[Recipe],
    resolved: dict[str, PriceRecord],
    refs_by_record: dict[int, str],
    citations: dict[str, Citation],
    *,
    household_size: int,
    days: int,
    exclude_categories: list[str] | frozenset[str],
) -> list[RecipeOffer]:
    """
    Recipes this household could actually be served, costed and affordable.

    `refs_by_record` maps `id(record)` to the citation ref retrieval assigned it,
    so the offer carries refs the citation index already knows. Identity rather
    than equality because `PriceRecord` is frozen and two stores can hold
    byte-identical rows for the same product; the ref belongs to the record
    retrieval actually cited, not to one that compares equal to it.
    """
    excluded = set(exclude_categories)
    offers: list[RecipeOffer] = []
    for recipe in recipes:
        if not recipe.is_costable:
            continue

        refs: dict[str, str] = {}
        records: dict[str, PriceRecord] = {}
        viable = True
        for ingredient in recipe.ingredients:
            record = resolved.get(ingredient.key)
            # An unresolved ingredient and an excluded one both refuse the
            # recipe, and neither is a substitution: a recipe with a swapped
            # ingredient is a different recipe, which is precisely what Req 2.9
            # exists to stop the model doing.
            if record is None or record.category in excluded:
                viable = False
                break
            ref = refs_by_record.get(id(record))
            if ref is None:
                viable = False
                break
            refs[ingredient.key] = ref
            records[ref] = record
        if not viable:
            continue

        payable = _payable_for(
            recipe, refs, records, citations, household_size=household_size, days=days
        )
        if payable is None:
            continue
        offers.append(RecipeOffer(recipe=recipe, refs=refs, payable_nzd=payable))

    # Cheapest first. Not a filter -- an ordering, so the prompt's list and the
    # budget trim both start from the option that costs the household least.
    return sorted(offers, key=lambda o: o.payable_nzd)


def _payable_for(
    recipe: Recipe,
    refs: dict[str, str],
    records: dict[str, PriceRecord],
    citations: dict[str, Citation],
    *,
    household_size: int,
    days: int,
) -> Decimal | None:
    """
    What one serving of this recipe for this household costs, payable.

    Costed through the REAL `assemble_plan`, not a second cost function. Every
    arithmetic defect this project has found was in plan costing, and each was
    fixed once, in there. A shortlist filter with its own arithmetic would be
    the second copy, and the second copy is the one that goes wrong.

    Returns None when the recipe cannot be costed at all, which the caller reads
    as "not offerable" rather than as an error: `recipes_to_draft` raising is the
    right behaviour for a plan being built, and the wrong behaviour for a
    catalogue being filtered.
    """
    # Imported INSIDE the function. `src.graph.nodes.__init__` imports this
    # module, and importing `src.graph.nodes.plan` at module scope initialises
    # that package -- so the cycle closes, and it closes only when this module
    # is imported FIRST. It worked from every entry point the graph uses and
    # failed from a script that imported `recipe_plan` directly, which is the
    # worst kind of cycle: invisible until somebody imports in a new order.
    # `tests/test_recipe_planning.py` imports it that way on purpose.
    from src.graph.nodes.plan import assemble_plan

    try:
        meal = recipe_to_meal(recipe, household_size=household_size, refs=refs, records=records)
    except (RecipeNotCostable, ValueError):
        return None
    try:
        plan = assemble_plan(
            PlanDraft(meals=[meal], reasoning="shortlist costing"),
            citations,
            household_size=household_size,
            days=days,
            # A budget is required by the schema and is not what is being
            # measured here -- the caller compares `payable_total_nzd` against
            # the household's share itself. Passing the real budget would make
            # `within_budget` on a throwaway plan look like a claim about the
            # shopper's request.
            budget_nzd=Decimal("999999"),
            exclusions=[],
            repair_attempts=0,
        )
    except (KeyError, ValueError):
        return None
    return plan.payable_total_nzd


def affordable_set(
    offers: list[RecipeOffer],
    citations: dict[str, Citation],
    records: dict[str, PriceRecord],
    *,
    household_size: int,
    days: int,
    budget_nzd: Decimal | None,
) -> list[RecipeOffer]:
    """
    The largest cheapest-first set of recipes that fits the budget TOGETHER.

    THIS IS `candidates_for_budget` AT THE RECIPE LEVEL, and it is here for the
    same reason that function exists: the model never sees a price, so it cannot
    keep itself inside a budget, and the only thing that can is the set it
    chooses FROM. Any subset of a set that fits also fits -- removing a meal
    never adds a pack -- so once this has run, whatever the model picks is
    affordable by construction.

    IT REPLACED A PER-RECIPE CAP, AND THE DIFFERENCE IS THE WHOLE POINT. Filtering
    each recipe against `budget / meals` rejects on a number no plan ever pays,
    because `assemble_plan` aggregates packs across meals and rounds up once:
    five recipes sharing rice and onions cost far less together than the sum of
    the five costed alone. Against the 26-product fixture catalogue that cap
    collapsed a 29-recipe shortlist to one, and the meal-plan suite read the
    result as under-feeding, which is what it was.

    GREEDY, CHEAPEST FIRST, AND IT SKIPS RATHER THAN STOPPING. A recipe that
    would push the running set over budget is passed over and the next one tried,
    because one expensive option early must not truncate the whole list. That is
    not optimal -- the optimal subset is a knapsack over shared packs, and
    solving it exactly would be a search whose answer nobody could explain to a
    shopper. Measured against the suite it finds the same five meals for "$35, a
    week of dinners, one person" that an exhaustive search over every
    combination finds, which is the evidence that greedy is enough here rather
    than an argument that it always would be.
    """
    if budget_nzd is None:
        return offers

    chosen: list[RecipeOffer] = []
    for offer in offers:
        trial = [*chosen, offer]
        merged: dict[str, str] = {}
        for o in trial:
            merged.update(o.refs)
        try:
            draft = recipes_to_draft(
                [o.recipe for o in trial],
                household_size=household_size,
                refs=merged,
                records=records,
            )
        except (RecipeNotCostable, ValueError):
            continue
        from src.graph.nodes.plan import assemble_plan

        try:
            plan = assemble_plan(
                draft,
                citations,
                household_size=household_size,
                days=days,
                budget_nzd=budget_nzd,
                exclusions=[],
                repair_attempts=0,
            )
        except (KeyError, ValueError):
            continue
        if plan.payable_total_nzd <= budget_nzd:
            chosen.append(offer)
    return chosen
