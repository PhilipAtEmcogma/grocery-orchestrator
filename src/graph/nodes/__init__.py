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
    PriceComparison,
    PriceComparisonEvent,
    PriceOption,
    SessionEvent,
    SourceRef,
    UsageMeta,
    assert_arithmetic,
)

# A pathological request ("prices for fifty things") would blow the latency
# budget against the gateway's 29-second ceiling.
MAX_ITEMS_PER_TURN = 5

MEAL_CATEGORIES = [
    "pantry", "produce", "meat", "dairy", "frozen", "bakery", "chilled", "seafood",
]


def _next_seq(state: GroceryState) -> int:
    return len(state.get("events", []))


def _exclusion_categories(exclusions: list[str]) -> list[str]:
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

    For a price check this resolves EVERY item the user asked about. Citation
    refs are numbered globally across all items rather than restarting per
    item, so a ref identifies exactly one price everywhere it appears.

    Items that do not resolve are recorded rather than dropped. A user who
    asks about three things and is answered about two has been quietly
    misled; partial results with an explicit gap are the honest outcome.
    """
    constraints = state.get("constraints", {})
    intent = state.get("intent")

    records: list = []
    item_groups: dict[str, list[str]] = {}
    unresolved: list[str] = []
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
                table="Products",
                pk=f"{rec.store.value}#{rec.category}",
                sk=rec.product_key,
            ),
        )
        citations.append(citation)
        records.append(rec)
        events.append(CitationEvent(seq=seq + len(events), citation=citation))
        return ref

    if intent == Intent.PRICE_CHECK:
        stores = constraints.get("preferred_stores") or None
        for term in constraints.get("query_items", [])[:MAX_ITEMS_PER_TURN]:
            key = repo.resolve_product_key(term)
            found = (
                repo.cheapest_for_product(key, limit=5, stores=stores) if key else []
            )
            if key is None or not found:
                unresolved.append(term)
                continue
            if key in item_groups:
                continue  # the user named the same product twice
            item_groups[key] = [add(rec) for rec in found]
    else:
        for rec in repo.candidates_for_budget(
            categories=MEAL_CATEGORIES,
            exclude_categories=_exclusion_categories(
                constraints.get("dietary_exclusions", [])
            ),
            limit_per_category=3,
        ):
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

    return {
        "records": records,
        "citations": citations,
        "citation_index": {c.ref: c for c in citations},
        "record_index": dict(zip([c.ref for c in citations], records, strict=False)),
        "item_groups": item_groups,
        "unresolved_items": unresolved,
        "events": events,
    }


def emit_no_data(state: GroceryState) -> dict:
    """The 'I don't have data for that' path. A SUCCESS outcome, not an error."""
    items = state.get("unresolved_items") or state.get("constraints", {}).get(
        "query_items", []
    )
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
                            dearest.price_nzd - c.price_nzd
                            if c.ref == cheapest.ref
                            else None
                        ),
                    )
                    for c in options
                ],
                reasoning=(
                    f"{cheapest.store.value.replace('_', ' ').title()} "
                    f"{cheapest.store_location} is cheapest at "
                    f"${cheapest.price_nzd} for {cheapest.unit}."
                ),
            )
        )

    return {"comparisons": comparisons}


def validate_plan(state: GroceryState) -> dict:
    """Arithmetic verification. Never trust model-computed totals."""
    plan = state.get("plan")
    if plan is None:
        return {"validation_errors": ["no plan produced"]}

    errors: list[str] = []
    try:
        assert_arithmetic(plan)
    except AssertionError as exc:
        errors.append(str(exc))

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
    if state.get("intent") in (Intent.PRICE_CHECK, Intent.MEAL_PLAN):
        return "retrieve"
    return "finalise"


def route_after_retrieval(state: GroceryState) -> str:
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
