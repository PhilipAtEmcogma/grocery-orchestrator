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
        ChatRequest(session_id="sess-multi01", turn_id="turn-multi01", message=message),
        repo,
        ScriptedModelClient(),
    )


def _comparisons(resp) -> list[str]:
    return [e.data.query_item for e in resp.events if e.type == "price_comparison"]


def _no_data(resp) -> list[str]:
    return [e.requested_item for e in resp.events if e.type == "no_data"]


def _notices(resp) -> list[str]:
    return [e.message for e in resp.events if e.type == "notice"]


# ------------------------------------------------------------- resolution


def test_three_items_produce_three_comparisons(repo):
    resp = _run(repo, "what's cheapest for butter, milk and eggs?")
    assert set(_comparisons(resp)) == {"butter-500g", "milk-2l", "eggs-size7-dozen"}


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
        products = {citations[o.citation_ref].source.sk for o in event.data.options}
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


EIGHT_ITEMS = "cheapest butter, milk, eggs, bread, cheese, rice, pasta, carrots"


def test_item_count_is_capped(repo):
    """A pathological request must not blow the latency budget."""
    from src.graph.nodes import MAX_ITEMS_PER_TURN

    resp = _run(repo, EIGHT_ITEMS)
    assert len(_comparisons(resp)) <= MAX_ITEMS_PER_TURN


def test_items_past_the_cap_are_named_not_dropped(repo):
    """
    Req 1.7: unanswered items must be named.

    Eight items asked, five compared. The user is entitled to know which three
    were not checked — silently answering a subset is the failure mode the
    partial-resolution path already exists to prevent.
    """
    resp = _run(repo, EIGHT_ITEMS)

    notice = " ".join(_notices(resp))
    assert "rice" in notice
    assert "pasta" in notice
    assert "carrots" in notice


def test_skipped_items_are_a_notice_not_a_no_data_claim(repo):
    """
    We have prices for these; we just did not look. Claiming 'no data' would
    be a different falsehood from saying nothing at all.
    """
    resp = _run(repo, EIGHT_ITEMS)

    assert _notices(resp)
    assert "rice" not in " ".join(str(i) for i in _no_data(resp))


def test_no_notice_when_every_item_fits(repo):
    """The notice must not fire on ordinary requests."""
    resp = _run(repo, "what's cheapest for butter, milk and eggs?")
    assert not [n for n in _notices(resp) if "at a time" in n]


def test_skipped_items_recorded_in_state(repo):
    """The names must survive retrieval, not just be counted."""
    from src.graph.build import build_graph
    from src.models.scripted import ScriptedModelClient

    graph = build_graph(repo, ScriptedModelClient())
    state = graph.invoke({"session_id": "s", "turn_id": "t", "message": EIGHT_ITEMS})
    assert state["skipped_items"] == ["rice", "pasta", "carrots"]


def test_overflow_is_reported_even_when_nothing_resolved(repo):
    """
    'I found nothing' and 'I checked five of your eight and found nothing'
    are different statements, and only the second one is true.
    """
    resp = _run(
        repo,
        "cheapest wagyu ribeye, truffle oil, saffron, caviar, foie gras, butter, milk, eggs",
    )
    assert not _comparisons(resp)
    assert any("didn't check" in n for n in _notices(resp))
    assert resp.events[-1].type == "done"


def test_extraction_bound_exceeds_the_comparison_cap(repo):
    """
    Collapsing the two caps into one would make the overflow unknowable —
    the orchestrator would never see the items it needs to name.
    """
    from src.graph.nodes import MAX_ITEMS_PER_TURN
    from src.prompts.intent import MAX_EXTRACTED_ITEMS

    assert MAX_EXTRACTED_ITEMS > MAX_ITEMS_PER_TURN
