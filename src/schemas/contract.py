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

import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Protocol

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
    # We could not build a plan we were willing to stand behind — repair
    # exhausted on drafts that failed validation, not on price. Separate from
    # BUDGET_INFEASIBLE because the budget may be perfectly generous, and
    # separate from INTERNAL_ERROR because the model plane is up and
    # answering. Additive under the v1 rules: clients tolerate unknown codes.
    PLAN_GENERATION_FAILED = "PLAN_GENERATION_FAILED"
    # An honest refusal when the user states a dietary exclusion we cannot
    # guarantee against our current data. Additive per Req 7.9 — dropping a
    # restriction is the dangerous direction of error, so the safe response
    # is refusal, not a best-effort plan.
    UNSUPPORTED_EXCLUSION = "UNSUPPORTED_EXCLUSION"
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
    total_nzd: Decimal = Field(
        ge=0,
        description=(
            "Value CONSUMED: line costs at fractional pack multipliers. Using "
            "500g of a 1kg pack contributes half that pack's price. This is "
            "not what the shopper pays and must not be used for budget "
            "messaging -- see payable_total_nzd."
        ),
    )
    payable_total_nzd: Decimal = Field(
        ge=0,
        description=(
            "Money actually PAYABLE: every distinct pack counted once at its "
            "full shelf price, because half a pack of butter cannot be "
            "bought. Equals the sum of the store baskets, and is the "
            "authoritative figure for anything the user is told about cost."
        ),
    )
    within_budget: bool = Field(
        description=(
            "payable_total_nzd <= budget_nzd. Computed from what the shopper "
            "pays, not from what the meals consume."
        ),
    )
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

    Enforces:
    1. Every citation_ref used by a payload was emitted as a CitationEvent
       BEFORE the event that uses it (ordering).
    2. Every CitationEvent source identifies a plausible base-table record:
       table name is non-empty, pk matches <store>#<location-slug>, and sk is
       a normalized product key.
    3. Citation price_nzd and unit_price_nzd are non-negative Decimals (schema
       already enforces, but verified here for completeness).
    4. A terminal 'done' event is present.

    NOT enforced here: literal money in prose. That is
    `assert_no_literal_money_in_response`, and it is deliberately a separate
    call rather than folded in, because the two have different consequences.
    This function runs inside `run_turn` on every response, so anything it
    rejects fails the whole turn; a model writing a price into its prose is
    instead handled at the prose node, which drops the sentence and still
    delivers the cited comparison. Raising here would turn that graceful
    degradation into a dead turn.

    This docstring previously listed the money rule as enforced. It was not,
    and had not been -- a reader following "run this in CI against every
    response" would have believed prose was covered by it. `validate.py`
    calls both, which is the pairing to copy.
    """
    # Track citations declared so far (order-sensitive).
    declared: dict[str, Citation] = {}
    violations: list[str] = []

    for ev in response.events:
        if isinstance(ev, CitationEvent):
            c = ev.citation
            declared[c.ref] = c

            # Source key structure validation.
            if not c.source.table:
                violations.append(f"{c.ref}: source.table is empty")
            if "#" not in c.source.pk:
                violations.append(f"{c.ref}: source.pk {c.source.pk!r} missing '#' separator")
            if not c.source.sk:
                violations.append(f"{c.ref}: source.sk is empty")

        elif isinstance(ev, PriceComparisonEvent):
            for opt in ev.data.options:
                if opt.citation_ref not in declared:
                    violations.append(
                        f"PriceComparison uses {opt.citation_ref} before it "
                        f"was declared (or never declared)"
                    )

        elif isinstance(ev, MealPlanEvent):
            for meal in ev.data.meals:
                for ing in meal.ingredients:
                    if ing.citation_ref not in declared:
                        violations.append(
                            f"MealPlan ingredient uses {ing.citation_ref} "
                            f"before it was declared (or never declared)"
                        )
            for basket in ev.data.baskets:
                for ref in basket.citation_refs:
                    if ref not in declared:
                        violations.append(
                            f"StoreBasket uses {ref} before it was declared (or never declared)"
                        )

    if not any(isinstance(ev, DoneEvent) for ev in response.events):
        violations.append("Response missing terminal 'done' event")

    if violations:
        raise AssertionError(
            f"Grounding violations ({len(violations)}):\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


class RetrievedRecord(Protocol):
    """
    The retrieved-record shape `assert_citations_match_retrieval` compares
    against. `src.retrieval.base.PriceRecord` satisfies it structurally.

    Declared as a Protocol rather than imported, and that is not stylistic:
    `retrieval/base.py` imports `Store` from this module, so importing
    PriceRecord here would close a cycle. A Protocol states exactly the fields
    the equality proof needs and nothing else, which also documents the
    coupling instead of hiding it behind a concrete class.

    Members are read-only properties rather than plain attributes, which is
    load-bearing: `PriceRecord` is a FROZEN dataclass, and a Protocol declaring
    mutable attributes demands settable ones, so the concrete type would not
    satisfy it. Read-only is also the honest declaration — this rule compares
    retrieved values, it never assigns them.
    """

    @property
    def product_key(self) -> str: ...
    @property
    def store(self) -> Store: ...
    @property
    def store_location(self) -> str: ...
    @property
    def display_name(self) -> str: ...
    @property
    def price_nzd(self) -> Decimal: ...
    @property
    def unit(self) -> str: ...
    @property
    def unit_price_nzd(self) -> Decimal: ...
    @property
    def on_special(self) -> bool: ...
    @property
    def valid_date(self) -> str: ...
    @property
    def store_key(self) -> str: ...


def assert_citations_match_retrieval(
    response: ChatResponse,
    *,
    table: str,
    records: Mapping[str, RetrievedRecord],
) -> None:
    """
    Req 3.5: every citation must BE a record that was actually retrieved.

    `assert_grounded` cannot do this and never could. It sees only the
    response, so it can check that a ref was declared before use and that the
    source keys are shaped like keys -- `table` non-empty, a `#` in the pk, a
    non-empty sk. Shape is not identity. A citation naming the right table with
    a plausible pk and a price nobody retrieved passed it cleanly, which meant
    the system's central claim rested on the fact that no code path currently
    fabricates one, rather than on a check that would notice if one did.

    This closes that by comparing each Citation against the immutable
    `PriceRecord` the retrieval node kept for it:

    * the ref was retrieved at all (an unknown ref is a fabricated citation)
    * table, partition key and sort key identify that exact stored record
    * every published value equals the retrieved value

    `records` is keyed by citation ref and comes from `GroceryState`'s
    `record_index`, which only `retrieve_prices` writes. `PriceRecord` is a
    frozen slots dataclass, so what is compared cannot have been edited between
    retrieval and here -- "immutable retrieved context" is a property of the
    type, not a convention.

    Raises rather than returning findings: Req 3.5 says refuse the response,
    and by the time this runs there is no repair available. `run_turn` calls
    it, which is the only place holding both the response and the state.

    NOT called by `validate.py` over `samples/`, because a committed sample has
    no retrieval context to compare against -- the samples prove shape, this
    proves identity, and conflating the two is what let shape stand in for
    identity in the first place. `validate.py` carries the wrong-key and
    altered-value negative controls Req 3.6 names, built against a stub record.
    """
    violations: list[str] = []

    for ev in response.events:
        if not isinstance(ev, CitationEvent):
            continue
        c = ev.citation
        rec = records.get(c.ref)

        if rec is None:
            # The dangerous one. Not "a payload referenced an undeclared ref"
            # (assert_grounded's check) but "a citation exists that retrieval
            # never produced" -- a fabricated price, correctly shaped.
            violations.append(f"{c.ref}: no retrieved record — this citation was not retrieved")
            continue

        for label, published, retrieved in (
            ("source.table", c.source.table, table),
            ("source.pk", c.source.pk, rec.store_key),
            ("source.sk", c.source.sk, rec.product_key),
            ("store", c.store, rec.store),
            ("store_location", c.store_location, rec.store_location),
            ("product_name", c.product_name, rec.display_name),
            ("price_nzd", c.price_nzd, rec.price_nzd),
            ("unit", c.unit, rec.unit),
            ("unit_price_nzd", c.unit_price_nzd, rec.unit_price_nzd),
            ("on_special", c.on_special, rec.on_special),
            ("valid_date", c.valid_date.isoformat(), rec.valid_date),
        ):
            if published != retrieved:
                violations.append(
                    f"{c.ref}: {label} is {published!r}, retrieved record has {retrieved!r}"
                )

    if violations:
        raise AssertionError(
            f"Citations do not match retrieval ({len(violations)}):\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


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
            raise AssertionError(f"Meal '{meal.name}' subtotal {meal.subtotal_nzd} != {expected}")

    # Same check for the plan-level total against the sum of meal subtotals.
    expected_total = sum((m.subtotal_nzd for m in plan.meals), Decimal(0))
    if abs(expected_total - plan.total_nzd) > tolerance:
        raise AssertionError(f"Plan total {plan.total_nzd} != {expected_total}")

    # The payable amount is the sum of the store baskets, each of which counts
    # a pack once at full price. Verified rather than trusted for the same
    # reason as every other figure here.
    expected_payable = sum((b.basket_total_nzd for b in plan.baskets), Decimal(0))
    if abs(expected_payable - plan.payable_total_nzd) > tolerance:
        raise AssertionError(
            f"Payable total {plan.payable_total_nzd} != {expected_payable} (sum of store baskets)"
        )

    # within_budget is a claim about what the shopper pays, so it is checked
    # against the payable amount.
    #
    # It used to be checked against total_nzd, the CONSUMPTION figure, and a
    # plan could therefore report within_budget=True while its shopping list
    # cost nearly twice the budget: $34.39 "of $60" against baskets totalling
    # $65.01. Consumption is the value the meals use; payable is the money
    # that leaves the shopper's account, and only the second one can answer
    # "can I afford this".
    if (plan.payable_total_nzd <= plan.budget_nzd) != plan.within_budget:
        raise AssertionError(
            f"within_budget={plan.within_budget} contradicts payable "
            f"{plan.payable_total_nzd} vs budget {plan.budget_nzd}"
        )


# Money-shaped strings that must never appear in user-visible prose-like fields.
#
# THE single definition. `src/prompts/prose.py` imports this one rather than
# keeping its own -- it previously held a byte-for-byte equivalent copy, and
# two copies of a safety rule drift the moment one of them is tuned. The prose
# node's check is what lets prose degrade; this module's check is what refuses
# a response. They must agree by construction, not by review.
#
# Deliberately narrow: "3 meals", "500g" and "2 people" are legitimate and must
# pass. Known over-match: a two-decimal number before a space and a unit
# ("1.25 kg") reads as money. Nothing in `fixtures/` or in the 585 records
# under `datasets/` matches it, and the fields where an over-match would be
# expensive are the ones that degrade rather than fail.
LITERAL_MONEY = re.compile(
    r"""
    \$\s*\d              # $3, $ 4.99
    | \d+\.\d{2}\b       # 3.49, 12.00
    | \b\d+\s*(?:dollars?|bucks|cents?)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def find_literal_money_in_plan(plan: MealPlan) -> list[str]:
    """
    Model-authored text inside a plan that carries a money-shaped string.

    `PlanDraft` has no price field, so the model cannot put a price in a
    STRUCTURED slot. It can still write one into free text: `DraftMeal.name`,
    `DraftIngredient.item` and `DraftIngredient.qty_display` pass through
    `assemble_plan` unchanged and reach the user.

    Those three were unchecked. A plan naming a meal "Budget Pasta - only
    $4.99 a head" with an ingredient "Butter (was 7.50, now 5.00)" passed
    assert_grounded, assert_arithmetic and assert_no_literal_money_in_response
    together, shipping two invented figures -- one of them a fabricated "was"
    price -- through a system whose central claim is that a price the user
    sees was retrieved. SYSTEM_PROMPT already forbids it ("NEVER state a
    price"), and nothing verified the instruction was obeyed. An instruction a
    model can ignore is exactly what this codebase replaces with a check
    everywhere else; this closes the last place it had not.

    Returns descriptions rather than raising. The caller is `validate_plan`,
    and the right response is a repair cycle: Req 3.7 says essential
    structured content fails rather than degrading, and in this graph
    "fails" means bounded repair and then an honest terminal, not an
    exception thrown at a user who asked for a meal plan.
    """
    violations: list[str] = []
    for meal in plan.meals:
        match = LITERAL_MONEY.search(meal.name)
        if match:
            violations.append(f"meal name {meal.name!r} states {match.group(0)!r}")
        for ing in meal.ingredients:
            for field, value in (("item", ing.item), ("qty", ing.qty)):
                match = LITERAL_MONEY.search(value)
                if match:
                    violations.append(f"ingredient {field} {value!r} states {match.group(0)!r}")
    return violations


