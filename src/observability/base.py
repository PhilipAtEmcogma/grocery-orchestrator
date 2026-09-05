"""
The observability boundary.

Powertools is a Lambda runtime concern. The graph and the eval harness run
with no AWS account and no Powertools install, and that property is
load-bearing — it is why CI needs no credentials. So instrumentation gets the
same treatment as retrieval and the model plane: a Protocol with a
do-nothing default. Instrumented code cannot tell whether anyone is
listening, and `src/observability/powertools.py` is the ONLY module in the
repo that imports `aws_lambda_powertools`.

REQ 11.5 LIVES IN THIS FILE. Every field derived from a request, a response
or an exception for logging passes through `request_fields()`,
`response_fields()` or `exception_fields()` below. That makes "no message
text, no location, no dietary information in logs" a property of three
reviewable functions rather than a habit spread across call sites — and it
makes the property testable, which `tests/test_observability.py` does.

Nothing here imports boto3, aws_lambda_powertools, or anything else that
needs credentials.
"""

from __future__ import annotations

import traceback
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import ValidationError

from src.models.base import PLAN_TASKS, REPAIR_TASKS, ModelError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from src.schemas.contract import ChatRequest, ChatResponse

# --------------------------------------------------------------- metric names
#
# One vocabulary, declared where nothing AWS-specific can reach it, so tests
# assert against the same constants the handler emits rather than string
# literals that can drift apart.

METRIC_TURNS = "TurnsProcessed"
METRIC_TURN_LATENCY = "TurnLatency"
METRIC_MODEL_LATENCY = "ModelLatency"
METRIC_MODEL_CALLS = "ModelCalls"
METRIC_RETRIEVAL_LATENCY = "RetrievalLatency"
METRIC_INPUT_TOKENS = "InputTokens"
METRIC_OUTPUT_TOKENS = "OutputTokens"
METRIC_CACHE_READ_TOKENS = "CacheReadTokens"
METRIC_REPAIR_ATTEMPTS = "RepairAttempts"
METRIC_REPAIR_EXHAUSTED = "RepairExhausted"
METRIC_GUARDRAIL_INTERVENED = "GuardrailIntervened"
METRIC_IDEMPOTENT_REPLAY = "IdempotentReplay"
METRIC_TURN_WITHOUT_CONTENT = "TurnWithoutContent"
METRIC_TURN_ID_REUSED = "TurnIdReused"
METRIC_IDEMPOTENCY_UNAVAILABLE = "IdempotencyUnavailable"
# A turn finished but another invocation had taken over its claim, so the result
# could not be cached. Worth a metric rather than a silent log line: a sustained
# rate means invocations are routinely running past the in-progress timeout,
# which is a latency problem wearing an idempotency costume.
METRIC_IDEMPOTENCY_CLAIM_LOST = "IdempotencyClaimLost"
METRIC_INVALID_REQUEST = "InvalidRequest"
METRIC_TURN_ERROR = "TurnError"
METRIC_PREFLIGHT = "PreflightRequests"

# Tasks whose model calls make up the meal-plan generation/repair cycle. The
# repair loop spans several graph nodes, so it is measured as the calls it
# makes rather than as one wrapping span — see `instrumented.py`.


# ------------------------------------------------------------------- protocol


class Span(Protocol):
    """One timed unit of work. An X-Ray subsegment, or nothing at all."""

    def annotate(self, **annotations: str | int | float | bool) -> None:
        """
        Attach indexed, searchable key/values to this span.

        Annotations are indexed by X-Ray and therefore queryable, which is
        also why they must never carry personal information (Req 11.5).
        """
        ...


class Telemetry(Protocol):
    """
    Spans and metrics, without naming a vendor.

    `count`/`duration` split on dimensions deliberately: a dimensioned metric
    becomes its own embedded-metric-format record (per-model latency needs
    its own dimension set), while an undimensioned one joins the turn's
    single record. Callers do not need to know that; they just say whether
    the number is per-model, per-intent, or per-turn.
    """

    # The return type is load-bearing and was wrong. Left unannotated, a
    # checker infers `None` from the `...` body, so BOTH implementations
    # failed to satisfy this protocol — they are `@contextmanager`-decorated
    # and return a `_GeneratorContextManager`. Every `with telemetry.span(...)`
    # was an error, and so was every assignment of a concrete telemetry to a
    # `Telemetry` parameter. The implementations were right; this line was
    # wrong.
    #
    # `AbstractContextManager[Span]` rather than `[Any]`: the two
    # implementations yield different concrete spans (`NullSpan`, `_XraySpan`)
    # but both satisfy `Span`, and the context manager is covariant in what it
    # yields, so the common protocol types this exactly. `Any` would type-check
    # just as quietly if someone yielded something with no `annotate()`.
    def span(
        self, name: str, **annotations: str | int | float | bool
    ) -> AbstractContextManager[Span]: ...

    def count(self, name: str, value: float = 1.0, **dimensions: str) -> None: ...

    def duration(self, name: str, milliseconds: float, **dimensions: str) -> None: ...


