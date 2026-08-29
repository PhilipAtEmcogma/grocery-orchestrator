r"""
DEMO 6 - The HTTP API, the contract, and idempotency
====================================================

HOW TO RUN
----------
    python Philip_demo/06_http_api_and_idempotency.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/06_http_api_and_idempotency.py

Offline. The Lambda handler defaults to fixtures + the scripted model unless
USE_DYNAMODB=1 / USE_BEDROCK=1 are set, so this exercises the real handler
with no AWS account.

To drive the same handler over real HTTP instead, in one terminal:

    python scripts/dev_server.py

and in another:

    curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
         -d '{"version":"1.0","session_id":"sess-local01",
              "turn_id":"turn-local01","message":"cheapest butter"}'

WHAT THIS DEMONSTRATES
----------------------
  1. The Lambda handler end to end, over an API Gateway event
  2. Status codes, and the rule that every response has a valid body
  3. A malformed request answered with a contract-valid error, not a stack trace
  4. Idempotency: the same turn_id replayed returns the CACHED response
  5. A replayed turn_id with a DIFFERENT payload is a conflict, not a silent overwrite
  6. The published samples/ validating against the contract
"""

from __future__ import annotations

import json
from pathlib import Path

from _demo_support import heading, section

import src.store.idempotency as idempotency
from src.handler import lambda_handler
from src.schemas.contract import (
    ChatRequest,
    ChatResponse,
    assert_grounded,
    assert_no_literal_money_in_response,
)
from src.store.idempotency import (
    AcquireStatus,
    InMemoryIdempotencyStore,
    fingerprint,
    make_key,
)

ROOT = Path(__file__).resolve().parent.parent

heading("DEMO 6 - The HTTP API, the contract, and idempotency")


def api_event(body: dict | str) -> dict:
    """An API Gateway proxy event, which is what Lambda actually receives."""
    return {
        "httpMethod": "POST",
        "path": "/chat",
        "headers": {"Content-Type": "application/json"},
        "body": body if isinstance(body, str) else json.dumps(body),
        "isBase64Encoded": False,
    }


# ------------------------------------------------------------ happy path
section("1. A turn through the real Lambda handler")
result = lambda_handler(
    api_event(
        {
            "version": "1.0",
            "session_id": "sess-http01",
            "turn_id": "turn-http01",
            "message": "cheapest butter",
        }
    )
)
print(f"  HTTP {result['statusCode']}")
body = json.loads(result["body"])
print(f"  events: {[e['type'] for e in body['events']]}")

# The body is not merely JSON - it satisfies the published contract.
response = ChatResponse.model_validate(body)
assert_grounded(response)
assert_no_literal_money_in_response(response)
print("  Body validates as ChatResponse, and passes both contract assertions.")

# --------------------------------------------------------- malformed input
section("2. A malformed request still gets a contract-valid body")
result = lambda_handler(api_event({"message": "no ids, no version"}))
print(f"  HTTP {result['statusCode']}")
bad_body = json.loads(result["body"])
print(f"  events: {[e['type'] for e in bad_body['events']]}")
err = next((e for e in bad_body["events"] if e["type"] == "error"), None)
if err:
    print(f"  {err['code']}: {err['message'][:90]}")
print("\n  The invariant is that there is NO path out of the handler without a")
print("  parseable body. A client should never have to deal with whatever API")
print("  Gateway synthesises from a stack trace.")

section("3. Even a body that is not JSON at all")
result = lambda_handler(api_event("this is not json{{{"))
print(f"  HTTP {result['statusCode']}  body parses: ", end="")
try:
    ChatResponse.model_validate(json.loads(result["body"]))
    print("yes")
except Exception as exc:
    print(f"NO - {type(exc).__name__}")

# ------------------------------------------------------------ idempotency
section("4. Idempotency - a replayed turn_id returns the cached response")
store = InMemoryIdempotencyStore()
payload = json.dumps({"message": "cheapest butter"})
key = make_key("sess-http02", "turn-http02")
print(f"  key = {key}")

first = store.acquire(key, fingerprint(payload))
print(f"  first acquire:  {first.status.value}")
print(f"  claim token:    {(first.claim_token or '')[:8]}...")
# complete() is owner-conditional: it takes the token acquire() handed back.
store.complete(key, first.claim_token or "", '{"events": ["... the original response ..."]}')

second = store.acquire(key, fingerprint(payload))
print(f"  second acquire: {second.status.value}")
print(f"  cached body returned: {second.cached_response}")
print("\n  A retry after a client timeout must not plan the meals twice, bill")
print("  twice, or return a different answer for the same question.")

section("4b. A superseded invocation cannot overwrite the newer claim")
fenced = InMemoryIdempotencyStore()
key3 = make_key("sess-http04", "turn-http04")
slow = fenced.acquire(key3, fingerprint(payload))
print(f"  invocation A acquires:      {(slow.claim_token or '')[:8]}...")

# A stalls past the in-progress timeout and B legitimately takes the claim over.
idempotency.IN_PROGRESS_TIMEOUT_SECONDS = -1
took_over = fenced.acquire(key3, fingerprint(payload))
idempotency.IN_PROGRESS_TIMEOUT_SECONDS = 60
print(f"  invocation B takes over:    {(took_over.claim_token or '')[:8]}...  (token rotated)")

print(f"  A tries to complete:        {fenced.complete(key3, slow.claim_token or '', '{}')}")
print(f"  A tries to release:         {fenced.release(key3, slow.claim_token or '')}")
b_wrote = fenced.complete(key3, took_over.claim_token or "", '{"fresh": 1}')
print(f"  B completes:                {b_wrote}")
print("\n  Without the fence, A's older answer would overwrite B's claim and be")
print("  served to the next retry as cached truth - or A's release would delete")
print("  B's marker and let a third invocation start the same turn again.")

section("5. In-flight replay is a conflict, not a race")
key2 = make_key("sess-http03", "turn-http03")
store.acquire(key2, fingerprint(payload))  # first request, still running
inflight = store.acquire(key2, fingerprint(payload))
print(f"  concurrent acquire: {inflight.status.value}")
print("  -> the handler answers HTTP 409 rather than running the turn twice.")

section("6. Same turn_id, DIFFERENT payload")
mismatch = store.acquire(key, fingerprint(json.dumps({"message": "something else"})))
print(f"  acquire status: {mismatch.status.value}")
print("\n  A client reusing a turn_id for a new question is a bug on their")
print("  side. Returning the cached answer to a different question would be")
print("  worse than an error, so it is reported rather than absorbed.")
matched = mismatch.status is AcquireStatus.PAYLOAD_MISMATCH
print(f"  reported as PAYLOAD_MISMATCH: {matched}")

# ------------------------------------------------------------- the samples
section("7. The published samples validate against the contract")
print("  These are what the frontend team builds against.\n")
for path in sorted((ROOT / "samples").glob("response_*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        resp = ChatResponse.model_validate(data)
        assert_grounded(resp)
        assert_no_literal_money_in_response(resp)
        codes = [e.code.value for e in resp.events if e.type == "error"]
        note = f"  ({', '.join(codes)})" if codes else ""
        print(f"    OK  {path.name}{note}")
    except Exception as exc:
        print(f"    FAIL {path.name}: {type(exc).__name__}: {str(exc)[:70]}")

for path in sorted((ROOT / "samples").glob("request_*.json")):
    ChatRequest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    print(f"    OK  {path.name}")

print("\n  `python validate.py` runs this in CI, including a negative test")
print("  that a sample with literal money in prose is REJECTED.")
print("\nDone.")
