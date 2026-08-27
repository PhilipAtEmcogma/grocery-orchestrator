"""
Meal plan node.

Three parts:
  generate_plan  — one model call producing a PlanDraft (no prices)
  assemble_plan  — computes EVERY monetary value in Python from records
  repair_plan    — builds targeted feedback and re-runs generation

The split matters. `assemble_plan` is pure arithmetic over retrieved data, so
it is exhaustively testable and cannot be wrong in the way a model can be.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from src.graph.state import GroceryState, usage_from
from src.models.base import (
    GuardrailBlocked,
    ModelClient,
    ModelError,
    ModelOutputInvalid,
    ModelTier,
)
from src.prompts.meal_plan import (
    SYSTEM_PROMPT,
    PlanDraft,
    build_repair_prompt,
    build_user_prompt,
    render_products,
)
from src.retrieval.base import PriceRecord
from src.schemas.contract import (
    Citation,
    Ingredient,
    Meal,
    MealPlan,
    StoreBasket,
)

CENT = Decimal("0.01")


def _round(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def assemble_plan(
    draft: PlanDraft,
    citations: dict[str, Citation],
    *,
    household_size: int,
    days: int,
    budget_nzd: Decimal,
    exclusions: list[str],
    repair_attempts: int,
) -> MealPlan:
    """
    Turn a price-free draft into a costed MealPlan.

    Every figure here is computed from Citation.price_nzd, which came from the
    price store. Nothing the model wrote contributes a number.

    Raises KeyError if the draft references an unknown citation ref — that is
    a hallucinated reference and must fail loudly rather than be dropped
    silently, which would produce a plan quietly missing an ingredient.
    """
    meals: list[Meal] = []
    # store -> (location, refs, running total)
    baskets: dict[str, tuple[str, set[str], Decimal]] = {}

    for draft_meal in draft.meals:
        ingredients: list[Ingredient] = []
        subtotal = Decimal("0")

        for line in draft_meal.ingredients:
            citation = citations[line.citation_ref]  # KeyError = hallucinated ref
            line_cost = _round(citation.price_nzd * line.packs)

            ingredients.append(
                Ingredient(
                    item=line.item,
                    qty=line.qty_display,
                    citation_ref=line.citation_ref,
                    line_cost_nzd=line_cost,
                )
            )
            subtotal += line_cost

            key = f"{citation.store.value}#{citation.store_location}"
            location, refs, running = baskets.get(
                key, (citation.store_location, set(), Decimal("0"))
            )
            # A pack is bought once even if used across several meals, so the
            # basket total counts each product once at full pack price.
            if line.citation_ref not in refs:
                running += citation.price_nzd
            refs.add(line.citation_ref)
            baskets[key] = (location, refs, running)

        meals.append(
            Meal(
                name=draft_meal.name,
                serves=draft_meal.serves,
                ingredients=ingredients,
                subtotal_nzd=_round(subtotal),
            )
        )

    total = _round(sum((m.subtotal_nzd for m in meals), Decimal(0)))

    store_baskets = [
        StoreBasket(
            store=citations[next(iter(refs))].store,
            store_location=location,
            citation_refs=sorted(refs, key=lambda r: int(r[1:])),
            basket_total_nzd=_round(running),
        )
        for location, refs, running in baskets.values()
    ]

    return MealPlan(
        household_size=household_size,
        days=days,
        budget_nzd=budget_nzd,
        total_nzd=total,
        within_budget=total <= budget_nzd,
        repair_attempts=repair_attempts,
        meals=meals,
        baskets=store_baskets,
        dietary_exclusions_applied=exclusions,
    )


def _cheaper_options(
    citations: list[Citation], used_refs: set[str], limit: int = 6
) -> str:
    """Name specific cheaper products the repair pass can swap toward."""
    unused = sorted(
        (c for c in citations if c.ref not in used_refs),
        key=lambda c: c.price_nzd,
    )[:limit]
    if not unused:
        return "No cheaper unused products are available."
    rows = "\n".join(
        f"  {c.ref} — {c.product_name} ({c.store.value} {c.store_location})"
        for c in unused
    )
    return f"Cheaper products you did not use:\n{rows}"


def generate_plan(state: GroceryState, model: ModelClient) -> dict:
    """
    One model call. QUALITY tier on the first attempt, FAST on repairs.

    The repair pass is arithmetic-driven substitution rather than creative
    planning, so it does not need the expensive model — and using the cheap
    one keeps the second call inside the latency budget.
    """
    citations = state.get("citations") or []
    if not citations:
        return {"plan": None, "validation_errors": ["no products available"]}

    citation_index = state.get("citation_index") or {}
    records: dict[str, PriceRecord] = state.get("record_index") or {}
    constraints = state.get("constraints", {})
    attempts = state.get("repair_attempts", 0)

    budget = constraints.get("budget_nzd")
    if budget is None:
        return {"plan": None, "validation_errors": ["no budget given"]}

    household = constraints.get("household_size", 1)
    days = constraints.get("days", 1)
    exclusions = constraints.get("dietary_exclusions", [])
    products = render_products(citations, records)

    if attempts == 0:
        tier = ModelTier.QUALITY
        task = "generate_plan"
        user_prompt = build_user_prompt(
            message=state["message"],
            household_size=household,
            days=days,
            budget_nzd=budget,
            exclusions=exclusions,
            products=products,
        )
    else:
        tier = ModelTier.FAST
        task = "repair_plan"
        previous = state.get("plan")
        over_by = (
            _round(previous.total_nzd - budget) if previous else Decimal("0")
        )
        used = {
            i.citation_ref
            for m in (previous.meals if previous else [])
            for i in m.ingredients
        }
        user_prompt = build_repair_prompt(
            products=products,
            over_by=over_by,
            budget=budget,
            household_size=household,
            days=days,
            exclusions=exclusions,
            previous_items=[
                i.item for m in (previous.meals if previous else []) for i in m.ingredients
            ][:12],
            cheaper_options=_cheaper_options(citations, used),
        )

    # Read before the try, not inside it: a name bound only on the
    # happy path is unbound on every except branch that needs it.
    _usage_before = model.last_usage

    try:
        # `task` is what the registry routes on, and it was previously left
        # to the parameter's default — so every plan call routed as
        # 'classify_intent' and landed on the FAST model, silently
        # contradicting the QUALITY tier this node asks for and leaving the
        # generate_plan/repair_plan rules in config/models.json unreachable.
        # It is also the label the trace and the per-model latency metric are
        # keyed on, so a wrong task attributes the plan path's latency to
        # classification.
        draft = model.structured(
            system=SYSTEM_PROMPT,
            user=user_prompt,
            schema=PlanDraft,
            tier=tier,
            max_tokens=2048,
            task=task,
        )
    except GuardrailBlocked:
        raise
    except ModelOutputInvalid as exc:
        # The model answered; the answer did not fit PlanDraft. That is a
        # quality failure and precisely what the repair loop is for, so it
        # stays a validation error. Caught BEFORE ModelError because it is a
        # subclass: ordering these the other way sent every schema failure
        # down the upstream path, which reported a model that could not honour
        # its own schema — Claude Haiku 4.5 overrunning the 600-character
        # `reasoning` cap on 8 of 11 cases — as though Bedrock were down.
        return {
            "plan": None,
            "validation_errors": [f"invalid plan draft: {exc}"],
            "usage": usage_from(model, _usage_before),
        }
    except ModelError as exc:
        # NOT a validation error. Reporting an unreachable model as one sent
        # this into the repair loop, which re-invoked the same broken client
        # twice more and then emitted BUDGET_INFEASIBLE — telling a user whose
        # Bedrock call had failed to "raise the budget", advice that cannot
        # help and that they may act on. It also made a total outage
        # indistinguishable from a genuinely unaffordable basket in the evals.
        return {
            "plan": None,
            "upstream_error": str(exc),
            "usage": usage_from(model, _usage_before),
        }

    try:
        plan = assemble_plan(
            draft,
            citation_index,
            household_size=household,
            days=days,
            budget_nzd=budget,
            exclusions=exclusions,
            repair_attempts=attempts,
        )
    except KeyError as exc:
        # A reference the retrieval layer never produced. Refuse it rather
        # than silently dropping the ingredient.
        return {
            "plan": None,
            "validation_errors": [f"plan referenced unknown product {exc}"],
            "usage": usage_from(model, _usage_before),
        }

    return {"plan": plan, "usage": usage_from(model, _usage_before)}


# route_after_validation used to be defined here as well as in
# src/graph/nodes/__init__.py. Only the latter was ever imported, so this copy
# was dead, and it would have silently disagreed with the live router the
# moment either changed — which is exactly what the upstream_error branch
# would have done. Removed rather than kept in sync.
