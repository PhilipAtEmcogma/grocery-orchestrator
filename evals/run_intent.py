"""
Intent classification eval.

Answers the question unit tests cannot: is the model actually any good, and
which model is best for this task at what cost?

Scoring is deterministic — no LLM judge needed here, because intent and
extracted constraints have correct answers. The judge is reserved for meal
plan quality, where "is this appetising" has no ground truth.

Design decisions worth knowing:

* `resolves_to` is asserted, not the raw query_item string. Asserting the
  model returned exactly "butter" would be brittle and would not tell us
  what we care about, which is whether retrieval finds the right product.

* Cases marked `known_gap` are reported separately and do not count against
  the score. Documenting a limitation honestly is more useful than tuning
  the golden set until everything passes.

* Null expectations are checked. "Must not invent a budget" is as important
  as "must extract the stated budget" — a hallucinated constraint silently
  changes what the user asked for.

Run:
    python evals/run_intent.py                    # scripted client, free, no AWS
    python evals/run_intent.py --model claude-haiku
    python evals/run_intent.py --compare claude-haiku claude-sonnet nova-lite
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
from src.graph.nodes.intent import classify_intent
from src.graph.state import GroceryState
from src.models.base import ModelClient
from src.models.registry import ModelSpec
from src.models.scripted import ScriptedModelClient
from src.retrieval.memory import InMemoryPriceRepository

CASES = Path(__file__).parent / "cases" / "intent.json"


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    known_gap: str | None
    failures: list[str] = field(default_factory=list)
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    # The model call failed and `classify_intent` fell back to keyword
    # heuristics. Recorded because this harness CANNOT see such a failure any
    # other way: the node degrades rather than raising, so a throttled run
    # still produces a classification for every case and still reports a tidy
    # accuracy -- one that measures the keyword fallback, not the model.
    degraded: bool = False


@dataclass
class Scorecard:
    model_label: str
    results: list[CaseResult]

    @property
    def degraded(self) -> list[CaseResult]:
        """Cases scored on the keyword fallback rather than on the model."""
        return [r for r in self.results if r.degraded]

    @property
    def scored(self) -> list[CaseResult]:
        return [r for r in self.results if r.known_gap is None]

    @property
    def gaps(self) -> list[CaseResult]:
        return [r for r in self.results if r.known_gap is not None]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.scored if r.passed)

    @property
    def accuracy(self) -> float:
        return self.passed / len(self.scored) if self.scored else 0.0

    @property
    def p50_latency_ms(self) -> int:
        values = sorted(r.latency_ms for r in self.results)
        return values[len(values) // 2] if values else 0

    def cost(self, spec: ModelSpec) -> Decimal:
        return sum(
            (spec.cost_for(r.input_tokens, r.output_tokens) for r in self.results),
            Decimal(0),
        )


def _check(case: dict, out: dict, repo: InMemoryPriceRepository) -> list[str]:
    """Deterministic scoring. Returns a list of failure descriptions."""
    expect = case["expect"]
    failures: list[str] = []
    constraints = out.get("constraints", {})

    if "intent" in expect and out["intent"].value != expect["intent"]:
        failures.append(f"intent {out['intent'].value} != {expect['intent']}")

    if "intent_in" in expect and out["intent"].value not in expect["intent_in"]:
        failures.append(f"intent {out['intent'].value} not in {expect['intent_in']}")

    if "resolves_to" in expect:
        items = constraints.get("query_items") or [""]
        resolved = repo.resolve_product_key(items[0])
        if resolved != expect["resolves_to"]:
            failures.append(f"resolves_to {resolved} != {expect['resolves_to']}")

    for field_name in ("household_size", "days"):
        if field_name in expect:
            actual = constraints.get(field_name)
            wanted = expect[field_name]
            if wanted is None:
                # Node defaults absent values to 1; treat that as "not stated".
                if actual not in (None, 1):
                    failures.append(f"{field_name} invented: {actual}")
            elif actual != wanted:
                failures.append(f"{field_name} {actual} != {wanted}")

    if "budget_nzd" in expect:
        actual = constraints.get("budget_nzd")
        wanted = expect["budget_nzd"]
        if wanted is None:
            if actual is not None:
                failures.append(f"budget invented: {actual}")
        elif actual is None or Decimal(str(actual)) != Decimal(wanted):
            failures.append(f"budget {actual} != {wanted}")

    if "exclusions" in expect:
        # Assert what the exclusions RESOLVE to (categories), not the exact
        # term string. "no meat" and "vegetarian" both map to {meat, seafood}
        # — the system's behaviour is identical for both. Asserting the literal
        # term tests the model's vocabulary alignment, not correctness.
        from src.graph.dietary import map_exclusions

        actual_terms = constraints.get("dietary_exclusions", [])
        actual_cats, _ = map_exclusions(actual_terms)
        wanted_cats, _ = map_exclusions(list(expect["exclusions"]))
        actual_set = set(actual_cats)
        wanted_set = set(wanted_cats)
        if not wanted_set.issubset(actual_set):
            failures.append(
                f"exclusions {sorted(actual_terms)} missing {sorted(wanted_set - actual_set)}"
            )

    if "multi_item" in expect:
        # Every item must resolve, not just the first. This is the check the
        # known_gap cases were waiting on.
        items = constraints.get("query_items") or []
        resolved = [repo.resolve_product_key(t) for t in items]
        missing = [w for w in expect["multi_item"] if w not in resolved]
        if missing:
            failures.append(f"multi-item: resolved {resolved}, missing {missing}")

    return failures


def run(model: ModelClient, label: str) -> Scorecard:
    repo = InMemoryPriceRepository()
    data = json.loads(CASES.read_text(encoding="utf-8"))
    results: list[CaseResult] = []

    for case in data["cases"]:
        state: GroceryState = {
            "session_id": "sess-eval001",
            "turn_id": f"turn-{case['id']}",
            "message": case["message"],
            "hints": case.get("hints", {}),
            "events": [],
        }

        started = time.perf_counter()
        degraded = False
        try:
            out = classify_intent(state, model)
            degraded = bool(out.get("intent_degraded"))
            failures = _check(case, out, repo)
        except Exception as exc:
            failures = [f"raised {type(exc).__name__}: {exc}"]
        elapsed = int((time.perf_counter() - started) * 1000)

        usage = model.last_usage or {}
        results.append(
            CaseResult(
                case_id=case["id"],
                passed=not failures,
                known_gap=case.get("known_gap"),
                failures=failures,
                degraded=degraded,
                latency_ms=elapsed,
                input_tokens=usage.get("input_tokens") or 0,
                output_tokens=usage.get("output_tokens") or 0,
            )
        )

    return Scorecard(model_label=label, results=results)


def report(card: Scorecard, spec: ModelSpec | None = None, verbose: bool = False) -> None:
    print(f"\n=== {card.model_label} ===")
    print(f"  accuracy   {card.accuracy:.1%}  ({card.passed}/{len(card.scored)})")
    print(f"  p50 latency {card.p50_latency_ms} ms")
    if spec is not None:
        print(f"  est. cost   ${card.cost(spec)} for {len(card.results)} cases")

    failed = [r for r in card.scored if not r.passed]
    if failed:
        print(f"\n  failures ({len(failed)}):")
        for r in failed:
            print(f"    {r.case_id}: {'; '.join(r.failures)}")

    if card.gaps:
        print(f"\n  known gaps ({len(card.gaps)}, not scored):")
        for r in card.gaps:
            status = "unexpectedly passes" if r.passed else "still open"
            print(f"    {r.case_id}: {r.known_gap} [{status}]")

    if verbose:
        print("\n  all cases:")
        for r in card.results:
            mark = "ok  " if r.passed else "FAIL"
            print(f"    {mark} {r.case_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="Model key to pin, e.g. claude-haiku")
    parser.add_argument("--compare", nargs="+", help="Compare several model keys")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--max-rpm",
        type=int,
        default=DEFAULT_MAX_RPM,
        help=(
            f"Bedrock requests per minute (default {DEFAULT_MAX_RPM}). This "
            f"suite is 30 cases against a 10/min account limit for Claude. "
            f"Unpaced, the tail is classified by the keyword FALLBACK rather "
            f"than by the model, and the run reports a plausible accuracy for "
            f"a model that answered a third of it. 0 disables pacing."
        ),
    )
    parser.add_argument(
        "--min-accuracy",
        type=float,
        help=(
            "Exit non-zero below this accuracy. Used in CI as a regression "
            "floor: a change that lowers the score fails the build."
        ),
    )
    args = parser.parse_args()

    keys = args.compare or ([args.model] if args.model else [])

    if not keys:
        card = run(ScriptedModelClient(), "scripted (no model call)")
        report(card, verbose=args.verbose)
        print(
            "\nBaseline only. The scripted client is rule-based, so this measures "
            "the harness, not a model. Pass --model once Bedrock is configured."
        )
        return _gate(card.accuracy, args.min_accuracy, "accuracy", card)

    from src.models.bedrock import BedrockModelClient
    from src.models.registry import ModelRegistry, RoutingPolicy

    pace_bedrock_calls(args.max_rpm)
    registry = ModelRegistry()
    cards: list[tuple[Scorecard, ModelSpec]] = []

    for key in keys:
        spec = registry.route("classify_intent", policy=RoutingPolicy.PINNED, pinned_key=key)
        client = BedrockModelClient(pinned_spec=spec)
        card = run(client, spec.display_name)
        report(card, spec, verbose=args.verbose)
        cards.append((card, spec))

    if len(cards) > 1:
        print("\n=== comparison ===")
        print(f"  {'model':<24} {'accuracy':>9} {'p50 ms':>8} {'cost':>12}")
        for card, spec in sorted(cards, key=lambda c: -c[0].accuracy):
            print(
                f"  {spec.display_name:<24} {card.accuracy:>8.1%} "
                f"{card.p50_latency_ms:>8} {'$' + str(card.cost(spec)):>12}"
            )

    best = max(c.accuracy for c, _ in cards)
    return _gate(best, args.min_accuracy, "accuracy")


def _gate(actual: float, floor: float | None, label: str, card: Scorecard | None = None) -> int:
    """Regression floor. Absent a floor, reporting is the only job."""
    # A degraded case was classified by the keyword fallback because the model
    # call failed, so the accuracy is part model and part heuristic. Unlike the
    # meal-plan harness, nothing here errors: `classify_intent` degrades by
    # design, so an unpaced run against a 10/min quota still answers all 30
    # cases and still prints a plausible percentage. That number is not a
    # measurement of the model, and it is not a pass or a failure either.
    if card is not None and card.degraded:
        print(
            f"\nINCONCLUSIVE: {len(card.degraded)}/{len(card.results)} cases fell back "
            f"to keyword matching because the model call failed, so {actual:.1%} is "
            f"partly the fallback's score. Re-run, and pace it (--max-rpm).",
            file=sys.stderr,
        )
        return 2
    if floor is None:
        return 0
    if actual < floor:
        print(f"\nFAIL: {label} {actual:.1%} is below the floor of {floor:.1%}")
        return 1
    print(f"\nOK: {label} {actual:.1%} meets the floor of {floor:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
