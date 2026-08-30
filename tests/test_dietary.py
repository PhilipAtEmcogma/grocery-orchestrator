"""
Dietary safety tests (Req 5.1, Invariant 3).

The rule this file exists for: an exclusion the user states must either be
honoured against retrieved products, or refused honestly. Silently ignoring
an unmappable term — which is how a vegan user used to end up with dairy in
their meal plan — is the shape of bug we cannot ship again, so the mapping
lives in `src/graph/dietary.py` as data and the graph refuses meal-plan
turns whose exclusions we cannot verify.

Two halves. Unit tests over the mapping itself, and end-to-end tests
through `run_turn` that the graph honours each supported term against
`fixtures/products.json` and refuses each unsupported one.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.graph.dietary import SUPPORTED_EXCLUSIONS, map_exclusions, supported_terms
from src.models.scripted import ScriptedModelClient
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import ChatRequest, ClientHints, ErrorCode

# --------------------------------------------------------- mapping unit tests


def test_supported_exclusions_uses_only_fixture_categories():
    """
    The mapping targets fixture categories, so a rename or a new category
    could quietly break exclusion coverage. Anchoring this here means an
    unknown category surfaces the moment the tests run rather than at some
    later meal-plan turn.
    """
    fixture_categories = set(InMemoryPriceRepository().all_categories)
    for term, categories in SUPPORTED_EXCLUSIONS.items():
        unknown = categories - fixture_categories
        assert not unknown, (
            f"'{term}' maps to unknown fixture categories {sorted(unknown)}. "
            f"Fixture has: {sorted(fixture_categories)}"
        )


@pytest.mark.parametrize("term", sorted(SUPPORTED_EXCLUSIONS))
def test_every_supported_term_excludes_at_least_one_category(term):
    """An exclusion mapping to an empty set is a term that is silently ignored."""
    categories, unsupported = map_exclusions([term])
    assert categories, f"'{term}' maps to no categories"
    assert not unsupported


def test_vegan_excludes_animal_categories():
    """
    THE regression test for the reported bug.

    A vegan user used to be told nothing was wrong and served meat, dairy
    and eggs, because "vegan" was extracted but never mapped.
    """
    categories, unsupported = map_exclusions(["vegan"])
    assert "meat" in categories
    assert "seafood" in categories
    assert "dairy" in categories
    # Eggs live in the "chilled" category in the current fixture — see
    # `SUPPORTED_EXCLUSIONS` for the reasoning about why this is not a
    # per-product tag yet.
    assert "chilled" in categories
    assert not unsupported


def test_unmapped_term_is_reported_not_dropped():
    """
    Silent drop is the bug. `gluten-free` and `nut-free` have no clean
    category-based mapping against the current fixture, so `map_exclusions`
    reports them explicitly — and the graph refuses the meal plan rather
    than filtering an incomplete map.
    """
    categories, unsupported = map_exclusions(["gluten-free"])
    assert unsupported == ["gluten-free"]
    # And the caller still gets the categories from anything else it CAN
    # honour, so a mixed request is handled correctly:
    categories, unsupported = map_exclusions(["vegan", "gluten-free"])
    assert "meat" in categories
    assert unsupported == ["gluten-free"]


def test_case_and_whitespace_are_normalised():
    categories, unsupported = map_exclusions(["  VEGAN  ", "Dairy-Free"])
    assert "meat" in categories
    assert "dairy" in categories
    assert not unsupported


def test_duplicates_do_not_multiply_the_category_set():
    categories, unsupported = map_exclusions(["vegan", "vegan"])
    # frozenset over a duplicate list would still be a frozenset; this
    # asserts the outer contract, which is the list the retrieval layer
    # gets.
    assert categories == sorted({"meat", "seafood", "dairy", "chilled"})
    assert not unsupported


def test_supported_terms_is_the_full_key_list():
    """The refusal message quotes this back to the user, so it must be complete."""
    assert set(supported_terms()) == set(SUPPORTED_EXCLUSIONS)


# ------------------------------------------------ end-to-end honour and refuse


@pytest.fixture(scope="module")
def repo() -> InMemoryPriceRepository:
    return InMemoryPriceRepository()


def _plan_request(exclusions: list[str]) -> ChatRequest:
    return ChatRequest(
        session_id="sess-diet01",
        turn_id="turn-diet01",
        message=f"meal plan for the week, {', '.join(exclusions)}",
        hints=ClientHints(
            household_size=2,
            budget_nzd=Decimal("40"),
            days=3,
            dietary_exclusions=exclusions,
        ),
    )


def _plan_categories(response) -> set[str]:
    """Categories that appear in the returned plan, via the retrieved citations."""
    return {e.citation.source.pk.split("#")[-1] for e in response.events if e.type == "citation"}


def test_vegan_plan_contains_no_animal_products(repo):
    """
    The end-to-end proof of the fix: a vegan user gets nothing from meat,
    seafood, dairy or chilled.
    """
    response = run_turn(_plan_request(["vegan"]), repo, ScriptedModelClient())
    categories = _plan_categories(response)
    for banned in ("meat", "seafood", "dairy", "chilled"):
        leaked = [
            e.citation.product_name
            for e in response.events
            if e.type == "citation" and e.citation.source.pk.endswith(banned)
        ]
        assert banned not in categories, f"vegan plan contains {banned} products: {leaked}"


def test_seafood_exclusion_removes_seafood_end_to_end(repo):
    """The case the old mapping did handle — confirm it still does."""
    response = run_turn(_plan_request(["seafood"]), repo, ScriptedModelClient())
    assert "seafood" not in _plan_categories(response)


def test_shellfish_is_honoured_as_seafood(repo):
    """
    A common lay term. Users type this rather than the category label; the
    mapping treats it as seafood.
    """
    response = run_turn(_plan_request(["shellfish"]), repo, ScriptedModelClient())
    assert "seafood" not in _plan_categories(response)


def test_unsupported_exclusion_refuses_the_meal_plan(repo):
    """
    Rather than filter an incomplete map and produce an unsafe plan, the
    graph returns an honest refusal — same principle as
    `emit_budget_infeasible` for a budget we cannot meet.
    """
    response = run_turn(_plan_request(["gluten-free"]), repo, ScriptedModelClient())
    types = {e.type for e in response.events}
    assert "meal_plan" not in types, "unsafe plan produced for gluten-free user"
    errors = [e for e in response.events if e.type == "error"]
    assert errors, "an unsupported exclusion must be reported"
    assert errors[0].code == ErrorCode.UNSUPPORTED_EXCLUSION
    assert errors[0].retryable is False
    # The user is given something actionable rather than a bare refusal.
    assert "gluten-free" in errors[0].message
    for term in ("vegan", "vegetarian", "dairy-free"):
        assert term in errors[0].message


def test_mixed_supported_and_unsupported_refuses(repo):
    """
    A partial match is not enough. Producing a vegan plan for a user who
    also said gluten-free would drop half their statement silently.
    """
    response = run_turn(_plan_request(["vegan", "gluten-free"]), repo, ScriptedModelClient())
    errors = [e for e in response.events if e.type == "error"]
    assert errors and errors[0].code == ErrorCode.UNSUPPORTED_EXCLUSION
    assert "meal_plan" not in {e.type for e in response.events}


def test_refusal_reaches_finalise(repo):
    """
    The turn still ends with a done event, so the frontend's terminal-event
    handling is unchanged — Invariant 2 (honest failure), preserved.
    """
    response = run_turn(_plan_request(["nut-free"]), repo, ScriptedModelClient())
    assert response.events[-1].type == "done"


def test_price_check_is_not_blocked_by_unsupported_exclusion(repo):
    """
    Dietary exclusions apply to meal plans, not to a price check for one
    named product. Blocking a butter query because the user also said
    "gluten-free" would refuse a legitimate question for no safety benefit.
    """
    request = ChatRequest(
        session_id="sess-diet02",
        turn_id="turn-diet02",
        message="cheapest butter",
        hints=ClientHints(dietary_exclusions=["gluten-free"]),
    )
    response = run_turn(request, repo, ScriptedModelClient())
    types = {e.type for e in response.events}
    assert "price_comparison" in types
    assert "error" not in types


# ---------------------------------------------------------------- bare nouns


@pytest.mark.parametrize(
    ("bare", "negated"),
    [("meat", "no meat"), ("dairy", "no dairy"), ("eggs", "no eggs")],
)
def test_a_bare_noun_maps_exactly_as_its_negated_form(bare: str, negated: str) -> None:
    """
    The extractor does not always phrase an exclusion the way the user did.

    `vegetarian dinner for 2 for 3 days on $50` was refused live with
    UNSUPPORTED_EXCLUSION because the model returned the exclusion as `meat`,
    and the table had `no meat` but not `meat`. The refusal then listed "no
    meat" among the supported terms while refusing "meat".

    Asserted as EQUALITY with the negated form rather than against a literal
    set: a bare noun that excluded something different from its negation would
    be a second policy decision wearing a synonym's clothes.
    """
    assert SUPPORTED_EXCLUSIONS[bare] == SUPPORTED_EXCLUSIONS[negated]

    categories, unsupported = map_exclusions([bare])
    assert not unsupported
    assert categories == sorted(SUPPORTED_EXCLUSIONS[negated])


def test_the_refusal_message_never_lists_a_term_it_would_refuse() -> None:
    """
    Every term `supported_terms()` offers must actually map.

    The live defect was visible in exactly this shape -- the message advertised
    "no meat" while the request for "meat" was being refused. A user reading
    that has no way to act on it.
    """
    for term in supported_terms():
        categories, unsupported = map_exclusions([term])
        assert not unsupported, f"{term!r} is advertised but refused"
        assert categories, f"{term!r} is advertised but excludes nothing"
