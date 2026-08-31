"""
Recipe-constrained planning on the shopper path (Req 2.9, Pilot Task 15c).

`tests/test_recipe_planning.py` covers `src/recipes/planning.py` -- the
arithmetic that turns recipes into a draft. This file covers the half that was
missing until 2026-08-31: the graph edge, the shortlist retrieval builds, the
selection node, and the fallback.

The properties worth pinning are the ones a reader would otherwise have to take
on trust:

  * a meal plan is built from NAMED RECIPES, not free composition;
  * the model can only pick from a shortlist that is already costable,
    dietary-viable and affordable, so it cannot pick badly in a way that reaches
    a price;
  * an id it was not offered is dropped rather than corrected;
  * when the recipe path cannot serve the request, the turn falls back to free
    composition AND SAYS SO.
"""

from __future__ import annotations

from decimal import Decimal
from typing import cast

import pytest

from src.graph.nodes.recipes import route_after_recipe_selection, select_recipes
from src.graph.recipe_plan import (
    affordable_set,
    curated_recipes,
    meals_needed,
    resolve_ingredients,
    shortlist,
)
from src.graph.state import GroceryState
from src.models.scripted import ScriptedModelClient
from src.prompts.recipe_select import RecipeSelection
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import ChatRequest, ClientHints


@pytest.fixture
def repo() -> InMemoryPriceRepository:
    return InMemoryPriceRepository()


def _request(message: str, **hints) -> ChatRequest:
    base: dict = {"household_size": 3, "days": 5, "budget_nzd": 120}
    base.update(hints)
    return ChatRequest(
        session_id="s" * 8, turn_id="t" * 8, message=message, hints=ClientHints(**base)
    )


def _plan_event(response):
    return next((e for e in response.events if e.type == "meal_plan"), None)


def _notices(response) -> list[str]:
    return [e.message for e in response.events if e.type == "notice"]


# ----------------------------------------------------------- the happy path


def test_a_meal_plan_is_built_from_named_recipes(repo):
    """
    Req 2.9, end to end and through the real graph.

    The observable difference from free composition is that every meal carries a
    recipe's NAME. "Sausages and Mash" is a meal; a basket containing sausages
    and potatoes is a shopping list, and the whole point of the curated
    catalogue is that the shopper gets the first.
    """
    response = run_turn(_request("feed 3 people for 5 days"), repo, ScriptedModelClient())
    event = _plan_event(response)
    assert event is not None, "no meal plan produced"

    names = {m.name for m in event.data.meals}
    catalogue = {r.name for r in curated_recipes()}
    assert names <= catalogue, f"meals not from the catalogue: {sorted(names - catalogue)}"
    assert not _notices(response), "the recipe path succeeded but announced a fallback"


def test_the_plan_stays_within_budget_and_is_costed_from_citations(repo):
    """
    The recipe path goes through `validate_plan` like every other plan.

    It is deterministic code producing this plan, which is exactly why it must
    not be trusted: a path that validated its own output would be the one place
    in the graph where a plan is believed rather than checked.
    """
    response = run_turn(_request("feed 3 for 5 days", budget_nzd=120), repo, ScriptedModelClient())
    event = _plan_event(response)
    assert event is not None
    plan = event.data
    assert plan.payable_total_nzd <= plan.budget_nzd
    assert plan.within_budget
    assert plan.meals, "a plan with no meals is not a plan"
    for meal in plan.meals:
        assert meal.serves == 3, "Req 2.6: every meal serves the stated household"


def test_meals_are_counted_from_food_needed_not_from_days():
    """
    A day is not a meal.

    Asking for one recipe per day under-fed every household in the meal-plan
    suite: invariants fell from 100% to 45% and budget used from 69% to 24%.
    The count comes from `min_grams_per_person_day`, the same figure the
    feasibility refusal uses, so the two cannot disagree about how much a
    household eats.
    """
    recipes = curated_recipes()
    one_day = meals_needed(recipes, household_size=1, days=1)
    week = meals_needed(recipes, household_size=1, days=7)
    bigger = meals_needed(recipes, household_size=4, days=7)

    assert week > one_day, "a week must need more meals than a day"
    assert bigger >= week, "a larger household must not need fewer meals"
    assert one_day >= 1


