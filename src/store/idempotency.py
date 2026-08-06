"""
Idempotency.

The contract promises that resending a turn_id yields the same answer without
re-running the work. Without this, a client that times out at the gateway's
29-second ceiling and retries triggers a second full generation — charged
twice, and possibly returning a different plan than the first attempt would
have.

Four decisions worth understanding:

1. KEYED ON SESSION AND TURN, NOT TURN ALONE. Clients generate turn_id, and
   nothing stops two sessions producing the same value. A collision would
   serve one user another user's shopping list.

2. THE PAYLOAD IS FINGERPRINTED. If the same turn_id arrives with different
   content, that is a client bug, and returning the cached response would
   silently answer a question nobody asked. It is rejected instead.

3. IN-FLIGHT REQUESTS ARE DETECTED. A retry usually arrives while the first
   attempt is STILL RUNNING — that is what a timeout means. Without an
   in-progress marker, both run and the point is lost.

4. RETRYABLE ERRORS ARE NOT CACHED. Caching a transient failure would make
   the client's retry permanently useless: it would receive the same failure
   forever. Only terminal outcomes are stored.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

DEFAULT_TTL_SECONDS = 24 * 60 * 60
# How long an in-progress marker is honoured before being treated as stale.
# Longer than the gateway ceiling so a slow-but-alive request is not
# duplicated; short enough that a crashed invocation does not block retries.
IN_PROGRESS_TIMEOUT_SECONDS = 60


class AcquireStatus(StrEnum):
    ACQUIRED = "acquired"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PAYLOAD_MISMATCH = "payload_mismatch"


@dataclass(frozen=True, slots=True)
class AcquireResult:
    status: AcquireStatus
    cached_response: str | None = None


def make_key(session_id: str, turn_id: str) -> str:
    """Scoped by session; a client-generated turn_id is not globally unique."""
    return f"{session_id}#{turn_id}"


def fingerprint(payload: str) -> str:
    """
    Content hash, so a reused turn_id with different content is caught.

    Truncated to 32 hex characters: enough that an accidental collision is
    not a practical concern, and this is a client-bug detector rather than a
    security boundary.
    """
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class IdempotencyStore(Protocol):
    """Storage for turn results. DynamoDB in production, memory in tests."""

    def acquire(self, key: str, payload_hash: str) -> AcquireResult:
        """
        Claim the key, or report why it cannot be claimed.

        Must be atomic. In DynamoDB this is a conditional put on
        attribute_not_exists — two concurrent invocations must not both
        receive ACQUIRED.
        """
        ...

    def complete(self, key: str, response_json: str) -> None:
        """Store a terminal result. Subsequent acquires return it."""
        ...

    def release(self, key: str) -> None:
        """
        Drop an in-progress marker without storing a result.

        Called when the turn failed in a way the client should retry. Leaving
        the marker would block the retry for the full in-progress timeout.
        """
        ...


@dataclass
class _Record:
    payload_hash: str
    status: str
    response_json: str | None
    started_at: float
    expires_at: float


class InMemoryIdempotencyStore(IdempotencyStore):
    """
    Fixture implementation.

    Correct for a single process only. A real deployment spans Lambda
    execution environments that share no memory, which is why the stored
    implementation is not optional in production.
    """

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._records: dict[str, _Record] = {}
        self._ttl = ttl_seconds

    def acquire(self, key: str, payload_hash: str) -> AcquireResult:
        now = time.time()
        record = self._records.get(key)

        if record is not None and record.expires_at <= now:
            record = None
            self._records.pop(key, None)

        if record is None:
            self._records[key] = _Record(
                payload_hash=payload_hash,
                status="in_progress",
                response_json=None,
                started_at=now,
                expires_at=now + self._ttl,
            )
            return AcquireResult(AcquireStatus.ACQUIRED)

        if record.payload_hash != payload_hash:
            return AcquireResult(AcquireStatus.PAYLOAD_MISMATCH)

        if record.status == "completed":
            return AcquireResult(AcquireStatus.COMPLETED, record.response_json)

        # In progress. If the owning invocation looks dead, take it over
        # rather than leaving the client stuck until the TTL expires.
        if now - record.started_at > IN_PROGRESS_TIMEOUT_SECONDS:
            record.started_at = now
            record.payload_hash = payload_hash
            return AcquireResult(AcquireStatus.ACQUIRED)

        return AcquireResult(AcquireStatus.IN_PROGRESS)

    def complete(self, key: str, response_json: str) -> None:
        record = self._records.get(key)
        if record is None:
            return
        record.status = "completed"
        record.response_json = response_json

    def release(self, key: str) -> None:
        self._records.pop(key, None)
