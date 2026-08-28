# Frontend Integration Guide

**Smart Grocery & Meal Budget Assistant** — how to point a chatbot UI at the orchestrator.

Contract version **1.0**. This guide is the practical companion to
[`CONTRACT-v1.md`](CONTRACT-v1.md), which remains the specification. If the two
ever disagree, [`src/schemas/contract.py`](src/schemas/contract.py) is the source
of truth — it's Pydantic, and CI validates every sample against it.

Everything below was captured from the dev server on 2026-08-10 and documents
current reference behavior, including release-blocking defects: citation
`table` is a logical label, `pk` uses category instead of location, `sk` may
not be the normalized base key, and comparison/prose text contains literal
money. The examples remain unchanged until Pilot Task 2 fixes code and
regenerates samples atomically. Do not treat those fields as the target
contract; the corrected target is in
[`CONTRACT-v1.md`](CONTRACT-v1.md).

---

## 1. Run it locally — about two minutes

You do **not** need an AWS account, credentials, or a Bedrock key. The dev server
runs the same handler the Lambda runs, backed by fixture data and a scripted
model.

```bash
git clone https://github.com/PhilipAtEmcogma/grocery-orchestrator.git
cd grocery-orchestrator
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python scripts/dev_server.py
```

You should see:

```
Smart Grocery orchestrator — dev server on http://localhost:8000
  POST /chat     contract v1.0
  GET  /health
```

Two endpoints:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Is it up? Returns `{"status":"ok","contract_version":"1.0"}` |
| `POST` | `/chat` | The real thing |

CORS is wide open (`Access-Control-Allow-Origin: *`) and `OPTIONS` preflight is
handled, so a browser app on `localhost:3000` can call it directly.

### A request that works

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "version": "1.0",
    "session_id": "sess-local01",
    "turn_id": "turn-demo001",
    "message": "cheapest butter",
    "hints": { "preferred_stores": ["paknsave"] }
  }'
```

`session_id` and `turn_id` are **client-generated**, 8–64 chars, UUIDv4 is fine.
`turn_id` must be unique per turn. On retry, resend the same validated request
with the same `turn_id`; the target service replays the completed answer rather
than starting a second generation. Pilot Task 6 still has to add canonical
validated-request hashing and stale-owner fencing before that exactly-once
property is production-ready.

### The response you get back

```json
{
  "version": "1.0",
  "session_id": "sess-local01",
  "turn_id": "turn-demo001",
  "events": [
    { "seq": 0, "type": "session", "session_id": "sess-local01", "turn_id": "turn-demo001", "version": "1.0" },
    { "seq": 1, "type": "intent", "intent": "price_check", "confidence": 0.96 },
    { "seq": 2, "type": "citation", "citation": {
        "ref": "c1", "store": "paknsave", "store_location": "Mangere",
        "product_name": "Pams Butter 500g", "price_nzd": "2.97", "unit": "500g",
        "unit_price_nzd": "5.94", "on_special": true, "valid_date": "2026-07-31",
        "source": { "table": "Products", "pk": "paknsave#dairy", "sk": "butter-500g" } } },
    { "seq": 3, "type": "citation", "citation": {
        "ref": "c2", "store": "paknsave", "store_location": "Sylvia Park",
        "product_name": "Pams Butter 500g", "price_nzd": "2.97", "unit": "500g",
        "unit_price_nzd": "5.94", "on_special": true, "valid_date": "2026-07-31",
        "source": { "table": "Products", "pk": "paknsave#dairy", "sk": "butter-500g" } } },
    { "seq": 4, "type": "token", "text": "The cheapest option is $2.97 at Pak'nSave Mangere." },
    { "seq": 5, "type": "token", "text": " That is the best price across the stores near you." },
    { "seq": 6, "type": "price_comparison", "data": {
        "query_item": "butter-500g",
        "options": [
          { "citation_ref": "c1", "is_cheapest": true,  "savings_vs_dearest_nzd": "0.00" },
          { "citation_ref": "c2", "is_cheapest": false, "savings_vs_dearest_nzd": null }
        ],
        "reasoning": "Paknsave Mangere is cheapest at $2.97 for 500g." } },
    { "seq": 7, "type": "done", "server_time": "2026-08-10T05:44:49.113992Z",
      "usage": { "model_ids": [], "input_tokens": null, "output_tokens": null,
                 "latency_ms": null, "guardrail_intervened": false } }
  ]
}
```

Read that shape as a captured reference response, not as proof of the target
invariants. It contains two known defects scheduled for Pilot Task 2:

- Options correctly carry `citation_ref`, but `reasoning` and token text still
  contain literal money. Frontends must resolve structured prices from
  citations and must not parse prose for monetary truth.
- Citation source fields currently use the logical label `Products` and
  `<store>#<category>` rather than the exact configured physical table name,
  `<store>#<location-slug>` base PK, and normalized product SK. The target
  contract example in `CONTRACT-v1.md` is authoritative for exact provenance.

