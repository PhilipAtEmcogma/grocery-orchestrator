> **One change is already queued for whenever this conversation happens.**
> Both `POST /chat` endpoints are public and unauthenticated today. The owner
> decided on 2026-08-31 that an API key lands in the SAME change that repoints
> the frontend at the CDK plane — so the URL change and a new required
> `x-api-key` header arrive together rather than breaking the client twice.
> A request without the header gets API Gateway's own 403, not the
> contract-valid `ChatResponse` this service guarantees on every other path,
> which is the part worth raising with whoever owns the client.
> `docs/OPEN-REVIEW-api-key.md`.

# Open review — the frontend team's contract does not match ours

## 0. MERGED 2026-08-31 — which makes this review urgent rather than open

**The owner merged `frontend-infra-setup` into `main` on 2026-08-31**, after
being shown what it contains and what merging it would cost. So the situation
this document was written to warn about is no longer hypothetical: **two
contract documents now stand in `main`**, and one of them returns HTTP 400 if
implemented.

That is a deliberate decision, not an accident, and it is the right kind of
deliberate — the frontend work is real, it had waited ten days, and holding it
hostage to a document would have been the worse trade. But it converts this
review from "something to settle before merging" into "something to settle
now", and the four items in §0.3 are the settling.

`docs/API-CONTRACT.md` carries a banner naming `CONTRACT-v1.md` as
authoritative. That is the minimum that stops a reader implementing the wrong
one; it is not the fix. The fix is one document.

### 0.1 What was on the branch — inspected before merging

Four commits, last one 2026-08-21, ~130 commits behind `main` when merged.
Inspected rather than assumed, because everything below turns on what it
contains:

| | |
|---|---|
| `frontend/` | A working Vite/React client. `chatClient.js` POSTs JSON and returns `{status, body}`; `App.jsx` renders `<pre>{JSON.stringify(response)}</pre>` |
| `docs/API-CONTRACT.md` | 180 lines. A SECOND contract document |
| 5 files | `REPOSITORY_AUDIT.md`, `docs/DEPLOYMENT-RUNBOOK.md`, `docs/IAM-MATRIX.md`, `docs/LOCAL-DEVELOPMENT.md`, `docs/OBSERVABILITY.md` — **all 0 bytes** |

**Their CLIENT works against our contract. Their DOCUMENT does not.** The client
sends `version`, `session_id`, `turn_id: crypto.randomUUID()` and `message` —
verified against `ChatRequest`, it validates. Their document describes flat
JSON with numeric prices, `location` as a required string and no `turn_id`,
which would return HTTP 400. **The client works because it does not follow its
own document.**

It also does not CONSUME our contract: it dumps the raw JSON into a `<pre>`
rather than handling the event list. So "the frontend is built against our
contract" would be too generous in both directions — the transport works, the
rendering is a placeholder, and the written contract disagrees.

### 0.2 What merging it cost, recorded rather than smoothed over

The merge was clean — one conflict, in `.gitignore`, resolved by keeping both
rules — and the full gate passes on the merged tree: 861 tests, the secret scan
over `frontend/package-lock.json` (the obvious risk, and it came back clean),
ruff, pyright, `validate.py`. **Nothing in the backend depends on the frontend
and no file overlaps**, which is why there was nothing to reconcile in code.

What it did cost:

- **Two contract documents in `main`.** `docs/API-CONTRACT.md` beside
  `CONTRACT-v1.md`. Banner added; document unchanged otherwise.
- **Five 0-byte files** now tracked: `REPOSITORY_AUDIT.md`,
  `docs/DEPLOYMENT-RUNBOOK.md`, `docs/IAM-MATRIX.md`,
  `docs/LOCAL-DEVELOPMENT.md`, `docs/OBSERVABILITY.md`. Left as-is by decision.
  Worth naming that `docs/OBSERVABILITY.md` at 0 bytes is worse than absent —
  somebody looking for observability documentation will find it and find
  nothing, while `docs/ARCHITECTURE.md` §3q holds the real material.

### 0.3 What to settle now

1. **One contract, not two.** Either `docs/API-CONTRACT.md` is deleted in
   favour of `CONTRACT-v1.md`, or it becomes a frontend-facing summary that
   cites rather than restates. Two documents that disagree is the failure mode.
