"""
Smart Grocery & Meal Budget Assistant — Frontend/Orchestrator Contract v1.0

This module is the SINGLE SOURCE OF TRUTH for the interface between the
frontend chatbot and the orchestrator Lambda.

Design principles
-----------------
1. EVENT-SHAPED. The response is always a list of events. Over REST the whole
   list is returned at once; over WebSocket the same events are emitted one at
   a time. The transport can change without the contract changing.

2. GROUNDING IS STRUCTURAL. Every price in the response references a Citation
   by `ref`. Prices are never restated inline. A price with no citation is a
   contract violation, which makes "never hallucinate a price" testable.

3. HONEST FAILURE. `no_data` and `budget_infeasible` are first-class outcomes,
   not errors to be papered over.

Owner: Backend/Orchestration + AI/Prompt Lead
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.0"


# ---------------------------------------------------------------- enums


# The four kinds of turn the assistant can classify a message as.
class Intent(StrEnum):
    PRICE_CHECK = "price_check"
    MEAL_PLAN = "meal_plan"
    GENERAL_CHAT = "general_chat"
    OUT_OF_SCOPE = "out_of_scope"


# Machine-readable failure/status codes carried on ErrorEvent.
class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    NO_DATA = "NO_DATA"
    STALE_DATA = "STALE_DATA"
    BUDGET_INFEASIBLE = "BUDGET_INFEASIBLE"
    GUARDRAIL_BLOCKED = "GUARDRAIL_BLOCKED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# The three supermarket chains the price data covers.
class Store(StrEnum):
    PAKNSAVE = "paknsave"
    WOOLWORTHS = "woolworths"
    NEW_WORLD = "new_world"


# ---------------------------------------------------------------- request


# The user's approximate location, used to scope which stores are relevant.
class Location(BaseModel):
    # Reject unknown fields instead of silently ignoring them.
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    label: str | None = Field(default=None, max_length=120)
    radius_km: float = Field(default=10.0, gt=0, le=50)


class ClientHints(BaseModel):
    """
    Optional structured hints from frontend UI controls.

    These SUPPLEMENT natural-language extraction, they do not replace it.
    If a hint conflicts with the user's message, the message wins and the
    orchestrator emits a `notice` event explaining the override.
    """

    model_config = ConfigDict(extra="forbid")

    # All fields are optional: a hint the frontend hasn't collected yet is
    # simply absent, not a validation error.
    household_size: int | None = Field(default=None, ge=1, le=20)
    budget_nzd: Decimal | None = Field(default=None, gt=0, le=10000)
    days: int | None = Field(default=None, ge=1, le=14)
    dietary_exclusions: list[str] = Field(default_factory=list, max_length=20)
    preferred_stores: list[Store] = Field(default_factory=list)


# The full inbound payload the orchestrator receives for one chat turn.
class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = CONTRACT_VERSION
    session_id: str = Field(min_length=8, max_length=64)
    turn_id: str = Field(
        min_length=8,
        max_length=64,
        description="Client-generated, unique per turn. Used for idempotency.",
    )
    message: str = Field(
        min_length=1,
        max_length=2000,
        description="Raw user text. Treated as UNTRUSTED by the orchestrator.",
    )
    location: Location | None = None
    hints: ClientHints | None = None


# ---------------------------------------------------------------- grounding


class SourceRef(BaseModel):
    """Pointer to the exact DynamoDB record a price came from."""

    model_config = ConfigDict(extra="forbid")

    table: str
    pk: str
    sk: str


class Citation(BaseModel):
    """
    A grounded price fact. Emitted BEFORE any event that references it.

    Every monetary figure shown to the user must trace to one of these.
    """

    model_config = ConfigDict(extra="forbid")

    ref: str = Field(pattern=r"^c\d+$", description="Reference id, e.g. 'c1'")
    store: Store
    store_location: str
    product_name: str
    price_nzd: Decimal = Field(ge=0)
    unit: str = Field(description="e.g. '500g', 'each', 'per kg'")
    unit_price_nzd: Decimal | None = Field(default=None, ge=0)
    on_special: bool = False
    valid_date: date
    source: SourceRef  # points back to the exact DB record, for auditing


# ---------------------------------------------------------------- payloads
# The actual content shown to the user: price comparisons and meal plans.
# Every price field here traces back to a Citation via citation_ref.


# One store's price for the compared item, as a line in the comparison table.
class PriceOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_ref: str = Field(pattern=r"^c\d+$")
    is_cheapest: bool = False
    savings_vs_dearest_nzd: Decimal | None = Field(default=None, ge=0)


# The price_check response payload: one item, compared across stores.
class PriceComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_item: str
    options: list[PriceOption] = Field(min_length=1)
    reasoning: str = Field(
        description="Why the cheapest was chosen. Must reference stores/prices "
        "only via cited facts."
    )


# One line item within a meal — an ingredient, its quantity, and its cost.
class Ingredient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str
    qty: str = Field(description="Human-readable, e.g. '500g', '2 cloves'")
    citation_ref: str = Field(pattern=r"^c\d+$")
    line_cost_nzd: Decimal = Field(ge=0)


# One meal within a plan: a name, who it serves, and its ingredient list.
class Meal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    serves: int = Field(ge=1)
    ingredients: list[Ingredient] = Field(min_length=1)
    subtotal_nzd: Decimal = Field(ge=0)


class StoreBasket(BaseModel):
    """Shopping list split by store — the actionable output for the user."""

    model_config = ConfigDict(extra="forbid")

    store: Store
    store_location: str
    citation_refs: list[str] = Field(min_length=1)
    basket_total_nzd: Decimal = Field(ge=0)


# The meal_plan response payload: several meals plus a per-store shopping list.
class MealPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    household_size: int = Field(ge=1)
    days: int = Field(ge=1)
    budget_nzd: Decimal = Field(gt=0)
    total_nzd: Decimal = Field(ge=0)
    within_budget: bool
    repair_attempts: int = Field(
        default=0, ge=0, description="Validate-and-repair cycles used. Observability."
    )
    meals: list[Meal] = Field(min_length=1)
    baskets: list[StoreBasket] = Field(min_length=1)
    dietary_exclusions_applied: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- events


# Shared base for every event type: just the ordering number `seq`.
class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=0, description="Monotonic within a turn. Ordering guarantee.")


# First event of every turn: confirms which session/turn this response is for.
class SessionEvent(_Event):
    type: Literal["session"] = "session"
    session_id: str
    turn_id: str
    version: str = CONTRACT_VERSION


class IntentEvent(_Event):
    """Lets the frontend switch UI treatment before content arrives."""

    type: Literal["intent"] = "intent"
    intent: Intent
    confidence: float = Field(ge=0, le=1)


# Announces one grounded price fact. Must appear before anything that cites it.
class CitationEvent(_Event):
    type: Literal["citation"] = "citation"
    citation: Citation


class TokenEvent(_Event):
    """Streaming text delta. Over REST, these are pre-joined but still emitted."""

    type: Literal["token"] = "token"
    text: str


# Carries the finished price-comparison payload.
class PriceComparisonEvent(_Event):
    type: Literal["price_comparison"] = "price_comparison"
    data: PriceComparison


# Carries the finished meal-plan payload.
class MealPlanEvent(_Event):
    type: Literal["meal_plan"] = "meal_plan"
    data: MealPlan


class NoticeEvent(_Event):
    """Non-fatal information, e.g. a hint was overridden or data is 3 days old."""

    type: Literal["notice"] = "notice"
    message: str


class NoDataEvent(_Event):
    """
    The explicit "I don't have data for that" outcome.

    This is a SUCCESS path, not an error. Never substitute a guess.
    """

    type: Literal["no_data"] = "no_data"
    requested_item: str | None = None
    message: str


# A failure outcome (e.g. budget infeasible, guardrail blocked).
class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    code: ErrorCode
    message: str = Field(description="Safe to display to the user.")
    retryable: bool = False


# Token counts, latency and model ids for the turn — observability, not shown to the user.
class UsageMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_ids: list[str] = Field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    guardrail_intervened: bool = False


class DoneEvent(_Event):
    """Terminal event. Always emitted, including after an error."""

    type: Literal["done"] = "done"
    usage: UsageMeta | None = None
    server_time: datetime


# The tagged union of every event kind. `discriminator="type"` tells pydantic
# to pick the right subtype by reading the `type` field, so parsing a raw
# event dict/JSON automatically produces the correct concrete class.
Event = Annotated[
    SessionEvent
    | IntentEvent
    | CitationEvent
    | TokenEvent
    | PriceComparisonEvent
    | MealPlanEvent
    | NoticeEvent
    | NoDataEvent
    | ErrorEvent
    | DoneEvent,
    Field(discriminator="type"),
]


class ChatResponse(BaseModel):
    """
    REST envelope. Over WebSocket, each event is sent individually with the
    identical schema — so the frontend's event handler is transport-agnostic.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = CONTRACT_VERSION
    session_id: str
    turn_id: str
    events: list[Event] = Field(min_length=1)


