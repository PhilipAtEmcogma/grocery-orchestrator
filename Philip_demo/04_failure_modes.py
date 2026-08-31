r"""
DEMO 4 - Failure modes: saying the true thing when things go wrong
==================================================================

HOW TO RUN
----------
    python Philip_demo/04_failure_modes.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/04_failure_modes.py

MODES
-----
    local  (default and only)  fixtures plus the scripted model client, with failing
                               model stubs injected deliberately. No AWS,
                               no credentials, no network.

    Asking for another mode exits without running anything, rather than
    quietly answering from fixtures. See Philip_demo/README.md.

WHAT THIS DEMONSTRATES
----------------------
Every terminal failure path, and - the point of the file - that each one says
something TRUE about what actually happened:

  1. NO_DATA               nothing matched the request
  2. UNSUPPORTED_EXCLUSION a dietary term we cannot verify
  3. BUDGET_INFEASIBLE     a costed plan that genuinely will not fit
  4. PLAN_GENERATION_FAILED  repair ran out on invalid drafts
  5. UPSTREAM_TIMEOUT / INTERNAL_ERROR  the model plane failed
  6. Retryability, and why it differs between these

WHY THIS FILE EXISTS
--------------------
These used to collapse into one another. An unreachable Bedrock endpoint was
reported as BUDGET_INFEASIBLE, so a user whose model call had timed out was
told "I couldn't build a plan within $30 ... would you like to raise the
budget?" - advice that could not help, on a budget that was never the
problem. Repair exhausting on malformed drafts said the same thing.

A failure message is a claim about the world. These paths exist so that each
claim is true.
"""

from __future__ import annotations

from decimal import Decimal

from _demo_support import (
    LOCAL,
    ModeUnavailable,
    heading,
    mode_banner,
    request,
    resolve_mode,
    section,
)

from src.graph.nodes.plan import PLAN_TASKS
from src.models.base import ModelError, ModelOutputInvalid
from src.models.scripted import ScriptedModelClient
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import ErrorCode

repo = InMemoryPriceRepository()

try:
    mode = resolve_mode(supports=(LOCAL,))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

heading("DEMO 4 - Failure modes")
mode_banner(
    mode,
    requires="nothing - no AWS account, credentials or network access",
    mocked=(
        "the price store (fixtures) and the model plane (scripted, plus stubs that fail on purpose)"
    ),
)


def terminal(resp) -> tuple[str, bool, str]:
    """The error event a turn ended on, if any."""
    err = next((e for e in resp.events if e.type == "error"), None)
    if err is None:
        return ("(no error - turn succeeded)", False, "")
    return (err.code.value, err.retryable, err.message)


class UnreachableModel(ScriptedModelClient):
    """The endpoint is down. Nothing to repair; retrying the prompt is futile."""

    def __init__(self, message: str, **kw):
        super().__init__(**kw)
        self.message = message

    def structured(self, **kw):
        if kw.get("task") in PLAN_TASKS:
            raise ModelError(self.message)
        return super().structured(**kw)


class MalformedDraftModel(ScriptedModelClient):
    """The model answers; the answer will not satisfy PlanDraft."""

    def structured(self, **kw):
        if kw.get("task") in PLAN_TASKS:
            raise ModelOutputInvalid("PlanDraft failed validation: reasoning too long")
        return super().structured(**kw)


# ------------------------------------------------------------------- no data
section("1. NO_DATA - nothing matched")
resp = run_turn(request("price of truffle oil"), repo, ScriptedModelClient())
no_data = [e for e in resp.events if e.type == "no_data"]
for ev in no_data:
    print(f"  no_data event: {ev.requested_item} -> {ev.message}")
print("\n  Note this is an EVENT, not an error code. Having no price for")
print("  truffle oil is a fact about the catalogue, not a failure of the")
print("  request, and the contract says so explicitly.")

