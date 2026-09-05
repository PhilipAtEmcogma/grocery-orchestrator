r"""
DEMO 20 - Recipe selection: the half that runs BEFORE the model
===============================================================

HOW TO RUN
----------
From the repository root:

    python Philip_demo/20_recipe_selection.py

On Windows without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/20_recipe_selection.py

No AWS account, credentials or network access.

MODES
-----
    local  (default and only)  fixtures plus the scripted model client.

WHAT THIS DEMONSTRATES
----------------------
Pilot Task 15c, which reached production on 2026-09-04 and had never had a
demo. It is the capability that most distinguishes this system, and the
argument behind it is the one worth watching:

  1. A model that cannot SEE an uncostable recipe cannot select one
  2. The three filters, each a refusal rather than a substitution
  3. Why the budget is NOT enforced here, and where it is instead
  4. What the model is actually asked to do once code has done the hard part
  5. The bound on ingredient lookups, which refuses rather than truncates
  6. What changed in production when this path went live

THE SHAPE OF THE ARGUMENT
-------------------------
Most of this file is code choosing what the model is allowed to consider.
That is the point. `shortlist()` resolves every ingredient to a real product,
drops any recipe it cannot cost in full, judges dietary viability from the
RESOLVED products rather than from recipe names, and costs what survives.

Only then is a model asked anything, and what it is asked is narrow: pick ids,
for variety, from a list where every option is already correct. There is no
field in the response for a quantity or a price, so a model cannot introduce
one -- the same reasoning demo 3 shows for citations.
"""

from __future__ import annotations

from _demo_support import (
    LOCAL,
    ModeUnavailable,
    heading,
    mode_banner,
    money,
    note,
    request,
    resolve_mode,
    section,
    show_events,
    step,
)

from src.graph.recipe_plan import (
    MAX_INGREDIENT_LOOKUPS,
    curated_recipes,
    meals_needed,
    resolve_ingredients,
)
from src.models.scripted import ScriptedModelClient
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn

repo = InMemoryPriceRepository()
model = ScriptedModelClient()

try:
    mode = resolve_mode(supports=(LOCAL,))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

heading("DEMO 20 - Recipe selection: the half that runs BEFORE the model")
mode_banner(
    mode,
    requires="nothing - no AWS account, credentials or network access",
    mocked="the price store (fixtures) and the model plane (ScriptedModelClient)",
)

# ---------------------------------------------------------------- 1. catalogue

section("1. The curated catalogue, as data")

recipes = curated_recipes()
note(f"{len(recipes)} curated recipes in config/recipes.json")
note("")
note("These replaced 175 IMPORTED recipes that demo 11 measures at 0/175")
note("costable against both catalogues. The import was not tuned into")
note("working -- it was abandoned and replaced, which is the finding.")
note("")
for r in recipes[:5]:
    names = ", ".join(i.name for i in r.ingredients[:3])
    note(f"  {r.name:44} {len(r.ingredients)} ingredients: {names} ...")
note(f"  ... and {len(recipes) - 5} more")

# ------------------------------------------------------------- 2. meals needed

section("2. How many meals a request actually needs")

for household, days in ((2, 3), (3, 5), (1, 7)):
    n = meals_needed(recipes, household_size=household, days=days)
    note(f"household {household}, {days} days  ->  {n} meals")
note("")
note("A DAY IS NOT A MEAL. Deriving meals from days alone would either")
note("under-feed a household or invent portions nobody asked for, so the")
note("count comes from the recipes' own serving sizes.")

# ------------------------------------------------------- 3. the three filters

section("3. The three filters, in order, each a refusal")

step(1, "COSTABLE - every ingredient must resolve to a product we hold")
# Called exactly as src/graph/nodes/retrieval.py calls it: one lookup per
# DISTINCT ingredient key, not per recipe line. Filters are None here because
# this demo is not scoping to a region or a freshness window.
resolved = resolve_ingredients(repo, recipes, near=None, locations=None, freshness=None)
distinct_keys = {i.key for r in recipes for i in r.ingredients}
note(f"    {len(distinct_keys)} distinct ingredient keys across {len(recipes)} recipes")
note(f"    {len(resolved)} of them resolved to a real product in the catalogue")
note("    A recipe priced from four of its five ingredients states a payable")
note("    total the shopper cannot spend to. All-or-nothing, for that reason.")

