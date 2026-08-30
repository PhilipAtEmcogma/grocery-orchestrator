"""
How much of the recipe catalogue can this product catalogue actually price?

Pilot Task 15 (Req 2.9) needs meal plans composed from curated recipes. A
recipe is only usable if EVERY ingredient can be priced: a plan whose payable
total is computed from part of the shopping list states a number the shopper
cannot spend to, and `within_budget` derived from it is a false promise.

So this reports the distance to that, rather than a yes/no. It resolves through
the same synonym table and the same catalogue the service uses, because a
coverage number computed against a different resolver measures nothing.

Usage:
    python scripts/check_recipe_coverage.py
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
    FixtureRecipeRepository,
    coverage,
    usable_recipes,
)
from src.retrieval.memory import InMemoryPriceRepository  # noqa: E402


def build_resolver(repo: InMemoryPriceRepository):
    """
    Resolve exactly as the service does, plus singular/plural of the whole term.

    The extra forms are the recipe side's problem, not the shopper's: a recipe
    writes `onion` where the catalogue sells `Brown Onions`. Being generous
    here can only make coverage look BETTER, so a bad number under this
    resolver is a floor rather than an artefact of strict matching.
    """

    def resolve(term: str) -> str | None:
        for candidate in (term, f"{term}s", term[:-1] if term.endswith("s") else term):
            key = repo.resolve_product_key(candidate)
            if key:
                return key
        return None

    return resolve


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-ratio", type=float, default=1.0, help="ingredients costable")
    parser.add_argument("--fail-under", type=int, default=0, help="exit 1 below this many usable")
    parser.add_argument("--missing", type=int, default=0, help="show N most-missing ingredients")
    args = parser.parse_args()

    recipes = FixtureRecipeRepository().all_recipes()
    resolve = build_resolver(InMemoryPriceRepository())
    covs = coverage(recipes, resolve)
    ratios = sorted((c.ratio for c in covs), reverse=True)

    print(f"recipes            {len(recipes)}")
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
            f"\nFAIL: {len(usable)} recipes are costable at >= {args.min_ratio:.0%}, "
            f"need {args.fail_under}. Pilot Task 15 cannot compose plans from a "
            "catalogue it cannot price -- see src/recipes/base.py.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