def assert_no_model_authored_money(response: ChatResponse) -> None:
    """
    Response-boundary backstop over model-authored text in a meal plan.

    `run_turn` calls this. It can only fire on a bug: `validate_plan` rejects
    these fields, and a plan that never came back clean is discarded in favour
    of `emit_plan_generation_failed` rather than emitted. Reaching here means
    the repair loop or the router let one through, and shipping an invented
    price is worse than losing the turn.

    Deliberately NARROWER than `assert_no_literal_money_in_response`: it does
    not look at token text. Prose is model-authored too, but it is
    non-essential, and the prose node already drops the sentence and ships the
    table when it finds money -- raising here would convert that degradation
    into a dead turn, contradicting the rule in `tests/test_prose.py` that a
    table with no sentence beats a sentence with a wrong price. Req 3.7 draws
    exactly this line: non-essential text is discarded, essential structured
    content fails.
    """
    violations: list[str] = []
    for ev in response.events:
        if isinstance(ev, MealPlanEvent):
            violations.extend(find_literal_money_in_plan(ev.data))

    if violations:
        raise AssertionError(
            f"Model-authored money in plan ({len(violations)}):\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


def assert_no_literal_money_in_response(response: ChatResponse) -> None:
    """
    Reject any literal monetary value in user-visible prose-like fields.

    Checks: token text, comparison reasoning, notice messages, and the
    model-authored text inside a meal plan (meal names, ingredient labels and
    quantities). Prices must live only in citation events and structured
    fields that carry a citation_ref -- never in free text where provenance
    cannot be verified.

    The whole-response call belongs to `validate.py` and CI. `run_turn` calls
    the narrower `assert_no_model_authored_money` instead; see its docstring
    for why the two differ.

    DELIBERATELY NOT CHECKED, and the exclusion is the interesting part:

    * `ErrorEvent.message` restates the user's OWN budget -- "I couldn't build
      a plan within $15 using current prices". That figure is the constraint
      they supplied, not a price we are claiming, and dropping it makes the
      refusal harder to act on. The rule is about prices presented as prices,
      not about digits.
    * `NoDataEvent.message` and `.requested_item` echo the user's own search
      term. Same reasoning, plus a blanket check here would let a user fail
      their own turn by typing a dollar sign.

    Both exclusions are safe only while those messages stay code-authored. A
    model-written error or no-data message would need this rule extended to
    it, because the argument above is entirely about who wrote the text.
    """
    violations: list[str] = []

    for ev in response.events:
        if isinstance(ev, TokenEvent):
            match = LITERAL_MONEY.search(ev.text)
            if match:
                violations.append(
                    f"TokenEvent seq={ev.seq}: literal money {match.group(0)!r} in text"
                )
        elif isinstance(ev, PriceComparisonEvent):
            match = LITERAL_MONEY.search(ev.data.reasoning)
            if match:
                violations.append(
                    f"PriceComparison '{ev.data.query_item}': literal money "
                    f"{match.group(0)!r} in reasoning"
                )
        elif isinstance(ev, NoticeEvent):
            match = LITERAL_MONEY.search(ev.message)
            if match:
                violations.append(
                    f"NoticeEvent seq={ev.seq}: literal money {match.group(0)!r} in message"
                )
        elif isinstance(ev, MealPlanEvent):
            violations.extend(
                f"MealPlan seq={ev.seq}: {v}" for v in find_literal_money_in_plan(ev.data)
            )

    if violations:
        raise AssertionError(
            f"Literal money in prose ({len(violations)}):\n"
            + "\n".join(f"  - {v}" for v in violations)
        )
