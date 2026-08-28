"""
GroceryState — the object every node reads from and writes to.

Design notes
------------
* Events accumulate. Nodes append; the finalise node assembles the response.
  `seq` is assigned at append time so ordering is a structural property.

* `citations` and `citation_index` are populated ONLY by the retrieval node.
  No other node may add a citation. That is what makes the grounding
  invariant enforceable: if generation invents a price, there is no citation
  to reference it and assert_grounded() fails.

* `repair_attempts` is bounded by MAX_REPAIR_ATTEMPTS. An unbounded repair
  loop is a runaway cost and latency risk, not just a correctness one.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, TypedDict

from src.models.base import ModelClient
from src.retrieval.base import PriceRecord
from src.schemas.contract import (
    Citation,
    Event,
    Intent,
    MealPlan,
    PriceComparison,
    Store,
    UsageMeta,
)

MAX_REPAIR_ATTEMPTS = 2


def usage_from(model: ModelClient, previous: dict | None = None) -> UsageMeta:
    """
    Lift a client's most recent call into the contract's usage shape.

    `last_usage` reports one call, so every node that calls the model must
    return this and let `merge_usage` combine them. A node that calls the model
    and does not return usage drops those tokens from the turn silently -- the
    response still validates, it just under-reports, which is the failure mode
    that left `model_ids` empty on every deployed response until this existed.

    PASS `previous`. `BedrockModelClient` assigns `self._usage` only after
    `converse` returns, so a call that raises `ModelError` leaves the PREVIOUS
    call's numbers in place. Reporting them again double-counts on exactly the
    turns that failed: a meal plan whose generation throttles through two
    repairs bills `classify_intent`'s tokens four times. Handing in the value
    read before the call lets an unchanged reading be recognised as "this call
    recorded nothing" and dropped.

    A guardrail block is NOT that case and keeps its numbers -- `converse`
    returned and wrote fresh usage before the stop reason was inspected, which
    is the call you most want the tokens for. `InstrumentedModelClient._call`
    guards its telemetry the same way, for the same reason.
    """
    raw = model.last_usage or {}
    if previous is not None and raw == previous:
        return UsageMeta()
    return UsageMeta(
        model_ids=list(raw.get("model_ids") or []),
        input_tokens=raw.get("input_tokens"),
        output_tokens=raw.get("output_tokens"),
        latency_ms=raw.get("latency_ms"),
        guardrail_intervened=bool(raw.get("guardrail_intervened")),
    )


def append_events(left: list[Event], right: list[Event]) -> list[Event]:
    """Reducer: nodes return only their NEW events, LangGraph concatenates."""
    return [*left, *right]


def merge_usage(left: UsageMeta | None, right: UsageMeta | None) -> UsageMeta:
    """
    Reducer: a turn makes several model calls -- classify_intent, generate_plan,
    up to two repairs, generate_prose -- and the contract reports one usage
    block for the turn. Without a reducer the last writer wins, so a plan turn
    would report only the prose call's tokens and the field would understate
    cost by most of the turn.

    Tokens and latency sum. `latency_ms` is therefore time spent in the model,
    not wall-clock for the turn; those differ and the summed figure is the one
    that maps to spend. Model ids accumulate in call order, deduplicated, so a
    turn routed entirely to one model reports it once. `guardrail_intervened`
    is sticky: a turn in which the guardrail fired once is a turn in which it
    fired, regardless of what a later call reports.
    """
    if left is None:
        return right or UsageMeta()
    if right is None:
        return left

    def _sum(a: int | None, b: int | None) -> int | None:
        # None means "not reported", which is not the same as zero -- a model
        # that returns no token counts must not read as a free call.
        return None if a is None and b is None else (a or 0) + (b or 0)

    return UsageMeta(
        model_ids=list(dict.fromkeys([*left.model_ids, *right.model_ids])),
        input_tokens=_sum(left.input_tokens, right.input_tokens),
        output_tokens=_sum(left.output_tokens, right.output_tokens),
        latency_ms=_sum(left.latency_ms, right.latency_ms),
        guardrail_intervened=(left.guardrail_intervened or right.guardrail_intervened),
    )


class Constraints(TypedDict, total=False):
    """Extracted from the user message, optionally seeded by client hints."""

    household_size: int
    budget_nzd: Decimal
    days: int
    dietary_exclusions: list[str]
    preferred_stores: list[Store]
    query_items: list[str]


class TurnInput(TypedDict):
    """
    Keys guaranteed present at graph entry. total=True (the default) so the
    type checker permits state["session_id"] without a .get() dance, and so
    omitting one is a type error at the call site rather than a KeyError at
    runtime.
    """

    session_id: str
    turn_id: str
    message: str


class GroceryState(TurnInput, total=False):
    """Everything below is populated by nodes as the graph executes."""

    # ---- input
    hints: dict
    location: dict | None

    # ---- classification
    intent: Intent
    intent_confidence: float
    intent_degraded: bool
    constraints: Constraints
    # Dietary terms the user stated that we cannot safely honour against the
    # current catalogue — see src/graph/dietary.py. Populated at
    # classify_intent time, so a meal_plan turn can refuse *before* doing
    # retrieval and generation work. An empty list is the normal case.
    unsupported_exclusions: list[str]

    # ---- retrieval (the ONLY source of prices)
    records: list[PriceRecord]
    citations: list[Citation]
    citation_index: dict[str, Citation]
    record_index: dict[str, PriceRecord]
    # product_key -> citation refs, one entry per resolved item
    item_groups: dict[str, list[str]]
    # items the user asked about that we have no data for
    unresolved_items: list[str]
    # items the user asked about that we never looked up, because the request
    # exceeded MAX_ITEMS_PER_TURN. Distinct from unresolved_items: we may well
    # have prices for these, we just did not check. Saying "no data" about them
    # would be a different lie from saying nothing.
    skipped_items: list[str]

    # ---- generation
    comparisons: list[PriceComparison]
    plan: MealPlan | None
    prose: str
    prose_error: str

    # Set by retrieval when the budget cannot cover this household for this
    # many days at the cheapest price per gram in the catalogue. Distinct from
    # over_budget, which is about a plan that was actually costed: this one
    # says no plan could exist, and is known before generation.
    budget_impossible: bool

    # ---- validate / repair loop
    repair_attempts: int
    validation_errors: list[str]
    # Set by validate_plan when a plan was produced and costs more than the
    # budget. This is the ONLY condition that makes "your budget does not
    # stretch" a true statement. Every other validation error -- a draft that
    # failed its schema, a hallucinated citation ref, no products, broken
    # arithmetic -- means we could not produce a valid plan, which is a
    # different fact and is not the user's budget's fault. Kept as a flag
    # rather than inferred from the error strings so that adding an error
    # message cannot silently reclassify a failure.
    over_budget: bool
    # An upstream failure — Bedrock unreachable, timed out, throttled, or
    # misconfigured. Deliberately NOT a validation error: a validation error
    # means "the model produced a plan and the plan is wrong", which the
    # repair loop can act on. This means "there is no model output at all",
    # which repair cannot fix and which must not be reported to the user as
    # a budget problem. See emit_upstream_failure.
    upstream_error: str

    # ---- output
    events: Annotated[list[Event], append_events]
    usage: Annotated[UsageMeta, merge_usage]

    # ---- control
    terminated: bool
