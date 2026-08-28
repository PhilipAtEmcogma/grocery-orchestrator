r"""
DEMO 2 - Meal planning, the repair loop, and dietary safety
===========================================================

HOW TO RUN
----------
    python Philip_demo/02_meal_planning.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/02_meal_planning.py

Offline. No AWS account or credentials needed.

WHAT THIS DEMONSTRATES
----------------------
  1. A budgeted meal plan for a household over several days
  2. Where every monetary value comes from (Python, never the model)
  3. The repair loop: an over-budget first draft is fed back and retried
  4. Dietary exclusions enforced against RETRIEVED PRODUCTS, not model claims
  5. Fail-closed refusal for an exclusion we cannot honour
  6. Store baskets: what you buy at each shop

WHY THE REPAIR LOOP IS THE INTERESTING PART
-------------------------------------------
It is the cycle a straight-line Lambda cannot express naturally, and it is
why LangGraph earns its place in this project. generate -> validate -> repair
-> generate, bounded by MAX_REPAIR_ATTEMPTS so it cannot run away.
"""

from __future__ import annotations

from decimal import Decimal

from _demo_support import citations, heading, request, section, show_events

from src.graph.dietary import SUPPORTED_EXCLUSIONS, supported_terms
from src.graph.state import MAX_REPAIR_ATTEMPTS
from src.models.scripted import ScriptedModelClient
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import ErrorCode, assert_arithmetic

repo = InMemoryPriceRepository()
model = ScriptedModelClient()

heading("DEMO 2 - Meal planning, the repair loop, and dietary safety")

# ------------------------------------------------------------------ a plan
section("1. A budgeted plan")
print("User: 'feed a flat of 3 for under $60 this week'\n")
resp = run_turn(
    request(
        "feed a flat of 3 for under $60 this week",
        household_size=3, budget_nzd=60, days=5,
    ),
    repo, model,
)
show_events(resp, skip=("session", "citation", "token"))

plan_event = next((e for e in resp.events if e.type == "meal_plan"), None)
if plan_event:
    plan = plan_event.data
    index = citations(resp)
    print(f"\n  {len(plan.meals)} meals for {plan.household_size} people "
          f"over {plan.days} days")
    print(f"  Total ${plan.total_nzd} of ${plan.budget_nzd} budget "
          f"({plan.total_nzd / plan.budget_nzd:.0%} used)\n")

    for meal in plan.meals:
        print(f"    {meal.name}  (serves {meal.serves})  ${meal.subtotal_nzd}")
        for ing in meal.ingredients:
            c = index[ing.citation_ref]
            print(f"        {ing.qty:<8} {ing.item:<22} ${ing.line_cost_nzd:>6}  "
                  f"[{ing.citation_ref}] {c.product_name}")

    # ------------------------------------------------------ the arithmetic
    section("2. Every number above was computed in Python")
    print("  The model chose citation refs and pack multipliers. It never")
    print("  produced a price: PlanDraft has no field it could put one in.")
    print("  assemble_plan() multiplies the CITED price by the pack count.\n")
    assert_arithmetic(plan)
    print("  assert_arithmetic() passed - line costs sum to subtotals, and")
    print("  subtotals sum to the total. Verified, not trusted.")

    # --------------------------------------------------------- the baskets
    section("3. What you actually buy, per store")
    for basket in plan.baskets:
        print(f"  {basket.store.value} {basket.store_location}: "
              f"${basket.basket_total_nzd} across "
              f"{len(basket.citation_refs)} products")
    print("\n  A pack is counted ONCE even when used across several meals,")
    print("  which is what makes ingredient reuse actually save money rather")
    print("  than merely look cheap.")

# ------------------------------------------------------------ repair loop
section("4. The repair loop")
print("A scripted model is forced to draft an over-budget plan (plan_packs=5),")
print("so the cycle is observable without needing a real model to misbehave.\n")
print(f"  MAX_REPAIR_ATTEMPTS = {MAX_REPAIR_ATTEMPTS}  (bounded: an unbounded")
print("  repair loop is a runaway cost and latency risk, not just a")
print("  correctness one)\n")

over_budget_model = ScriptedModelClient(plan_packs=Decimal("5"))
resp = run_turn(
    request("plan dinners", turn="turn-demo05", household_size=2, budget_nzd=40, days=3),
    repo, over_budget_model,
)
plan_event = next((e for e in resp.events if e.type == "meal_plan"), None)
if plan_event:
    print(f"  Delivered after {plan_event.data.repair_attempts} repair attempt(s): "
          f"${plan_event.data.total_nzd} of ${plan_event.data.budget_nzd}")
else:
    err = next(e for e in resp.events if e.type == "error")
    print(f"  Repair exhausted -> {err.code.value}")
    print(f"  {err.message}")
    print("\n  Note the failing draft is NOT also emitted. Showing an")
    print("  over-budget shopping list beside 'I could not make one' would")
    print("  be incoherent.")

# ------------------------------------------------------- dietary exclusions
section("5. Dietary exclusions, checked against retrieved products")
print("User: 'vegetarian meal plan for 2, $50' \n")
resp = run_turn(
    request(
        "vegetarian meal plan for 2",
        turn="turn-demo06",
        household_size=2, budget_nzd=50, days=3,
        dietary_exclusions=["vegetarian"],
    ),
    repo, model,
)
plan_event = next((e for e in resp.events if e.type == "meal_plan"), None)
if plan_event:
    index = citations(resp)
    banned = SUPPORTED_EXCLUSIONS["vegetarian"]
    print(f"  'vegetarian' rules out categories: {sorted(banned)}")

    # A Citation carries no `category` -- it is a wire type for the frontend,
    # which has no use for one -- so the category is looked up from the
    # repository by the product key the citation already carries in source.sk.
    #
    # Worth being exact about, because the eval harness got this wrong: it read
    # source.pk.split("#")[-1] believing pk was '<store>#<category>'. It is
    # '<store>#<location>', so that check compared store locations against
    # category names, never matched, and the dietary invariant could not fail.
    category_by_product = {r.product_key: r.category for r in repo.all_records}
    used_categories = {
        category_by_product[index[i.citation_ref].source.sk]
        for m in plan_event.data.meals for i in m.ingredients
    }
    print(f"  Categories actually used: {sorted(used_categories)}")
    violations = used_categories & banned
    print(f"  Violations: {sorted(violations) if violations else 'none'}")
    print("\n  Checked against the retrieved records, not against what the")
    print("  model claims it applied. A model asserting compliance is not")
    print("  evidence of compliance.")

# ------------------------------------------------------------ fail closed
section("6. An exclusion we cannot honour is REFUSED, not guessed")
print("User: 'gluten-free meal plan'")
print("We have no per-product allergen tagging, so we cannot verify it.\n")
resp = run_turn(
    request(
        "gluten-free meal plan",
        turn="turn-demo07",
        household_size=2, budget_nzd=50, days=3,
        dietary_exclusions=["gluten-free"],
    ),
    repo, model,
)
err = next((e for e in resp.events if e.type == "error"), None)
if err:
    print(f"  {err.code.value}")
    print(f"  {err.message}\n")
print(f"  Terms we CAN honour: {supported_terms()}")
print("\n  Dropping a restriction is the dangerous direction of error, so the")
print("  refusal happens BEFORE retrieval and generation. We do not do the")
print("  work for a plan we could not safely verify.")

matched = err is not None and err.code == ErrorCode.UNSUPPORTED_EXCLUSION
print(f"\n  Refusal carried the expected contract code: {matched}")
print("\nDone.")
