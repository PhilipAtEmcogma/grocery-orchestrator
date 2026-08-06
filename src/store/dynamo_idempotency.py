"""
DynamoDB-backed idempotency store.

NOT YET IMPLEMENTED — no AWS account. Present so the import resolves and the
wiring is proven; construction raises rather than silently behaving like a
store that never deduplicates, which would look like working software.

Table design (add to DYNAMODB-SCHEMA.md when built):

    PK   idem#<session_id>#<turn_id>
    ttl  epoch seconds, 24h

The acquire operation MUST be a conditional put on attribute_not_exists(pk),
or on a stale in-progress marker. Read-then-write is not sufficient: two
Lambda invocations racing on the same key would both read "absent" and both
proceed, which defeats the entire purpose.
"""

from __future__ import annotations

from src.store.idempotency import AcquireResult, IdempotencyStore


class DynamoIdempotencyStore(IdempotencyStore):
    def __init__(self, table_name: str = "grocery-idempotency-dev") -> None:
        self.table_name = table_name
        raise NotImplementedError(
            "DynamoIdempotencyStore is not implemented yet. "
            "Unset USE_DYNAMODB to run against the in-memory store."
        )

    def acquire(self, key: str, payload_hash: str) -> AcquireResult:
        raise NotImplementedError

    def complete(self, key: str, response_json: str) -> None:
        raise NotImplementedError

    def release(self, key: str) -> None:
        raise NotImplementedError