# ------------------------------------------------------------- the shortlist


def _shortlist_for(
    repo, *, household_size=3, days=5, budget=Decimal("120"), exclude=(), affordable=True
):
    """
    The shortlist as retrieval builds it, without running the whole graph.

    `affordable=False` stops before `affordable_set`, which is a different
    question: that step is GREEDY over the budget, so a narrower candidate list
    can admit recipes a wider one could not afford room for. Exclusions only
    ever remove, and that property is the one worth asserting.
    """
    from src.graph.nodes import current_freshness

    recipes = curated_recipes()
    resolved = resolve_ingredients(
        repo, recipes, near=None, locations=None, freshness=current_freshness()
    )
    refs = {id(rec): f"c{i}" for i, rec in enumerate(resolved.values(), 1)}
    citations, records = {}, {}
    from datetime import date

    from src.schemas.contract import Citation, SourceRef

    for rec in resolved.values():
        ref = refs[id(rec)]
        citations[ref] = Citation(
            ref=ref,
            store=rec.store,
            store_location=rec.store_location,
            product_name=rec.display_name,
            price_nzd=rec.price_nzd,
            unit=rec.unit,
            unit_price_nzd=rec.unit_price_nzd,
            on_special=rec.on_special,
            valid_date=date.fromisoformat(rec.valid_date),
            source=SourceRef(table=repo.table_name, pk=rec.store_key, sk=rec.product_key),
        )
        records[ref] = rec
    offers = shortlist(
        recipes,
        resolved,
        refs,
        citations,
        household_size=household_size,
        days=days,
        exclude_categories=list(exclude),
    )
    if affordable:
        offers = affordable_set(
            offers,
            citations,
            records,
            household_size=household_size,
            days=days,
            budget_nzd=budget,
        )
    return offers, citations, records


def test_the_shortlist_holds_only_recipes_every_ingredient_of_which_resolved(repo):
    """
    All or nothing, because a partly-costed recipe states a total nobody can pay.

    Against the 26-product fixture catalogue most curated recipes do NOT fully
    resolve, so this is the ordinary case rather than an edge one.
    """
    offers, _, _ = _shortlist_for(repo)
    assert offers, "the shortlist is empty against the fixtures; the test lost its input"
    for offer in offers:
        for ingredient in offer.recipe.ingredients:
            assert ingredient.key in offer.refs, (
                f"{offer.recipe.recipe_id} was offered without a price for {ingredient.key}"
            )


def test_the_shortlist_fits_the_budget_as_a_SET_not_recipe_by_recipe(repo):
    """
    Any subset of the offer must be affordable, which is what makes the model's
    choice safe whatever it picks.

    The first design capped each recipe at `budget / meals` and it was wrong for
    a reason worth keeping: `assemble_plan` aggregates packs across meals and
    rounds up once, so recipes sharing ingredients cost far less together than
    apart. That cap rejected on a number no plan ever pays and collapsed a
    29-recipe shortlist to one.
    """
    from src.graph.nodes.plan import assemble_plan
    from src.recipes.planning import recipes_to_draft

    budget = Decimal("60")
    offers, citations, records = _shortlist_for(repo, budget=budget)
    assert len(offers) >= 2, "need at least two recipes to test a set"

    merged: dict[str, str] = {}
    for offer in offers:
        merged.update(offer.refs)
    draft = recipes_to_draft(
        [o.recipe for o in offers], household_size=3, refs=merged, records=records
    )
    plan = assemble_plan(
        draft,
        citations,
        household_size=3,
        days=5,
        budget_nzd=budget,
        exclusions=[],
        repair_attempts=0,
    )
    assert plan.payable_total_nzd <= budget, (
        f"the whole offered set costs {plan.payable_total_nzd} against a {budget} budget, "
        f"so some selection the model could make would not fit"
    )


