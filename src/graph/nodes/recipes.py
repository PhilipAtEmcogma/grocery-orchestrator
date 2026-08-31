"""
Recipe selection node (Req 2.9, Pilot Task 15c).

    retrieve_prices --plan--> select_recipes
                                |-- selected -----> validate_plan
                                `-- fallback -----> generate_plan (free composition)

WHAT REQ 2.9 ACTUALLY ASKS FOR. "A meal plan SHALL select meals from a curated
catalogue rather than composing them freely, with the model choosing recipe ids
and deterministic code owning scaling, dietary verification and totals." Every
noun in the second half is code below this line; the model's whole contribution
is `RecipeSelection.recipe_ids`, and that schema has no other field.

THE MODEL CANNOT CHOOSE BADLY IN A WAY THAT REACHES A PRICE. Three things stand
between it and the shopper, and none of them trusts it:

  * it is offered only the shortlist `retrieve_prices` built -- already costable,
    already dietary-viable against the RESOLVED products, already affordable at
    this household's share of the budget;
  * an id outside that shortlist is dropped as a fabrication, the same way a
    citation ref nobody retrieved is. It is not "corrected" to a near match:
    `resolve_product_key` refuses fuzzy matching for exactly this reason, and a
    silently substituted recipe is a different meal than the one chosen;
  * every quantity, pack count and dollar figure is computed afterwards by
    `recipes_to_draft` and `assemble_plan`, which the free-composition path
    already uses. There is no second costing implementation to disagree.

FALLING BACK IS A DECISION, AND IT IS ANNOUNCED. When the recipe path cannot
produce a plan -- too few viable recipes (vegan is 7 of 29, and the reason is
recorded in `tasks.md` 15b), nothing affordable, or a model that returned no
usable id -- the turn falls through to free composition and emits a
`NoticeEvent` saying so.

Both are honest answers and they are DIFFERENT PRODUCTS: "Tuesday: Sausages and
Mash" and a list of cheap products the model grouped are not the same thing, and
not saying which one the shopper got would be the quiet substitution this
codebase refuses everywhere else. The alternative -- refusing the turn -- would
regress requests that work today, which is a worse trade than a named fallback.
"""

from __future__ import annotations

from decimal import Decimal

from src.graph.nodes.plan import assemble_plan
from src.graph.recipe_plan import curated_recipes
from src.graph.state import GroceryState, usage_from
from src.models.base import (
    TASK_SELECT_RECIPES,
    GuardrailBlocked,
    ModelClient,
    ModelError,
    ModelOutputInvalid,
    ModelTier,
)
from src.prompts.recipe_select import (
    MAX_SELECTED_RECIPES,
    SYSTEM_PROMPT,
    RecipeSelection,
    build_selection_prompt,
)
from src.recipes.planning import RecipeNotCostable, recipes_to_draft
from src.schemas.contract import NoticeEvent, UsageMeta

#: Said to the shopper when the plan was composed rather than cooked from the
#: catalogue. One sentence, no apology, and it names the reason.
_FALLBACK_NOTICE = (
    "I couldn't build this from my recipe collection ({reason}), so I've put "
    "together a shopping list of affordable items instead."
)

_REASONS = {
    "no_shortlist": "nothing in it fits your budget and preferences",
    "no_selection": "I couldn't pick a set of meals",
    "not_costable": "the meals I picked couldn't be priced",
}


