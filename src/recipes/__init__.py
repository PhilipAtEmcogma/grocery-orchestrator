"""Curated recipe catalogue (Req 2.9, Pilot Task 15). See base.py for why it is not yet wired."""

from src.recipes.base import (
    Recipe,
    RecipeIngredient,
    RecipeRepository,
    is_viable_for,
    recipe_categories,
    recipe_excluded_categories,
)
from src.recipes.memory import (
    ASSUMED_ON_HAND,
    CURATED_RECIPES,
    CuratedRecipeRepository,
    FixtureRecipeRepository,
    RecipeCoverage,
    coverage,
    usable_recipes,
)

__all__ = [
    "ASSUMED_ON_HAND",
    "CURATED_RECIPES",
    "CuratedRecipeRepository",
    "FixtureRecipeRepository",
    "Recipe",
    "RecipeCoverage",
    "RecipeIngredient",
    "RecipeRepository",
    "coverage",
    "is_viable_for",
    "recipe_categories",
    "recipe_excluded_categories",
    "usable_recipes",
]
