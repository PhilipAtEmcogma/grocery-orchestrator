"""
Prose tests.

The property: the assistant can talk about prices without ever producing one.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.graph.nodes.prose import render, store_name
from src.models.base import ModelTier, T
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
        source=SourceRef(table="grocery-products-dev", pk="paknsave#sylvia-park", sk=f"p-{ref}"),
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
    # Prose is now money-free: placeholders expand to product/store labels
    assert "Product c1" in out
    assert "Pak'nSave Sylvia Park" in out
    assert "[[c1]]" not in out
    assert "$" not in out


def test_unknown_placeholder_raises_rather_than_rendering_visibly():
    """A shopper reading '[[c9]]' has been shown a defect."""
    with pytest.raises(KeyError):
        render("Cheapest is [[c9]].", {"c1": _citation("c1", "2.97")}, {})


def test_computed_figures_render():
    figures = {"total": "the plan total", "budget": "your budget"}
    out = render("Total [[total]] of [[budget]].", {}, figures)
    assert out == "Total the plan total of your budget."


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
    # Prose is now money-free — verify it contains store/product info instead
    text = "".join(t.text for t in tokens)
    assert "$" not in text
    assert "Pak'nSave" in text or "Woolworths" in text or "New World" in text


def test_meal_plan_emits_prose(repo):
    resp = run_turn(
        _req("feed a flat of 3 for under $80", household_size=3,
             budget_nzd=80, days=3),  # feasible under whole-pack pricing
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


# ------------------------------------------- misattribution guard (Req 4)


class _CitesRefModel(ScriptedModelClient):
    """
    A model that always cites one chosen placeholder for the prose call.

    Everything else defers to the scripted client, so intent classification and
    planning behave normally and only the sentence under test is controlled.
    """

    def __init__(self, ref: str) -> None:
        super().__init__()
        self._ref = ref

    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        tier: ModelTier,
        max_tokens: int = 1024,
        task: str = "classify_intent",
    ) -> T:
        if task == "generate_prose":
            self._usage = {"model_ids": ["stub"], "latency_ms": 1}
            return schema(text=f"The cheapest option is [[{self._ref}]] this week.")
        return super().structured(
            system=system, user=user, schema=schema, tier=tier,
            max_tokens=max_tokens, task=task,
        )


def _cheapest_ref(resp) -> str:
    comparison = next(e.data for e in resp.events if e.type == "price_comparison")
    return next(o.citation_ref for o in comparison.options if o.is_cheapest)


def test_prose_survives_when_it_cites_the_computed_cheapest(repo):
    """Control: the guard must not reject the sentence it is meant to allow."""
    probe = run_turn(_req("cheapest butter"), repo, ScriptedModelClient())
    winner = _cheapest_ref(probe)

    resp = run_turn(_req("cheapest butter"), repo, _CitesRefModel(winner))

    assert [e for e in resp.events if e.type == "token"], (
        "prose citing the computed cheapest ref was rejected"
    )


def test_prose_is_dropped_when_it_cites_a_dearer_option(repo):
    """
    The regression this guard exists for.

    The placeholder list carries no prices, so before the winner was named in
    the prompt the model chose a citation unaided -- live Nova named Pak'nSave
    Sylvia Park while the comparison flagged Mangere. Both were $2.97, so a tie
    hid it; on a non-tie the sentence would have named a DEARER store as
    cheapest, which Req 4 forbids. Degrading to the structured payload is the
    honest failure.
    """
    probe = run_turn(_req("cheapest butter"), repo, ScriptedModelClient())
    winner = _cheapest_ref(probe)
    citations = [e.citation.ref for e in probe.events if e.type == "citation"]
    dearer = next(r for r in citations if r != winner)

    resp = run_turn(_req("cheapest butter"), repo, _CitesRefModel(dearer))

    assert not [e for e in resp.events if e.type == "token"], (
        f"prose citing {dearer} was published while the comparison flags "
        f"{winner} as cheapest"
    )
    # The turn still succeeds -- the comparison is the substance.
    assert [e for e in resp.events if e.type == "price_comparison"]
    assert [e.type for e in resp.events][-1] == "done"


# --------------------------------------------- money on the far side of render
#
# The pre-render check sees `[[total]]`, not a number: placeholders are
# expanded afterwards, so the string the user reads is not the string that was
# validated. Nothing can inject money there today — `figures` maps to fixed
# words and `_describe` emits a product and store label — which makes the
# guarantee a property of the current code rather than a rule about it. These
# pin the rule, so that "show the price in the sentence" fails here instead of
# shipping.


def test_money_introduced_by_rendering_is_caught(repo, monkeypatch):
    """A figure placeholder that expands to a price must not reach the user."""
    import src.graph.nodes.prose as prose_mod

    original = prose_mod.render

    def leaky(text, citations, figures):
        return original(text, citations, {**figures, "total": "$41.20"})

    monkeypatch.setattr(prose_mod, "render", leaky)
    resp = run_turn(
        _req("feed a flat of 3 for under $80", household_size=3,
             budget_nzd=80, days=3),
        repo, ScriptedModelClient(),
    )
    assert not [e for e in resp.events if e.type == "token"]


def test_the_turn_survives_money_introduced_by_rendering(repo, monkeypatch):
    """
    Degrades, does not die. This is why the check lives here and not in
    `run_turn`, which raises: the user keeps the cited answer and loses only
    the sentence.
    """
    import src.graph.nodes.prose as prose_mod

    original = prose_mod.render

    def leaky(text, citations, figures):
        return original(text, citations, {**figures, "total": "$41.20"})

    monkeypatch.setattr(prose_mod, "render", leaky)
    resp = run_turn(
        _req("feed a flat of 3 for under $80", household_size=3,
             budget_nzd=80, days=3),
        repo, ScriptedModelClient(),
    )
    types = [e.type for e in resp.events]
    assert "meal_plan" in types
    assert types[-1] == "done"


def test_clean_rendering_still_produces_prose(repo):
    """The new check must not reject the labels rendering actually emits."""
    resp = run_turn(
        _req("feed a flat of 3 for under $80", household_size=3,
             budget_nzd=80, days=3),
        repo, ScriptedModelClient(),
    )
    assert [e for e in resp.events if e.type == "token"]