def select_recipes(state: GroceryState, model: ModelClient) -> dict:
    """
    Choose recipes from the shortlist and build the plan, or fall back.

    Returns `plan` on success. On any failure it returns `recipe_fallback` with
    a reason and a notice event, and the router sends the turn to
    `generate_plan`.

    UPSTREAM FAILURES ARE NOT FALLBACKS. A model that could not be reached is
    recorded as `upstream_error` and left to the existing terminal path -- the
    graph already refuses to collapse "we could not reach the model" into
    "your budget does not stretch", and collapsing it into "no recipe fitted"
    would be the same mistake in new clothes.
    """
    shortlist = state.get("recipe_shortlist") or []
    if not shortlist:
        return _fallback(state, "no_shortlist")

    constraints = state.get("constraints", {})
    household = constraints.get("household_size", 1)
    days = constraints.get("days", 1)
    budget = constraints.get("budget_nzd")
    if budget is None:
        # The clarification path refuses a meal plan with no budget before it
        # reaches here, so this is a routing bug rather than a user error.
        return _fallback(state, "no_shortlist")

    # HOW MANY MEALS, not how many days. Retrieval derived it from
    # `min_grams_per_person_day`, the same figure the feasibility refusal uses,
    # because a day is not a meal -- see `recipe_plan.meals_needed` for the
    # regression that established this.
    wanted = state.get("recipe_meals_wanted") or days

    by_id = {r.recipe_id: r for r in curated_recipes()}
    refs_by_recipe = state.get("recipe_refs") or {}
    offered = [
        (rid, by_id[rid].name, ", ".join(sorted(refs_by_recipe.get(rid, {}))))
        for rid in shortlist
        if rid in by_id
    ]

    _usage_before = model.last_usage
    try:
        selection = model.structured(
            system=SYSTEM_PROMPT,
            user=build_selection_prompt(
                message=state.get("message", ""),
                household_size=household,
                days=days,
                meals=wanted,
                exclusions=constraints.get("dietary_exclusions", []),
                offered=offered,
            ),
            schema=RecipeSelection,
            tier=ModelTier.FAST,
            task=TASK_SELECT_RECIPES,
        )
    except GuardrailBlocked:
        # A refusal here is about the shopper's own message, which the prompt
        # carries. Falling back to free composition serves them a plan rather
        # than an error, and the guardrail still saw the message on the
        # generate_plan call that follows.
        return _fallback(state, "no_selection", usage=usage_from(model, _usage_before))
    except ModelOutputInvalid:
        return _fallback(state, "no_selection", usage=usage_from(model, _usage_before))
    except ModelError as exc:
        return {
            "upstream_error": str(exc),
            "usage": usage_from(model, _usage_before),
        }

    # VALIDATED AGAINST THE SHORTLIST, NOT MERELY AGAINST THE CATALOGUE. A real
    # recipe id that was not offered is still a fabrication for this turn: it
    # failed one of the three filters, so it is either uncostable, excluded by
    # the shopper's diet, or unaffordable. Order is preserved because the plan
    # lists meals in the order chosen; duplicates are dropped because a week of
    # the same dinner is not what "choose 5 meals" meant.
    # LITERALLY WHAT THE MODEL SAID, before anything is dropped or added. The
    # scorecard reads this, so it can tell a repeated selection apart from a
    # short one -- dedupe turns "the same recipe four times" into "one recipe",
    # and a suite scoring the deduped list reports a repetition defect as a
    # counting defect.
    model_chose = list(selection.recipe_ids)

    allowed = set(shortlist)
    chosen: list[str] = []
    for rid in selection.recipe_ids[:MAX_SELECTED_RECIPES]:
        if rid in allowed and rid not in chosen:
            chosen.append(rid)
    if not chosen:
        return _fallback(
            state, "no_selection", usage=usage_from(model, _usage_before), model_chose=model_chose
        )

    chosen = chosen[:wanted]

    # TOP UP, when the model returned fewer meals than the household needs.
    #
    # UNUSED RECIPES FIRST, REPEATS ONLY WHEN THE SHORTLIST RUNS OUT. Filling
    # with repeats while distinct options sat unchosen was the first version,
    # and it is worse for the shopper on the one axis this path exists to
    # improve -- a week of the same dinner is what "select from a catalogue" was
    # meant to replace. It also made two failure modes indistinguishable: a model
    # that picked too few produced a plan full of repeats, so the eval blamed
    # repetition for a counting failure.
    #
    # Repeating IS legitimate once distinct options are exhausted, and it is not
    # padding: a repeated meal is a real meal the shopper buys real food for, and
    # `assemble_plan` aggregates its packs into the same basket. Vegan is 7 of 29
    # viable against the real catalogue, so a narrow diet reaches this often. The
    # alternative -- a plan covering three of seven days -- passes every
    # arithmetic check and under-feeds the household, which is the exact failure
    # `min_budget_used` exists to catch.
    if len(chosen) < wanted:
        for rid in shortlist:
            if len(chosen) >= wanted:
                break
            if rid not in chosen:
                chosen.append(rid)
    if len(chosen) < wanted:
        distinct = list(chosen)
        i = 0
        while len(chosen) < wanted and distinct:
            chosen.append(distinct[i % len(distinct)])
            i += 1

    plan = _cost_within_budget(
        state,
        chosen,
        by_id,
        household_size=household,
        days=days,
        budget=Decimal(str(budget)),
        exclusions=constraints.get("dietary_exclusions", []),
    )
    if plan is None:
        return _fallback(
            state, "not_costable", usage=usage_from(model, _usage_before), model_chose=model_chose
        )

    return {
        "selected_recipes": chosen[: len(plan.meals)],
        # WHAT THE MODEL RETURNED, before the top-up and the budget trim.
        #
        # Reported separately because the node CORRECTS the model: it fills a
        # short selection from unused recipes and drops meals that do not fit.
        # Those are the right things for a plan and the wrong things for a
        # scorecard -- a node that silently repairs every mistake qualifies every
        # model, which is a gate that cannot fail. `evals/run_recipe_select.py`
        # scores this list; the shopper is served the one above.
        "recipe_selection_model": model_chose,
        "plan": plan,
        "usage": usage_from(model, _usage_before),
    }


