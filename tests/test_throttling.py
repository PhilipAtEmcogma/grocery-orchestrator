"""
A throttle is a distinct failure, and it is now visible as one.

WHAT WAS WRONG. `BedrockModelClient._converse` caught every `ClientError` and
raised one opaque `ModelError("Bedrock call failed: ...")`. Two very different
incidents therefore produced the same signal: "Bedrock is down" and "we asked
faster than our quota allows". The first is escalated to AWS; the second is
answered by pacing, a quota increase, or routing to a second model. Nothing in
CloudWatch could tell an operator which one was happening.

`config/alarms.json` recorded throttling and stale data together as "neither has
a metric yet". That was true of throttling. It was NOT true of stale data --
`TurnError` has carried a `code` dimension since 2026-08-30, so `STALE_DATA` was
already published and only the alarm was missing. The grouped note is why nobody
checked the second half; the stale-data half of this work was one alarm.

THE TWO HALVES TESTED HERE:

  1. `_converse` maps a rate refusal to `ModelThrottled` and leaves every other
     `ClientError` a plain `ModelError`.
  2. `InstrumentedModelClient` counts `ModelThrottled` with the same `model` and
     `task` dimensions the latency metric uses, and counts nothing on a call
     that succeeded or failed some other way.

Both halves matter and neither implies the other: a correct classification that
nothing counts is invisible, and a counter fed by a classification that never
fires reads as a healthy service.
"""

from __future__ import annotations

from contextlib import AbstractContextManager

import pytest
from botocore.exceptions import ClientError

from src.models.base import ModelError, ModelThrottled, ModelTier
from src.observability import NULL_TELEMETRY, InstrumentedModelClient, TurnStats
from src.observability.base import METRIC_MODEL_LATENCY, METRIC_MODEL_THROTTLED, Span

# --------------------------------------------------------------------------
# doubles
# --------------------------------------------------------------------------


class _RecordingTelemetry:
    """
    Counts and durations with their dimensions, and no vendor.

    The signatures below match `Telemetry` EXACTLY rather than loosely. A
    double typed `**annotations: object` does not satisfy the protocol, and
    pyright says so -- which is the whole reason the protocol is written down:
    `Telemetry.span`'s return type was wrong once already, and both real
    implementations silently failed to satisfy it.
    """

    def __init__(self) -> None:
        self.counts: list[tuple[str, float, dict]] = []
        self.durations: list[tuple[str, float, dict]] = []

    def span(
        self, name: str, **annotations: str | int | float | bool
    ) -> AbstractContextManager[Span]:
        return NULL_TELEMETRY.span(name, **annotations)

    def count(self, name: str, value: float = 1.0, **dimensions: str) -> None:
        self.counts.append((name, value, dimensions))

    # `milliseconds`, not `value`. The protocol names it that, and a keyword
    # argument's NAME is part of the signature -- pyright rejects the double
    # over the parameter name alone, which is correct: a caller passing
    # `milliseconds=` would fail against a double that called it `value`.
    def duration(self, name: str, milliseconds: float, **dimensions: str) -> None:
        self.durations.append((name, milliseconds, dimensions))

    def named(self, metric: str) -> list[tuple[str, float, dict]]:
        return [record for record in self.counts if record[0] == metric]


