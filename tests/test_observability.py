"""
Observability tests (Req 12.1, 12.2) and the privacy constraint on them
(Req 11.5).

The point of this file is that the observability layer is PROVEN NOW, not
after a deployment. Every assertion runs against the real Powertools objects
the handler uses — the same decorated `lambda_handler`, the same Logger,
Tracer and Metrics instances — rather than against mocks that would agree
with whatever the code happens to do:

* Logs are parsed back out of stdout as JSON, the way CloudWatch will.
* Metrics are parsed as embedded metric format records, the way CloudWatch
  will extract them.
* Subsegments are read off a real X-Ray segment. Powertools disables tracing
  outside Lambda, so `xray_segment` re-enables the SDK deliberately and reads
  the tree the recorder actually built.

Two capture paths, because the two utilities write differently. Metrics are
`print()`ed and land in pytest's stdout capture. The Logger's stream handler
bound itself to whatever `sys.stdout` was when the module was first imported,
which is not the object any later fixture replaces — so `log_stream` rebinds
it to a buffer this file owns. `captured()` reads both and returns them
together, so an assertion about "what reached stdout" cannot be satisfied by
output that merely went somewhere else.
"""

from __future__ import annotations

import io
import json
import uuid

import pytest

from src.observability.base import (
    METRIC_CACHE_READ_TOKENS,
    METRIC_GUARDRAIL_INTERVENED,
    METRIC_IDEMPOTENT_REPLAY,
    METRIC_INPUT_TOKENS,
    METRIC_MODEL_LATENCY,
    METRIC_OUTPUT_TOKENS,
    METRIC_REPAIR_ATTEMPTS,
    METRIC_REPAIR_EXHAUSTED,
    METRIC_TURN_LATENCY,
    METRIC_TURN_WITHOUT_CONTENT,
    METRIC_TURNS,
    exception_fields,
    request_fields,
)
from src.schemas.contract import ChatRequest

# A message, a location and a set of dietary exclusions made of words that
# appear nowhere else in this repository. If any of them reaches stdout, it
# got there from the request.
PERSONAL_MESSAGE = (
    "dinner plan for a whanau of five on $20 this week, "
    "quinoa and halloumi, absolutely no shellfish"
)
PERSONAL_LABEL = "Aro Valley"
PERSONAL_EXCLUSIONS = ["shellfish", "dairy-free"]
FORBIDDEN = [
    "quinoa",
    "halloumi",
    "whanau",
    "shellfish",
    "dairy-free",
    PERSONAL_LABEL,
    "Aro",
    "-41.29",
    "174.76",
]


# ------------------------------------------------------------------ fixtures


@pytest.fixture(autouse=True)
def _fresh_idempotency_store(monkeypatch):
    """Module-level cache; reset so replay tests are not contaminated."""
    import src.handler as handler_mod

    monkeypatch.setattr(handler_mod, "_idempotency", None)


@pytest.fixture(autouse=True)
def _cold_start_reset():
    """
    Powertools tracks cold start in module-level flags shared by the whole
    process. Reset both so each test sees the invocation it thinks it does.
    """
    import aws_lambda_powertools.logging.logger as powertools_logger
    from aws_lambda_powertools.metrics.provider.cold_start import reset_cold_start_flag

    reset_cold_start_flag()
    powertools_logger.is_cold_start = True
    yield
    reset_cold_start_flag()
    powertools_logger.is_cold_start = True


@pytest.fixture(autouse=True)
def log_stream():
    """
    Rebind the Powertools log handler to a buffer this file owns.

    The handler took its stream when `src.observability.powertools` was first
    imported, which under pytest is not the object `capsys`/`capfd` later
    replace — so without this the log records are written somewhere no
    assertion can see, and a privacy test would pass by not looking.
    """
    from src.observability.powertools import logger

    handler = logger.registered_handler
    previous = handler.stream
    stream = io.StringIO()
    handler.setStream(stream)
    try:
        yield stream
    finally:
        handler.setStream(previous)


