"""
Prose eval — can this model produce prose the node will actually ship?

`generate_prose` was routed to models nothing had scored for prose. Recording
the scorecards for Pilot Task 7 forced the admission into `config/models.json`,
and this closes it.

WHAT IS MEASURED, AND WHY IT IS NOT "IS THE SENTENCE NICE". Every check here is
a rule-violation check. The prose node imposes a protocol — write placeholders,
never money, attribute the cheapest option correctly — and **degrades silently
on any breach**, dropping the sentence and shipping the bare table. So the
question that matters operationally is not whether the prose reads well. It is
whether a model can follow the protocol at all, because a model that cannot
produces a product with no prose in it and no error to show for it.

An LLM judge was considered and rejected for the meal-plan suite, for a reason
that applies here with more force: it puts a non-deterministic scorer inside a
suite whose entire value is being deterministic. Legacy 5.6 (subjective quality
scoring for variety and appeal) stays open, and stays honestly labelled as open.

    python evals/run_prose.py                      # scripted baseline
    python evals/run_prose.py --model nova-lite    # live, per-model scorecard
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals._pacing import DEFAULT_MAX_RPM, pace_bedrock_calls
from src.graph.nodes.prose import store_name
from src.models.base import GuardrailBlocked, ModelClient
from src.models.registry import ModelSpec
from src.retrieval.filters import pin_to_fixture_snapshot
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import LITERAL_MONEY, ChatRequest, ChatResponse

CASES = Path(__file__).parent / "cases" / "prose.json"

# An unresolved placeholder reaching the user is a visible defect. `render()`
# raises on unknown tokens, so any survivor here means the renderer was bypassed.
UNRESOLVED = re.compile(r"\[\[")

UPSTREAM_CODES = {"INTERNAL_ERROR", "UPSTREAM_TIMEOUT", "RATE_LIMITED"}


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    violations: list[str] = field(default_factory=list)
    delivered: bool = False
    sentences: int = 0
    chars: int = 0
    latency_ms: int = 0
    guardrail_blocked: bool = False
    upstream: bool = False


@dataclass
class Scorecard:
    model_label: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def scored(self) -> list[CaseResult]:
        # Same exclusions as the intent harness, for the same reasons: a
        # Guardrail refusal is the safety layer working, and an upstream failure
        # is a statement about the account rather than about the model.
        return [r for r in self.results if not r.guardrail_blocked and not r.upstream]

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

    @property
    def delivery_rate(self) -> float:
        """Of the cases that SHOULD have prose, how many got it."""
        wanted = [r for r in self.scored if r.case_id.startswith(("pr-", "mp-"))]
        return sum(1 for r in wanted if r.delivered) / len(wanted) if wanted else 0.0

    def mean(self, attr: str) -> float:
        vals = [getattr(r, attr) for r in self.scored if r.delivered]
        return sum(vals) / len(vals) if vals else 0.0


def _check(case: dict, response: ChatResponse) -> tuple[list[str], bool, str]:
    """Rule violations for one turn, whether prose shipped, and the text."""
    tokens = [e for e in response.events if e.type == "token"]
    text = "".join(t.text for t in tokens).strip()
    delivered = bool(tokens)
    violations: list[str] = []

    if not case["expect_prose"]:
        if delivered:
            violations.append("prose was emitted on a turn with no citations behind it")
        return violations, delivered, text

    if not delivered:
        # The headline failure. The node caught something and dropped the
        # sentence: bad placeholder, money, or a misattributed cheapest option.
        violations.append("no prose delivered — the node rejected the model's output")
        return violations, delivered, text

    match = LITERAL_MONEY.search(text)
    if match:
        # Should be unreachable: the node checks twice. If it fires, the second
        # check is broken rather than the model being naughty.
        violations.append(f"literal money {match.group(0)!r} reached the user")

    if UNRESOLVED.search(text):
        violations.append("an unresolved placeholder reached the user")

    # Attribution, for price checks: the sentence must name the option the
    # comparison marks cheapest. The node verifies the citation REF; this
    # verifies the rendered label, which is what the shopper actually reads.
    comparisons = [e for e in response.events if e.type == "price_comparison"]
    citations = {e.citation.ref: e.citation for e in response.events if e.type == "citation"}
    if comparisons:
        cheapest_refs = [
            o.citation_ref for c in comparisons for o in c.data.options if o.is_cheapest
        ]
        named = [
            c
            for ref in cheapest_refs
            if (c := citations.get(ref))
            and f"{store_name(c.store.value)} {c.store_location}" in text
        ]
        if not named:
            violations.append("prose names no cheapest option from the comparison")

    return violations, delivered, text


def run(model: ModelClient, label: str) -> Scorecard:
    repo = InMemoryPriceRepository()
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    card = Scorecard(model_label=label)

    for case in cases:
        request = ChatRequest(
            version="1.0",
            session_id="sess-evalprose",
            turn_id=f"turn-{case['id']}",
            message=case["message"],
        )
        started = time.perf_counter()
        try:
            response = run_turn(request, repo, model)
        except GuardrailBlocked:
            card.results.append(CaseResult(case["id"], False, guardrail_blocked=True))
            continue
        except Exception as exc:
            card.results.append(
                CaseResult(case["id"], False, [f"raised {type(exc).__name__}: {exc}"])
            )
            continue
        elapsed = int((time.perf_counter() - started) * 1000)

        codes = [e.code.value for e in response.events if e.type == "error"]
        if any(c in UPSTREAM_CODES for c in codes):
            card.results.append(CaseResult(case["id"], False, upstream=True))
            continue
        if "GUARDRAIL_BLOCKED" in codes:
            card.results.append(CaseResult(case["id"], False, guardrail_blocked=True))
            continue

        violations, delivered, text = _check(case, response)
        card.results.append(
            CaseResult(
                case_id=case["id"],
                passed=not violations,
                violations=violations,
                delivered=delivered,
                sentences=text.count(".") if delivered else 0,
                chars=len(text),
                latency_ms=elapsed,
            )
        )

    return card


def report(card: Scorecard, spec: ModelSpec | None = None) -> None:
    print(f"\n=== {card.model_label} ===")
    print(f"  rule compliance  {card.pass_rate:.1%}  ({card.passed}/{len(card.scored)})")
    print(f"  prose delivered  {card.delivery_rate:.1%}  of turns that should have it")
    if card.blocked:
        ids = ", ".join(r.case_id for r in card.blocked)
        print(f"  guardrail        {len(card.blocked)} refused before generation ({ids})")
    if card.upstream_failures:
        ids = ", ".join(r.case_id for r in card.upstream_failures)
        print(f"  upstream         {len(card.upstream_failures)} never answered ({ids})")

    print("\n  shape (reported, not scored):")
    print(f"    sentences      {card.mean('sentences'):.1f}")
    print(f"    characters     {card.mean('chars'):.0f}")
    if spec is not None:
        print(f"    p50 latency    {card.mean('latency_ms'):.0f} ms")

    failed = [r for r in card.scored if not r.passed]
    if failed:
        print(f"\n  failures ({len(failed)}):")
        for r in failed:
            for v in r.violations:
                print(f"    {r.case_id}: {v}")


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
        print(f"\nFAIL: rule compliance {card.pass_rate:.1%} is below the floor of {floor:.1%}")
        return 1
    print(f"\nOK: rule compliance {card.pass_rate:.1%} meets the floor of {floor:.1%}")
    return 0


def main() -> int:
    # Freshness is judged as of the fixture capture, not the wall clock: these
    # run against a committed SNAPSHOT, and judging a snapshot against today
    # makes every price stale on a date nobody chose. See filters.py.
    pin_to_fixture_snapshot()
    parser = argparse.ArgumentParser(description="Prose rule-compliance eval")
    parser.add_argument("--model", help="Model key to pin, e.g. nova-lite")
    parser.add_argument("--min-pass-rate", type=float)
    parser.add_argument("--max-rpm", type=int, default=DEFAULT_MAX_RPM)
    args = parser.parse_args()

    if not args.model:
        from src.models.scripted import ScriptedModelClient

        card = run(ScriptedModelClient(), "scripted (no model call)")
        report(card)
        print(
            "\nBaseline only. The scripted client returns fixed placeholder text, "
            "so this measures the harness and the node, not a model."
        )
        return _gate(card, args.min_pass_rate)

    import os

    os.environ["USE_BEDROCK"] = "1"
    pace_bedrock_calls(args.max_rpm)

    from src.models.bedrock import BedrockModelClient
    from src.models.registry import ModelRegistry, RoutingPolicy

    registry = ModelRegistry()
    spec = registry.route("generate_prose", policy=RoutingPolicy.PINNED, pinned_key=args.model)
    card = run(BedrockModelClient(pinned_spec=spec), spec.display_name)
    report(card, spec)
    return _gate(card, args.min_pass_rate)


if __name__ == "__main__":
    raise SystemExit(main())
