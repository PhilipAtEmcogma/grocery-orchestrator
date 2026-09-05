r"""
DEMO 11 - The recipe catalogue, and the gate that keeps it out of the graph
===========================================================================

HOW TO RUN
----------
    python Philip_demo/11_recipe_coverage_gate.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/11_recipe_coverage_gate.py

MODES
-----
    local  (default and only)  reads datasets/data/dynamodb_recipe_batches/
                               and fixtures/products.json, both committed.
                               No AWS, no credentials, no network.

WHAT THIS DEMONSTRATES
----------------------
  1. The imported recipe catalogue that exists and is loaded
  2. Why it was NOT wired into the graph, measured rather than asserted
  3. Ingredient coverage: what fraction of a recipe can actually be priced
  4. What is missing, by frequency, and what that says about the two datasets
  5. The staples list, why it is tiny, and the experiment that proved it
  6. The forcing function: a test fails if this silently becomes good enough
  7. What was done instead, and why it is curation rather than gaming

THE DECISION THIS FILE RECORDS
------------------------------
Req 2.9 says a meal plan SHALL select meals from a curated catalogue rather
than composing them freely. The catalogue is here. The classification is here.
The planner is not, and this is the measurement that says why:

A plan built from a recipe whose ingredients are 15% priced states a payable
total computed from a sixth of what the shopper has to buy. That number is not
an estimate, it is wrong, and `within_budget` derived from it is a false
promise - the single failure mode this codebase exists to prevent.

Refusing to plan is the honest outcome. Shipping the planner without the data
would replace an honest refusal with a confident lie.

UPDATED 2026-08-31, AND THE UPDATE IS THE INTERESTING PART
----------------------------------------------------------
Every measurement below still holds: the imported TheMealDB catalogue really
cannot be priced against this product data, and that has not improved. What
changed is the response. Pilot Task 15b stopped trying to make 175 imported
recipes fit and wrote 29 against the catalogue we actually have, every
ingredient priceable by construction - see section 7, which measures both so
you can compare them rather than take this paragraph's word for it.

So this file is no longer "the planner does not exist". It is the record of WHY
the planner does not use these recipes, which is a different and more useful
claim - and it is still a negative result, because the imported catalogue is
still unusable and section 7 shows exactly how far off it is.

ARCHITECTURE
------------
    datasets/data/dynamodb_recipe_batches/   TheMealDB, collected by the data team
        v
    FixtureRecipeRepository.all_recipes()
        v
    coverage(recipes, resolve)   <- `resolve` is INJECTED, and is the same
        v                           resolution the service really performs
    RecipeCoverage per recipe
        v
    usable_recipes(minimum_ratio=1.0)   ->  zero, then and now
"""

from __future__ import annotations

import collections
import statistics

from _demo_support import (
    LOCAL,
    ModeUnavailable,
    heading,
    mode_banner,
    note,
    resolve_mode,
    section,
)

from src.recipes import (
    ASSUMED_ON_HAND,
    CuratedRecipeRepository,
    FixtureRecipeRepository,
    coverage,
    recipe_excluded_categories,
    usable_recipes,
)
from src.retrieval.memory import InMemoryPriceRepository

try:
    mode = resolve_mode(supports=(LOCAL,))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

heading("DEMO 11 - The recipe catalogue, and the gate that keeps it out of the graph")
mode_banner(
    mode,
    requires="nothing - both datasets are committed",
    mocked="nothing. This is the real catalogue measured against the real resolver.",
)

repo = InMemoryPriceRepository()


def real_catalogue_resolver():
    """
    Resolve an ingredient term against the REAL catalogue, not the fixture.

    The collected dataset through the same synonym table `resolve_product_key`
    uses, so what this resolves is what a turn resolves. Returns None if the
    dataset is not present, and section 7 says so rather than quietly reporting
    a fixture number under a real-catalogue heading.

    The load moved to `src/recipes/catalogue.py` on 2026-08-31. It used to be a
    copy of the one in `tests/test_curated_recipes.py`, and a third variant sat
    in `scripts/check_recipe_coverage.py` reading the fixture catalogue while
    reporting under a real-catalogue heading -- which is the defect the audit
    of 2026-08-30 recorded as D3. One loader now, and it names itself.
    """
    from src.recipes.catalogue import load_dataset_catalogue
    from src.retrieval.memory import load_synonyms

    catalogue = load_dataset_catalogue()
    if catalogue is None:
        return None

    synonyms = load_synonyms()
    return lambda term: catalogue.resolve(term, synonyms)


