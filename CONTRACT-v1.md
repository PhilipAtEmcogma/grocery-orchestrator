# Frontend ↔ Orchestrator Contract v1.0

**Smart Grocery & Meal Budget Assistant**
Owner: Backend/Orchestration + AI/Prompt Lead
Status: **Published shape; frontend review and pilot-hardening gaps remain**
Region: `ap-southeast-2` (Sydney)

The v1 event shapes remain the compatibility baseline. Pilot Tasks 2–3
corrected citation construction, citation-before-use ordering, money-free comparison/prose labels, regenerated samples, and offline
GuardrailBlocked propagation. Remaining release blockers include immutable
retrieved-record/value equality, whole-response runtime literal-money
integration, and qualifying live Guardrail policy evidence. Those changes stay
additive within v1 where possible; breaking schema changes require v2.

The generated samples now use configured table/`store_key`/normalized
`product_key` citations and money-free prose labels. They prove contract shape,
declaration/order, and basic source shape only. Neither sample validation nor
current `assert_grounded()` independently compares citation keys or values with
an immutable retrieved record; Req 3.5–3.6 equality and altered-value controls
remain a Pilot Task 2 follow-up.

---

## Why this shape

The response is a **list of events**, not a single object. This is deliberate:

- **Week 1–2** ships over `POST /chat` (API Gateway REST). The whole event list
  comes back at once in the `events` array.
- **Week 3–4** upgrades to WebSocket streaming. The *same events* arrive one at
  a time.

If you write your client as an **event handler** rather than a response parser,
the transport upgrade costs you almost nothing. Please do that.

The authoritative shopper transport remains REST to the deterministic Lambda
service. Planned local MCP and the proposed AgentCore Gateway hybrid may expose
only coarse complete-application operations returning this same validated
contract; neither is a new path around LangGraph. Gateway is proposed under ADR
0002 and requires mentor approval. The separate proposed Runtime reviewer never
serves this frontend contract.

---

## Request

`POST /chat`

```json
{
  "version": "1.0",
  "session_id": "sess-7f3a9c21",
  "turn_id": "turn-0001-a4b8",
  "message": "what's the cheapest butter near me?",
  "location": {
    "lat": -36.8899, "lon": 174.8536,
    "label": "Sylvia Park, Auckland", "radius_km": 8
  },
  "hints": {
    "household_size": 3, "budget_nzd": 30, "days": 3,
    "dietary_exclusions": ["seafood"],
    "preferred_stores": ["paknsave"]
  }
}
```

| Field | Required | Notes |
|---|---|---|
| `session_id` | ✅ | Stable across a conversation. 8–64 chars. |
| `turn_id` | ✅ | **Unique per turn**, client-generated. Used for idempotency — resend the same `turn_id` on network retry. Target behavior compares canonical validated content, not raw JSON bytes. |
| `message` | ✅ | Raw user text, max 2000 chars. |
| `location` | ⬜ | Omit if the user hasn't granted permission. Price results will be national rather than local. |
| `hints` | ⬜ | Optional, from UI controls (sliders, chips, etc). |

The target idempotency guarantee returns the same completed answer without a
second generation. Canonicalization treats insignificant whitespace, object-key
order, and omitted-versus-explicit-null optional fields as equivalent.
**Current pilot blockers:** the handler still fingerprints the raw request body,
and stale takeover does not yet fence `complete()`/`release()` with an owner
token. Formatting-equivalent retries can therefore mismatch, and an old owner
can race a newer claim. Pilot Task 6 updates both stores and their shared
canonicalization/race/contract tests.

**On `hints`:** these *supplement* natural-language extraction, they don't
replace it. If the user types "actually make it $50" while the budget slider
says $30, **the message wins** and you'll get a `notice` event explaining the
override. Don't treat hints as authoritative for display — use the values echoed
back in the `meal_plan` payload.

---

## Response

```json
{
  "version": "1.0",
  "session_id": "sess-7f3a9c21",
  "turn_id": "turn-0001-a4b8",
  "events": [ { "seq": 0, "type": "session", ... }, ... ]
}
```

Every event has a `seq` (monotonic from 0 within a turn) and a `type`.
**Render in `seq` order.** Over WebSocket, `seq` lets you detect gaps.

### HTTP status

**Every status carries a parseable `ChatResponse` with a `done` event, including
`500`.** The outcome of a turn is in the body, not the status line — parse the
body rather than branching on the status.

