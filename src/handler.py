"""
Lambda entrypoint (API Gateway proxy integration).

THE INVARIANT: this function always returns a contract-valid ChatResponse.
There is no code path that returns a stack trace, an empty body, or anything
the frontend cannot parse. A client that has implemented the event handler
never needs a special case for "the backend fell over".

That is why every exception is mapped to an ErrorEvent followed by a DoneEvent
rather than being allowed to propagate.

The invariant is about the BODY, not the status. An unhandled exception still
answers 500 — see `_last_resort` — because a bug that got past the handlers
should be visible to anything watching HTTP. It answers 500 with a parseable
ChatResponse, which is the part that matters to the client.

OBSERVABILITY (Req 12.1, 12.2) IS ATTACHED HERE AND NOWHERE ELSE. The
Powertools logger, tracer and metrics live at this boundary; the graph
receives instrumented *wrappers* of the repository and model client, which
implement the same Protocols it already depends on. Nothing below this file
knows observability exists, which is what keeps the graph and the eval
harness runnable with no AWS account.

REQ 11.5 IS A HARD CONSTRAINT ON EVERY LOG LINE BELOW. No message text, no
location, no dietary information. Note in particular:

  * `log_event=False` is passed explicitly, not left to default. The
    `POWERTOOLS_LOGGER_LOG_EVENT` environment variable would otherwise dump
    the whole API Gateway event — which contains the user's message — into
    CloudWatch, turning a configuration change into a privacy incident.
  * `capture_response=False` on the tracer, because a meal-plan response
    carries the applied dietary exclusions.
  * `logger.exception()` is never called. A traceback ends with `str(exc)`,
    and a pydantic ValidationError's string form embeds the input that
    failed — for a malformed request, that IS the user's message. Exceptions
    are rendered by `exception_fields()` instead.
"""

from __future__ import annotations

import binascii
import contextlib
import json
import os
import time
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from src.models.base import GuardrailBlocked, ModelClient, ModelError
from src.observability import (
    NULL_TELEMETRY,
    InstrumentedModelClient,
    InstrumentedPriceRepository,
    Telemetry,
    TurnStats,
    exception_fields,
    has_content,
    request_fields,
    response_fields,
    turn_intent,
)
from src.observability.base import (
    METRIC_CACHE_READ_TOKENS,
    METRIC_GUARDRAIL_INTERVENED,
    METRIC_IDEMPOTENCY_UNAVAILABLE,
    METRIC_IDEMPOTENT_REPLAY,
    METRIC_INPUT_TOKENS,
    METRIC_INVALID_REQUEST,
    METRIC_MODEL_CALLS,
    METRIC_OUTPUT_TOKENS,
    METRIC_PREFLIGHT,
    METRIC_REPAIR_ATTEMPTS,
    METRIC_REPAIR_EXHAUSTED,
    METRIC_RETRIEVAL_LATENCY,
    METRIC_TURN_ERROR,
    METRIC_TURN_ID_REUSED,
    METRIC_TURN_LATENCY,
    METRIC_TURN_WITHOUT_CONTENT,
    METRIC_TURNS,
)
from src.observability.powertools import (
    TELEMETRY,
    LocalLambdaContext,
    logger,
    metrics,
    tracer,
)
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


def _best_effort_ids(raw_body: str) -> tuple[str, str]:
    """
    Recover the session and turn ids from a body that failed validation, so
    the error response can still be correlated with the client's request.

    THIS FUNCTION MUST NOT RAISE, and the bare `except Exception` is the
    honest way to say so rather than a shrug. It is called from inside an
    `except ValidationError` block, and an exception raised there does NOT
    fall through to the sibling `except (json.JSONDecodeError, ...)` clause —
    sibling handlers only catch what the `try` raised. It escapes the handler
    entirely and becomes the bare 500 this module exists to prevent.

    That is not hypothetical: this replaces a `json.loads()` guarded only by
    `startswith("{")`, which let a truncated body like `{"session_id": "x",`
    through to the parser and straight out of the Lambda.

    The body is already known to be malformed, so every step has to tolerate
    failure — including the parse succeeding but yielding a list, a string or
    a null, none of which have `.get`.
    """
    try:
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            return "unknown-session", "unknown-turn"
        return (
            str(payload.get("session_id") or "unknown-session"),
            str(payload.get("turn_id") or "unknown-turn"),
        )
    except Exception:
        return "unknown-session", "unknown-turn"


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


