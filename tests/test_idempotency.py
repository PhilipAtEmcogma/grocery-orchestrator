"""
Idempotency tests.

The property: resending a turn returns the same answer without redoing the
work — and the cases where that would be WRONG are handled instead.
"""

from __future__ import annotations

import json
import time

import pytest

from src.handler import lambda_handler
from src.schemas.contract import ChatResponse
from src.store.idempotency import (
    IN_PROGRESS_TIMEOUT_SECONDS,
    AcquireStatus,
    InMemoryIdempotencyStore,
    fingerprint,
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
    store.acquire(key, h)
    store.complete(key, '{"events":[]}')

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
    store.acquire(key, h)
    store.release(key)
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
        fingerprint(json.dumps(body)),
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
        fingerprint(json.dumps(body)),
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
