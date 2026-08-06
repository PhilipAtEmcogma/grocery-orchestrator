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
