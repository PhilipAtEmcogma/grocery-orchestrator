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


def append_events(left: list[Event], right: list[Event]) -> list[Event]:
    """Reducer: nodes return only their NEW events, LangGraph concatenates."""
    return [*left, *right]


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

    # ---- validate / repair loop
    repair_attempts: int
    validation_errors: list[str]

    # ---- output
    events: Annotated[list[Event], append_events]
    usage: UsageMeta

    # ---- control
    terminated: bool