recipes = FixtureRecipeRepository().all_recipes()


def resolve(term: str) -> str | None:
    """
    Resolve exactly as the service does, plus singular/plural of the whole term.

    Copied in shape from scripts/check_recipe_coverage.py. The extra forms are
    the recipe side's problem, not the shopper's: a recipe writes `onion` where
    the catalogue sells `Brown Onions`. Being generous here can only make
    coverage look BETTER, so a bad number under this resolver is a floor.
    """
    for candidate in (term, f"{term}s", term[:-1] if term.endswith("s") else term):
        key = repo.resolve_product_key(candidate)
        if key:
            return key
    return None


# ------------------------------------------------------------- what is here
section("1. The catalogue that exists")
ingredients = {i.key for r in recipes for i in r.ingredients}
areas = collections.Counter(r.area for r in recipes)
categories = collections.Counter(r.category for r in recipes)
print(f"  recipes                {len(recipes)}")
print(f"  distinct ingredients   {len(ingredients)}")
print(f"  median ingredient count {statistics.median(len(r.ingredients) for r in recipes):.0f}")
print(f"  cuisines               {len(areas)}  e.g. {[a for a, _ in areas.most_common(6)]}")
top_categories = [c for c, _ in categories.most_common(6)]
print(f"  categories             {len(categories)}  e.g. {top_categories}")

example = recipes[0]
print("\n  One recipe, as loaded:")
print(f"    recipe_id    {example.recipe_id}")
print(f"    name         {example.name}")
print(f"    category     {example.category}  ({example.area})")
print(f"    attribution  {example.attribution[:60]}...")
print(f"    ingredients  {len(example.ingredients)}")
for ingredient in example.ingredients[:6]:
    print(f"      {ingredient.key:<24} {ingredient.measure!r}")
note("")
note("`measure` is deliberately NOT parsed. Scaling a recipe is Req 2.9 work")
note("that only matters once ingredients can be priced, and a parser written")
note("against measures nobody can cost would be untested speculation.")

# ------------------------------------------------------------ the safety map
section("2. Dietary classification, which the catalogue does carry")
for recipe in recipes[:6]:
    excluded = recipe_excluded_categories(recipe)
    print(f"  {recipe.name[:38]:<38} -> {sorted(excluded) or '(none)'}")
note("")
note("Same category vocabulary as src/graph/dietary.py, so a recipe and a")
note("product speak the same safety language. This part is finished; it is")
note("the pricing that is not.")

# ----------------------------------------------------------------- coverage
section("3. Coverage - what fraction of each recipe can be priced")
covs = coverage(recipes, resolve)
ratios = sorted((c.ratio for c in covs), reverse=True)
print(f"  best recipe    {ratios[0]:.0%}")
print(f"  median recipe  {statistics.median(ratios):.0%}")
print(f"  worst recipe   {ratios[-1]:.0%}\n")
print(f"  {'costable at or above':<22} recipes")
print(f"  {'-' * 22} -------")
for threshold in (1.0, 0.9, 0.8, 0.5, 0.25):
    print(f"  {threshold:>19.0%}    {len(usable_recipes(covs, minimum_ratio=threshold))}")

print("\n  The three best-covered recipes, and what each still lacks:\n")
for c in sorted(covs, key=lambda c: c.ratio, reverse=True)[:3]:
    print(f"    {c.ratio:>4.0%}  {c.name[:34]:<34} {c.costable}/{c.needed} costable")
    print(f"          missing: {', '.join(c.missing[:6])}")
note("")
note("Zero recipes are fully costable, under any staples assumption. That is")
note("the number that killed the imported catalogue - a measurement rather")
note("than an opinion, and section 7 shows what replaced it.")