| Status | When |
|---|---|
| `200` | The turn completed. This includes turns that ended in an `error` event we anticipated and handled. |
| `400` | The request was malformed, or reused a `turn_id` with different content. |
| `409` | That `turn_id` is still in flight. Back off and retry. |
| `500` | A bug on our side that got past the error handling. Body is a retryable `INTERNAL_ERROR` + `done`, exactly as at `200`. |

`500` is **additive**: a client already handling `200`, `400` and `409` needs no
change to handle it. The distinction between an `INTERNAL_ERROR` at `200` and one
at `500` exists for our alerting — a `500` means specifically "a failure nobody
predicted", which is otherwise invisible without reading logs. For a client both
mean the same thing: retry once with the same `turn_id`.

One caveat: some HTTP clients reject 5xx before you can read the body (`axios`
does by default, `fetch` does not). If yours does, disable that for this
endpoint — the body on a `500` is still the contract response and still tells
you what happened.

### Event types

| `type` | When | What to do with it |
|---|---|---|
| `session` | Always first | Confirm ids match what you sent |
| `intent` | Early | **Switch UI treatment before content arrives** — price card vs meal-plan card vs plain chat |
| `citation` | Before any event referencing it | Store in a lookup map keyed by `ref` |
| `token` | Streaming | Append to the message bubble |
| `price_comparison` | `price_check` turns | Render comparison table |
| `meal_plan` | `meal_plan` turns | Render plan + shopping list |
| `notice` | Occasionally | Small inline note (data age, hint override) |
| `no_data` | When we have no data | Render as a normal assistant reply, **not** an error |
| `error` | On failure | Show `message` — it's already user-safe |
| `done` | Always last | Stop spinner. Present even after an `error`. |

### The citation model — please read this bit

**No source price appears inline in a content payload without a
`citation_ref`.** Derived savings and totals are computed from cited prices and
must remain traceable to the records used.

Prices live in `citation` events. Payloads reference them by `ref` (`"c1"`,
`"c2"`…). To display a source price, look it up in the citation map.

```json
{ "seq": 2, "type": "citation", "citation": {
    "ref": "c1",
    "store": "paknsave",
    "store_location": "Sylvia Park",
    "product_name": "Pams Butter 500g",
    "price_nzd": "3.49",
    "unit": "500g",
    "unit_price_nzd": "6.98",
    "on_special": true,
    "valid_date": "2026-07-30",
    "source": {
      "table": "grocery-products-dev",
      "pk": "paknsave#sylvia-park",
      "sk": "butter-500g"
    }
} }
```

The target invariant requires `source` to identify the exact DynamoDB base
record: `table` is the configured physical table name (for the current live
environment, `grocery-products-dev`), `pk` is
`store_key = <chain>#<location-slug>`, and `sk` is the normalized
`product_key`. A response must emit the citation before any event that uses its
ref, and final validation must compare citation values with the retrieved
record.

**Implemented construction, incomplete final proof:** generated samples and the
reference workflow now use the configured physical table, `store_key`, and
normalized `product_key`; citation-before-use is checked and comparison/prose
labels are money-free. Current final validation checks declaration, order, and
basic source shape but lacks immutable retrieved-record context, so it does not
independently prove key/value equality or altered-value negative controls.
Frontend code must resolve structured prices through `citation_ref` and never
parse prose for money.

**Please surface `valid_date` and `on_special` in the UI.** Location/radius and
stale-data enforcement are planned in Pilot Task 5. Until that lands, the
presence of a location or capture date does not prove that the server filtered
by radius or rejected old data.

### Meal-plan totals

The v1 payload currently carries one `total_nzd`. Pilot Task 4 will define and
verify the authoritative full-pack amount payable after aggregating repeated
ingredient use. If exposing both consumption and payable totals requires new
optional fields, that is an additive v1 change. The frontend must use the
server-verified payable total for budget messaging and must not recompute with
floating-point arithmetic.

### Missing meal-plan constraints

Budget, household size, and duration are required to produce a meaningful
plan. The target behavior is a contract-valid clarification when one is
missing. The exact additive v1 event/message representation will be agreed with
the frontend before Pilot Task 4 changes the executable schema.

### Money is sent as a string

