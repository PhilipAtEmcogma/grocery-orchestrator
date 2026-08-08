"""
Multi-item price queries.

"What's cheapest for butter, milk and eggs?" is a normal thing to ask. Until
now it answered about butter and silently dropped the rest — a wrong answer,
not a missing feature.
"""

from __future__ import annotations

import pytest

from src.models.scripted import ScriptedModelClient
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import ChatRequest, assert_grounded


@pytest.fixture(scope="module")
def repo() -> InMemoryPriceRepository:
    return InMemoryPriceRepository()


def _run(repo, message: str):
    return run_turn(
        ChatRequest(session_id="sess-multi01", turn_id="turn-multi01",
                    message=message),
        repo, ScriptedModelClient(),
    )


def _comparisons(resp) -> list[str]:
    return [e.data.query_item for e in resp.events if e.type == "price_comparison"]


def _no_data(resp) -> list[str]:
    return [e.requested_item for e in resp.events if e.type == "no_data"]


# ------------------------------------------------------------- resolution


def test_three_items_produce_three_comparisons(repo):
    resp = _run(repo, "what's cheapest for butter, milk and eggs?")
    assert set(_comparisons(resp)) == {
        "butter-500g", "milk-2l", "eggs-size7-dozen"
    }


def test_two_items_joined_by_and(repo):
    resp = _run(repo, "compare prices for bread and cheese")
    assert set(_comparisons(resp)) == {"bread-white-700g", "cheese-tasty-1kg"}


def test_single_item_still_works(repo):
    resp = _run(repo, "cheapest butter")
    assert _comparisons(resp) == ["butter-500g"]


def test_duplicate_items_are_not_repeated(repo):
    resp = _run(repo, "cheapest butter and butter")
    assert _comparisons(resp) == ["butter-500g"]


# ------------------------------------------------------------- partial results


def test_partial_resolution_answers_what_it_can(repo):
    """Answering two of three without saying so would quietly mislead."""
    resp = _run(repo, "cheapest butter and wagyu ribeye")

    assert "butter-500g" in _comparisons(resp)
    assert _no_data(resp)


def test_unresolved_item_is_named_in_the_gap(repo):
    resp = _run(repo, "cheapest butter and wagyu ribeye")
    assert any("wagyu" in item for item in _no_data(resp))


def test_all_items_unresolved_terminates_as_no_data(repo):
    resp = _run(repo, "cheapest wagyu ribeye and truffle oil")
    assert not _comparisons(resp)
    assert _no_data(resp)
    assert resp.events[-1].type == "done"


# ------------------------------------------------------------- invariants


def test_citation_refs_are_unique_across_items(repo):
    """A ref must identify exactly one price everywhere it appears."""
    resp = _run(repo, "what's cheapest for butter, milk and eggs?")
    refs = [e.citation.ref for e in resp.events if e.type == "citation"]
    assert len(refs) == len(set(refs))


def test_each_comparison_cites_only_its_own_item(repo):
    """Cross-contamination would show milk prices under butter."""
    resp = _run(repo, "what's cheapest for butter, milk and eggs?")

    citations = {e.citation.ref: e.citation for e in resp.events if e.type == "citation"}
    for event in (e for e in resp.events if e.type == "price_comparison"):
        products = {
            citations[o.citation_ref].source.sk for o in event.data.options
        }
        assert len(products) == 1, f"{event.data.query_item} mixes {products}"


def test_multi_item_response_is_grounded(repo):
    resp = _run(repo, "what's cheapest for butter, milk and eggs?")
    assert_grounded(resp)


def test_each_comparison_flags_exactly_one_cheapest(repo):
    resp = _run(repo, "compare prices for bread and cheese")
    for event in (e for e in resp.events if e.type == "price_comparison"):
        flagged = [o for o in event.data.options if o.is_cheapest]
        assert len(flagged) == 1


def test_seq_stays_contiguous_with_multiple_comparisons(repo):
    resp = _run(repo, "what's cheapest for butter, milk and eggs?")
    assert [e.seq for e in resp.events] == list(range(len(resp.events)))


# ------------------------------------------------------------- bounds


def test_item_count_is_capped(repo):
    """A pathological request must not blow the latency budget."""
    from src.graph.nodes import MAX_ITEMS_PER_TURN

    resp = _run(
        repo,
        "cheapest butter, milk, eggs, bread, cheese, rice, pasta, carrots",
    )
    assert len(_comparisons(resp)) <= MAX_ITEMS_PER_TURN