# ------------------------------------------------------- unsupported exclusion
section("2. UNSUPPORTED_EXCLUSION - a term we cannot verify")
resp = run_turn(
    request(
        "gluten-free meal plan",
        turn="turn-fail02",
        household_size=2,
        budget_nzd=60,
        days=3,
        dietary_exclusions=["gluten-free"],
    ),
    repo,
    ScriptedModelClient(),
)
code, retryable, message = terminal(resp)
print(f"  {code}  retryable={retryable}")
print(f"  {message}\n")
print("  Refused BEFORE retrieval and generation. We have no per-product")
print("  allergen data, so we cannot verify the plan, and dropping a dietary")
print("  restriction is the dangerous direction of error.")

# ------------------------------------------------------------ budget infeasible
section("3. BUDGET_INFEASIBLE - a costed plan that genuinely will not fit")
resp = run_turn(
    request("plan dinners", turn="turn-fail03", household_size=2, budget_nzd=5, days=3),
    repo,
    ScriptedModelClient(plan_packs=Decimal("5")),
)
code, retryable, message = terminal(resp)
print(f"  {code}  retryable={retryable}")
print(f"  {message}\n")
print("  This one has EARNED the right to talk about the budget: a plan was")
print("  actually built and priced, and it came out over. retryable=False,")
print("  because a budget that does not stretch will not stretch on a retry.")
print("  The failing plan is not also emitted - showing an over-budget list")
print("  beside 'I could not make one' would be incoherent.")

# ------------------------------------------------------- generation failure
section("4. PLAN_GENERATION_FAILED - repair ran out on invalid drafts")
resp = run_turn(
    request("plan dinners", turn="turn-fail04", household_size=2, budget_nzd=500, days=3),
    repo,
    MalformedDraftModel(),
)
code, retryable, message = terminal(resp)
print("  Budget offered: $500 - ample.")
print(f"  {code}  retryable={retryable}")
print(f"  {message}\n")
print("  Before this path existed, the same situation ended on")
print("  BUDGET_INFEASIBLE and told this user to raise a $500 budget.")
print("  retryable=True: generation is non-deterministic, so another")
print("  attempt may well work.")

# ---------------------------------------------------------- upstream failure
section("5. The model plane failed - two flavours")
for label, message_text in [
    ("timeout", "Bedrock call failed: Read timeout on endpoint URL"),
    ("misconfiguration", "BEDROCK_GUARDRAIL_ID is not set and REQUIRE_GUARDRAIL is on"),
]:
    resp = run_turn(
        request(
            "plan dinners", turn=f"turn-fail{label[:2]}9", household_size=2, budget_nzd=60, days=3
        ),
        repo,
        UnreachableModel(message_text),
    )
    code, retryable, msg = terminal(resp)
    print(f"  {label:<16} -> {code:<24} retryable={retryable}")
print(f"\n  User-facing message: {msg}")
print("\n  Note what is NOT in there: 'BEDROCK_GUARDRAIL_ID is not set' is")
print("  operator detail and stays in the log. A shopper cannot act on it.")
print("  The repair loop is skipped entirely - re-prompting a client we")
print("  already know is failing only spends the latency budget.")

# ------------------------------------------------------------- retryability
section("6. Retryability is a real signal, not decoration")
print(f"  {'code':<26} {'retryable':<10} what a retry would achieve")
print(f"  {'-' * 26} {'-' * 10} {'-' * 34}")
rows = [
    (ErrorCode.UNSUPPORTED_EXCLUSION, False, "nothing - we still lack the data"),
    (ErrorCode.BUDGET_INFEASIBLE, False, "nothing - the budget is the budget"),
    (ErrorCode.PLAN_GENERATION_FAILED, True, "may work - generation varies"),
    (ErrorCode.UPSTREAM_TIMEOUT, True, "may work - transient"),
]
for code_enum, retry, effect in rows:
    print(f"  {code_enum.value:<26} {retry!s:<10} {effect}")

print("\n  A client that renders all four as 'something went wrong, try again'")
print("  wastes the user's time on two of them and hides an actionable")
print("  alternative on the other two.")
print("\nDone.")