class _Inner:
    """A model client that fails, or does not, on demand."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.last_usage: dict = {}

    def structured(self, *, system, user, schema, tier, max_tokens=1024, task="classify_intent"):
        self.last_usage = {"model_key": "nova-lite", "input_tokens": 10, "output_tokens": 5}
        if self._error is not None:
            raise self._error
        return schema()

    def text(self, *, system, user, tier, max_tokens=1024, task="generate_prose"):
        self.last_usage = {"model_key": "nova-lite", "input_tokens": 10, "output_tokens": 5}
        if self._error is not None:
            raise self._error
        return "ok"


def _client_error(code: str, status: int = 400) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "synthetic"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "Converse",
    )


def _bedrock_client(raises: Exception):
    """
    A BedrockModelClient whose `converse` raises, with no AWS anywhere.

    Returns the spec alongside the client. `client._pinned` is typed
    `ModelSpec | None`, so reading it back at each call site would make every
    test carry a narrowing assertion about a value this helper just chose.
    """
    from src.models.bedrock import BedrockModelClient
    from src.models.registry import ModelRegistry

    registry = ModelRegistry()
    spec = registry.get("nova-lite")
    client = BedrockModelClient.__new__(BedrockModelClient)
    client._registry = registry
    client._pinned = spec
    client._usage = {}

    class _Stub:
        def converse(self, **kwargs):
            raise raises

    client._client = _Stub()
    return client, spec


# --------------------------------------------------------------------------
# half one: the classification
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    ["ThrottlingException", "TooManyRequestsException", "ThrottledException"],
    ids=["standard", "bedrock-on-demand", "sdk-variant"],
)
def test_every_throttle_name_aws_uses_is_recognised(code, monkeypatch):
    """
    Three names, because AWS uses three and they are not interchangeable.

    Matching only `ThrottlingException` would leave the metric reading zero
    during precisely the incident it exists to describe, on the on-demand
    path this service actually runs.
    """
    monkeypatch.delenv("BEDROCK_GUARDRAIL_ID", raising=False)
    monkeypatch.setenv("REQUIRE_GUARDRAIL", "0")
    client, spec = _bedrock_client(_client_error(code))

    with pytest.raises(ModelThrottled):
        client._converse(system="s", user="u", spec=spec, max_tokens=64)


def test_an_unknown_throttle_is_still_a_throttle_on_the_status_code(monkeypatch):
    """
    429 means the same thing whatever the body calls it.

    The code list is a set of names AWS can add to. Falling back to the HTTP
    status means a throttle AWS renames tomorrow is still counted as a
    throttle rather than silently re-labelled an outage -- which is the
    direction that matters, because an outage pages someone.
    """
    monkeypatch.delenv("BEDROCK_GUARDRAIL_ID", raising=False)
    monkeypatch.setenv("REQUIRE_GUARDRAIL", "0")
    client, spec = _bedrock_client(_client_error("SomeFutureRateException", status=429))

    with pytest.raises(ModelThrottled):
        client._converse(system="s", user="u", spec=spec, max_tokens=64)


@pytest.mark.parametrize(
    "code",
    ["ValidationException", "AccessDeniedException", "ServiceQuotaExceededException"],
    ids=["invalid-request", "permissions", "hard-quota"],
)
def test_other_client_errors_stay_plain_model_errors(code, monkeypatch):
    """
    The classification has to be narrow or it is not a classification.

    `ServiceQuotaExceededException` is in this list deliberately. It is a hard
    account limit rather than a rate: it is not fixed by pacing, and counting
    it as a throttle would put a ticket-to-AWS problem on a graph that says
    "slow down".
    """
    monkeypatch.delenv("BEDROCK_GUARDRAIL_ID", raising=False)
    monkeypatch.setenv("REQUIRE_GUARDRAIL", "0")
    client, spec = _bedrock_client(_client_error(code))

    with pytest.raises(ModelError) as raised:
        client._converse(system="s", user="u", spec=spec, max_tokens=64)
    assert not isinstance(raised.value, ModelThrottled), (
        f"{code} was classified as a throttle. It is not a rate refusal, and "
        f"reporting it as one sends the operator to the wrong remedy."
    )


def test_a_throttle_is_still_a_model_error_so_nothing_downstream_changes():
    """
    The subclass must not become a new failure mode the edges do not catch.

    `src/handler.py` and the graph both handle `ModelError`. If `ModelThrottled`
    were a sibling rather than a subclass, a throttle would escape the error
    boundary and become the 500 the contract invariant exists to prevent.
    """
    assert issubclass(ModelThrottled, ModelError)


# --------------------------------------------------------------------------
# half two: the counting
# --------------------------------------------------------------------------


def test_a_throttled_call_is_counted_with_its_model_and_task():
    telemetry = _RecordingTelemetry()
    wrapped = InstrumentedModelClient(_Inner(ModelThrottled("quota")), telemetry, TurnStats())

    with pytest.raises(ModelThrottled):
        wrapped.text(system="s", user="u", tier=ModelTier.FAST, task="generate_prose")

    counted = telemetry.named(METRIC_MODEL_THROTTLED)
    assert len(counted) == 1, "a throttled call must be counted exactly once"

    _, value, dimensions = counted[0]
    assert value == 1
    assert dimensions == {"model": "nova-lite", "task": "generate_prose"}, (
        "the throttle count must carry the same dimensions as the latency "
        "metric, so a throttle rate is readable against the latency of the "
        "same model and task"
    )


def test_the_throttled_call_still_reports_its_latency():
    """
    A throttle that loses its latency datapoint takes the turn's timing with it.

    The count is emitted in the same `finally` as the span and the duration, so
    this asserts the addition did not move the existing accounting.
    """
    telemetry = _RecordingTelemetry()
    wrapped = InstrumentedModelClient(_Inner(ModelThrottled("quota")), telemetry, TurnStats())

    with pytest.raises(ModelThrottled):
        wrapped.text(system="s", user="u", tier=ModelTier.FAST, task="generate_prose")

    assert [name for name, _, _ in telemetry.durations] == [METRIC_MODEL_LATENCY]


@pytest.mark.parametrize(
    "error",
    [None, ModelError("upstream is down")],
    ids=["successful-call", "ordinary-failure"],
)
def test_nothing_else_is_counted_as_a_throttle(error):
    """
    The metric under-reporting is bad; over-reporting makes the alarm useless.

    A metric that counts every failure would breach a threshold of five during
    any outage, and the operator would learn to read "throttled" as "something
    is wrong" -- which is the state this change exists to leave.
    """
    telemetry = _RecordingTelemetry()
    wrapped = InstrumentedModelClient(_Inner(error), telemetry, TurnStats())

    def call():
        return wrapped.text(system="s", user="u", tier=ModelTier.FAST, task="generate_prose")

    if error is None:
        assert call() == "ok"
    else:
        with pytest.raises(ModelError):
            call()

    assert not telemetry.named(METRIC_MODEL_THROTTLED)


# --------------------------------------------------------------------------
# the alarms these metrics exist for
# --------------------------------------------------------------------------


def test_both_alarms_that_were_deferred_for_want_of_a_metric_now_exist():
    """
    Task 12 left throttling and stale-data alarms out, for want of metrics.

    This is the control on that entry being discharged rather than reworded.
    `tests/test_alarms.py` already proves every alarm binds to a metric
    something publishes; this proves these two specific ones are present, so
    deleting either fails a test that says why it mattered.
    """
    from scripts.apply_alarms import CONFIG, load_config

    names = {alarm["name"] for alarm in load_config(CONFIG)["alarms"]}
    assert "grocery-orchestrator-model-throttled-dev" in names
    assert "grocery-orchestrator-stale-data-dev" in names


def test_the_stale_data_alarm_is_dimensioned_on_the_code():
    """
    Undimensioned, it would fire on every honest refusal the service makes.

    `TurnError` is emitted for NO_DATA and BUDGET_INFEASIBLE too, both of which
    are correct answers at healthy volume. The same argument as the
    internal-error alarm, which `tests/test_alarms.py` already holds.
    """
    from scripts.apply_alarms import CONFIG, load_config

    alarm = next(
        a
        for a in load_config(CONFIG)["alarms"]
        if a["name"] == "grocery-orchestrator-stale-data-dev"
    )
    assert alarm["dimensions"].get("code") == "STALE_DATA"


def test_the_throttle_alarm_watches_the_total_rather_than_one_model():
    """
    A quota is shared, so binding the alarm to one model leaves the rest unwatched.

    The dimensions exist for diagnosis in the console. The alarm deliberately
    takes the undimensioned total -- the opposite choice from the stale-data
    alarm above, and for the opposite reason, which is worth a test each so
    neither gets "fixed" into the other.
    """
    from scripts.apply_alarms import CONFIG, load_config

    alarm = next(
        a
        for a in load_config(CONFIG)["alarms"]
        if a["name"] == "grocery-orchestrator-model-throttled-dev"
    )
    assert alarm["dimensions"] == {}
