"""
Graph nodes.

Every node is a function of state -> partial state, which makes them
independently unit-testable without running the whole graph.

STUBBED at this stage: classify_intent, generate_comparison, generate_plan
return deterministic output with no model call. The TOPOLOGY is real.
Filling each stub is a separate increment that does not touch the graph.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from src.graph.nodes.intent import classify_intent as classify_intent
from src.graph.nodes.plan import generate_plan as generate_plan
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
    PriceComparison,
    PriceComparisonEvent,
    PriceOption,
    SessionEvent,
    SourceRef,
    UsageMeta,
    assert_arithmetic,
)

MEAL_CATEGORIES = [
    "pantry", "produce", "meat", "dairy", "frozen", "bakery", "chilled", "seafood",
]


def _next_seq(state: GroceryState) -> int:
    """The next event sequence number: one past however many events exist so far."""
    return len(state.get("events", []))


def _exclusion_categories(exclusions: list[str]) -> list[str]:
    """Map free-text dietary exclusions (e.g. 'vegetarian') to fixture categories
    (e.g. 'meat', 'seafood') that retrieve_prices can filter out."""
    out: set[str] = set()
    for ex in exclusions:
        low = ex.lower()
        if low in {"seafood", "fish"}:
            out.add("seafood")
        if low in {"vegetarian", "no meat"}:
            out |= {"meat", "seafood"}
        if low in {"dairy-free", "no dairy"}:
            out.add("dairy")
    return sorted(out)


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

    Downstream nodes may reference these but cannot add to them, which is what
    makes the grounding invariant hold by construction.
    """
    constraints = state.get("constraints", {})
    intent = state.get("intent")
    key: str | None = None

    if intent == Intent.PRICE_CHECK:
        # Resolve the free-text query to a canonical product, then fetch that
        # product's prices across stores (cheapest first), optionally
        # restricted to the user's preferred stores.
        key = repo.resolve_product_key(constraints.get("query_item", ""))
        records = (
            repo.cheapest_for_product(
                key, limit=5, stores=constraints.get("preferred_stores") or None
            )
            if key
            else []
        )
    else:
        # Meal planning: pull cheap candidates across every category the meal
        # planner might use, skipping categories excluded on dietary grounds.
        records = repo.candidates_for_budget(
            categories=MEAL_CATEGORIES,
            exclude_categories=_exclusion_categories(
                constraints.get("dietary_exclusions", [])
            ),
            limit_per_category=3,
        )

    citations: list[Citation] = []
    events: list[CitationEvent] = []
    seq = _next_seq(state)

    # Turn each internal PriceRecord into a wire-format Citation with a
    # short ref ("c1", "c2", ...) that later nodes/prompts refer back to,
    # and emit one CitationEvent per record so the frontend sees the facts
    # before any payload that relies on them.
    for i, rec in enumerate(records, start=1):
        citation = Citation(
            ref=f"c{i}",
            store=rec.store,
            store_location=rec.store_location,
            product_name=rec.display_name,
            price_nzd=rec.price_nzd,
            unit=rec.unit,
            unit_price_nzd=rec.unit_price_nzd,
            on_special=rec.on_special,
            valid_date=rec.valid_date,
            source=SourceRef(
                table="Products",
                pk=f"{rec.store.value}#{rec.category}",
                sk=rec.product_key,
            ),
        )
        citations.append(citation)
        events.append(CitationEvent(seq=seq + i - 1, citation=citation))

    return {
        "records": records,
        "citations": citations,
        "citation_index": {c.ref: c for c in citations},
        "record_index": dict(zip([c.ref for c in citations], records, strict=False)),
        "resolved_product_key": key,
        "events": events,
    }