step(2, "DIETARY-VIABLE - judged from the RESOLVED products, not the name")
note("    'Scrambled Eggs on Toast' excludes nothing by its name. Its resolved")
note("    products carry real categories, so the honest answer is available")
note("    and a guess is not needed.")

step(3, "COSTED, ordered cheapest first - but NOT filtered on price")
note("    The first version DID filter here, at budget/meals, and it was wrong:")
note("    assemble_plan aggregates packs across meals and rounds up ONCE, so")
note("    four recipes sharing onions and rice cost far less together than the")
note("    sum of the four costed alone. Against the 26-product fixture set that")
note("    cap collapsed a 29-recipe shortlist to ONE, and the meal-plan eval")
note("    read it as under-feeding -- which is what it was.")

note("")
note("    `shortlist()` itself needs the citation plumbing retrieval builds --")
note("    refs by record identity, so an offer carries refs the citation index")
note("    already knows. Section 6 runs the real path rather than rebuilding it")
note("    here, because a demo that reimplements the caller is testing itself.")
costable = [r for r in recipes if all(i.is_costable for i in r.ingredients)]
note("")
note(
    f"    recipes whose every ingredient carries a scalable quantity: "
    f"{len(costable)}/{len(recipes)}"
)
note("    (an imported recipe carries free text like '1 tbsp' and fails this)")

# -------------------------------------------------------- 4. where budget goes

section("4. So where IS the budget enforced?")

note("After the model has chosen, on the REAL aggregated total, by")
note("select_recipes trimming meals off the end until the plan fits.")
note("")
note("That is deterministic code owning the budget -- which is what Req 2.9")
note("asks for -- and it is the same correction the free-composition path")
note("already made when it moved the budget check from consumption to money")
note("payable. `payable_nzd` is still computed and carried, because ordering")
note("cheapest-first gives the trim something better than arbitrary to remove.")

# ------------------------------------------------------------- 5. the bound

section("5. The bound on lookups refuses rather than truncates")

note(f"MAX_INGREDIENT_LOOKUPS = {MAX_INGREDIENT_LOOKUPS}")
note("")
note("A shortlist silently built from the first N keys would offer recipes")
note("whose costability depends on dictionary ordering -- a different answer")
note("on a different day, with nothing to show why. So the bound raises")
note("TooManyIngredients instead, and the caller degrades honestly.")

# ------------------------------------------------------ 6. the whole turn

section("6. The whole path, on a real turn")

response = run_turn(
    request("feed 3 people for 5 days on $80", turn="turn-rs01"),
    repo,
    model,
)
show_events(response)

plan = next((e for e in response.events if e.type == "meal_plan"), None)
if plan is not None:
    curated_names = {r.name for r in recipes}
    chosen = [m.name for m in plan.data.meals]
    hits = [n for n in chosen if n in curated_names]
    note("")
    note(f"meals: {len(chosen)}, of which named curated recipes: {len(hits)}")
    for name in chosen:
        mark = "CURATED" if name in curated_names else "free composition"
        note(f"  [{mark:16}] {name}")
    note("")
    note(f"total {money(plan.data.total_nzd)}, payable {money(plan.data.payable_total_nzd)}")

# ------------------------------------------------------ 7. what changed live

section("7. What changed in production when this went live")

note("Measured on the deployed endpoint before and after, same message:")
note("")
note("  before (v11)  3 meals, 0 of 3 curated   Spinach and Prawn Stir-fry ...")
note("  after  (v12)  3 meals, 3 of 3 curated   Banana and Oat Porridge ...")
note("")
note("The membership test is the evidence, not the names sounding plausible:")
note("the 'before' names appear nowhere in config/recipes.json and all three")
note("'after' names do.")
note("")
note("Two consequences nobody predicted, both measured in Pilot Task 16:")
note("  - meal-plan p95 fell 11.7-12.2s -> 3.51s, because this path assembles")
note("    the plan deterministically and drops the Nova Pro generate_plan call")
note("  - a meal-plan turn got ~5x CHEAPER and the throughput ceiling FELL,")
note("    from 10.0 to 6.7 turns/min, because it makes three Nova Lite calls")
note("    where it used to make two")

print("\nDone.")
