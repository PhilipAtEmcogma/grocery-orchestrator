# DynamoDB Schema and Migration Plan

**Smart Grocery & Meal Budget Assistant**
Author: Philip (Backend/Orchestration, AI/Prompt Lead)
Status: **Products/idempotency implemented and deployed; recipes shipped (Task 15);
price-history (Table 4) built in code but NOT deployed**
Region: `ap-southeast-2` (Sydney)

The current account contains `grocery-products-dev` and
`grocery-idempotency-dev`; the products table is seeded and both adapters have
been live-verified. Claim-owner idempotency hardening landed 2026-08-29 and was
verified against the live table. The candidate-query access pattern is RESOLVED
(GSI2, 2026-08-30); the pattern was deferred until there was load evidence to
choose from, and `tests/test_price_repository_contract.py` fails once the
dataset outgrows a defensible Scan.

**Table 4, the append-only price history (`grocery-price-history-dev`), is
BUILT IN CODE (`src/history/`, wired into ingestion) but NOT DEPLOYED** —
`aws dynamodb list-tables` (2026-09-02) shows only products and idempotency
(plus the data team's `smart-grocery-*`). Its section below is a specification
the ingestion write path already targets; the table itself awaits a deploy.
`grocery-meals-dev` remains a planned CDK definition (the curated recipes ship
today from `config`/`src/recipes`, not from a meals table).

Further resources must be defined in TypeScript CDK. Existing stateful tables
are adopted/imported rather than recreated. Manual console creation is no
longer the default path.

A reviewed architecture diagram labels the DynamoDB box "Products + Sessions".
There is no sessions table and none is planned for the pilot: that label is
`grocery-idempotency-dev`, which is already session-scoped with a TTL. A
genuine conversation-state store is a Privacy Act 2020 workstream first —
TTL, scoping, consent and deletion — per `.kiro/steering/security.md` line 25.
See `docs/ARCHITECTURE.md` §2.

Three table domains are retained: products, meals/recipes, and idempotency. A
materialized candidate view may be added by Pilot Task 6 if an index on the
products table cannot serve category/location/freshness queries without a
scan; that decision must be supported by access-pattern and load evidence.

---

## Why three tables and not one

Single-table design pays off when related entities are fetched *together* in
one query. Prices and meal data are queried at completely different moments by
different access patterns, so combining them would add key-design complexity
and save zero round trips.

The idempotency table is separate for a different reason again. It is
short-lived operational state, not domain data: one conditional write per turn,
one read on retry, everything expiring within a day. Putting it alongside
either of the others would mix a hot, tiny, high-churn access pattern into a
table sized and keyed for something else.

Within the meal table we *do* use single-table design, because recipes and
saved plans are both meal-domain entities and the `PK` prefix cleanly
separates them.

---

## Table 1 — `grocery-products-dev`

Grocery prices. Written by the scraper, read by the orchestrator.

### Keys

| | Partition key | Sort key |
|---|---|---|
| Base table | `store_key` — `paknsave#sylvia-park` | `product_key` — `butter-500g` |
| **GSI1** | `product_key` | `gsi1_sk` — `000000297#paknsave#sylvia-park` |

### The GSI is the important part

The primary user question is *"what's the cheapest X near me?"* — one product,
all stores. The base table partitions by **store**, so answering that from the
base table alone would need one query per store, or a scan.

GSI1 partitions by **product** and sorts by a **zero-padded price** embedded
in the sort key. DynamoDB sorts sort keys lexicographically, so
`000000297` < `000000391` < `000000412` — meaning the cheapest option is
literally the first item returned.

```python
table.query(
    IndexName="GSI1",
    KeyConditionExpression=Key("product_key").eq("butter-500g"),
    ScanIndexForward=True,
    Limit=5,
)
```

One query. Already sorted. No scan, no application-side sorting.

**Cost of this design:** a price change requires rewriting the GSI entry,
because the price is part of the sort key. With a daily full refresh this is
acceptable.

### Exact citation provenance

A public citation identifies the base-table record, not a category or GSI
position:

```text
table = grocery-products-dev   # exact configured physical table name
pk    = store_key              # e.g. paknsave#sylvia-park
sk    = product_key            # e.g. butter-500g
```

`category` is an attribute and must never be substituted into `pk`. GSI1 can
find the record, but the citation carries the configured physical table name
and base PK/SK. Pilot Task 2 corrected reference-node construction to those
keys, normalized product sort keys, regenerated samples, and added
citation-before-use/basic-source checks.

That is not yet independent exact-record proof. Current final validation has no
immutable retrieved-record context, so it cannot compare citation keys and
monetary values with the retrieved item or exercise the full wrong-key and
altered-value controls required by Req 3.5–3.6. The citation-construction defect
is closed; the retrieval-context equality follow-up remains open.

### Location, freshness and meal-candidate access patterns

GSI1 remains correct for a bounded price comparison: query by product, then
apply the request's explicit eligible store/location set before citations are
created. A location request must never silently widen to national results.

**RESOLVED 2026-08-30 (Pilot Task 6b): GSI2, option 1.**

`candidates_for_budget()` used to `Scan` the products table on every meal-plan
turn. It now issues one `Query` per wanted category against **GSI2**.

| Index | PK | SK |
|---|---|---|
| **GSI2** | `category` | `gsi2_sk` — `000000297#butter-500g#paknsave#sylvia-park` |

Zero-padded cents lead the sort key, so the cheapest rows are the first read and
the query stops early. The product key follows, because the caller wants
*distinct products* rather than one cheap product at ten stores, and it keeps
the key unique per item. `category` was already a base attribute, so — exactly
as with GSI1 — only the sort key had to be added.

The decision was deferred until there was evidence to make it on, and the
evidence points one way:

1. **The access pattern is the index.** `candidates_for_budget` asks for the
   *N* cheapest distinct products in each of about eight categories. That is
   partition-by-category, sort-by-price, written out.
2. **The load is now real.** The catalogue is 2,939 rows, not the 152 seeded
   ones. A Scan reads every row on every meal-plan turn to return roughly two
   dozen, and DynamoDB bills rows *read*.
3. **The data team reached the same shape independently.** Their
   `smart-grocery-products-dev` carries a `CategoryPriceIndex` on
   `category`/`price` — two teams, the same question, the same index.

Option 2 — a materialised candidate view — stays unbuilt and is now unnecessary
at this scale; revisit it only if partition cardinality on `category` becomes
the constraint, which eight categories over thousands of rows is far from.

Distance and freshness remain application-side filters: neither is expressible
as a key condition, so the query over-fetches and pages, the same way
`cheapest_for_product` does and for the same reason — filtering a single page
and returning the survivors is the truncation defect that reported `no_data`
for a stocked product.

**A sparse index is silent.** DynamoDB omits an item that lacks the sort-key
attribute, with no error anywhere, so a row written without `gsi2_sk` simply
never appears as a meal-plan candidate. Both `ingestion/normalise.py` and
`scripts/load_seed_data.py` write it, and a test asserts every fixture row
carries it.

`dynamodb:Scan` has been **removed** from the orchestrator role: no read path
uses it any more, and a permission nothing needs is one somebody can quietly
start using again.

Every record keeps `valid_date`; Pilot Task 5 defines the freshness threshold
and stale-only outcome.

### Proposed review-trigger path

Pilot Task 13 may use filtered DynamoDB Streams -> SQS/DLQ to decouple bounded
data-quality review from publication. The stream is a trigger, not a new price
source. Messages carry record identifiers and non-sensitive review metadata;
the reviewer receives a capped sanitised snapshot, writes a versioned S3 review
artefact, and has no table-write or publication permission. Retry, DLQ/redrive,
backlog, duplication, and disable/drain evidence are required before enabling
the mapping. The AgentCore Runtime consumer is proposed under ADR 0002 and
requires mentor approval; deterministic ingestion remains authoritative.

### Attributes

| Attribute | Type | Notes |
|---|---|---|
| `store_key` | S | PK |
| `product_key` | S | SK, GSI1 PK. Normalised — see below |
| `gsi1_sk` | S | `{price_cents:09d}#{chain}#{location}` |
| `store` | S | `paknsave` \| `woolworths` \| `new_world` |
| `store_location` | S | `Sylvia Park` |
| `lat`, `lon` | N | For distance filtering |
| `display_name` | S | As the retailer writes it |
| `canonical_name` | S | Normalised display form |
| `category` | S | `dairy`, `meat`, `produce`… |
| `price_nzd` | S | **String, not Number** — see below |
| `unit`, `unit_price_nzd` | S | `500g`, `6.98` |
| `pack_grams` | N | Needed to scale quantities |
| `on_special` | BOOL | |
| `valid_date` | S | ISO date. Surface this in the UI |

### Two attribute decisions worth knowing

**Money is stored as a String.** DynamoDB's Number type round-trips through
float in most SDK paths, which introduces the `$23.159999999998` class of bug.
Storing `"3.49"` and parsing to `Decimal` keeps money exact end to end — and
it matches the contract, which already sends money as a string on the wire.

**`product_key` normalisation is load-bearing.** Pak'nSave writes
"Pams Butter 500g", Woolworths "Butter, 500g", New World "Value BUTTER 500G".
All three must map to `butter-500g` or the comparison silently compares
nothing. This is the single most likely source of wrong answers in the system.
The scraper owns this mapping; it is not something to leave to fuzzy matching
at read time.

### Console settings that are NOT on by default

- **Point-in-time recovery** — off by default. Turn it on.
- **On-demand capacity** — no provisioning for student-scale traffic.
- Encryption at rest is on by default (AWS-owned key); that is sufficient here.

---

## Table 2 — `grocery-meals-dev`

Two entity types, separated by `PK` prefix.

### Entity A — Recipe

| | Value |
|---|---|
| PK | `RECIPE#<recipe_id>` |
| SK | `META` |

| Attribute | Type | Notes |
|---|---|---|
| `name` | S | "Beef Bolognese Pasta" |
| `serves_base` | N | Servings at base quantities |
| `method` | S | **Must be original text — see legal note** |
| `tags` | SS | `vegetarian`, `quick`, `one-pot`, `dinner` |
| `excludes` | SS | `seafood`, `dairy`, `gluten` — what it is safe for |
| `ingredients` | L | List of maps, see below |
| `prep_minutes` | N | |

Each ingredient map:
```json
{ "product_key": "beef-mince-1kg", "packs_per_serve": "0.167", "item": "Beef mince", "qty_display": "500g" }
```

Ingredients reference `product_key`, **not** prices. Prices come from the
products table at query time, which is what keeps a saved recipe from going
stale when prices move.

**GSI1 for recipe lookup by dietary tag:**

| | Partition key | Sort key |
|---|---|---|
| GSI1 | `tag` | `recipe_id` |

Written as one item per tag (`TAG#vegetarian` / `recipe_id`), so
"all vegetarian recipes" is one query rather than a scan with a filter. A
scan-and-filter works at 50 recipes and stops working at 5,000; the GSI costs
nothing to add now and avoids a rewrite later.

### Entity B — Saved plan

| | Value |
|---|---|
| PK | `PLAN#<session_id>` |
| SK | `<iso8601_created_at>#<turn_id>` |

| Attribute | Type | Notes |
|---|---|---|
| `plan_json` | S | The full `MealPlan` payload from the contract |
| `budget_nzd` | S | String, as above |
| `household_size`, `days` | N | |
| `total_nzd` | S | |
| `within_budget` | BOOL | |
| `created_at` | S | ISO timestamp |
| `ttl` | N | **Unix epoch seconds — see below** |

The ISO timestamp leading the sort key means "this session's plans, newest
first" is a single query with `ScanIndexForward=False`.

**TTL is a privacy control, not housekeeping.** Saved plans are tied to a
session and reveal household size, budget, and dietary restrictions — the last
of which can imply health information. Enable TTL on the `ttl` attribute with
a 30-day expiry so the data deletes itself rather than accumulating
indefinitely. Under the Privacy Act 2020, holding personal information longer
than needed is the thing to avoid, and an automatic expiry is far more
reliable than a cleanup job someone remembers to write.

---

## Table 3 — `grocery-idempotency-dev`

Turn deduplication. Written and read by the orchestrator only.

The contract promises that resending a `turn_id` returns the same answer
without re-running the work. The plan path runs close to the gateway's
29-second ceiling, so a client timeout followed by a retry is expected, not
exceptional — and without this table it means paying for generation twice and
possibly returning a different plan than the first attempt would have.

### Keys

| | Partition key |
|---|---|
| Base table | `pk` — `idem#<session_id>#<turn_id>` |

No sort key and no index. There is exactly one access pattern: fetch or claim
one known key. Anything more would be design for its own sake.

**Scoped by session, not by turn alone.** Clients generate `turn_id` and
nothing makes it globally unique — two sessions can produce the same value. An
unscoped key would eventually serve one user another user's shopping list,
which is a privacy failure created by a caching optimisation.

### Attributes

| Attribute | Type | Notes |
|---|---|---|
| `pk` | S | `idem#<session_id>#<turn_id>` |
| `payload_hash` | S | Truncated SHA-256 of the canonical validated `ChatRequest` |
| `claim_token` | S | Fresh opaque owner token for each successful acquire or stale takeover |
| `status` | S | `in_progress` \| `completed` |
| `response_json` | S | The full `ChatResponse`. Absent while in progress |
| `started_at` | N | Epoch seconds, when the current owner took the claim |
| `ttl` | N | **Unix epoch seconds — 24h.** Enable TTL on this attribute |

### Canonical request fingerprint

Fingerprint validated request content, not raw HTTP bytes. The handler first
validates a `ChatRequest`, serializes `model_dump(mode="json", exclude_none=True)`
as UTF-8 JSON with recursively sorted object keys and compact separators, then
hashes those bytes. List order is preserved. Defaults are included, while an
omitted optional field and the same field explicitly set to `null` are treated
as equivalent. Consequently whitespace and JSON object-key order cannot create
a false payload mismatch, and semantically different validated requests still
cannot share a turn identifier.

The canonicalization algorithm is part of the idempotency contract. Its test
vectors must be shared by the in-memory and DynamoDB implementations before
production traffic.

### `acquire` must be a conditional put with owner fencing

This is the part that is easy to get wrong and impossible to catch on one
machine.

The obvious implementation reads the key, sees nothing, and writes a marker.
Two Lambda invocations racing on the same key both read "absent", both write,
and both proceed to generate — which defeats the entire purpose of the table
while passing every single-threaded test you write against it.

The claim has to be **one atomic conditional write**: succeed only if no live
record exists or the prior in-progress claim is stale. Every successful acquire
or takeover generates a fresh opaque `claim_token`, stores it, and returns it
to the caller.

```python
claim_token = secrets.token_urlsafe(24)
table.put_item(
    Item={
        "pk": key,
        "payload_hash": payload_hash,
        "claim_token": claim_token,
        "status": "in_progress",
        "started_at": now,
        "ttl": now + 86400,
    },
    ConditionExpression=("attribute_not_exists(pk) OR (#s = :in_progress AND started_at < :stale)"),
    ExpressionAttributeNames={"#s": "status"},
    ExpressionAttributeValues={
        ":in_progress": "in_progress",
        ":stale": now - IN_PROGRESS_TIMEOUT_SECONDS,
    },
)
```

A `ConditionalCheckFailedException` is the normal path, not an error: it means
somebody else holds the key. Read the returned item to decide which outcome to
report. Ask for `ReturnValuesOnConditionCheckFailure` so the losing writer gets
the existing item without a second round trip.

**The second clause takes over an abandoned claim.** An invocation that crashed
mid-turn leaves an `in_progress` marker that nothing will ever complete.
Without the staleness condition the client is blocked for the full 24-hour TTL
on a turn that will never finish. The timeout is set longer than the gateway
ceiling — so a slow-but-alive request is not duplicated — and far shorter than
the TTL. A takeover always rotates `claim_token`; `started_at` alone is not an
ownership proof.

### The four outcomes

| Outcome | Condition | Handler response |
|---|---|---|
| Acquired | No live record, or the existing claim is stale | Return the new `claim_token`; run the turn |
| Completed | `status = completed` | Return `response_json` verbatim |
| In progress | `status = in_progress`, claim still fresh | `409`, retryable |
| Payload mismatch | Stored `payload_hash` differs | `400`, **not** retryable |

**Payload mismatch is a rejection, not a cache hit.** The same `turn_id` with
different validated content is a client bug. Returning the stored response
would answer a question the client did not ask, and would do it invisibly.

**Completion and release are owner-conditional.** `complete()` may update only
when `status = in_progress AND claim_token = <token returned by acquire>`;
`release()` may delete only under the same condition. A failed condition means
the caller lost ownership and must never overwrite or delete the newer claim.
This fences an old invocation that resumes after another invocation has taken
over its stale claim.

**Only terminal outcomes are written back.** A turn that failed in a retryable
way releases its own claim instead of completing it. Caching a transient
failure would make the client's retry permanently useless — it would receive
the same failure forever, from a mechanism built to help it recover.

### Point-in-time recovery, as at 2026-08-29

| Table | PITR | Reasoning |
|---|---|---|
| `grocery-products-dev` | ENABLED | Serving data |
| `smart-grocery-products-dev` | ENABLED | Upstream dataset, 3000 items |
| `smart-grocery-recipes-dev` | ENABLED | Upstream dataset, 175 items |
| `grocery-idempotency-dev` | **DISABLED, deliberately** | A 24-hour TTL cache holding no source of truth. Restoring it would replay stale claim tokens and cached responses over live ones, which is worse than starting empty. Enabling it would claim recoverability for state that should never be recovered. |

### Console settings that are NOT on by default

- **TTL on the `ttl` attribute** — off by default. Without it this table grows
  without bound, and it holds canonical request fingerprints and full response
  bodies including shopping lists.
- **On-demand capacity** — one small write and one read per turn.
- **Point-in-time recovery** — required for this table as well as products and
  future meal/session tables by Req 11.7. A short TTL limits retention; it does
  not waive the recovery control.
- Encryption at rest must be verified explicitly for every table.

---

## Table 4 — `grocery-price-history-dev`

Append-only price history. Written by ingestion alongside the products write,
read by ops and the data-quality reviewer. **Never read on the shopper path and
never a source of a citation** — see the invariant note below.

### Why it exists

The deterministic ingestion rules catch a row that is internally inconsistent
(`implausible_unit_price`: a unit price that disagrees with its own pack size).
They cannot catch a row that is internally consistent and simply wrong — $12.99
for a $1.29 item, with a matching unit price, passes every check. `docs/ARCHITECTURE.md`
§3p records that the largest class of anomaly the rules miss needs a **baseline**
— "this price doubled overnight" — and a baseline needs history. This table is
that history, and it is the input the ADR 0002 reviewer's enriched snapshot
draws on.

### Keys

| | Partition key | Sort key |
|---|---|---|
| Base table | `history_pk` — `paknsave#sylvia-park#butter-500g` | `valid_date` — `2026-07-31` |

`history_pk` collapses the products base key `(store_key, product_key)` into one
partition, so "this product's price over time at this store" is a single query.
The **capture date is the sort key**, which is what makes a refresh APPEND: a new
capture date is a new row; a same-day re-run overwrites an identical row, so a
repeated refresh cannot invent a second data point for a day that had one. A
finer (per-second) key would duplicate history on every same-day re-run — the
opposite of what a baseline wants.

A date range on the sort key is the window query:

```python
table.query(
    KeyConditionExpression=(
        Key("history_pk").eq("paknsave#sylvia-park#butter-500g")
        & Key("valid_date").between("2026-05-02", "2026-07-31")
    ),
)
```

### Append-only, and the average is computed at READ time

There is no update path and no delete path (a baseline you can rewrite is not a
baseline), and **no stored average**. `src/history.summarise` computes the mean,
min, max and latest over exactly the rows the window query returned, at read
time. Two reasons: it keeps the table genuinely append-only (a running average
would be a mutable field on an immutable row), and the average always reflects
the window the caller chose rather than one frozen at write time. The reviewer
gets a `deviation_ratio` (price ÷ windowed average) computed in code, so a model
can say "this is 10× its own history" without being handed the arithmetic to get
wrong.

### Attributes

| Attribute | Type | Notes |
|---|---|---|
| `history_pk` | S | PK — `{store_key}#{product_key}` |
| `valid_date` | S | SK, ISO date — the capture date |
| `store_key` | S | Carried so a row is self-describing without splitting the PK |
| `product_key` | S | |
| `price_nzd` | S | **String, not Number** — the money rule, as everywhere |
| `unit_price_nzd` | S | |
| `on_special` | BOOL | Whether that capture was a special — context for a deviation |

No `lat`/`lon`, no `display_name`, no `category`: a baseline needs who, what, how
much and when, and nothing more. Fewer fields is less to leak and less to keep in
step.

### The invariant note — never shopper-facing

An average over a window is **not a price anyone can pay at any actual store on
any actual day**. Surfacing it to a shopper would breach the grounding invariant
(a shopper-visible price must be a single retrieved fact at a named store with a
capture date). So `src/history` is imported by ingestion (write) and, later, the
reviewer (read) — and **never by `src/graph/` or `src/retrieval/`**. A test
asserts the shopper path does not import it. History informs ops and the
reviewer; it never informs an answer.

### Console settings that are NOT on by default

- **Point-in-time recovery** — ON. Unlike the idempotency cache, history IS a
  source of truth (for trend detection), so it is recoverable.
- **On-demand capacity** — one small write per product per refresh.
- **TTL** — a long retention rather than the idempotency table's 24 hours: a
  baseline is only as good as its window, and a 90-day deviation check needs 90
  days of history. A retention around 180–365 days, or none for the pilot's
  scale, with the value revisited when the row count justifies it. No shopper
  data means the Privacy-Act minimisation pressure that drives the idempotency
  and saved-plan TTLs does not apply here.
- Encryption at rest verified explicitly, as for every table.

---

## Legal note on the recipe catalogue

Worth raising before anyone starts populating the recipe catalogue.

Ingredient *lists* are generally treated as statements of fact and are not
protected by copyright. The **written method is** — it is creative expression,
and copying it from a recipe site into our database is infringement regardless
of attribution.

So: source ingredient combinations freely, but **write the method text
ourselves**. Do not scrape recipe instructions. This is cheap to get right now
and expensive to unwind later, and it is exactly the kind of thing a judging
panel or an AWS reviewer may ask about.

---

## Accepted decision: catalogue-constrained generation

Pilot Task 15 uses the catalogue as a constraint (Option B). The model selects
approved recipe ids and cited products; it does not invent recipes, prices, or
payable totals. Deterministic code owns portion scaling, dietary verification,
pack aggregation, arithmetic, and final validation. This extends grounding
from price records to the meal definition while keeping publication authority
out of the model.

The cost is reduced variety and the need to seed and review original recipe
content. A future hybrid fallback would change the safety boundary and requires
a separate decision; it is not the current pilot design.

---

## Migration path to CDK

The products and idempotency tables already exist. Pilot Task 9 adopts/imports
those stateful resources into a TypeScript CDK stateful stack before the
service stack is deployed; they must not be replaced or recreated.

1. Regenerate `describe-table` evidence from the deployment account in
   `ap-southeast-2` and compare it with this document before import.
2. Define stable logical ids and matching physical names, billing mode, keys,
   indexes, encryption, PITR, TTL, retention, and deletion protection in CDK.
3. Use `cdk import` (or the reviewed equivalent adoption workflow), inspect the
   change set, and prove that no table replacement or data loss is proposed.
4. Review stateful adoption separately from service deployment.
5. Create all new resources CDK-first. Do not resume console-first creation.

**Live configuration dumps are local evidence, not committed artefacts.**
`describe-table` output includes account-bearing ARNs. Keep regenerated copies
under the gitignored `infra/manual/` path, record sanitized assertions or
review results in version control, and treat CDK plus this schema as the
committed source of truth.

If regenerated live evidence disagrees with this document or the proposed CDK,
stop the import and reconcile the difference. Existing live state is evidence
to inspect, not permission to silently encode drift.
