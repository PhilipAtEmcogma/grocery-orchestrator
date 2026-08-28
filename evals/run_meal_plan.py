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
import time
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

# Which fixture categories each exclusion term rules out. Mirrors
# `src/graph/dietary.SUPPORTED_EXCLUSIONS`, kept in step by a sanity test
# in `tests/test_dietary.py` — a divergence between the harness and the
# production mapping would score a plan against categories the plan was
# never filtered on.
EXCLUSION_CATEGORIES = {
    "seafood": {"seafood"},
    "vegetarian": {"meat", "seafood"},
    "dairy-free": {"dairy"},
    "vegan": {"meat", "seafood", "dairy", "chilled"},
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


# Error codes that mean "we never got an answer from the model", as opposed to
# "the model answered and the answer was wrong". Scoring the first kind as if
# it were the second is how a total Bedrock outage once rendered as a tidy 27%
# for two different models at once.
UPSTREAM_CODES = {"INTERNAL_ERROR", "UPSTREAM_TIMEOUT", "RATE_LIMITED"}


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    violations: list[str] = field(default_factory=list)
    metrics: PlanMetrics = field(default_factory=PlanMetrics)
    error_code: str | None = None

    @property
    def is_upstream_failure(self) -> bool:
        """The model never answered — infrastructure, not plan quality."""
        if self.error_code in UPSTREAM_CODES:
            return True
        # Any exception that escaped the graph. Deliberately NOT a list of
        # known network errors: the first version of this guard enumerated
        # ReadTimeout/ConnectTimeout/EndpointConnectionError, and the very
        # next run died on UnauthorizedSSOTokenError — an expired login — and
        # was duly reported as three models scoring 0%. An exception is never
        # a judgement about plan quality, whatever its type, so the rule is
        # the shape of the failure rather than a roster of causes that will
        # always be one entry behind reality.
        return any(v.startswith("raised ") for v in self.violations)


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

    @property
    def upstream_failures(self) -> int:
        return sum(1 for r in self.results if r.is_upstream_failure)

    @property
    def answered(self) -> int:
        """Cases where the model actually produced something to judge."""
        return len(self.results) - self.upstream_failures

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
        # Payable, so the reported utilisation matches the ceiling check.
        budget_used=(float(plan.payable_total_nzd / plan.budget_nzd) if plan.budget_nzd else 0.0),
        distinct_meals=len({m.name for m in plan.meals}),
        distinct_products=len({i.citation_ref for i in lines}),
        ingredient_lines=len(lines),
        repair_attempts=plan.repair_attempts,
    )