@pytest.fixture
def captured(capfd, log_stream):
    """Everything the invocation wrote: log records and EMF records."""

    def read() -> tuple[str, list[dict], list[dict]]:
        raw = capfd.readouterr().out + "\n" + log_stream.getvalue()
        log_stream.seek(0)
        log_stream.truncate(0)

        logs: list[dict] = []
        emf: list[dict] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            (emf if "_aws" in record else logs).append(record)
        return raw, logs, emf

    return read


@pytest.fixture
def never_affordable(monkeypatch):
    """
    A model whose every draft busts the budget, so the repair loop runs to
    exhaustion instead of succeeding on the first pass.

    `plan_packs` is the scripted client's existing knob for exactly this: a
    real model cannot be made to overspend on demand, and a test that depends
    on the fixture prices staying unaffordable is a test that breaks the next
    time the fixtures change.
    """
    from decimal import Decimal

    import src.handler as handler_mod
    from src.models.scripted import ScriptedModelClient

    monkeypatch.setattr(
        handler_mod, "_model", ScriptedModelClient(plan_packs=Decimal("5"))
    )


@pytest.fixture
def xray_segment():
    """
    A real X-Ray segment to hang subsegments from.

    Powertools disables the X-Ray SDK outside Lambda, which is why nothing is
    emitted by the rest of the suite. Enabling it here is what makes the
    tracing assertions real rather than a check that a no-op was called.
    `streaming_threshold` is raised so completed subsegments stay attached to
    the parent instead of being streamed to a daemon that is not running.
    """
    from aws_xray_sdk import global_sdk_config
    from aws_xray_sdk.core import xray_recorder
    from aws_xray_sdk.core.context import Context

    was_enabled = global_sdk_config.sdk_enabled()
    global_sdk_config.set_sdk_enabled(True)
    xray_recorder.configure(context=Context(), sampling=False, streaming_threshold=100)
    segment = xray_recorder.begin_segment("test-turn")
    try:
        yield segment
    finally:
        xray_recorder.clear_trace_entities()
        global_sdk_config.set_sdk_enabled(was_enabled)


# ------------------------------------------------------------------- helpers


def _body(message: str = "cheapest butter", **extra) -> dict:
    unique = uuid.uuid4().hex[:8]
    return {
        "version": "1.0",
        "session_id": f"sess-{unique}",
        "turn_id": f"turn-{unique}",
        "message": message,
        **extra,
    }


def _event(body: dict | str) -> dict:
    return {
        "httpMethod": "POST",
        "body": body if isinstance(body, str) else json.dumps(body),
    }


def _meal_plan_body(**extra) -> dict:
    """A turn that exercises retrieval, plan generation and the repair loop."""
    return _body(
        PERSONAL_MESSAGE,
        hints={
            "household_size": 5,
            "budget_nzd": 20,
            "days": 3,
            "dietary_exclusions": PERSONAL_EXCLUSIONS,
        },
        location={"lat": -41.29, "lon": 174.76, "label": PERSONAL_LABEL},
        **extra,
    )


def _metric(emf: list[dict], name: str) -> list[tuple[float, dict]]:
    """Every (value, dimensions) pair emitted for a metric name."""
    found: list[tuple[float, dict]] = []
    for record in emf:
        for group in record["_aws"]["CloudWatchMetrics"]:
            if not any(m["Name"] == name for m in group["Metrics"]):
                continue
            dimensions = {
                key: record[key] for names in group["Dimensions"] for key in names
            }
            value = record[name]
            found.append((value[0] if isinstance(value, list) else value, dimensions))
    return found


def _subsegments(segment) -> list:
    out = []
    stack = list(segment.subsegments)
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(current.subsegments)
    return out


def _log(logs: list[dict], message: str) -> dict:
    matches = [record for record in logs if record.get("message") == message]
    assert matches, f"no {message!r} log line in {[r.get('message') for r in logs]}"
    return matches[0]


# ------------------------------------------- Req 11.5: nothing personal in logs


