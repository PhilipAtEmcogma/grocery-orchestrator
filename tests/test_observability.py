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
import logging
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
# $30 here is DELIBERATELY not affordable against whole-pack pricing: the
# repair and exhaustion tests below need a turn whose drafts bust the
# budget. Tests that need a delivered plan use _affordable_meal_plan_body.
# Note the message names a budget, and a message beats a hint when both do.
PERSONAL_MESSAGE = (
    "dinner plan for a whanau of five on $30 this week, "
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

# Field NAMES, which leak even when their values do not. `hint_count` is a
# number rather than a key list precisely because "this user set
# dietary_exclusions" reports that they have dietary restrictions, and a
# restriction can imply health information (Req 11.6). Kept separate from
# FORBIDDEN because these are our schema's words, not the user's — a test
# asserting "this came from the request" cannot use them.
FORBIDDEN_KEYS = [
    "dietary_exclusions",
    "budget_nzd",
    "household_size",
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

    # Powertools types `registered_handler` as the base `logging.Handler`,
    # which has no stream. It is a StreamHandler in fact — that is the whole
    # reason this fixture can rebind it — so narrow it rather than reaching
    # through the declared type. The assert also turns a future Powertools
    # change of handler class into a clear failure here instead of an
    # AttributeError inside a privacy test.
    handler = logger.registered_handler
    assert isinstance(handler, logging.StreamHandler), (
        f"expected a StreamHandler to rebind, got {type(handler).__name__}"
    )
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


class _RecordSink(logging.Handler):
    """A root handler that keeps every stdlib record, at any level."""

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        # Never raise: a formatting error here would mask the leak the caller
        # is looking for, so fall back to the rawest form of the record.
        try:
            self.lines.append(self.format(record))
        except Exception:  # pragma: no cover - defensive
            self.lines.append(f"{record.name} {record.msg!r} {record.args!r}")


@pytest.fixture
def every_sink(capfd, log_stream):
    """
    Everything this invocation could put in CloudWatch — not just stdout.

    `captured()` reads stdout, which is where Powertools writes. That is not
    the whole log surface, and the two tests below exist because the gaps are
    reachable:

    * Lambda ships the function's STDERR to the same log group. A bare
      `sys.stderr.write` or anything writing to it leaks exactly as much as a
      print, and a fixture reading only `.out` scores it as a pass.
    * The graph deliberately cannot import Powertools (see
      `test_graph_and_evals_do_not_import_powertools`), so a node author who
      wants to log has stdlib `logging` and nothing else. Those records go to
      a root handler — installed by the Lambda runtime in production, and by
      pytest here, which swallows them before they reach any stream. So this
      attaches its own root handler rather than trusting a stream to catch
      them.
    * Both loggers are forced to DEBUG, because the level is an environment
      variable. A `logger.debug(request.message)` that is silent at the
      default INFO is one `LOG_LEVEL` change away from being printed, and a
      test that cannot see it is a test that approves it.
    """
    from src.observability.powertools import logger as powertools_logger

    root = logging.getLogger()
    sink = _RecordSink()
    sink.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))

    previous_root_level = root.level
    previous_log_level = powertools_logger.log_level
    root.addHandler(sink)
    root.setLevel(logging.DEBUG)
    powertools_logger.setLevel(logging.DEBUG)

    def read() -> str:
        streams = capfd.readouterr()
        text = "\n".join([streams.out, streams.err, log_stream.getvalue(), *sink.lines])
        log_stream.seek(0)
        log_stream.truncate(0)
        sink.lines.clear()
        return text

    try:
        yield read
    finally:
        root.removeHandler(sink)
        root.setLevel(previous_root_level)
        powertools_logger.setLevel(previous_log_level)


@pytest.fixture
def never_affordable(monkeypatch):
    """
    A turn whose draft busts the budget, so the repair loop runs to exhaustion
    instead of succeeding on the first pass.

    Two knobs, because one is no longer enough. `plan_packs` inflates portion
    sizes, which raises CONSUMPTION -- and the budget check now compares
    PAYABLE, the whole-pack cost, which portion size does not move at all. On
    its own this fixture stopped forcing anything.

    So retrieval is uncapped too. Production pre-filters candidates so that
    buying every one of them ONCE stays inside the budget, which removes the
    common overspend without eliminating it -- a draft using 1.2 packs buys
    two and can still exceed. Uncapping here forces that state reliably rather
    than waiting for a multi-pack draft to turn up.
    """
    from decimal import Decimal

    import src.handler as handler_mod
    from src.models.scripted import ScriptedModelClient
    from src.retrieval.memory import InMemoryPriceRepository

    class _Uncapped(InMemoryPriceRepository):
        def candidates_for_budget(self, **kwargs):
            kwargs["budget_nzd"] = None
            return super().candidates_for_budget(**kwargs)

    monkeypatch.setattr(handler_mod, "_repo", _Uncapped())
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


