"""
The feasibility floor, and the calibration behind its one policy number.

`min_grams_per_person_day` is the only judgement in the planning path — every
other figure is derived from the catalogue. A number like that drifts silently
unless something holds it to the reasons it was chosen for, so this file
encodes those reasons as tests rather than leaving them in a comment nobody
re-reads.

What it pins:
  * the config is present, well-formed, and in a sane range
  * the two requests the project already said must be refused ARE refused
  * the seven that must produce a plan are NOT refused
  * the choice is not balanced on a knife edge — neighbouring values still
    satisfy every expectation, so a small catalogue change cannot flip it

If one of these fails, the number needs revisiting with the catalogue in
front of you. It is a statement about these prices as much as about people.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.graph.feasibility import (
    CONFIG_PATH,
    min_grams_per_person_day,
    minimum_spend,
)
from src.retrieval.memory import InMemoryPriceRepository


@pytest.fixture(scope="module")
def repo() -> InMemoryPriceRepository:
    return InMemoryPriceRepository()


@pytest.fixture(scope="module")
def records(repo) -> list:
    return repo.all_records


# ------------------------------------------------------------------ config


def test_config_is_present_and_parses():
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert data["min_grams_per_person_day"] == min_grams_per_person_day()


def test_config_documents_that_it_is_unreviewed():
    """
    The number was set by inspecting fixtures, not by anyone who knows about
    food. That is a fine starting point and a bad thing to forget, so the
    caveat is part of the artifact rather than a line in a chat log.
    """
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert "NOT been reviewed" in data["_review"]


def test_floor_is_in_a_plausible_range():
    """
    Wide bounds on purpose: this catches a typo or a unit mix-up (60, 6000),
    not a considered change. 6000g/person/day is six kilos of food; 60g is a
    biscuit.
    """
    assert 100 <= min_grams_per_person_day() <= 2000


def test_a_non_integer_floor_is_rejected(tmp_path, monkeypatch):
    bad = tmp_path / "feasibility.json"
    bad.write_text(json.dumps({"min_grams_per_person_day": "600"}), encoding="utf-8")
    monkeypatch.setattr("src.graph.feasibility.CONFIG_PATH", bad)
    with pytest.raises(ValueError, match="positive integer"):
        min_grams_per_person_day()


# ------------------------------------------------------- the arithmetic


def test_minimum_spend_scales_with_household_and_days(records):
    one = minimum_spend(records, 1, 1)
    many = minimum_spend(records, 3, 7)
    assert one is not None and many is not None
    assert many == one * 21


def test_minimum_spend_uses_the_cheapest_food_by_weight(records):
    """No plan can beat it, which is what makes a budget below it impossible."""
    cheapest = min(r.price_nzd / r.pack_grams for r in records if r.pack_grams)
    expected = cheapest * 2 * 3 * min_grams_per_person_day()
    assert minimum_spend(records, 2, 3) == expected


def test_missing_weights_report_cannot_tell_rather_than_impossible():
    """
    A catalogue without weights is OUR data problem. Returning a floor there
    would refuse the user's turn for it; None lets the caller carry on.
    """
    class _Weightless:
        price_nzd = Decimal("3.00")
        pack_grams = 0

    assert minimum_spend([_Weightless()], 2, 3) is None


# --------------------------------------------------------- the calibration

# (household, days, budget, must_refuse, why)
CALIBRATION = [
    (5, 7, Decimal("15"), True, "eval case plan-006: genuinely impossible"),
    (2, 3, Decimal("5"), True, "tests/test_plan.py: infeasible-budget scenarios"),
    (3, 7, Decimal("40"), False, "eval case plan-001"),
    (2, 3, Decimal("20"), False, "eval case plan-002"),
    (2, 4, Decimal("35"), False, "eval case plan-004"),
    (1, 7, Decimal("35"), False, "eval case plan-005"),
    (2, 4, Decimal("40"), False, "eval case plan-008"),
    (3, 7, Decimal("90"), False, "Philip_demo/02"),
    (5, 3, Decimal("90"), False, "tests/test_observability.py affordable body"),
]


@pytest.mark.parametrize(
    ("household", "days", "budget", "must_refuse", "why"), CALIBRATION
)
def test_calibration_matches_existing_expectations(
    records, household, days, budget, must_refuse, why
):
    floor = minimum_spend(records, household, days)
    assert floor is not None
    refused = budget < floor
    assert refused is must_refuse, (
        f"{why}: {household} people x {days} days on ${budget} should "
        f"{'be refused' if must_refuse else 'produce a plan'}, floor is ${floor:.2f}"
    )


# Values BELOW this admit the $5-for-two-people case the project says must be
# refused; values above ~2100 would start refusing eval case plan-002, which
# must produce a plan. Both edges are computed from the catalogue in
# test_the_safe_range_is_what_the_docs_claim, so they cannot drift unnoticed.
KNOWN_TOO_LOW = [400, 500]
KNOWN_SAFE = [600, 700, 900, 1100]


@pytest.mark.parametrize("candidate", KNOWN_SAFE)
def test_safe_neighbours_agree_with_the_current_setting(
    records, candidate, monkeypatch
):
    """An ordinary catalogue price change must not quietly reverse a refusal."""
    monkeypatch.setattr(
        "src.graph.feasibility.min_grams_per_person_day", lambda: candidate
    )
    for hh, d, b, must_refuse, why in CALIBRATION:
        floor = minimum_spend(records, hh, d)
        assert floor is not None
        assert (b < floor) is must_refuse, f"{candidate}g disagrees on {why}"


@pytest.mark.parametrize("candidate", KNOWN_TOO_LOW)
def test_values_below_the_range_admit_a_request_that_must_be_refused(
    records, candidate, monkeypatch
):
    """
    Documents WHY 600 rather than something smaller, as a failing case rather
    than a claim. 500 is included because it is closer than it looks: the
    lower edge is 525g, not 400g as first assumed.
    """
    monkeypatch.setattr(
        "src.graph.feasibility.min_grams_per_person_day", lambda: candidate
    )
    admitted = []
    for hh, d, b, must, why in CALIBRATION:
        if not must:
            continue
        floor = minimum_spend(records, hh, d)
        assert floor is not None
        if b >= floor:
            admitted.append(why)
    assert admitted, f"{candidate}g was expected to be too low, but refuses everything"


def _safe_range(records) -> tuple[Decimal, Decimal]:
    """
    The window every expectation in CALIBRATION agrees on, in grams.

    Derived from all of them rather than from a hand-picked pair, because the
    binding case is not obvious: the upper edge is set by plan-001 (3 people,
    7 days, $40 -> ~1197g), not by the smaller-looking plan-002. Guessing it
    put 1500 in the "safe" list when it is not.
    """
    cheapest = min(r.price_nzd / r.pack_grams for r in records if r.pack_grams)
    lower = max(
        b / (cheapest * hh * d) for hh, d, b, must, _ in CALIBRATION if must
    )
    upper = min(
        b / (cheapest * hh * d) for hh, d, b, must, _ in CALIBRATION if not must
    )
    return lower, upper


def test_the_configured_value_sits_inside_the_safe_range(records):
    """
    The range is a property of the CURRENT prices. Deriving it here means a
    catalogue change that narrows it shows up as a failure rather than as a
    stale sentence in a config comment.
    """
    lower, upper = _safe_range(records)
    assert lower < min_grams_per_person_day() < upper, (
        f"configured {min_grams_per_person_day()}g is outside "
        f"{lower:.0f}g..{upper:.0f}g for the current catalogue"
    )


def test_the_range_is_wide_enough_to_be_meaningful(records):
    """
    A window barely wider than the value would mean the number is doing
    arbitrary work and the expectations behind it need rethinking, not
    retuning.
    """
    lower, upper = _safe_range(records)
    assert upper > lower * 2, (
        f"safe range {lower:.0f}g..{upper:.0f}g has collapsed; the "
        f"expectations in CALIBRATION are close to contradicting each other"
    )