def _check_invariants(
    case: dict,
    plan: MealPlan | None,
    error_code: str | None,
    citations: dict,
    categories: dict[str, str],
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

    # Hard budget ceiling, measured in money the shopper actually pays.
    #
    # This checked total_nzd, the CONSUMPTION figure, which is smaller than
    # the shopping list whenever a recipe uses part of a pack -- almost
    # always. So a plan whose baskets came to $65.01 against a $60 budget
    # scored as within budget on a $34.39 consumption total, and the budget
    # invariant was measuring a number the user never pays.
    if plan.payable_total_nzd > plan.budget_nzd:
        v.append(
            f"over budget: payable ${plan.payable_total_nzd} > "
            f"${plan.budget_nzd} (consumption was ${plan.total_nzd})"
        )
    if not plan.within_budget:
        v.append("within_budget flag is False on a delivered plan")

    # Budget floor. within_budget alone would let an $8 plan for $30 pass.
    floor = expect.get("min_budget_used")
    if floor is not None and plan.budget_nzd:
        used = float(plan.payable_total_nzd / plan.budget_nzd)
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
                if citation and _category_of(citation, categories) in banned:
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


def _category_of(citation, categories: dict[str, str]) -> str:
    """
    The category of the product a citation points at.

    Was `citation.source.pk.split("#")[-1]`, on the stated assumption that the
    partition key is '<store>#<category>'. It is not: retrieve_prices sets
    pk to the record's `store_key`, which is '<store>#<location>'. So this
    returned 'sylvia-park' where 'dairy' was expected, and the exclusion check
    below compared store locations against category names -- two sets that
    never intersect.

    The consequence was a safety invariant that could not fail. Every
    meal-plan score ever recorded by this harness includes a dietary check
    that was silently vacuous, and a plan serving beef to someone who asked
    for vegetarian would have passed.

    The category lives on PriceRecord and nowhere on the wire, so it is looked
    up through the repository by the product key the citation already carries.
    """
    return categories.get(citation.source.sk, "")


def run(model: ModelClient, label: str) -> Scorecard:
    repo = InMemoryPriceRepository()
    # product_key -> category, so a cited product can be checked against
    # the dietary exclusions the case asked for.
    categories = {r.product_key: r.category for r in repo.all_records}
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
            results.append(CaseResult(case["id"], False, [f"raised {type(exc).__name__}: {exc}"]))
            continue

        plan = next((e.data for e in response.events if e.type == "meal_plan"), None)
        error_code = next((e.code.value for e in response.events if e.type == "error"), None)
        citations = {e.citation.ref: e.citation for e in response.events if e.type == "citation"}

        violations = _check_invariants(case, plan, error_code, citations, categories)
        metrics = _measure(plan, citations) if plan else PlanMetrics()
        results.append(CaseResult(case["id"], not violations, violations, metrics, error_code))

    return Scorecard(label, results)


class UpstreamOutage(RuntimeError):
    """Raised instead of returning a score the run did not actually measure."""


def assert_measured(card: Scorecard) -> None:
    """
    Refuse to report a pass rate when the model was never reached.

    A pass rate is a claim about model quality. If every case failed upstream,
    the run measured the network and the Bedrock configuration, and reporting
    a percentage invites exactly the mistake it caused once already: reading
    an outage as a model that scored badly, and worse, comparing two such
    numbers to each other as if the comparison meant something.

    Deliberately raises rather than exiting non-zero, so `--compare` cannot
    quietly drop one model and rank the survivor.
    """
    if card.answered == 0:
        first = card.results[0].violations
        raise UpstreamOutage(
            # ASCII only: this goes to stderr on a cp1252 Windows console,
            # where an em dash renders as a replacement character and makes a
            # diagnostic message look like corruption.
            f"{card.model_label}: all {len(card.results)} cases failed upstream. "
            f"The model was never reached, so there is no pass rate to report.\n"
            f"  First failure: {first[0] if first else 'unknown'}\n"
            f"  Check AWS credentials, the region, and BEDROCK_GUARDRAIL_ID."
        )


def report(card: Scorecard, spec: ModelSpec | None = None) -> None:
    print(f"\n=== {card.model_label} ===")
    print(f"  invariants  {card.pass_rate:.0%}  ({card.passed}/{len(card.results)})")
    if card.upstream_failures:
        # Partial outages still distort the score; the reader needs to know how
        # much of it is infrastructure before comparing it to anything.
        print(
            f"  WARNING: {card.upstream_failures}/{len(card.results)} cases failed "
            f"upstream (model never answered). The rate above understates quality "
            f"by up to {card.upstream_failures / len(card.results):.0%}."
        )
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


DEFAULT_MAX_RPM = 9


def pace_bedrock_calls(max_rpm: int) -> None:
    """
    Admit at most `max_rpm` Bedrock requests per minute, process-wide.

    ON BY DEFAULT, because the failure it prevents is a silently wrong result
    rather than an error. This account allows 10 cross-region requests per
    minute for both Claude models and 25 for Nova Pro. One rep of this suite
    fires 25-40 requests as fast as the harness can issue them, so an unpaced
    Claude run hits the wall part-way through and the TAIL of the case list
    fails with INTERNAL_ERROR -- which reads as "the model failed those cases"
    and is really "the account stopped answering".

    That is not hypothetical. Three consecutive bands scored Claude Haiku 4.5
    at 82-91% with every rep contaminated, while Nova Pro scored 100% clean on
    the same suite. Paced, Haiku scores 100% too. The gap was the quota, and
    comparing the two unpaced compares their request budgets rather than their
    planning.

    9/min rather than 10 leaves room for the retry the Bedrock client makes
    internally, which also counts against the limit.
    """
    if max_rpm <= 0:
        return

    import src.models.bedrock as bedrock_mod

    interval = 60.0 / max_rpm
    # -inf, not 0.0: the first call has nothing to wait for, and starting
    # at zero made it sleep a full interval before the run even began.
    last = [float("-inf")]
    original = bedrock_mod.BedrockModelClient._converse

    def paced(self, **kwargs):
        wait = interval - (time.monotonic() - last[0])
        if wait > 0:
            time.sleep(wait)
        last[0] = time.monotonic()
        return original(self, **kwargs)

    bedrock_mod.BedrockModelClient._converse = paced


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--compare", nargs="+")
    parser.add_argument(
        "--max-rpm",
        type=int,
        default=DEFAULT_MAX_RPM,
        help=(
            f"Bedrock requests per minute (default {DEFAULT_MAX_RPM}). The "
            f"account allows 10/min for Claude and 25/min for Nova Pro; "
            f"exceeding it fails the tail of the suite and reads as model "
            f"failure. 0 disables pacing -- only for a model you have "
            f"confirmed has the headroom."
        ),
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        help="Exit non-zero below this invariant pass rate. CI regression floor.",
    )
    args = parser.parse_args()

    keys = args.compare or ([args.model] if args.model else [])

    if not keys:
        card = run(ScriptedModelClient(), "scripted (no model call)")
        assert_measured(card)
        report(card)
        print(
            "\nBaseline only. The scripted planner picks by position, not by "
            "suitability, so treat this as a floor to beat rather than a target."
        )
        return _gate(card.pass_rate, args.min_pass_rate, card)

    from src.models.bedrock import BedrockModelClient
    from src.models.registry import ModelRegistry, RoutingPolicy

    pace_bedrock_calls(args.max_rpm)
    if args.max_rpm > 0:
        print(
            f"pacing Bedrock at {args.max_rpm} requests/min "
            f"(--max-rpm 0 to disable; see the note on pace_bedrock_calls)"
        )

    registry = ModelRegistry()
    cards: list[tuple[Scorecard, ModelSpec]] = []
    for key in keys:
        spec = registry.route("generate_plan", policy=RoutingPolicy.PINNED, pinned_key=key)
        card = run(BedrockModelClient(pinned_spec=spec), spec.display_name)
        try:
            assert_measured(card)
        except UpstreamOutage as exc:
            print(f"\nABORTED\n{exc}", file=sys.stderr)
            return 2
        report(card, spec)
        cards.append((card, spec))

    if len(cards) > 1:
        print("\n=== comparison ===")
        print(f"  {'model':<24} {'invariants':>11} {'budget':>8} {'variety':>8} {'upstream':>9}")
        for card, spec in sorted(cards, key=lambda c: -c[0].pass_rate):
            print(
                f"  {spec.display_name:<24} {card.pass_rate:>10.0%} "
                f"{card.mean('budget_used'):>7.0%} "
                f"{card.mean('distinct_meals'):>8.1f} "
                f"{card.upstream_failures:>9}"
            )

        # One run of 11 cases is a small, noisy sample: repeated runs of the
        # same model on this suite have differed by ~18 points. A gap narrower
        # than that is not evidence of anything, and saying so here is cheaper
        # than watching someone re-route production on it.
        rates = sorted((c.pass_rate for c, _ in cards), reverse=True)
        spread = (rates[0] - rates[1]) * len(cards[0][0].results)
        if spread < 2:
            print(
                "\n  These are within ~1 case of each other on an 11-case suite.\n"
                "  That is inside this eval's run-to-run noise — do not rank them\n"
                "  on a single run. Repeat each model before drawing a conclusion."
            )

    best_card = max((c for c, _ in cards), key=lambda c: c.pass_rate)
    return _gate(best_card.pass_rate, args.min_pass_rate, best_card)


def _gate(actual: float, floor: float | None, card: Scorecard | None = None) -> int:
    if floor is None:
        return 0
    # A gate is a claim that the code met a quality bar. A run with upstream
    # failures cannot support that claim in either direction: it would fail a
    # good model because Bedrock was slow, or — worse in CI, where the retry
    # is automatic — pass on a rate assembled from fewer cases than the suite
    # contains. Neither outcome is about the code under test.
    if card is not None and card.upstream_failures:
        print(
            f"\nINCONCLUSIVE: {card.upstream_failures}/{len(card.results)} cases "
            f"failed upstream, so {actual:.0%} is not a measurement of quality. "
            f"Re-run; do not treat this as a pass or a failure.",
            file=sys.stderr,
        )
        return 2
    if actual < floor:
        print(f"\nFAIL: pass rate {actual:.0%} is below the floor of {floor:.0%}")
        return 1
    print(f"\nOK: pass rate {actual:.0%} meets the floor of {floor:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