def handle_turn(
    request: ChatRequest,
    *,
    telemetry: Telemetry = NULL_TELEMETRY,
    stats: TurnStats | None = None,
) -> tuple[int, ChatResponse]:
    """
    Run one turn. Separated from the Lambda plumbing so it is testable
    without constructing an API Gateway event.

    `telemetry` and `stats` are keyword-only with working defaults, so a
    caller that does not care about observability — a test, the dev server —
    calls this exactly as before and gets a no-op tracer and a throwaway
    accumulator. The wrappers are always applied rather than applied
    conditionally: one code path is worth more than one attribute lookup.
    """
    from src.runner import run_turn

    stats = stats if stats is not None else TurnStats()

    try:
        # Inside the try deliberately. Dependency construction can fail —
        # missing credentials, an unimplemented adapter, a bad table name —
        # and those must map to a clean error like any other failure, not
        # escape as a bare 500 with a stack trace.
        repo, model = _dependencies()
        return 200, run_turn(
            request,
            InstrumentedPriceRepository(repo, telemetry, stats),
            InstrumentedModelClient(model, telemetry, stats),
        )

    except GuardrailBlocked:
        # Deliberately vague to the user, and deliberately NOT logged with the
        # message body — that would put the blocked content in CloudWatch.
        stats.guardrail_intervened = True
        logger.warning("guardrail_intervened")
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
        logger.error("model_error", extra=exception_fields(exc))
        return 200, _error_response(
            session_id=request.session_id,
            turn_id=request.turn_id,
            code=ErrorCode.INTERNAL_ERROR,
            message="Something went wrong on my end. Please try again.",
            retryable=True,
        )

    except AssertionError as exc:
        # A final grounding or response invariant failed. Refuse to ship the
        # response rather than exposing unverifiable content. Exact immutable
        # record/value comparison remains a separate documented follow-up.
        logger.error("grounding_violation", extra=exception_fields(exc))
        return 200, _error_response(
            session_id=request.session_id,
            turn_id=request.turn_id,
            code=ErrorCode.INTERNAL_ERROR,
            message="I couldn't verify those prices, so I'd rather not guess. Please try again.",
            retryable=True,
        )

    except Exception as exc:
        logger.error("unhandled_exception", extra=exception_fields(exc))
        return 200, _error_response(
            session_id=request.session_id,
            turn_id=request.turn_id,
            code=ErrorCode.INTERNAL_ERROR,
            message="Something went wrong on my end. Please try again.",
            retryable=True,
        )


def _is_terminal(response: ChatResponse) -> bool:
    """A result worth caching: anything but a failure the client should retry."""
    return not any(e.type == "error" and getattr(e, "retryable", False) for e in response.events)


def _error_codes(response: ChatResponse) -> list[str]:
    return [str(e.code) for e in response.events if e.type == "error"]


def _emit_turn_metrics(*, response: ChatResponse, stats: TurnStats, elapsed_ms: int) -> None:
    """
    Per-turn metrics in embedded metric format (Req 12.2).

    Undimensioned metrics join this invocation's single EMF record;
    dimensioned ones become their own record (see PowertoolsTelemetry).
    """
    TELEMETRY.duration(METRIC_TURN_LATENCY, elapsed_ms)
    TELEMETRY.count(METRIC_MODEL_CALLS, stats.model_calls)
    TELEMETRY.duration(METRIC_RETRIEVAL_LATENCY, stats.retrieval_ms)
    TELEMETRY.count(METRIC_INPUT_TOKENS, stats.input_tokens)
    TELEMETRY.count(METRIC_OUTPUT_TOKENS, stats.output_tokens)
    # Prompt caching is only worth its write cost if reads actually happen.
    # This is the number that says whether the cachePoint is earning anything.
    TELEMETRY.count(METRIC_CACHE_READ_TOKENS, stats.cache_read_tokens)

    # Only on turns that attempted a plan. Emitting a zero for every price
    # check would drag the average toward zero and hide a rising repair rate.
    if stats.is_plan_turn:
        TELEMETRY.count(METRIC_REPAIR_ATTEMPTS, stats.repair_attempts)

    if stats.guardrail_intervened:
        TELEMETRY.count(METRIC_GUARDRAIL_INTERVENED)

    for code in _error_codes(response):
        TELEMETRY.count(METRIC_TURN_ERROR, 1, code=code)
        if code == ErrorCode.BUDGET_INFEASIBLE:
            TELEMETRY.count(METRIC_REPAIR_EXHAUSTED)

    # A turn that returned session, intent and done and nothing else answered
    # nobody. For out_of_scope and general_chat that is the designed
    # behaviour; for any other intent it means generation is silently
    # dropping its output, and the intent dimension is what tells the two
    # apart in CloudWatch instead of in a user complaint.
    if not has_content(response):
        TELEMETRY.count(METRIC_TURN_WITHOUT_CONTENT, 1, intent=turn_intent(response))


