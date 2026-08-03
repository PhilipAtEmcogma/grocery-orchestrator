"""Validates the sample payloads against the contract. Run in CI."""

import sys
from pathlib import Path

from src.schemas.contract import (
    ChatRequest,
    ChatResponse,
    MealPlanEvent,
    assert_arithmetic,
    assert_grounded,
)

SAMPLES = Path(__file__).parent / "samples"

requests_ok = 0
responses_ok = 0
failures = []

# Every sample request must parse as a valid ChatRequest.
for path in sorted(SAMPLES.glob("request_*.json")):
    try:
        ChatRequest.model_validate_json(path.read_text())
        requests_ok += 1
        print(f"  OK  {path.name}")
    except Exception as e:
        failures.append((path.name, e))
        print(f"FAIL  {path.name}: {e}")

# Every sample response must parse AND satisfy the grounding/arithmetic invariants.
for path in sorted(SAMPLES.glob("response_*.json")):
    try:
        resp = ChatResponse.model_validate_json(path.read_text())
        assert_grounded(resp)
        for ev in resp.events:
            if isinstance(ev, MealPlanEvent):
                assert_arithmetic(ev.data)
        responses_ok += 1
        print(f"  OK  {path.name}  (grounding + arithmetic verified)")
    except Exception as e:
        failures.append((path.name, e))
        print(f"FAIL  {path.name}: {e}")

print(f"\n{requests_ok} requests, {responses_ok} responses validated")

# Negative test: an ungrounded price must be REJECTED.
# This response cites "c99" in a price_comparison payload but never emits a
# CitationEvent declaring it — assert_grounded must catch that.
print("\nNegative test — ungrounded price must fail:")
bad = {
    "version": "1.0",
    "session_id": "sess-abc12345",
    "turn_id": "turn-0001abc",
    "events": [
        {"seq": 0, "type": "session", "session_id": "sess-abc12345",
         "turn_id": "turn-0001abc", "version": "1.0"},
        {"seq": 1, "type": "price_comparison",
         "data": {"query_item": "butter",
                  "options": [{"citation_ref": "c99", "is_cheapest": True}],
                  "reasoning": "invented"}},
        {"seq": 2, "type": "done", "server_time": "2026-07-30T19:45:00Z"},
    ],
}
try:
    assert_grounded(ChatResponse.model_validate(bad))
    print("  UNEXPECTED PASS — invariant is broken!")
    failures.append(("negative_test", "did not reject ungrounded ref"))
except AssertionError as e:
    print(f"  Correctly rejected: {e}")

# Non-zero exit code fails the CI step if anything above did not behave as expected.
sys.exit(1 if failures else 0)
