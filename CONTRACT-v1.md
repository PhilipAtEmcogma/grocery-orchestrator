# Frontend ↔ Orchestrator Contract v1.0

**Smart Grocery & Meal Budget Assistant**
Owner: Backend/Orchestration + AI/Prompt Lead
Status: **Draft for frontend team review** — please raise issues before build starts
Region: `ap-southeast-2` (Sydney)

---

## Why this shape

The response is a **list of events**, not a single object. This is deliberate:

- **Week 1–2** ships over `POST /chat` (API Gateway REST). The whole event list
  comes back at once in the `events` array.
- **Week 3–4** upgrades to WebSocket streaming. The *same events* arrive one at
  a time.

If you write your client as an **event handler** rather than a response parser,
the transport upgrade costs you almost nothing. Please do that.

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
| `turn_id` | ✅ | **Unique per turn**, client-generated. Used for idempotency — resend the same `turn_id` on network retry and you won't get a duplicate charge or duplicate answer. |
| `message` | ✅ | Raw user text, max 2000 chars. |
| `location` | ⬜ | Omit if the user hasn't granted permission. Price results will be national rather than local. |
| `hints` | ⬜ | Optional, from UI controls (sliders, chips, etc). |

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

**No monetary value appears inline in a payload without a `citation_ref`.**

Prices live only in `citation` events. Payloads reference them by `ref`
(`"c1"`, `"c2"`…). To display a price, look it up in your citation map.

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
    "source": { "table": "Products", "pk": "paknsave#dairy", "sk": "pams-butter-500g" }
} }
```

This exists so that *"never invent a price"* is mechanically enforceable rather
than a promise. A response containing a `citation_ref` with no matching
`citation` event is a **contract violation** and fails our CI. The `source`
field traces back to the exact DynamoDB record.

**Please surface `valid_date` and `on_special` in the UI.** Stale prices are our
biggest trust risk and users should be able to see how fresh the data is.

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
| `GUARDRAIL_BLOCKED` | ❌ | Request refused on safety grounds |
| `OUT_OF_SCOPE` | ❌ | Not a grocery/meal question |
| `UPSTREAM_TIMEOUT` | ✅ | Retry with the **same** `turn_id` |
| `RATE_LIMITED` | ✅ | Back off and retry |
| `INTERNAL_ERROR` | ✅ | Retry once with the same `turn_id`. Arrives at HTTP `200` when handled, `500` when it escaped the handlers — identical body either way |

`BUDGET_INFEASIBLE` and `UNSUPPORTED_EXCLUSION` both matter. The first is the
honest answer when you can't feed five people for $15; the second is the
honest answer when someone asks for gluten-free and we cannot guarantee it.
Please don't render either as a generic failure — the `message` field is
where the actionable alternatives are.

---

## Latency expectations

| Turn type | Expected p50 | Design for |
|---|---|---|
| `price_check` | ~4–6s | Spinner |
| `meal_plan` | ~18–25s | **Progress affordance** — show `intent` and `citation` events as they arrive so the user sees work happening |

The meal-plan path runs a validate-and-repair loop: we generate a plan, check
the arithmetic in code, and regenerate if it's over budget. `repair_attempts`
in the payload tells you how many cycles it took. This is why streaming matters.

**REST phase caveat:** API Gateway caps synchronous integrations at 29 seconds.
A large meal plan may occasionally hit this and return `UPSTREAM_TIMEOUT`.
Handle it gracefully — it's the reason we're moving to WebSocket in week 3.

---

## Files

| File | Purpose |
|---|---|
| `schemas/contract.py` | Pydantic v2 models — **source of truth** |
| `samples/request_*.json` | Example requests |
| `samples/response_*.json` | Example responses, incl. failure cases |
| `validate.py` | CI check: schema + grounding + arithmetic invariants |

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