class NullSpan:
    """The default span. Does nothing, cheaply."""

    __slots__ = ()

    def annotate(self, **annotations: str | int | float | bool) -> None:
        return


class NullTelemetry:
    """
    The default Telemetry. Every measurement is discarded.

    This is what the eval harness, the local dev server and every test that
    is not about observability get, and it is why instrumenting the model and
    repository wrappers costs those callers nothing but an attribute lookup.
    """

    __slots__ = ()

    @contextmanager
    def span(self, name: str, **annotations: str | int | float | bool) -> Iterator[NullSpan]:
        yield _NULL_SPAN

    def count(self, name: str, value: float = 1.0, **dimensions: str) -> None:
        return

    def duration(self, name: str, milliseconds: float, **dimensions: str) -> None:
        return


_NULL_SPAN = NullSpan()
NULL_TELEMETRY = NullTelemetry()


# ------------------------------------------------- static conformance checks
#
# These bindings exist ONLY to be type-checked. Assigning a concrete object to
# a protocol-annotated name is what makes a checker verify the implementation
# against the protocol, member by member and return type by return type.
#
# Without them nothing checked conformance at all. `Protocol` is not
# `@runtime_checkable` here, and even if it were, `isinstance()` against a
# runtime-checkable Protocol only tests that the attribute NAMES exist — it
# does not look at signatures or return types. So the tests could pass, the
# suite could be green, and `Telemetry` could be a description of something no
# implementation actually was. That is exactly what had happened: `span()`
# declared a `None` return that neither implementation had.
#
# The Powertools implementation is asserted in `powertools.py` instead, for
# the reason this module exists: importing it here would drag
# `aws_lambda_powertools` into the graph and the eval harness, which is the
# boundary `test_graph_and_evals_do_not_import_powertools` enforces.
if TYPE_CHECKING:
    _null_telemetry_conforms: Telemetry = NULL_TELEMETRY
    _null_span_conforms: Span = _NULL_SPAN


# ------------------------------------------------------------------ turn stats


@dataclass
class TurnStats:
    """
    What one turn consumed, accumulated by the wrappers in `instrumented.py`.

    Deliberately NOT global and NOT cached: one instance per turn, created by
    the handler. A Lambda execution environment serves many turns, and a
    counter that survives between them reports the wrong number on the second
    invocation — the same reasoning that makes `clear_state=True` mandatory on
    the logger.

    Counts and durations only. Nothing here is derived from message content.

    Durations are floats. A DynamoDB query and a fixture lookup both round to
    zero as integer milliseconds, and a latency breakdown whose fast half
    reads as 0 cannot be used to attribute anything.
    """

    model_calls: int = 0
    model_ms: float = 0.0
    plan_ms: float = 0.0
    retrieval_calls: int = 0
    retrieval_ms: float = 0.0
    repair_attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    guardrail_intervened: bool = False
    models_used: list[str] = field(default_factory=list)

    plan_calls: int = 0

    @property
    def is_plan_turn(self) -> bool:
        """True once plan generation has been attempted at least once."""
        return self.plan_calls > 0

    def record_model(self, *, model: str, task: str, elapsed_ms: float, usage: dict) -> None:
        self.model_calls += 1
        self.model_ms += elapsed_ms
        if model not in self.models_used:
            self.models_used.append(model)

        self.input_tokens += _as_int(usage.get("input_tokens"))
        self.output_tokens += _as_int(usage.get("output_tokens"))
        self.cache_read_tokens += _as_int(usage.get("cache_read_tokens"))
        if usage.get("guardrail_intervened"):
            self.guardrail_intervened = True

        if task in PLAN_TASKS:
            self.plan_calls += 1
            self.plan_ms += elapsed_ms
        # Every call after the first is a repair pass. Counting the calls
        # rather than reading the finished plan is what makes this correct on
        # the infeasible path too, where the failing plan is discarded and
        # there is no MealPlan left to read `repair_attempts` off.
        if task in REPAIR_TASKS:
            self.repair_attempts += 1