def test_no_request_content_reaches_stdout_on_a_real_turn(captured):
    """
    THE Req 11.5 TEST.

    A full meal-plan turn — retrieval, generation, the repair loop, the
    metrics flush — and then every byte written to stdout is searched for the
    message, the location and the dietary exclusions. Asserting on the whole
    stream rather than on one log record is deliberate: logs, EMF records and
    anything Powertools prints on its own all land here, and a leak in any of
    them is the same incident.
    """
    from src.handler import lambda_handler

    result = lambda_handler(_event(_meal_plan_body()))
    assert result["statusCode"] == 200

    raw, logs, _ = captured()
    assert logs, "the turn produced no log output at all"

    lowered = raw.lower()
    leaked = [term for term in FORBIDDEN if term.lower() in lowered]
    assert not leaked, f"personal information reached stdout: {leaked}\n{raw}"


def test_turn_log_reports_shape_not_content(captured):
    from src.handler import lambda_handler

    lambda_handler(_event(_meal_plan_body()))
    _, logs, _ = captured()

    turn = _log(logs, "turn_complete")
    # Shape is reported...
    assert turn["message_chars"] == len(PERSONAL_MESSAGE)
    assert turn["has_location"] is True
    assert turn["hint_count"] == 4
    assert turn["event_count"] > 0
    # ...and the hint KEYS are withheld along with their values, because a
    # key list reports that this user has dietary restrictions.
    assert "dietary_exclusions" not in json.dumps(turn)


def test_request_fields_is_the_only_request_derived_vocabulary():
    request = ChatRequest.model_validate(_meal_plan_body())
    fields = request_fields(request)

    assert set(fields) == {"message_chars", "has_location", "hint_count"}
    serialised = json.dumps(fields)
    for term in FORBIDDEN:
        assert term.lower() not in serialised.lower()


def test_validation_error_is_logged_without_the_rejected_input(captured):
    """
    pydantic's ValidationError embeds `input_value`. For a malformed request
    that value IS the user's message, so the handler must never log the
    exception itself — only the count and the field paths.
    """
    from src.handler import lambda_handler

    # Valid JSON, wrong shape, carrying the secret text in a rejected field.
    result = lambda_handler(
        _event({"session_id": "short", "message": PERSONAL_MESSAGE, "nonsense": True})
    )
    assert result["statusCode"] == 400

    raw, logs, _ = captured()
    assert "quinoa" not in raw.lower()

    invalid = _log(logs, "invalid_request")
    assert invalid["error_type"] == "ValidationError"
    assert invalid["error_count"] >= 1
    assert "session_id" in invalid["error_fields"]


def test_unhandled_exception_is_logged_without_its_message(captured, monkeypatch):
    """
    The generic failure path must not fall back to logging the exception. An
    unknown exception is precisely the one whose message might quote the user.
    """
    import src.handler as handler_mod
    from src.handler import handle_turn

    def boom(*_args, **_kwargs):
        raise RuntimeError(f"leaking the message: {PERSONAL_MESSAGE}")

    monkeypatch.setattr(handler_mod, "_dependencies", boom)

    status, _ = handle_turn(ChatRequest.model_validate(_meal_plan_body()))
    assert status == 200

    raw, logs, _ = captured()
    assert "quinoa" not in raw.lower()

    unhandled = _log(logs, "unhandled_exception")
    assert unhandled["error_type"] == "RuntimeError"
    # The code location survives, so the defect is still locatable.
    assert any("handler.py" in frame for frame in unhandled["error_at"])
    assert "error_detail" not in unhandled


def test_allowlisted_exceptions_keep_their_message():
    """ModelError text is a Bedrock diagnostic, not user input. It is kept."""
    from src.models.base import ModelError

    try:
        raise ModelError("ThrottlingException from bedrock-runtime")
    except ModelError as exc:
        fields = exception_fields(exc)

    assert fields["error_detail"] == "ThrottlingException from bedrock-runtime"


def test_log_event_stays_off_even_when_the_env_var_turns_it_on(captured, monkeypatch):
    """
    POWERTOOLS_LOGGER_LOG_EVENT dumps the whole API Gateway event, message
    included. The handler passes log_event=False explicitly so that a
    configuration change cannot turn a debugging convenience into a privacy
    incident.
    """
    monkeypatch.setenv("POWERTOOLS_LOGGER_LOG_EVENT", "true")

    from src.handler import lambda_handler

    lambda_handler(_event(_meal_plan_body()))
    raw, _, _ = captured()

    assert "quinoa" not in raw.lower()
    assert "httpMethod" not in raw


