"""
How much of the recipe catalogue can this product catalogue actually price?

Pilot Task 15 (Req 2.9) needs meal plans composed from curated recipes. A
recipe is only usable if EVERY ingredient can be priced: a plan whose payable
total is computed from part of the shopping list states a number the shopper
cannot spend to, and `within_budget` derived from it is a false promise.

So this reports the distance to that, rather than a yes/no. It resolves through
the same synonym table the service uses, because a coverage number computed
against a different resolver measures nothing.

IT NAMES THE CATALOGUE IT RESOLVED AGAINST, AND THAT IS THE POINT. Until
2026-08-31 this script resolved through `InMemoryPriceRepository()`, which
defaults to the 26-product fixture file, while the serving table held 2,759
rows of an entirely different catalogue and `src/recipes/base.py` described the
measurement as being against "300 items per store". The docstring named the
real catalogue; the code read the fixture one, and the output said neither.
A blocking decision on Req 2.9 rested on an instrument pointed at the wrong
data. The conclusion happened to survive -- zero of the 175 imported recipes
are fully priceable against EITHER catalogue -- and a number that is right by
luck is not evidence.

Usage:
    python scripts/check_recipe_coverage.py
    python scripts/check_recipe_coverage.py --catalogue fixtures
    python scripts/check_recipe_coverage.py --recipes curated
    python scripts/check_recipe_coverage.py --min-ratio 1.0 --fail-under 1
    python scripts/check_recipe_coverage.py --missing 20   # what is absent, by frequency
"""

from __future__ import annotations

import argparse
import collections
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.recipes import (  # noqa: E402
    CuratedRecipeRepository,
    FixtureRecipeRepository,
    coverage,
    usable_recipes,
)
from src.recipes.catalogue import (  # noqa: E402
    Catalogue,
    load_dataset_catalogue,
    load_fixture_catalogue,
)
from src.retrieval.memory import load_synonyms  # noqa: E402


def build_resolver(catalogue: Catalogue):
    """
    Resolve exactly as the service does, plus singular/plural of the whole term.

    The extra forms are the recipe side's problem, not the shopper's: a recipe
    writes `onion` where the catalogue sells `Brown Onions`. Being generous
    here can only make coverage look BETTER, so a bad number under this
    resolver is a floor rather than an artefact of strict matching.
    """
    synonyms = load_synonyms()

    def resolve(term: str) -> str | None:
        for candidate in (term, f"{term}s", term[:-1] if term.endswith("s") else term):
            key = catalogue.resolve(candidate, synonyms)
            if key:
                return key
        return None

    return resolve


def select_catalogue(requested: str) -> tuple[Catalogue, Catalogue | None]:
    """
    The catalogue to measure against, and the dataset one if it exists.

    The second return value is what lets `--fail-under` refuse to gate a
    decision from the fixture catalogue while the real one is sitting there.
    """
    dataset = load_dataset_catalogue()
    if requested == "fixtures":
        return load_fixture_catalogue(), dataset
    if dataset is None:
        print(
            "datasets/data/dynamodb_products is not present; falling back to "
            "the fixture catalogue. Every number below describes 26 products.",
            file=sys.stderr,
        )
        return load_fixture_catalogue(), None
    return dataset, dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalogue",
        choices=("datasets", "fixtures"),
        default="datasets",
        help="product catalogue to resolve against (default: the real one)",
    )
    parser.add_argument(
        "--recipes",
        choices=("imported", "curated"),
        default="imported",
        help="imported = the 175 from TheMealDB; curated = config/recipes.json",
    )
    parser.add_argument("--min-ratio", type=float, default=1.0, help="ingredients costable")
    parser.add_argument("--fail-under", type=int, default=0, help="exit 1 below this many usable")
    parser.add_argument("--missing", type=int, default=0, help="show N most-missing ingredients")
    args = parser.parse_args()

    catalogue, dataset = select_catalogue(args.catalogue)

    # A gate is a decision, and a decision measured against 26 products while
    # the service serves 2,759 is the D3 defect with a threshold attached.
    # Refuse rather than report: an instrument that can gate the wrong data is
    # worse than one that cannot gate at all.
    if args.fail_under and catalogue.name == "fixtures" and dataset is not None:
        print(
            "REFUSING TO GATE: --fail-under was asked to make a decision from "
            f"the fixture catalogue ({catalogue.describe()}) while the real one "
            f"is available ({dataset.describe()}). Drop --catalogue fixtures, "
            "or drop --fail-under and read the number as a fixture number.",
            file=sys.stderr,
        )
        return 2

    repository = (
        CuratedRecipeRepository() if args.recipes == "curated" else FixtureRecipeRepository()
    )
    recipes = repository.all_recipes()
    resolve = build_resolver(catalogue)
    covs = coverage(recipes, resolve)
    ratios = sorted((c.ratio for c in covs), reverse=True)

    # Both inputs, before any result. A coverage figure without them is the
    # thing this script exists to stop being quotable.
    print(f"product catalogue   {catalogue.describe()}")
    print(f"recipe catalogue    {args.recipes} -- {len(recipes)} recipes")
    print(f"ingredient coverage  best {ratios[0]:.0%}   median {statistics.median(ratios):.0%}")
    for threshold in (1.0, 0.9, 0.8, 0.5):
        count = len(usable_recipes(covs, minimum_ratio=threshold))
        print(f"  costable >= {threshold:>4.0%}   {count}")

    usable = usable_recipes(covs, minimum_ratio=args.min_ratio)
    print(f"\nusable at >= {args.min_ratio:.0%}: {len(usable)}")

    if args.missing:
        absent: collections.Counter[str] = collections.Counter()
        for c in covs:
            absent.update(c.missing)
        print(f"\nmost frequently missing ingredients (of {len(absent)} distinct):")
        for name, count in absent.most_common(args.missing):
            print(f"  {count:4d}  {name}")

    if len(usable) < args.fail_under:
        print(
            f"\nFAIL: {len(usable)} recipes are costable at >= {args.min_ratio:.0%} "
            f"against {catalogue.describe()}, need {args.fail_under}. Pilot Task 15 "
            "cannot compose plans from a catalogue it cannot price -- see "
            "src/recipes/base.py.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
