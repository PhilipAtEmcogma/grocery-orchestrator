"""
Pilot Task 4 — ask for a missing planning constraint instead of guessing it.
"""

from __future__ import annotations

import pytest

from src.models.scripted import ScriptedModelClient
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import (
    ChatRequest,
    ChatResponse,
    ClarificationEvent,
    ClientHints,
    ErrorCode,
)

# ============================================ Pilot Task 4: asking, not guessing
#
# `classify_intent` used to write `constraints["household_size"] = household if
# household is not None else 1` and the same for `days`. That contradicts Req
# 6.3 — reject inference of unstated constraints — and, worse, destroyed the
# only evidence that the user had not said. A plan for one person over one day
# is a real answer to a question nobody asked, and downstream it is
# indistinguishable from a plan the shopper actually requested.
#
# The scenarios in datasets/DATA_SCHEMA.md made the gap concrete: Scenario 5,
# "Plan a quick dinner for 2 people that is completely dairy-free", states no
# budget and returned PLAN_GENERATION_FAILED — "I couldn't put together a plan
# I trust this time" — for a request that was simply under-specified.


def _turn(message: str, **hints) -> ChatResponse:
    request = ChatRequest(
        version="1.0",
        session_id="sess-clar001",
        turn_id="turn-clar001",
        message=message,
        hints=ClientHints(**hints) if hints else None,
    )
    return run_turn(request, InMemoryPriceRepository(), ScriptedModelClient())


def _clarification(response: ChatResponse) -> ClarificationEvent | None:
    return next((e for e in response.events if isinstance(e, ClarificationEvent)), None)


@pytest.mark.parametrize(
    ("message", "hints", "expected"),
    [
        ("plan me some dinners", {}, ["household_size", "days", "budget_nzd"]),
        ("dinner for 2 people tonight", {}, ["budget_nzd"]),
        ("dinner for 2 people on $40", {}, ["days"]),
        ("dinners for 5 days on $90", {}, ["household_size"]),
    ],
    ids=["nothing-given", "no-budget", "no-duration", "no-household"],
)
def test_a_meal_plan_asks_for_exactly_what_is_missing(message, hints, expected):
    event = _clarification(_turn(message, **hints))
    assert event is not None, "an under-specified plan must ask rather than guess"
    assert [m.value for m in event.missing] == expected


def test_the_dataset_scenario_that_used_to_fail_now_asks():
    """
    datasets/DATA_SCHEMA.md Scenario 5. Before this it produced
    PLAN_GENERATION_FAILED, which told the shopper the failure was ours when
    the request was merely incomplete.
    """
    response = _turn("Plan a quick dinner for 2 people that is completely dairy-free.")

    assert not any(e.type == "error" for e in response.events), (
        "an under-specified request is not a failure"
    )
    event = _clarification(response)
    assert event is not None
    assert [m.value for m in event.missing] == ["days", "budget_nzd"]


def test_a_complete_request_is_never_interrupted():
    """The half that stops this becoming an assistant that only asks questions."""
    response = _turn("feed a flat of 3 for 7 days on $120")

    assert _clarification(response) is None
    assert any(e.type == "meal_plan" for e in response.events)


@pytest.mark.parametrize(
    "message",
    [
        "We are 3 university flatmates. Can you recommend a dinner for under $15 total tonight?",
        "A high-protein seafood dinner for 2 people under $30 tonight",
    ],
    ids=["scenario-2", "scenario-4"],
)
def test_the_dataset_scenarios_still_plan_in_one_turn(message):
    """
    Clarifying a fact the user plainly stated is worse than defaulting it.

    "3 university flatmates" and "tonight" ARE statements of household size and
    duration; an adjective between the number and the noun, or the word
    "tonight" rather than "1 day", must not turn a complete request into an
    interrogation.
    """
    response = _turn(message)

    assert _clarification(response) is None, "the user already said this"
    assert any(e.type == "meal_plan" for e in response.events)


def test_a_price_check_is_unaffected():
    """None of these constraints mean anything for "cheapest butter"."""
    response = _turn("cheapest butter")

    assert _clarification(response) is None
    assert any(e.type == "price_comparison" for e in response.events)


def test_an_unsupported_exclusion_still_wins_over_a_missing_constraint():
    """
    Ordering, and it is a safety decision rather than a stylistic one.

    A dietary term we cannot honour is the more important thing to report, and
    asking for a budget first would bury it under a question the shopper would
    answer before ever learning we cannot meet their restriction.
    """
    response = _turn("plan gluten-free dinners")

    assert _clarification(response) is None
    codes = [e.code for e in response.events if e.type == "error"]
    assert codes == [ErrorCode.UNSUPPORTED_EXCLUSION]


def test_hints_satisfy_a_constraint_the_message_omits():
    """A slider the user set is a statement, the same as typing it."""
    response = _turn("plan some dinners", household_size=2, days=3, budget_nzd=60)

    assert _clarification(response) is None
    assert any(e.type == "meal_plan" for e in response.events)


def test_the_missing_list_names_ClientHints_fields():
    """
    So a frontend can raise the control that collects the value rather than
    parsing English out of the message. That is the difference between a
    chatbot and a product.
    """
    event = _clarification(_turn("plan me some dinners"))
    assert event is not None
    assert {m.value for m in event.missing} <= set(ClientHints.model_fields)


def test_clarification_is_not_an_error_and_carries_no_plan():
    """
    Nothing failed. The request was valid and we understood it; we are one fact
    short of answering. Emitting an ErrorEvent would make `retryable` the only
    signal, and a client reading retryable=true resends the identical request
    and loops.
    """
    response = _turn("plan me some dinners")

    assert not any(e.type == "error" for e in response.events)
    assert not any(e.type == "meal_plan" for e in response.events)
    assert any(e.type == "done" for e in response.events)


def test_no_model_call_is_spent_on_an_under_specified_plan():
    """
    Emitted before retrieval, like the dietary refusal: there is no point
    pricing a basket for a plan we have already decided we cannot build.
    """
    model = ScriptedModelClient()
    request = ChatRequest(
        version="1.0",
        session_id="sess-clar001",
        turn_id="turn-clar002",
        message="plan me some dinners",
    )
    run_turn(request, InMemoryPriceRepository(), model)

    plan_calls = [t for t, schema in model.calls if schema == "PlanDraft"]
    assert plan_calls == [], "an under-specified request must not reach the planner"