@logger.inject_lambda_context(clear_state=True, log_event=False)
@tracer.capture_lambda_handler(capture_response=False)
@metrics.log_metrics(capture_cold_start_metric=True)
def _observed_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    started = time.perf_counter()

    if event.get("httpMethod") == "OPTIONS":
        # Counted rather than ignored: preflight doubles the gateway request
        # count, which matters for both throttling and cost.
        TELEMETRY.count(METRIC_PREFLIGHT)
        return {"statusCode": 204, "headers": CORS_HEADERS, "body": ""}

    TELEMETRY.count(METRIC_TURNS)

    raw_body = event.get("body") or "{}"

    # Parse and validate. A malformed request still gets a contract-valid
    # response, because the frontend's error handling should not need a
    # special case for "the body wasn't JSON".
    #
    # The base64 decode belongs INSIDE this try, not before it. API Gateway
    # sets isBase64Encoded on binary content types, and a body that is not
    # valid base64 raises binascii.Error while one that is not valid UTF-8
    # raises UnicodeDecodeError — the very exception the second clause below
    # names. Decoded above the try, neither could ever reach the handler that
    # was written for them.
    try:
        if event.get("isBase64Encoded"):
            import base64

            raw_body = base64.b64decode(raw_body).decode("utf-8")

        request = ChatRequest.model_validate_json(raw_body)
    except ValidationError as exc:
        session_id, turn_id = _best_effort_ids(raw_body)
        logger.set_correlation_id(session_id)
        # exception_fields() reduces the ValidationError to a count and the
        # field paths that failed. Logging the exception itself would log the
        # rejected input, which is the message.
        logger.warning("invalid_request", extra=exception_fields(exc))
        TELEMETRY.count(METRIC_INVALID_REQUEST)
        return _http(
            400,
            _error_response(
                session_id=session_id,
                turn_id=turn_id,
                code=ErrorCode.INVALID_REQUEST,
                message="That request wasn't in a format I understand.",
                retryable=False,
            ),
        )
    except (json.JSONDecodeError, UnicodeDecodeError, binascii.Error) as exc:
        logger.warning("unparseable_body", extra=exception_fields(exc))
        TELEMETRY.count(METRIC_INVALID_REQUEST)
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

    # Correlation from here on: every log line in this turn carries the
    # session and turn identifiers (Req 12.1). `clear_state=True` on the
    # decorator drops them again at the end of the invocation, so a warm
    # container cannot carry one user's session id onto the next turn.
    logger.set_correlation_id(request.session_id)
    logger.append_keys(turn_id=request.turn_id)

    stats = TurnStats()

    # Idempotency. A client that timed out and retried must not trigger a
    # second generation — the first is often still running.
    key = make_key(request.session_id, request.turn_id)
    payload_hash = fingerprint(raw_body)
    store = _idempotency_store()

    try:
        acquired = store.acquire(key, payload_hash)
    except Exception as exc:
        # An idempotency store failure must not fail the turn. Degrade to
        # running the work: a duplicate response is a worse outcome than an
        # error, but a much better one than no response at all.
        logger.error("idempotency_unavailable", extra=exception_fields(exc))
        TELEMETRY.count(METRIC_IDEMPOTENCY_UNAVAILABLE)
        acquired = None

    if acquired is not None:
        if acquired.status is AcquireStatus.COMPLETED and acquired.cached_response:
            logger.info("idempotent_replay")
            TELEMETRY.count(METRIC_IDEMPOTENT_REPLAY)
            return {
                "statusCode": 200,
                "headers": {**CORS_HEADERS, "X-Idempotent-Replay": "true"},
                "body": acquired.cached_response,
            }

        if acquired.status is AcquireStatus.IN_PROGRESS:
            logger.info("turn_in_flight")
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
            logger.warning("turn_id_reused")
            TELEMETRY.count(METRIC_TURN_ID_REUSED)
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

    status, response = handle_turn(request, telemetry=TELEMETRY, stats=stats)

    if acquired is not None and acquired.status is AcquireStatus.ACQUIRED:
        # Only terminal outcomes are cached. Storing a retryable failure would
        # make the client's retry permanently useless — it would receive the
        # same failure forever.
        #
        # Guarded for the same reason acquire() is: an idempotency store
        # failure must not fail the turn. The work is already done and the
        # response is already correct — throwing it away because the bookkeeping
        # write failed would turn a degraded cache into a failed request. It
        # was only acquire() that was protected, which left the store able to
        # fail the turn from two lines further down.
        try:
            if _is_terminal(response):
                store.complete(key, response.model_dump_json())
            else:
                store.release(key)
        except Exception as exc:
            logger.error("idempotency_unavailable", extra=exception_fields(exc))
            TELEMETRY.count(METRIC_IDEMPOTENCY_UNAVAILABLE)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    _emit_turn_metrics(response=response, stats=stats, elapsed_ms=elapsed_ms)

    # Identifiers, shape and counts only. Never the message text, never the
    # location, never the dietary exclusions — all three are personal
    # information under the Privacy Act (Req 11.5). Everything derived from
    # the request or the response goes through the helpers in
    # src/observability/base.py, which is where that property is enforced and
    # tested.
    logger.info(
        "turn_complete",
        extra={
            "status": status,
            "latency_ms": elapsed_ms,
            "model_calls": stats.model_calls,
            "models": stats.models_used,
            "model_ms": stats.model_ms,
            "plan_ms": stats.plan_ms,
            "repair_attempts": stats.repair_attempts,
            "retrieval_calls": stats.retrieval_calls,
            "retrieval_ms": stats.retrieval_ms,
            "input_tokens": stats.input_tokens,
            "output_tokens": stats.output_tokens,
            "cache_read_tokens": stats.cache_read_tokens,
            **request_fields(request),
            **response_fields(response),
        },
    )
    return _http(status, response)


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """
    The configured entrypoint.

    A thin shim over the instrumented handler so that callers without a
    Lambda context — the local dev server, the test suite — exercise the same
    decorated path Lambda does, rather than a second uninstrumented one. An
    observability layer that only runs in production is an observability
    layer nobody has tested.

    AND THE LAST RESORT. The invariant at the top of this file says there is
    no path out of here without a contract-valid body. Until now that was a
    property of having enumerated the raising call sites correctly, and three
    were missed — all the same shape: code sitting before the `try`, or
    inside an `except` block, where a raise cannot reach the clause written
    for it. That review is the one that has now failed, so the invariant is
    made structural rather than remembered.

    This does not make failures quiet, and it is not a way of turning a crash
    into a success. Anything arriving here is a bug: it is logged at ERROR
    with its type and code location, and answered with a 500. What changes is
    only that the client gets a body it can parse instead of whatever API
    Gateway synthesises from a stack trace.
    """
    try:
        return _observed_handler(event, context if context is not None else LocalLambdaContext())
    except Exception as exc:
        return _last_resort(event, exc)


