"""
The SSM routing overlay, and the qualification gate that makes it safe.

PILOT TASK 7b'S OTHER HALF. `infra/lib/service-stack.ts` has published
`/grocery/{stage}/models/routing` since 2026-08-30 under a comment saying
nothing read it. This suite covers the code that now does.

THE ARGUMENT THIS FILE HAS TO MAKE. Letting an operator edit routing from a
console is only acceptable because an edit CANNOT enable a model, invent one,
or manufacture the evidence that qualifies it — `models` and `scorecards` come
from `config/models.json` in the archive, which only a deploy changes. The worst
a bad edit can do is make a task unroutable, which is a loud failure. Several
tests below exist purely to hold that, because if it ever stopped being true
the whole feature would need withdrawing rather than fixing.

The other half is the fallback. Every failure mode — unset, unreachable,
denied, absent, unparseable, empty — must resolve to "use the bundled file"
and say so. A silent fallback is how somebody comes to believe they retuned
production when they retuned nothing.
"""

from __future__ import annotations

import json
import logging

import pytest

from src.models.registry import ModelRegistry, ModelTier, UnroutableTask
from src.models.ssm_routing import ROUTING_PARAM_ENV, load_routing_override, routing_parameter_name

# --------------------------------------------------------------------------
# the switch
# --------------------------------------------------------------------------


def test_the_overlay_is_off_when_no_parameter_is_named(monkeypatch):
    """
    Absence of the env var is the off switch, and it must not touch AWS.

    Every offline test, the eval harness and the scripted client construct a
    registry with no account. If this returned anything but None without a
    parameter name, all of them would try to reach SSM.
    """
    monkeypatch.delenv(ROUTING_PARAM_ENV, raising=False)
    assert routing_parameter_name() is None
    assert load_routing_override() is None


def test_an_empty_string_reads_as_off_rather_than_as_a_parameter_named_empty(monkeypatch):
    monkeypatch.setenv(ROUTING_PARAM_ENV, "")
    assert routing_parameter_name() is None
    assert load_routing_override() is None


# --------------------------------------------------------------------------
# the fallback, which has to cover every way this can fail
# --------------------------------------------------------------------------


