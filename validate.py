"""Validates the sample payloads against the contract. Run in CI."""

import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from src.retrieval.base import PriceRecord
from src.schemas.contract import (
    ChatRequest,
    ChatResponse,
    Citation,
    CitationEvent,
    DoneEvent,
    Event,
    MealPlanEvent,
    SourceRef,
    Store,
    assert_arithmetic,
    assert_citations_match_retrieval,
    assert_grounded,
    assert_no_literal_money_in_response,
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
        assert_no_literal_money_in_response(resp)
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
        {
            "seq": 0,
            "type": "session",
            "session_id": "sess-abc12345",
            "turn_id": "turn-0001abc",
            "version": "1.0",
        },
        {
            "seq": 1,
            "type": "price_comparison",
            "data": {
                "query_item": "butter",
                "options": [{"citation_ref": "c99", "is_cheapest": True}],
                "reasoning": "invented",
            },
        },
        {"seq": 2, "type": "done", "server_time": "2026-07-30T19:45:00Z"},
    ],
}
try:
    assert_grounded(ChatResponse.model_validate(bad))
    print("  UNEXPECTED PASS — invariant is broken!")
    failures.append(("negative_test", "did not reject ungrounded ref"))
except AssertionError as e:
    print(f"  Correctly rejected: {e}")

# Negative test: citation used BEFORE it is declared must fail (ordering).
print("\nNegative test — citation used before declared must fail:")
bad_order = {
    "version": "1.0",
    "session_id": "sess-abc12345",
    "turn_id": "turn-0002abc",
    "events": [
        {
            "seq": 0,
            "type": "session",
            "session_id": "sess-abc12345",
            "turn_id": "turn-0002abc",
            "version": "1.0",
        },
        {
            "seq": 1,
            "type": "price_comparison",
            "data": {
                "query_item": "butter",
                "options": [{"citation_ref": "c1", "is_cheapest": True}],
                "reasoning": "cheapest option",
            },
        },
        {
            "seq": 2,
            "type": "citation",
            "citation": {
                "ref": "c1",
                "store": "paknsave",
                "store_location": "Sylvia Park",
                "product_name": "Butter 500g",
                "price_nzd": "2.97",
                "unit": "500g",
                "on_special": False,
                "valid_date": "2026-07-30",
                "source": {
                    "table": "grocery-products-dev",
                    "pk": "paknsave#sylvia-park",
                    "sk": "butter-500g",
                },
            },
        },
        {"seq": 3, "type": "done", "server_time": "2026-07-30T19:45:00Z"},
    ],
}
try:
    assert_grounded(ChatResponse.model_validate(bad_order))
    print("  UNEXPECTED PASS — ordering check is broken!")
    failures.append(("negative_order_test", "did not reject citation-after-use"))
except AssertionError as e:
    print(f"  Correctly rejected: {e}")

# Negative test: literal money in reasoning must fail.
print("\nNegative test — literal money in reasoning must fail:")
bad_money = {
    "version": "1.0",
    "session_id": "sess-abc12345",
    "turn_id": "turn-0003abc",
    "events": [
        {
            "seq": 0,
            "type": "session",
            "session_id": "sess-abc12345",
            "turn_id": "turn-0003abc",
            "version": "1.0",
        },
        {
            "seq": 1,
            "type": "citation",
            "citation": {
                "ref": "c1",
                "store": "paknsave",
                "store_location": "Sylvia Park",
                "product_name": "Butter 500g",
                "price_nzd": "2.97",
                "unit": "500g",
                "on_special": False,
                "valid_date": "2026-07-30",
                "source": {
                    "table": "grocery-products-dev",
                    "pk": "paknsave#sylvia-park",
                    "sk": "butter-500g",
                },
            },
        },
        {
            "seq": 2,
            "type": "price_comparison",
            "data": {
                "query_item": "butter",
                "options": [{"citation_ref": "c1", "is_cheapest": True}],
                "reasoning": "Cheapest at $2.97 for 500g",
            },
        },
        {"seq": 3, "type": "done", "server_time": "2026-07-30T19:45:00Z"},
    ],
}
try:
    assert_no_literal_money_in_response(ChatResponse.model_validate(bad_money))
    print("  UNEXPECTED PASS — literal-money check is broken!")
    failures.append(("negative_money_test", "did not reject literal money"))
