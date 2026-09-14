"""
Graph nodes.

Every node is a function of state -> partial state, which makes them
independently unit-testable without running the whole graph.

FOUR MODULES, AND THE SEAMS ARE THE INVARIANTS.

    retrieval.py   `retrieve_prices` -- the ONLY creator of Citations -- with
                   the filters it hands the repository and the three honest
                   terminals that say what it could not find.
    plan.py        free composition, assembly, and the repair prompts.
    recipes.py     recipe selection (Req 2.9) and the fallback.
    prose.py       the one node allowed to fail without failing the turn.
    __init__.py    everything else, plus the ROUTING, which is the topology.

Split out of a single 925-line file on 2026-08-31. The re-exports below are what
keep every existing caller working -- `from src.graph.nodes import X` resolves
exactly as it did, and `build.py` still reads `nodes.retrieve_prices`. That
matters more than usual here: `compiled_graph` resolves node functions from this
package AT BUILD TIME, so a test monkeypatching `src.graph.nodes.generate_plan`
is patching this namespace and would be silently defeated by an import that
bypassed it.

Dietary exclusion mapping lives in `src/graph/dietary.py` -- the single
reviewable source of truth for what a user term means and whether it can be
honoured. Nodes here consume `map_exclusions()` output; no node defines its
own mapping.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from src.graph.dietary import supported_terms
from src.graph.nodes._shared import _join, _next_seq, _preference_unmet
from src.graph.nodes.intent import classify_intent as classify_intent
from src.graph.nodes.plan import generate_plan as generate_plan
from src.graph.nodes.prose import generate_prose as generate_prose
from src.graph.nodes.recipes import route_after_recipe_selection as route_after_recipe_selection
from src.graph.nodes.recipes import select_recipes as select_recipes
from src.graph.nodes.retrieval import MAX_ITEMS_PER_TURN as MAX_ITEMS_PER_TURN
from src.graph.nodes.retrieval import MEAL_CATEGORIES as MEAL_CATEGORIES
from src.graph.nodes.retrieval import current_freshness as current_freshness
from src.graph.nodes.retrieval import emit_no_data as emit_no_data
from src.graph.nodes.retrieval import emit_stale_data as emit_stale_data
from src.graph.nodes.retrieval import emit_unknown_region as emit_unknown_region
from src.graph.nodes.retrieval import retrieve_prices as retrieve_prices
from src.graph.state import MAX_REPAIR_ATTEMPTS, GroceryState
from src.recipes.base import preference_terms_met
from src.schemas.contract import (
    ClarificationEvent,
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    Intent,
    MealPlanEvent,
    MissingConstraint,
    NoticeEvent,
    PriceComparison,
    PriceComparisonEvent,
    PriceOption,
    SessionEvent,
    UsageMeta,
    assert_arithmetic,
    assert_costed_from_citations,
    find_literal_money_in_plan,
)

# --------------------------------------------------------------- nodes


def validate_input(state: GroceryState) -> dict:
    """Emit the session event. Input is schema-validated at the edge already."""
    return {
        "events": [
            SessionEvent(
                seq=_next_seq(state),
                session_id=state["session_id"],
                turn_id=state["turn_id"],
            )
        ],
        "repair_attempts": 0,
        "validation_errors": [],
        "terminated": False,
    }


def generate_comparison(state: GroceryState) -> dict:
    """
    One comparison per item the user asked about.

    Reads state['citation_index'] and nothing else, so it cannot invent a
    price. When a model replaces this, that property must be preserved by
    construction rather than by prompt instruction.
    """
    index = state.get("citation_index") or {}
    groups = state.get("item_groups") or {}
    if not groups or not index:
        return {"comparisons": []}

    comparisons: list[PriceComparison] = []
    for product_key, refs in groups.items():
        options = [index[r] for r in refs if r in index]
        if not options:
            continue

        cheapest, dearest = options[0], options[-1]
        comparisons.append(
            PriceComparison(
                query_item=product_key,
                options=[
                    PriceOption(
                        citation_ref=c.ref,
                        is_cheapest=(c.ref == cheapest.ref),
                        savings_vs_dearest_nzd=(
                            dearest.price_nzd - c.price_nzd if c.ref == cheapest.ref else None
                        ),
                    )
                    for c in options
                ],
                reasoning=(
                    f"{cheapest.store.value.replace('_', ' ').title()} "
                    f"{cheapest.store_location} is cheapest for "
                    f"{cheapest.product_name}"
                    f"{' (on special)' if cheapest.on_special else ''}."
                ),
            )
        )

    return {"comparisons": comparisons}


def validate_plan(state: GroceryState) -> dict:
    """Arithmetic verification. Never trust model-computed totals."""
    plan = state.get("plan")
    if plan is None:
        # No plan to price, so nothing here is evidence about the budget.
        return {"validation_errors": ["no plan produced"], "over_budget": False}

    errors: list[str] = []
    try:
        assert_arithmetic(plan)
    except AssertionError as exc:
        errors.append(str(exc))

    # The stronger half: every figure re-derived from the cited prices rather
    # than merely checked for internal consistency. `assert_arithmetic` proves
    # the sums agree with each other, which a consistently wrong line cost also
    # satisfies. This proves they follow from what retrieval returned, and it is
    # where the reuse/multipack case is caught -- packs aggregated across meals
    # and rounded up once, not per appearance.
    try:
        assert_costed_from_citations(plan, state.get("citation_index") or {})
    except AssertionError as exc:
        errors.append(str(exc))

    # Model-authored free text inside the plan. `PlanDraft` has no price
    # field, so a price cannot reach a STRUCTURED slot -- but meal names,
    # ingredient names and quantities are free text the model writes and the
    # user reads, and nothing checked them. A plan naming a meal "Pasta -
    # only $4.99 a head" cleared every assertion the system had.
    #
    # Deliberately NOT folded into `over_budget`: a plan carrying an invented
    # figure is our failure to generate, not a fact about the shopper's
    # budget, and routing it to emit_budget_infeasible would tell them to
    # raise a budget that was never the problem -- the same false statement
    # the upstream-failure split already fixed once.
    errors.extend(find_literal_money_in_plan(plan))

    # Against PAYABLE, not consumption. Checking total_nzd here meant the
    # repair loop never fired for a plan whose shopping list busted the budget
    # while its fractional line costs did not -- the common case, since most
    # recipes use part of a pack.
    over_budget = plan.payable_total_nzd > plan.budget_nzd
    if over_budget:
        errors.append(
            f"payable {plan.payable_total_nzd} exceeds budget {plan.budget_nzd} "
            f"by {plan.payable_total_nzd - plan.budget_nzd}"
        )
    return {"validation_errors": errors, "over_budget": over_budget}


def repair_plan(state: GroceryState) -> dict:
    """Increments the attempt counter; regeneration happens on the loop back."""
    return {"repair_attempts": state.get("repair_attempts", 0) + 1}


def emit_dietary_unsupported(state: GroceryState) -> dict:
    """
    Honest refusal when a stated dietary exclusion cannot be safely honoured.

    Reached only for meal_plan turns (a price_check for one product does not
    apply a dietary filter). Dropping a restriction is the dangerous
    direction of error, so a plan we cannot verify is refused rather than
    guessed — same principle as `emit_budget_infeasible` (Req 4.5, Req 5.1).

    The message names the terms we cannot honour AND the ones we can, so the
    user has an actionable next step rather than being told what will not
    work.
    """
    unsupported = state.get("unsupported_exclusions") or []
    supported = supported_terms()
    return {
        "terminated": True,
        "events": [
            ErrorEvent(
                seq=_next_seq(state),
                code=ErrorCode.UNSUPPORTED_EXCLUSION,
                retryable=False,
                message=(
                    f"I can't safely plan meals for {_join(unsupported)} "
                    f"from my current data. I can plan around any of: "
                    f"{', '.join(supported)}. Would you like me to try with "
                    f"different constraints?"
                ),
            )
        ],
    }


def emit_preference_unavailable(state: GroceryState) -> dict:
    """
    The shopper asked the plan to be built around foods we cannot match to any
    product in the current supermarket data.

    Reached only for a meal_plan turn where the user stated one or more
    `preferred_ingredients` and NONE of them matches a product or a known food
    category -- "a quinoa meal plan" against a catalogue with no quinoa, or "a
    dinosaur meal plan". We refuse and ask them to try a different ingredient,
    rather than quietly serving a plan about something else with a footnote.
    That footnote is the right answer for a food we DO carry but cannot afford
    (`finalise`'s unmet-preference notice); it is the wrong answer here, because
    it implies we built a plan that honours the request.

    THE MESSAGE POINTS AT THE DATA, NOT AT COMPREHENSION, and this is deliberate.
    The resolver refuses fuzzy matching by design (design.md §8), so it cannot
    tell a real food we do not stock ("quinoa") from a word that is not food
    ("dinosaurs") -- both simply fail to resolve. Claiming "I didn't understand
    that" would be wrong for quinoa and rude for a typo; claiming "I don't stock
    that" would be wrong for dinosaurs. The one statement true for every case
    that reaches here is that we could not match it to the products we have, so
    that is what it says. Distinguishing the two would need a food ontology this
    project deliberately does not have.

    NOT A SAFETY REFUSAL, and deliberately unlike `emit_dietary_unsupported`
    two functions up. An unmet EXCLUSION is dangerous -- serving a vegetarian
    chicken -- so that path fails closed. An unavailable PREFERENCE is only an
    availability gap, so this is retryable and its remedy is "ask for something
    we carry". The two share a shape and not a reason.

    SINGLE-TURN. This refuses within the one turn rather than asking, waiting,
    and refusing only a repeated answer we still cannot match. True
    clarify-then-refuse-on-repeat needs turn-to-turn memory the orchestrator
    does not have -- the graph sees one request at a time -- and standing that
    up means the AgentCore Memory / session-state workstream, gated behind a
    Privacy Act 2020 design (consent, TTL, deletion). Deferred deliberately; see
    design.md §8. The frontend gives the shopper the next turn to rephrase.
    """
    unavailable = state.get("unavailable_preferences") or []
    return {
        "terminated": True,
        "events": [
            ErrorEvent(
                seq=_next_seq(state),
                code=ErrorCode.PREFERENCE_UNAVAILABLE,
                # An availability gap in the data, not a fact about the budget:
                # asking for something the catalogue carries is the move that
                # works, so it is worth trying again.
                retryable=True,
                message=(
                    f"I couldn't build a meal plan around {_join(unavailable)} — "
                    f"I can't match that to the products in the supermarket data "
                    f"I have right now. Could you try a different ingredient, or "
                    f"ask me to plan without it?"
                ),
            )
        ],
    }


def emit_upstream_failure(state: GroceryState) -> dict:
    """
    The model could not be reached. Distinct from every other terminal node
    here, which describe things that are true about the user's *request*.

    This one is about us. Saying "I couldn't build a plan within $30 using
    current prices" when Bedrock timed out is not a softer way of reporting an
    outage — it is a false statement about their budget, and the alternatives
    it offers (raise the budget, cut days) cannot possibly work. So the
    message says the service failed, and `retryable` is True because, unlike a
    budget that genuinely does not stretch, trying again is the right move.

    The underlying error goes to the log, not the user: it can name internal
    configuration ("BEDROCK_GUARDRAIL_ID is not set"), which is operator
    detail, not something a shopper can act on.
    """
    detail = state.get("upstream_error", "")
    timed_out = "timeout" in detail.lower() or "timed out" in detail.lower()
    return {
        "terminated": True,
        "plan": None,
        "events": [
            ErrorEvent(
                seq=_next_seq(state),
                code=(ErrorCode.UPSTREAM_TIMEOUT if timed_out else ErrorCode.INTERNAL_ERROR),
                retryable=True,
                message=(
                    "I couldn't reach the service that builds meal plans just "
                    "then, so I haven't got a plan for you. Your budget and "
                    "preferences are fine — please try again in a moment."
                ),
            )
        ],
    }


def emit_plan_generation_failed(state: GroceryState) -> dict:
    """
    Repair exhausted without ever producing a valid plan.

    Reached when the failures were about the plan's validity rather than its
    price: a draft that would not satisfy PlanDraft, a hallucinated citation
    ref, arithmetic that did not reconcile. The budget may be perfectly
    generous; we simply could not build something we were willing to stand
    behind, and saying otherwise sends the user to change a setting that was
    never the problem.

    Carries its own code rather than INTERNAL_ERROR. Folding it in there
    would tell an operator that the model plane had failed when it is up and
    answering, which is the same conflation, one layer along. Adding an enum
    member is additive under the v1 rules -- clients are required to tolerate
    codes they do not recognise, exactly as they do unknown event types.
    """
    return {
        "terminated": True,
        "plan": None,
        "events": [
            ErrorEvent(
                seq=_next_seq(state),
                code=ErrorCode.PLAN_GENERATION_FAILED,
                # Generation is non-deterministic, so unlike a budget that
                # genuinely does not stretch, another attempt may well work.
                retryable=True,
                message=(
                    "I couldn't put together a plan I trust this time. That's "
                    "a problem on my end, not with your budget or your "
                    "preferences — please try again."
                ),
            )
        ],
    }


def emit_budget_infeasible(state: GroceryState) -> dict:
    """Repair budget exhausted. Honest failure with actionable alternatives."""
    budget = state.get("constraints", {}).get("budget_nzd", Decimal("0"))
    return {
        "terminated": True,
        # Discard the failing draft. Emitting a plan we have just declared
        # infeasible would show the user a shopping list that busts their
        # budget, directly beside an error saying we could not make one.
        "plan": None,
        "events": [
            ErrorEvent(
                seq=_next_seq(state),
                code=ErrorCode.BUDGET_INFEASIBLE,
                retryable=False,
                message=(
                    f"I couldn't build a plan within ${budget} using current prices. "
                    "Would you like to raise the budget, reduce the number of days, "
                    "or see the cheapest option available?"
                ),
            )
        ],
    }


def _unmet_preference_notices(state: GroceryState, plan, seq: int) -> list[NoticeEvent]:
    """
    A notice per stated preference the finished plan does not answer, or none.

    Judged from the products the plan CITES, via `preference_terms_met`, rather
    than from the recipe ids that were offered. The offer set is what the model
    could choose from; a shopper reads the basket. See that function for why the
    two questions need two matchers and what must not diverge between them.

    ONE NOTICE PER UNMET TERM rather than one listing all of them, because the
    reasons differ: "seafood, chicken" on a tight budget may be seafood priced
    out and chicken absent from the catalogue, and a single sentence would have
    to pick one explanation and be wrong about the other.
    """
    preferences = state.get("constraints", {}).get("preferred_ingredients") or []
    if not preferences:
        return []

    records = state.get("record_index") or {}
    cited = {ingredient.citation_ref for meal in plan.meals for ingredient in meal.ingredients}
    products = [
        (records[ref].canonical_name, records[ref].category) for ref in cited if ref in records
    ]

    met = preference_terms_met(list(preferences), products)
    unmet = [term for term in preferences if term not in met]
    if not unmet:
        return []

    constraints = state.get("constraints", {})
    return [
        NoticeEvent(
            seq=seq + offset,
            message=_preference_unmet(
                [term],
                (state.get("cheapest_preferred") or {}).get(term),
                household_size=constraints.get("household_size", 1),
                days=constraints.get("days", 1),
                budget_nzd=constraints.get("budget_nzd"),
            ),
        )
        for offset, term in enumerate(unmet)
    ]


def finalise(state: GroceryState) -> dict:
    """Terminal node. Always emits `done`, including after an error."""
    events: list[object] = []
    seq = _next_seq(state)

    for comparison in state.get("comparisons") or []:
        events.append(PriceComparisonEvent(seq=seq, data=comparison))
        seq += 1

    plan = state.get("plan")
    if plan is not None:
        # A STATED PREFERENCE THAT DID NOT SURVIVE IS REPORTED, NOT DROPPED.
        #
        # The same obligation as the `no_data` and `skipped` notices in
        # `retrieve_prices`, applied to the one request shape that had no way to
        # express it. The live defect: "i would like to have seafood meal planned
        # for me" at $30 for 3 people over 3 days returned a banana porridge
        # plan. Extraction had inverted the request into an exclusion -- fixed in
        # src/prompts/intent.py -- but even corrected, the plan is identical,
        # because no seafood meal fits $30. Silence made a budget limit look like
        # the assistant ignoring the request.
        #
        # HERE, AND NOT IN `retrieve_prices`, WHICH IS WHERE IT WAS FIRST PUT.
        # That node sees what the model may choose FROM; this one sees what the
        # shopper is handed. `_cost_within_budget` drops the meal with the
        # highest marginal cost, which is very often the preferred one, so the
        # first version could offer a chicken recipe, judge the preference met,
        # and then print a plan with no chicken and no explanation -- the same
        # silent drop, one layer below where it was fixed. Found by dry-running
        # demo 31 rather than by a test, which is the argument for dry runs.
        #
        # `finalise` is the only node that sees the final plan on EVERY path:
        # after the trim, after a repair loop, and on the free-composition
        # fallback that has no recipes at all.
        for notice in _unmet_preference_notices(state, plan, seq):
            events.append(notice)
            seq += 1

        events.append(MealPlanEvent(seq=seq, data=plan))
        seq += 1

    events.append(
        DoneEvent(
            seq=seq,
            server_time=datetime.now(UTC),
            usage=state.get("usage") or UsageMeta(),
        )
    )
    return {"events": events}


# --------------------------------------------------------------- routing


# The three facts a meal plan cannot be built without. Ordered as a person
# would ask for them: who am I feeding, for how long, and for how much.
REQUIRED_FOR_PLAN: tuple[MissingConstraint, ...] = (
    MissingConstraint.HOUSEHOLD_SIZE,
    MissingConstraint.DAYS,
    MissingConstraint.BUDGET_NZD,
)

_CONSTRAINT_QUESTIONS = {
    MissingConstraint.HOUSEHOLD_SIZE: "how many people you're feeding",
    MissingConstraint.DAYS: "how many days it needs to cover",
    MissingConstraint.BUDGET_NZD: "what you'd like to spend",
}


def missing_plan_constraints(state: GroceryState) -> list[MissingConstraint]:
    """
    Which required planning facts the user has not supplied.

    Reads absence rather than inferring a value, which is only possible because
    `classify_intent` stopped defaulting household size and duration to 1. A
    silent default is not a smaller version of this function -- it is this
    function always returning an empty list, and answering a question nobody
    asked.
    """
    constraints = state.get("constraints", {})
    return [field for field in REQUIRED_FOR_PLAN if constraints.get(field.value) is None]


def emit_clarification(state: GroceryState) -> dict:
    """
    Ask for what is missing instead of guessing it.

    Before retrieval, for the same reason as `emit_dietary_unsupported`: there
    is no point pricing a basket for a plan we have already decided we cannot
    build, and a model call spent on an under-specified request is a model call
    wasted against the latency budget.

    The message names every missing fact at once. Asking for them one per turn
    would be three round trips for a request the user could have completed in
    one sentence.
    """
    missing = missing_plan_constraints(state)
    asks = [_CONSTRAINT_QUESTIONS[field] for field in missing]
    return {
        "terminated": True,
        "events": [
            ClarificationEvent(
                seq=_next_seq(state),
                missing=missing,
                message=(
                    f"Happy to plan that — I just need to know {_join(asks)}. "
                    f'For example: "dinner for 3 people for 5 days on $80".'
                ),
            )
        ],
    }


def route_after_intent(state: GroceryState) -> str:
    intent = state.get("intent")
    # An unsupported dietary exclusion refuses the plan BEFORE retrieval:
    # the alternative is filtering an incomplete map at retrieval time and
    # producing a plan we cannot verify. Only meal_plan carries the risk —
    # a price_check for one product does not apply a dietary filter, and
    # blocking it would refuse a legitimate query for no safety benefit.
    if intent == Intent.MEAL_PLAN and state.get("unsupported_exclusions"):
        return "dietary_unsupported"
    # Checked after the dietary refusal: an exclusion we cannot honour is a
    # safety matter and stays the reported reason even when the request is also
    # under-specified. Asking for a budget first would bury it.
    if intent == Intent.MEAL_PLAN and missing_plan_constraints(state):
        # A DEGRADED CLASSIFICATION CANNOT SUPPORT "YOU DID NOT TELL ME".
        #
        # `missing_plan_constraints` reads ABSENCE, and absence is only evidence
        # about the user when something actually read their message. When the
        # model call failed, `classify_intent` falls back to keyword heuristics
        # that do not extract household size, days or budget at all -- so every
        # constraint reads as missing whatever the shopper wrote, and the
        # clarification says "I just need to know your budget" to someone who
        # said "feed 3 people for 5 days on $80".
        #
        # That is the same false statement `emit_upstream_failure` was written
        # to prevent one node further on: reporting an outage as a fact about
        # the request. It is worse here, because the offered remedy is
        # impossible -- rephrasing cannot fix a throttle, and the shopper has
        # been told the fault is theirs. Retrying is the move that works, and
        # only the upstream failure says so.
        #
        # MEASURED, NOT SUPPOSED (Pilot Task 16, gate G6 Phase B, 2026-09-04):
        # under a deliberate 21x quota breach, 14 of 24 turns sending ONE
        # unambiguous message came back as clarifications at intent confidence
        # 0.45 -- the keyword fallback -- while the 8 turns whose throttle
        # landed on a LATER call correctly returned a retryable error.
        # `upstream_error` is deliberately NOT set here, though every other
        # node that reaches this terminal does set it. It is read by
        # `route_after_validation` and `route_after_recipe_selection`, both of
        # which are downstream of this point -- so a degraded classification
        # that DID extract its constraints would carry the field into a
        # perfectly successful recipe selection and be forced onto the
        # upstream-failed path anyway. The cost of leaving it unset is that
        # `emit_upstream_failure` reports INTERNAL_ERROR rather than
        # distinguishing UPSTREAM_TIMEOUT; the message is identical and both
        # are retryable, which is what the shopper acts on.
        if state.get("intent_degraded"):
            return "upstream_failure"
        return "clarify"
    if intent in (Intent.PRICE_CHECK, Intent.MEAL_PLAN):
        return "retrieve"
    return "finalise"


def route_after_retrieval(state: GroceryState) -> str:
    # Checked first: a place we cannot map is a different answer from "nothing
    # in range", and the shopper needs to know which of the two happened.
    if state.get("unknown_region"):
        return "unknown_region"
    if not state.get("citations"):
        # Checked before no_data: "everything I have is out of date" is a
        # different and truer statement than "I have nothing", and only one of
        # them is about the product.
        if state.get("stale_only"):
            return "stale"
        return "no_data"
    # Checked before budget and before "plan": if the shopper asked the plan to
    # be built around foods we cannot match to any product in the current data,
    # that is a more fundamental answer than "your budget does not stretch" --
    # asking for something we carry is the move, not raising the budget. Only
    # fires when EVERY stated preference is unavailable (lenient); a mix with one
    # real food proceeds and `finalise` notices the rest. After no_data/stale,
    # which are catalogue-wide facts that outrank a single preference.
    if state.get("unavailable_preferences"):
        return "preference_unavailable"
    # Checked before "plan": there is no point spending a model call on a
    # request the catalogue's own cheapest prices say is impossible.
    if state.get("budget_impossible"):
        return "infeasible"
    return "plan" if state.get("intent") == Intent.MEAL_PLAN else "comparison"


def route_after_validation(state: GroceryState) -> str:
    """The repair loop's conditional edge."""
    # Checked before validation_errors: an upstream failure produced no plan
    # to repair, so looping would just re-invoke a client we already know is
    # failing — burning two more calls and the latency budget with it — and
    # land on the wrong terminal message.
    if state.get("upstream_error"):
        return "upstream_failed"
    if not state.get("validation_errors"):
        return "finalise"
    if state.get("repair_attempts", 0) >= MAX_REPAIR_ATTEMPTS:
        # Exhausting repair says we failed; it does not say why. Only a plan
        # that was actually costed and came out over budget licenses the
        # budget message. Repair exhausted on malformed drafts is our failure
        # to generate, and telling that user to raise their budget is the same
        # false statement this graph already fixed on the upstream path.
        return "infeasible" if state.get("over_budget") else "generation_failed"
    return "repair"