def emit_no_data(state: GroceryState) -> dict:
    """The 'I don't have data for that' path. A SUCCESS outcome, not an error."""
    item = state.get("constraints", {}).get("query_item", "that item")
    return {
        "terminated": True,
        "events": [
            NoDataEvent(
                seq=_next_seq(state),
                requested_item=item[:80],
                message=(
                    "I don't have price data for that at any of the stores near you. "
                    "I can check a different item if you like."
                ),
            )
        ],
    }


def generate_comparison(state: GroceryState) -> dict:
    """
    STUB — assembles the comparison from citations only.

    This stub CANNOT hallucinate: it reads state['citations'] and nothing else.
    When Bedrock replaces it, that property must be preserved by construction,
    not by prompt instruction.
    """
    citations = state.get("citations") or []
    if not citations:
        # Unreachable via route_after_retrieval, which sends empty results to
        # emit_no_data. Guarded anyway: a comparison with no citations would
        # be an ungrounded response, which must never ship.
        return {"comparison": None}

    # citations is already sorted cheapest-first by the repository.
    cheapest, dearest = citations[0], citations[-1]

    # Build one PriceOption per store, flagging the cheapest and computing
    # its savings versus the priciest option.
    options = [
        PriceOption(
            citation_ref=c.ref,
            is_cheapest=(c.ref == cheapest.ref),
            savings_vs_dearest_nzd=(
                dearest.price_nzd - c.price_nzd if c.ref == cheapest.ref else None
            ),
        )
        for c in citations
    ]

    comparison = PriceComparison(
        query_item=state.get("resolved_product_key") or "item",
        options=options,
        reasoning=(
            f"{cheapest.store.value.replace('_', ' ').title()} "
            f"{cheapest.store_location} is cheapest at ${cheapest.price_nzd} "
            f"for {cheapest.unit}."
        ),
    )
    return {"comparison": comparison}


def validate_plan(state: GroceryState) -> dict:
    """Arithmetic verification. Never trust model-computed totals."""
    plan = state.get("plan")
    if plan is None:
        return {"validation_errors": ["no plan produced"]}

    errors: list[str] = []
    # Re-derive every subtotal/total from ingredient line costs; catch it if
    # the plan's numbers don't add up.
    try:
        assert_arithmetic(plan)
    except AssertionError as exc:
        errors.append(str(exc))

    # A mathematically-consistent plan can still be over budget; that's a
    # separate error that triggers the same repair loop.
    if plan.total_nzd > plan.budget_nzd:
        errors.append(
            f"total {plan.total_nzd} exceeds budget {plan.budget_nzd} "
            f"by {plan.total_nzd - plan.budget_nzd}"
        )
    return {"validation_errors": errors}


def repair_plan(state: GroceryState) -> dict:
    """Increments the attempt counter; regeneration happens on the loop back."""
    return {"repair_attempts": state.get("repair_attempts", 0) + 1}


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

    # Emit whichever payload this turn produced (at most one of the two).
    comparison = state.get("comparison")
    if comparison is not None:
        events.append(PriceComparisonEvent(seq=seq, data=comparison))
        seq += 1

    plan = state.get("plan")
    if plan is not None:
        events.append(MealPlanEvent(seq=seq, data=plan))
        seq += 1

    # The terminal event every turn ends with, success or failure.
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
    """Only price_check/meal_plan need retrieved prices; other intents skip straight to finalise."""
    if state.get("intent") in (Intent.PRICE_CHECK, Intent.MEAL_PLAN):
        return "retrieve"
    return "finalise"


def route_after_retrieval(state: GroceryState) -> str:
    """No citations means nothing was found; otherwise branch on which payload to build."""
    if not state.get("citations"):
        return "no_data"
    return "plan" if state.get("intent") == Intent.MEAL_PLAN else "comparison"


def route_after_validation(state: GroceryState) -> str:
    """The repair loop's conditional edge."""
    if not state.get("validation_errors"):
        return "finalise"
    if state.get("repair_attempts", 0) >= MAX_REPAIR_ATTEMPTS:
        return "infeasible"
    return "repair"
