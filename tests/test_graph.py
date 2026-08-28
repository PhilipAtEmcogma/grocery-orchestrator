"""
Walking-skeleton tests. No AWS, no network, deterministic, milliseconds.

These same tests must pass unchanged when the stubs are replaced by Bedrock
calls and the fixture repo by DynamoDB. That is the point of the protocol
boundaries.
"""

from __future__ import annotations

import pytest

from src.models.scripted import ScriptedModelClient
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import (
    ChatRequest,
    ClientHints,
    assert_grounded,
)


@pytest.fixture(scope="module")
def repo() -> InMemoryPriceRepository:
    return InMemoryPriceRepository()


@pytest.fixture
def model() -> ScriptedModelClient:
    return ScriptedModelClient()


def _types(resp) -> list[str]:
    """Shorthand: the ordered list of event type strings in a response."""
    return [e.type for e in resp.events]


def _req(message: str, hints: dict | None = None) -> ChatRequest:
    """Build a minimal valid ChatRequest for a given message and optional hints."""
    return ChatRequest(
        session_id="sess-testing1",
        turn_id="turn-testing1",
        message=message,
        hints=ClientHints(**hints) if hints else None,
    )


# ------------------------------------------------------------- happy paths


def test_price_check_produces_grounded_comparison(repo, model):
    """A basic price question should yield a comparison bookended by session/done events."""
    resp = run_turn(_req("what's the cheapest butter near me?"), repo, model)

    assert "price_comparison" in _types(resp)
    assert _types(resp)[0] == "session"
    assert _types(resp)[-1] == "done"
    assert_grounded(resp)


def test_price_check_cheapest_is_actually_cheapest(repo, model):
    """The option flagged is_cheapest must genuinely have the lowest price."""
    resp = run_turn(_req("cheapest butter"), repo, model)

    citations = {e.citation.ref: e.citation for e in resp.events if e.type == "citation"}
    comparison = next(e.data for e in resp.events if e.type == "price_comparison")

    flagged = [o for o in comparison.options if o.is_cheapest]
    assert len(flagged) == 1

    cheapest_price = citations[flagged[0].citation_ref].price_nzd
    assert cheapest_price == min(c.price_nzd for c in citations.values())


def test_intent_event_precedes_content(repo, model):
    """The frontend needs the intent event before the payload it explains."""
    resp = run_turn(_req("how much is milk"), repo, model)

    seqs = {e.type: e.seq for e in resp.events}
    assert seqs["intent"] < seqs["price_comparison"]


def test_seq_is_contiguous_from_zero(repo, model):
    """seq must be a gapless 0..n-1 sequence so ordering is unambiguous."""
    resp = run_turn(_req("cheapest cheese"), repo, model)
    assert [e.seq for e in resp.events] == list(range(len(resp.events)))


# ------------------------------------------------------------- honest failure


def test_unknown_product_returns_no_data_not_a_guess(repo, model):
    """A product absent from the fixtures must produce no_data, never an invented price."""
    resp = run_turn(_req("what's the cheapest wagyu ribeye"), repo, model)

    assert "no_data" in _types(resp)
    assert "price_comparison" not in _types(resp)
    assert _types(resp)[-1] == "done"


def test_meal_plan_produces_a_costed_plan(repo, model):
    """The plan node is real now, so a feasible budget must yield a plan."""
    # $80, not $30. The scripted planner buys ~16 whole packs regardless of
    # budget, so its PAYABLE total is about $65 -- it only ever fitted $30
    # while within_budget was computed from fractional consumption. The
    # scenario under test is "a feasible budget yields a plan"; the number
    # just has to actually be one.
    resp = run_turn(
        _req("feed a flat of 3 for under $80 this week, no seafood",
             hints={"household_size": 3, "budget_nzd": 80, "days": 3,
                    "dietary_exclusions": ["seafood"]}),
        repo,
        model,
    )

    plans = [e for e in resp.events if e.type == "meal_plan"]
    assert len(plans) == 1
    assert plans[0].data.within_budget is True
    assert _types(resp)[-1] == "done"


def test_repair_loop_is_bounded(repo, model):
    """A runaway repair loop is a cost and latency risk, not just correctness."""
    resp = run_turn(
        _req("meal plan for the week", hints={"budget_nzd": 5}),
        repo,
        model,
    )
    assert _types(resp)[-1] == "done"


# ------------------------------------------------------------- grounding


def test_every_response_is_grounded(repo, model):
    """Sweep several message types and check the grounding invariant holds for all of them."""
    messages = [
        "cheapest butter",
        "how much is a dozen eggs",
        "price of frozen peas",
        "cheapest wagyu ribeye",
        "hello there",
    ]
    for msg in messages:
        resp = run_turn(_req(msg), repo, model)
        assert_grounded(resp)


def test_dietary_exclusion_removes_seafood(repo, model):
    """A stated seafood exclusion must actually filter tuna/salmon out of retrieval."""
    resp = run_turn(
        _req("meal plan", hints={"budget_nzd": 30, "dietary_exclusions": ["seafood"]}),
        repo,
        model,
    )
    products = [
        e.citation.source.sk for e in resp.events if e.type == "citation"
    ]
    assert not any("tuna" in p or "salmon" in p for p in products)


# ------------------------------------------------------------- retrieval unit


def test_resolve_product_key_prefers_specific_match(repo):
    """Both the specific and generic synonym should resolve to the same product."""
    assert repo.resolve_product_key("frozen peas") == "frozen-peas-1kg"
    assert repo.resolve_product_key("peas") == "frozen-peas-1kg"


def test_resolve_returns_none_rather_than_guessing(repo):
    """Unknown/unstocked products must resolve to None, never a nearest-match guess."""
    assert repo.resolve_product_key("wagyu ribeye") is None
    assert repo.resolve_product_key("truffle oil") is None


def test_cheapest_for_product_is_sorted(repo):
    """cheapest_for_product must return results in ascending price order."""
    recs = repo.cheapest_for_product("butter-500g")
    prices = [r.price_nzd for r in recs]
    assert prices == sorted(prices)


def test_messy_naming_still_resolves_across_stores(repo):
    """Each chain writes the name differently; all must map to one key."""
    recs = repo.cheapest_for_product("butter-500g")
    names = {r.display_name for r in recs}
    assert len(names) > 1, "fixtures should have inconsistent naming"
    assert len({r.store for r in recs}) == 3, "all three chains present"
