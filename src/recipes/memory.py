"""
Fixture-backed RecipeRepository, plus the coverage measurement that gates
Pilot Task 15.

Reads the batch-write JSON the data team committed under
`datasets/data/dynamodb_recipe_batches/`, the same envelope
`ingestion/lineage_b.py` reads for products. No AWS account required.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.recipes.base import Recipe, RecipeIngredient, RecipeRepository

RECIPE_DIR = Path(__file__).resolve().parents[2] / "datasets" / "data" / "dynamodb_recipe_batches"

# Ingredients a shopper is assumed to have, and which therefore neither need
# pricing nor count against a recipe's coverage.
#
# DELIBERATELY TINY, AND IT MUST STAY THAT WAY. Every entry here is a cost the
# plan does not show the shopper, so a generous list is a way of making a
# budget look achievable by ignoring what it leaves out -- the exact failure
# this project refuses everywhere else. Water is not a grocery product; salt
# and pepper are in essentially every kitchen and cost pennies.
#
# It was measured before being trusted: widening this list to a full spice rack
# and pantry (40+ terms) moved the number of usable recipes from zero to zero.
# The gap is not staples, and pretending otherwise would hide that.
ASSUMED_ON_HAND: frozenset[str] = frozenset(
    {"water", "salt", "pepper", "black pepper", "sea salt", "ice", "cold water", "boiling water"}
)


class FixtureRecipeRepository(RecipeRepository):
    """Every recipe the data team collected, from the committed batches."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or RECIPE_DIR
        self._recipes: list[Recipe] | None = None

    def _load(self) -> list[Recipe]:
        if self._recipes is not None:
            return self._recipes

        recipes: list[Recipe] = []
        for batch in sorted(self._path.glob("*.json")):
            payload = json.loads(batch.read_text(encoding="utf-8"))
            for entry in payload["SmartGroceryRecipes"]:
                item = entry.get("PutRequest", {}).get("Item", entry)
                recipes.append(_to_recipe(item))
        self._recipes = recipes
        return recipes

    def all_recipes(self) -> list[Recipe]:
        return list(self._load())

    def get(self, recipe_id: str) -> Recipe | None:
        return next((r for r in self._load() if r.recipe_id == recipe_id), None)


def _to_recipe(item: dict) -> Recipe:
    ingredients = tuple(
        RecipeIngredient(
            key=i["M"]["key"]["S"],
            name=i["M"]["name"]["S"],
            measure=i["M"].get("measure", {}).get("S", ""),
        )
        for i in item["ingredients"]["L"]
    )
    return Recipe(
        recipe_id=item["recipe_id"]["S"],
        name=item["recipe_name"]["S"],
        category=item["category"]["S"],
        area=item.get("area", {}).get("S", ""),
        ingredients=ingredients,
        # TheMealDB's terms require attribution, and it travels with the recipe
        # rather than being reconstructed at display time.
        attribution=item.get("attribution", {}).get("S", ""),
    )


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecipeCoverage:
    """How much of one recipe this catalogue can actually price."""

    recipe_id: str
    name: str
    needed: int
    costable: int
    missing: tuple[str, ...]

    @property
    def ratio(self) -> float:
        return self.costable / self.needed if self.needed else 1.0


def coverage(
    recipes: list[Recipe],
    resolve: object,
    *,
    assumed_on_hand: frozenset[str] = ASSUMED_ON_HAND,
) -> list[RecipeCoverage]:
    """
    Per-recipe ingredient coverage, given a `resolve(term) -> key | None`.

    `resolve` is injected rather than imported so this measures whatever
    resolution the SERVICE would actually do -- the synonym table filtered to
    the catalogue that is really loaded. A coverage number computed against a
    different resolver than the graph uses would be a measurement of nothing,
    which is the mistake `docs/ARCHITECTURE.md` §3f records for the Guardrail.
    """
    resolver = resolve  # named for readability at the call site below
    out: list[RecipeCoverage] = []
    for recipe in recipes:
        needed = [i.key for i in recipe.ingredients if i.key.lower() not in assumed_on_hand]
        missing = [k for k in needed if not resolver(k)]  # type: ignore[operator]
        out.append(
            RecipeCoverage(
                recipe_id=recipe.recipe_id,
                name=recipe.name,
                needed=len(needed),
                costable=len(needed) - len(missing),
                missing=tuple(sorted(set(missing))),
            )
        )
    return out


def usable_recipes(
    coverages: list[RecipeCoverage], *, minimum_ratio: float
) -> list[RecipeCoverage]:
    """
    Recipes complete enough to cost honestly.

    `minimum_ratio` is not a quality dial. Below 1.0 a plan states a payable
    total computed from less than the whole shopping list, and the shopper is
    told a number they cannot spend to. The threshold exists so the gap can be
    reported as a distance rather than a yes/no.
    """
    return [c for c in coverages if c.ratio >= minimum_ratio]
