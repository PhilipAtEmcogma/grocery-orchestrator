"""
Request -> graph -> validated response.

This is what handler.py (the Lambda entrypoint) will call. Keeping it separate
from the handler means it is testable without any Lambda event shape.
"""

from __future__ import annotations

from src.graph.build import build_graph
from src.graph.state import GroceryState
from src.models.base import ModelClient
from src.retrieval.base import PriceRepository
from src.schemas.contract import (
    ChatRequest,
    ChatResponse,
    assert_grounded,
    assert_no_model_authored_money,
)


def run_turn(request: ChatRequest, repo: PriceRepository, model: ModelClient) -> ChatResponse:
    # Build a fresh graph per call, wired to this call's repo/model.
    graph = build_graph(repo, model)

    # Annotated so the type checker can see this satisfies GroceryState.
    # GroceryState is total=False, so a partial dict is valid: the remaining
    # keys are populated by nodes as the graph executes.
    initial: GroceryState = {
        "session_id": request.session_id,
        "turn_id": request.turn_id,
        "message": request.message,
        "hints": request.hints.model_dump(mode="json") if request.hints else {},
        "location": (request.location.model_dump(mode="json") if request.location else None),
        "events": [],
    }

    # Run the graph to completion; `final` is the fully-populated state dict.
    final = graph.invoke(initial)

    response = ChatResponse(
        session_id=request.session_id,
        turn_id=request.turn_id,
        events=final["events"],
    )

    # Fail loudly rather than shipping an ungrounded response. In production
    # this becomes a caught exception that emits INTERNAL_ERROR, but during
    # development a crash is the correct, visible behaviour.
    assert_grounded(response)

    # Backstop over the model-authored free text in a plan. This can only fire
    # on a bug: validate_plan rejects these fields and the router discards a
    # plan that never came back clean, so reaching here means something let one
    # through -- and shipping an invented price is worse than losing the turn.
    #
    # NARROWER than assert_no_literal_money_in_response, deliberately. That one
    # also covers prose, which is non-essential and already degrades at the
    # prose node; raising on it here would turn "you lose the sentence" into
    # "you lose the turn". Req 3.7 draws the line exactly there, and
    # `validate.py` runs the whole-response version over `samples/` in CI.
    assert_no_model_authored_money(response)
    return response