# ------------------------------------------------------------ what is missing
section("4. What is missing, and what the gap actually is")
absent: collections.Counter[str] = collections.Counter()
for c in covs:
    absent.update(c.missing)
print(f"  {len(absent)} distinct ingredients cannot be priced. The most frequent:\n")
print(f"  {'recipes needing it':>18}  ingredient")
print(f"  {'-' * 18}  ----------")
for name, count in absent.most_common(15):
    print(f"  {count:>18}  {name}")
note("")
note("These are not exotic. They are a spice rack and a condiment shelf.")
note("TheMealDB recipes are international home cooking reaching for soy")
note("sauce, fish sauce, ginger, coriander, cumin and paprika. The product")
note("catalogue is weighted to fresh produce, meat and dairy, with no spice")
note("rack, no condiments and no long tail. The two datasets were built for")
note("different jobs and do not meet.")

# ------------------------------------------------------------------ staples
section("5. The staples list, and why widening it does not help")
print(f"  ASSUMED_ON_HAND has {len(ASSUMED_ON_HAND)} entries:")
print(f"    {sorted(ASSUMED_ON_HAND)}\n")

wide = frozenset(
    ASSUMED_ON_HAND
    | {
        "olive oil",
        "vegetable oil",
        "sugar",
        "flour",
        "plain flour",
        "butter",
        "garlic",
        "onion",
        "onions",
        "milk",
        "eggs",
        "egg",
        "cumin",
        "paprika",
        "oregano",
        "thyme",
        "bay leaf",
        "cinnamon",
        "turmeric",
        "chilli powder",
        "soy sauce",
        "vinegar",
        "honey",
        "mustard",
        "stock",
        "chicken stock",
        "beef stock",
        "vegetable stock",
        "cornflour",
        "baking powder",
        "yeast",
        "rosemary",
        "parsley",
        "coriander",
        "ginger",
        "nutmeg",
        "cloves",
        "black peppercorns",
        "sesame oil",
        "tomato puree",
        "lemon juice",
    }
)
wide_covs = coverage(recipes, resolve, assumed_on_hand=wide)
print(f"  Widening it to a full spice rack and pantry ({len(wide)} terms):\n")
print(
    f"    fully costable, tiny list  ({len(ASSUMED_ON_HAND):>2} terms): "
    f"{len(usable_recipes(covs, minimum_ratio=1.0))}"
)
print(
    f"    fully costable, wide list  ({len(wide):>2} terms): "
    f"{len(usable_recipes(wide_covs, minimum_ratio=1.0))}"
)
note("")
note("Zero to zero. The gap is not staples, and a generous list would only")
note("hide that. Every entry on it is a cost the plan does not show the")
note("shopper, so widening it is a way of making a budget look achievable by")
note("ignoring what it leaves out - the exact failure this project refuses")
note("everywhere else.")

print(
    f"\n  Median coverage moves from "
    f"{statistics.median(c.ratio for c in covs):.0%} to "
    f"{statistics.median(c.ratio for c in wide_covs):.0%} - it gets WORSE."
)
note("")
note("Not a bug, and worth sitting with. The wide list above includes butter,")
note("milk, eggs, garlic and onions, which the catalogue DOES stock. Calling")
note("them 'on hand' deletes them from the denominator AND from the costable")
note("count, so what is left is the unpriceable remainder. Assuming away the")
note("part you can price does not improve a plan's honesty; it just stops you")
note("measuring the part you cannot. That is why the real list is eight items")
note("long and holds nothing anybody buys.")

# --------------------------------------------------------- the forcing shape
section("6. The gate is a build failure, not a note in a document")
usable_now = usable_recipes(covs, minimum_ratio=1.0)
print(f"  usable at 100%: {len(usable_now)}")
print("\n  scripts/check_recipe_coverage.py reports this number, and")
print("  tests/test_recipes.py fails the build if it silently becomes good")
print("  enough to proceed without anyone noticing:\n")
print("    python scripts/check_recipe_coverage.py --missing 20")
print("    python scripts/check_recipe_coverage.py --min-ratio 1.0 --fail-under 1")
note("")
note("The same forcing shape the Scan ceiling used for Pilot Task 6b, pointed")
note("the other way: there, a gate that fires when load grows; here, a gate")
note("that fires when the data finally arrives. Either way the decision is")
note("made by evidence rather than by somebody remembering to re-check.")

