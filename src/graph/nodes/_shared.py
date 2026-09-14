"""
Helpers two node modules both need.

`_next_seq` and `_join` were in `src/graph/nodes/__init__.py` when every node
lived there. Splitting retrieval out (2026-08-31) left them wanted at both ends,
and `retrieval.py` importing them from the package `__init__` that imports
`retrieval.py` is a cycle -- the same one `src/graph/recipe_plan.py` hit, which
closed only when it was imported first and so was invisible from every entry
point the graph uses.

A leaf module both can import is the version of that with no ordering to get
wrong.
"""

from __future__ import annotations

from decimal import Decimal

from src.graph.state import GroceryState


def _next_seq(state: GroceryState) -> int:
    return len(state.get("events", []))


def _join(items: list[str]) -> str:
    """Human list: 'a', 'a and b', 'a, b and c'. Truncated per item, not overall."""
    clean = [i[:80] for i in items]
    if len(clean) == 1:
        return clean[0]
    return f"{', '.join(clean[:-1])} and {clean[-1]}"


def _preference_unmet(
    preferences: list[str],
    cheapest_preferred: tuple[str, str] | None,
    *,
    household_size: int,
    days: int,
    budget_nzd: Decimal | None,
) -> str:
    """
    Why the plan does not contain the food the shopper asked for.

    HERE RATHER THAN IN EITHER CALLER because the facts come from two nodes and
    the sentence has to be one voice. `retrieve_prices` knows whether a matching
    recipe existed and what the cheapest would have cost; only `finalise` knows
    whether the plan the shopper is actually handed contains it.

    TWO DIFFERENT FACTS, AND CONFLATING THEM WOULD BE THE LIE. If a matching
    recipe existed and the budget removed it, the limit is the shopper's money
    and the fix is in their hands, so the message says so and quotes what the
    cheapest one would actually cost. If no matching recipe existed at all, the
    limit is our catalogue and no budget would change it; telling them to raise
    their budget would send them to spend more for a result that cannot happen.

    THE FIGURE IS GROUNDED, not estimated. It is a `payable_nzd` off a
    `RecipeOffer`, which `_payable_for` costed through the real `assemble_plan`
    from retrieved citations -- the same arithmetic the plan itself uses. This is
    code-authored text, so `assert_no_literal_money` does not apply to it (that
    guard exists to stop a MODEL inventing a price), but the number still has to
    be one we retrieved, and this one is.
    """
    wanted = _join(preferences)

    if cheapest_preferred is None:
        return (
            f"I don't have a {wanted} recipe I can price from the products near "
            f"you, so this plan has none. Raising the budget won't change that."
        )

    name, payable = cheapest_preferred
    cost = Decimal(payable)
    scope = f"{household_size} " + ("person" if household_size == 1 else "people")
    span = f"{days} " + ("day" if days == 1 else "days")

    # THE CHEAPEST MATCH CAN COST LESS THAN THE BUDGET AND STILL NOT SURVIVE,
    # and saying "no chicken meal fits at $25" beside "the cheapest is $16.67"
    # is a sentence that argues with itself. The first draft did exactly that.
    #
    # The figure is what ONE such meal costs. The constraint is the whole plan:
    # the household needs several meals, `_cost_within_budget` drops the dearest
    # marginal one until the basket fits, and the preferred meal is very often
    # the dearest. So when the match is individually affordable the honest
    # reason is competition, not price -- and the shopper's lever is different
    # too. Raising the budget a little genuinely helps here, where in the branch
    # below they would need to clear the whole figure.
    if budget_nzd is not None and cost <= budget_nzd:
        return (
            f"A {wanted} meal would fit on its own — {name} at ${cost:.2f} — but "
            f"not alongside the other meals {scope} need over {span} on "
            f"${budget_nzd:.2f}, so this plan has none."
        )

    budget_clause = f" at ${budget_nzd:.2f}" if budget_nzd is not None else ""
    return (
        f"No {wanted} meal fits{budget_clause} for {scope} over {span} — the "
        f"cheapest I can build is {name} at ${cost:.2f}, so this plan has none."
    )