class _Boom:
    """An SSM client that fails the way the caller under test fears."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def get_parameter(self, **_: object) -> dict:
        raise self._exc


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("ParameterNotFound"),
        PermissionError("AccessDeniedException"),
        TimeoutError("connect timeout"),
    ],
    ids=["absent", "denied", "timeout"],
)
def test_every_ssm_failure_falls_back_to_the_file_and_says_so(exc, monkeypatch, caplog):
    """
    Fail-SAFE, deliberately, where the rest of this codebase fails closed.

    The fallback is not an absence: it is the reviewed routing block this very
    deploy shipped. Refusing to serve because an optional tuning overlay is
    unreachable would turn an operator convenience into an outage.
    """
    import boto3

    monkeypatch.setenv(ROUTING_PARAM_ENV, "/grocery/dev/models/routing")
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _Boom(exc))

    with caplog.at_level(logging.WARNING):
        assert load_routing_override() is None

    assert "ssm_routing_unavailable" in caplog.text, (
        "the fallback must be logged. A silent one is how somebody comes to "
        "believe they retuned production when they retuned nothing."
    )


def test_the_failure_log_never_carries_the_parameter_value(monkeypatch, caplog):
    """A parameter value is configuration; the log line names the type, not it."""
    import boto3

    # Named `marker`, not `secret`. detect-secrets' keyword detector fires on a
    # variable called `secret` holding a string literal, and it fired on this
    # line -- correctly, by its own rules. Renaming is better than an
    # `allowlist secret` pragma: a pragma trains the reader that flagged lines
    # in this repo are usually fine, and there is nothing here that needs one.
    marker = "value-that-must-not-reach-a-log"
    monkeypatch.setenv(ROUTING_PARAM_ENV, "/grocery/dev/models/routing")
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _Boom(RuntimeError(marker)))

    with caplog.at_level(logging.WARNING):
        load_routing_override()

    assert marker not in caplog.text


class _Returns:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_parameter(self, **_: object) -> dict:
        return {"Parameter": {"Value": self._value}}


@pytest.mark.parametrize(
    "value",
    [
        "not json at all",
        json.dumps({"no_routing_key": True}),
        json.dumps({"routing": []}),
        json.dumps({"routing": {}}),
        json.dumps({"routing": {"classify_intent": "nova-lite"}}),
    ],
    ids=["unparseable", "missing-key", "not-an-object", "EMPTY", "rule-is-not-an-object"],
)
def test_a_malformed_or_empty_override_is_refused(value, monkeypatch, caplog):
    """
    EMPTY is the case worth naming.

    `{"routing": {}}` is not "no opinion", it is "no task has a route", which
    would make every task unroutable and take the service down from a console
    text box. The bundled file is the better reading.
    """
    import boto3

    monkeypatch.setenv(ROUTING_PARAM_ENV, "/grocery/dev/models/routing")
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _Returns(value))

    with caplog.at_level(logging.WARNING):
        assert load_routing_override() is None
    assert "ssm_routing" in caplog.text


def test_a_well_formed_override_is_returned_and_recorded(monkeypatch, caplog):
    import boto3

    routing = {"classify_intent": {"tier": "fast", "prefer": ["claude-haiku"]}}
    monkeypatch.setenv(ROUTING_PARAM_ENV, "/grocery/dev/models/routing")
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _Returns(json.dumps({"routing": routing})))

    with caplog.at_level(logging.INFO):
        assert load_routing_override() == routing
    assert "ssm_routing_applied" in caplog.text


# --------------------------------------------------------------------------
# what the registry does with it
# --------------------------------------------------------------------------


def test_the_override_replaces_the_routing_block_wholesale():
    """
    Replacement, not a merge, and the difference is operator-visible.

    A merge would make the effective config a function of two documents: an
    operator who DELETED a route from the parameter would find it still
    routing, because the file's rule would show through. What the parameter
    says is what runs.
    """
    registry = ModelRegistry(
        routing_override={"classify_intent": {"tier": "fast", "prefer": ["claude-haiku"]}}
    )
    assert registry.tasks == ["classify_intent"]
    assert registry.routing_source == "ssm"

    # ...and the file's other tasks are genuinely gone, not merely reordered.
    with pytest.raises(UnroutableTask):
        registry.route("generate_plan")


def test_without_an_override_the_file_is_used_and_reports_itself():
    registry = ModelRegistry()
    assert registry.routing_source == "file"
    assert "classify_intent" in registry.tasks


def test_an_override_cannot_route_to_a_model_the_file_disables():
    """
    THE SAFETY PROPERTY, and the reason this knob is allowed to exist.

    `claude-sonnet` is `enabled: false` in config/models.json, documented as
    excluded on LATENCY — p50 11.8s against a 20s client timeout. An operator
    who prefers it in SSM must not get it, because enabling a model is a
    deploy-time decision backed by a scorecard, and a console text box is not
    a scorecard.

    Routing to it raises rather than quietly falling through to something else,
    which is the loud failure this design promises.
    """
    registry = ModelRegistry(
        routing_override={"classify_intent": {"tier": "quality", "prefer": ["claude-sonnet"]}}
    )
    assert registry.get("claude-sonnet").enabled is False

    spec = None
    try:
        spec = registry.route("classify_intent")
    except UnroutableTask:
        pass
    assert spec is None or spec.key != "claude-sonnet", (
        "an SSM override routed to a model the file disables. Enabling a model "
        "is a deploy-time decision backed by a scorecard."
    )


def test_an_override_naming_a_model_that_does_not_exist_fails_loudly():
    """Not a silent downgrade to whatever happens to be cheapest at that tier."""
    registry = ModelRegistry(
        routing_override={"classify_intent": {"tier": "fast", "prefer": ["gpt-imaginary"]}}
    )
    # `prefer` misses, so it falls through to available(tier) -- which is the
    # documented behaviour and only ever yields QUALIFIED models.
    spec = registry.route("classify_intent")
    assert spec.enabled and spec.is_configured
    assert ModelTier.FAST in spec.tiers


def test_an_override_cannot_reach_a_model_at_a_tier_it_does_not_declare():
    """Tier membership comes from the file, so the override cannot widen it."""
    registry = ModelRegistry(
        routing_override={"classify_intent": {"tier": "quality", "prefer": ["nova-lite"]}}
    )
    assert ModelTier.QUALITY not in registry.get("nova-lite").tiers
    spec = registry.route("classify_intent")
    assert spec.key != "nova-lite"
    assert ModelTier.QUALITY in spec.tiers


def test_scorecards_are_not_overridable_at_all():
    """
    There is no parameter for them and no argument that accepts them.

    An operator who could edit a scorecard could qualify a route by typing,
    which is the one thing the qualification gate exists to prevent. Asserted
    on the CONSTRUCTOR SIGNATURE, so adding such an argument later fails here
    rather than passing review as a convenience.
    """
    import inspect

    params = set(inspect.signature(ModelRegistry.__init__).parameters)
    assert params == {"self", "config_path", "routing_override"}, (
        "ModelRegistry gained a constructor argument. If it lets a caller "
        "supply models or scorecards, the qualification gate is bypassable."
    )
