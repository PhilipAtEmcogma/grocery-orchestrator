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


# ------------------------------------------- the dietary check must be able to fail
#
# _category_of read `citation.source.pk.split("#")[-1]` on the stated
# assumption that pk is '<store>#<category>'. retrieve_prices sets pk to the
# record's store_key, which is '<store>#<location>' -- so it returned
# 'sylvia-park' where 'dairy' was expected, and the exclusion check compared
# store locations against category names. Those sets never intersect, so the
# safety invariant could not fail: a plan serving beef to someone who asked
# for vegetarian passed. A check that cannot fail is not a check, and the only
# test that proves one works is one that makes it fail.

from datetime import date  # noqa: E402
from decimal import Decimal  # noqa: E402

from evals.run_meal_plan import _category_of, _check_invariants  # noqa: E402
from src.retrieval.memory import InMemoryPriceRepository  # noqa: E402
from src.schemas.contract import (  # noqa: E402
    Citation,
    Ingredient,
    Meal,
    MealPlan,
    SourceRef,
    Store,
    StoreBasket,
)


def _cited(ref: str, product_key: str) -> Citation:
    return Citation(
        ref=ref,
        store=Store.PAKNSAVE,
        store_location="Sylvia Park",
        product_name="Beef Mince 1kg",
        price_nzd=Decimal("12.00"),
        unit="1kg",
        on_special=False,
        valid_date=date(2026, 7, 31),
        source=SourceRef(
            table="grocery-products-dev",
            # The shape that caused the bug: '<store>#<location>', NOT category.
            pk="paknsave#sylvia-park",
            sk=product_key,
        ),
    )


def _plan_using(ref: str) -> MealPlan:
    return MealPlan(
        household_size=2, days=1,
        budget_nzd=Decimal("50"), total_nzd=Decimal("12.00"),
        within_budget=True, repair_attempts=0,
        meals=[Meal(
            name="Beef pasta", serves=2,
            ingredients=[Ingredient(
                item="beef mince", qty="1kg",
                citation_ref=ref, line_cost_nzd=Decimal("12.00"),
            )],
            subtotal_nzd=Decimal("12.00"),
        )],
        baskets=[StoreBasket(
            store=Store.PAKNSAVE, store_location="Sylvia Park",
            citation_refs=[ref], basket_total_nzd=Decimal("12.00"),
        )],
        dietary_exclusions_applied=["vegetarian"],
    )


def test_category_lookup_does_not_return_a_store_location():
    """The exact defect: 'sylvia-park' where a category was expected."""
    citation = _cited("c1", "beef-mince-1kg")
    assert _category_of(citation, {"beef-mince-1kg": "meat"}) == "meat"


def test_meat_in_a_vegetarian_plan_is_reported_as_a_violation():
    violations = _check_invariants(
        {
            "id": "synthetic",
            "hints": {"household_size": 2},
            "expect": {"exclude_categories": ["vegetarian"]},
        },
        _plan_using("c1"),
        None,
        {"c1": _cited("c1", "beef-mince-1kg")},
        {"beef-mince-1kg": "meat"},
    )
    assert any("violates" in v for v in violations), (
        "a vegetarian plan built from beef mince must fail the dietary "
        "invariant; before the fix this returned no violations at all"
    )


def test_a_compliant_vegetarian_plan_still_passes():
    """The check must fail on violations without failing on everything."""
    violations = _check_invariants(
        {
            "id": "synthetic",
            "hints": {"household_size": 2},
            "expect": {"exclude_categories": ["vegetarian"]},
        },
        _plan_using("c1"),
        None,
        {"c1": _cited("c1", "lentils-dried-500g")},
        {"lentils-dried-500g": "pantry"},
    )
    assert not any("violates" in v for v in violations)


def test_every_fixture_product_resolves_to_a_category():
    """A silently empty lookup would make the check vacuous a second way."""
    repo = InMemoryPriceRepository()
    categories = {r.product_key: r.category for r in repo.all_records}
    assert categories
    assert all(c for c in categories.values())