def _personal_body(message: str, **extra) -> dict:
    """
    A request carrying all three kinds of personal information at once —
    message text, a location and dietary exclusions — so that whichever path
    the turn takes, there is something of each kind available to leak.
    """
    return _body(
        message,
        hints={
            "household_size": 5,
            "budget_nzd": 30,
            "days": 3,
            "dietary_exclusions": PERSONAL_EXCLUSIONS,
        },
        location={"lat": -41.29, "lon": 174.76, "label": PERSONAL_LABEL},
        **extra,
    )


def _meal_plan_body(**extra) -> dict:
    """A turn that exercises retrieval, plan generation and the repair loop."""
    return _personal_body(PERSONAL_MESSAGE, **extra)


def _affordable_meal_plan_body(**extra) -> dict:
    """
    A plan turn that actually succeeds.

    _meal_plan_body deliberately cannot be afforded, which is what the repair
    and exhaustion tests want. Tests that need a delivered meal_plan event
    need the opposite, and the budget has to be feasible against PAYABLE cost
    -- whole packs, not fractional consumption. Both the message and the hint
    carry the figure because the message wins when they disagree.
    """
    return _body(
        "dinner plan for a whanau of five on $90 this week, no shellfish",
        hints={
            "household_size": 5,
            "budget_nzd": 90,
            "days": 3,
            "dietary_exclusions": PERSONAL_EXCLUSIONS,
        },
        location={"lat": -41.29, "lon": 174.76, "label": PERSONAL_LABEL},
        **extra,
    )


def _repairable_body(**extra) -> dict:
    """
    A turn that reaches generation and then busts its budget, so the repair
    loop runs.

    The window is narrow and both edges matter. Above the feasibility floor
    (5 people x 7 days needs at least ~$33 at the cheapest price per gram), or
    the turn is refused before a single model call. Below what the uncapped
    candidate set costs (~$50), or the draft fits and there is nothing to
    repair. $40 sits between the two.

    Pair it with `never_affordable`, which removes the candidate cap. The cap
    does not make overspend impossible -- multi-pack usage still can -- but
    removing it makes the state reliable to reproduce.
    """
    return _body(
        "dinner plan for a whanau of five on $40 this week, no shellfish",
        hints={
            "household_size": 5,
            "budget_nzd": 40,
            "days": 7,
            "dietary_exclusions": PERSONAL_EXCLUSIONS,
        },
        location={"lat": -41.29, "lon": 174.76, "label": PERSONAL_LABEL},
        **extra,
    )


def _invoke(body: dict | str) -> dict:
    from src.handler import lambda_handler

    return lambda_handler(_event(body))


def _types(result: dict) -> set[str]:
    return {event["type"] for event in json.loads(result["body"])["events"]}


def _codes(result: dict) -> set[str]:
    return {
        str(event["code"]).lower()
        for event in json.loads(result["body"])["events"]
        if event["type"] == "error"
    }


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


# ---------------------------------------------- every path, every log sink
#
# The test above runs ONE turn and reads ONE stream, which is how the leak it
# was written for survived review: the reviewer checks the log calls on the
# path in front of them, and the test agrees with the reviewer. These
# scenarios exist so that a careless log line has nowhere quiet to sit. Each
# one asserts the path it claims to take BEFORE the scan, because a scenario
# that silently stops reaching `emit_no_data` degrades into a fourth copy of
# the meal-plan turn and still passes.


def _turn_meal_plan(monkeypatch) -> None:
    # The affordable body: this scenario exists to put a DELIVERED plan in
    # front of the log scan, and _meal_plan_body deliberately cannot be
    # afforded. The assert above is exactly the guard described in the comment
    # -- without it this scenario would quietly become a budget_infeasible
    # turn and stop covering the plan path at all.
    assert "meal_plan" in _types(_invoke(_affordable_meal_plan_body()))


