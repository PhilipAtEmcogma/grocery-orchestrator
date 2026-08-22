"""
Dietary exclusion mapping.

Exclusions are safety-critical (Req 5, Invariant 3). The mapping from
free-text exclusion terms to the product categories they rule out lives here
so it is reviewable in one place, tested directly, and impossible to
silently disagree with itself — which was previously how "vegan" slipped
through: extraction produced it, but no mapping table honoured it, so a vegan
user got meat, dairy and eggs in their meal plan. Silent failure of a safety
control is the worst shape of bug this project deals with.

Two rules the design enforces here:

1. **Fail closed on the unknown.** `map_exclusions()` returns the terms it
   could not map, and the graph refuses the plan when any exist. Silently
   dropping "gluten-free" because the fixture has no gluten tags would be an
   unsafe plan wearing the costume of a helpful one. `emit_dietary_unsupported`
   is what the graph reaches when this happens.

2. **Category-based, not per-product tagging — for now.** The fixture stores
   a `category` per product, so mapping "vegan" to {meat, seafood, dairy,
   chilled} is exact against the current catalogue. Anything the categories
   cannot express — gluten, tree nuts, soy — is unmappable and must refuse
   rather than approximate. When per-product allergen tags land in the
   fixture (Task 2.9's follow-on), this becomes a straight extension: the
   mapping widens to include those terms, and unmapped shrinks.
"""

from __future__ import annotations

# term -> fixture categories that must be excluded to honour the term.
#
# `frozenset` because these are lookup tables, never mutated, and the type
# checker catches "did you mean to include another category" as an error
# rather than a silent Falseism from a set the caller wrote to.
#
# The chilled category currently holds one product (eggs). It joins the
# vegan and vegetarian sets because eggs are the animal product it carries.
# If future scraper output puts other things in `chilled`, this table has to
# be revisited — which the tests below will catch by asserting on the
# categories the fixture actually uses, not on a frozen expectation.
SUPPORTED_EXCLUSIONS: dict[str, frozenset[str]] = {
    "seafood": frozenset({"seafood"}),
    "fish": frozenset({"seafood"}),
    "shellfish": frozenset({"seafood"}),
    "pescatarian": frozenset({"meat"}),  # excludes meat, keeps seafood
    "vegetarian": frozenset({"meat", "seafood"}),
    "no meat": frozenset({"meat", "seafood"}),
    "vegan": frozenset({"meat", "seafood", "dairy", "chilled"}),
    "dairy-free": frozenset({"dairy"}),
    "no dairy": frozenset({"dairy"}),
    "no eggs": frozenset({"chilled"}),
}


def map_exclusions(terms: list[str]) -> tuple[list[str], list[str]]:
    """
    Return the categories to exclude and the terms we cannot honour.

    Case-insensitive on the key. The output category list is sorted and
    de-duplicated so the retrieval layer receives a stable filter.

    An empty string or bare whitespace is ignored rather than reported as
    unsupported — it never came from a user in a form they could correct.
    """
    categories: set[str] = set()
    unsupported: list[str] = []
    seen: set[str] = set()

    for raw in terms:
        term = raw.strip().lower()
        if not term:
            continue
        if term in seen:
            continue
        seen.add(term)
        if term in SUPPORTED_EXCLUSIONS:
            categories |= SUPPORTED_EXCLUSIONS[term]
        else:
            unsupported.append(term)

    return sorted(categories), unsupported


def supported_terms() -> list[str]:
    """The list a refusal message can quote to the user."""
    return sorted(SUPPORTED_EXCLUSIONS.keys())