# ------------------------------------------------ Req 12.1: structured logging


def test_logs_are_json_correlated_by_session(captured):
    from src.handler import lambda_handler

    body = _body()
    lambda_handler(_event(body))
    _, logs, _ = captured()

    turn = _log(logs, "turn_complete")
    assert turn["correlation_id"] == body["session_id"]
    assert turn["turn_id"] == body["turn_id"]
    assert turn["service"] == "grocery-orchestrator"
    assert turn["level"] == "INFO"


def test_lambda_context_and_cold_start_are_injected(captured):
    from src.handler import lambda_handler

    lambda_handler(_event(_body()))
    _, logs, _ = captured()
    first = _log(logs, "turn_complete")

    assert first["cold_start"] is True
    assert first["function_name"]
    assert first["function_request_id"]
    assert first["function_memory_size"]

    lambda_handler(_event(_body()))
    _, logs, _ = captured()
    assert _log(logs, "turn_complete")["cold_start"] is False


def test_correlation_state_does_not_survive_into_the_next_invocation(captured):
    """
    `clear_state=True`. Appended keys otherwise persist in a warm execution
    environment, and one user's session id appearing on another user's turn
    is a privacy defect, not just a confusing log.
    """
    from src.handler import lambda_handler

    first = _body()
    lambda_handler(_event(first))
    captured()

    # A request that fails validation before any correlation id is set.
    lambda_handler(_event("not json at all"))
    raw, _, _ = captured()

    assert first["session_id"] not in raw
    assert first["turn_id"] not in raw


# --------------------------------------------------- Req 12.2: X-Ray subsegments


def test_subsegments_cover_retrieval_and_every_model_call(xray_segment, captured):
    from src.handler import lambda_handler

    lambda_handler(_event(_meal_plan_body()))
    captured()

    names = [sub.name for sub in _subsegments(xray_segment)]

    assert "## _observed_handler" in names
    assert "retrieval.candidates_for_budget" in names
    assert "model.classify_intent" in names
    assert "model.generate_plan" in names


def test_each_repair_attempt_is_its_own_subsegment(
    xray_segment, captured, never_affordable
):
    """
    The repair loop spans four graph nodes, so it is traced as one subsegment
    per attempt rather than one wrapping span — which is also the more useful
    breakdown for the 29-second ceiling question, because it shows what each
    attempt costs rather than only the total.
    """
    from src.graph.state import MAX_REPAIR_ATTEMPTS
    from src.handler import lambda_handler

    lambda_handler(_event(_meal_plan_body()))
    captured()

    subsegments = _subsegments(xray_segment)
    assert [s.name for s in subsegments].count("model.generate_plan") == 1

    repairs = [sub for sub in subsegments if sub.name == "model.repair_plan"]
    assert len(repairs) == MAX_REPAIR_ATTEMPTS

    # Numbered within the turn, so a trace shows which attempt cost what.
    attempts = sorted(sub.annotations["attempt"] for sub in repairs)
    assert attempts == list(range(MAX_REPAIR_ATTEMPTS))


def test_model_subsegments_are_annotated_for_latency_attribution(xray_segment, captured):
    from src.handler import lambda_handler

    lambda_handler(_event(_meal_plan_body()))
    captured()

    plan = next(
        sub for sub in _subsegments(xray_segment) if sub.name == "model.generate_plan"
    )
    annotations = plan.annotations

    assert annotations["task"] == "generate_plan"
    assert annotations["tier"] == "quality"
    assert annotations["model"] == "scripted-quality"
    assert "latency_ms" in annotations
    assert annotations["guardrail_intervened"] is False


def test_subsegment_annotations_carry_no_personal_information(xray_segment, captured):
    """Annotations are indexed and queryable, so Req 11.5 applies to them too."""
    from src.handler import lambda_handler

    lambda_handler(_event(_meal_plan_body()))
    captured()

    serialised = json.dumps(
        [sub.annotations for sub in _subsegments(xray_segment)], default=str
    ).lower()
    leaked = [term for term in FORBIDDEN if term.lower() in serialised]
    assert not leaked, f"personal information in trace annotations: {leaked}"


