"""
Graph nodes.

Every node is a function of state -> partial state, which makes them
independently unit-testable without running the whole graph.

Dietary exclusion mapping lives in `src/graph/dietary.py` — the single
reviewable source of truth for what a user term means and whether it can be
honoured. Nodes here consume `map_exclusions()` output; no node defines its
own mapping.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from src.graph.dietary import map_exclusions, supported_terms
from src.graph.feasibility import minimum_spend
from src.graph.nodes.intent import classify_intent as classify_intent
from src.graph.nodes.plan import generate_plan as generate_plan
from src.graph.nodes.prose import generate_prose as generate_prose
from src.graph.state import MAX_REPAIR_ATTEMPTS, GroceryState
from src.retrieval.base import PriceRepository
from src.schemas.contract import (
    Citation,
    CitationEvent,
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    Intent,
    MealPlanEvent,
    NoDataEvent,
    NoticeEvent,
    PriceComparison,
    PriceComparisonEvent,
    PriceOption,
    SessionEvent,
    SourceRef,
    UsageMeta,
    assert_arithmetic,
    find_literal_money_in_plan,
)

# A pathological request ("prices for fifty things") would blow the latency
# budget against the gateway's 29-second ceiling.
MAX_ITEMS_PER_TURN = 5

MEAL_CATEGORIES = [
    "pantry",
    "produce",
    "meat",
    "dairy",
    "frozen",
    "bakery",
    "chilled",
    "seafood",
]


def _next_seq(state: GroceryState) -> int:
    return len(state.get("events", []))


def _join(items: list[str]) -> str:
    """Human list: 'a', 'a and b', 'a, b and c'. Truncated per item, not overall."""
    clean = [i[:80] for i in items]
    if len(clean) == 1:
        return clean[0]
    return f"{', '.join(clean[:-1])} and {clean[-1]}"


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


def retrieve_prices(state: GroceryState, repo: PriceRepository) -> dict:
    """
    The grounding node. The ONLY place Citations are created.

    For a price check this resolves EVERY item the user asked about. Citation
    refs are numbered globally across all items rather than restarting per
    item, so a ref identifies exactly one price everywhere it appears.

    Items that do not resolve are recorded rather than dropped. A user who
    asks about three things and is answered about two has been quietly
    misled; partial results with an explicit gap are the honest outcome. The
    same applies to items past MAX_ITEMS_PER_TURN, which are named rather than
    discarded — for the user there is no difference between a question that
    was answered wrongly and one that was never acknowledged.
    """
    constraints = state.get("constraints", {})
    intent = state.get("intent")
    household_size = constraints.get("household_size", 1)
    days_covered = constraints.get("days", 1)
    infeasible_upfront = False

    records: list = []
    item_groups: dict[str, list[str]] = {}
    unresolved: list[str] = []
    skipped: list[str] = []
    citations: list[Citation] = []
    events: list = []
    seq = _next_seq(state)
    ref_n = 0

    def add(rec) -> str:
        nonlocal ref_n
        ref_n += 1
        ref = f"c{ref_n}"
        citation = Citation(
            ref=ref,
            store=rec.store,
            store_location=rec.store_location,
            product_name=rec.display_name,
            price_nzd=rec.price_nzd,
            unit=rec.unit,
            unit_price_nzd=rec.unit_price_nzd,
            on_special=rec.on_special,
            valid_date=rec.valid_date,
            source=SourceRef(
                table=repo.table_name,
                pk=rec.store_key,
                sk=rec.product_key,
            ),
        )
        citations.append(citation)
        records.append(rec)
        events.append(CitationEvent(seq=seq + len(events), citation=citation))
        return ref

    if intent == Intent.PRICE_CHECK:
        stores = constraints.get("preferred_stores") or None
        requested = constraints.get("query_items", [])
        skipped = list(requested[MAX_ITEMS_PER_TURN:])
        for term in requested[:MAX_ITEMS_PER_TURN]:
            key = repo.resolve_product_key(term)
            found = repo.cheapest_for_product(key, limit=5, stores=stores) if key else []
            if key is None or not found:
                unresolved.append(term)
                continue
            if key in item_groups:
                continue  # the user named the same product twice
            item_groups[key] = [add(rec) for rec in found]
    else:
        # `map_exclusions` returns (categories, unsupported). classify_intent
        # already recorded the unsupported terms and the router refuses the
        # turn before it reaches here, so retrieval trusts that the mapping
        # exists — anything unmappable that got this far is a routing bug and
        # must fail rather than silently produce an unsafe plan.
        exclude_categories, unsupported = map_exclusions(constraints.get("dietary_exclusions", []))
        if unsupported:
            raise RuntimeError(
                f"routing bug: retrieve_prices reached with unsupported "
                f"exclusions {unsupported!r}. classify_intent should have "
                f"routed to emit_dietary_unsupported."
            )
        # The budget goes to retrieval, not to the model. The model never
        # sees a price, so it cannot keep itself inside a budget; what it CAN
        # be given is a candidate set where every possible selection is
        # affordable. Without this the plan node could only discover the
        # overspend afterwards, and its one repair lever -- smaller portions --
        # does not reduce what a pack costs.
        budget = constraints.get("budget_nzd")
        candidates = repo.candidates_for_budget(
            categories=MEAL_CATEGORIES,
            exclude_categories=exclude_categories,
            limit_per_category=3,
            budget_nzd=budget,
        )

        # Refuse before generating when the budget cannot cover the request at
        # any price. Checked here rather than after costing a draft because
        # the candidate set is now capped to the budget, so a draft built from
        # it always "fits" -- affordability alone can no longer tell us the
        # request was possible.
        if budget is not None:
            floor = minimum_spend(candidates, household_size, days_covered)
            if floor is not None and budget < floor:
                infeasible_upfront = True

        for rec in candidates:
            add(rec)

    # Honest gaps for items we could not answer, alongside the ones we could.
    # Only when SOME items resolved; if none did, routing sends the turn to
    # the terminal no_data path instead.
    if item_groups and unresolved:
        for term in unresolved:
            events.append(
                NoDataEvent(
                    seq=seq + len(events),
                    requested_item=term[:80],
                    message=f"I don't have price data for {term}.",
                )
            )

    # Items we never looked at, because the request exceeded the cap. A notice
    # rather than a no_data event: we are not claiming there is no price for
    # these, only that we did not check. Emitted even when nothing resolved,
    # since "I checked five of your seven items and found nothing" and "I found
    # nothing" are different statements and only one of them is true.
    if skipped:
        events.append(
            NoticeEvent(
                seq=seq + len(events),
                message=(
                    f"I can look up {MAX_ITEMS_PER_TURN} items at a time, so I "
                    f"didn't check {_join(skipped)}. Ask me again for those."
                ),
            )
        )

    return {
        "records": records,
        "citations": citations,
        "citation_index": {c.ref: c for c in citations},
        "record_index": dict(zip([c.ref for c in citations], records, strict=False)),
        "item_groups": item_groups,
        "unresolved_items": unresolved,
        "skipped_items": skipped,
        "budget_impossible": infeasible_upfront,
        "events": events,
    }


def emit_no_data(state: GroceryState) -> dict:
    """The 'I don't have data for that' path. A SUCCESS outcome, not an error."""
    items = state.get("unresolved_items") or state.get("constraints", {}).get("query_items", [])
    if not items:
        items = ["that item"]

    seq = _next_seq(state)
    return {
        "terminated": True,
        "events": [
            NoDataEvent(
                seq=seq + i,
                requested_item=item[:80],
                message=(
                    f"I don't have price data for {item} at any of the stores "
                    f"near you. I can check something else if you like."
                ),
            )
            for i, item in enumerate(items)
        ],
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


def finalise(state: GroceryState) -> dict:
    """Terminal node. Always emits `done`, including after an error."""
    events: list[object] = []
    seq = _next_seq(state)

    for comparison in state.get("comparisons") or []:
        events.append(PriceComparisonEvent(seq=seq, data=comparison))
        seq += 1

    plan = state.get("plan")
    if plan is not None:
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


def route_after_intent(state: GroceryState) -> str:
    intent = state.get("intent")
    # An unsupported dietary exclusion refuses the plan BEFORE retrieval:
    # the alternative is filtering an incomplete map at retrieval time and
    # producing a plan we cannot verify. Only meal_plan carries the risk —
    # a price_check for one product does not apply a dietary filter, and
    # blocking it would refuse a legitimate query for no safety benefit.
    if intent == Intent.MEAL_PLAN and state.get("unsupported_exclusions"):
        return "dietary_unsupported"
    if intent in (Intent.PRICE_CHECK, Intent.MEAL_PLAN):
        return "retrieve"
    return "finalise"


def route_after_retrieval(state: GroceryState) -> str:
    if not state.get("citations"):
        return "no_data"
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
