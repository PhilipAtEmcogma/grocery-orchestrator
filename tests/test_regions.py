"""
Pilot Task 5b — a shopper can name a place instead of sending coordinates.

`Location` required `lat` and `lon`, so a client could not say "North Shore" at
all — and four of the five demo scenarios in `datasets/DATA_SCHEMA.md` ask
exactly that. Worse, the 3,000-record dataset carries no coordinates on any row,
so a radius filter cannot run against it however well a client specifies one.

A region therefore resolves to a SET OF STORE LOCATIONS rather than a centre and
a radius. That is also the better model of what someone means: "North Shore" is
the shops on the Shore, not everything within N kilometres of a midpoint, and a
radius drawn around Takapuna reaches across the harbour bridge.
"""

from __future__ import annotations

import pytest

from src.graph.regions import known_regions, locations_for, resolve_region, strip_region
from src.models.scripted import ScriptedModelClient
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import ChatRequest, ChatResponse, ErrorCode, Location


def _turn(message: str, location: Location | None = None) -> ChatResponse:
    request = ChatRequest(
        version="1.0",
        session_id="sess-reg0001",
        turn_id="turn-reg0001",
        message=message,
        location=location,
    )
    return run_turn(request, InMemoryPriceRepository(), ScriptedModelClient())


def _stores(response: ChatResponse) -> set[str]:
    return {e.citation.store_location for e in response.events if e.type == "citation"}


# ------------------------------------------------------------ the contract


def test_coordinates_alone_are_still_valid():
    """Additive: every previously valid request must remain valid."""
    assert Location(lat=-36.8, lon=174.8).region is None


def test_a_region_alone_is_now_valid():
    assert Location(region="North Shore").lat is None


@pytest.mark.parametrize(
    "payload",
    [{}, {"label": "somewhere"}, {"radius_km": 5}],
    ids=["empty", "label-only", "radius-only"],
)
def test_a_location_expressing_no_place_is_refused(payload):
    """
    Accepting it would silently widen the request back to national results,
    which is the direction Req 1.5 forbids. Omitting `location` entirely is how
    a client asks for national.
    """
    with pytest.raises(ValueError, match=r"coordinates .* or a region"):
        Location(**payload)


def test_half_a_coordinate_is_refused():
    """Guessing the other half would put the shopper somewhere they are not."""
    with pytest.raises(ValueError):
        Location(lat=-36.8, region="North Shore", lon=None)


# ------------------------------------------------------------- resolution


def test_a_suburb_is_an_alias_for_its_own_region():
    """ "near Albany" is what the demo scenario actually says."""
    region = resolve_region("cheapest 2L milk near Albany right now")
    assert region is not None
    assert region.name == "north shore"
    assert "Devonport" in region.store_locations


def test_a_region_name_resolves_directly():
    region = resolve_region("cheapest butter in East Auckland")
    assert region is not None
    assert region.name == "east auckland"


def test_a_place_we_do_not_map_resolves_to_nothing():
    assert resolve_region("cheapest butter in Whangarei") is None


def test_matching_respects_word_boundaries():
    """ "Albany" must not be found inside a longer word."""
    assert resolve_region("cheapest Albanyville cheese") is None


def test_an_unmappable_structured_region_returns_none_not_everything():
    """
    The caller must not read this as "no filter". Ignoring it would answer a
    question about Whangarei with Auckland prices and give no sign.
    """
    assert locations_for("Whangarei") is None
    assert locations_for("South Auckland")


# --------------------------------------------------------- item extraction


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("cheapest butter near Albany", "cheapest butter"),
        ("cheapest butter in East Auckland", "cheapest butter"),
        ("dinner for 2 in West Auckland", "dinner for 2"),
        ("price of milk", "price of milk"),
    ],
)
def test_the_region_is_removed_before_the_item_is_extracted(message, expected):
    """
    Latent until regions existed. "cheapest butter near Albany" extracted the
    item as "butter albany", which resolves to nothing, so the turn returned
    `no_data` for a product we stock. Nobody noticed because there was no way to
    ask for a region in the first place.
    """
    assert strip_region(message) == expected


# ---------------------------------------------------------------- the turn


def test_a_region_in_the_message_scopes_the_result():
    assert _stores(_turn("cheapest butter near Albany")) == {"Devonport"}


def test_a_structured_region_scopes_the_result():
    assert _stores(_turn("cheapest butter", Location(region="East Auckland"))) == {
        "Mt Wellington",
        "Sylvia Park",
    }


def test_no_location_still_returns_national_results():
    assert len(_stores(_turn("cheapest butter"))) > 2


def test_coordinates_still_scope_the_result():
    near = Location(lat=-36.9761, lon=174.7767, radius_km=5)
    assert _stores(_turn("cheapest butter", near)) == {"Mangere"}


def test_an_unknown_region_is_refused_and_says_what_we_cover():
    """
    Refused rather than ignored, and actionable rather than merely apologetic —
    the same shape as the dietary refusal.
    """
    response = _turn("cheapest butter", Location(region="Whangarei"))

    errors = [e for e in response.events if e.type == "error"]
    assert [e.code for e in errors] == [ErrorCode.INVALID_REQUEST]
    assert "Whangarei" in errors[0].message
    for name in known_regions():
        assert name in errors[0].message.lower()


def test_a_region_with_no_stores_in_the_catalogue_says_no_data():
    """
    West Auckland is mapped, but the fixture catalogue holds no store there.
    "I have no prices there" is the honest answer, and it is not the same as
    "I do not know where that is".
    """
    response = _turn("cheapest butter in West Auckland")

    assert _stores(response) == set()
    assert any(e.type == "no_data" for e in response.events)
    assert not any(e.type == "error" for e in response.events)


def test_a_region_never_widens_back_to_national():
    """The dangerous direction, and the reason the filter exists at all."""
    scoped = _stores(_turn("cheapest butter near Albany"))
    national = _stores(_turn("cheapest butter"))

    assert scoped < national
