"""
The quota checker.

It exists because the throughput ceiling was derived by hand, written into a
document, and would be wrong the moment a quota moved or the routing changed —
and because the reflex fix for a throughput problem, asking AWS to raise the
limit, is unavailable for the models this service actually routes to. That was
assumed the wrong way round until someone ran one command.

These tests cover the parts that can be wrong without being obviously wrong.
Nothing here talks to AWS: quota payloads are passed in.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.check_quotas import (
    MEAL_PLAN_TASKS_MAX,
    MEAL_PLAN_TASKS_MIN,
    calls_per_model,
    ceiling,
    quota_for,
)
from src.graph.state import MAX_REPAIR_ATTEMPTS
from src.models.registry import ModelRegistry


def _q(name: str, value: float, adjustable: bool = False) -> dict:
    return {
        "QuotaName": f"Cross-region model inference requests per minute for {name}",
        "Value": value,
        "Adjustable": adjustable,
    }


# --------------------------------------------------------- matching quotas


def test_matches_a_model_despite_the_provider_prefix():
    """AWS says 'Anthropic Claude Haiku 4.5'; the catalogue says 'Claude Haiku 4.5'."""
    quotas = [_q("Anthropic Claude Haiku 4.5", 10, True)]
    found = quota_for(quotas, "Claude Haiku 4.5")
    assert found is not None and found["Value"] == 10


def test_a_longer_variant_does_not_shadow_the_base_model():
    """
    The trap this rule exists for: "Claude Sonnet 4.5" also matches
    "Claude Sonnet 4.5 V1 1M Context Length", a different model capped at
    1/min. Taking the smallest value would report a ceiling twenty times too
    low for a model the service never calls.
    """
    quotas = [
        _q("Anthropic Claude Sonnet 4.5 V1", 10, True),
        _q("Anthropic Claude Sonnet 4.5 V1 1M Context Length", 1, True),
    ]
    found = quota_for(quotas, "Claude Sonnet 4.5")
    assert found is not None and found["Value"] == 10


def test_other_quota_families_are_ignored():
    """
    'On-demand' and 'Global cross-region' are different limits for calls this
    service does not make: the configured ids carry apac./au. prefixes and so
    resolve through cross-region inference profiles. On-demand Nova Lite is
    10/min against cross-region's 20, so mixing them halves the reported
    ceiling.
    """
    quotas = [
        {
            "QuotaName": "On-demand model inference requests per minute for Amazon Nova Lite",
            "Value": 10,
            "Adjustable": False,
        },
        {
            "QuotaName": (
                "Global cross-region model inference requests per minute "
                "for Amazon Nova Lite"
            ),
            "Value": 5,
            "Adjustable": False,
        },
        _q("Amazon Nova Lite", 20),
    ]
    found = quota_for(quotas, "Amazon Nova Lite")
    assert found is not None and found["Value"] == 20


def test_an_unknown_model_reports_nothing_rather_than_guessing():
    assert quota_for([_q("Amazon Nova Lite", 20)], "Some Future Model") is None


# ------------------------------------------------------------- the ceiling


def test_the_binding_model_is_the_one_with_least_headroom_per_turn():
    """
    Not the smallest quota. A model called twice per turn at 20/min binds
    tighter than one called once at 25/min, which is exactly the case here and
    the reason a raw quota table misleads.
    """
    counts = {"Amazon Nova Lite": 2, "Amazon Nova Pro": 1}
    quotas = [_q("Amazon Nova Lite", 20), _q("Amazon Nova Pro", 25)]
    turns, binding, adjustable = ceiling(counts, quotas)
    assert (turns, binding) == (10.0, "Amazon Nova Lite")
    assert adjustable is False


def test_adjustability_is_reported_from_the_binding_model_only():
    """
    A raisable quota on a model that is not the constraint is not a way out,
    and reporting it as one is the mistake this whole script exists to stop.
    """
    counts = {"Amazon Nova Lite": 2, "Claude Haiku 4.5": 1}
    quotas = [
        _q("Amazon Nova Lite", 20, adjustable=False),
        _q("Anthropic Claude Haiku 4.5", 10, adjustable=True),
    ]
    _, binding, adjustable = ceiling(counts, quotas)
    assert binding == "Amazon Nova Lite"
    assert adjustable is False


def test_repairs_lower_the_ceiling():
    quotas = [_q("Amazon Nova Lite", 20), _q("Amazon Nova Pro", 25)]
    registry = ModelRegistry()
    best = ceiling(calls_per_model(registry, MEAL_PLAN_TASKS_MIN), quotas)[0]
    worst = ceiling(calls_per_model(registry, MEAL_PLAN_TASKS_MAX), quotas)[0]
    assert worst < best


# ------------------------------------------------------- the task sequence


def test_the_worst_case_matches_the_configured_repair_bound():
    """
    The task list is the one thing here that cannot be read from AWS or the
    registry, so it is the one thing that can silently drift from the graph.
    This catches the drift that is mechanical; a new model call added to a
    node still has to be added by hand.
    """
    extra = len(MEAL_PLAN_TASKS_MAX) - len(MEAL_PLAN_TASKS_MIN)
    assert extra == MAX_REPAIR_ATTEMPTS
    assert MEAL_PLAN_TASKS_MAX[: len(MEAL_PLAN_TASKS_MIN)] == MEAL_PLAN_TASKS_MIN


def test_calls_are_counted_against_the_live_routing():
    """Change config/models.json routing and this follows, without an edit."""
    registry = ModelRegistry()
    counts = calls_per_model(registry, MEAL_PLAN_TASKS_MIN)
    assert sum(counts.values()) == len(MEAL_PLAN_TASKS_MIN)
    for task in MEAL_PLAN_TASKS_MIN:
        assert registry.route(task).display_name in counts


def test_an_unroutable_task_does_not_inflate_the_ceiling():
    """
    Skipping it silently would UNDERCOUNT calls and report more headroom than
    exists, so the guard is that the count drops rather than the task being
    charged to some default model.
    """
    registry = ModelRegistry()
    counts = calls_per_model(registry, [*MEAL_PLAN_TASKS_MIN, "summarise_the_news"])
    assert sum(counts.values()) == len(MEAL_PLAN_TASKS_MIN)


@pytest.mark.parametrize("task", MEAL_PLAN_TASKS_MIN)
def test_every_task_in_the_sequence_is_routable(task):
    """An unroutable task in this list means the script is silently measuring
    a turn the service cannot actually perform."""
    ModelRegistry().route(task)