def test_price_check_traces_each_item_lookup(xray_segment, captured):
    from src.handler import lambda_handler

    lambda_handler(_event(_body("compare butter and milk prices")))
    captured()

    names = [sub.name for sub in _subsegments(xray_segment)]
    assert names.count("retrieval.resolve_product_key") == 2


# ------------------------------------------------------- Req 12.2: EMF metrics


def test_turn_emits_the_core_metrics(captured):
    from src.handler import lambda_handler

    lambda_handler(_event(_body()))
    _, _, emf = captured()

    for name in (
        METRIC_TURNS,
        METRIC_TURN_LATENCY,
        METRIC_INPUT_TOKENS,
        METRIC_OUTPUT_TOKENS,
        METRIC_CACHE_READ_TOKENS,
    ):
        assert _metric(emf, name), f"{name} was not emitted"

    assert _metric(emf, METRIC_TURNS)[0][0] == 1.0
    assert _metric(emf, METRIC_TURN_LATENCY)[0][0] > 0


def test_model_latency_is_dimensioned_by_model_and_task(captured):
    from src.handler import lambda_handler

    lambda_handler(_event(_meal_plan_body()))
    _, _, emf = captured()

    emitted = _metric(emf, METRIC_MODEL_LATENCY)
    by_task = {dimensions["task"]: dimensions["model"] for _, dimensions in emitted}

    assert by_task["classify_intent"] == "scripted-fast"
    assert by_task["generate_plan"] == "scripted-quality"
    assert by_task["repair_plan"] == "scripted-fast"
    assert all("service" in dimensions for _, dimensions in emitted)


def test_repair_attempts_are_counted_when_the_loop_exhausts(captured, never_affordable):
    from src.graph.state import MAX_REPAIR_ATTEMPTS
    from src.handler import lambda_handler

    lambda_handler(_event(_meal_plan_body()))
    _, _, emf = captured()

    assert _metric(emf, METRIC_REPAIR_ATTEMPTS)[0][0] == float(MAX_REPAIR_ATTEMPTS)
    # The failing plan is discarded on this path, so the count cannot come
    # from the response — it comes from counting the model calls.
    assert _metric(emf, METRIC_REPAIR_EXHAUSTED)[0][0] == 1.0


def test_repair_metric_matches_the_plan_that_was_returned(captured):
    """
    On a turn that succeeds, the metric and the MealPlan agree. Two
    independent counts of the same thing — the wrapper's model calls and the
    graph's own attempt counter — so a drift in either is visible here rather
    than in a CloudWatch dashboard nobody can reconcile.
    """
    from src.handler import lambda_handler

    result = lambda_handler(_event(_meal_plan_body()))
    _, _, emf = captured()

    plans = [
        event for event in json.loads(result["body"])["events"] if event["type"] == "meal_plan"
    ]
    assert plans, "expected a meal plan on this turn"

    assert _metric(emf, METRIC_REPAIR_ATTEMPTS)[0][0] == float(
        plans[0]["data"]["repair_attempts"]
    )


def test_repair_attempts_absent_on_turns_that_never_planned(captured):
    """
    A zero for every price check would drag the average toward zero and hide
    a rising repair rate on the turns that actually plan.
    """
    from src.handler import lambda_handler

    lambda_handler(_event(_body("cheapest butter")))
    _, _, emf = captured()

    assert not _metric(emf, METRIC_REPAIR_ATTEMPTS)


def test_turn_without_content_is_counted_with_its_intent(captured):
    """
    The out_of_scope path returns session, intent and done — nothing else.
    That is expected there, and the intent dimension is what would let a
    silent drop on any OTHER intent be seen in CloudWatch rather than in a
    user complaint.
    """
    from src.handler import lambda_handler

    result = lambda_handler(_event(_body("write me a limerick about the weather")))
    events = json.loads(result["body"])["events"]
    assert [e["type"] for e in events] == ["session", "intent", "done"]

    _, _, emf = captured()
    emitted = _metric(emf, METRIC_TURN_WITHOUT_CONTENT)
    assert emitted, "a turn with no content event was not counted"
    assert emitted[0][1]["intent"] == "out_of_scope"


