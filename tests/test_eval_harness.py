"""
Eval harness guards.

The eval harness is measurement equipment, and equipment that reports a
plausible number when it is not actually measuring anything is worse than
equipment that reports nothing. This is not hypothetical: a run of
`run_meal_plan.py --compare` once returned a tidy 27% for two different
models because BEDROCK_GUARDRAIL_ID was unset and every model call raised
before reaching Bedrock. Two identical scores from two providers is the
signature of a harness fault, and the harness said nothing.

These tests pin the guards that make that state loud instead of plausible.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.run_meal_plan import (
    CaseResult,
    Scorecard,
    UpstreamOutage,
    _gate,
    assert_measured,
)


def _upstream(case_id: str, code: str = "INTERNAL_ERROR") -> CaseResult:
    return CaseResult(
        case_id, False, [f"no plan produced (error={code})"], error_code=code
    )


def _quality_failure(case_id: str) -> CaseResult:
    """A real failure: the model answered and the answer broke an invariant."""
    return CaseResult(case_id, False, ["over budget: $41.20 > $30"])


def _pass(case_id: str) -> CaseResult:
    return CaseResult(case_id, True)


# --------------------------------------------------- classifying failures


@pytest.mark.parametrize("code", ["INTERNAL_ERROR", "UPSTREAM_TIMEOUT", "RATE_LIMITED"])
def test_upstream_error_codes_are_not_quality_failures(code):
    assert _upstream("plan-001", code).is_upstream_failure


@pytest.mark.parametrize(
    "exc",
    [
        "raised ReadTimeoutError: Read timeout on endpoint URL",
        "raised ConnectTimeoutError: Connect timeout",
        "raised EndpointConnectionError: Could not connect",
        # The one that actually bit: an expired SSO login mid-run was reported
        # as three models scoring 0% rather than as a run that never happened.
        "raised UnauthorizedSSOTokenError: The SSO session has expired",
        "raised NoCredentialsError: Unable to locate credentials",
        "raised ThrottlingException: Rate exceeded",
    ],
)
def test_any_escaped_exception_counts_as_upstream(exc):
    """
    Exceptions are caught per case, so they carry no terminal error code — and
    an exception is never a judgement about plan quality, whatever its type.
    Enumerating known network errors left the guard one cause behind reality.
    """
    assert CaseResult("plan-001", False, [exc]).is_upstream_failure


def test_budget_infeasible_is_a_real_result_not_an_outage():
    """A genuinely unaffordable basket is the model answering correctly."""
    assert not _upstream("plan-001", "BUDGET_INFEASIBLE").is_upstream_failure


def test_an_over_budget_plan_is_a_quality_failure():
    assert not _quality_failure("plan-001").is_upstream_failure


# --------------------------------------------------- refusing to report


def test_total_outage_raises_instead_of_reporting_a_pass_rate():
    card = Scorecard("Claude Sonnet 4.5", [_upstream(f"plan-{i:03d}") for i in range(11)])
    assert card.pass_rate == 0.0  # a number it would once have printed
    with pytest.raises(UpstreamOutage):
        assert_measured(card)


def test_the_outage_message_names_where_to_look():
    card = Scorecard("Claude Sonnet 4.5", [_upstream("plan-001")])
    with pytest.raises(UpstreamOutage, match="BEDROCK_GUARDRAIL_ID"):
        assert_measured(card)


def test_a_working_run_is_not_blocked():
    card = Scorecard("Nova Pro", [_pass("plan-001"), _quality_failure("plan-002")])
    assert_measured(card)  # must not raise


def test_one_answered_case_is_enough_to_report():
    """The hard abort is for 'measured nothing', not 'measured badly'."""
    card = Scorecard("Nova Pro", [_upstream("plan-001"), _quality_failure("plan-002")])
    assert_measured(card)


# --------------------------------------------------- the CI gate


def test_gate_is_inconclusive_when_any_case_failed_upstream():
    """
    In CI the retry is automatic, so a gate that fails on infrastructure gets
    re-run until it passes — and a gate that passes on a partial run passes on
    fewer cases than the suite contains. Neither is about the code.
    """
    card = Scorecard("Nova Pro", [_pass("plan-001"), _upstream("plan-002")])
    assert _gate(card.pass_rate, 0.4, card) == 2


def test_gate_still_fails_a_genuinely_bad_run():
    card = Scorecard("Nova Pro", [_pass("plan-001"), _quality_failure("plan-002")])
    assert _gate(card.pass_rate, 0.9, card) == 1


def test_gate_still_passes_a_genuinely_good_run():
    card = Scorecard("Nova Pro", [_pass("plan-001"), _pass("plan-002")])
    assert _gate(card.pass_rate, 0.9, card) == 0


def test_gate_is_inert_without_a_floor():
    card = Scorecard("Nova Pro", [_upstream("plan-001")])
    assert _gate(card.pass_rate, None, card) == 0