except AssertionError as e:
    print(f"  Correctly rejected: {e}")

# Negative tests: a citation must BE the record it names (Req 3.5-3.6).
#
# The three checks above all read the response alone, which is why they cannot
# catch these two. `assert_grounded` accepts a wrong partition key -- it still
# contains a '#' -- and accepts any price at all, because it has nothing to
# compare one to. Shape is not identity.
#
# The samples carry no retrieval context, so these are built against a stub
# record here rather than run over `samples/`. Req 3.6 names four negative
# cases; unknown references and content-before-citation are covered above, and
# these are the remaining two.
STUB_TABLE = "grocery-products-dev"
STUB_RECORD = PriceRecord(
    product_key="butter-500g",
    store=Store.PAKNSAVE,
    store_location="Mangere",
    display_name="Pams Butter 500g",
    canonical_name="butter",
    category="dairy",
    price_nzd=Decimal("2.97"),
    unit="500g",
    unit_price_nzd=Decimal("5.94"),
    pack_grams=500,
    on_special=True,
    valid_date="2026-07-31",
    lat=-36.98,
    lon=174.80,
    store_key="paknsave#mangere",
)


def _cited(**overrides) -> ChatResponse:
    """A one-citation response matching STUB_RECORD unless told otherwise."""
    source = SourceRef(
        table=overrides.pop("table", STUB_TABLE),
        pk=overrides.pop("pk", "paknsave#mangere"),
        sk=overrides.pop("sk", "butter-500g"),
    )
    fields = {
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
    events: list[Event] = []
    events.append(CitationEvent(seq=0, citation=Citation(**{**fields, **overrides}, source=source)))
    events.append(DoneEvent(seq=1, server_time=datetime(2026, 7, 30, 19, 45, tzinfo=UTC)))
    return ChatResponse(session_id="sess-negctl01", turn_id="turn-negctl01", events=events)


def _expect_rejected(label: str, response: ChatResponse, key: str) -> None:
    print(f"\nNegative test — {label} must fail:")
    # It must pass the shape check first, or the new rule is not what caught it.
    try:
        assert_grounded(response)
    except AssertionError:
        print("  SETUP ERROR — assert_grounded rejected it, so this proves nothing")
        failures.append((key, "negative case was caught by the shape check"))
        return
    try:
        assert_citations_match_retrieval(response, table=STUB_TABLE, records={"c1": STUB_RECORD})
        print("  UNEXPECTED PASS — retrieved-record equality is broken!")
        failures.append((key, f"did not reject {label}"))
    except AssertionError as e:
        print(f"  Correctly rejected: {str(e).splitlines()[1].strip()}")


_expect_rejected(
    "an incorrect source key",
    _cited(pk="paknsave#sylvia-park"),
    "negative_source_key_test",
)
_expect_rejected(
    "an altered value",
    _cited(price_nzd=Decimal("0.99")),
    "negative_altered_value_test",
)

# Positive control: the same construction, untampered, must pass. A rule that
# rejects everything is as useless as one that rejects nothing.
print("\nPositive control — a citation matching its record must pass:")
try:
    assert_citations_match_retrieval(_cited(), table=STUB_TABLE, records={"c1": STUB_RECORD})
    print("  OK  matching citation accepted")
except AssertionError as e:
    print(f"  UNEXPECTED FAIL — the rule rejects a correct citation: {e}")
    failures.append(("positive_control", str(e)))

# Non-zero exit code fails the CI step if anything above did not behave as expected.
sys.exit(1 if failures else 0)
