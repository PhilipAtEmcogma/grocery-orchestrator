"""
Prose tests.

The property: the assistant can talk about prices without ever producing one.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.graph.nodes.prose import render, store_name
from src.models.scripted import ScriptedModelClient
from src.prompts.prose import (
    assert_no_literal_money,
    referenced_placeholders,
)
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import ChatRequest, Citation, ClientHints, SourceRef, Store


@pytest.fixture(scope="module")
def repo() -> InMemoryPriceRepository:
    return InMemoryPriceRepository()


def _citation(ref: str, price: str) -> Citation:
    return Citation(
        ref=ref, store=Store.PAKNSAVE, store_location="Sylvia Park",
        product_name=f"Product {ref}", price_nzd=Decimal(price), unit="500g",
        on_special=False, valid_date=date(2026, 7, 31),
        source=SourceRef(table="Products", pk="paknsave#dairy", sk=f"p-{ref}"),
    )


def _req(message: str, **hints) -> ChatRequest:
    return ChatRequest(
        session_id="sess-prose01", turn_id="turn-prose01", message=message,
        hints=ClientHints(**hints) if hints else None,
    )


# ------------------------------------------------------------- money rejection


@pytest.mark.parametrize(
    "text",
    [
        "Pak'nSave is cheapest at $2.97 this week.",
        "It costs 3.49 for the block.",
        "You would save 71 cents.",
        "That is 12 dollars cheaper.",
    ],
)
def test_literal_money_is_rejected(text):
    """The structural guarantee for free text."""
    with pytest.raises(ValueError, match="literal monetary"):
        assert_no_literal_money(text)


@pytest.mark.parametrize(
    "text",
    [
        "The cheapest option is [[c1]] this week.",
        "This plan covers 3 days for 2 people.",
        "A 500g pack goes further across 2 meals.",
        "Reusing 1 pack of mince kept the cost down.",
    ],
)
def test_legitimate_numbers_are_allowed(text):
    """Quantities are not prices. Over-rejecting would gut the prose."""
    assert_no_literal_money(text)


# ------------------------------------------------------------- rendering


def test_placeholders_expand_to_grounded_figures():
    citations = {"c1": _citation("c1", "2.97")}
    out = render("Cheapest is [[c1]] today.", citations, {})
    assert "$2.97" in out
    assert "Pak'nSave Sylvia Park" in out
    assert "[[c1]]" not in out


def test_unknown_placeholder_raises_rather_than_rendering_visibly():
    """A shopper reading '[[c9]]' has been shown a defect."""
    with pytest.raises(KeyError):
        render("Cheapest is [[c9]].", {"c1": _citation("c1", "2.97")}, {})


def test_computed_figures_render():
    out = render("Total [[total]] of [[budget]].", {}, {"total": "$23.16", "budget": "$30"})
    assert out == "Total $23.16 of $30."


def test_store_names_use_retailer_capitalisation():
    """'Paknsave' is wrong in a way a New Zealand reader notices."""
    assert store_name("paknsave") == "Pak'nSave"
    assert store_name("new_world") == "New World"


def test_referenced_placeholders_are_extracted():
    found = referenced_placeholders("[[c1]] beats [[c2]], total [[total]].")
    assert found == {"c1", "c2", "total"}


# ------------------------------------------------------------- end to end


def test_price_check_emits_prose(repo):
    resp = run_turn(_req("what's the cheapest butter near me?"), repo,
                    ScriptedModelClient())
    tokens = [e for e in resp.events if e.type == "token"]
    assert tokens
    assert "$" in "".join(t.text for t in tokens)


def test_meal_plan_emits_prose(repo):
    resp = run_turn(
        _req("feed a flat of 3 for under $30", household_size=3,
             budget_nzd=30, days=3),
        repo, ScriptedModelClient(),
    )
    tokens = [e for e in resp.events if e.type == "token"]
    assert tokens


def test_prose_precedes_the_structured_payload(repo):
    """The sentence introduces the table; it should not follow it."""
    resp = run_turn(_req("cheapest butter"), repo, ScriptedModelClient())
    types = [e.type for e in resp.events]
    assert types.index("token") < types.index("price_comparison")


def test_every_price_in_prose_came_from_a_citation(repo):
    """The grounding guarantee, applied to prose."""
    resp = run_turn(_req("cheapest butter"), repo, ScriptedModelClient())

    prices = {
        f"${e.citation.price_nzd}" for e in resp.events if e.type == "citation"
    }
    prose = "".join(e.text for e in resp.events if e.type == "token")

    import re
    for amount in re.findall(r"\$\d+\.\d{2}", prose):
        assert amount in prices, f"{amount} is not a retrieved price"


# ------------------------------------------------------------- degradation


def test_model_writing_a_literal_price_degrades_to_no_prose(repo):
    """Better a table with no sentence than a sentence with a wrong price."""
    resp = run_turn(_req("cheapest butter"), repo,
                    ScriptedModelClient(prose_writes_money=True))

    assert not [e for e in resp.events if e.type == "token"]
    assert "price_comparison" in [e.type for e in resp.events]


def test_unknown_placeholder_degrades_rather_than_failing_the_turn(repo):
    resp = run_turn(_req("cheapest butter"), repo,
                    ScriptedModelClient(prose_bad_placeholder=True))

    assert not [e for e in resp.events if e.type == "token"]
    assert resp.events[-1].type == "done"


def test_prose_failure_does_not_break_grounding(repo):
    from src.schemas.contract import assert_grounded

    for client in (
        ScriptedModelClient(prose_writes_money=True),
        ScriptedModelClient(prose_bad_placeholder=True),
    ):
        assert_grounded(run_turn(_req("cheapest butter"), repo, client))


def test_prose_is_generated_on_the_cheap_tier(repo):
    """Explanatory text does not need the expensive model."""
    from src.models.base import ModelTier

    model = ScriptedModelClient()
    run_turn(_req("cheapest butter"), repo, model)
    prose_calls = [t for t, s in model.calls if s == "ProseResult"]
    assert prose_calls == [ModelTier.FAST]
