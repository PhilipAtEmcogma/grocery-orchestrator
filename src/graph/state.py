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
    query_item: str  # the product the user asked about, for price_check turns


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

    # ---- input: raw client-supplied context, unvalidated beyond the contract
    hints: dict              # ClientHints dict, dumped from the request
    location: dict | None    # Location dict, dumped from the request

    # ---- classification: set by classify_intent
    intent: Intent
    intent_confidence: float
    intent_degraded: bool    # True if the model call failed and keyword fallback was used
    constraints: Constraints # extracted + hint-reconciled household size, budget, etc.

    # ---- retrieval (the ONLY source of prices): set by retrieve_prices
    records: list[PriceRecord]              # raw price records returned by the repository
    citations: list[Citation]               # wire-format citations built from those records
    citation_index: dict[str, Citation]     # citations keyed by ref, for O(1) lookup
    record_index: dict[str, PriceRecord]    # records keyed by the same refs
    resolved_product_key: str | None        # canonical product key matched from the query

    # ---- generation: set by generate_comparison / generate_plan
    comparison: PriceComparison | None
    plan: MealPlan | None
    prose: str

    # ---- validate / repair loop: set by validate_plan / repair_plan
    repair_attempts: int
    validation_errors: list[str]

    # ---- output: accumulated across every node, assembled by finalise
    events: Annotated[list[Event], append_events]
    usage: UsageMeta

    # ---- control
    terminated: bool  # set once a terminal node (no_data/error/finalise path) has run
