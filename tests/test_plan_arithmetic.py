"""
Pilot Task 4b — every plan figure must follow from the cited prices.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.models.scripted import ScriptedModelClient
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import (
    ChatRequest,
    Citation,
    Ingredient,
    Meal,
    MealPlan,
    SourceRef,
    Store,
    StoreBasket,
    assert_arithmetic,
    assert_costed_from_citations,
)

# ================================ Pilot Task 4b: figures follow from citations
#
# `assert_arithmetic` checks that four sums agree WITH EACH OTHER: meals sum to
# the total, baskets sum to the payable. That is worth having and it is not
# enough. A line cost that is wrong by construction propagates consistently
# through all four and passes every one of them, and nothing checked a basket
# total against anything at all.
#
# The reuse/multipack case is why it matters, and it is the one the design
# document named as unproven. A product used 0.5 packs in one meal and 0.7 in
# another totals 1.2 — and you must buy TWO packs. Counting one pack per
# appearance, summing the fractions, or rounding per meal instead of once at the
# end each produce a plausible basket the old checks accepted. A draft using
# five packs of mince once reported a basket holding one, and a plan consuming
# $221 of food shipped against a $40 budget.


def _cit(ref: str, price: str, location: str = "Sylvia Park") -> Citation:
    return Citation(
        ref=ref,
        store=Store.PAKNSAVE,
        store_location=location,
        product_name=f"Product {ref}",
        price_nzd=Decimal(price),
        unit="1kg",
        on_special=False,
        valid_date=date(2026, 7, 31),
        source=SourceRef(table="grocery-products-dev", pk="paknsave#sylvia-park", sk=f"p-{ref}"),
    )


def _ing(ref: str, packs: str, line_cost: str) -> Ingredient:
    return Ingredient(
        item=f"item {ref}",
        qty="some",
        citation_ref=ref,
        packs=Decimal(packs),
        line_cost_nzd=Decimal(line_cost),
    )


def _plan(meals: list[Meal], baskets: list[StoreBasket], budget: str = "60") -> MealPlan:
    total = sum((m.subtotal_nzd for m in meals), Decimal(0))
    payable = sum((b.basket_total_nzd for b in baskets), Decimal(0))
    return MealPlan(
        household_size=2,
        days=2,
        budget_nzd=Decimal(budget),
        total_nzd=total,
        payable_total_nzd=payable,
        within_budget=payable <= Decimal(budget),
        repair_attempts=0,
        meals=meals,
        baskets=baskets,
        dietary_exclusions_applied=[],
    )


def _reuse_plan(basket_total: str) -> tuple[MealPlan, dict[str, Citation]]:
    """
    One $10 product used 0.5 packs in one meal and 0.7 in another.

    Consumption is $12.00; the shopper must buy TWO packs, so the basket is
    $20.00. Every wrong answer here is a plausible one.
    """
    citations = {"c1": _cit("c1", "10.00")}
    meals = [
        Meal(
            name="Monday",
            serves=2,
            ingredients=[_ing("c1", "0.5", "5.00")],
            subtotal_nzd=Decimal("5.00"),
        ),
        Meal(
            name="Tuesday",
            serves=2,
            ingredients=[_ing("c1", "0.7", "7.00")],
            subtotal_nzd=Decimal("7.00"),
        ),
    ]
    baskets = [
        StoreBasket(
            store=Store.PAKNSAVE,
            store_location="Sylvia Park",
            citation_refs=["c1"],
            basket_total_nzd=Decimal(basket_total),
        )
    ]
    return _plan(meals, baskets), citations


def test_a_correctly_costed_plan_passes():
    """The positive control. A rule that rejects everything certifies nothing."""
    plan, citations = _reuse_plan("20.00")
    assert_costed_from_citations(plan, citations)
    assert_arithmetic(plan)


@pytest.mark.parametrize(
    ("basket_total", "label"),
    [
        ("10.00", "one pack per product, however much is used"),
        ("12.00", "the fractional consumption figure, not whole packs"),
        ("30.00", "rounded up per meal instead of once at the end"),
    ],
)
def test_the_reuse_and_multipack_cases_are_caught(basket_total, label):
    """
    Each of these is a real way to get pack counting wrong, and each produces a
    plan whose four internal sums agree perfectly.
    """
    plan, citations = _reuse_plan(basket_total)

    assert_arithmetic(plan), "the old check cannot see this"
    with pytest.raises(AssertionError, match="whole packs at shelf price"):
        assert_costed_from_citations(plan, citations)


def test_a_line_cost_that_does_not_follow_from_the_price_is_caught():
    """
    Consistently wrong, so every sum still agrees. This is the case the sums
    were structurally unable to detect.
    """
    citations = {"c1": _cit("c1", "10.00")}
    meals = [
        Meal(
            name="Monday",
            serves=2,
            ingredients=[_ing("c1", "1", "3.00")],
            subtotal_nzd=Decimal("3.00"),
        )
    ]
    baskets = [
        StoreBasket(
            store=Store.PAKNSAVE,
            store_location="Sylvia Park",
            citation_refs=["c1"],
            basket_total_nzd=Decimal("10.00"),
        )
    ]
    plan = _plan(meals, baskets)

    assert_arithmetic(plan), "internally consistent, and wrong"
    with pytest.raises(AssertionError, match="line cost"):
        assert_costed_from_citations(plan, citations)


def test_a_basket_listing_a_product_the_meals_never_used_is_caught():
    plan, citations = _reuse_plan("20.00")
    plan.baskets[0].citation_refs = ["c1", "c2"]

    with pytest.raises(AssertionError, match="meals used"):
        assert_costed_from_citations(plan, citations)


def test_an_undeclared_citation_is_caught():
    plan, _ = _reuse_plan("20.00")

    with pytest.raises(AssertionError, match="not a declared citation"):
        assert_costed_from_citations(plan, {})


def test_packs_are_aggregated_across_meals_not_within_them():
    """
    The distinction the whole check exists for. Two meals each using 0.6 packs
    of the same product need ONE pack between them, not two: 1.2 rounds up to 2
    — but 0.6 rounded per meal would be 1 + 1 = 2 as well, so the discriminating
    case is 0.4 + 0.4 = 0.8, which is one pack aggregated and two per meal.
    """
    citations = {"c1": _cit("c1", "10.00")}
    meals = [
        Meal(
            name=f"Meal {i}",
            serves=2,
            ingredients=[_ing("c1", "0.4", "4.00")],
            subtotal_nzd=Decimal("4.00"),
        )
        for i in range(2)
    ]
    correct = StoreBasket(
        store=Store.PAKNSAVE,
        store_location="Sylvia Park",
        citation_refs=["c1"],
        basket_total_nzd=Decimal("10.00"),
    )
    per_meal = StoreBasket(
        store=Store.PAKNSAVE,
        store_location="Sylvia Park",
        citation_refs=["c1"],
        basket_total_nzd=Decimal("20.00"),
    )

    assert_costed_from_citations(_plan(meals, [correct]), citations)
    with pytest.raises(AssertionError, match="whole packs"):
        assert_costed_from_citations(_plan(meals, [per_meal]), citations)


def test_the_real_planner_satisfies_the_check():
    """
    End to end: whatever `assemble_plan` builds must survive its own audit, or
    the check is wrong rather than the planner.
    """
    request = ChatRequest(
        version="1.0",
        session_id="sess-arith01",
        turn_id="turn-arith01",
        message="feed a flat of 3 for under $90 this week, no seafood",
    )
    response = run_turn(request, InMemoryPriceRepository(), ScriptedModelClient())

    plans = [e.data for e in response.events if e.type == "meal_plan"]
    citations = {e.citation.ref: e.citation for e in response.events if e.type == "citation"}
    assert plans, "expected a plan for a feasible request"
    assert_costed_from_citations(plans[0], citations)
