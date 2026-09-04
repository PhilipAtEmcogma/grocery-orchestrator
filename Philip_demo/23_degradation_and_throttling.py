r"""
DEMO 23 - What the service says when the model cannot be reached
=================================================================

HOW TO RUN
----------
From the repository root:

    python Philip_demo/23_degradation_and_throttling.py

On Windows without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/23_degradation_and_throttling.py

No AWS account, credentials or network access.

MODES
-----
    local  (default and only)  the real graph, driven with a model client that
                               raises the way a throttled Bedrock client does.

WHAT THIS DEMONSTRATES
----------------------
  1. The ceiling that makes this reachable in normal operation
  2. Degradation: the model call fails and the keyword fallback answers
  3. The defect - a complete request answered with "tell me more"
  4. Why that was worse than a wrong error code
  5. The fix, and what it must NOT break
  6. Measured live, before and after

WHY THIS MATTERS MORE THAN IT LOOKS
-----------------------------------
The binding Bedrock quota here is Nova Lite at 20 requests per minute, and it
is NOT adjustable. A meal-plan turn costs three of them. So the service
ceiling is about 6.7 meal-plan turns a minute, service-wide, across all users
-- roughly 400 an hour. Throttling is not an exotic failure at this scale. It
is what a demo day looks like.

This file exists because "it degrades honestly" was a claim this repository
made for months and had never watched.
"""

from __future__ import annotations

from _demo_support import (
    LOCAL,
    ModeUnavailable,
    heading,
    mode_banner,
    note,
    request,
    resolve_mode,
    section,
    show_events,
    step,
)

from src.graph.nodes import route_after_intent
from src.models.base import ModelError
from src.models.scripted import ScriptedModelClient
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import Intent

repo = InMemoryPriceRepository()

try:
    mode = resolve_mode(supports=(LOCAL,))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

heading("DEMO 23 - What the service says when the model cannot be reached")
mode_banner(
    mode,
    requires="nothing - no AWS account, credentials or network access",
    mocked="the model plane; the graph and its routing are the real ones",
)

COMPLETE = "feed 3 people for 5 days on $80"


class ThrottledModel(ScriptedModelClient):
    """
    A model whose every call raises, the way a throttled Bedrock client does.

    Subclasses the scripted client rather than reimplementing the interface, so
    this stays about ROUTING and does not become a second implementation of the
    model contract that can drift from the real one.
    """

    def structured(self, *args, **kwargs):
        raise ModelError("throttled: too many requests")

    def text(self, *args, **kwargs):
        raise ModelError("throttled: too many requests")


# ----------------------------------------------------------- 1. the ceiling

section("1. The ceiling that makes this ordinary, not exotic")

note("Amazon Nova Lite: 20 requests/min in ap-southeast-2, NOT adjustable.")
note("(Claude's limits ARE adjustable. Nova is what production routes to,")
note(" so the reflex 'ask AWS to raise it' is unavailable here.)")
note("")
note("  price check                2 Nova Lite calls  -> 10.0 turns/min")
note("  meal plan, from a recipe   3 Nova Lite calls  ->  6.7 turns/min")
note("  meal plan, 2 repairs       5 Nova Lite calls  ->  4.0 turns/min")
note("")
note("Run scripts/check_quotas.py against the account for the live figures;")
note("docs/THROUGHPUT-AND-SCALING.md carries the reasoning and the options.")

# --------------------------------------------------------- 2. degradation

section("2. Degradation: the fallback answers instead")

note(f"message: {COMPLETE!r}")
note("It states household size, duration AND budget. Nothing is missing.")
note("")
good = run_turn(request(COMPLETE, turn="turn-deg001"), repo, ScriptedModelClient())
bad = run_turn(request(COMPLETE, turn="turn-deg002"), repo, ThrottledModel())

for label, resp in (("model reachable", good), ("model throttled", bad)):
    intent = next((e for e in resp.events if e.type == "intent"), None)
    terminal = next(
        (
            e.type
            for e in resp.events
            if e.type in ("meal_plan", "clarification", "error", "no_data")
        ),
        "?",
    )
    conf = getattr(intent, "confidence", None)
    note(f"  {label:18} intent confidence {conf}   ->  {terminal}")