The intended streaming property remains that citations arrive before any
structured content event that references them. Pilot Task 2 broadens final
validation and regenerates these captured samples after implementation.

---

## 2. The three things people get wrong

### 2.1 Write an event handler, not a response parser

The tempting version — and the one that will cost you a rewrite in week 3:

```js
// DON'T
const comparison = response.events[6].data;   // breaks the moment seq shifts
const intent = response.events[1].intent;
```

Event positions are not stable. The number of citations varies with how many
stores stock the item, prose is split into a variable number of `token` events,
and a `notice` can appear anywhere in the middle. The response above happened to
put `price_comparison` at index 6; add one more store and it's at index 7.

Write this instead:

```js
function handleEvent(ev, ui) {
  switch (ev.type) {
    case "session":          ui.confirmIds(ev.session_id, ev.turn_id); break;
    case "intent":           ui.switchLayout(ev.intent); break;
    case "citation":         ui.citations.set(ev.citation.ref, ev.citation); break;
    case "token":            ui.appendText(ev.text); break;
    case "price_comparison": ui.addPriceCard(ev.data); break;
    case "meal_plan":        ui.renderPlan(ev.data); break;
    case "notice":           ui.addInlineNote(ev.message); break;
    case "no_data":          ui.addAssistantReply(ev.message); break;
    case "error":            ui.showError(ev.code, ev.message, ev.retryable); break;
    case "done":             ui.stopSpinner(); break;
    default:                 break;   // see 2.3
  }
}

// REST today:
for (const ev of response.events.sort((a, b) => a.seq - b.seq)) handleEvent(ev, ui);

// WebSocket in week 3 — same function, no other change:
socket.onmessage = (m) => handleEvent(JSON.parse(m.data), ui);
```

That's the entire reason the response is event-shaped. Week 1–2 ships over REST
and you get the whole `events` array at once; week 3–4 upgrades to WebSocket and
the *same events* arrive one at a time. If your rendering logic is a `switch` on
`type`, the transport swap costs you two lines.

Sort by `seq` before dispatching. Over WebSocket, `seq` also lets you detect a
dropped event — it's monotonic from 0 within a turn.

### 2.2 Money is a decimal string — never `parseFloat`

Every monetary field arrives as a **string**: `"price_nzd": "2.97"`, not `2.97`.

```js
// DON'T — this is how you end up with "$23.159999999998" in a shopping list
const total = items.reduce((sum, i) => sum + parseFloat(i.line_cost_nzd), 0);
```

Binary floating point cannot represent `0.10` or `2.97` exactly. Add up twenty
grocery line items and the error surfaces in the cents column, on the one number
in your UI that users will actually check.

Use a decimal library for arithmetic and `Intl.NumberFormat` for display:

```js
import Decimal from "decimal.js";   // or dinero.js, or big.js

const total = items.reduce((sum, i) => sum.plus(new Decimal(i.line_cost_nzd)),
                           new Decimal(0));

const nzd = new Intl.NumberFormat("en-NZ", { style: "currency", currency: "NZD" });
nzd.format(total.toNumber());   // "$23.16"
```

Keep the string form all the way from JSON to the decimal constructor. The moment
a price passes through a `Number`, the precision is gone. That includes
`JSON.parse` reviver tricks and any ORM-ish layer that "helpfully" coerces types.

