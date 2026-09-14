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

from src.graph.nodes._shared import _preference_unmet
from src.graph.nodes.intent import _reconcile
from src.models.scripted import ScriptedModelClient
from src.prompts.intent import SYSTEM_PROMPT, IntentResult
from src.recipes.base import preference_terms_met
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


def test_a_blank_extraction_does_not_mask_a_hinted_preference():
    """
    A live model returns `['']` -- a non-empty list of one empty string -- for
    a bare "a meal plan please". That is truthy, so a reconciler that filtered
    blanks only AFTER choosing extracted-or-hinted would let the junk win the
    `or` and then filter it to nothing, silently discarding the hint. This was
    a real defect found live: a hinted "dinosaurs" was dropped and the turn
    built a plan about something else instead of refusing.

    An all-blank extraction must fall through to the hint, exactly as an empty
    list does. A REAL extracted preference still replaces the hint -- that is
    `test_a_message_preference_replaces_a_hinted_one`, and it must still hold.
    """
    constraints, _ = _reconcile(
        _extracted(preferred_ingredients=[""]),
        {"preferred_ingredients": ["dinosaurs"]},
    )
    assert constraints.get("preferred_ingredients") == ["dinosaurs"]


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


def test_a_preference_offered_but_trimmed_out_is_still_reported(repo):
    """
    THE SECOND SILENT DROP, one layer below the first.

    `_cost_within_budget` removes the meal with the HIGHEST MARGINAL COST, and
    the preferred meal is very often the dearest -- so at $25 the fixtures offer
    Chicken and Rice Bake ($16.67, affordable on its own), the model selects it,
    and the trim then takes it out again. The first version of this notice read
    the OFFER SET, judged the preference met, and printed a porridge plan with no
    explanation.

    Found by dry-running demo 31, not by a test, which is why the demo exists.
    """
    response = run_turn(
        _request(
            "a chicken dinner for a flat of 3 for 3 days",
            household_size=3,
            budget_nzd=Decimal("25"),
            days=3,
        ),
        repo,
        ScriptedModelClient(),
    )

    plans = [e for e in response.events if e.type == "meal_plan"]
    assert plans, "the turn produced no plan"
    served = " ".join(m.name.lower() for m in plans[0].data.meals)
    assert "chicken" not in served, "the fixture no longer reproduces the trim; pick a new budget"

    notices = [e.message for e in response.events if e.type == "notice"]
    assert any("chicken" in n and "this plan has none" in n for n in notices), (
        f"chicken was trimmed out of the plan with no explanation: {notices}"
    )


def test_a_preference_the_plan_honours_gains_no_notice(repo):
    """
    The other half. At $30 the same request keeps the chicken, so explaining its
    absence would be a false statement about a plan that contains it.
    """
    response = run_turn(
        _request(
            "a chicken dinner for a flat of 3 for 3 days",
            household_size=3,
            budget_nzd=Decimal("30"),
            days=3,
        ),
        repo,
        ScriptedModelClient(),
    )

    plans = [e for e in response.events if e.type == "meal_plan"]
    served = " ".join(m.name.lower() for m in plans[0].data.meals)
    assert "chicken" in served, "the fixture no longer affords chicken here"

    notices = [e.message for e in response.events if e.type == "notice"]
    assert not any("this plan has none" in n for n in notices), notices


# ----------------------------------------------------- the notice, as a sentence


def test_the_notice_blames_the_budget_when_a_recipe_existed():
    """
    The actionable case: a matching recipe exists and the money removed it, so
    the fix is in the shopper's hands and the message quotes the figure they
    would need.
    """
    message = _preference_unmet(
        ["seafood"],
        ("Prawn and Rice Stir Fry", "35.04"),
        household_size=3,
        days=3,
        budget_nzd=Decimal("30"),
    )

    assert "seafood" in message
    assert "$30.00" in message
    assert "$35.04" in message
    assert "Prawn and Rice Stir Fry" in message
    assert "3 people over 3 days" in message


def test_the_notice_blames_the_catalogue_when_no_recipe_existed():
    """
    NOT interchangeable with the message above, and this is the whole reason
    retrieval records the cheapest match per term. Telling a shopper to raise
    their budget for a recipe we do not have sends them to spend more for a
    result that cannot happen.
    """
    message = _preference_unmet(
        ["ostrich"], None, household_size=2, days=1, budget_nzd=Decimal("50")
    )

    assert "ostrich" in message
    assert "Raising the budget won't change that." in message
    assert "$50.00" not in message