note("")
note("Confidence 0.45 is the signature of the keyword fallback: the model call")
note("raised ModelError, classify_intent caught it, and heuristics answered.")
note("The turn still completes. Nothing looks broken from the outside.")

# ------------------------------------------------------------ 3. the defect

section("3. The defect this used to produce")

note("The keyword fallback extracts NO household size, NO duration and NO")
note("budget -- it classifies intent and little else. And")
note("missing_plan_constraints reads ABSENCE:")
note("")
note("    constraints.get('budget_nzd') is None  ->  'they did not tell us'")
note("")
note("So every constraint read as missing whatever the shopper actually wrote,")
note("and route_after_intent sent a COMPLETE request to emit_clarification.")
note("")
note("The shopper was asked to rephrase, and the remedy offered cannot work:")
note("rephrasing does not fix a throttle. Retrying does. They were told the")
note("fault was theirs.")

# ------------------------------------------------- 4. worse than a bad code

section("4. Why that was worse than a wrong error code")

note("emit_upstream_failure already exists to prevent exactly this shape one")
note("node further on. Its docstring:")
note("")
note('    Saying "I couldn\'t build a plan within $30 using current prices"')
note("    when Bedrock timed out is not a softer way of reporting an outage --")
note("    it is a false statement about their budget, and the alternatives it")
note("    offers (raise the budget, cut days) cannot possibly work.")
note("")
note("A clarification is the same false statement aimed at the MESSAGE instead")
note("of the budget. And its existing wording is already right for this case:")
note("'Your budget and preferences are fine -- please try again in a moment.'")

# -------------------------------------------------------------- 5. the fix

section("5. The fix, and the four things it must not break")

note("route_after_intent routes a DEGRADED meal-plan classification to")
note("upstream_failure instead of clarify. Watch it decide:")
note("")
cases = (
    (
        "degraded, constraints missing",
        {"intent": Intent.MEAL_PLAN, "constraints": {}, "intent_degraded": True},
    ),
    (
        "NOT degraded, genuinely under-specified",
        {"intent": Intent.MEAL_PLAN, "constraints": {}, "intent_degraded": False},
    ),
    (
        "degraded but constraints present",
        {
            "intent": Intent.MEAL_PLAN,
            "constraints": {"household_size": 3, "days": 5, "budget_nzd": 80},
            "intent_degraded": True,
        },
    ),
    (
        "degraded, unsupported dietary exclusion",
        {
            "intent": Intent.MEAL_PLAN,
            "constraints": {},
            "intent_degraded": True,
            "unsupported_exclusions": ["macadamia"],
        },
    ),
    (
        "degraded price check",
        {"intent": Intent.PRICE_CHECK, "constraints": {}, "intent_degraded": True},
    ),
)
for label, state in cases:
    note(f"  {label:42} -> {route_after_intent(state)}")
note("")
step(1, "a genuinely under-specified request still ASKS (Task 4 is not undone)")
step(2, "a degraded turn that HAS its constraints still retrieves")
step(3, "a dietary refusal still outranks it -- safety stays the reported reason")
step(4, "price checks are untouched: they carry no planning constraints")

section("The turn, after the fix")

show_events(bad)
note("")
note("Retryable, and it says the service failed rather than the shopper.")

# --------------------------------------------------------- 6. measured live

section("6. Measured live, before and after")

note("Found by Pilot Task 16 gate G6 Phase B, which breached the Nova Lite")
note("quota 21x on purpose. 24 turns, all sending the message at the top of")
note("this file:")
note("")
note("                          v12 (before)   v13 (after)")
note("  clarification              14 of 24         0")
note("  error, retryable            5 of 24        14")
note("  meal_plan                   2 of 24        10")
note("  contract-valid bodies         40/40     24/24")
note("")
note("Note what did NOT change and never had to: zero invented plans, zero")
note("malformed bodies, zero 5xx without a contract body. The stated criteria")
note("passed before the fix. It was the gate's PURPOSE that failed.")
note("")
note("Why it went unnoticed for so long: throttle a LATER call and the")
note("behaviour was already correct. Everything downstream working is exactly")
note("what hid it.")

print("\nDone.")
