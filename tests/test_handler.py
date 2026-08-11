"""
Handler tests.

The invariant under test: whatever goes in, a contract-valid ChatResponse
comes out. The frontend should never need a special case for "the backend
returned something unparseable".
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.handler import handle_turn, lambda_handler
from src.schemas.contract import ChatRequest, ChatResponse, ErrorCode


def _event(body: str | dict, method: str = "POST") -> dict:
    if isinstance(body, dict):
        body = json.dumps(body)
    return {"httpMethod": method, "body": body}


@pytest.fixture(autouse=True)
def _fresh_idempotency_store(monkeypatch):
    """
    Reset the cached store between tests.

    It is module-level so it survives warm Lambda invocations, which is the
    point in production and cross-test contamination here.
    """
    import src.handler as handler_mod

    monkeypatch.setattr(handler_mod, "_idempotency", None)


def _valid_body(message: str = "cheapest butter", **extra) -> dict:
    # Unique ids per call. Reusing a turn_id with different content is now a
    # rejected request, which is the behaviour under test in
    # test_reused_turn_id_with_different_content_is_rejected.
    unique = uuid.uuid4().hex[:8]
    return {
        "version": "1.0",
        "session_id": f"sess-{unique}",
        "turn_id": f"turn-{unique}",
        "message": message,
        **extra,
    }


def _parse(result: dict) -> ChatResponse:
    """Parsing with the contract model IS the assertion."""
    return ChatResponse.model_validate_json(result["body"])


# ------------------------------------------------------------- happy path


def test_valid_request_returns_200_and_parses():
    result = lambda_handler(_event(_valid_body()))
    assert result["statusCode"] == 200
    response = _parse(result)
    assert response.events[0].type == "session"
    assert response.events[-1].type == "done"


def test_ids_are_echoed_back():
    body = _valid_body()
    response = _parse(lambda_handler(_event(body)))
    assert response.session_id == body["session_id"]
    assert response.turn_id == body["turn_id"]


def test_meal_plan_request_works_end_to_end():
    body = _valid_body(
        "feed a flat of 3 for under $30 this week, no seafood",
        hints={"household_size": 3, "budget_nzd": 30, "days": 3,
               "dietary_exclusions": ["seafood"]},
    )
    response = _parse(lambda_handler(_event(body)))
    assert "meal_plan" in [e.type for e in response.events]


# ------------------------------------------------------------- bad input


@pytest.mark.parametrize(
    "body",
    [
        '{"nonsense": true}',           # valid JSON, wrong shape
        "not json at all",              # not JSON
        "",                             # empty
        "{}",                           # empty object
        '{"version":"1.0","session_id":"x","turn_id":"y","message":""}',  # empty msg
    ],
)
def test_bad_input_still_returns_a_parseable_contract_response(body):
    result = lambda_handler(_event(body))
    response = _parse(result)          # would raise if unparseable
    assert response.events[-1].type == "done"


def test_malformed_request_is_400_with_invalid_request_code():
    response = _parse(lambda_handler(_event('{"nonsense": true}')))
    errors = [e for e in response.events if e.type == "error"]
    assert errors[0].code == ErrorCode.INVALID_REQUEST
    assert errors[0].retryable is False


def test_oversized_message_is_rejected_not_truncated():
    """Silently truncating input would change what the user asked."""
    response = _parse(lambda_handler(_event(_valid_body("x" * 5000))))
    errors = [e for e in response.events if e.type == "error"]
    assert errors[0].code == ErrorCode.INVALID_REQUEST


# ------------------------------------------- raising from inside an except
#
# A `try` protects the code in the `try`. Code in an `except` block, or above
# the `try`, is not covered by the sibling clauses — a raise there escapes the
# handler and becomes the bare 500 this module exists to prevent. Every case
# below crashed the handler before these tests existed, and they are grouped
# because they are one mistake, not four.


@pytest.mark.parametrize(
    "body",
    [
        '{"session_id": "x",',                              # truncated mid-object
        '{"session_id": "sess-abcd1234", "message": "hi",}',  # trailing comma
        '{"session_id": "sess-abcd1234"',                   # unclosed object
        "{",                                                # just a brace
        "[1, 2, 3]",                                        # valid JSON, not an object
        "null",                                             # valid JSON, not an object
        '"just a string"',                                  # valid JSON, not an object
        "123",                                              # valid JSON, not an object
    ],
)
def test_unparseable_body_still_returns_a_contract_response(body):
    """
    The regression test for the reported bug.

    A body starting with `{` used to be re-parsed with a bare `json.loads()`
    inside the `except ValidationError` block, to recover the session id for
    the error response. For a TRUNCATED object that parse raises
    JSONDecodeError — and because it raises from inside an except block, the
    `except (json.JSONDecodeError, ...)` clause two lines below cannot catch
    it. It escaped as an unhandled exception.

    The non-object cases are the same recovery step failing differently: JSON
    that parses fine but yields a list, a null or a string, none of which have
    `.get`.
    """
    result = lambda_handler(_event(body))

    response = _parse(result)  # would raise if the body were not a ChatResponse
    assert result["statusCode"] == 400
    assert response.events[-1].type == "done"

    errors = [e for e in response.events if e.type == "error"]
    assert errors[0].code == ErrorCode.INVALID_REQUEST
    assert errors[0].retryable is False


def test_ids_are_still_recovered_from_a_body_that_failed_validation():
    """
    The recovery step has to keep working, not just stop crashing. A client
    that sent a request it can identify should get an error it can match to
    that request.
    """
    body = {"session_id": "sess-abcd1234", "turn_id": "turn-abcd1234"}  # no message
    response = _parse(lambda_handler(_event(body)))

    assert response.session_id == "sess-abcd1234"
    assert response.turn_id == "turn-abcd1234"


def test_ids_fall_back_to_placeholders_when_they_cannot_be_recovered():
    response = _parse(lambda_handler(_event('{"session_id": "x",')))

    assert response.session_id == "unknown-session"
    assert response.turn_id == "unknown-turn"


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ("!!!not-base64!!!", "binascii.Error"),
        ("/w==", "decodes to bytes that are not UTF-8"),
    ],
)
def test_undecodable_base64_body_still_returns_a_contract_response(body, why):
    """
    The decode used to sit ABOVE the try, so neither of these could reach the
    `except (..., UnicodeDecodeError)` clause that was written for exactly the
    second one. API Gateway sets isBase64Encoded on binary content types, so
    this is reachable by any client that sets the wrong Content-Type.
    """
    result = lambda_handler(
        {"httpMethod": "POST", "body": body, "isBase64Encoded": True}
    )

    assert result["statusCode"] == 400, why
    assert _parse(result).events[-1].type == "done"


def test_idempotency_store_failure_after_the_turn_does_not_fail_the_turn(monkeypatch):
    """
    `acquire()` was guarded with the comment "an idempotency store failure
    must not fail the turn"; `complete()` and `release()`, two lines below,
    were not. The work is already done and the response is already correct at
    that point, so a failed bookkeeping write must not discard it.
    """
    import src.handler as handler_mod
    from src.store.idempotency import AcquireStatus

    class Acquired:
        status = AcquireStatus.ACQUIRED
        cached_response = None

    class FailsToRecord:
        def acquire(self, *_args, **_kwargs):
            return Acquired()

        def complete(self, *_args, **_kwargs):
            raise RuntimeError("DynamoDB is unavailable")

        def release(self, *_args, **_kwargs):
            raise RuntimeError("DynamoDB is unavailable")

    monkeypatch.setattr(handler_mod, "_idempotency", FailsToRecord())

    result = lambda_handler(_event(_valid_body()))
    response = _parse(result)

    assert result["statusCode"] == 200
    # The real answer, not an error — the turn itself never failed.
    assert [e for e in response.events if e.type == "price_comparison"]
    assert response.events[-1].type == "done"


def test_an_exception_escaping_the_handler_still_produces_a_contract_response(
    monkeypatch,
):
    """
    The last resort. Enumerating raising call sites is the review that has
    already failed once, so the invariant is structural: whatever escapes
    `_observed_handler` — including a future one of these — becomes the same
    retryable INTERNAL_ERROR body as any other server-side failure.

    The status is 500 and not the 200 a HANDLED internal error returns. This
    path only runs when the enumeration was wrong, and at 200 that is
    invisible to anything watching HTTP — the operator would have to already
    be reading logs to learn that the net fired.
    """
    import src.handler as handler_mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("something nobody predicted")

    monkeypatch.setattr(handler_mod, "_observed_handler", boom)

    result = lambda_handler(_event(_valid_body()))
    response = _parse(result)

    assert result["statusCode"] == 500
    assert response.events[-1].type == "done"

    errors = [e for e in response.events if e.type == "error"]
    assert errors[0].code == ErrorCode.INTERNAL_ERROR
    assert errors[0].retryable is True
    # The client is told nothing about the crash beyond "try again".
    assert "nobody predicted" not in result["body"]


def test_a_handled_internal_error_stays_200(monkeypatch):
    """
    The other half of the distinction, and the reason it is worth having: a
    failure the handler predicted and mapped is a 200, so a 500 means
    specifically "a bug got past the handlers". If this ever drifts to 500 the
    signal is gone — every internal error would look like an escaped one.
    """
    import src.handler as handler_mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("a failure we do handle")

    monkeypatch.setattr(handler_mod, "_dependencies", boom)

    result = lambda_handler(_event(_valid_body()))
    response = _parse(result)

    assert result["statusCode"] == 200
    errors = [e for e in response.events if e.type == "error"]
    assert errors[0].code == ErrorCode.INTERNAL_ERROR


# Powertools warns that no metrics were published, which is correct and
# expected: the invocation crashed before the first counter. Scoped to this
# test rather than filtered globally, so a real empty-metrics regression
# elsewhere still shows up.
@pytest.mark.filterwarnings("ignore:No application metrics to publish")
@pytest.mark.parametrize("event", [None, [], "not an event", 42])
def test_a_non_dict_event_does_not_escape_the_last_resort(event):
    """
    The net itself reads `event["body"]` to correlate its response, and it
    runs precisely when an assumption has already broken. If that read can
    raise, the net has the same defect as the code it is catching.
    """
    result = lambda_handler(event)

    assert result["statusCode"] == 500
    assert _parse(result).events[-1].type == "done"


@pytest.mark.parametrize(
    "raw",
    [
        '{"session_id": "x",',
        "[1, 2, 3]",
        "null",
        "",
        "\udcff",  # a lone surrogate: encodable as a str, not as UTF-8
        "{" * 2000,  # deep enough to blow the parser's recursion limit
    ],
)
def test_id_recovery_never_raises(raw):
    """
    `_best_effort_ids` is called from inside an except block, so "must not
    raise" is its entire contract — a unit-level guard on the property the
    tests above exercise end to end.
    """
    from src.handler import _best_effort_ids

    session_id, turn_id = _best_effort_ids(raw)

    assert isinstance(session_id, str) and session_id
    assert isinstance(turn_id, str) and turn_id


# ------------------------------------------------------------- failure modes


def test_unhandled_exception_becomes_a_clean_error(monkeypatch):
    """A crash must not leak a stack trace or a bare 500."""
    import src.handler as handler_mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated catastrophe")

    monkeypatch.setattr(handler_mod, "_dependencies", boom)

    request = ChatRequest.model_validate(_valid_body())
    status, response = handle_turn(request)

    assert status == 200
    errors = [e for e in response.events if e.type == "error"]
    assert errors[0].code == ErrorCode.INTERNAL_ERROR
    assert errors[0].retryable is True


def test_error_message_does_not_leak_internals(monkeypatch):
    import src.handler as handler_mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("connection string postgres://admin:hunter2@db")

    monkeypatch.setattr(handler_mod, "_dependencies", boom)

    _, response = handle_turn(ChatRequest.model_validate(_valid_body()))
    errors = [e for e in response.events if e.type == "error"]
    assert "hunter2" not in errors[0].message
    assert "postgres" not in errors[0].message


def test_grounding_violation_fails_closed(monkeypatch):
    """
    If assert_grounded fires, the response must be refused rather than sent.
    This is the last line of the grounding defence.
    """
    import src.handler as handler_mod

    def ungrounded(*_args, **_kwargs):
        raise AssertionError("Ungrounded citation refs: ['c99']")

    monkeypatch.setattr(handler_mod, "run_turn", ungrounded, raising=False)
    monkeypatch.setattr("src.runner.run_turn", ungrounded)

    _, response = handle_turn(ChatRequest.model_validate(_valid_body()))
    types = [e.type for e in response.events]
    assert "price_comparison" not in types
    assert "error" in types


# ------------------------------------------------------------- http plumbing


def test_options_preflight_returns_cors_headers():
    result = lambda_handler({"httpMethod": "OPTIONS"})
    assert result["statusCode"] == 204
    assert "Access-Control-Allow-Origin" in result["headers"]


def test_response_is_json_content_type():
    result = lambda_handler(_event(_valid_body()))
    assert result["headers"]["Content-Type"] == "application/json"


def test_base64_encoded_body_is_decoded():
    import base64

    body = json.dumps(_valid_body())
    event = {
        "httpMethod": "POST",
        "body": base64.b64encode(body.encode()).decode(),
        "isBase64Encoded": True,
    }
    assert lambda_handler(event)["statusCode"] == 200
