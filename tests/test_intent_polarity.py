"""
Polarity: a food asked FOR is not a food ruled OUT.

THE DEFECT THIS FILE PINS, 2026-09-14. A shopper typed "i would like to have
seafood meal planned for me" and got a banana-and-oat-porridge plan under the
sentence "All seafood has been excluded as requested." Extraction had returned
`dietary_exclusions: ["seafood"]`, the dietary filter had honoured it exactly as
designed, and the prose had confirmed it exactly as designed. Every component
behaved correctly on an input that inverted the request.

WHY NOTHING DOWNSTREAM COULD CATCH IT. `classify_intent` is the only place in
the system that can still see whether the shopper said "seafood" or "no
seafood". By the time `map_exclusions` receives the term it is a decision, not a
phrase, and a guard there would have to re-read the message to second-guess it —
two readers of the same sentence, which is the arrangement that produced this
bug in the first place. So the fix is at the only place with the evidence, and
the tests here split accordingly:

  * the PROMPT rule, asserted as text, because the live model is what reads it
    and no offline client can speak for it. `evals/cases/intent.json` pol-001
    to pol-005 score the model itself;
  * the PLUMBING, asserted through the graph — that a preference reaches the
    shortlist, reorders it, and is reported when it cannot be met.

THE SCRIPTED CLIENT COULD NOT REPRODUCE THIS, which is why 1,000 passing tests
said nothing about it. `_extract_exclusions` has required "no seafood" or
"without seafood" since it was written, so the offline suite only ever exercised
the negative case. That asymmetry — a stub stricter than the model it stands in
for — is the reason a one-line prompt rule needed a test file.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.graph.nodes.intent import _reconcile
from src.models.scripted import ScriptedModelClient
from src.prompts.intent import SYSTEM_PROMPT, IntentResult
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import ChatRequest, ClientHints, Intent

# ------------------------------------------------------------ the prompt rule


def test_the_prompt_states_polarity_as_a_rule_not_an_example():
    """
    The old prompt said "use the canonical form when the user's phrasing
    MATCHES one", and "seafood" matches "seafood". Every example given was a
    negation, but an example is not a rule — the model generalised from the
    field name and got it backwards.
    """
    assert "POLARITY FIRST" in SYSTEM_PROMPT
    assert "RULING IT OUT" in SYSTEM_PROMPT
    assert "ASKING FOR IT" in SYSTEM_PROMPT
    assert "merely CONTAINS the word" in SYSTEM_PROMPT


def test_the_prompt_carries_the_failing_message_as_a_worked_example():
    """
    The exact input that failed, with both fields spelled out. A rule stated
    abstractly is one the model can agree with and still misapply.
    """
    assert "I would like a seafood meal plan" in SYSTEM_PROMPT
    assert 'preferred_ingredients ["seafood"]' in SYSTEM_PROMPT


def test_the_prompt_covers_both_polarities_in_one_message():
    """ "a seafood dinner, no dairy" must not collapse to one field or the other."""
    assert "A single message can do both" in SYSTEM_PROMPT


def test_the_schema_has_somewhere_to_put_an_affirmative_food():
    """
    The structural half of the fix. While `dietary_exclusions` was the only
    food-shaped field, a model holding a salient food noun had one slot for it
    and that slot meant the opposite of what the shopper wanted.
    """
    assert "preferred_ingredients" in IntentResult.model_fields
    assert IntentResult(intent=Intent.MEAL_PLAN, confidence=1.0).preferred_ingredients == []


# --------------------------------------------------------- the scripted client


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("i would like to have seafood meal planned for me", ["seafood"]),
        ("a high-protein seafood dinner for 2 people under $30 tonight", ["seafood"]),
        ("can you do something with chicken and rice", ["chicken", "rice"]),
        # Position order, not table order: `_PREFERENCE_TERMS` lists chicken
        # before rice, so a stub returning table order would pass the case above
        # and fail this one.
        ("can you do something with rice and chicken", ["rice", "chicken"]),
    ],
)
def test_scripted_client_reads_an_affirmative_food_as_a_preference(message, expected):
    assert ScriptedModelClient._extract_preferences(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "no seafood please",
        "meal plan without fish",
        "no meat or seafood this week",
        "a fish-free week",
    ],
)
def test_scripted_client_does_not_read_a_negated_food_as_a_preference(message):
    """
    The two extractors must agree on what a negation is. A message landing in
    BOTH fields would build a plan around a food the dietary filter had just
    removed — and the preference notice would then report it unmet, which is
    true but for the wrong reason.
    """
    assert ScriptedModelClient._extract_preferences(message) == []


def test_a_negated_food_still_becomes_an_exclusion():
    """This fix must not turn exclusions into preferences on the way past."""
    assert ScriptedModelClient._extract_exclusions("no seafood please") == ["seafood"]


def test_punctuation_stops_the_negation_run():
    """
    "no dairy, but seafood" negates dairy and asks for seafood. Without the
    punctuation barrier the two-word lookbehind would swallow the comma and
    read the seafood as negated too.
    """
    assert ScriptedModelClient._extract_preferences("no dairy, but seafood please") == ["seafood"]


# ------------------------------------------------------------- reconciliation


def _extracted(**kwargs) -> IntentResult:
    return IntentResult(intent=Intent.MEAL_PLAN, confidence=0.9, **kwargs)


def test_preferences_reach_the_constraints():
    constraints, _ = _reconcile(_extracted(preferred_ingredients=["seafood"]), {})
    assert constraints.get("preferred_ingredients") == ["seafood"]


def test_a_message_preference_replaces_a_hinted_one():
    """
    NOT additive, unlike exclusions. Someone who set "chicken" in the UI and
    now types "actually, seafood" is replacing it; unioning would build a plan
    around both and honour neither.
    """
    constraints, _ = _reconcile(
        _extracted(preferred_ingredients=["seafood"]),
        {"preferred_ingredients": ["chicken"]},
    )
    assert constraints.get("preferred_ingredients") == ["seafood"]


def test_a_hinted_preference_survives_when_the_message_states_none():
    constraints, _ = _reconcile(_extracted(), {"preferred_ingredients": ["chicken"]})
    assert constraints.get("preferred_ingredients") == ["chicken"]


def test_exclusions_are_still_additive():
    """
    The asymmetry is the point: dropping a dietary restriction is the dangerous
    direction, and dropping a preference is not.
    """
    constraints, _ = _reconcile(
        _extracted(dietary_exclusions=["seafood"]),
        {"dietary_exclusions": ["dairy-free"]},
    )
    assert constraints.get("dietary_exclusions") == ["dairy-free", "seafood"]


def test_preference_order_is_the_order_the_shopper_said_them():
    """
    `_preference_rank` ranks by position, so sorting this list would silently
    reassign which preference wins the budget.
    """
    constraints, _ = _reconcile(_extracted(preferred_ingredients=["seafood", "chicken"]), {})
    assert constraints.get("preferred_ingredients") == ["seafood", "chicken"]


def test_blank_preferences_are_dropped():
    constraints, _ = _reconcile(_extracted(preferred_ingredients=["", "  ", "seafood"]), {})
    assert constraints.get("preferred_ingredients") == ["seafood"]


# ---------------------------------------------------------------- end to end


@pytest.fixture(scope="module")
def repo() -> InMemoryPriceRepository:
    return InMemoryPriceRepository()


def _request(message: str, **hints) -> ChatRequest:
    return ChatRequest(
        session_id="sess-pol001",
        turn_id="turn-pol001",
        message=message,
        hints=ClientHints(**hints) if hints else None,
    )


def test_an_affirmative_seafood_request_excludes_nothing(repo):
    """
    THE regression test, at the level the shopper experienced it.

    The failure was not only the missing fish — it was being told that seafood
    had been excluded at their own request. A plan that cannot afford fish is a
    budget limit; a plan that reports excluding it is a false statement about
    what the shopper asked for.
    """
    response = run_turn(
        _request(
            "i would like to have seafood meal planned for me",
            household_size=3,
            budget_nzd=Decimal("30"),
            days=3,
        ),
        repo,
        ScriptedModelClient(),
    )

    plans = [e for e in response.events if e.type == "meal_plan"]
    assert plans, "an affirmative request must still produce a plan"
    assert plans[0].data.dietary_exclusions_applied == []


def test_the_unmet_preference_is_reported_rather_than_dropped(repo):
    """
    The silent-drop half. `retrieve_prices` already reports items it could not
    price and items it did not check; a preference it could not fit was the one
    request shape with no way to say so.
    """
    response = run_turn(
        _request(
            "i would like to have seafood meal planned for me",
            household_size=3,
            budget_nzd=Decimal("30"),
            days=3,
        ),
        repo,
        ScriptedModelClient(),
    )

    notices = [e.message for e in response.events if e.type == "notice"]
    assert any("seafood" in n for n in notices), (
        f"the shopper asked for seafood and got none, with no explanation: {notices}"
    )


def test_a_plan_with_no_stated_preference_gains_no_notice(repo):
    """The half that stops this becoming an assistant that explains itself twice."""
    response = run_turn(
        _request("meal plan for the week", household_size=3, budget_nzd=Decimal("60"), days=3),
        repo,
        ScriptedModelClient(),
    )

    notices = [e.message for e in response.events if e.type == "notice"]
    assert not any("this plan has none" in n for n in notices)


# ----------------------------------------------------- the unmet-preference notice


def _offer(name: str, payable: str):
    """A RecipeOffer stub carrying only what the notice reads off it."""
    from src.graph.recipe_plan import RecipeOffer
    from src.recipes.base import Recipe, RecipeIngredient

    recipe = Recipe(
        recipe_id="nz#test",
        name=name,
        category="Seafood",
        area="New Zealand",
        ingredients=(
            RecipeIngredient(key="tuna", name="Tuna", measure="90g", grams_per_serving=90),
        ),
        attribution="test",
        serves=2,
    )
    return RecipeOffer(
        recipe=recipe, refs={"tuna": "c1"}, payable_nzd=Decimal(payable), preference_rank=0
    )


def test_the_notice_blames_the_budget_when_a_recipe_existed():
    """
    The actionable case: a matching recipe exists and the money removed it, so
    the fix is in the shopper's hands and the message quotes the figure they
    would need.
    """
    from src.graph.nodes.retrieval import _preference_unmet

    message = _preference_unmet(
        ["seafood"],
        [_offer("Prawn and Rice Stir Fry", "35.04"), _offer("Tuna Pasta Salad", "68.06")],
        household_size=3,
        days=3,
        budget_nzd=Decimal("30"),
    )

    assert "seafood" in message
    assert "$30.00" in message
    # The CHEAPEST matching option, not the first or the dearest.
    assert "$35.04" in message
    assert "Prawn and Rice Stir Fry" in message
    assert "3 people over 3 days" in message


def test_the_notice_blames_the_catalogue_when_no_recipe_existed():
    """
    NOT interchangeable with the message above, and this is the whole reason
    the pre-budget shortlist is kept. Telling a shopper to raise their budget
    for a recipe we do not have sends them to spend more for a result that
    cannot happen.
    """
    from src.graph.nodes.retrieval import _preference_unmet

    message = _preference_unmet(["ostrich"], [], household_size=2, days=1, budget_nzd=Decimal("50"))

    assert "ostrich" in message
    assert "Raising the budget won't change that." in message
    assert "$50.00" not in message


def test_the_notice_reads_naturally_for_one_person_and_one_day():
    """Pluralisation, because "1 people over 1 days" reads as a machine talking."""
    from src.graph.nodes.retrieval import _preference_unmet

    message = _preference_unmet(
        ["seafood"],
        [_offer("Tuna Pasta Salad", "20.00")],
        household_size=1,
        days=1,
        budget_nzd=Decimal("10"),
    )

    assert "1 person over 1 day" in message


def test_the_notice_omits_the_budget_clause_when_none_was_stated():
    """
    A budget is absent, not zero. `affordable_set` returns everything in that
    case, so an unmet preference here is about the catalogue, and inventing
    "at $0.00" would be a claim the shopper never made.
    """
    from src.graph.nodes.retrieval import _preference_unmet

    message = _preference_unmet(
        ["seafood"],
        [_offer("Tuna Pasta Salad", "20.00")],
        household_size=2,
        days=2,
        budget_nzd=None,
    )

    assert "$" not in message.split("$20.00")[0]
    assert "No seafood meal fits for 2 people over 2 days" in message


def test_multiple_unmet_preferences_are_named_together():
    from src.graph.nodes.retrieval import _preference_unmet

    message = _preference_unmet(["seafood", "lamb"], [], household_size=2, days=2, budget_nzd=None)

    assert "seafood and lamb" in message
