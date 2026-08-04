# DynamoDB Schema Proposal

**Smart Grocery & Meal Budget Assistant**
Author: Philip (Backend/Orchestration, AI/Prompt Lead)
Status: **Proposal for team review**
Region: `ap-southeast-2` (Sydney)

Two tables, per the 3 Aug team decision. Build manually in the console first,
migrate to CDK later.

---

## Why two tables and not one

Single-table design pays off when related entities are fetched *together* in
one query. Prices and meal data are queried at completely different moments by
different access patterns, so combining them would add key-design complexity
and save zero round trips.

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

## Legal note on the recipe catalogue

Worth raising before anyone starts populating it.

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
3. **Dump the config immediately after creating each resource**, and commit it:

```bash
aws dynamodb describe-table --table-name grocery-products-dev \
    --region ap-southeast-2 > infra/manual/products-table.json
```

4. When migrating, use `cdk import` to adopt the existing tables into a stack
   rather than recreating them, so no data is lost.

The dumped JSON is the exact spec of what exists. Writing the CDK then becomes
transcription from a known document instead of clicking through consoles
trying to remember what was set three weeks earlier.
