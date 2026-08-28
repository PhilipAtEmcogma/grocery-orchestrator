"""
DynamoDB-backed idempotency store.

Table: grocery-idempotency-dev (see DYNAMODB-SCHEMA.md)
PK: idem#<session_id>#<turn_id>
TTL: 24h, enabled on the `ttl` attribute.

The acquire operation is a conditional put on attribute_not_exists(pk) OR
a stale in-progress marker. This is the ONLY correct implementation: a
read-then-write would allow two concurrent Lambda invocations to both
proceed, defeating the entire purpose.

Run the idempotency tests with:

    IDEMPOTENCY_DYNAMO_TABLE=grocery-idempotency-dev python -m pytest \
        tests/test_idempotency.py
"""

from __future__ import annotations

import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from src.store.idempotency import (
    DEFAULT_TTL_SECONDS,
    IN_PROGRESS_TIMEOUT_SECONDS,
    AcquireResult,
    AcquireStatus,
    IdempotencyStore,
)

REGION = "ap-southeast-2"


class DynamoIdempotencyStore(IdempotencyStore):
    def __init__(self, table_name: str = "grocery-idempotency-dev") -> None:
        self._table_name = table_name
        dynamodb = boto3.resource(
            "dynamodb",
            region_name=REGION,
            config=Config(
                retries={"max_attempts": 2, "mode": "standard"},
                read_timeout=10,
                connect_timeout=5,
            ),
        )
        self._table = dynamodb.Table(table_name)  # type: ignore[union-attr]
        self._ttl_seconds = DEFAULT_TTL_SECONDS

    def acquire(self, key: str, payload_hash: str) -> AcquireResult:
        """
        Atomic claim via conditional put.

        Succeeds only if:
        - No record exists for this key (attribute_not_exists), OR
        - An in_progress record exists but is stale (older than the timeout)

        A ConditionalCheckFailedException means someone else holds the key.
        We then read the item to determine which of the four outcomes applies.
        """
        now = int(time.time())
        pk = f"idem#{key}"
        stale_threshold = now - IN_PROGRESS_TIMEOUT_SECONDS

        try:
            self._table.put_item(
                Item={
                    "pk": pk,
                    "payload_hash": payload_hash,
                    "status": "in_progress",
                    "started_at": now,
                    "ttl": now + self._ttl_seconds,
                },
                ConditionExpression=(
                    "attribute_not_exists(pk) OR (#s = :in_progress AND started_at < :stale)"
                ),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":in_progress": "in_progress",
                    ":stale": stale_threshold,
                },
            )
            return AcquireResult(AcquireStatus.ACQUIRED)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

        # The conditional put failed — someone else holds the key.
        # Read the existing record to decide the outcome.
        response = self._table.get_item(Key={"pk": pk})
        item = response.get("Item")

        if item is None:
            # Race: TTL deleted it between the put and the get.
            # Retry once — this is vanishingly rare.
            return self.acquire(key, payload_hash)

        # Payload mismatch: same turn_id, different content = client bug.
        if item.get("payload_hash") != payload_hash:
            return AcquireResult(AcquireStatus.PAYLOAD_MISMATCH)

        # Completed: return the cached response.
        if item.get("status") == "completed":
            return AcquireResult(
                AcquireStatus.COMPLETED,
                item.get("response_json"),
            )

        # In progress and not stale — someone else is working on it.
        return AcquireResult(AcquireStatus.IN_PROGRESS)

    def complete(self, key: str, response_json: str) -> None:
        """Store a terminal result. Subsequent acquires return it verbatim."""
        pk = f"idem#{key}"
        self._table.update_item(
            Key={"pk": pk},
            UpdateExpression="SET #s = :completed, response_json = :resp",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":completed": "completed",
                ":resp": response_json,
            },
        )

    def release(self, key: str) -> None:
        """
        Drop the in-progress marker without caching a result.

        Called when the turn failed in a retryable way — caching the failure
        would make the client's retry permanently useless.
        """
        pk = f"idem#{key}"
        self._table.delete_item(Key={"pk": pk})
