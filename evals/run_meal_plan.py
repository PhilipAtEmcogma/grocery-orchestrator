"""
Meal plan eval.

Different in kind from the intent eval. "Is this plan good?" has no single
correct answer, so the harness separates two things:

  INVARIANTS — pass/fail. Exclusions honoured, budget respected, every price
  grounded, serve counts matching. A plan violating one of these is wrong
  regardless of how appealing it reads.

  METRICS — reported, not scored. Budget utilisation, ingredient reuse, meal
  variety. These have no threshold that is right for every case, but they are
  how you tell an improvement from a regression across prompt changes.

The check most people miss: `within_budget` is ONE-SIDED. A plan costing $8 of
a $30 budget passes it and is probably not feeding three people for a week.
`min_budget_used` is the floor that catches under-feeding.

Run:
    python evals/run_meal_plan.py
    python evals/run_meal_plan.py --model claude-sonnet
    python evals/run_meal_plan.py --compare claude-sonnet nova-pro
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models.base import ModelClient
from src.models.registry import ModelSpec
from src.models.scripted import ScriptedModelClient
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import ChatRequest, ClientHints, MealPlan

CASES = Path(__file__).parent / "cases" / "meal_plan.json"

# Which fixture categories each exclusion term rules out.
EXCLUSION_CATEGORIES = {
    "seafood": {"seafood"},
    "vegetarian": {"meat", "seafood"},
    "dairy-free": {"dairy"},
}


@dataclass
class PlanMetrics:
    budget_used: float = 0.0
    distinct_meals: int = 0
    distinct_products: int = 0
    ingredient_lines: int = 0
    repair_attempts: int = 0

    @property
    def reuse_ratio(self) -> float:
        """
        Lines per distinct product. Above 1.0 means packs are shared across
        meals, which is how a plan actually saves money rather than merely
        looking cheap.
        """
        if not self.distinct_products:
            return 0.0
        return self.ingredient_lines / self.distinct_products


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    violations: list[str] = field(default_factory=list)
    metrics: PlanMetrics = field(default_factory=PlanMetrics)


@dataclass
class Scorecard:
    model_label: str
    results: list[CaseResult]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / len(self.results) if self.results else 0.0

    def mean(self, attr: str) -> float:
        values = [
            getattr(r.metrics, attr)
            for r in self.results
            if r.metrics.distinct_meals  # only cases that produced a plan
        ]
        return sum(values) / len(values) if values else 0.0


def _measure(plan: MealPlan, citations: dict) -> PlanMetrics:
    lines = [i for m in plan.meals for i in m.ingredients]
    return PlanMetrics(
        budget_used=float(plan.total_nzd / plan.budget_nzd) if plan.budget_nzd else 0.0,
        distinct_meals=len({m.name for m in plan.meals}),
        distinct_products=len({i.citation_ref for i in lines}),
        ingredient_lines=len(lines),
        repair_attempts=plan.repair_attempts,
    )


def _check_invariants(
    case: dict, plan: MealPlan | None, error_code: str | None, citations: dict
) -> list[str]:
    expect = case["expect"]
    v: list[str] = []

    if not expect.get("must_produce_plan", True):
        if plan is not None:
            v.append("produced a plan where it should have refused")
        wanted_code = expect.get("expect_error_code")
        if wanted_code and error_code != wanted_code:
            v.append(f"error code {error_code} != {wanted_code}")
        return v

    if plan is None:
        v.append(f"no plan produced (error={error_code})")
        return v

    # Hard budget ceiling.
    if plan.total_nzd > plan.budget_nzd:
        v.append(f"over budget: ${plan.total_nzd} > ${plan.budget_nzd}")
    if not plan.within_budget:
        v.append("within_budget flag is False on a delivered plan")

    # Budget floor. within_budget alone would let an $8 plan for $30 pass.
    floor = expect.get("min_budget_used")
    if floor is not None and plan.budget_nzd:
        used = float(plan.total_nzd / plan.budget_nzd)
        if used < floor:
            v.append(
                f"under-spends: {used:.0%} of budget, floor is {floor:.0%} "
                f"(${plan.total_nzd} of ${plan.budget_nzd})"
            )

    # Dietary exclusions. Checked against the actual retrieved products, not
    # against what the model claims it applied.
    banned: set[str] = set()
    for term in expect.get("exclude_categories", []):
        banned |= EXCLUSION_CATEGORIES.get(term, {term})
    if banned:
        for meal in plan.meals:
            for ing in meal.ingredients:
                citation = citations.get(ing.citation_ref)
                if citation and _category_of(citation) in banned:
                    v.append(
                        f"'{meal.name}' uses {citation.product_name}, "
                        f"which violates {sorted(banned)}"
                    )

    if expect.get("serves_matches_household"):
        household = case["hints"].get("household_size", 1)
        for meal in plan.meals:
            if meal.serves != household:
                v.append(f"'{meal.name}' serves {meal.serves}, household is {household}")

    minimum = expect.get("min_distinct_meals")
    if minimum is not None:
        distinct = len({m.name for m in plan.meals})
        if distinct < minimum:
            v.append(f"only {distinct} distinct meals, wanted {minimum}")

    return v


def _category_of(citation) -> str:
    """Fixture pk is '<store>#<category>'."""
    return citation.source.pk.split("#")[-1]


def run(model: ModelClient, label: str) -> Scorecard:
    repo = InMemoryPriceRepository()
    data = json.loads(CASES.read_text(encoding="utf-8"))
    results: list[CaseResult] = []

    for case in data["cases"]:
        hints = case.get("hints") or {}
        request = ChatRequest(
            session_id="sess-evalplan",
            turn_id=f"turn-{case['id']}",
            message=case["message"],
            hints=ClientHints(**hints) if hints else None,
        )

        try:
            response = run_turn(request, repo, model)
        except Exception as exc:
            results.append(
                CaseResult(case["id"], False, [f"raised {type(exc).__name__}: {exc}"])
            )
            continue

        plan = next(
            (e.data for e in response.events if e.type == "meal_plan"), None
        )
        error_code = next(
            (e.code.value for e in response.events if e.type == "error"), None
        )
        citations = {
            e.citation.ref: e.citation
            for e in response.events
            if e.type == "citation"
        }

        violations = _check_invariants(case, plan, error_code, citations)
        metrics = _measure(plan, citations) if plan else PlanMetrics()
        results.append(
            CaseResult(case["id"], not violations, violations, metrics)
        )

    return Scorecard(label, results)


def report(card: Scorecard, spec: ModelSpec | None = None) -> None:
    print(f"\n=== {card.model_label} ===")
    print(f"  invariants  {card.pass_rate:.0%}  ({card.passed}/{len(card.results)})")
    print("\n  quality metrics (reported, not scored):")
    print(f"    budget used      {card.mean('budget_used'):.0%}")
    print(f"    distinct meals   {card.mean('distinct_meals'):.1f}")
    print(f"    reuse ratio      {card.mean('reuse_ratio'):.2f} lines per product")
    print(f"    repair attempts  {card.mean('repair_attempts'):.2f}")

    failed = [r for r in card.results if not r.passed]
    if failed:
        print(f"\n  violations ({len(failed)} cases):")
        for r in failed:
            print(f"    {r.case_id}:")
            for msg in r.violations[:3]:
                print(f"      - {msg}")
            if len(r.violations) > 3:
                print(f"      ... and {len(r.violations) - 3} more")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--compare", nargs="+")
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        help="Exit non-zero below this invariant pass rate. CI regression floor.",
    )
    args = parser.parse_args()

    keys = args.compare or ([args.model] if args.model else [])

    if not keys:
        card = run(ScriptedModelClient(), "scripted (no model call)")
        report(card)
        print(
            "\nBaseline only. The scripted planner picks by position, not by "
            "suitability, so treat this as a floor to beat rather than a target."
        )
        return _gate(card.pass_rate, args.min_pass_rate)

    from src.models.bedrock import BedrockModelClient
    from src.models.registry import ModelRegistry, RoutingPolicy

    registry = ModelRegistry()
    cards: list[tuple[Scorecard, ModelSpec]] = []
    for key in keys:
        spec = registry.route(
            "generate_plan", policy=RoutingPolicy.PINNED, pinned_key=key
        )
        card = run(BedrockModelClient(pinned_spec=spec), spec.display_name)
        report(card, spec)
        cards.append((card, spec))

    if len(cards) > 1:
        print("\n=== comparison ===")
        print(f"  {'model':<24} {'invariants':>11} {'budget':>8} {'variety':>8}")
        for card, spec in sorted(cards, key=lambda c: -c[0].pass_rate):
            print(
                f"  {spec.display_name:<24} {card.pass_rate:>10.0%} "
                f"{card.mean('budget_used'):>7.0%} "
                f"{card.mean('distinct_meals'):>8.1f}"
            )

    best = max(c.pass_rate for c, _ in cards)
    return _gate(best, args.min_pass_rate)


def _gate(actual: float, floor: float | None) -> int:
    if floor is None:
        return 0
    if actual < floor:
        print(f"\nFAIL: pass rate {actual:.0%} is below the floor of {floor:.0%}")
        return 1
    print(f"\nOK: pass rate {actual:.0%} meets the floor of {floor:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
