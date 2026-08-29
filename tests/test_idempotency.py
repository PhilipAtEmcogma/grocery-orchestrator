"""
Idempotency tests.

The property: resending a turn returns the same answer without redoing the
work — and the cases where that would be WRONG are handled instead.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal

import pytest

import src.store.idempotency as idempotency_module
from src.handler import lambda_handler
from src.schemas.contract import ChatRequest, ChatResponse, ClientHints
from src.store.idempotency import (
    IN_PROGRESS_TIMEOUT_SECONDS,
    AcquireStatus,
    InMemoryIdempotencyStore,
    canonical_payload,
    fingerprint,
    fingerprint_request,
    make_key,
)


@pytest.fixture
def store() -> InMemoryIdempotencyStore:
    return InMemoryIdempotencyStore()


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch):
    import src.handler as handler_mod

    monkeypatch.setattr(handler_mod, "_idempotency", None)


def _body(
    session: str = "sess-idem01", turn: str = "turn-idem01", message: str = "cheapest butter"
) -> dict:
    return {
        "version": "1.0",
        "session_id": session,
        "turn_id": turn,
        "message": message,
    }


def _event(body: dict) -> dict:
    return {"httpMethod": "POST", "body": json.dumps(body)}


# ------------------------------------------------------------- store


def test_first_acquire_wins(store):
    result = store.acquire(make_key("s", "t"), fingerprint("{}"))
    assert result.status is AcquireStatus.ACQUIRED


def test_concurrent_acquire_is_reported_in_progress(store):
    """A retry usually arrives while the first attempt is still running."""
    key, h = make_key("s", "t"), fingerprint("{}")
    store.acquire(key, h)
    assert store.acquire(key, h).status is AcquireStatus.IN_PROGRESS


def test_completed_turn_returns_the_stored_response(store):
    key, h = make_key("s", "t"), fingerprint("{}")
    claim = store.acquire(key, h)
    store.complete(key, claim.claim_token or "", '{"events":[]}')

    result = store.acquire(key, h)
    assert result.status is AcquireStatus.COMPLETED
    assert result.cached_response == '{"events":[]}'


def test_same_turn_id_different_payload_is_rejected(store):
    """Returning the cached response would answer a different question."""
    key = make_key("s", "t")
    store.acquire(key, fingerprint('{"message":"butter"}'))
    result = store.acquire(key, fingerprint('{"message":"milk"}'))
    assert result.status is AcquireStatus.PAYLOAD_MISMATCH


def test_keys_are_scoped_by_session(store):
    """Clients generate turn_ids; two sessions can collide."""
    h = fingerprint("{}")
    store.acquire(make_key("session-a", "turn-1"), h)
    other = store.acquire(make_key("session-b", "turn-1"), h)
    assert other.status is AcquireStatus.ACQUIRED


def test_stale_in_progress_marker_is_taken_over(store, monkeypatch):
    """A crashed invocation must not block retries until the TTL expires."""
    key, h = make_key("s", "t"), fingerprint("{}")
    store.acquire(key, h)

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + IN_PROGRESS_TIMEOUT_SECONDS + 1)
    assert store.acquire(key, h).status is AcquireStatus.ACQUIRED


def test_release_allows_a_fresh_attempt(store):
    key, h = make_key("s", "t"), fingerprint("{}")
    claim = store.acquire(key, h)
    store.release(key, claim.claim_token or "")
    assert store.acquire(key, h).status is AcquireStatus.ACQUIRED


# ------------------------------------------------------------- handler


def test_replay_returns_the_same_response_without_rerunning():
    body = _body()
    first = lambda_handler(_event(body))
    second = lambda_handler(_event(body))

    assert second["headers"].get("X-Idempotent-Replay") == "true"
    assert first["body"] == second["body"]


def test_replay_does_not_invoke_the_model_again(monkeypatch):
    """The cost saving is the point, not just response consistency."""
    import src.handler as handler_mod
    from src.models.scripted import ScriptedModelClient

    model = ScriptedModelClient()
    monkeypatch.setattr(handler_mod, "_model", model)

    body = _body()
    lambda_handler(_event(body))
    calls_after_first = len(model.calls)

    lambda_handler(_event(body))
    assert len(model.calls) == calls_after_first


def test_different_turns_are_not_deduplicated():
    first = lambda_handler(_event(_body(turn="turn-a", message="cheapest butter")))
    second = lambda_handler(_event(_body(turn="turn-b", message="cheapest milk")))

    assert second["headers"].get("X-Idempotent-Replay") != "true"
    assert first["body"] != second["body"]


def test_reused_turn_id_with_different_content_is_rejected():
    lambda_handler(_event(_body(message="cheapest butter")))
    result = lambda_handler(_event(_body(message="cheapest milk")))

    assert result["statusCode"] == 400
    response = ChatResponse.model_validate_json(result["body"])
    errors = [e for e in response.events if e.type == "error"]
    assert errors[0].retryable is False


def test_in_flight_retry_gets_a_retryable_response(monkeypatch):
    """The first attempt may still succeed; the client should wait, not fail."""
    import src.handler as handler_mod

    store = InMemoryIdempotencyStore()
    monkeypatch.setattr(handler_mod, "_idempotency", store)

    body = _body()
    store.acquire(
        make_key(body["session_id"], body["turn_id"]),
        # The canonical fingerprint of the VALIDATED request, which is what the
        # handler computes. Seeding with fingerprint(json.dumps(body)) used to
        # work and now produces a payload mismatch, which is the change working.
        fingerprint_request(ChatRequest.model_validate(body)),
    )

    result = lambda_handler(_event(body))
    assert result["statusCode"] == 409
    response = ChatResponse.model_validate_json(result["body"])
    errors = [e for e in response.events if e.type == "error"]
    assert errors[0].retryable is True


def test_retryable_failure_is_not_cached(monkeypatch):
    """
    Caching a transient failure would make the client's retry permanently
    useless — it would receive the same failure forever.
    """
    import src.handler as handler_mod

    store = InMemoryIdempotencyStore()
    monkeypatch.setattr(handler_mod, "_idempotency", store)

    def boom(*_a, **_k):
        raise RuntimeError("transient")

    monkeypatch.setattr(handler_mod, "_dependencies", boom)

    body = _body()
    lambda_handler(_event(body))

    result = store.acquire(
        make_key(body["session_id"], body["turn_id"]),
        # The canonical fingerprint of the VALIDATED request, which is what the
        # handler computes. Seeding with fingerprint(json.dumps(body)) used to
        # work and now produces a payload mismatch, which is the change working.
        fingerprint_request(ChatRequest.model_validate(body)),
    )
    assert result.status is AcquireStatus.ACQUIRED


def test_store_failure_degrades_rather_than_failing_the_turn(monkeypatch):
    """A broken idempotency store must not take the product down."""
    import src.handler as handler_mod

    class Broken:
        def acquire(self, *_a, **_k):
            raise RuntimeError("store unavailable")

        def complete(self, *_a, **_k):
            raise RuntimeError("store unavailable")

        def release(self, *_a, **_k):
            raise RuntimeError("store unavailable")

    monkeypatch.setattr(handler_mod, "_idempotency", Broken())

    result = lambda_handler(_event(_body()))
    assert result["statusCode"] == 200
    ChatResponse.model_validate_json(result["body"])


# ============================================ Pilot Task 6: canonical fingerprint
#
# The handler used to hash the RAW REQUEST BODY. That made the fingerprint
# sensitive to things carrying no meaning — whitespace, JSON object-key order,
# whether an optional field was omitted or sent explicitly as null — so a client
# whose retry serialised its dict differently was told PAYLOAD_MISMATCH: a 400
# it is not allowed to retry, issued by the mechanism that exists to help it
# recover from a timeout.
#
# DYNAMODB-SCHEMA.md calls the algorithm part of the idempotency contract and
# requires its vectors to be shared by both store implementations. These are
# those vectors.


def _req(**overrides) -> ChatRequest:
    base = {
        "version": "1.0",
        "session_id": "sess-abc12345",
        "turn_id": "turn-0001abc",
        "message": "cheapest butter",
    }
    return ChatRequest(**{**base, **overrides})


EQUIVALENT_PAIRS = [
    ("omitted vs explicit null hints", _req(), _req(hints=None)),
    (
        "trailing zeros on money",
        _req(hints=ClientHints(budget_nzd=Decimal("30"))),
        _req(hints=ClientHints(budget_nzd=Decimal("30.00"))),
    ),
    (
        "same content, built separately",
        _req(hints=ClientHints(household_size=3, budget_nzd=Decimal("45.50"))),
        _req(hints=ClientHints(budget_nzd=Decimal("45.5"), household_size=3)),
    ),
]

DISTINCT_PAIRS = [
    ("different message", _req(), _req(message="cheapest milk")),
    (
        "different budget",
        _req(hints=ClientHints(budget_nzd=Decimal("30"))),
        _req(hints=ClientHints(budget_nzd=Decimal("50"))),
    ),
    (
        "different household size",
        _req(hints=ClientHints(household_size=2)),
        _req(hints=ClientHints(household_size=4)),
    ),
    (
        "exclusion order — treated as distinct on purpose",
        _req(hints=ClientHints(dietary_exclusions=["dairy", "seafood"])),
        _req(hints=ClientHints(dietary_exclusions=["seafood", "dairy"])),
    ),
]


@pytest.mark.parametrize(("label", "left", "right"), EQUIVALENT_PAIRS, ids=lambda v: v)
def test_equivalent_requests_share_a_fingerprint(label, left, right):
    assert fingerprint_request(left) == fingerprint_request(right), label


@pytest.mark.parametrize(("label", "left", "right"), DISTINCT_PAIRS, ids=lambda v: v)
def test_different_requests_do_not_share_a_fingerprint(label, left, right):
    """
    The dangerous direction. A fingerprint that collides across genuinely
    different content would serve a cached answer to a question nobody asked,
    invisibly.

    List order is deliberately in this list rather than the equivalent one:
    ["dairy","seafood"] and ["seafood","dairy"] ARE the same request, but
    proving that in general needs a per-field rule, and erring toward rejecting
    a retry beats serving the wrong plan to someone with an allergy.
    """
    assert fingerprint_request(left) != fingerprint_request(right), label


def test_the_canonical_form_is_key_sorted_and_compact():
    """
    Pinned because the fingerprint is stored: changing the serialisation
    silently invalidates every in-flight claim in the table.
    """
    payload = canonical_payload(_req(hints=ClientHints(budget_nzd=Decimal("30"))))
    assert payload.startswith('{"hints":')
    assert '"session_id":"sess-abc12345"' in payload
    assert '"budget_nzd":"30"' in payload
    assert ", " not in payload, "separators must be compact"


def test_raw_body_formatting_cannot_cause_a_mismatch():
    """
    The regression, stated as the client sees it: the same request serialised
    two reasonable ways must not be a payload mismatch.
    """
    body = {
        "version": "1.0",
        "session_id": "sess-abc12345",
        "turn_id": "turn-0001abc",
        "message": "cheapest butter",
    }
    reordered = {k: body[k] for k in reversed(list(body))}
    spaced = json.dumps(body, indent=4)

    assert json.dumps(body) != json.dumps(reordered)
    assert json.dumps(body) != spaced

    prints = {
        fingerprint_request(ChatRequest.model_validate(body)),
        fingerprint_request(ChatRequest.model_validate(reordered)),
        fingerprint_request(ChatRequest.model_validate_json(spaced)),
    }
    assert len(prints) == 1


# ================================================== Pilot Task 6: owner fencing
#
# complete() and release() were unconditional. The race:
#
#   1. invocation A acquires, then stalls past the in-progress timeout
#   2. invocation B legitimately takes over the stale claim and starts work
#   3. A wakes up
#
# Unfenced, A's complete() overwrites B's claim with A's older answer — which
# the next retry is then served as cached truth — or A's release() deletes B's
# marker, letting a THIRD invocation start the same turn while B is still
# running. Both defeat the table's entire purpose while passing every
# single-threaded test.


def _stale(monkeypatch) -> None:
    """Make every existing claim look abandoned."""
    monkeypatch.setattr(idempotency_module, "IN_PROGRESS_TIMEOUT_SECONDS", -1)


def test_acquire_returns_a_token_and_a_takeover_rotates_it(monkeypatch):
    store = InMemoryIdempotencyStore()
    key, h = make_key("s", "t"), fingerprint("{}")

    first = store.acquire(key, h)
    assert first.claim_token, "an acquired claim must carry proof of ownership"

    _stale(monkeypatch)
    second = store.acquire(key, h)

    assert second.status is AcquireStatus.ACQUIRED
    assert second.claim_token != first.claim_token, (
        "a takeover must rotate the token; started_at alone is not ownership"
    )


def test_a_superseded_invocation_cannot_complete(monkeypatch):
    store = InMemoryIdempotencyStore()
    key, h = make_key("s", "t"), fingerprint("{}")
    first = store.acquire(key, h)
    _stale(monkeypatch)
    second = store.acquire(key, h)

    assert store.complete(key, first.claim_token or "", '{"stale":true}') is False
    assert store.complete(key, second.claim_token or "", '{"fresh":true}') is True

    monkeypatch.setattr(idempotency_module, "IN_PROGRESS_TIMEOUT_SECONDS", 60)
    replay = store.acquire(key, h)
    assert replay.status is AcquireStatus.COMPLETED
    assert replay.cached_response == '{"fresh":true}', "the older answer must not win"


def test_a_superseded_invocation_cannot_release(monkeypatch):
    """
    The worse of the two. Deleting the newer marker would let a third
    invocation start the same turn while the second is still running.
    """
    store = InMemoryIdempotencyStore()
    key, h = make_key("s", "t"), fingerprint("{}")
    first = store.acquire(key, h)
    _stale(monkeypatch)
    second = store.acquire(key, h)

    assert store.release(key, first.claim_token or "") is False

    monkeypatch.setattr(idempotency_module, "IN_PROGRESS_TIMEOUT_SECONDS", 60)
    assert store.acquire(key, h).status is AcquireStatus.IN_PROGRESS, (
        "the surviving claim must still be held by the invocation that owns it"
    )
    assert store.release(key, second.claim_token or "") is True


def test_an_unknown_token_is_refused():
    store = InMemoryIdempotencyStore()
    key, h = make_key("s", "t"), fingerprint("{}")
    store.acquire(key, h)

    assert store.complete(key, "not-the-token", "{}") is False
    assert store.release(key, "not-the-token") is False
