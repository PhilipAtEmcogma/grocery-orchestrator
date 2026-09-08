"""
The variety metric, and the blind spot that justifies it. Legacy task 5.6.

THE CEILING THIS LIFTS. `evals/run_recipe_select.py` scored four rule
violations — fabrication, dietary, repetition, count — and both candidate
models passed all of them on every case. `config/models.json` recorded the
consequence plainly: *"BOTH MODELS SCORE 100% AND THE SUITE THEREFORE CANNOT
RANK THEM... Nothing here asks whether the MENU is good."*

`distinct mains` was reported and deliberately not scored, and the reason was
correct: an absolute count is not comparable across cases, because three meals
from a seven-recipe shortlist cannot be judged against four from a twelve-recipe
one. Averaging or thresholding one manufactures a gradient that means nothing.

**Normalising against what was ACHIEVABLE dissolves that objection**, and these
tests hold both halves: that the blind spot is real, and that the new number
closes it without re-introducing the incomparability.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.run_recipe_select import CaseResult, Scorecard
from src.graph.recipe_plan import curated_recipes


def _by_main() -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for recipe in curated_recipes():
        if recipe.ingredients:
            grouped.setdefault(recipe.ingredients[0].key, []).append(recipe.recipe_id)
    return grouped


# --------------------------------------------------------------------------
# the blind spot is real
# --------------------------------------------------------------------------


def test_the_catalogue_allows_a_valid_selection_that_is_one_meal_five_times():
    """
    THE CASE THE FOUR RULE CHECKS CANNOT SEE.

    Five DISTINCT pasta recipes exist. A model returning all five commits no
    fabrication (each was offered), no dietary breach, no repetition (the ids
    differ) and no under-count. It scores 100% on every existing check and
    serves pasta five nights running.

    If this ever stops being constructible the blind spot has closed on its
    own and the metric below is no longer load-bearing — so the fixture is
    asserted rather than assumed.
    """
    shared = {main: ids for main, ids in _by_main().items() if len(ids) >= 5}
    assert shared, (
        "no main ingredient has five recipes any more. The variety metric was "
        "built because a selection of five DISTINCT ids could still be one "
        "ingredient five times; re-check whether it still earns its place."
    )


# --------------------------------------------------------------------------
# and the metric sees it
# --------------------------------------------------------------------------


def _card(*pairs: tuple[int, int]) -> Scorecard:
    """A scorecard from (distinct_mains, achievable_mains) pairs."""
    card = Scorecard("test")
    for i, (distinct, achievable) in enumerate(pairs):
        card.results.append(
            CaseResult(
                case_id=f"c{i}",
                passed=True,
                distinct_mains=distinct,
                achievable_mains=achievable,
            )
        )
    return card


def test_five_pasta_dishes_score_far_below_five_different_meals():
    """
    Both selections pass every rule check. Only this number tells them apart.
    """
    # Locals, not repeated property access -- `variety` returns `float | None`
    # and a checker cannot narrow a property across statements.
    monotonous = _card((1, 5)).variety  # five meals, all pasta
    varied = _card((5, 5)).variety  # five meals, five mains
    assert monotonous is not None and varied is not None

    assert monotonous == pytest.approx(0.2)
    assert varied == pytest.approx(1.0)
    assert varied > monotonous


def test_a_narrow_shortlist_is_not_punished_for_being_narrow():
    """
    THE OBJECTION THIS METRIC HAD TO ANSWER, stated in config/models.json:
    "three meals from a seven-recipe shortlist cannot beat four from a
    twelve-recipe one".

    Under the raw count it cannot: 3 < 4, and the narrower request loses for
    reasons that are nothing to do with the model. Normalised, both took
    everything available and both score 1.0 — which is the only defensible
    reading, because neither could have done better.
    """
    narrow = _card((3, 3))
    wide = _card((4, 4))

    assert narrow.variety == wide.variety == pytest.approx(1.0)


def test_taking_less_than_was_available_scores_below_one():
    """The other direction: the ceiling has to bite when it is not reached."""
    assert _card((3, 5)).variety == pytest.approx(0.6)


def test_it_is_orthogonal_to_count_so_a_short_selection_is_not_punished_twice():
    """
    COUNT already scores under-selection. Measuring it again here would make one
    failure move two numbers, and a failure that moves two numbers is harder to
    read, not easier.

    Two meals chosen from a shortlist of ten mains: the ceiling is two, because
    two meals cannot show more than two mains. Both distinct, so 1.0 — the
    selection is monotonous in NO respect. That it was too short is COUNT's
    finding to report.
    """
    assert _card((2, 2)).variety == pytest.approx(1.0)


def test_an_average_over_no_observations_is_absent_rather_than_zero():
    """
    A model that selected nothing anywhere has no variety score, not a score of
    zero. Reporting 0.0 would rank it below a model that did badly, when in fact
    nothing was measured — the same distinction `treat_missing_data` draws for
    the alarms.
    """
    assert Scorecard("empty").variety is None
    assert _card((0, 0)).variety is None


def test_cases_with_nothing_selected_are_excluded_rather_than_scored_zero():
    """One unmeasurable case must not drag the average for the others."""
    card = _card((4, 4), (0, 0))
    assert card.variety == pytest.approx(1.0)


# --------------------------------------------------------------------------
# and it moves on real runs
# --------------------------------------------------------------------------


def test_the_scripted_baseline_reports_a_variety_number():
    """
    End to end through the real harness, so the metric cannot be correct in
    arithmetic and unwired in practice -- the failure mode `select_recipes`
    itself had for five days.
    """
    from evals.run_recipe_select import run
    from src.models.scripted import ScriptedModelClient
    from src.retrieval.filters import pin_to_fixture_snapshot

    pin_to_fixture_snapshot()
    card = run(ScriptedModelClient(), "scripted")

    # Bound to a local: `variety` is a PROPERTY, so a checker cannot carry an
    # `is not None` narrowing across two accesses -- each one could in principle
    # return something different. pyright said so, and it was right.
    variety = card.variety
    assert variety is not None
    # The scripted selector spreads across mains deliberately, so it should be
    # high -- but asserting "> 0.5" rather than a pinned figure, because this is
    # a property of the selector and the catalogue, and pinning it would make a
    # recipe addition fail a test about the metric.
    assert 0.5 < variety <= 1.0