Fields affected: `price_nzd`, `unit_price_nzd`, `savings_vs_dearest_nzd`,
`line_cost_nzd`, `subtotal_nzd`, `basket_total_nzd`, `total_nzd`,
`payable_total_nzd`, `budget_nzd`.

### 2.2a Show `payable_total_nzd`, not `total_nzd`

A meal plan carries two totals and they are not interchangeable:

- **`payable_total_nzd`** — what the shopper hands over. Every pack counted
  once at full shelf price; equals the sum of the store baskets. **This is the
  one to render**, and the one `within_budget` is computed from.
- **`total_nzd`** — what the meals *consume*, at fractional pack multipliers.
  Smaller, because a recipe using 500g of a 1kg pack counts half a pack.

The difference is large. A plan whose `total_nzd` is `$34.39` against a `$60`
budget has a shopping list costing `$65.01`, because half a pack of butter
cannot be bought. `total_nzd` is the right number for "how much food value
this plan uses" and the wrong number for "can I afford it".

If you already shipped against `total_nzd` as the headline figure, that is the
field to change — it was understating the bill, and until recently
`within_budget` agreed with it, so plans that busted the budget reported
`within_budget: true`. `samples/response_meal_plan.json` showed exactly that
and has been corrected.

### 2.3 Ignore unknown event types — don't throw

That `default: break;` in the switch above is load-bearing.

Adding a new event type is an **additive** change under our versioning rules, so
it ships as `1.x` without warning and without a major version bump. A client that
throws on an unrecognised `type` will break on a release that was contractually
safe.

```js
default:
  console.debug("ignoring unknown event type:", ev.type);
  break;      // NOT: throw new Error(...)
```

The same applies to unknown *fields* on a known event. If a `citation` gains a
`promo_ends_date` next month, your renderer should keep working. Don't validate
with a strict schema that rejects extra keys.

---

## 3. What changed since CONTRACT-v1.md was written

Two changes. **Both are additive — the version stays 1.x** and nothing you've
already built becomes invalid. But both change what your UI has to be able to
draw, and both are easy to miss if you only read the original samples.

### 3.1 A response can contain more than one `price_comparison`

`CONTRACT-v1.md` and `samples/response_price_check.json` both show exactly one
`price_comparison` event, and it's natural to read that as "one per turn". It
isn't. The orchestrator resolves **every item the user asked about**, up to five,
and emits **one `price_comparison` event per item**.

Real output for `"price of butter and milk and bread"`:

```
seq  type              query_item
 0   session
 1   intent            price_check
 2   citation          Pams Butter 500g
 …   citation          … (15 citations across the three items)
16   citation          White Bread, 700g
17   token             The cheapest option is $2.97 at Pak'nSave Mangere.
18   token             That is the best price across the stores near you.
19   price_comparison  butter-500g
20   price_comparison  milk-2l
21   price_comparison  bread-white-700g
22   done
```

**What your UI must do:** render **N comparison cards, not one.** If you have a
single `priceComparison` slot in your state, a three-item question will overwrite
it twice and show only bread. Append to a list.

Two details from that capture worth knowing:

- `query_item` is an internal product key (`"bread-white-700g"`), not display
  text. Use it as a React key or card id. For a heading, use the `product_name`
  from any of the card's citations.
- `savings_vs_dearest_nzd` is only populated on the option where
  `is_cheapest: true`. It's `null` on the others — don't render "save $null".

That whole response is committed as
[`samples/response_multi_comparison.json`](samples/response_multi_comparison.json)
— load it as a fixture and check you get three cards.

### 3.2 `no_data` and `notice` can appear *alongside* results, not only instead of them

The original contract table describes `no_data` as "when we have no data", and
`samples/response_no_data.json` shows it as the only content event in the turn.
That's still one valid case — but it's no longer the only one.

If a user asks about three items and we can only price two, we answer the two and
say so about the third, in the same turn. Real output for
`"price of butter and wagyu ribeye"`:

```
seq  type              detail
 0   session
 1   intent            price_check
 2   citation          Pams Butter 500g
 …   citation          … (5 butter citations)
 7   no_data           I don't have price data for wagyu ribeye.
 8   token             The cheapest option is $2.97 at Pak'nSave Mangere.
 9   token             That is the best price across the stores near you.
10   price_comparison  butter-500g
11   done
```

