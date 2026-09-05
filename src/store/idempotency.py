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
import json
import secrets
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from src.schemas.contract import ChatRequest

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
    # Present only on ACQUIRED. `complete()` and `release()` require it, and a
    # caller that never acquired has nothing to prove ownership with.
    claim_token: str | None = None


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


def _canonical(value: Any) -> Any:
    """
    Reduce a dumped request to a form where equivalent content compares equal.

    `Decimal` is the interesting case. `model_dump(mode="json")` renders it with
    its original scale, so a client sending `"budget_nzd": 30` and one sending
    `30.00` produced DIFFERENT fingerprints for the same budget -- and the
    second was rejected as a payload mismatch, which is a 400 the client cannot
    retry. Normalising drops trailing zeros so the two agree. `format(..., "f")`
    rather than `str()` because `Decimal("30").normalize()` is `3E+1`, and an
    exponent form would make the fingerprint depend on magnitude.

    Dates and enums are rendered here rather than by `mode="json"` so that the
    whole structure passes through one traversal with one set of rules.
    """
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def canonical_payload(request: ChatRequest) -> str:
    """
    The bytes the fingerprint is taken over: validated CONTENT, not HTTP bytes.

    The handler used to hash the raw request body, which made the fingerprint
    sensitive to things that carry no meaning -- whitespace, JSON object-key
    order, and whether an optional field was omitted or sent explicitly as
    null. A client whose retry serialised its dict in a different order was
    told its correct retry was a client bug, by the mechanism that exists to
    help it recover from a timeout.

    `exclude_none=True` makes omitted and explicitly-null equivalent.
    `sort_keys=True` sorts recursively, so object order cannot matter. List
    order IS preserved: `["dairy", "seafood"]` and `["seafood", "dairy"]` are
    the same request, but proving that in general needs a per-field rule, and
    treating a different list as a different request errs toward rejecting a
    retry rather than serving the wrong cached answer.
    """
    payload = _canonical(request.model_dump(mode="python", exclude_none=True))
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint_request(request: ChatRequest) -> str:
    """Fingerprint of the validated request. This is what the handler stores."""
    return fingerprint(canonical_payload(request))


def new_claim_token() -> str:
    """
    Opaque proof of ownership, rotated on every acquire and every takeover.

    `started_at` alone is not ownership: two invocations can hold the same
    timestamp, and an invocation that resumes after its claim was taken over
    still remembers a timestamp that once was current.
    """
    return secrets.token_urlsafe(24)


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

    def complete(self, key: str, claim_token: str, response_json: str) -> bool:
        """
        Store a terminal result, only if this caller still owns the claim.

        Returns False when the fence failed: another invocation took over the
        stale claim while this one was working, and its claim must not be
        overwritten with an older answer. The caller has still done valid work
        and should return its response to ITS client -- what it has lost is the
        right to cache it, not the right to answer.
        """
        ...

    def release(self, key: str, claim_token: str) -> bool:
        """
        Drop this caller's in-progress marker without storing a result.

        Called when the turn failed in a way the client should retry. Leaving
        the marker would block the retry for the full in-progress timeout.

        Owner-conditional for the same reason as `complete`, and the failure is
        nastier: deleting a newer invocation's marker would let a THIRD
        invocation start the same turn again while the second is still running.
        """
        ...


@dataclass
class _Record:
    payload_hash: str
    status: str
    response_json: str | None
    started_at: float
    expires_at: float
    claim_token: str = ""


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
            token = new_claim_token()
            self._records[key] = _Record(
                payload_hash=payload_hash,
                status="in_progress",
                response_json=None,
                started_at=now,
                expires_at=now + self._ttl,
                claim_token=token,
            )
            return AcquireResult(AcquireStatus.ACQUIRED, claim_token=token)

        if record.payload_hash != payload_hash:
            return AcquireResult(AcquireStatus.PAYLOAD_MISMATCH)

        if record.status == "completed":
            return AcquireResult(AcquireStatus.COMPLETED, record.response_json)

        # In progress. If the owning invocation looks dead, take it over
        # rather than leaving the client stuck until the TTL expires.
        if now - record.started_at > IN_PROGRESS_TIMEOUT_SECONDS:
            # A takeover ALWAYS rotates the token. Reusing it would leave the
            # abandoned invocation able to complete or release a claim it no
            # longer holds, which is the whole failure this fences.
            token = new_claim_token()
            record.started_at = now
            record.payload_hash = payload_hash
            record.claim_token = token
            return AcquireResult(AcquireStatus.ACQUIRED, claim_token=token)

        return AcquireResult(AcquireStatus.IN_PROGRESS)

    def complete(self, key: str, claim_token: str, response_json: str) -> bool:
        record = self._records.get(key)
        if record is None or record.claim_token != claim_token:
            return False
        record.status = "completed"
        record.response_json = response_json
        return True

    def release(self, key: str, claim_token: str) -> bool:
        record = self._records.get(key)
        if record is None or record.claim_token != claim_token:
            return False
        del self._records[key]
        return True