def _cost_within_budget(
    state: GroceryState,
    chosen: list[str],
    by_id: dict,
    *,
    household_size: int,
    days: int,
    budget: Decimal,
    exclusions: list[str],
):
    """
    Cost the selection, trimming meals off the END until it fits.

    THIS IS WHERE THE BUDGET IS ENFORCED, and it is deterministic code doing it
    -- Req 2.9's actual division of labour. The model chose and ordered; it never
    saw a price and could not have costed its own choice.

    IT DROPS THE MEAL WITH THE HIGHEST MARGINAL COST, not the last one.

    Trimming from the end was the first version and it is the intuitive one --
    the model returns recipes "in the order they should be served", so the end
    is its least preferred choice. Measured against the meal-plan suite it cost
    a case: "a week of dinners for one person on $35" came back with three
    distinct meals where the suite asks for four, because the fourth meal the
    model happened to put last was not the one whose removal freed the most
    money.

    Marginal cost is what the shopper actually pays for a meal, once the packs
    it shares with the rest of the plan are accounted for -- so removing the
    dearest keeps the most meals for the budget, which is what
    `min_distinct_meals` is measuring and what a shopper wants. The model's
    order is cosmetic (the plan lists meals in it) and is preserved among
    whatever survives.

    O(n^2) `assemble_plan` calls at worst, n bounded by `MAX_SELECTED_RECIPES`
    -- so at most ~105 evaluations of pure arithmetic over a handful of
    ingredients, on a path already waiting on a model call.

    THE FIRST DESIGN ENFORCED THE BUDGET AS A PER-RECIPE FILTER IN THE
    SHORTLIST, and it was wrong for a reason worth keeping: `assemble_plan`
    aggregates packs across meals and rounds up once, so recipes sharing
    ingredients cost less together than apart. A per-recipe cap rejects on a
    number no plan ever pays -- it collapsed a 29-recipe shortlist to one
    against the fixtures, and the meal-plan eval correctly read the result as
    under-feeding. The aggregate total is the only figure the shopper hands over,
    so it is the only one worth checking against.

    Returns None when even one meal cannot be afforded or costed, which the
    caller reads as a fallback rather than an error.
    """

    def cost(subset: list[str]):
        """The plan for a subset, or None if it cannot be costed at all."""
        try:
            draft = recipes_to_draft(
                [by_id[rid] for rid in subset],
                household_size=household_size,
                refs=_merged_refs(state, subset),
                records=state.get("record_index") or {},
            )
            return assemble_plan(
                draft,
                state.get("citation_index") or {},
                household_size=household_size,
                days=days,
                budget_nzd=budget,
                exclusions=exclusions,
                repair_attempts=0,
            )
        except (RecipeNotCostable, KeyError, ValueError):
            # `recipes_to_draft` raises rather than dropping an ingredient it
            # cannot price, which is what a PLAN needs. Here it means the
            # shortlist and the record index disagree, and no smaller subset
            # fixes that -- so the caller falls back rather than trimming
            # toward a plan with a hole in it.
            return None

    remaining = list(chosen)
    while remaining:
        plan = cost(remaining)
        if plan is None:
            return None
        if plan.payable_total_nzd <= budget:
            return plan
        if len(remaining) == 1:
            return None
        # Drop the meal whose removal saves the most. `costed` holds the total
        # WITHOUT each candidate, so the smallest of them is the biggest saving.
        costed = []
        for rid in remaining:
            without = [r for r in remaining if r != rid]
            trial = cost(without)
            if trial is not None:
                costed.append((trial.payable_total_nzd, rid))
        if not costed:
            return None
        _, drop = min(costed)
        remaining = [r for r in remaining if r != drop]
    return None


def _merged_refs(state: GroceryState, chosen: list[str]) -> dict[str, str]:
    """
    Ingredient key -> citation ref, across every chosen recipe.

    Flat because `recipes_to_draft` costs a set of recipes together, and two
    recipes using `onions` must resolve to the SAME citation -- otherwise
    `assemble_plan` would count two separate packs of the same product and
    round each up, charging the shopper twice for one bag of onions.
    """
    refs_by_recipe = state.get("recipe_refs") or {}
    merged: dict[str, str] = {}
    for rid in chosen:
        merged.update(refs_by_recipe.get(rid, {}))
    return merged


def _fallback(
    state: GroceryState,
    reason: str,
    usage: UsageMeta | None = None,
    model_chose: list[str] | None = None,
) -> dict:

    out: dict = {
        "recipe_fallback": reason,
        "recipe_selection_model": model_chose or [],
        "events": [
            NoticeEvent(
                seq=len(state.get("events", [])),
                message=_FALLBACK_NOTICE.format(reason=_REASONS[reason]),
            )
        ],
    }
    if usage is not None:
        out["usage"] = usage
    return out


def route_after_recipe_selection(state: GroceryState) -> str:
    """Recipe plan, free composition, or an upstream failure that is neither."""
    if state.get("upstream_error"):
        return "upstream_failed"
    if state.get("plan") is not None and state.get("selected_recipes"):
        return "validate"
    return "compose"
