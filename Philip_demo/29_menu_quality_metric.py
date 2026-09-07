r"""
DEMO 29 - When every model scores 100%, the suite has stopped measuring
=======================================================================

HOW TO RUN
----------
From the repository root:

    python Philip_demo/29_menu_quality_metric.py

On Windows without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/29_menu_quality_metric.py

No AWS account, credentials or network access. It runs the real eval harness
against the scripted client.

MODES
-----
    local  (default and only)  the scripted model client, offline.

WHAT THIS DEMONSTRATES
----------------------
Legacy task 5.6, and an eval-design problem worth more than the feature:

  1. A suite where every model scores 100% cannot rank anything
  2. The blind spot, CONSTRUCTED from the real catalogue
  3. Why the obvious fix was correctly refused for a year
  4. How normalising dissolves the objection instead of ignoring it
  5. The metric on a real run
  6. Why it is REPORTED and not a floor, and what is still missing

WHO THIS IS FOR
---------------
Anyone choosing between models, and anyone who has ever looked at a green
dashboard and wondered what it was actually telling them.

EXPECTED RESULT
---------------
Every check prints OK. The one to watch is section 2: a selection of five
DISTINCT recipes that is pasta five nights running, scoring 100% on every rule
check and 0.2 on variety. Exit code 0.
"""

from __future__ import annotations

from _demo_support import LOCAL, heading, mode_banner, note, require, resolve_mode, section

from evals.run_recipe_select import CaseResult, Scorecard, run
from src.graph.recipe_plan import curated_recipes
from src.models.scripted import ScriptedModelClient

mode = resolve_mode(supports=(LOCAL,))
mode_banner(mode, requires="nothing", mocked="the model client only")

heading("DEMO 29 - Scoring the menu, not just the rules")


# ------------------------------------------------------------ 1. the ceiling

section("1. A suite where everyone scores 100% has stopped measuring")

note("config/models.json recorded it, in its own words:")
note("")
note('    "BOTH MODELS SCORE 100% AND THE SUITE THEREFORE CANNOT RANK THEM.')
note("     Every check here is a RULE VIOLATION check -- did you invent an id,")
note("     did you repeat one while alternatives remained, did you breach a")
note("     stated exclusion, did you choose enough meals. Neither model breaks")
note('     rules. NOTHING HERE ASKS WHETHER THE MENU IS GOOD."')
note("")
note("Claude Haiku and Nova Lite both scored 100%, three reps each, zero")
note("fallbacks. The suite could not tell you which to route to.")


# --------------------------------------------------------- 2. the blind spot

section("2. The blind spot, constructed from the real catalogue")

by_main: dict[str, list[str]] = {}
for recipe in curated_recipes():
    if recipe.ingredients:
        by_main.setdefault(recipe.ingredients[0].key, []).append(recipe.recipe_id)

note(f"  {len(curated_recipes())} curated recipes, {len(by_main)} distinct main ingredients")
note("")
note("  mains with several recipes behind them:")
for main, ids in sorted(by_main.items(), key=lambda kv: -len(kv[1]))[:4]:
    if len(ids) > 1:
        note(f"    {main:14} {len(ids)} recipes   {', '.join(ids)}")

worst = max(by_main.items(), key=lambda kv: len(kv[1]))
note("")
note(f"So a model can return {len(worst[1])} DISTINCT recipe ids -- {', '.join(worst[1])} --")
note(f"and every one of them is {worst[0]}.")
note("")
note("  fabrication   passes: each was offered")
note("  dietary       passes")
note("  repetition    passes: the ids differ")
note("  count         passes: five meals")
note("  ------------------------------------------")
note(f"  RESULT        100% on every rule, and {worst[0]} five nights running")

require(len(worst[1]) >= 5, "the blind spot is no longer constructible")
note("")
note("  [OK  ] the blind spot is real and still constructible")
note("")
note("A test asserts that too, so if the catalogue ever changes such that it")
note("closes on its own, the metric's justification is re-examined rather than")
note("assumed.")


# ---------------------------------------------------- 3. the refused fix

section("3. Why the obvious fix was correctly refused")

note("`distinct mains` was already REPORTED. Scoring it was refused, and the")
note("reason was right:")
note("")
note('    "no threshold on variety is right for every request -- three meals')
note("     from a seven-recipe shortlist cannot beat four from a twelve-recipe")
note('     one, and scoring it would manufacture a gradient"')
note("")
note("Under an absolute count, 3 < 4, so the narrower request loses for")
note("reasons that have nothing to do with the model. Averaging or")
note("thresholding that number would produce a ranking that means nothing.")