`"price_nzd": "3.49"` — a **string**, not a float. Parse with a decimal library,
never `parseFloat`, or you'll get `$23.159999999998` in a shopping list.
JS: use `Intl.NumberFormat` for display and a decimal lib for arithmetic.

---

## Error codes

| Code | Retryable | Meaning |
|---|---|---|
| `INVALID_REQUEST` | ❌ | Malformed — a bug on one of our sides |
| `NO_DATA` | ❌ | Not an error path; see `no_data` event instead |
| `STALE_DATA` | ⬜ | Data too old to be trustworthy |
| `BUDGET_INFEASIBLE` | ❌ | Budget genuinely can't be met. `message` suggests alternatives — **render it, don't swallow it** |
| `UNSUPPORTED_EXCLUSION` | ❌ | A stated dietary term we can't safely honour against our current catalogue (e.g. `gluten-free` while we still lack per-product allergen tagging). `message` names the terms we can honour — render it, don't swallow it |
| `PLAN_GENERATION_FAILED` | ✅ | We couldn't build a plan we trust — repair ran out of attempts on invalid drafts. **Not** a budget problem; the budget may be ample. Retry with the same `turn_id` |
| `GUARDRAIL_BLOCKED` | ❌ | Request refused on safety grounds |
| `OUT_OF_SCOPE` | ❌ | Not a grocery/meal question |
| `UPSTREAM_TIMEOUT` | ✅ | Retry with the **same** `turn_id` |
| `RATE_LIMITED` | ✅ | Back off and retry |
| `INTERNAL_ERROR` | ✅ | Retry once with the same `turn_id`. Arrives at HTTP `200` when handled, `500` when it escaped the handlers — identical body either way |

Do not collapse `PLAN_GENERATION_FAILED` into `BUDGET_INFEASIBLE` in your UI.
They look adjacent and are not: the first is our failure and worth retrying,
the second is a true statement about the user's budget that retrying cannot
change. Telling someone to raise a budget that was never the problem is the
specific defect this code exists to prevent.

`BUDGET_INFEASIBLE` and `UNSUPPORTED_EXCLUSION` both matter. The first is the
honest answer when you can't feed five people for $15; the second is the
honest answer when someone asks for gluten-free and we cannot guarantee it.
Please don't render either as a generic failure — the `message` field is
where the actionable alternatives are.

---

## Latency expectations and pilot targets

The figures below are targets until Pilot Task 12 measures a deployed service;
they are not current SLO evidence.

| Turn type | Pilot target | Client treatment |
|---|---|---|
| `price_check` | p95 < 5s | Spinner |
| `meal_plan` | p95 < 20s; p99 < ~25s escalation trigger | Progress affordance; show safe partial events when streaming exists |

The meal-plan path runs a validate-and-repair loop: the service generates a
plan, verifies arithmetic in code, and regenerates within a fixed bound. The
current REST response is returned as one event list; event-at-a-time WebSocket
delivery is later roadmap work.

**REST phase caveat:** the architecture retains a 29-second synchronous design
ceiling. A large meal plan may hit `UPSTREAM_TIMEOUT`; clients should retry
with the same `turn_id`. Increasing gateway timeout is not the first remedy:
measure and apply the documented model/plan-size/prefilter/prose mitigations
before considering a quota trade-off or the AgentCore contingency.

---

## Files

| File | Purpose |
|---|---|
| `src/schemas/contract.py` | Pydantic v2 models — **source of truth** |
| `samples/request_*.json` | Example requests |
| `samples/response_*.json` | Example responses, incl. failure cases |
| `validate.py` | CI check: schema, citation declaration/order/basic source shape, arithmetic; not independent retrieved-record equality |

Build against the samples. They're validated in CI, so if the samples and the
implementation ever diverge, the build breaks.

## Versioning

`version` is in every request and response. Breaking changes bump to `2.0` and
we'll run both for a transition period. Additive changes (new optional field,
new event type) stay `1.x` — **your client must ignore unknown event types
rather than throwing.**

---

## Open questions for the frontend team

1. Do you want `token` events for the meal-plan explanation, or only the
   structured `meal_plan` payload? Streaming prose is nicer but more work.
2. How do you want `location` handled when permission is denied — omit, or
   fall back to a user-typed suburb?
3. Any UI need for conversation history replay, or is `session_id` continuity
   enough?
4. Preferred `session_id` / `turn_id` format — UUIDv4 is fine, just confirm.
