"""
Pacing tests for the deployed-latency baseline (Pilot Task 16, gate G6).

THE BUG THESE EXIST FOR SHIPPED AND WAS NEVER RUN. `scripts/measure_latency.py`
paced at a flat 9 turns/min, which was inside the Nova Lite quota when a
meal-plan turn made two Nova Lite calls. Pilot Task 15c added `select_recipes`
to every meal-plan turn on 2026-08-31 and it reached production on 2026-09-04,
making that three calls -- so the default became 27 requests/min against a cap
of 20, and the first serious run would have thrown its meal-plan half into
throttling and reported the result as latency.

Nobody would have noticed from the output. `docs/THROUGHPUT-AND-SCALING.md`
records why: throttling arrives at the TAIL of a run, so it reads as "the last
few turns were slow" rather than "the account stopped answering". Three model
bands were scored that way before anyone checked the quota.

So the property under test is not "the arithmetic is right". It is **a run
cannot be paced over the binding quota by default**, whatever mix of turns it
is given, and the pacing re-derives itself when the graph gains a call.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from measure_latency import (
    MEAL_PLANS,
    NOVA_LITE_CALLS,
    NOVA_LITE_RPM,
    PRICE_CHECKS,
    gap_seconds,
)


@pytest.mark.parametrize("kind", sorted(NOVA_LITE_CALLS))
def test_no_turn_kind_paces_over_the_binding_quota(kind: str) -> None:
    """
    The whole point. For every kind of turn, the resulting request rate must sit
    at or under the quota -- not on average, and not for the mix someone
    happened to test with.
    """
    turns_per_min = 60.0 / gap_seconds(kind)
    calls_per_min = turns_per_min * NOVA_LITE_CALLS[kind]
    assert calls_per_min <= NOVA_LITE_RPM, (
        f"{kind} paces at {calls_per_min:.1f} model calls/min against a "
        f"{NOVA_LITE_RPM}/min cap -- this is the 2026-09-04 defect returning"
    )


def test_a_costlier_turn_is_paced_more_slowly() -> None:
    """
    Pacing must follow the CALL COST, not the turn count. A flat rate treats a
    two-call price check and a three-call meal plan as equal, which is exactly
    how the old default went over quota on one of them while staying inside it
    on the other.
    """
    assert NOVA_LITE_CALLS["meal_plan"] > NOVA_LITE_CALLS["price_check"]
    assert gap_seconds("meal_plan") > gap_seconds("price_check")


def test_the_old_flat_default_would_now_breach_the_quota() -> None:
    """
    The regression, pinned as a test rather than left in a commit message.

    9 turns/min was the default until 2026-09-04 and was correct for a two-call
    meal plan. Against today's three calls it is 27/min. If this ever stops
    being true -- because a call was removed from the meal-plan path -- that is
    worth knowing too, and this fails to say so.
    """
    old_default_rate = 9.0
    calls = old_default_rate * NOVA_LITE_CALLS["meal_plan"]
    assert calls > NOVA_LITE_RPM, (
        "the old flat 9/min default no longer breaches the quota; the meal-plan "
        "call count must have changed, so re-check NOVA_LITE_CALLS"
    )


def test_the_flat_override_is_still_available_and_can_exceed_the_quota() -> None:
    """
    Measuring throttling on purpose is a legitimate thing to want -- gate G6's
    second phase does exactly that. The override exists so that breaching the
    quota is a thing you ASK for rather than a thing you get by default.
    """
    assert gap_seconds("meal_plan", flat_rpm=30) == pytest.approx(2.0)
    assert (
        60.0 / gap_seconds("meal_plan", flat_rpm=30) * NOVA_LITE_CALLS["meal_plan"] > NOVA_LITE_RPM
    )


def test_every_probe_message_classifies_as_exactly_one_kind() -> None:
    """
    The pacing looks a message up by membership in MEAL_PLANS, so a message in
    both lists (or in neither) would be paced as the wrong kind -- silently,
    since the run would still complete.
    """
    assert not set(PRICE_CHECKS) & set(MEAL_PLANS)
    assert set(NOVA_LITE_CALLS) == {"price_check", "meal_plan"}