def test_dietary_exclusions_are_applied_to_the_shortlist_from_resolved_products(repo):
    """
    The vegan-safety property, at the level the shopper experiences it.

    `recipe_excluded_categories` scans ingredient NAMES and reports "Scrambled
    Eggs on Toast" as excluding nothing. The shortlist derives the answer from
    what each ingredient RESOLVES to, which is the only version that is true.
    """
    offers, _, _ = _shortlist_for(repo, exclude=("meat", "seafood", "dairy"), affordable=False)
    for offer in offers:
        for ingredient in offer.recipe.ingredients:
            assert offer.refs[ingredient.key], ingredient.key

    # AN EXCLUSION ONLY EVER REMOVES. Compared BEFORE the affordability step,
    # which is greedy over the budget and so can admit a recipe to the narrower
    # list that the wider one had no room for. Asserting the subset after that
    # step is a real property of neither, and it was the first version of this
    # test -- it failed, correctly, on a vegan list holding one recipe the
    # unrestricted list could not afford.
    unrestricted, _, _ = _shortlist_for(repo, affordable=False)
    assert {o.recipe.recipe_id for o in offers} < {o.recipe.recipe_id for o in unrestricted}, (
        "excluding meat, seafood and dairy did not narrow the shortlist"
    )


def test_a_vegan_request_is_never_served_meat_or_dairy(repo):
    """The invariant, through the whole graph rather than through a helper."""
    from src.graph.dietary import map_exclusions

    excluded, unsupported = map_exclusions(["vegan"])
    assert not unsupported
    response = run_turn(
        _request(
            "vegan dinners for 2 for 4 days",
            household_size=2,
            days=4,
            budget_nzd=50,
            dietary_exclusions=["vegan"],
        ),
        repo,
        ScriptedModelClient(),
    )
    event = _plan_event(response)
    if event is None:
        pytest.skip("no vegan plan possible against the fixture catalogue")
    citations = {c.ref: c for e in response.events if e.type == "citation" for c in [e.citation]}
    for meal in event.data.meals:
        for line in meal.ingredients:
            record = repo.cheapest_for_product(
                repo.resolve_product_key(citations[line.citation_ref].product_name) or "", limit=1
            )
            if record:
                assert record[0].category not in excluded, (
                    f"a vegan plan contains {record[0].display_name} ({record[0].category})"
                )


# --------------------------------------------------------------- selection


def _post_retrieval_state(repo, **hints) -> GroceryState:
    from src.graph.nodes import retrieve_prices
    from src.schemas.contract import Intent

    base = {"household_size": 3, "days": 5, "budget_nzd": Decimal("120")}
    base.update(hints)
    state = {
        "session_id": "s" * 8,
        "turn_id": "t" * 8,
        "message": "feed us",
        "intent": Intent.MEAL_PLAN,
        "constraints": {**base, "dietary_exclusions": hints.get("dietary_exclusions", [])},
        "events": [],
        "hints": {},
        "location": None,
    }
    state.update(retrieve_prices(cast(GroceryState, state), repo))
    # `cast`, like the state literals in tests/test_plan.py: GroceryState is a
    # TypedDict with every key required, and a node only ever sees the subset
    # the graph has filled in by the time it runs. Constructing all of them here
    # would assert a shape production never has.
    return cast(GroceryState, state)


