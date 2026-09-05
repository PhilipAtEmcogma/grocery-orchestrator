"""
Recipe selection (Req 2.9, Pilot Task 15c).

THE MODEL'S ENTIRE CONTRIBUTION IS A LIST OF IDS. `RecipeSelection` has one
field and it holds strings that must already appear in the shortlist the prompt
offered. There is no field for a quantity, a pack count, a price, an ingredient
or a meal name — so there is nothing here for a model to invent. Req 2.9 says
the model selects and deterministic code owns scaling, dietary verification and
totals, and this is the narrowest schema that expresses that.

Compare `PlanDraft`, which the free-composition path uses: that one carries meal
names, ingredient names and pack multipliers, all model-authored, and every one
of them needed a check written for it after a defect. A schema with one field of
constrained strings needs one check, and it is an `in` test.

WHAT THE MODEL IS NOT SHOWN. No prices, like everywhere else. But also: only
recipes that retrieval has ALREADY proven costable, dietary-viable against the
resolved products, and individually affordable at the household's budget share.
A model that cannot see an uncostable recipe cannot select one, which is a
stronger guarantee than validating the selection afterwards — the same reason
`candidates_for_budget` caps the candidate set rather than checking the plan's
total after the fact.

WHY IT IS STILL WORTH A MODEL. Choosing five recipes for a household is a
judgement about variety, balance and what goes together, over a shortlist where
every option is already correct. That is the shape of problem a language model
is good at and deterministic code is bad at — and it is exactly the shape where
being wrong costs a duller menu rather than a wrong price.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

#: Recipe ids per selection. A cap for the same reason MAX_ITEMS_PER_TURN
#: exists: a model asked for five that returns fifty has misunderstood, and the
#: cost of finding out downstream is a plan nobody asked for.
MAX_SELECTED_RECIPES = 14


class RecipeSelection(BaseModel):
    """
    Recipe ids, in the order they should be served.

    ORDER IS THE ONLY OTHER THING THE MODEL DECIDES, and it is cosmetic: the
    plan lists meals in the order returned. Nothing downstream reads it as a
    fact about the world, so it cannot be wrong in a way that reaches a price.
    """

    recipe_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_SELECTED_RECIPES,
        description="Recipe ids chosen from the offered list, one per meal.",
    )


SYSTEM_PROMPT = """You choose meals for a New Zealand household from a fixed list.

RULES
- Choose ONLY from the recipe ids given to you. Never invent an id.
- Choose exactly the number of meals asked for, unless fewer are offered.
- Do not repeat a recipe unless there are fewer recipes than meals needed.
- Prefer variety: different main ingredients and different styles across the week.
- NEVER state a price, a quantity, a pack count or a total. You are not given
  prices and you cannot compute them. Return ids only.

Return JSON matching the schema. No other text."""


def build_selection_prompt(
    *,
    message: str,
    household_size: int,
    days: int,
    meals: int,
    exclusions: list[str],
    offered: list[tuple[str, str, str]],
) -> str:
    """
    The user prompt: the request, and the shortlist.

    `offered` is (recipe_id, name, main ingredients) per recipe. The ingredient
    names are the RESOLVED PRODUCT names rather than the recipe's own terms, so
    what the model reads is what the shopper would buy — and so a model
    reasoning about variety is reasoning about the same objects the plan will
    cost. No prices, no pack sizes, no quantities.

    The user's own message is included and DELIMITED, because it carries
    preferences the structured constraints do not ("something quick",
    "the kids won't eat fish"). It is untrusted input like everywhere else:
    `src/models/guardrail.py` tags it, and the shortlist is what bounds the
    damage a prompt injection could do — the worst available outcome is a
    differently-ordered list of ids we already validated.
    """
    lines = [f"  {rid} | {name} | {items}" for rid, name, items in offered]
    exclusion_line = (
        f"Dietary exclusions already applied to this list: {', '.join(exclusions)}.\n"
        if exclusions
        else ""
    )
    return (
        f"Household of {household_size}, {days} day(s). Choose {meals} meal(s).\n"
        f"{exclusion_line}"
        f"\nThe shopper asked:\n<<<{message[:500]}>>>\n"
        f"\nAvailable recipes (id | name | main ingredients):\n"
        + "\n".join(lines)
        + "\n\nReturn the recipe ids you choose."
    )