def test_answered_turns_are_not_counted_as_contentless(captured):
    from src.handler import lambda_handler

    lambda_handler(_event(_body("cheapest butter")))
    _, _, emf = captured()

    assert not _metric(emf, METRIC_TURN_WITHOUT_CONTENT)


def test_idempotent_replay_is_counted(captured):
    from src.handler import lambda_handler

    body = _body()
    lambda_handler(_event(body))
    captured()

    replay = lambda_handler(_event(body))
    assert replay["headers"]["X-Idempotent-Replay"] == "true"

    raw, logs, emf = captured()
    assert _metric(emf, METRIC_IDEMPOTENT_REPLAY)[0][0] == 1.0
    assert _log(logs, "idempotent_replay")
    assert "quinoa" not in raw.lower()


def test_guardrail_intervention_is_counted(captured, monkeypatch):
    from src.handler import handle_turn
    from src.models.bedrock import GuardrailBlocked

    def blocked(*_args, **_kwargs):
        raise GuardrailBlocked("Request blocked by Bedrock Guardrail")

    monkeypatch.setattr("src.runner.run_turn", blocked)

    from src.observability import TurnStats
    from src.observability.powertools import TELEMETRY, metrics

    stats = TurnStats()
    status, _ = handle_turn(
        ChatRequest.model_validate(_meal_plan_body()), telemetry=TELEMETRY, stats=stats
    )
    assert status == 200
    assert stats.guardrail_intervened is True

    # handle_turn does not flush; the decorator on the handler does. Flushing
    # here is what makes the metric observable in this test.
    TELEMETRY.count(METRIC_GUARDRAIL_INTERVENED)
    metrics.flush_metrics()

    raw, _, emf = captured()
    assert _metric(emf, METRIC_GUARDRAIL_INTERVENED)[0][0] == 1.0
    # The blocked content must not be logged, which is the whole reason the
    # guardrail warning carries no detail.
    assert "quinoa" not in raw.lower()


# ------------------------------------------------------------ the boundary


def test_graph_and_evals_do_not_import_powertools():
    """
    The constraint that makes this design work: Powertools stays at the
    handler edge. The graph, the model plane, the retrieval layer, the runner
    and both eval harnesses must keep running with no AWS account and no
    Powertools install, which is why CI needs no credentials.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    allowed = {
        root / "src" / "handler.py",
        root / "src" / "observability" / "powertools.py",
    }

    offenders: list[str] = []
    for path in [*(root / "src").rglob("*.py"), *(root / "evals").rglob("*.py")]:
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                continue
            if any(m.split(".")[0] in {"aws_lambda_powertools", "aws_xray_sdk"} for m in modules):
                offenders.append(str(path.relative_to(root)))

    assert not offenders, f"Powertools escaped the handler boundary: {sorted(set(offenders))}"


def test_graph_runs_with_the_null_telemetry():
    """
    The default path: no telemetry, no stats, no Powertools involvement. This
    is what the eval harness and every other test exercise, and it must stay
    a working, unmeasured code path.
    """
    from src.models.scripted import ScriptedModelClient
    from src.observability import (
        NULL_TELEMETRY,
        InstrumentedModelClient,
        InstrumentedPriceRepository,
        TurnStats,
    )
    from src.retrieval.memory import InMemoryPriceRepository
    from src.runner import run_turn

    stats = TurnStats()
    response = run_turn(
        ChatRequest.model_validate(_body("cheapest butter")),
        InstrumentedPriceRepository(InMemoryPriceRepository(), NULL_TELEMETRY, stats),
        InstrumentedModelClient(ScriptedModelClient(), NULL_TELEMETRY, stats),
    )

    assert response.events[-1].type == "done"
    # The wrappers still account for the turn even with telemetry switched off,
    # which is what lets the handler report usage on a path that emits nothing.
    assert stats.model_calls > 0
    assert stats.retrieval_calls > 0
