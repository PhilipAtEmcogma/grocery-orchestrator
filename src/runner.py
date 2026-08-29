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
    assert_citations_match_retrieval,
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

    # Req 3.5, and the reason this function takes `repo` as well as the graph:
    # it is the only place holding BOTH the finished response and the state the
    # graph built it from. `assert_grounded` above can only see the response, so
    # it checks that source keys are SHAPED like keys; this checks that they
    # ARE the keys of a record retrieval actually returned, and that every
    # published value equals the retrieved one.
    #
    # `record_index` is written only by `retrieve_prices` and holds frozen
    # `PriceRecord`s, so the comparison is against something that cannot have
    # been edited on the way here.
    #
    # `.get(...) or {}` is not a soft landing: an empty index with citations
    # present fails every citation, which is the correct direction. A turn that
    # emitted no citations has nothing to prove and passes trivially.
    assert_citations_match_retrieval(
        response,
        table=repo.table_name,
        records=final.get("record_index") or {},
    )
    return response
