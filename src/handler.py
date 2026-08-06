"""
Lambda entrypoint (API Gateway proxy integration).

THE INVARIANT: this function always returns a contract-valid ChatResponse.
There is no code path that returns a bare 500, a stack trace, or a body the
frontend cannot parse. A client that has implemented the event handler never
needs a special case for "the backend fell over".

That is why every exception is mapped to an ErrorEvent followed by a DoneEvent
rather than being allowed to propagate.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from src.models.base import ModelClient, ModelError
from src.retrieval.base import PriceRepository
from src.schemas.contract import (
    CONTRACT_VERSION,
    ChatRequest,
    ChatResponse,
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    SessionEvent,
    UsageMeta,
)
from src.store.idempotency import (
    AcquireStatus,
    IdempotencyStore,
    fingerprint,
    make_key,
)

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

CORS_HEADERS = {
    "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
    "Content-Type": "application/json",
}

# Cached across warm invocations. Building the graph on every request would
# add avoidable latency to a path already tight against the 29s ceiling.
_repo: PriceRepository | None = None
_model: ModelClient | None = None
_idempotency: IdempotencyStore | None = None


def _dependencies() -> tuple[PriceRepository, ModelClient]:
    """
    Resolve the repository and model client.

    Selected by environment rather than hardcoded, so the same handler runs
    against fixtures locally and DynamoDB/Bedrock in AWS.
    """
    global _repo, _model

    if _repo is None:
        if os.environ.get("USE_DYNAMODB") == "1":
            from src.retrieval.dynamo import DynamoPriceRepository

            _repo = DynamoPriceRepository()
        else:
            from src.retrieval.memory import InMemoryPriceRepository

            _repo = InMemoryPriceRepository()

    if _model is None:
        if os.environ.get("USE_BEDROCK") == "1":
            from src.models.bedrock import BedrockModelClient

            _model = BedrockModelClient()
        else:
            from src.models.scripted import ScriptedModelClient

            _model = ScriptedModelClient()

    return _repo, _model


def _idempotency_store() -> IdempotencyStore:
    global _idempotency

    if _idempotency is None:
        if os.environ.get("USE_DYNAMODB") == "1":
            from src.store.dynamo_idempotency import DynamoIdempotencyStore

            _idempotency = DynamoIdempotencyStore()
        else:
            from src.store.idempotency import InMemoryIdempotencyStore

            # Single-process only. Lambda execution environments share no
            # memory, so this is correct locally and wrong in production —
            # which is why USE_DYNAMODB selects the stored implementation.
            _idempotency = InMemoryIdempotencyStore()

    return _idempotency


def _error_response(
    *,
    session_id: str,
    turn_id: str,
    code: ErrorCode,
    message: str,
    retryable: bool,
) -> ChatResponse:
    """A minimal but complete response: session, error, done."""
    return ChatResponse(
        version=CONTRACT_VERSION,
        session_id=session_id,
        turn_id=turn_id,
        events=[
            SessionEvent(seq=0, session_id=session_id, turn_id=turn_id),
            ErrorEvent(seq=1, code=code, message=message, retryable=retryable),
            DoneEvent(seq=2, server_time=datetime.now(UTC), usage=UsageMeta()),
        ],
    )


def _http(status: int, response: ChatResponse) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": CORS_HEADERS,
        "body": response.model_dump_json(),
    }


def handle_turn(request: ChatRequest) -> tuple[int, ChatResponse]:
    """
    Run one turn. Separated from the Lambda plumbing so it is testable
    without constructing an API Gateway event.
    """
    from src.models.bedrock import GuardrailBlocked
    from src.runner import run_turn

    try:
        # Inside the try deliberately. Dependency construction can fail —
        # missing credentials, an unimplemented adapter, a bad table name —
        # and those must map to a clean error like any other failure, not
        # escape as a bare 500 with a stack trace.
        repo, model = _dependencies()
        return 200, run_turn(request, repo, model)

    except GuardrailBlocked:
        # Deliberately vague to the user, and deliberately NOT logged with the
        # message body — that would put the blocked content in CloudWatch.
        logger.warning("guardrail_intervened session=%s", request.session_id)
        return 200, _error_response(
            session_id=request.session_id,
            turn_id=request.turn_id,
            code=ErrorCode.GUARDRAIL_BLOCKED,
            message=(
                "I can't help with that request. I can compare grocery prices "
                "or plan meals within a budget."
            ),
            retryable=False,
        )

    except ModelError as exc:
        logger.error("model_error session=%s err=%s", request.session_id, exc)
        return 200, _error_response(
            session_id=request.session_id,
            turn_id=request.turn_id,
            code=ErrorCode.INTERNAL_ERROR,
            message="Something went wrong on my end. Please try again.",
            retryable=True,
        )

    except AssertionError as exc:
        # assert_grounded failed: the response contained a price with no
        # citation. Refuse to ship it. This is the last line of the grounding
        # defence and it must fail closed.
        logger.error("grounding_violation session=%s err=%s", request.session_id, exc)
        return 200, _error_response(
            session_id=request.session_id,
            turn_id=request.turn_id,
            code=ErrorCode.INTERNAL_ERROR,
            message="I couldn't verify those prices, so I'd rather not guess. "
            "Please try again.",
            retryable=True,
        )

    except Exception as exc:
        logger.exception("unhandled session=%s err=%s", request.session_id, exc)
        return 200, _error_response(
            session_id=request.session_id,
            turn_id=request.turn_id,
            code=ErrorCode.INTERNAL_ERROR,
            message="Something went wrong on my end. Please try again.",
            retryable=True,
        )


def _is_terminal(response: ChatResponse) -> bool:
    """A result worth caching: anything but a failure the client should retry."""
    return not any(
        e.type == "error" and getattr(e, "retryable", False) for e in response.events
    )


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    started = time.perf_counter()

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 204, "headers": CORS_HEADERS, "body": ""}

    raw_body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw_body = base64.b64decode(raw_body).decode("utf-8")

    # Parse and validate. A malformed request still gets a contract-valid
    # response, because the frontend's error handling should not need a
    # special case for "the body wasn't JSON".
    try:
        request = ChatRequest.model_validate_json(raw_body)
    except ValidationError as exc:
        payload = json.loads(raw_body) if raw_body.strip().startswith("{") else {}
        logger.warning("invalid_request errors=%d", exc.error_count())
        return _http(
            400,
            _error_response(
                session_id=str(payload.get("session_id") or "unknown-session"),
                turn_id=str(payload.get("turn_id") or "unknown-turn"),
                code=ErrorCode.INVALID_REQUEST,
                message="That request wasn't in a format I understand.",
                retryable=False,
            ),
        )
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _http(
            400,
            _error_response(
                session_id="unknown-session",
                turn_id="unknown-turn",
                code=ErrorCode.INVALID_REQUEST,
                message="That request wasn't in a format I understand.",
                retryable=False,
            ),
        )

    # Idempotency. A client that timed out and retried must not trigger a
    # second generation — the first is often still running.
    key = make_key(request.session_id, request.turn_id)
    payload_hash = fingerprint(raw_body)
    store = _idempotency_store()

    try:
        acquired = store.acquire(key, payload_hash)
    except Exception:
        # An idempotency store failure must not fail the turn. Degrade to
        # running the work: a duplicate response is a worse outcome than an
        # error, but a much better one than no response at all.
        logger.exception("idempotency_unavailable session=%s", request.session_id)
        acquired = None

    if acquired is not None:
        if acquired.status is AcquireStatus.COMPLETED and acquired.cached_response:
            logger.info(
                "idempotent_replay session=%s turn=%s",
                request.session_id,
                request.turn_id,
            )
            return {
                "statusCode": 200,
                "headers": {**CORS_HEADERS, "X-Idempotent-Replay": "true"},
                "body": acquired.cached_response,
            }

        if acquired.status is AcquireStatus.IN_PROGRESS:
            return _http(
                409,
                _error_response(
                    session_id=request.session_id,
                    turn_id=request.turn_id,
                    code=ErrorCode.RATE_LIMITED,
                    message="I'm still working on that. Try again in a moment.",
                    retryable=True,
                ),
            )

        if acquired.status is AcquireStatus.PAYLOAD_MISMATCH:
            # Same turn_id, different content. Returning the cached response
            # would answer a question the client did not ask.
            logger.warning(
                "turn_id_reused session=%s turn=%s",
                request.session_id,
                request.turn_id,
            )
            return _http(
                400,
                _error_response(
                    session_id=request.session_id,
                    turn_id=request.turn_id,
                    code=ErrorCode.INVALID_REQUEST,
                    message=(
                        "That request id has already been used for a different "
                        "message. Please use a new one."
                    ),
                    retryable=False,
                ),
            )

    status, response = handle_turn(request)

    if acquired is not None and acquired.status is AcquireStatus.ACQUIRED:
        # Only terminal outcomes are cached. Storing a retryable failure would
        # make the client's retry permanently useless — it would receive the
        # same failure forever.
        if _is_terminal(response):
            store.complete(key, response.model_dump_json())
        else:
            store.release(key)

    # Log identifiers and shape only. Never the message text, never the
    # location — both are personal information under the Privacy Act.
    logger.info(
        "turn session=%s turn=%s events=%d ms=%d",
        request.session_id,
        request.turn_id,
        len(response.events),
        int((time.perf_counter() - started) * 1000),
    )
    return _http(status, response)
