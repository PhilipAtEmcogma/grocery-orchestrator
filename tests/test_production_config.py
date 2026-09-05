"""
Req 12.5 — a production stage fails startup rather than serving a demo.

The failure this guards is invisible by construction. `_dependencies()` selects
by environment, and every fallback is a working implementation: drop
`USE_DYNAMODB` and the service answers from 26 fixture products, drop
`USE_BEDROCK` and a rule-based stand-in writes the plans. Grounding, arithmetic
and dietary checks all still pass, because they check internal consistency and
the fixtures are internally consistent. Nothing looks wrong.

These tests are written against `assert_production_configuration` directly with
an injected environment, so they never touch `os.environ` and cannot leak state
between cases.
"""

from __future__ import annotations

import pytest

from src.handler import (
    PRODUCTION_STAGES,
    STAGE_ENV,
    ConfigurationError,
    assert_production_configuration,
)

COMPLETE = {
    STAGE_ENV: "prod",
    "USE_DYNAMODB": "1",
    "USE_BEDROCK": "1",
    "BEDROCK_GUARDRAIL_ID": "b1xezpqe04kx",
    "BEDROCK_GUARDRAIL_VERSION": "2",
    "CORS_ORIGIN": "https://example.cloudfront.net",
}


def test_a_complete_production_environment_passes() -> None:
    assert_production_configuration(dict(COMPLETE))


@pytest.mark.parametrize("stage", sorted(PRODUCTION_STAGES))
def test_every_production_stage_name_is_checked(stage: str) -> None:
    env = dict(COMPLETE, **{STAGE_ENV: stage})
    assert_production_configuration(env)  # complete: fine
    del env["USE_DYNAMODB"]
    with pytest.raises(ConfigurationError):
        assert_production_configuration(env)


@pytest.mark.parametrize(
    "missing",
    ["USE_DYNAMODB", "USE_BEDROCK", "BEDROCK_GUARDRAIL_ID", "BEDROCK_GUARDRAIL_VERSION"],
)
def test_each_missing_variable_fails_and_says_which(missing: str) -> None:
    """
    The message must name the variable.

    A startup failure that says only "misconfigured" sends the reader to the
    code; this one has to be actionable from a CloudWatch log line, because
    that is where it will be read.
    """
    env = dict(COMPLETE)
    del env[missing]
    with pytest.raises(ConfigurationError, match=missing):
        assert_production_configuration(env)


@pytest.mark.parametrize("value", ["0", "", "true", "yes"])
def test_a_flag_that_is_not_exactly_one_selects_the_demo_and_so_fails(value: str) -> None:
    """
    `_dependencies()` compares against the string "1" exactly.

    `USE_DYNAMODB=true` reads as enabled to a human and selects fixtures in
    code. That gap is the whole failure mode, so the check tests for the same
    exact value the selector does rather than for truthiness.
    """
    env = dict(COMPLETE, USE_DYNAMODB=value)
    with pytest.raises(ConfigurationError, match="USE_DYNAMODB"):
        assert_production_configuration(env)


def test_draft_guardrail_version_is_refused() -> None:
    """
    DRAFT moves. Evidence gathered against it describes whatever it was that day.

    `docs/ARCHITECTURE.md` records that DRAFT is deliberately not granted in the
    orchestrator's IAM policy either -- this is the same rule at the other end.
    """
    for value in ("DRAFT", "draft"):
        with pytest.raises(ConfigurationError, match="numbered version"):
            assert_production_configuration(dict(COMPLETE, BEDROCK_GUARDRAIL_VERSION=value))


def test_wildcard_cors_is_refused_in_production() -> None:
    with pytest.raises(ConfigurationError, match="wildcard CORS"):
        assert_production_configuration(dict(COMPLETE, CORS_ORIGIN="*"))


def test_the_message_lists_every_problem_not_just_the_first() -> None:
    """
    One deploy, one fix. Reporting only the first missing variable turns a
    misconfiguration into a sequence of failed deployments.
    """
    env = {STAGE_ENV: "prod"}
    with pytest.raises(ConfigurationError) as caught:
        assert_production_configuration(env)
    message = str(caught.value)
    for name in ("USE_DYNAMODB", "USE_BEDROCK", "BEDROCK_GUARDRAIL_ID", "CORS_ORIGIN"):
        assert name in message


@pytest.mark.parametrize("stage", ["", "dev", "local", "test", "staging"])
def test_non_production_stages_keep_their_fallbacks(stage: str) -> None:
    """
    The fallbacks are the reason the graph runs with no AWS account at all.

    Every test in this repository, both eval harnesses, the demos and the dev
    server depend on them, so the check must be silent outside production --
    and `staging` is deliberately NOT a production stage: it is where you
    exercise a partial configuration on purpose.
    """
    assert_production_configuration({STAGE_ENV: stage} if stage else {})


def test_an_unset_stage_is_not_production() -> None:
    """
    Fail-closed applies to the CONFIGURATION, not to the stage name.

    Defaulting an unset stage to production would break every offline test and
    every developer machine on the day it landed, which is a good way to have
    the check deleted. The deploy sets the stage; that is Pilot Task 10's job
    and is recorded in infra/docs/01 as part of the env-var contract.
    """
    assert_production_configuration({"USE_DYNAMODB": "0"})
