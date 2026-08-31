# Frontend ↔ Orchestrator Contract v1.0

**Smart Grocery & Meal Budget Assistant**
Owner: Backend/Orchestration + AI/Prompt Lead
Status: **Published shape; frontend review and pilot-hardening gaps remain**
Region: `ap-southeast-2` (Sydney)

The v1 event shapes remain the compatibility baseline. Pilot Tasks 2–3
corrected citation construction, citation-before-use ordering, money-free comparison/prose labels, regenerated samples, and offline
GuardrailBlocked propagation. The remaining release blocker in this area is qualifying live Guardrail policy
evidence. Runtime literal-money enforcement and retrieved-record/value equality
both closed on 2026-08-29. Those changes stay
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
**Implemented 2026-08-29.** The fingerprint is taken over the validated
request, so whitespace, object-key order and omitted-versus-null cannot cause a
false mismatch — and neither can trailing zeros on money, since `30` and `30.00`
are the same budget. Every claim carries an owner token, rotated on acquire and
on takeover, and `complete()`/`release()` are conditional on it: an invocation
whose claim was taken over while it was working cannot overwrite the newer claim
with an older answer, nor delete it. Verified against the live table, not only
in memory.

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
| `notice` | Occasionally | Small inline note (data age, hint override, or a meal plan composed from products rather than named recipes) |
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
labels are money-free. Since 2026-08-29 final validation also compares every
citation against the immutable record retrieval returned — the ref must have
been retrieved, table/pk/sk must identify that exact record, and every published
value must equal the retrieved one — with wrong-key and altered-value negative
controls in CI.
Frontend code must resolve structured prices through `citation_ref` and never
parse prose for money.

**Please surface `valid_date` and `on_special` in the UI.** Location/radius and
stale-data enforcement are planned in Pilot Task 5. Until that lands, the
presence of a location or capture date does not prove that the server filtered
by radius or rejected old data.

### Meal-plan totals — two of them, and only one answers "can I afford this"

A meal plan now carries **both** figures, and they mean different things:

| Field | Meaning |
|---|---|
| `total_nzd` | Value **consumed** — line costs at fractional pack multipliers. Using 500g of a 1kg pack contributes half that pack's price. |
| `payable_total_nzd` | Money **payable** — every distinct pack counted once at full shelf price. Equals the sum of `baskets[].basket_total_nzd`. |

**Use `payable_total_nzd` for anything the user is told about cost**, and do
not recompute it with floating-point arithmetic. `within_budget` is computed
from it server-side.

They diverge because you cannot buy half a pack of butter. The gap is not
small: a plan reporting `total_nzd` of `$34.39` against a `$60` budget had a
shopping list costing `$65.01`. Until this was split, `within_budget` was
computed from consumption, so plans reported `within_budget: true` while their
baskets busted the budget by nearly 2x — including in
`samples/response_meal_plan.json`, the reference example. If you built against
that sample and rendered `total_nzd` as "your total", you were understating
what the shopper pays; switch to `payable_total_nzd`.

`total_nzd` is retained because it is the right number for "how much food value
does this plan use", which is what the per-meal subtotals add up to. It is the
wrong number for a budget.

### Missing meal-plan constraints

Budget, household size and duration are all required to produce a meaningful
plan. When any is missing the turn returns a **`clarification` event** instead
of a plan, and no error:

```json
{
  "seq": 2,
  "type": "clarification",
  "missing": ["days", "budget_nzd"],
  "message": "Happy to plan that — I just need to know how many days it needs to cover and what you'd like to spend. For example: \"dinner for 3 people for 5 days on $80\"."
}
```

`missing` names **`hints` fields exactly**, so you can raise the control that
collects the value — the budget slider, the household stepper — rather than
parsing the sentence. Send the next turn with those hints populated, or let the
user restate it in words; either satisfies it.

**It is not an error, and that is deliberate.** Nothing failed: the request was
valid and we understood it. An `ErrorEvent` would make `retryable` the only
signal, and a client reading `retryable: true` resends the identical request
and loops forever. A `notice` would be wrong too, since a notice accompanies a
result and this one replaces it.

Additive under the v1 rules — your client already ignores unknown event types,
so an older client sees a turn with no plan and no error, exactly what it saw
before this existed. `samples/response_clarification.json` is the worked
example.

**We do not guess.** `household_size` and `days` used to default silently to 1,
which meant an under-specified request got a confident plan for one person for
one day. That is a real answer to a question nobody asked. What we DO read is
what you actually said: "3 university flatmates" is a household of three, and
"tonight" is one day — a single meal is a stated duration, not a missing one.

### Location scope and price freshness

`location` accepts **either coordinates or a named region**:

```json
{ "location": { "region": "North Shore" } }
{ "location": { "lat": -36.98, "lon": 174.78, "radius_km": 5 } }
```

At least one is required — a `location` expressing no place is refused, because
accepting it would silently widen the request back to national. Omitting
`location` entirely is how you ask for national results. Region names and their
aliases live in `config/regions.json`; a region we cannot map is refused with
the list of those we can. A region may also simply be said — "cheapest butter
near Albany" — and is resolved from the message.

