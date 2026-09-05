"""
Recipe selection eval — does this model choose meals a household can eat?

`select_recipes` is a routed task of its own (Req 2.9, Pilot Task 15c), so it
needs a scorecard of its own: the rule in this repository is that a model may
not serve a task it was never scored on, and adding a task without a suite is
exactly the hole `ModelRegistry.unscored_routes()` exists to refuse.

WHAT IS SCORED, AND WHY THESE FOUR. The second audit named the two failure modes
this task introduces — "selecting a recipe whose dietary classification does not
match the request, or selecting the same recipe five times" — and neither
existing suite would catch either. Both are scored here, with two more that the
shape of the task makes possible:

* FABRICATION      — every id returned must be one that was offered. A real
                     recipe id that was not on the shortlist is still a
                     fabrication for this turn: it failed one of the three
                     filters, so it is uncostable, excluded by the shopper's
                     diet, or unaffordable.
* DIETARY          — every selected recipe must be viable under the stated
                     exclusions, judged from the RESOLVED products rather than
                     the recipe's name. This is guaranteed by the shortlist, and
                     it is scored anyway: the guarantee is the thing worth
                     testing end to end, and a filter that silently stopped
                     filtering would look exactly like one that works.
* REPETITION       — no duplicate ids while distinct ones remain. Repeats are
                     legitimate when the shortlist is smaller than the meal
                     count (vegan is 7 of 29), so this scores repeating when
                     there was an alternative, not repeating at all.
* COUNT            — as many meals as were asked for, capped by what exists.

WHAT IS NOT SCORED. Whether the menu is *good*. Variety of main ingredients is
reported and deliberately not scored, for the reason the meal-plan suite gives
about its own reported metrics: no threshold is right for every request, and
scoring one manufactures a gradient without establishing that it means anything.

NOTE WHAT CANNOT BE WRONG HERE. There is no price check in this suite, because
there is nothing for the model to get wrong about money: `RecipeSelection` has
one field and it holds ids. That is the point of the schema.

    python evals/run_recipe_select.py                    # scripted baseline
    python evals/run_recipe_select.py --model nova-lite  # live, per-model scorecard
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals._pacing import DEFAULT_MAX_RPM, pace_bedrock_calls
from src.graph.nodes import retrieve_prices
from src.graph.nodes.recipes import select_recipes
from src.graph.recipe_plan import curated_recipes
from src.models.base import TASK_SELECT_RECIPES, GuardrailBlocked, ModelClient, ModelError
from src.models.registry import ModelSpec
from src.recipes.base import is_viable_for
from src.retrieval.filters import pin_to_fixture_snapshot
from src.retrieval.memory import InMemoryPriceRepository
from src.schemas.contract import Intent

CASES = Path(__file__).parent / "cases" / "recipe_select.json"


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    violations: list[str] = field(default_factory=list)
    selected: int = 0
    offered: int = 0
    distinct_mains: int = 0
    fell_back: bool = False
    latency_ms: int = 0
    guardrail_blocked: bool = False
    upstream: bool = False


@dataclass
class Scorecard:
    model_label: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def scored(self) -> list[CaseResult]:
        # Guardrail blocks are scored as FAILURES, like the repair suite and
        # unlike the intent suite. The selection prompt carries the shopper's
        # message, so a block is possible in principle -- but every case here is
        # an ordinary grocery request, and a content filter refusing "dinners
        # for four on $60" is a defect in the prompt or the policy, not a
        # legitimate refusal to excuse.
        return [r for r in self.results if not r.upstream]

    @property
    def upstream_failures(self) -> list[CaseResult]:
        return [r for r in self.results if r.upstream]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.scored if r.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / len(self.scored) if self.scored else 0.0


def _state_for(case: dict, repo: InMemoryPriceRepository) -> dict:
    """
    A post-retrieval state, built by the REAL retrieval node.

    Not a hand-made shortlist. The shortlist is the guarantee this task rests
    on -- costable, dietary-viable, collectively affordable -- and a suite that
    constructed its own would score the model against a world the service does
    not produce. `evals/run_repair.py` learned this the hard way: its first
    candidate helper passed no budget and no exclusions, so two models "failed"
    against a candidate set production never builds.
    """
    hints = case["hints"]
    state = {
        "session_id": "evalsess",
        "turn_id": case["id"][:12].ljust(8, "0"),
        "message": case["message"],
        "intent": Intent.MEAL_PLAN,
        "constraints": {
            "household_size": hints["household_size"],
            "days": hints["days"],
            "budget_nzd": Decimal(str(hints["budget_nzd"])),
            "dietary_exclusions": hints.get("dietary_exclusions", []),
        },
        "events": [],
        "hints": hints,
        "location": None,
    }
    state.update(retrieve_prices(state, repo))  # type: ignore[arg-type]
    return state


def _check(case: dict, state: dict, out: dict, repo: InMemoryPriceRepository) -> list[str]:
    v: list[str] = []
    offered = list(state.get("recipe_shortlist") or [])
    # WHAT THE MODEL RETURNED, not what the node served. `select_recipes` tops a
    # short selection up from unused recipes and trims meals that do not fit --
    # right for a plan, and fatal for a scorecard, because a node that repairs
    # every mistake qualifies every model. Scoring the served list made a
    # planted "returns one meal every time" model score 100%.
    chosen = list(out.get("recipe_selection_model") or [])

    if out.get("recipe_fallback"):
        # A fallback is a legitimate outcome of the TURN and a failure of this
        # TASK when the shortlist had something to choose from. Scored that way
        # rather than excluded, because "the model returned nothing usable" is
        # the failure mode a scorecard is for.
        if offered:
            v.append(f"fell back to free composition with {len(offered)} recipes offered")
        return v

    if not chosen:
        v.append("selected nothing")
        return v

    fabricated = [r for r in chosen if r not in offered]
    if fabricated:
        v.append(f"selected ids that were not offered: {fabricated}")

    excluded = set(case.get("expect", {}).get("exclude_categories", []))
    if excluded:
        by_id = {r.recipe_id: r for r in curated_recipes()}

        def category_of(term: str) -> str | None:
            key = repo.resolve_product_key(term)
            if key is None:
                return None
            found = repo.cheapest_for_product(key, limit=1)
            return found[0].category if found else None

        for rid in set(chosen):
            recipe = by_id.get(rid)
            if recipe is None:
                continue
            if not is_viable_for(recipe, excluded, category_of):
                v.append(f"{rid} breaches the stated exclusions {sorted(excluded)}")

    # Repetition BEFORE count, so the more specific defect is the one named: a
    # model that returns the same id four times has also returned one distinct
    # meal, and reporting that as "chose too few" sends the reader after the
    # wrong thing. Repeating is legitimate only when distinct options ran out.
    if len(set(chosen)) < len(chosen) and len(offered) >= len(chosen):
        v.append(
            f"repeated a recipe {len(chosen) - len(set(chosen))} time(s) with "
            f"{len(offered)} distinct options available"
        )

    wanted = state.get("recipe_meals_wanted") or 1
    expected = min(wanted, len(offered))
    if len(set(chosen)) < expected and len(set(chosen)) == len(chosen):
        v.append(f"chose {len(chosen)} meals, {expected} were available and needed")

    return v


def run(model: ModelClient, label: str) -> Scorecard:
    payload = json.loads(CASES.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    card = Scorecard(model_label=label)
    repo = InMemoryPriceRepository()

    for case in cases:
        state = _state_for(case, repo)
        started = time.perf_counter()
        try:
            out = select_recipes(state, model)  # type: ignore[arg-type]
        except GuardrailBlocked:
            card.results.append(
                CaseResult(
                    case["id"],
                    False,
                    ["guardrail refused the selection prompt"],
                    guardrail_blocked=True,
                )
            )
            continue
        except ModelError as exc:
            card.results.append(CaseResult(case["id"], False, [str(exc)], upstream=True))
            continue
        elapsed = int((time.perf_counter() - started) * 1000)

        if out.get("upstream_error"):
            card.results.append(
                CaseResult(case["id"], False, [out["upstream_error"]], upstream=True)
            )
            continue

        violations = _check(case, state, out, repo)
        by_id = {r.recipe_id: r for r in curated_recipes()}
        mains = {
            by_id[rid].ingredients[0].key
            for rid in (out.get("selected_recipes") or [])
            if rid in by_id and by_id[rid].ingredients
        }
        card.results.append(
            CaseResult(
                case_id=case["id"],
                passed=not violations,
                violations=violations,
                selected=len(out.get("selected_recipes") or []),
                offered=len(state.get("recipe_shortlist") or []),
                distinct_mains=len(mains),
                fell_back=bool(out.get("recipe_fallback")),
                latency_ms=elapsed,
            )
        )
    return card


def report(card: Scorecard) -> None:
    print(f"\n=== {card.model_label} ===")
    print(f"  {TASK_SELECT_RECIPES:<16} {card.pass_rate:.1%}  ({card.passed}/{len(card.scored)})")
    scored = card.scored
    if scored:
        print("\n  reported, not scored:")
        print(f"    meals chosen     {sum(r.selected for r in scored) / len(scored):.1f}")
        print(f"    recipes offered  {sum(r.offered for r in scored) / len(scored):.1f}")
        print(f"    distinct mains   {sum(r.distinct_mains for r in scored) / len(scored):.1f}")
        print(f"    fell back        {sum(1 for r in scored if r.fell_back)}/{len(scored)}")
    if card.upstream_failures:
        print(f"  upstream         {len(card.upstream_failures)} never answered")

    failed = [r for r in scored if not r.passed]
    if failed:
        print(f"\n  failures ({len(failed)}):")
        for r in failed:
            for violation in r.violations:
                print(f"    {r.case_id}: {violation}")


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
    if card.pass_rate < floor:
        print(f"\nFAIL: selection {card.pass_rate:.1%} is below the floor of {floor:.1%}")
        return 1
    print(f"\nOK: selection {card.pass_rate:.1%} meets the floor of {floor:.1%}")
    return 0


def main() -> int:
    pin_to_fixture_snapshot()
    parser = argparse.ArgumentParser(description="Recipe selection eval")
    parser.add_argument("--model", help="Model key to pin, e.g. nova-lite")
    parser.add_argument("--min-pass-rate", type=float)
    parser.add_argument("--max-rpm", type=int, default=DEFAULT_MAX_RPM)
    args = parser.parse_args()

    if not args.model:
        from src.models.scripted import ScriptedModelClient

        card = run(ScriptedModelClient(), "scripted (no model call)")
        report(card)
        print(
            "\nBaseline only. The scripted selector spreads across main ingredients "
            "and then fills in order, so this measures the shortlist, the prompt and "
            "the validation wiring rather than a model's judgement."
        )
        return _gate(card, args.min_pass_rate)

    import os

    os.environ["USE_BEDROCK"] = "1"
    pace_bedrock_calls(args.max_rpm)

    from src.models.bedrock import BedrockModelClient
    from src.models.registry import ModelRegistry, RoutingPolicy

    spec: ModelSpec = ModelRegistry().route(
        TASK_SELECT_RECIPES, policy=RoutingPolicy.PINNED, pinned_key=args.model
    )
    card = run(BedrockModelClient(pinned_spec=spec), spec.display_name)
    report(card)
    return _gate(card, args.min_pass_rate)


if __name__ == "__main__":
    raise SystemExit(main())