# -------------------------------------------------- 4. normalising

section("4. Normalising dissolves the objection rather than ignoring it")

note("The ceiling is COMPUTABLE. With n meals chosen from a shortlist holding")
note("m distinct mains, no selection can show more than min(n, m).")
note("")
note("  variety = distinct mains taken / min(meals chosen, mains available)")
note("")
note("Which asks a question that IS comparable across every case:")
note("")
note("      of the variety available to you, how much did you take?")
note("")


def card(*pairs: tuple[int, int]) -> Scorecard:
    sc = Scorecard("demo")
    for i, (distinct, achievable) in enumerate(pairs):
        sc.results.append(
            CaseResult(
                case_id=f"c{i}", passed=True, distinct_mains=distinct, achievable_mains=achievable
            )
        )
    return sc


rows = [
    ("five meals, five mains", (5, 5), 1.0, "took everything available"),
    ("five meals, ALL PASTA", (1, 5), 0.2, "the blind spot, scored"),
    ("three of five taken", (3, 5), 0.6, "the ceiling bites"),
    ("narrow shortlist, all taken", (3, 3), 1.0, "not punished for being narrow"),
    ("wide shortlist, all taken", (4, 4), 1.0, "...and neither is this one"),
]
for label, pair, expect, why in rows:
    got = card(pair).variety
    mark = "OK  " if got is not None and abs(got - expect) < 1e-9 else "FAIL"
    note(f"  [{mark}] {label:30} -> {got:.1%}   ({why})")
    require(
        got is not None and abs(got - expect) < 1e-9, "got is not None and abs(got - expect) < 1e-9"
    )

note("")
note("THE FOURTH AND FIFTH ROWS ARE THE OBJECTION, ANSWERED. Three-of-three and")
note("four-of-four both score 1.0, because neither could have done better --")
note("which is the only defensible reading, and exactly what the raw count")
note("could never say.")

note("")
note("It is also ORTHOGONAL TO COUNT. The denominator uses meals actually")
note("SELECTED, so under-selecting is not punished twice -- COUNT scores that,")
note("and one check measuring one thing is what makes a failure legible.")
note(f"    two meals, both different -> {card((2, 2)).variety:.1%}, not a penalty")

note("")
note("And an average over NO observations is absent, not zero:")
note(f"    a model that selected nothing -> {card((0, 0)).variety}")
note("Reporting 0.0 would rank an UNMEASURED model below a bad one -- the same")
note("distinction treat_missing_data draws for the alarms.")


# ------------------------------------------------------- 5. a real run

section("5. The metric on a real run of the eval harness")

scorecard = run(ScriptedModelClient(), "scripted")
variety = scorecard.variety
scored = scorecard.scored

note(f"  cases scored     {len(scored)}")
note(f"  pass rate        {scorecard.pass_rate:.1%}   <- saturated, as before")
mains_avg = sum(r.distinct_mains for r in scored) / len(scored)
note(f"  distinct mains   {mains_avg:.1f}   <- not comparable")
note(f"  variety taken    {variety:.1%}   <- comparable, and rankable")
require(
    variety is not None and 0.5 < variety <= 1.0, "variety is not None and 0.5 < variety <= 1.0"
)
note("")
note("  [OK  ] the metric is wired into the real harness, not just correct in")
note("         arithmetic -- which is the failure mode select_recipes itself")
note("         had for five days")


# -------------------------------------------------- 6. reported, not floored

section("6. Why it is reported and NOT a floor")

note("Because 'variety is desirable' is an ASSUMPTION this product never")
note("tests. It does not ask a shopper whether they would rather batch-cook")
note("one thing five times and save the effort.")
note("")
note("So the number ranks models against each other and is not a gate. The")
note("honest order is: measure a baseline across the enabled models, then")
note("argue for a threshold -- the same discipline CI-GATE-HEALTH 1 applies to")
note("the eval floors, where the rule is 'raise a floor from a re-measurement,")
note("or not at all'.")
note("")
note("WHAT IS STILL MISSING: appeal. It is the other half of task 5.6 and it")
note("is deliberately NOT attempted. It needs a human judgement or an LLM")
note("judge with its own qualification, and inventing a proxy would manufacture")
note("exactly the meaningless gradient the variety objection warned about.")
note("")
note("The per-model figures need a live run, which costs money and has not")
note("been taken.")

print("\nDone.")