`notice` behaves the same way. Ask about seven items and you get five comparisons
plus:

```
27   notice            I can look up 5 items at a time, so I didn't check rice
                       and pasta. Ask me again for those.
```

**What your UI must do:** handle a **partial answer**. Specifically:

- Don't treat `no_data` as terminal. It is not "the turn failed" — more content
  is coming behind it. Keep the spinner running until `done`.
- Don't treat `no_data` as an error. No red, no warning triangle. It renders as
  ordinary assistant text: *"I don't have price data for wagyu ribeye."*
- Render `notice` as a small inline note near the results, not as a toast that
  disappears. It's explaining a gap the user can see in the results.
- A turn can contain **both** result cards and gap messages. Your layout needs
  room for both at once.

That response is committed as
[`samples/response_partial.json`](samples/response_partial.json).

This matters more than it looks. Silently dropping the `no_data` for wagyu means
a user who asked about two things gets an answer about one and no indication the
other was ever heard — which reads as the app ignoring them.

---

## 4. Every event type, and what to do with it

| `type` | When it appears | What your UI does |
|---|---|---|
| `session` | Always first, `seq: 0` | Check `session_id`/`turn_id` match what you sent. Discard the response if not — it's a stale reply to an earlier turn. |
| `intent` | Early, before content | Switch layout now: price card, meal-plan card, or plain chat. `intent` is one of `price_check`, `meal_plan`, `general_chat`, `out_of_scope`. |
| `citation` | Before anything referencing it | Store in a `Map` keyed by `citation.ref`. Never render on its own. **Surface `valid_date` and `on_special`** — stale prices are our biggest trust risk. |
| `token` | Streaming prose | Append `ev.text` to the message bubble. Already includes its own leading space where needed — don't add one. |
| `price_comparison` | `price_check` turns | Render a comparison table. **Possibly several per turn — append, don't replace.** Resolve each `citation_ref` against your citation map for the prices. |
| `meal_plan` | `meal_plan` turns | Render meals plus the per-store shopping list. All prices via `citation_ref`. `repair_attempts` is observability — don't show it. |
| `notice` | Occasionally, mid-turn | Small inline note: data age, an overridden hint, items we didn't check. Non-fatal, non-blocking. |
| `no_data` | We have no data for an item | Render as a **normal assistant reply, not an error**. May appear alongside results (§3.2), and more than once. |
| `error` | On failure | Show `ev.message` — it's already written to be user-safe. Offer retry if `ev.retryable`. |
| `done` | Always last | Stop the spinner. **Emitted even after an `error`** — so `done` is the only reliable "turn finished" signal. |

Two behaviours that surprise people:

- **A turn can have no content at all.** `"who won the rugby last night"` returns
  exactly three events: `session`, `intent` (`out_of_scope`), `done`. No `token`,
  no `error`. If your UI waits for text before clearing the spinner, it hangs.
  Wait for `done`.
- **The HTTP status is not the outcome.** You'll see `200`, `400` (malformed
  request or a reused `turn_id` with different content), `409` (that `turn_id`
  is still in flight — back off and retry), and `500` (a bug on our side). In
  every case the body is a valid `ChatResponse` with a `done` event. Parse the
  body; don't branch on the status code.

  **`500` is additive — if you already handle `400` and `409`, you need no
  change.** It carries the same body shape as everything else: a retryable
  `INTERNAL_ERROR` event followed by `done`. Treat it exactly as you treat an
  `INTERNAL_ERROR` at `200` — retry once with the same `turn_id`. The only
  thing to avoid is a transport layer that throws on 5xx before your parser
  sees the body: if your HTTP client does that by default (`fetch` does not,
  `axios` does), turn it off for this endpoint, or you'll lose a response
  that's telling you what happened.

  The reason it isn't just another `200`: at `200` a crash we didn't predict
  looks identical to an internal error we did, and nobody finds out without
  reading logs. The status distinguishes them for our alerting, not for your
  branching.

### Error codes

