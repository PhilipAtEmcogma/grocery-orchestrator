"""
A degraded classification must not be reported as an under-specified request.

FOUND BY MEASUREMENT, NOT BY REVIEW (Pilot Task 16, gate G6 Phase B,
2026-09-04). Under a deliberate 21x breach of the Nova Lite quota, 24 turns
were sent carrying ONE unambiguous message -- "feed 3 people for 5 days on
$80". Fourteen came back as CLARIFICATION REQUESTS at intent confidence 0.45,
which is the keyword fallback: the model call had been throttled.

The mechanism is not confidence routing. `missing_plan_constraints` reads
ABSENCE, and `_fallback` extracts no household size, no duration and no budget
at all -- so when the model call fails, every constraint reads as missing
whatever the shopper actually wrote, and `route_after_intent` sends a perfectly
complete request to `emit_clarification`.

WHY THAT IS WORSE THAN A WRONG ERROR CODE. The shopper is told the fault is
theirs, and the remedy offered cannot work: rephrasing does not fix a throttle.
Retrying does, and only the upstream-failure path says so. It is the same false
statement `emit_upstream_failure` already exists to prevent one node further on
-- "I couldn't build a plan within $30 using current prices" when Bedrock timed
out is a false claim about the budget -- applied to the message instead.

The eight turns whose throttle landed on a LATER call returned the retryable
error correctly, which is why this went unnoticed: the behaviour was right
whenever anything had already succeeded.
"""

from __future__ import annotations

from typing import cast

import pytest

from src.graph.nodes import route_after_intent
from src.graph.state import GroceryState
from src.models.base import ModelError
from src.models.scripted import ScriptedModelClient
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import (
    ChatRequest,
    ChatResponse,
    ClarificationEvent,
    ErrorCode,
    ErrorEvent,
    Intent,
)

COMPLETE_REQUEST = "feed 3 people for 5 days on $80"


class _FailingModel(ScriptedModelClient):
    """
    A model whose every call raises, the way a throttled Bedrock client does.

    Subclasses the scripted client rather than reimplementing the interface, so
    this stays a test about ROUTING and does not quietly become a second
    implementation of the model contract that can drift from the real one.
    """

    def structured(self, *args, **kwargs):
        raise ModelError("throttled: too many requests")

    def text(self, *args, **kwargs):
        raise ModelError("throttled: too many requests")


def _turn(model) -> ChatResponse:
    request = ChatRequest(
        version="1.0",
        session_id="sess-degraded",
        turn_id="turn-degraded",
        message=COMPLETE_REQUEST,
    )
    return run_turn(request, InMemoryPriceRepository(), model)


def _of[E](response: ChatResponse, kind: type[E]) -> E | None:
    return next((e for e in response.events if isinstance(e, kind)), None)


# ------------------------------------------------------------------ routing


def test_a_degraded_meal_plan_routes_to_upstream_failure_not_clarification():
    """
    The unit-level statement of the defect.

    `cast`, as in tests/test_plan.py and tests/test_recipe_selection.py:
    GroceryState is a TypedDict the graph fills in progressively, and a routing
    predicate reads a handful of its keys. Spelling out the rest would describe
    a state this function never sees.
    """
    state = {"intent": Intent.MEAL_PLAN, "constraints": {}, "intent_degraded": True}
    assert route_after_intent(cast(GroceryState, state)) == "upstream_failure"


def test_an_undegraded_meal_plan_still_asks_for_what_is_missing():
    """
    The fix must not swallow the honest case. A genuinely under-specified
    request -- one where the model DID read the message and the constraints
    really are absent -- must still ask, which is what Pilot Task 4 built.
    """
    state = {"intent": Intent.MEAL_PLAN, "constraints": {}, "intent_degraded": False}
    assert route_after_intent(cast(GroceryState, state)) == "clarify"


def test_a_degraded_classification_with_constraints_present_still_retrieves():
    """
    Degradation alone does not divert a turn. If the fallback still produced
    the constraints -- or the hints supplied them -- there is a real request to
    serve, and answering it is better than refusing on principle.
    """
    state = {
        "intent": Intent.MEAL_PLAN,
        "constraints": {"household_size": 3, "days": 5, "budget_nzd": 80},
        "intent_degraded": True,
    }
    assert route_after_intent(cast(GroceryState, state)) == "retrieve"


def test_a_dietary_refusal_still_outranks_a_degraded_classification():
    """
    Ordering is load-bearing. An exclusion we cannot honour is a safety matter
    and stays the reported reason; it was checked before clarification for that
    reason, and must stay ahead of the new branch too.
    """
    state = {
        "intent": Intent.MEAL_PLAN,
        "constraints": {},
        "intent_degraded": True,
        "unsupported_exclusions": ["macadamia"],
    }
    assert route_after_intent(cast(GroceryState, state)) == "dietary_unsupported"


# ------------------------------------------------------------- end to end


def test_a_throttled_turn_answers_retryably_rather_than_asking_to_rephrase():
    """
    The measured defect, end to end, through the real graph.

    Before the fix this produced a ClarificationEvent listing household_size,
    days and budget_nzd -- for a message that states all three.
    """
    response = _turn(_FailingModel())

    assert _of(response, ClarificationEvent) is None, (
        "a throttled turn asked the shopper to rephrase a complete request"
    )
    error = _of(response, ErrorEvent)
    assert error is not None, "a throttled turn must say something failed"
    assert error.retryable is True, "retrying is the move that works; the shopper must be told"
    assert error.code in (ErrorCode.INTERNAL_ERROR, ErrorCode.UPSTREAM_TIMEOUT)


def test_the_working_model_still_plans_the_same_request():
    """
    The control. The identical message through a working model must NOT reach
    the upstream path -- otherwise this test file would pass against a service
    that had simply stopped planning.
    """
    response = _turn(ScriptedModelClient())
    assert _of(response, ClarificationEvent) is None
    error = _of(response, ErrorEvent)
    assert error is None or error.code not in (
        ErrorCode.INTERNAL_ERROR,
        ErrorCode.UPSTREAM_TIMEOUT,
    ), "the control turn failed upstream; this suite would prove nothing"


@pytest.mark.parametrize("degraded", [True, False])
def test_price_checks_are_untouched_by_the_branch(degraded: bool) -> None:
    """
    The branch is scoped to meal plans. A price check carries no planning
    constraints to be missing, so degradation must not divert it -- it should
    still reach retrieval and answer from the catalogue.
    """
    state = {
        "intent": Intent.PRICE_CHECK,
        "constraints": {},
        "intent_degraded": degraded,
    }
    assert route_after_intent(cast(GroceryState, state)) == "retrieve"