def _turn_price_check(monkeypatch) -> None:
    """butter and milk are in the fixtures, so this reaches
    generate_comparison — a node the meal-plan turn never visits."""
    result = _invoke(
        _personal_body("whanau shopping: how much do butter and milk cost")
    )
    assert "price_comparison" in _types(result)


def _turn_no_data(monkeypatch) -> None:
    """Nothing in the fixtures matches, so the graph takes the no-data edge."""
    result = _invoke(_personal_body("how much do quinoa and halloumi cost"))
    assert "no_data" in _types(result)
    assert "price_comparison" not in _types(result)


def _turn_out_of_scope(monkeypatch) -> None:
    result = _invoke(_personal_body("write me a limerick about quinoa and halloumi"))
    assert _types(result) == {"session", "intent", "done"}


def _turn_budget_infeasible(monkeypatch) -> None:
    """The repair loop runs to exhaustion and the failing plan is discarded."""
    from decimal import Decimal

    import src.handler as handler_mod
    from src.models.scripted import ScriptedModelClient

    monkeypatch.setattr(
        handler_mod, "_model", ScriptedModelClient(plan_packs=Decimal("5"))
    )
    assert "budget_infeasible" in _codes(_invoke(_meal_plan_body()))


def _turn_guardrail_blocked(monkeypatch) -> None:
    """
    The exception text is the blocked content, which is the worst thing in
    the process to log — and the reason the handler's guardrail branch logs a
    bare event name with no fields at all.
    """
    from src.models.base import GuardrailBlocked

    def blocked(*_args, **_kwargs):
        raise GuardrailBlocked(f"blocked input: {PERSONAL_MESSAGE}")

    monkeypatch.setattr("src.runner.run_turn", blocked)
    assert "guardrail_blocked" in _codes(_invoke(_meal_plan_body()))


def _turn_unhandled_exception(monkeypatch) -> None:
    import src.handler as handler_mod

    def boom(*_args, **_kwargs):
        raise RuntimeError(f"leaking the message: {PERSONAL_MESSAGE}")

    monkeypatch.setattr(handler_mod, "_dependencies", boom)
    assert "internal_error" in _codes(_invoke(_meal_plan_body()))


def _turn_handler_escaped(monkeypatch) -> None:
    """
    The last-resort net in `lambda_handler`. It is a log call on a path that
    by definition nobody predicted, so it gets scanned like the predictable
    ones — the exception carries the message here for exactly that reason.
    """
    import src.handler as handler_mod

    def boom(*_args, **_kwargs):
        raise RuntimeError(f"escaped with: {PERSONAL_MESSAGE}")

    monkeypatch.setattr(handler_mod, "_observed_handler", boom)
    assert "internal_error" in _codes(_invoke(_meal_plan_body()))


def _turn_model_error(monkeypatch) -> None:
    """
    ModelError text is allowlisted into the log, so the text here is what a
    real one carries. Putting the user's message in a ModelError would be
    contriving the leak the allowlist deliberately accepts; the point of this
    scenario is that the error PATH gets scanned like every other.
    """
    from src.models.base import ModelError

    def throttled(*_args, **_kwargs):
        raise ModelError("ThrottlingException from bedrock-runtime")

    monkeypatch.setattr("src.runner.run_turn", throttled)
    assert "internal_error" in _codes(_invoke(_meal_plan_body()))


def _turn_invalid_request(monkeypatch) -> None:
    """Valid JSON, wrong shape — the message rides in on a rejected field."""
    result = _invoke(
        {"session_id": "short", "message": PERSONAL_MESSAGE, "nonsense": True}
    )
    assert result["statusCode"] == 400


def _turn_unparseable_body(monkeypatch) -> None:
    result = _invoke(f"this is not json at all: {PERSONAL_MESSAGE}")
    assert result["statusCode"] == 400


def _turn_idempotent_replay(monkeypatch) -> None:
    body = _meal_plan_body()
    _invoke(body)
    replay = _invoke(body)
    assert replay["headers"]["X-Idempotent-Replay"] == "true"


def _turn_id_reused(monkeypatch) -> None:
    first = _meal_plan_body()
    _invoke(first)
    result = _invoke({**first, "message": f"{PERSONAL_MESSAGE} plus extra halloumi"})
    assert result["statusCode"] == 400