Coordinates narrow to stores within `radius_km`; omitting `location` returns
national results rather than a refusal. A location **never silently widens back
to national** — if nothing is in range you get `no_data` for that item, because
a shopper who asked for prices near them and received prices 500km away has been
answered confidently and uselessly.

Every citation carries `valid_date`, and prices older than the configured
threshold are excluded before any comparison is built. If some are fresh, the
comparison is made from those alone. If **every** price for the request is
stale, the turn returns `STALE_DATA` naming the newest capture date it found,
and it is **retryable** — ingestion resolves it.

That is a refusal rather than a disclaimer because the product's claim is not
"here is a price" but "here is the *cheapest* price", and a comparison drawn
from stale rows can be wrong in a way a stale price alone is not: the winner
changes when a special rotates.

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

**Status: proposed defaults, awaiting confirmation.** Each question below now
carries a default the orchestrator adopts if we have not heard otherwise by
**Friday 2026-09-11**. Nothing here is decided — a default is what happens on
silence, not an answer we received. Confirm or override; both are cheap now and
expensive once the pilot deploys.

Two of the four are cheaper than they read, because the implementation already
answers them. One has a real cost and a schema gap behind it.

### 1. `token` events, or only the structured payload?

**Already emitted.** `generate_prose` splits the explanation into sentences and
emits one `TokenEvent` each; four of the nine files in `samples/` contain them.
Over REST they arrive pre-joined, in the same response as everything else.

**Proposed default:** we keep emitting them, you render the structured
`meal_plan` payload, and `token` events stay ignorable until the WebSocket
upgrade makes them arrive one at a time. The versioning rule already requires
your client to ignore unknown event types, so this costs you nothing today.

**Override if** you want the prose rendered now. It is the same text either way;
only the delivery timing changes later.

### 2. `location` when permission is denied — omit, or fall back to a suburb?

**The one worth a conversation**, and the only question with a defect behind it:
`Location` currently requires `lat` and `lon`, so **the contract cannot express a
user-typed suburb at all**. `label` is optional and decorative. The fallback
option this question offers is not implementable as the schema stands.

It is also the primary input to Pilot Task 5 (location, store scope and
freshness), which cannot be designed against a shape nobody has agreed.

**RESOLVED 2026-08-29, on the proposed default.** Shipped, because the
teammate's demo scenarios ask for a named place in four cases out of five and
the answer had not arrived. `lat`/`lon` are optional under a validator requiring
either coordinates or a `region`; omitting `location` entirely still means
national. Everything previously valid stays valid.

The fallback resolves to a set of STORE LOCATIONS rather than a synthesised
coordinate, which is both truer to what "North Shore" means and the only option
that works — the 3,000-record dataset carries no `lat`/`lon` at all.

**Override if** you would rather send a synthesised centroid; say so and this
becomes a client-side concern instead.

**Override if** suburb fallback matters during the pilot rather than after it.
Tell us early — this is the one answer that changes an executable schema rather
than a document.

### 3. Conversation history replay, or `session_id` continuity?

**Proposed default: continuity only.** Replay is not a toggle on our side. It
pulls in conversation memory, which is deferred, and beyond that AgentCore
Memory, which is gated behind Cognito, consent, TTL, deletion/export and a
privacy review. "Yes" is a substantially larger commitment than the question
makes it sound, which is why we are proposing an answer rather than asking
open-endedly.

**Override if** the UI genuinely cannot work without it. It then becomes a scoped
requirement carrying those gates, not a contract field.

### 4. `session_id` / `turn_id` format

**Proposed default: no format constraint. Do not adopt a UUIDv4 requirement.**

Both fields are validated as 8–64 characters and nothing else. Every example in
this document, all nine files in `samples/`, and the dev-server command in the
README use `sess-7f3a9c21` / `turn-0001-a4b8` — **none of which is a UUIDv4**.
Confirming "UUIDv4 is fine" and then enforcing it would invalidate our own
published samples and every example a frontend developer has already copied.

UUIDv4 is a good thing for you to *generate*. It is a bad thing for us to
*require*. Idempotency needs uniqueness per turn and stability per session; the
format is not the contract's business.

**Override if** you want malformed ids rejected at the boundary. Name the shape,
and it lands as a v2 change rather than a v1 tightening.

### Resolving these

Answers are recorded here with their date, replacing the proposal. Question 2
additionally needs its schema gap closed whichever way it goes — the current
`Location` can express only one of the two options it offers.

### They have questions of their own, and a different contract

**Found 2026-08-31 on the branch `frontend-infra-setup`, which was merged into
`main` the same day — so `docs/API-CONTRACT.md` is now in the repository, with a
banner pointing here.** THIS document is what the service implements. The
frontend side
wrote their own `docs/API-CONTRACT.md`, provisionally, from a commit predating
this document. It assumes a flat object rather than an event list, numeric
prices rather than strings, no `turn_id`, and `location` as a required string —
and its own §12 lists six things it says need confirming.

All six are answered, and the two shapes that actually break are named and
verified, in
[`docs/OPEN-REVIEW-frontend-contract.md`](docs/OPEN-REVIEW-frontend-contract.md).
Their shipped client works against this contract today; their *document* does
not. **Read that before answering the four questions above** — the two sets are
about the same interface from opposite ends, and answering ours without
reconciling theirs would leave two contracts standing.