def test_the_notice_reads_naturally_for_one_person_and_one_day():
    """Pluralisation, because "1 people over 1 days" reads as a machine talking."""
    message = _preference_unmet(
        ["seafood"],
        ("Tuna Pasta Salad", "20.00"),
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
    message = _preference_unmet(
        ["seafood"],
        ("Tuna Pasta Salad", "20.00"),
        household_size=2,
        days=2,
        budget_nzd=None,
    )

    assert "No seafood meal fits for 2 people over 2 days" in message


def test_two_unmet_preferences_get_a_notice_each_with_its_own_reason():
    """
    ONE NOTICE PER TERM, because the reasons differ. "seafood, ostrich" can be
    seafood priced out AND ostrich absent from the catalogue, and a single
    sentence would have to pick one explanation and be wrong about the other.
    """
    priced_out = _preference_unmet(
        ["seafood"],
        ("Prawn and Rice Stir Fry", "35.04"),
        household_size=3,
        days=3,
        budget_nzd=Decimal("30"),
    )
    absent = _preference_unmet(
        ["ostrich"], None, household_size=3, days=3, budget_nzd=Decimal("30")
    )

    assert "$35.04" in priced_out
    assert "Raising the budget won't change that." in absent
    assert "Raising the budget" not in priced_out


# --------------------------------------------- the plan-level preference match


def test_the_plan_match_reads_the_basket_not_the_recipe_ids():
    """
    `preference_terms_met` answers "does the basket contain it", which is the
    only question worth asking once the trim has run. Category first, so a
    prawn product answers "seafood" without the word appearing anywhere.
    """
    assert preference_terms_met(["seafood"], [("Cooked Peeled Prawns", "seafood")]) == {"seafood"}
    assert preference_terms_met(["chicken"], [("Whole Chicken", "meat")]) == {"chicken"}
    assert preference_terms_met(["seafood"], [("Rolled Oats", "pantry")]) == set()


def test_the_plan_match_covers_a_plan_built_without_recipes():
    """
    The free-composition path has no recipe ids at all. A recipe-level check
    there would report every preference unmet even when the composed basket
    does contain the food -- a false claim in the opposite direction.
    """
    composed = [("Rolled Oats", "pantry"), ("Yellowfin Tuna Loin", "seafood")]
    assert preference_terms_met(["seafood"], composed) == {"seafood"}


def test_the_notice_says_competition_not_price_when_the_match_was_affordable():
    """
    "No chicken meal fits at $25.00 -- the cheapest I can build is $16.67" is a
    sentence that argues with itself, and it was the first draft.

    $16.67 is what ONE such meal costs. The household needs three, the trim
    drops the dearest marginal meal until the basket fits, and the preferred
    meal is usually the dearest. So the reason is competition for the budget,
    not the price of the meal -- and the shopper's lever differs: a small raise
    genuinely helps here, where clearing the full figure is needed below.
    """
    message = _preference_unmet(
        ["chicken"],
        ("Chicken and Rice Bake", "16.67"),
        household_size=3,
        days=3,
        budget_nzd=Decimal("25"),
    )

    assert "would fit on its own" in message
    assert "$16.67" in message
    assert "$25.00" in message
    assert "No chicken meal fits" not in message


def test_the_notice_says_too_dear_when_the_match_exceeds_the_budget_alone():
    """The other branch: one such meal costs more than the whole budget."""
    message = _preference_unmet(
        ["seafood"],
        ("Prawn and Rice Stir Fry", "35.04"),
        household_size=3,
        days=3,
        budget_nzd=Decimal("30"),
    )

    assert "No seafood meal fits at $30.00" in message
    assert "would fit on its own" not in message


# --------------------------------------- preference unavailable: refuse the turn
#
# A preference we cannot match to any product in the current data refuses with
# PREFERENCE_UNAVAILABLE instead of quietly serving an unrelated plan with a
# footnote -- the footnote is right for a food we carry but cannot afford, and
# wrong for one we cannot match at all. To the strict resolver a real food we do
# not stock ("quinoa") and a nonsense word ("dinosaurs") are identical, so both
# take this path with ONE honest message that points at the data. LENIENT: one
# available preference is enough to proceed. Driven through hints, because the
# scripted extractor only knows a fixed preference vocabulary -- the graph path
# (retrieval -> route -> refuse) is what is under test.


def test_a_nonsense_preference_refuses_the_turn(repo):
    """
    "a dinosaur meal plan" builds no plan. We cannot match it to any product, so
    we say so and ask for a different ingredient rather than serving porridge
    with a note that dinosaurs could not be worked in.
    """
    response = run_turn(
        _request(
            "a dinosaur meal plan",
            household_size=2,
            budget_nzd=Decimal("200"),
            days=3,
            preferred_ingredients=["dinosaurs"],
        ),
        repo,
        ScriptedModelClient(),
    )

    plans = [e for e in response.events if e.type == "meal_plan"]
    assert not plans, "an unavailable preference must not produce a plan"

    errors = [e for e in response.events if e.type == "error"]
    assert errors, "the turn should refuse with an error"
    assert errors[0].code == "PREFERENCE_UNAVAILABLE"
    assert errors[0].retryable is True, "asking for something we carry is the move"
    assert "dinosaurs" in errors[0].message
    # The message points at the DATA, not at comprehension. We must not claim to
    # have failed to understand -- that would be wrong for a real unstocked food.
    assert "understand" not in errors[0].message.lower()
    assert "match" in errors[0].message.lower()


def test_a_real_but_unstocked_preference_refuses_the_same_way(repo):
    """
    THE POINT OF THE RENAME. "quinoa" is a real food; the fixture catalogue has
    none, and the strict resolver cannot tell it from "dinosaurs". Both refuse
    with the SAME PREFERENCE_UNAVAILABLE message pointing at the data -- we do
    not pretend to know it is real-but-unstocked versus not-food, because
    distinguishing them would need a food ontology we deliberately do not have.
    """
    response = run_turn(
        _request(
            "a quinoa meal plan",
            household_size=2,
            budget_nzd=Decimal("200"),
            days=3,
            preferred_ingredients=["quinoa"],
        ),
        repo,
        ScriptedModelClient(),
    )

    plans = [e for e in response.events if e.type == "meal_plan"]
    assert not plans, "no plan for an unmatched food"
    errors = [e for e in response.events if e.type == "error"]
    assert errors and errors[0].code == "PREFERENCE_UNAVAILABLE"
    assert "quinoa" in errors[0].message
    assert "understand" not in errors[0].message.lower()


def test_a_real_but_unaffordable_preference_still_plans_with_a_notice(repo):
    """
    THE DISTINCTION THAT MADE THIS WORTH DESIGNING. Seafood is a food we carry
    (a known category); if it is priced out that is a budget outcome, not an
    availability one. So the turn still produces a plan and NOTICES the unmet
    seafood -- it must not be swept into the preference-unavailable refusal.
    """
    response = run_turn(
        _request(
            "a seafood meal plan",
            household_size=3,
            budget_nzd=Decimal("30"),
            days=3,
            preferred_ingredients=["seafood"],
        ),
        repo,
        ScriptedModelClient(),
    )

    assert not [e for e in response.events if e.type == "error"], (
        "a real food priced out is a notice, never a refusal"
    )
    assert [e for e in response.events if e.type == "meal_plan"], "a plan must still be produced"
    notices = [e.message for e in response.events if e.type == "notice"]
    assert any("seafood" in n for n in notices)


def test_a_mixed_preference_is_lenient_and_plans_around_the_available_one(repo):
    """
    "chicken and dinosaurs": chicken is available, so the turn proceeds and
    plans around chicken. One unmatchable word beside a valid one does not
    refuse the whole request -- `finalise`'s existing notice reports the rest.
    """
    response = run_turn(
        _request(
            "chicken and dinosaurs for dinner",
            household_size=3,
            budget_nzd=Decimal("200"),
            days=3,
            preferred_ingredients=["chicken", "dinosaurs"],
        ),
        repo,
        ScriptedModelClient(),
    )

    assert not [e for e in response.events if e.type == "error"], (
        "one recognised preference is enough to proceed (lenient)"
    )
    assert [e for e in response.events if e.type == "meal_plan"], "chicken plans a meal"


def test_a_plan_with_no_preference_is_untouched_by_the_refusal(repo):
    """The refusal fires only on a STATED preference; an ordinary plan is unaffected."""
    response = run_turn(
        _request("meal plan for the week", household_size=3, budget_nzd=Decimal("60"), days=3),
        repo,
        ScriptedModelClient(),
    )

    assert not [
        e for e in response.events if e.type == "error" and e.code == "PREFERENCE_UNAVAILABLE"
    ]
    assert [e for e in response.events if e.type == "meal_plan"], "a normal plan still builds"