def _as_int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) else 0


# ------------------------------------------------------- Req 11.5 log fields


# Event kinds that carry something to the user. A turn with none of these
# answered nobody: `out_of_scope` and `general_chat` emit session, intent and
# done and nothing else, which is expected — but so does a generation path
# that has started silently dropping its output, which is not.
CONTENT_EVENT_TYPES = frozenset(
    {"token", "price_comparison", "meal_plan", "no_data", "notice", "error"}
)

# Exception types whose message text is known to contain only internal
# identifiers — a Bedrock error string, a citation ref, a schema name. Every
# other exception is logged as a type and a code location, because the
# message may quote the user. `pydantic.ValidationError` is the specific
# reason this allowlist exists: its string form embeds `input_value`, which
# for a malformed request IS the user's message.
_MESSAGE_SAFE_EXCEPTIONS: tuple[type[BaseException], ...] = (ModelError, AssertionError)

_MAX_FRAMES = 6
_MAX_DETAIL_CHARS = 500


def request_fields(request: ChatRequest) -> dict[str, object]:
    """
    The only request-derived fields permitted in a log line (Req 11.5).

    Shape, never content. How long the message was, whether a location was
    supplied, how many hints arrived — not the message, not the suburb or
    coordinates, and not the dietary exclusions, whose *names* are withheld
    as well as their values because a restriction can imply health
    information (Req 11.6). `hint_count` is a number for the same reason:
    a key list would report that this user has dietary restrictions.
    """
    hints = request.hints.model_dump() if request.hints else {}
    return {
        "message_chars": len(request.message),
        "has_location": request.location is not None,
        "hint_count": sum(1 for value in hints.values() if value not in (None, [], {})),
    }


def response_fields(response: ChatResponse) -> dict[str, object]:
    """
    Shape of what was returned. Event *kinds* and counts, never payloads —
    a meal plan payload carries the applied dietary exclusions.
    """
    types = [event.type for event in response.events]
    return {
        "event_count": len(types),
        "event_types": sorted(set(types)),
        "has_content": has_content(response),
    }


def has_content(response: ChatResponse) -> bool:
    return any(event.type in CONTENT_EVENT_TYPES for event in response.events)


def turn_intent(response: ChatResponse) -> str:
    """
    The classified intent, for use as a metric dimension.

    Bounded cardinality: it is an enum. Returns 'unclassified' when the turn
    failed before classification, which is itself worth being able to see.
    """
    for event in response.events:
        if event.type == "intent":
            return str(event.intent)
    return "unclassified"


def exception_fields(exc: BaseException) -> dict[str, object]:
    """
    An exception rendered for logging without quoting the user.

    Always: the type and the code path that produced it, as `file:line in
    function` — enough to localise a defect exactly, and made of our source
    layout rather than anyone's data.

    The message is included only for exception types allowlisted above.
    `ValidationError` gets a count and the field paths that failed, which is
    the diagnostic half of the message with none of the input values.

    This is why the handler does not call `logger.exception()`: a traceback
    ends with `str(exc)`, so the fail-safe generic path would be the one that
    leaks.
    """
    fields: dict[str, object] = {
        "error_type": type(exc).__name__,
        "error_at": _frames(exc),
    }

    if isinstance(exc, ValidationError):
        fields["error_count"] = exc.error_count()
        # `loc` only. `errors()` entries also carry `input` and `msg`, which
        # is where the user's message would be.
        fields["error_fields"] = sorted(
            {".".join(str(part) for part in error["loc"]) for error in exc.errors()}
        )
    elif isinstance(exc, _MESSAGE_SAFE_EXCEPTIONS):
        fields["error_detail"] = str(exc)[:_MAX_DETAIL_CHARS]

    return fields


def _frames(exc: BaseException) -> list[str]:
    """Innermost frames as `file:line in function`. No source text, no locals."""
    return [
        f"{PurePath(frame.filename).name}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(exc.__traceback__)[-_MAX_FRAMES:]
    ]