def test_an_id_that_was_not_offered_is_dropped_not_corrected(repo):
    """
    A fabrication, treated the way a fabricated citation ref is.

    NOT resolved to a near match. `resolve_product_key` refuses fuzzy matching
    because a wrong match produces a confident wrong answer, and substituting a
    recipe the shopper did not choose is the same failure with a menu attached.
    """
    state = _post_retrieval_state(repo)
    real = (state.get("recipe_shortlist") or [])[0]

    class Liar(ScriptedModelClient):
        def _select_recipes(self, user):
            return RecipeSelection(recipe_ids=["r-does-not-exist", real])

    out = select_recipes(state, Liar())
    assert "r-does-not-exist" not in (out.get("selected_recipes") or [])
    assert real in out["selected_recipes"]


def test_a_selection_of_nothing_usable_falls_back_and_says_so(repo):
    state = _post_retrieval_state(repo)

    class AllFabricated(ScriptedModelClient):
        def _select_recipes(self, user):
            return RecipeSelection(recipe_ids=["nope-1", "nope-2"])

    out = select_recipes(state, AllFabricated())
    assert out["recipe_fallback"] == "no_selection"
    assert out["events"], "a fallback must tell the shopper which plan they got"
    assert "recipe collection" in out["events"][0].message
    assert route_after_recipe_selection(cast(GroceryState, {**state, **out})) == "compose"


def test_an_empty_shortlist_falls_back_without_calling_the_model(repo):
    """
    No shortlist means nothing to choose from, so there is nothing to ask.

    Spending a model call to be told what code already knows is the cost this
    check avoids, and on the meal-plan path it is a call on the binding quota.
    """
    state = _post_retrieval_state(repo)
    state["recipe_shortlist"] = []
    model = ScriptedModelClient()
    out = select_recipes(state, model)
    assert out["recipe_fallback"] == "no_shortlist"
    assert model.calls == [], "the model was called with nothing to select from"


def test_an_unreachable_model_is_not_reported_as_a_recipe_failure(repo):
    """
    "We could not reach the model" and "no recipe fitted" are different facts.

    The graph already refuses to collapse the first into "your budget does not
    stretch"; collapsing it into a fallback notice would be the same mistake in
    new clothes, and it would hide an outage behind a cheerful message.
    """
    from src.models.base import ModelError

    state = _post_retrieval_state(repo)

    class Unreachable(ScriptedModelClient):
        def _select_recipes(self, user):
            raise ModelError("Bedrock call failed: timeout")

    out = select_recipes(state, Unreachable())
    assert out.get("upstream_error")
    assert not out.get("recipe_fallback")
    assert route_after_recipe_selection(cast(GroceryState, {**state, **out})) == "upstream_failed"


def test_the_model_never_sees_a_price(repo):
    """
    The selection prompt carries ids, names and product names. No money.

    Asserted rather than assumed because this is the one prompt in the system
    built from RETRIEVED records, and a careless change to what `offered`
    carries is how a price would get in.
    """
    state = _post_retrieval_state(repo)
    model = ScriptedModelClient()
    select_recipes(state, model)
    prompts = [user for schema, user in model.prompts if schema == "RecipeSelection"]
    assert prompts, "no selection prompt was built"
    from src.schemas.contract import LITERAL_MONEY

    assert not LITERAL_MONEY.search(prompts[0]), "a price reached the selection prompt"


def test_what_the_model_returned_is_reported_apart_from_what_was_served(repo):
    """
    The node CORRECTS the model, so a scorecard must not read the corrected list.

    `select_recipes` tops a short selection up from unused recipes and trims
    meals that do not fit. Both are right for a plan and fatal for a gate: a
    node that repairs every mistake qualifies every model.
    """
    state = _post_retrieval_state(repo)
    offered = state.get("recipe_shortlist") or []
    assert len(offered) >= 2

    class Miser(ScriptedModelClient):
        def _select_recipes(self, user):
            return RecipeSelection(recipe_ids=[offered[0]])

    out = select_recipes(state, Miser())
    assert out["recipe_selection_model"] == [offered[0]], "the raw selection was not reported"
    assert len(out["selected_recipes"]) > 1, "the node did not top the short selection up"
