"""
Pilot Task 5 — location scope (Req 1.5/1.6) and price freshness (Req 8.4).

Both were declared and unimplemented. `Location` carried `lat`, `lon` and
`radius_km`, `PriceRecord` carried `lat` and `lon`, and NOTHING read either: a
shopper in Wellington got Auckland prices. `STALE_DATA` existed in the error
enum and appeared nowhere in `src/`.

Both filters are applied INSIDE the repository rather than to what it returns,
and that placement is the requirement. `cheapest_for_product` returns the
cheapest `limit` rows; filtering afterwards would return nothing for a product
whose five cheapest happen to be out of radius or out of date, and the graph
reads nothing as `no_data` — telling a shopper we have no price for something
stocked fresh at the shop down the road. That is the truncation defect Pilot
Task 6 fixed for the store filter, and these go down the same seam so it cannot
be reintroduced.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.graph.nodes import current_freshness
from src.models.scripted import ScriptedModelClient
from src.retrieval.filters import (
    AS_OF_ENV,
    FreshnessFilter,
    NearFilter,
    haversine_km,
    max_price_age_days,
)
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import ChatRequest, ChatResponse, ErrorCode, Location

# A fixture store, and a point ~490km away.
SYLVIA_PARK = (-36.8912, 174.8437)
WELLINGTON = (-41.29, 174.76)

FIXTURE_CAPTURE = date(2026, 7, 31)


def _turn(message: str, location: Location | None = None) -> ChatResponse:
    request = ChatRequest(
        version="1.0",
        session_id="sess-loc0001",
        turn_id="turn-loc0001",
        message=message,
        location=location,
    )
    return run_turn(request, InMemoryPriceRepository(), ScriptedModelClient())


def _stores(response: ChatResponse) -> set[str]:
    return {e.citation.store_location for e in response.events if e.type == "citation"}


def _codes(response: ChatResponse) -> list[ErrorCode]:
    return [e.code for e in response.events if e.type == "error"]


# --------------------------------------------------------------- geometry


def test_haversine_is_right_to_within_a_shopper_s_indifference():
    """~490km Auckland to Wellington. Nobody picks a supermarket on 50 metres."""
    km = haversine_km(*SYLVIA_PARK, *WELLINGTON)
    assert 480 < km < 500


def test_a_point_is_inside_its_own_radius():
    assert NearFilter(*SYLVIA_PARK, radius_km=1).covers(*SYLVIA_PARK)


# ----------------------------------------------------------- Req 1.5 / 1.6


def test_no_location_returns_national_results():
    """
    Req 1.6, and it is a REQUIREMENT rather than a fallback: no location means
    national results, never a refusal.
    """
    stores = _stores(_turn("cheapest butter"))
    assert len(stores) > 1


def test_a_location_restricts_to_stores_within_the_radius():
    """Req 1.5. Before this the coordinates were carried and never read."""
    near = _turn("cheapest butter", Location(lat=-36.9761, lon=174.7767, radius_km=5))
    assert _stores(near) == {"Mangere"}


def test_a_wider_radius_admits_more_stores_and_never_fewer():
    tight = _stores(_turn("cheapest butter", Location(lat=-36.9761, lon=174.7767, radius_km=5)))
    wide = _stores(_turn("cheapest butter", Location(lat=-36.9761, lon=174.7767, radius_km=50)))
    assert tight < wide


def test_a_location_with_nothing_in_range_says_so_rather_than_widening():
    """
    The dangerous direction is silently widening back to national. A shopper who
    asked for prices near them and got prices 500km away has been answered
    confidently and uselessly.
    """
    response = _turn("cheapest butter", Location(lat=-45.0, lon=170.0, radius_km=1))

    assert _stores(response) == set()
    assert any(e.type == "no_data" for e in response.events)
    assert _codes(response) == [], "no data is a success outcome, not an error"


def test_the_radius_is_applied_before_the_limit(monkeypatch):
    """
    The ordering that stops this becoming the truncation defect again.

    `cheapest_for_product` returns the cheapest `limit` rows. If the filter ran
    afterwards, asking for one result near Mangere would take the single
    cheapest row nationally and then discard it for being elsewhere — reporting
    no data for a product Mangere stocks.
    """
    repo = InMemoryPriceRepository()
    # DEVONPORT, deliberately: at $4.12 it is the DEAREST butter in the
    # catalogue, so the row we want is last in a price-ordered list and cannot
    # survive a limit applied before the filter. Mangere would prove nothing —
    # it happens to hold the national cheapest, so `recs[:1]` then filter
    # returns the right answer for the wrong reason, and this test passed
    # against a deliberately broken implementation until it was changed.
    near = NearFilter(-36.8296, 174.7954, 2)

    found = repo.cheapest_for_product("butter-500g", limit=1, near=near)

    assert len(found) == 1
    assert found[0].store_location == "Devonport"


# ------------------------------------------------------------- Req 8.4


def test_the_threshold_is_configuration_not_code():
    """Reviewable by someone who knows groceries, like the feasibility floor."""
    assert max_price_age_days() > 0


def test_fresh_and_stale_are_decided_against_the_injected_date():
    fresh = FreshnessFilter(as_of=date(2026, 8, 5), max_age_days=14)
    stale = FreshnessFilter(as_of=date(2026, 10, 1), max_age_days=14)

    assert fresh.is_fresh(FIXTURE_CAPTURE.isoformat())
    assert not stale.is_fresh(FIXTURE_CAPTURE.isoformat())
    assert stale.age_days(FIXTURE_CAPTURE.isoformat()) == 62


def test_the_reference_date_is_injectable_so_fixtures_do_not_rot(monkeypatch):
    """
    Committed fixtures carry a fixed capture date. Under a wall clock they drift
    into staleness as calendar time passes and every demo turns red on a day
    nobody chose. A suite whose result depends on when you run it is not a suite.
    """
    monkeypatch.setenv(AS_OF_ENV, "2026-08-05")
    assert current_freshness().as_of == date(2026, 8, 5)

    monkeypatch.setenv(AS_OF_ENV, "2026-10-01")
    assert current_freshness().as_of == date(2026, 10, 1)


def test_stale_only_data_is_refused_rather_than_presented(monkeypatch):
    """
    The honest outcome. Not `no_data` — we HAVE prices and saying otherwise
    would be false — and not a quiet answer either.

    The claim is not "here is a price" but "here is the CHEAPEST price", and a
    comparison drawn from stale rows can be wrong in a way a stale price alone
    is not, because the winner changes when a special rotates.
    """
    monkeypatch.setenv(AS_OF_ENV, "2026-10-01")
    response = _turn("cheapest butter")

    assert _codes(response) == [ErrorCode.STALE_DATA]
    assert not any(e.type == "price_comparison" for e in response.events)


def test_the_stale_refusal_names_the_capture_date(monkeypatch):
    """An apology without a date is not checkable."""
    monkeypatch.setenv(AS_OF_ENV, "2026-10-01")
    message = next(e.message for e in _turn("cheapest butter").events if e.type == "error")

    assert FIXTURE_CAPTURE.isoformat() in message


def test_stale_data_is_retryable(monkeypatch):
    """
    Unlike a budget that genuinely does not stretch, this resolves itself the
    moment ingestion runs. Marking it non-retryable would tell the client to
    stop asking for something that is about to become available.
    """
    monkeypatch.setenv(AS_OF_ENV, "2026-10-01")
    error = next(e for e in _turn("cheapest butter").events if e.type == "error")

    assert error.retryable is True


def test_a_meal_plan_is_not_costed_from_stale_prices(monkeypatch):
    """
    A plan priced from out-of-date rows is a shopping list whose total is
    fiction — and the total is the entire point of the budget.
    """
    monkeypatch.setenv(AS_OF_ENV, "2026-10-01")
    request = ChatRequest(
        version="1.0",
        session_id="sess-loc0001",
        turn_id="turn-loc0002",
        message="feed 2 people for 3 days on $60",
    )
    response = run_turn(request, InMemoryPriceRepository(), ScriptedModelClient())

    assert _codes(response) == [ErrorCode.STALE_DATA]
    assert not any(e.type == "meal_plan" for e in response.events)


def test_stale_is_distinguished_from_absent(monkeypatch):
    """
    "Everything I hold is out of date" and "I hold nothing for that" are
    different facts, and only one of them is about the product. Collapsing them
    would make the honest answer the false one.
    """
    monkeypatch.setenv(AS_OF_ENV, "2026-10-01")
    stale = _turn("cheapest butter")
    absent = _turn("cheapest wagyu ribeye")

    assert _codes(stale) == [ErrorCode.STALE_DATA]
    assert _codes(absent) == []
    assert any(e.type == "no_data" for e in absent.events)


@pytest.mark.parametrize("repo_kwargs", [{}, {"budget_nzd": Decimal("60")}])
def test_candidate_selection_honours_freshness(repo_kwargs):
    """The plan path filters the candidate POOL, before per-category selection."""
    from src.graph.nodes import MEAL_CATEGORIES

    repo = InMemoryPriceRepository()
    stale = FreshnessFilter(as_of=date(2026, 10, 1), max_age_days=14)

    assert (
        repo.candidates_for_budget(
            categories=MEAL_CATEGORIES,
            exclude_categories=[],
            freshness=stale,
            **repo_kwargs,
        )
        == []
    )