def _turn_in_flight(monkeypatch) -> None:
    from src.handler import _idempotency_store
    from src.store.idempotency import fingerprint, make_key

    body = _meal_plan_body()
    raw = json.dumps(body)
    _idempotency_store().acquire(
        make_key(body["session_id"], body["turn_id"]), fingerprint(raw)
    )
    assert _invoke(raw)["statusCode"] == 409


TURNS = {
    "budget_infeasible": _turn_budget_infeasible,
    "guardrail_blocked": _turn_guardrail_blocked,
    "handler_escaped": _turn_handler_escaped,
    "idempotent_replay": _turn_idempotent_replay,
    "in_flight": _turn_in_flight,
    "invalid_request": _turn_invalid_request,
    "meal_plan": _turn_meal_plan,
    "model_error": _turn_model_error,
    "no_data": _turn_no_data,
    "out_of_scope": _turn_out_of_scope,
    "price_check": _turn_price_check,
    "turn_id_reused": _turn_id_reused,
    "unhandled_exception": _turn_unhandled_exception,
    "unparseable_body": _turn_unparseable_body,
}


@pytest.mark.parametrize("turn", sorted(TURNS))
def test_no_personal_information_reaches_any_log_sink(turn, every_sink, monkeypatch):
    """
    THE REGRESSION TEST FOR THE LEAK.

    Every branch the handler and the graph can take, run for real, with the
    message, the location and the dietary exclusions all present in the
    request — and then every sink the invocation could have written to is
    searched: stdout, stderr, the Powertools stream and stdlib logging, at
    DEBUG.

    Reading the log calls is what let the last one through. This does not
    read them. It asserts on what came out.
    """
    TURNS[turn](monkeypatch)

    written = every_sink()
    assert written.strip(), "the turn wrote nothing at all, so the scan proves nothing"

    lowered = written.lower()
    leaked = sorted(
        {term for term in (*FORBIDDEN, *FORBIDDEN_KEYS) if term.lower() in lowered}
    )
    if leaked:
        offending = [
            line
            for line in written.splitlines()
            if any(term.lower() in line.lower() for term in leaked)
        ]
        pytest.fail(
            f"personal information reached the logs on the {turn} path: {leaked}\n"
            + "\n".join(offending[:10])
        )


def test_the_leak_scan_can_actually_see_a_leak(every_sink, monkeypatch):
    """
    The scan above is only worth its runtime if it fails when it should, and
    each sink is a separate way for it to stop looking — the original leak was
    invisible to a stdout-only reader. So write the message to all four and
    require every one of them to be caught.

    Without this, a refactor that quietly drops a sink turns the whole
    parametrised suite green and nobody finds out until the next incident.
    """
    import sys

    from src.observability.powertools import logger as powertools_logger

    sinks = {
        "stdout": lambda: print(PERSONAL_MESSAGE),
        "stderr": lambda: sys.stderr.write(PERSONAL_MESSAGE + "\n"),
        "powertools": lambda: powertools_logger.info(
            "careless", extra={"m": PERSONAL_MESSAGE}
        ),
        # The one available to a graph node, which cannot import Powertools.
        "stdlib_logging": lambda: logging.getLogger("src.graph.nodes.plan").debug(
            "planning for %s", PERSONAL_MESSAGE
        ),
        "hint_keys": lambda: powertools_logger.info(
            "careless", extra={"hints": ["household_size", "dietary_exclusions"]}
        ),
    }

    for name, leak in sinks.items():
        every_sink()  # discard anything buffered from the previous sink
        leak()
        lowered = every_sink().lower()
        assert any(
            term.lower() in lowered for term in (*FORBIDDEN, *FORBIDDEN_KEYS)
        ), f"a leak written to {name} was invisible to the scan"


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

    # Needs a turn that actually reaches generation: the default body is
    # below the feasibility floor and is refused before any model call.
    lambda_handler(_event(_affordable_meal_plan_body()))
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

    lambda_handler(_event(_repairable_body()))
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

    # Needs a turn that actually reaches generation: the default body is
    # below the feasibility floor and is refused before any model call.
    lambda_handler(_event(_affordable_meal_plan_body()))
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


def test_model_latency_is_dimensioned_by_model_and_task(captured, never_affordable):
    from src.handler import lambda_handler

    lambda_handler(_event(_repairable_body()))
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

    lambda_handler(_event(_repairable_body()))
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

    result = lambda_handler(_event(_affordable_meal_plan_body()))
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
    from src.models.base import GuardrailBlocked

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