`ev.code` on an `error` event:

| Code | Retryable | Note |
|---|---|---|
| `INVALID_REQUEST` | ❌ | A bug on one of our sides |
| `STALE_DATA` | ⬜ | Data too old to trust |
| `BUDGET_INFEASIBLE` | ❌ | **Render the `message`** — it contains real alternatives ("raise the budget, reduce the days…"). Not a generic failure. |
| `PLAN_GENERATION_FAILED` | ✅ | Our side couldn't produce a valid plan. Offer a retry. Do **not** show budget advice — the budget may be fine. |
| `UNSUPPORTED_EXCLUSION` | ❌ | A stated dietary term we cannot safely honour (e.g. `gluten-free` while we still lack allergen tagging). Also **render the `message`** — it lists the terms we can honour, so the user has an actionable next step |
| `GUARDRAIL_BLOCKED` | ❌ | Refused on safety grounds |
| `OUT_OF_SCOPE` | ❌ | Not a grocery question |
| `UPSTREAM_TIMEOUT` | ✅ | Retry with the **same** `turn_id` |
| `RATE_LIMITED` | ✅ | Back off, then retry |
| `INTERNAL_ERROR` | ✅ | Retry once, same `turn_id`. Arrives at `200` when we handled the failure, `500` when it escaped — same body, same handling |

`NO_DATA` exists in the enum but you should never see it as an `error` — it comes
through as a `no_data` event instead.

---

## 5. Sample files

Every shape, as a file you can load into a fixture or a Storybook story. These
are validated in CI, so if they drift from the implementation the build breaks.

| File | Shape |
|---|---|
| [`samples/request_price_check.json`](samples/request_price_check.json) | A price-check request |
| [`samples/request_meal_plan.json`](samples/request_meal_plan.json) | A meal-plan request, with `location` and `hints` |
| [`samples/response_price_check.json`](samples/response_price_check.json) | Happy path: citations + one comparison |
| [`samples/response_meal_plan.json`](samples/response_meal_plan.json) | Happy path: full plan, meals, per-store baskets |
| [`samples/response_no_data.json`](samples/response_no_data.json) | **Failure case** — `no_data` as the whole answer |
| [`samples/response_budget_infeasible.json`](samples/response_budget_infeasible.json) | **Failure case** — `BUDGET_INFEASIBLE` error with alternatives in the message |
| [`samples/response_unsupported_exclusion.json`](samples/response_unsupported_exclusion.json) | **Failure case** — `UNSUPPORTED_EXCLUSION` for a dietary term we cannot honour (e.g. gluten-free) |
| [`samples/response_guardrail_blocked.json`](samples/response_guardrail_blocked.json) | **Failure case** — `GUARDRAIL_BLOCKED` |
| [`samples/response_multi_comparison.json`](samples/response_multi_comparison.json) | **§3.1** — three items, three `price_comparison` events, 15 citations |
| [`samples/response_partial.json`](samples/response_partial.json) | **§3.2** — a partial answer: `no_data` at `seq 7`, results at `seq 10` |

The last two are captured verbatim from the dev server for the queries in §3, so
they're the exact bytes your handler will see. Use them as the fixtures for your
N-cards and partial-answer rendering — those are the two cases most likely to be
missed, and they're now the two easiest to test against.

Also worth knowing: `samples/` shows the *contract*, but the dev server runs off
`fixtures/`, which has more stores and different prices. The shapes match; the
values won't. Don't write a test that asserts butter costs $3.49.

---

## Related

| File | What it's for |
|---|---|
| [`CONTRACT-v1.md`](CONTRACT-v1.md) | The specification, plus latency expectations and the WebSocket roadmap. Open questions for you are at the bottom — we'd like answers. |
| [`src/schemas/contract.py`](src/schemas/contract.py) | Pydantic models. Source of truth. |
| [`validate.py`](validate.py) | The CI check: schema, grounding, and arithmetic invariants |
| [`scripts/dev_server.py`](scripts/dev_server.py) | The local server |

Questions: raise a GitHub issue on this repo or ping the backend channel. If
something in here doesn't match what the server actually does, that's a bug in
this document and we want to hear about it.