# ------------------------------------------------- what was done instead
section("7. What was done instead - the curated catalogue, and which catalogue")
curated = CuratedRecipeRepository().all_recipes()

fixture_covs = coverage(curated, repo.resolve_product_key)
fixture_costable = len(usable_recipes(fixture_covs, minimum_ratio=1.0))

real_resolve = real_catalogue_resolver()


def _fully_costable(rs, resolve) -> int:
    return sum(all(resolve(i.key) for i in r.ingredients) for r in rs)


real_costable = _fully_costable(curated, real_resolve) if real_resolve else None
real_imported = _fully_costable(recipes, real_resolve) if real_resolve else None

print(f"  imported recipes   {len(covs):>3}")
print(f"  curated recipes    {len(curated):>3}\n")
print(f"  {'resolved against':<34} {'recipes':>7}  {'fully costable':>14}")
print(f"  {'-' * 34} {'-' * 7}  {'-' * 14}")
print(f"  {'fixtures/products.json (152 rows)':<34} {len(covs):>7}  {'0':>14}   imported")
print(
    f"  {'fixtures/products.json (152 rows)':<34} {len(curated):>7}  "
    f"{fixture_costable:>14}   curated"
)
if real_costable is None:
    print(f"  {'datasets/ (the real catalogue)':<34} {'-':>7}  {'not present':>14}")
else:
    print(
        f"  {'datasets/ (the real catalogue)':<34} {len(covs):>7}  {real_imported:>14}   imported"
    )
    print(
        f"  {'datasets/ (the real catalogue)':<34} {len(curated):>7}  {real_costable:>14}   curated"
    )
note("")
note("READ THE THIRD COLUMN AGAINST THE FIRST, NOT ON ITS OWN. The curated")
note("recipes were written against the REAL catalogue - the ~2,700 rows the")
note("deployed table holds - so that is the number that describes them. The")
note("152-row offline fixture is a different universe, and measuring them")
note("against it measures the fixture.")
note("")
note("That is not a caveat to wave away; it is this project's most repeated")
note("lesson turning up again. Evidence is only about the thing it was")
note("collected from. tests/test_curated_recipes.py resolves against")
note("datasets/ for exactly this reason, and its docstring says so.")
note("")
note("The imported 175 fail against BOTH catalogues - shown in the table, not")
note("asserted here - which is why the decision was made at all. Against the")
note("real catalogue their best recipe reaches 62% and the median is 13%, so")
note("the gap is not a fixture artefact and more product data would not have")
note("closed it.")
note("")
note("THIS IS CURATION, NOT GAMING, and the difference is worth being precise")
note("about, because the numbers look exactly like somebody moving a goalpost.")
note("")
note("The gate asks one question: can this recipe be priced against this")
note("catalogue? Widening ASSUMED_ON_HAND answers it by DELETING it - section 5")
note("tried that and the median got worse. Writing recipes against the catalogue")
note("answers it by SATISFYING it: every ingredient resolves to a product that")
note("has a price, so a plan built from one states a payable total the shopper")
note("can really spend to.")
note("")
note("What was given up is real and should be said out loud. 175 imported")
note("recipes carried variety this project no longer has; 29 hand-written ones")
note("cover less of what a person might want to eat. That is a smaller product")
note("honestly priced rather than a larger one priced wrongly, and")
note("config/recipes.json records the same reasoning next to the data.")
note("")
note("The forcing test in section 6 is UNCHANGED and still points at the")
note("imported catalogue. If the product data ever grows enough to price those")
note("175, the build fails and somebody has to make the decision again.")

print("\n  This demo is a NEGATIVE result, and that is what it is for. A demo")
print("  suite that only shows what works tells you nothing about where the")
print("  edges are. src/recipes/base.py carries the full reasoning.")

print("\nDone.")
