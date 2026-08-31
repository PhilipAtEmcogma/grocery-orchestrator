"""
Repair eval — can this model fix a plan it did not write?

Repair is SEPARATELY ROUTED from `generate_plan`, and resolves to a different
model: generation runs on the QUALITY tier, repair on FAST. So the model that
repairs a plan is usually not the model that produced it, and nothing measured
the repairer. The meal-plan suite exercises repair only
incidentally — it fires during those runs and its output is scored through the
same invariants — so a repair-specific regression would surface as a slightly
lower meal-plan score with no indication of where it came from.

This drives `generate_plan` directly with `repair_attempts` already set, the way
`run_intent.py` drives `classify_intent`, and scores the repaired draft against
the thing repair exists to achieve.

TWO KINDS OF REPAIR, AND SINCE 2026-08-31 TWO ROUTED TASKS. This harness has
always scored the two kinds separately; `config/models.json` now routes them
separately too, `repair_budget` to Nova Lite and `repair_defect` to Claude
Haiku, each perfect at its half and below the 90% floor on the other.

So the per-kind rates below are the SCORECARDS, not a breakdown, and
`--min-pass-rate` gates each half independently. Gating the combined rate would
let a model that is perfect at one half and weak at the other clear the bar on
the average -- which is exactly the situation the split exists to describe.

Two kinds of repair, because the graph feeds it two kinds of failure:

* BUDGET — the previous plan was costed and came out over. The repaired plan
  must come in at or under budget while still covering the household and days.
* DEFECT — the previous plan was rejected for something that is not about money
  (an invented price in a meal name, a bad ref, broken arithmetic). The repaired
  plan must not repeat it. Before `build_defect_repair_prompt` existed these
  were told "your plan came to $0 OVER the $X budget", which describes none of
  them.

    python evals/run_repair.py                      # scripted baseline
    python evals/run_repair.py --model nova-lite    # live, per-model scorecard
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals._pacing import DEFAULT_MAX_RPM, pace_bedrock_calls
from src.graph.dietary import map_exclusions
from src.graph.nodes import validate_plan
from src.graph.nodes.plan import generate_plan
from src.graph.state import GroceryState
from src.models.base import (
    TASK_REPAIR_BUDGET,
    TASK_REPAIR_DEFECT,
    GuardrailBlocked,
    ModelClient,
    ModelError,
)
from src.models.registry import ModelSpec
from src.retrieval.filters import pin_to_fixture_snapshot
from src.retrieval.memory import InMemoryPriceRepository
from src.schemas.contract import Citation, MealPlan, SourceRef, find_literal_money_in_plan

CASES = Path(__file__).parent / "cases" / "repair.json"

#: Case kind -> the routed task it measures.
#:
#: The `kind` field predates the routing split and stays as the case
#: vocabulary; this is the single place the two are tied together, so a new
#: kind cannot quietly score no task and a renamed task cannot quietly score no
#: cases. `tests/test_eval_harness.py` asserts it covers every kind in the
#: suite and every repair task in `config/models.json`.
KIND_TO_TASK = {"budget": TASK_REPAIR_BUDGET, "defect": TASK_REPAIR_DEFECT}

MEAL_CATEGORIES = ["protein", "carbohydrate", "vegetable", "dairy", "pantry"]


@dataclass
class CaseResult:
    case_id: str
    kind: str
    passed: bool
    violations: list[str] = field(default_factory=list)
    produced: bool = False
    latency_ms: int = 0
    guardrail_blocked: bool = False
    upstream: bool = False


@dataclass
class Scorecard:
    model_label: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def scored(self) -> list[CaseResult]:
        # Guardrail blocks are NOT excluded here, unlike the intent and prose
        # suites. A repair prompt contains no untrusted user content -- it is
        # assembled entirely from code, config and validation errors this system
        # generated -- so there is nothing in it a content filter should ever
        # legitimately refuse. A block means the prompt reads as an attack, and
        # that is a defect in the prompt.
        #
        # This is not hypothetical. `build_defect_repair_prompt` shipped phrased
        # as a stack of imperatives ("Never write a price ... ANYWHERE", "Use
        # ONLY citation refs") and the PROMPT_ATTACK filter refused every defect
        # repair, turning a recoverable failure into GUARDRAIL_BLOCKED for the
        # user. Offline tests could not see it: the scripted client has no
        # guardrail. This suite is what found it, and this is what stops it
        # coming back.
        return [r for r in self.results if not r.upstream]

    @property
    def blocked(self) -> list[CaseResult]:
        return [r for r in self.results if r.guardrail_blocked]

    @property
    def upstream_failures(self) -> list[CaseResult]:
        return [r for r in self.results if r.upstream]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.scored if r.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / len(self.scored) if self.scored else 0.0

    def rate_for(self, kind: str) -> float:
        subset = [r for r in self.scored if r.kind == kind]
        return sum(1 for r in subset if r.passed) / len(subset) if subset else 0.0


def _citations(
    repo: InMemoryPriceRepository, *, budget: Decimal, exclusions: list[str]
) -> tuple[list[Citation], dict, dict]:
    """
    The candidate set a repair pass is given to choose from.

    Mirrors `retrieve_prices` exactly -- same categories, same per-category
    limit, the budget pre-filter, AND the dietary exclusion filter. The first
    version of this helper passed `exclude_categories=[]` and `budget_nzd=None`,
    so a vegetarian case handed the model meat and dairy and an unbounded
    candidate list. Two models then "failed" by cramming 14 ingredients into one
    meal against a cap of 12, and the number would have been recorded as a
    weakness in the models rather than in this function. A harness that feeds a
    different world from production measures a model nobody ships.
    """
    exclude_categories, _unsupported = map_exclusions(exclusions)
    records = repo.candidates_for_budget(
        categories=MEAL_CATEGORIES,
        exclude_categories=exclude_categories,
        limit_per_category=3,
        budget_nzd=budget,
    )
    citations: list[Citation] = []
    index: dict[str, Citation] = {}
    rec_index: dict = {}
    for i, rec in enumerate(records, start=1):
        ref = f"c{i}"
        c = Citation(
            ref=ref,
            store=rec.store,
            store_location=rec.store_location,
            product_name=rec.display_name,
            price_nzd=rec.price_nzd,
            unit=rec.unit,
            unit_price_nzd=rec.unit_price_nzd,
            on_special=rec.on_special,
            # PriceRecord stores the capture date as a string; Citation types it
            # as a date. `retrieve_prices` relies on pydantic coercing it, and so
            # does this, but the checker cannot see through that.
            valid_date=date.fromisoformat(rec.valid_date),
            source=SourceRef(table=repo.table_name, pk=rec.store_key, sk=rec.product_key),
        )
        citations.append(c)
        index[ref] = c
        rec_index[ref] = rec
    return citations, index, rec_index


def _state(case: dict, repo: InMemoryPriceRepository, previous: MealPlan | None) -> GroceryState:
    citations, index, rec_index = _citations(
        repo,
        budget=Decimal(str(case["budget_nzd"])),
        exclusions=case.get("exclusions", []),
    )
    state: GroceryState = {
        "session_id": "sess-evalrepair",
        "turn_id": f"turn-{case['id']}",
        "message": case["message"],
        "citations": citations,
        "citation_index": index,
        "record_index": rec_index,
        "constraints": {
            "household_size": case["household_size"],
            "days": case["days"],
            "budget_nzd": Decimal(str(case["budget_nzd"])),
            "dietary_exclusions": case.get("exclusions", []),
        },
        # The whole point: the model is entering on a REPAIR pass, not a first
        # attempt, so it receives repair feedback rather than the original brief.
        "repair_attempts": 1,
        "over_budget": case["kind"] == "budget",
        "validation_errors": case.get("validation_errors", []),
        "plan": previous,
        "events": [],
    }
    return state


def _seed_previous(case: dict, repo: InMemoryPriceRepository) -> MealPlan | None:
    """
    A deliberately over-budget plan for the budget cases, built by the scripted
    planner so the repair pass has something concrete to cut.
    """
    if case["kind"] != "budget":
        return None
    from src.models.scripted import ScriptedModelClient

    seed_state = _state(case, repo, None)
    seed_state["repair_attempts"] = 0
    seed_state["over_budget"] = False
    out = generate_plan(seed_state, ScriptedModelClient(plan_packs=Decimal("3")))
    return out.get("plan")


def _check(case: dict, plan: MealPlan | None, errors: list[str]) -> list[str]:
    budget = Decimal(str(case["budget_nzd"]))
    violations: list[str] = []

    if plan is None:
        violations.append(f"no plan produced: {'; '.join(errors) or 'unknown'}")
        return violations

    if case["kind"] == "budget":
        # What repair exists to do. Measured on PAYABLE, not consumption: you
        # cannot buy half a pack, and checking the fractional figure is the bug
        # that let a $65 basket report as fitting a $60 budget.
        if plan.payable_total_nzd > budget:
            violations.append(f"still over budget: payable {plan.payable_total_nzd} > {budget}")
    else:
        money = find_literal_money_in_plan(plan)
        if money:
            violations.append(f"repeated the defect: {money[0]}")

    # Both kinds must still respect the brief. A repair that drops the household
    # size or the allergy to hit a number is not a repair.
    if plan.household_size != case["household_size"]:
        violations.append(f"household {plan.household_size} != {case['household_size']}")
    if plan.days != case["days"]:
        violations.append(f"days {plan.days} != {case['days']}")
    for term in case.get("exclusions", []):
        if term not in plan.dietary_exclusions_applied:
            violations.append(f"dropped the exclusion {term!r} on regeneration")

    return violations


def run(model: ModelClient, label: str) -> Scorecard:
    repo = InMemoryPriceRepository()
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    card = Scorecard(model_label=label)

    for case in cases:
        previous = _seed_previous(case, repo)
        state = _state(case, repo, previous)

        started = time.perf_counter()
        try:
            out = generate_plan(state, model)
        except GuardrailBlocked:
            card.results.append(
                CaseResult(
                    case["id"],
                    case["kind"],
                    False,
                    ["the repair prompt was refused by the Guardrail — it reads as an attack"],
                    guardrail_blocked=True,
                )
            )
            continue
        except ModelError as exc:
            card.results.append(
                CaseResult(case["id"], case["kind"], False, [str(exc)], upstream=True)
            )
            continue
        elapsed = int((time.perf_counter() - started) * 1000)

        plan = out.get("plan")
        errors = list(out.get("validation_errors") or [])
        if plan is not None:
            # Run the same arithmetic and budget checks the graph would.
            errors += list(validate_plan({**state, "plan": plan}).get("validation_errors") or [])

        violations = _check(case, plan, errors)
        card.results.append(
            CaseResult(
                case_id=case["id"],
                kind=case["kind"],
                passed=not violations,
                violations=violations,
                produced=plan is not None,
                latency_ms=elapsed,
            )
        )

    return card


def report(card: Scorecard) -> None:
    print(f"\n=== {card.model_label} ===")
    # Per ROUTED TASK, because that is what routing and the qualification gate
    # read. The combined figure is printed last and labelled as not the
    # scorecard: it is the number that hid Claude Haiku's 71.4% budget half
    # behind its perfect defect half while the two were one task.
    for kind, task in KIND_TO_TASK.items():
        subset = [r for r in card.scored if r.kind == kind]
        passed = sum(1 for r in subset if r.passed)
        print(f"  {task:<15} {card.rate_for(kind):.1%}  ({passed}/{len(subset)} cases)")
    print(f"  (combined)      {card.pass_rate:.1%}  - reported, NOT the scorecard")
    if card.blocked:
        print(
            f"  guardrail        {len(card.blocked)} repair prompts REFUSED "
            f"— scored as failures, see Scorecard.scored"
        )
    if card.upstream_failures:
        print(f"  upstream         {len(card.upstream_failures)} never answered")

    failed = [r for r in card.scored if not r.passed]
    if failed:
        print(f"\n  failures ({len(failed)}):")
        for r in failed:
            for v in r.violations:
                print(f"    {r.case_id} [{r.kind}]: {v}")


def _gate(card: Scorecard, floor: float | None) -> int:
    if card.upstream_failures:
        print(
            f"\nINCONCLUSIVE: {len(card.upstream_failures)}/{len(card.results)} cases "
            f"failed upstream, so this is not a measurement of the model.",
            file=sys.stderr,
        )
        return 2
    if floor is None:
        return 0

    # EACH HALF, NOT THE AVERAGE, since 2026-08-31.
    #
    # `repair_budget` and `repair_defect` are separately routed tasks, so the
    # floor has to be met by each of them or the gate is measuring something no
    # turn experiences. It also closes the shape that made the split necessary:
    # Claude Haiku scored 83.3% combined -- one number, comfortably readable as
    # "a bit weak" -- while being 100% on defect repair and 71.4% on budget.
    # Averaging a strength against a weakness hides both.
    failures = [
        (task, card.rate_for(kind))
        for kind, task in KIND_TO_TASK.items()
        if any(r.kind == kind for r in card.scored) and card.rate_for(kind) < floor
    ]
    if failures:
        for task, rate in failures:
            print(f"\nFAIL: {task} {rate:.1%} is below the floor of {floor:.1%}")
        return 1
    print(f"\nOK: every repair task meets the floor of {floor:.1%}")
    return 0


def main() -> int:
    # Freshness is judged as of the fixture capture, not the wall clock: these
    # run against a committed SNAPSHOT, and judging a snapshot against today
    # makes every price stale on a date nobody chose. See filters.py.
    pin_to_fixture_snapshot()
    parser = argparse.ArgumentParser(description="Repair-pass eval")
    parser.add_argument("--model", help="Model key to pin, e.g. nova-lite")
    parser.add_argument("--min-pass-rate", type=float)
    parser.add_argument("--max-rpm", type=int, default=DEFAULT_MAX_RPM)
    args = parser.parse_args()

    if not args.model:
        from src.models.scripted import ScriptedModelClient

        card = run(ScriptedModelClient(), "scripted (no model call)")
        report(card)
        print(
            "\nBaseline only. The scripted planner shrinks portions on the FAST "
            "tier rather than reasoning about substitutions, so this measures the "
            "harness and the repair prompt wiring, not a model."
        )
        return _gate(card, args.min_pass_rate)

    import os

    os.environ["USE_BEDROCK"] = "1"
    pace_bedrock_calls(args.max_rpm)

    from src.models.bedrock import BedrockModelClient
    from src.models.registry import ModelRegistry, RoutingPolicy

    spec: ModelSpec = ModelRegistry().route(
        # Either repair task resolves the same pinned model; PINNED bypasses
        # the routing rule entirely and only needs a task that exists.
        TASK_REPAIR_BUDGET,
        policy=RoutingPolicy.PINNED,
        pinned_key=args.model,
    )
    card = run(BedrockModelClient(pinned_spec=spec), spec.display_name)
    report(card)
    return _gate(card, args.min_pass_rate)


if __name__ == "__main__":
    raise SystemExit(main())
