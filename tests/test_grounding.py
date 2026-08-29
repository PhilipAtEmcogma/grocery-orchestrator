"""
Req 3.5-3.6: a citation must BE a record that was actually retrieved.

`assert_grounded` sees only the response, so it can check that a ref was
declared before use and that source keys are SHAPED like keys — `table`
non-empty, a `#` in the pk, a non-empty sk. Shape is not identity. A citation
naming the right table, with a plausible partition key and a price nobody ever
retrieved, passed it cleanly. The system's central claim — that a price the
user sees came from the price store — therefore rested on no code path
currently fabricating one, rather than on a check that would notice if one did.

`assert_citations_match_retrieval` compares each Citation against the frozen
`PriceRecord` the retrieval node kept for it. These tests are the negative
controls Req 3.6 names: unknown references, incorrect source keys, altered
values, and content emitted before its citation (that last one lives with
`assert_grounded`, in validate.py).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

import src.graph.nodes as nodes
from src.models.scripted import ScriptedModelClient
from src.retrieval.base import PriceRecord
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import (
    ChatRequest,
    ChatResponse,
    Citation,
    CitationEvent,
    DoneEvent,
    Event,
    SourceRef,
    Store,
    assert_citations_match_retrieval,
)

TABLE = "grocery-products-dev"
# Any fixed instant; these tests assert on provenance, never on the clock.
FIXED_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _record(**overrides) -> PriceRecord:
    base = {
        "product_key": "butter-500g",
        "store": Store.PAKNSAVE,
        "store_location": "Mangere",
        "display_name": "Pams Butter 500g",
        "canonical_name": "butter",
        "category": "dairy",
        "price_nzd": Decimal("2.97"),
        "unit": "500g",
        "unit_price_nzd": Decimal("5.94"),
        "pack_grams": 500,
        "on_special": True,
        "valid_date": "2026-07-31",
        "lat": -36.98,
        "lon": 174.80,
        "store_key": "paknsave#mangere",
    }
    return PriceRecord(**{**base, **overrides})


def _citation(**overrides) -> Citation:
    """A citation that matches `_record()` exactly, unless told otherwise."""
    source = SourceRef(
        table=overrides.pop("table", TABLE),
        pk=overrides.pop("pk", "paknsave#mangere"),
        sk=overrides.pop("sk", "butter-500g"),
    )
    base = {
        "ref": "c1",
        "store": Store.PAKNSAVE,
        "store_location": "Mangere",
        "product_name": "Pams Butter 500g",
        "price_nzd": Decimal("2.97"),
        "unit": "500g",
        "unit_price_nzd": Decimal("5.94"),
        "on_special": True,
        "valid_date": date(2026, 7, 31),
    }
    return Citation(**{**base, **overrides}, source=source)


def _response(citation: Citation) -> ChatResponse:
    # Appended rather than built as a literal: `list` is invariant, so a
    # literal infers list[CitationEvent | DoneEvent] and will not assign to the
    # declared list[Event].
    events: list[Event] = []
    events.append(CitationEvent(seq=0, citation=citation))
    events.append(DoneEvent(seq=1, server_time=FIXED_TIME))
    return ChatResponse(session_id="sess-ground01", turn_id="turn-ground01", events=events)


def _check(citation: Citation, records=None) -> None:
    assert_citations_match_retrieval(
        _response(citation),
        table=TABLE,
        records={"c1": _record()} if records is None else records,
    )


# ------------------------------------------------------------ positive control


def test_a_citation_built_from_its_record_passes():
    """
    The half that stops this being a check which rejects everything. A
    conformance suite that cannot pass certifies as little as one that cannot
    fail, and every field below is compared, so a spurious mismatch here would
    make the whole rule unusable.
    """
    _check(_citation())


def test_a_turn_with_no_citations_has_nothing_to_prove():
    events: list[Event] = [DoneEvent(seq=0, server_time=FIXED_TIME)]
    response = ChatResponse(session_id="sess-ground01", turn_id="turn-ground01", events=events)
    assert_citations_match_retrieval(response, table=TABLE, records={})


# ------------------------------------------------------- unknown reference


def test_a_citation_that_was_never_retrieved_is_rejected():
    """
    The dangerous case, and the one `assert_grounded` cannot see.

    Not "a payload referenced an undeclared ref" — that check already existed —
    but "a correctly shaped citation exists that retrieval never produced".
    That is a fabricated price wearing the right clothes.
    """
    with pytest.raises(AssertionError, match="was not retrieved"):
        _check(_citation(), records={})


def test_an_empty_index_fails_every_citation_rather_than_passing():
    """
    Fail closed. `run_turn` passes `record_index or {}`, and the tempting
    reading of an empty index is "nothing to compare, carry on" — which would
    disable the rule entirely on any turn where the index went missing.
    """
    with pytest.raises(AssertionError):
        _check(_citation(), records={})


# ---------------------------------------------------------- incorrect keys


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("table", "some-other-table", "source.table"),
        ("pk", "paknsave#sylvia-park", "source.pk"),
        ("sk", "milk-2l", "source.sk"),
    ],
    ids=["wrong-table", "wrong-partition-key", "wrong-sort-key"],
)
def test_source_keys_must_identify_that_exact_record(field, value, expected):
    """
    Req 3.5's "identifies that exact stored record".

    `assert_grounded` accepts every one of these: the table is non-empty, the
    pk still contains a '#', the sk is still non-empty. All three point at a
    real-looking record that is not the one the price came from.
    """
    with pytest.raises(AssertionError, match=expected):
        _check(_citation(**{field: value}))


# --------------------------------------------------------- altered values


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("price_nzd", Decimal("0.99")),
        ("unit_price_nzd", Decimal("1.98")),
        ("product_name", "Caviar 1kg"),
        ("store", Store.WOOLWORTHS),
        ("store_location", "Ponsonby"),
        ("unit", "1kg"),
        ("on_special", False),
        ("valid_date", date(2020, 1, 1)),
    ],
)
def test_every_published_value_must_equal_the_retrieved_value(field, value):
    """
    Req 3.5's "every cited value equals the retrieved value".

    Parametrised over every field rather than spot-checking the price, because
    a stale `valid_date` or a wrong `store_location` sends a shopper to the
    wrong shelf just as surely as a wrong number does — and the Fair Trading
    Act attaches to the comparison published, not to the figure alone.
    """
    with pytest.raises(AssertionError, match=field.replace("_nzd", "")):
        _check(_citation(**{field: value}))


def test_the_report_names_both_sides_of_the_mismatch():
    """A diagnostic that says only "mismatch" costs an hour to act on."""
    with pytest.raises(AssertionError) as exc:
        _check(_citation(price_nzd=Decimal("0.99")))
    message = str(exc.value)
    assert "0.99" in message
    assert "2.97" in message


# ------------------------------------------------------ end to end, via run_turn


def _tamper_retrieval(monkeypatch, field: str, value) -> None:
    """
    Patch the retrieval node to publish a citation that disagrees with the
    record it kept — the shape a real construction bug would take.

    `build_graph` resolves `nodes.retrieve_prices` when the graph is built, and
    `run_turn` builds it per call, so patching the module attribute is enough.
    """
    original = nodes.retrieve_prices

    def patched(state, repo):
        out = original(state, repo=repo)
        for ev in out["events"]:
            if isinstance(ev, CitationEvent):
                setattr(ev.citation, field, value)
                break
        return out

    monkeypatch.setattr(nodes, "retrieve_prices", patched)


@pytest.mark.parametrize(
    ("field", "value"),
    [("price_nzd", Decimal("0.01")), ("product_name", "Caviar 1kg")],
    ids=["altered-price", "altered-product-name"],
)
def test_run_turn_refuses_a_tampered_citation(monkeypatch, field, value):
    """
    Proves `run_turn` actually calls the check.

    The unit tests above prove the rule works; this proves it is wired in. A
    correct assertion nobody calls is the failure mode this repository has
    already paid for twice — the secret scan that could not run, and the repair
    branch that no test reached.
    """
    _tamper_retrieval(monkeypatch, field, value)
    request = ChatRequest(
        version="1.0",
        session_id="sess-ground01",
        turn_id="turn-ground01",
        message="cheapest butter",
    )
    with pytest.raises(AssertionError, match="do not match retrieval"):
        run_turn(request, InMemoryPriceRepository(), ScriptedModelClient())


def test_an_untampered_turn_still_completes():
    """The positive control for the wiring: real turns must not trip it."""
    request = ChatRequest(
        version="1.0",
        session_id="sess-ground01",
        turn_id="turn-ground02",
        message="cheapest butter",
    )
    response = run_turn(request, InMemoryPriceRepository(), ScriptedModelClient())
    assert any(isinstance(e, CitationEvent) for e in response.events)
