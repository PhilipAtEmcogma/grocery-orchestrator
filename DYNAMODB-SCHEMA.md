# DynamoDB Schema Proposal

**Smart Grocery & Meal Budget Assistant**
Author: Philip (Backend/Orchestration, AI/Prompt Lead)
Status: **Proposal for team review**
Region: `ap-southeast-2` (Sydney)

Three tables. Build manually in the console first, migrate to CDK later.

The 3 Aug team decision covered two — products and meals. The third,
idempotency, came out of the turn-deduplication work and was missing from this
document until now.

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

**Cost of this design:** a price change requires delete-and-rewrite of the GSI
item, because the price is part of the sort key. With a daily full refresh
from the scraper that is a non-issue.

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
| `payload_hash` | S | Truncated SHA-256 of the raw request body |
| `status` | S | `in_progress` \| `completed` |
| `response_json` | S | The full `ChatResponse`. Absent while in progress |
| `started_at` | N | Epoch seconds, when the claim was taken |
| `ttl` | N | **Unix epoch seconds — 24h.** Enable TTL on this attribute |

### `acquire` must be a conditional put, not a read-then-write

This is the part that is easy to get wrong and impossible to catch on one
machine.

The obvious implementation reads the key, sees nothing, and writes a marker.
Two Lambda invocations racing on the same key both read "absent", both write,
and both proceed to generate — which defeats the entire purpose of the table
while passing every single-threaded test you write against it.

The claim has to be **one atomic conditional write**: succeed only if no live
record exists.

```python
table.put_item(
    Item={
        "pk": key,
        "payload_hash": payload_hash,
        "status": "in_progress",
        "started_at": now,
        "ttl": now + 86400,
    },
    ConditionExpression=(
        "attribute_not_exists(pk) OR (#s = :in_progress AND started_at < :stale)"
    ),
    ExpressionAttributeNames={"#s": "status"},
    ExpressionAttributeValues={
        ":in_progress": "in_progress",
        ":stale": now - IN_PROGRESS_TIMEOUT_SECONDS,
    },
)
```

A `ConditionalCheckFailedException` is the normal path, not an error: it means
somebody else holds the key. Read the item to decide which of the three
outcomes to return.

**The second clause takes over an abandoned claim.** An invocation that crashed
mid-turn leaves an `in_progress` marker that nothing will ever complete.
Without the staleness condition the client is blocked for the full 24-hour TTL
on a turn that will never finish. The timeout is set longer than the gateway
ceiling — so a slow-but-alive request is not duplicated — and far shorter than
the TTL.

Ask for `ReturnValuesOnConditionCheckFailure` so the losing writer gets the
existing item back without a second round trip.

### The four outcomes

| Outcome | Condition | Handler response |
|---|---|---|
| Acquired | No live record, or the existing claim is stale | Run the turn |
| Completed | `status = completed` | Return `response_json` verbatim |
| In progress | `status = in_progress`, claim still fresh | `409`, retryable |
| Payload mismatch | Stored `payload_hash` differs | `400`, **not** retryable |

**Payload mismatch is a rejection, not a cache hit.** The same `turn_id` with
different content is a client bug. Returning the stored response would answer a
question the client did not ask, and would do it invisibly.

**Only terminal outcomes are written back.** A turn that failed in a retryable
way deletes its claim instead of completing it. Caching a transient failure
would make the client's retry permanently useless — it would receive the same
failure forever, from a mechanism built to help it recover.

### Console settings that are NOT on by default

- **TTL on the `ttl` attribute** — off by default. Without it this table grows
  without bound, and it holds request payload hashes and full response bodies
  including shopping lists.
- **On-demand capacity** — one small write and one read per turn.
- Point-in-time recovery is **not** needed here. This is disposable
  operational state with a 24-hour life; there is nothing to recover to.

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

## Open decision: how strongly does the catalogue constrain generation?

This changes the `generate_plan` node materially, so the team should pick
deliberately.

**Option A — catalogue as inspiration.** Sonnet still composes meals freely
from available products; recipes are retrieved as examples in the prompt.
Maximum variety, minimal code change, but the model can still produce a meal
that is technically valid and unappetising.

**Option B — catalogue as constraint.** Sonnet *selects* recipe ids and scales
portions; it does not invent meals. Every meal is then something a human
wrote and approved. This extends the grounding guarantee from prices to meals:
the model cannot invent a bad recipe because it cannot invent a recipe at all.
Cost is variety, and someone has to seed the catalogue.

**Option C — hybrid.** Prefer catalogue recipes; allow free composition only
when no catalogue recipe fits the constraints, and flag those in the response.

My recommendation is **B for the demo, C as the real product.** Option B is
the stronger story: "our meal plans are grounded in a curated catalogue, so we
cannot produce the failure mode Pak'nSave's Savey Meal-bot had." That is a
concrete, defensible answer to the obvious question about AI safety in a food
context.

---

## Migration path to CDK

Manual creation first, per the team decision. To keep the CDK migration
mechanical rather than archaeological:

1. **Tag every resource** on creation: `Project=SmartGrocery`, `Env=dev`,
   `Owner=<name>`.
2. **Name consistently**: `grocery-<thing>-dev`. CDK will want to create
   resources alongside the manual ones during migration, and identical names
   collide.
3. **Dump the config immediately after creating each resource**, into
   `infra/manual/`:

```bash
aws dynamodb describe-table --table-name grocery-products-dev \
    --region ap-southeast-2 > infra/manual/products-table.json
```

4. When migrating, use `cdk import` to adopt the existing tables into a stack
   rather than recreating them, so no data is lost.

**`infra/manual/` is gitignored — the dumps are local artefacts, not committed
records.** `describe-table` output carries the account ID in every ARN, and
that is not something to put in a repository this project intends to open up.

That makes **this document the committed reference for the CDK migration**, and
the dumps a regenerable check against it. Regenerate them from the live account
when the migration starts; do not rely on a copy from three weeks earlier.

Be aware of what the trade costs. A dump captures what AWS *actually* set —
applied defaults, billing mode, stream settings, index status — where this
document captures what we *intended*. Those diverge, quietly, and the dump is
what would have caught it. The mitigation is that regenerating is one command
and must be step one of the migration, not a later reconciliation. If the two
ever disagree, **the dump is right and this document is stale**, and fixing
this document is part of the migration work.