# ---------------------------------------------------------------- invariants


def assert_grounded(response: ChatResponse) -> None:
    """
    Contract invariant check. Run this in CI against every response.

    Enforces: every citation_ref used by a payload was actually emitted as a
    CitationEvent earlier in the same turn. This is the mechanical expression
    of "no price may originate from model generation".
    """
    declared: set[str] = set()  # refs announced via a CitationEvent
    used: set[str] = set()      # refs actually referenced by payload data

    # Walk the whole event list, collecting which refs were declared and
    # which were used by any price-comparison or meal-plan payload.
    for ev in response.events:
        if isinstance(ev, CitationEvent):
            declared.add(ev.citation.ref)
        elif isinstance(ev, PriceComparisonEvent):
            used.update(o.citation_ref for o in ev.data.options)
        elif isinstance(ev, MealPlanEvent):
            for meal in ev.data.meals:
                used.update(i.citation_ref for i in meal.ingredients)
            for basket in ev.data.baskets:
                used.update(basket.citation_refs)

    # Any ref used but never declared is an ungrounded (hallucinated) price.
    orphans = used - declared
    if orphans:
        raise AssertionError(f"Ungrounded citation refs: {sorted(orphans)}")

    if not any(isinstance(ev, DoneEvent) for ev in response.events):
        raise AssertionError("Response missing terminal 'done' event")


def assert_arithmetic(plan: MealPlan, tolerance: Decimal = Decimal("0.02")) -> None:
    """
    Verify the model did not invent totals. Run before emitting a MealPlanEvent.
    Failure here should trigger a repair cycle, not be passed to the user.
    """
    # Recompute each meal's subtotal from its ingredient line costs and
    # compare against the stored value, within a small rounding tolerance.
    for meal in plan.meals:
        expected = sum((i.line_cost_nzd for i in meal.ingredients), Decimal(0))
        if abs(expected - meal.subtotal_nzd) > tolerance:
            raise AssertionError(
                f"Meal '{meal.name}' subtotal {meal.subtotal_nzd} != {expected}"
            )

    # Same check for the plan-level total against the sum of meal subtotals.
    expected_total = sum((m.subtotal_nzd for m in plan.meals), Decimal(0))
    if abs(expected_total - plan.total_nzd) > tolerance:
        raise AssertionError(f"Plan total {plan.total_nzd} != {expected_total}")

    # The within_budget flag must agree with the actual total vs. budget.
    if (plan.total_nzd <= plan.budget_nzd) != plan.within_budget:
        raise AssertionError("within_budget flag contradicts the arithmetic")