2. **The five empty files** — delete or fill.
3. **Which URL the client wires to**, which is the cutover question
   (`docs/ARCHITECTURE.md` §3m).
4. **The API key**, which lands in that same change (see the note above).

---



**Open, and wants the frontend teammate.** Fifteen minutes, no code reading.

**What this is.** The branch `frontend-infra-setup` carries a document,
`docs/API-CONTRACT.md`, in which the frontend side wrote down the API it
expects. It disagrees with `CONTRACT-v1.md` on nearly every field. This is the
reconciliation nobody had done, written from that branch rather than from
guesswork about what they believe.

**Nothing here is a criticism of that work.** The branch was cut on 2026-08-19
from a commit two weeks before the contract settled, and their document is
explicit that it is provisional — its own §12 lists six things it says need
confirming. This answers those six.

---

## 1. Two things will break, and one of them is their own example

Both verified by posting the exact shapes through `lambda_handler`, not by
reading the schema.

### `location` as a string returns HTTP 400

Their §5 documents `location` as a **required string**:

```json
{ "message": "...", "session_id": "demo-session-001", "location": "Auckland" }
```

Our `Location` is an object. A string fails validation before the turn starts:

```
their App.jsx shape (no location)      HTTP 200  ok, 11 events
their DOC shape (location string)      HTTP 400  ERROR INVALID_REQUEST
```

**Their shipped code is fine** — `App.jsx` sends only `version`, `session_id`,
`turn_id` and `message`, which works and returns 11 events. The break arrives
the day somebody implements §5 of their own document.

### `"Auckland"` is not a region even in the right shape

Sent correctly as `{"region": "Auckland"}` it validates, and then fails
differently:

```
location as named region               HTTP 200  ERROR INVALID_REQUEST:
                                       I don't have stores mapped for Auckland.
```

Auckland as a whole is not a shopping region; `config/regions.json` maps five
sub-regions, because "the shops near me" is a set of store locations rather
than a circle drawn round a city:

| region | aliases you can send |
|---|---|
| `north shore` | north shore, albany, devonport, takapuna |
| `central auckland` | central auckland, city centre, newmarket, mt albert, ponsonby, grey lynn |
| `west auckland` | west auckland, lincoln road, new lynn, henderson |
| `east auckland` | east auckland, sylvia park, remuera, mt wellington, botany |
| `south auckland` | south auckland, manukau, papakura, mangere, otara |

Omitting `location` entirely means national, which is why their client works
today.

---

## 2. Where the two documents disagree

| | Their `docs/API-CONTRACT.md` | Our `CONTRACT-v1.md` |
|---|---|---|
| Response shape | One flat object with a `type` | **Ordered list of typed events** under `events` |
| Prices | `"price": 3.49` — a number | `"price_nzd": "2.97"` — a **string**, always |
| Prose | A `message` field on the object | `token` events, and **money in them is a bug** |
| Citations | `{source, date}` inline on each item | Top-level `citation` events with `ref`; items carry `citation_ref` |
| `turn_id` | absent | **required**, 8–64 chars, drives idempotency |
| `location` | required string | optional object — `lat`/`lon`, or `region` |
| Totals | `grand_total`, `remaining_budget` | `total_nzd` **and** `payable_total_nzd` — different questions |
| Errors | `{"error": "...", "message": "..."}`, HTTP 500 | an `error` event **inside a valid event list**, with a code and `retryable` |
| Availability | `"availability": "in_stock"` | not carried — we hold prices, not stock levels |

**Prices as strings is the one most likely to be dismissed as pedantry.** It is
not. `3.49` as a JSON number is a float on the way in and out, and this project
has already shipped a plan that reported fitting a $60 budget with a $65.01
shopping list. Money is `Decimal` in Python and a string on the wire, end to
end. Parse it with a decimal library, not `Number()`.

**Two totals is the second.** `total_nzd` is the value consumed at fractional
pack multipliers; `payable_total_nzd` is what the shopper hands over — whole
packs at shelf price. `within_budget` follows the second. Render the second, or
you will show a shopper a total they cannot spend to.

A real `price_comparison` event, for reference:

```json
{ "seq": 9, "type": "price_comparison",
  "data": { "query_item": "butter-500g",
            "options": [ { "citation_ref": "c1", "is_cheapest": true,
                           "savings_vs_dearest_nzd": "1.15" } ],
            "reasoning": "Paknsave Mangere is cheapest for Pams Butter 500g (on special)." } }
```

The prices live on the `citation` events that `citation_ref` points at. That
indirection is the grounding guarantee showing through the wire format: an item
cannot state a price without naming a record we retrieved.

---

## 3. Their six pending questions, answered

From their §12. Three were already answered in `CONTRACT-v1.md`; all six are
answered here.

1. **"Confirm the exact Lambda request format."** `version`, `session_id`
   (8–64), `turn_id` (8–64, unique per turn), `message` (1–2000), optional
   `location`, optional `hints`. `extra="forbid"` — an unknown field is a 400.
2. **"Confirm the exact Lambda response format."** `{version, session_id,
   turn_id, events[]}`. Nine event types. **Ignore unknown event types** — that
   is the versioning rule, and it is what lets us add events without a v2.
3. **"Confirm whether `location` is free text or a fixed list."** Neither, quite:
   an object taking `lat`/`lon` **or** `region`, where `region` is matched
   case-insensitively against the alias table in §1. Free text in the *message*
   works too — "cheapest milk near Albany" resolves.
4. **"Confirm whether prices are numeric values or formatted strings."**
   **Strings**, without a currency symbol — `"2.97"`. See §2.
5. **"Confirm the final API Gateway URL."** `POST /dev/chat` on
   `https://woqmel35lk.execute-api.ap-southeast-2.amazonaws.com`. **Treat it as
   provisional**: a second, CDK-managed plane exists under a `-cdk` suffix and
   the cutover is deliberately deferred *until a frontend exists to coordinate
   the URL change with* (`docs/ARCHITECTURE.md` §3m). Tell us which URL you wire
   to and that decision can finally be made — it is currently waiting on you.
6. **"Confirm authentication requirements."** None today; the endpoint is
   anonymous by design for the pilot. An API key on the existing usage plan is
   the likely first control. Nothing for you to implement yet.

Their `VITE_API_URL` already defaults to `http://localhost:8000/chat`, which is
exactly what `python scripts/dev_server.py` serves — so the whole event contract
can be developed against offline, with no AWS account and no credentials.

---

## 4. What we are not doing

**We are not adopting their frontend.** `AGENTS.md` states teammates own the
frontend chatbot; the scaffold on that branch is theirs, and moving it into this
repository would take ownership of a component this repository has deliberately
not claimed.

**We are not merging their `docs/API-CONTRACT.md`.** It would put a second,
contradictory contract document in `docs/` — and "equivalent copies are the
dangerous kind" is a rule this repository has already paid to learn. There is
one contract, `CONTRACT-v1.md`, and `src/schemas/contract.py` is the source of
truth under it.

**Their `.gitignore` additions were not taken either.** The `.DS_Store` half is
already covered by ours, unanchored and with the reasoning attached. The
`frontend/*` half ignores paths that do not exist in `main`; it belongs in the
same change that lands a `frontend/` directory, not before it.

---

## 5. What would resolve this

One conversation, and then two edits that are not ours to make:

- Their `docs/API-CONTRACT.md` either points at `CONTRACT-v1.md` or is deleted.
  Two contracts is the failure mode, not either one of them.
- `location` in their client is sent as an object with a `region` from the alias
  table, or omitted. Not a bare string, and not `"Auckland"`.

And one that is ours: **question 5 above unblocks the CDK cutover.** It has been
waiting on a frontend to coordinate with since 2026-08-31, and there is now a
frontend, on a branch, that nobody told us about.

---

## 6. Provenance of this document

Written 2026-08-31 from `origin/frontend-infra-setup` at `9a09d87` — merged
into `main` the same day, see §0 — four
commits by a teammate dated 2026-08-19 to 2026-08-21, branched from `1e479c9`
and 120 commits behind `main`. Every behavioural claim above was produced by
posting the shape through `src/handler.py` and recording what came back.

The branch also carries five zero-byte files — `REPOSITORY_AUDIT.md`,
`docs/DEPLOYMENT-RUNBOOK.md`, `docs/IAM-MATRIX.md`, `docs/LOCAL-DEVELOPMENT.md`,
`docs/OBSERVABILITY.md` — which are placeholders, not content.
