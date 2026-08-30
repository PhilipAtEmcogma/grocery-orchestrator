r"""
DEMO 18 - The evaluation layer, and the gate that stops an unmeasured route
============================================================================

HOW TO RUN
----------
    python Philip_demo/18_evaluation_and_qualification.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/18_evaluation_and_qualification.py

MODES
-----
    local  (default and only)  runs all five harnesses against the SCRIPTED
                               client. No AWS, no credentials, no network,
                               no spend. Live scoring is a separate,
                               deliberately manual operation - see section 6.

WHAT THIS DEMONSTRATES
----------------------
  1. Five suites, and what each one can and cannot tell you
  2. Running them - the real harnesses, the real cases, offline
  3. What a SCRIPTED guardrail run proves, and what it does not
  4. The scorecards: measured evidence, stored as data
  5. The qualification gate - no route a turn can reach may be unmeasured
  6. Pacing, and the three model bands that were scored against a quota

THE DISTINCTION THIS FILE IS ABOUT
----------------------------------
Unit tests answer "does the code do what it says". Evals answer "is the model
actually any good at this, and which one, at what cost". They are different
questions, and the second one cannot be answered by a test suite.

But an eval result is only about the model and configuration it was collected
under. A scorecard with no run behind it is a claim; a scorecard collected
against DRAFT describes a policy that has since moved; a suite fired as fast
as it can measures the account's quota rather than the model. Each of those
happened here, and each is why some part of this file exists.

ARCHITECTURE
------------
    evals/cases/*.json          golden cases, with `note` and `known_gap`
        v
    evals/run_*.py              the harness, driving the REAL code path
        |                         intent   -> classify_intent node
        |                         meal_plan-> the whole graph
        |                         repair   -> generate_plan with attempts set
        |                         prose    -> generate_prose node
        |                         guardrail-> lambda_handler, end to end
        v
    a scorecard                 rate, sample size, reps
        v
    config/models.json _scorecards
        v
    ModelRegistry.unscored_routes()   <- the gate. Must be empty.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from _demo_support import (
    LOCAL,
    ModeUnavailable,
    heading,
    mode_banner,
    note,
    resolve_mode,
    section,
    step,
)

from src.models.registry import ModelRegistry

try:
    mode = resolve_mode(supports=(LOCAL,))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "evals" / "cases"

heading("DEMO 18 - The evaluation layer, and the qualification gate")
mode_banner(
    mode,
    requires="nothing - the harnesses run against the scripted client",
    mocked=(
        "the model plane. A scripted run measures the HARNESS and the code path, never a model."
    ),
)

registry = ModelRegistry()

SUITES = (
    (
        "intent",
        "run_intent.py",
        "classification and constraint extraction",
        "deterministic scoring - intent and constraints have correct answers",
    ),
    (
        "meal_plan",
        "run_meal_plan.py",
        "the whole graph, end to end",
        "invariants are pass/fail; quality metrics are reported, not scored",
    ),
    (
        "repair",
        "run_repair.py",
        "can a model fix a plan it did not write",
        "budget repair and defect repair scored separately",
    ),
    (
        "prose",
        "run_prose.py",
        "can a model follow the placeholder protocol",
        "rule-violation checks, not 'is the sentence nice'",
    ),
    (
        "guardrail",
        "run_guardrail.py",
        "the red-team set through lambda_handler",
        "must_block needs a LIVE run; scripted proves only the harness",
    ),
)

# ------------------------------------------------------------- the suites
section("1. Five suites, and what each measures")
for name, script, what, how in SUITES:
    cases = json.loads((CASES / f"{name}.json").read_text(encoding="utf-8"))
    count = len(cases if isinstance(cases, list) else cases.get("cases", []))
    print(f"  {name:<11} {count:>3} cases   evals/{script}")
    print(f"              {what}")
    print(f"              {how}\n")

sample = json.loads((CASES / "intent.json").read_text(encoding="utf-8"))
first = (sample if isinstance(sample, list) else sample["cases"])[0]
print("  One case, as committed:\n")
print("    " + json.dumps(first, indent=4).replace("\n", "\n    ")[:600])
note("")
note("`resolves_to` is asserted rather than the raw extracted string. Asserting")
note("the model returned exactly 'butter' would be brittle and would not")
note("measure what matters, which is whether RETRIEVAL finds the right product.")

# ------------------------------------------------------------- running them
section("2. Running all five, offline")
results: list[tuple[str, int, str]] = []
for name, script, _, _ in SUITES:
    step(len(results) + 1, f"evals/{script}")
    # S603: argv is this interpreter plus a filename from the SUITES tuple
    # above. No shell, and nothing here comes from user input.
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "evals" / script)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=600,
        check=False,
    )
    body = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    headline = next(
        (
            ln.strip()
            for ln in body
            if any(
                k in ln
                for k in (
                    "accuracy",
                    "invariants",
                    "repair success",
                    "rule compliance",
                    "must_allow",
                )
            )
        ),
        "(no headline line)",
    )
    results.append((name, proc.returncode, headline))
    print(f"      exit {proc.returncode}   {headline}")

print(f"\n  {'suite':<12} {'exit':<5} headline")
print(f"  {'-' * 12} {'-' * 5} --------")
for name, code, headline in results:
    print(f"  {name:<12} {code:<5} {headline}")
note("")
note("These are BASELINES, and each harness says so in its own last line. The")
note("scripted client is rule-based, so what passed above is the harness, the")
note("cases and the code path - not a model.")
note("")
note("The intent baseline is deliberately NOT 100%: the scripted extractor")
note("cannot resolve every phrasing, and tuning it until the golden set went")
note("green would make the number meaningless. A floor to beat is more useful")
note("than a ceiling that flatters.")

# ------------------------------------------------------------ the guardrail
section("3. What a scripted guardrail run proves, and what it does not")
print("  The scripted client never reaches Bedrock, so no Guardrail can")
print("  intervene and EVERY must_block case comes back allowed.\n")
print("  A scripted run therefore proves:")
print("    - the harness parses its cases")
print("    - the handler answers all twenty")
print("    - no legitimate grocery question is refused\n")
print("  It is NOT evidence about the policy, and must_block is not gated for")
print("  it. That is why the run above reports 'STRUCTURAL run - must_allow")
print("  only' rather than a score.")
note("")
note("Live acceptance is 13/13 must_block AND 7/7 must_allow against a")
note("NUMBERED Guardrail version:")
note("")
note("    python evals/run_guardrail.py --model nova-lite")
note("")
note("Which needs credentials, a guardrail id and a numbered version - and")
note("that last word is load-bearing. The deployed function applied version 1")
note("for days while the recorded evidence described version 2, and a")
note("documented must_allow case was being refused in production while the")
note("record said 9/9. Demo 17 section 9 is where you can now see which")
note("version a running service has.")

# ------------------------------------------------------------- scorecards
section("4. The scorecards: measured evidence, stored as data")
print(f"  score floor: {registry.score_floor:.0%}\n")
print(f"  {'task':<18} {'model':<16} {'rate':>7}  {'n':>4}  reps  qualifies")
print(f"  {'-' * 18} {'-' * 16} {'-' * 7}  {'-' * 4}  ----  ---------")
for task in ("classify_intent", "generate_plan", "repair_plan", "generate_prose"):
    for key in registry.routable_models(task):
        card = registry.scorecard(task, key)
        if card is None:
            print(f"  {task:<18} {key:<16} {'-':>7}  {'-':>4}  {'-':<4}  NO SCORECARD")
            continue
        rate = float(card.get("rate", 0.0))
        print(
            f"  {task:<18} {key:<16} {rate:>6.1%}  {card.get('scored', '?'):>4}  "
            f"{card.get('reps', '-')!s:<4}  {'yes' if rate >= registry.score_floor else 'NO'}"
        )
note("")
note("Per TASK, not per model. A model can be excellent at classification and")
note("below the floor on planning, and a single overall score would hide that.")
note("`reps` matters as much as `rate`: one clean run of eleven cases is not")
note("the same evidence as three.")

# ----------------------------------------------------------------- the gate
section("5. The gate: no reachable route may be unmeasured")
unscored = registry.unscored_routes()
unevidenced = registry.unevidenced_models()
print(f"  unscored_routes()      {unscored or '[]  <- empty, which is the pass'}")
print(f"  unevidenced_models()   {unevidenced or '[]  <- empty, which is the pass'}")
print(f"  unscored_tasks()       {registry.unscored_tasks() or '{}'}")
note("")
note("`routable_models(task)` is NOT just the preference list. When no")
note("preferred model is eligible, route() falls through to available(tier)")
note("sorted by cost, so any enabled model declaring that tier is a candidate.")
note("Reading only the preference list is how claude-sonnet once sat as a live")
note("fallback for EVERY task while being documented as unfit.")

print("\n  Which is also why a routing rule can now name `exclude`:\n")
for task in ("classify_intent", "generate_plan", "repair_plan", "generate_prose"):
    print(f"    {task:<18} reachable: {registry.routable_models(task)}")
note("")
note("`enabled: false` fixed the Sonnet case only because Sonnet was unfit")
note("EVERYWHERE. Per-task scoring implies a per-task exclusion, and the")
note("config could not previously express it. exclude is honoured in route()")
note("AND in routable_models(), because a gate reporting a pair no turn can")
note("reach fails the build on a route that does not exist.")

# ------------------------------------------------------------------ pacing
section("6. Pacing, and the numbers that were really the quota")
print("  evals/_pacing.py wraps BedrockModelClient._converse process-wide at")
print("  9 requests per minute - 9 rather than 10, because the client's own")
print("  internal retry also counts against the limit.\n")
print("  Wrapping the CLIENT rather than threading a delay through each")
print("  harness, because the call sites are inside the graph: one turn makes")
print("  several model calls and only the client sees all of them.")
note("")
note("Three consecutive meal-plan bands scored Claude Haiku 4.5 at 82-91%")
note("with every rep contaminated, while Nova Pro scored 100% clean on the")
note("same suite. Paced, Haiku scores 100% too. The gap was the request")
note("budget, not the model.")
note("")
note("On the guardrail suite an unpaced tail does not merely lower a score -")
note("it makes the Guardrail appear to have let unsafe content through.")

# ------------------------------------------------------------- known gaps
section("7. Two fields that keep the golden sets honest")
gaps: list[tuple[str, str, str]] = []
notes: list[tuple[str, str, str]] = []
for name, _, _, _ in SUITES:
    raw = json.loads((CASES / f"{name}.json").read_text(encoding="utf-8"))
    cases = raw if isinstance(raw, list) else raw.get("cases", [])
    for case in cases:
        if not isinstance(case, dict):
            continue
        if case.get("known_gap"):
            gaps.append((name, case.get("id", "?"), str(case["known_gap"])[:80]))
        if case.get("note"):
            notes.append((name, case.get("id", "?"), str(case["note"])[:96]))

print(f"  known_gap   {len(gaps):>3} cases   scored SEPARATELY, not against the rate")
print(f"  note        {len(notes):>3} cases   why this case is worded the way it is\n")
if gaps:
    for suite, case_id, why in gaps[:8]:
        print(f"    {suite:<11} {case_id:<14} {why}")
else:
    print("  No case currently carries a known_gap. The mechanism is live in")
    print("  run_intent.py - known_gap results are excluded from the accuracy")
    print("  denominator and listed on their own - and the committed sets have")
    print(
        "  simply had theirs closed. Read that as 'nothing is being carried",
    )
    print("  quietly', not as 'the field is unused'.\n")

print("  A sample of the notes, which are the eval-discipline record:\n")
for suite, case_id, why in notes[:6]:
    print(f"    {suite:<11} {case_id:<14} {why}")
note("")
note("Changing a case's expectations without recording why is how a suite")
note("stops measuring. One repair case, rb-003, had been FAILING via the")
note("unsupported-exclusion path rather than by overspending - scored as a")
note("budget case while testing nothing about budgets. Its budget was lowered")
note("to where a normal plan fits and a 3x-pack plan does not, and the `note`")
note("says so. That is not lowering a bar a model failed to clear; the case")
note("never exercised its own kind.")

section("8. What was NOT measured by this run")
note("No model was scored. Every number above came from the scripted client")
note("or from scorecards recorded by earlier live runs.")
note("")
note("Live scoring is deliberately a separate, manual operation: it costs")
note("money, it is paced, and it must name the model and the guardrail")
note("version it was collected under.")
note("")
note("    python evals/run_intent.py     --compare nova-lite claude-haiku nova-pro")
note("    python evals/run_meal_plan.py  --model nova-pro")
note("    python evals/run_repair.py     --model nova-lite")
note("    python evals/run_prose.py      --model nova-lite")
note("    python evals/run_guardrail.py  --model nova-lite")

print("\nDone.")