def _last_resort(event: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    """
    Turn an escaped exception into the same contract-valid response every
    other failure produces.

    The BODY is identical to every other internal failure: a retryable
    INTERNAL_ERROR followed by a done event, so a client that parses the body
    — which is what the contract tells it to do — needs no new case.

    The STATUS is 500, and deliberately not the 200 that `handle_turn`'s
    handled internal errors return. This function only runs when the
    enumeration of raising call sites has already turned out to be wrong. At
    200 that is indistinguishable at the HTTP layer from an internal error we
    predicted and handled, so nobody learns the net fired without reading
    logs — and the whole reason it exists is that reading was not enough.
    5xx is the one signal that reaches gateway metrics, load-balancer alarms
    and uptime checks without anyone having wired up a log filter first.

    No metric is emitted. The Powertools metrics decorator has already
    flushed this invocation's EMF record by the time an exception reaches
    here, so a count added now would sit in the metric set and be attributed
    to the NEXT invocation of a warm environment. The log line and the status
    are the alarm; see design.md 12.6.
    """
    # `event` is typed as a dict and API Gateway always sends one, but this
    # function is reached exactly when something has already violated an
    # assumption — and `event.get` on a non-dict would raise from inside the
    # net, which is the bug this whole change is about.
    body = event.get("body") if isinstance(event, dict) else None
    session_id, turn_id = _best_effort_ids(body or "{}")

    # Belt and braces: this is the function that must not raise, and it is
    # reached precisely when assumptions have already failed. Losing the log
    # line is bad; losing the response as well because the logger was the
    # thing that broke is worse.
    with contextlib.suppress(Exception):
        logger.error("handler_escaped", extra=exception_fields(exc))

    return _http(
        500,
        _error_response(
            session_id=session_id,
            turn_id=turn_id,
            code=ErrorCode.INTERNAL_ERROR,
            message="Something went wrong on my end. Please try again.",
            retryable=True,
        ),
    )
