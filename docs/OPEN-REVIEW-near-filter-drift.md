# Resolved — fixture rows were shadowing the real catalogue; removed and guarded

**Status: RESOLVED 2026-09-03, having been closed once too early.** It was not a
near-filter bug and not a stale record — it was fixture data present in the live
serving table, hiding the real Lineage B prices for head-term queries. Kept as a
record rather than deleted, because the reintroduce/remove cycle and the reason
it went unnoticed are the useful part.
**Raised:** 2026-09-01, by the parity re-run (`docs/ARCHITECTURE.md` §3s).
**Closed:** 2026-09-01 — **and reopened by the account on 2026-09-03**, which
found the rows back in the table and the actual vector untouched. See
"What closing this too early cost" and "Status of the fix".

> **The 2026-09-01 close was wrong, and this is the interesting part.** It said
> "the loader is guarded so it cannot recur" — one vector, guarded, described as
> the vector. The thing re-adding the rows was the **scheduled ingestion
> Lambda**, running nightly, which no guard on a hand-run script could reach. The
> record read as resolved for two days while the defect recurred every night.

---

## The finding, up front

The two drifts the parity run flagged (`cheapest milk near Albany` → New World
**Devonport** $4.94; `cheapest butter` → Pak'nSAVE **Mangere** $2.97) were both
**fixture rows**, matched byte-for-byte to `fixtures/products.json`. Devonport
and Mangere are fixture-only suburbs — they do not exist in the data team's
Lineage B catalogue at all. The live `grocery-products-dev` table again
contained the 152 fixture rows that `ARCHITECTURE.md` §3j records as removed on
2026-08-30, and those rows were **shadowing the real, cheaper, correctly-located
prices**.

Confirmed directly against the account on 2026-09-01: a dry-run reported
**152 of 152** fixture rows present, and after `--remove` the fixture product
keys `milk-2l` and `butter-500g` dropped to a GSI1 count of 0 while the real
`standard-milk-2l` stayed at 10. The endpoint now serves the real answers —
`cheapest milk near Albany` → Pak'nSAVE Albany $4.79, `cheapest butter` →
Pak'nSAVE Albany $9.49.

For `cheapest milk near Albany` the real answer is Pak'nSAVE / standard milk at
**Albany for $4.79** (Lineage B), which is both cheaper and actually in Albany.
The shopper is being shown a fabricated fixture price ($4.94 at a store 13 km
away) instead. That is a grounding/correctness problem, not a location one.

The near-filter, the region mapping, and the filter-before-limit ordering are
all working exactly as designed. The original hypothesis below (a near-filter
bug) was **wrong**, and the evidence that overturned it is in "What the
investigation found".

---

## What was observed (and only that)

The 2026-09-01 parity re-run (`scripts/check_parity.py`, output in
`reports/parity_rerun_2026-09-01.txt`) compared the two live planes and found
them at parity — both planes return the SAME answers. But two of those answers
have moved away from what the 2026-08-30 deployment record documents:

| Request | Record (2026-08-30, ARCHITECTURE.md §3c/§3j/§3m) | Observed (2026-09-01, both planes) |
|---|---|---|
| `cheapest butter` | Pak'nSAVE **Albany** $9.49 Mainland Salted Butter | Pak'nSAVE **Mangere** $2.97 Pams Butter 500g |
| `cheapest milk near Albany` | Pak'nSAVE **Albany** $4.79 Pams Value Standard Milk | New World **Devonport** $4.94 |

**This is not a parity failure**, and it is not a near-filter bug either. Both
planes agree with each other on the new answers, so it is a change in the shared
data, not a difference between the hand-made and CDK planes — and the
investigation below establishes the real cause. It is exactly the shape this
project keeps flagging: *a number changed and nothing alarmed, because
everything still matched everything else.*

## What the investigation found

All of this is reproducible from the repository alone (no AWS access needed);
the one thing it cannot show is the *current* live table, addressed under
"The one gap" below.

**1. The live answers are fixture rows, matched byte-for-byte.** The committed
`fixtures/products.json` contains exactly these rows:

| product_key | store | display_name | price | valid |
|---|---|---|---|---|
| `milk-2l` | `new_world#Devonport` | Value MILK 2L | $4.94 | 2026-07-31 |
| `butter-500g` | `paknsave#Mangere` | Pams Butter 500g | $2.97 | 2026-07-31 |

Those are the two live answers, down to the display name and the capture date.

**2. Devonport and Mangere are fixture-only suburbs.** The fixture catalogue's
store locations are `Devonport, Mangere, Mt Wellington, Newmarket, Ponsonby,
Sylvia Park`. The data team's Lineage B catalogue (the real 2,759 rows) has
`Albany, Lincoln Road, Manukau, Mt Albert, New Lynn, Newmarket, Papakura,
Remuera, Sylvia Park` — **no Devonport, no Mangere.** So a live answer citing a
Devonport or Mangere store cannot have come from the real catalogue; it can only
be a fixture row.

**3. The fixtures shadow the real data through the synonym order.** `"milk"`
resolves (via `config/product-synonyms.json`) to the candidate list
`['milk-2l', 'standard-milk-2l']`, in that order. In Lineage B, `milk-2l` has
**zero** rows and `standard-milk-2l` has ten — including **Albany at $4.79**.
`resolve_product_key` returns the first candidate that has any rows. With the
fixtures present, `milk-2l` has rows (the fixture ones), so it wins, and the
resolver never reaches `standard-milk-2l` and the real Albany price. Remove the
fixtures and the same query resolves to `standard-milk-2l` → Albany $4.79, which
is what §3j recorded on 2026-08-30.

**4. So the real answer was being hidden, and it is cheaper and better located.**
`cheapest milk near Albany` should return Albany $4.79; it was returning a
Devonport $4.94 fixture invention. `near Albany` correctly scopes to the North
Shore region (`{Albany, Devonport}` per `config/regions.json`), the filter
ordering is correct, the coordinates are fine — none of that was the problem.
The problem was a fabricated row sitting in the serving table, now removed.

## The mechanism: the fixtures were re-added

`ARCHITECTURE.md` §3j says the fixture rows were removed on 2026-08-30 with
`scripts/load_seed_data.py --remove`. But that script's DEFAULT action (no flag)
**loads** the fixtures; `--remove` is the inverse. So any later run of
`python scripts/load_seed_data.py` with no `--remove` — during demo prep, a
redeploy step, or a smoke test — re-adds all 152 fixture rows. That is the most
likely path back in, and it leaves no signal: the loader is idempotent and the
rows look valid.

This is the §3j defect reintroduced through the front door, and it is the same
family the repo keeps recording — a control (fixture removal) that was correct
once and silently undone, with nothing to notice.

## The gap that was, and how it closed — plus a casing trap worth keeping

The live table state was later confirmed directly (SSO profile, account
`097087133897`): a dry-run reported **152 of 152** fixture rows present, and the
removal deleted all 152. The endpoint then served the real Albany answers.

**One wrong turn is worth recording, because it nearly produced a false "already
fixed".** The first live probe queried the base table by
`store_key = "new_world#Devonport"` — capital D, spaced — and got **count 0**,
which briefly looked like the fixtures were already gone. They were not: the
stored `store_key` is **slugged lowercase**, `new_world#devonport`, so the probe
was simply looking under the wrong key. The authoritative, casing-independent
check is a **GSI1 query on `product_key`** (`milk-2l`, `butter-500g`), which does
not depend on the store-key slug — it showed 6 fixture rows each, and the table
`ItemCount` was ~2,911 (2,759 real + 152 fixture). Cross-checking the surprising
result against a second method is what caught it. When probing this table by
hand, query GSI1 by `product_key`, or use the exact slugged `store_key`, never
the display-cased location.

## Status of the fix

Four things were identified; the two that can be done in code without touching
the live table are done, and are in the same change as this note.

- **B — Stop it recurring. ✅ LANDED.** `scripts/load_seed_data.py` now refuses
  to load the fixtures when the real catalogue is already present, unless
  `--force` is passed. It detects the real catalogue with a cheap probe:
  `real_catalogue_present()` queries a small set of **real-only store keys**
  (`_REAL_ONLY_STORE_KEYS` = `paknsave#albany`, `new_world#albany` — present in
  Lineage B, absent from the fixtures) using `Select="COUNT"`, so it needs one
  `Query` per probe and no `Scan`. This is the durable fix: it makes a
  re-add deliberate rather than accidental, so option A cannot be silently
  undone the way the 2026-08-30 removal was.

- **C — Regression test. ✅ LANDED.** `tests/test_ingestion.py` now asserts the
  guard refuses over a real catalogue and writes nothing, that a clean table
  still loads, that `--force` bypasses, and — the part that keeps the guard
  honest — that `_REAL_ONLY_STORE_KEYS` stays **disjoint from the fixtures and
  present in Lineage B**, so a future catalogue change that invalidated the
  probe fails the build instead of silently disarming the guard.

- **A — Re-remove the fixtures from the live table. ✅ DONE 2026-09-01.**
  Run against the account with the `grocery` SSO profile:

  ```bash
  python scripts/load_seed_data.py --remove --dry-run   # reported: 152 of 152 present
  python scripts/load_seed_data.py --remove             # deleted: 152
  ```

  Verified after: GSI1 `product_key` counts `milk-2l` = 0 and `butter-500g` = 0
  (fixtures gone), `standard-milk-2l` = 10 (real intact); and the endpoint,
  with fresh session ids, returns `cheapest milk near Albany` → Pak'nSAVE Albany
  **$4.79** and `cheapest butter` → Pak'nSAVE Albany **$9.49**. Reversible with
  the loader itself (now guarded, so a re-add is deliberate).

- **D — Reconcile `ARCHITECTURE.md` §3c/§3j with the account. ✅ DONE 2026-09-01.**
  Those sections' worked examples (`cheapest milk near Albany` → Albany $4.79)
  are true again and were re-verified live. §3j records the reintroduce/remove
  cycle and that the guard now prevents a recurrence.

**All four parts were done, and it recurred anyway.** See below.

## What closing this too early cost — 2026-09-03

An account check on 2026-09-03, two days after this was marked resolved, found
the 152 fixture rows **back in `grocery-products-dev`** and `cheapest milk near
Albany` answering with the Devonport fixture price again.

**The vector was the scheduled ingestion Lambda, not the loader.** Every night at
03:18 the price-refresh state machine invoked `ingestion.handler.refresh` with no
`PRICE_SOURCE` set; `resolve_source` read `default_source: fixtures` from
`config/data-sources.json` — the correct default for a laptop — and wrote the
fixture catalogue over the real one. Option B above guarded the path a human
takes. This one was taken by a schedule.

**It was silent because it had reached steady state.** The execution output read
`fetched 51, written 51, added 0, changed 0, unchanged 51` for each retailer —
which is exactly what the diff *should* say when yesterday's re-injection is
still sitting in the table. The one control that could have seen this correctly
reported nothing to see. The freshness stopgap hid the other half: at
`max_price_age_days: 45` the 2026-07-31 fixture rows stay "fresh" until
2026-09-14, where the original 14 would have turned every fixture answer into
`STALE_DATA` on 2026-08-14 and made the shadowing loud.

**Remediated live, in this order:**

```bash
aws scheduler update-schedule --name grocery-price-refresh-dev --state DISABLED
python scripts/load_seed_data.py --remove       # 152 of 152 present, 152 deleted
```

Verified after: GSI1 `milk-2l` and `butter-500g` (fixture keys) at **0** each,
`standard-milk-2l` (real) at 10, `cheapest milk near Albany` → Pak'nSAVE Albany
**$4.79** and `cheapest butter` → Pak'nSAVE Albany **$9.49**. The schedule is
byte-identical to what was live apart from `State: DISABLED`.

**Then guarded properly, in code, at two layers** (`ingestion/guard.py`):

- **A deployment may not DEFAULT to the fixtures.** `resolve_source` refuses when
  it detects a deployment (`AWS_LAMBDA_FUNCTION_NAME`, or `APP_STAGE` once the
  cutover sets it) and nothing selected a source. `PRICE_SOURCE=fixtures` is
  explicit and still allowed. This is the layer that stops the nightly re-injection.
- **Nothing WRITES the fixtures over a table that holds the real catalogue.**
  `refresh()` runs the same `real_catalogue_present` probe the loader uses — one
  shared definition, not two — and refuses, dry run included. This layer does not
  care how the fixtures were selected.

**And the refusal is visible.** A thrown branch does not fail the Step Functions
execution: `config/ingestion-state-machine.json` catches `States.ALL` inside the
item processor so one broken retailer does not discard the other two, which means
`ExecutionsFailed` reports nothing for a branch that died. The Lambda now prints
`ingestion_refresh_failed` before re-raising, and `grocery-ingestion-refresh-failed-dev`
alarms on it — otherwise this fix would have replaced a silent wrong write with a
silent failed write.

**The source question that was open here was settled 2026-09-04** (PR #81,
`ARCHITECTURE.md` §3u). `scripts/build_lambda.py` now ships
`datasets/data/dynamodb_products` in the archive, and the deployed function runs
`PRICE_SOURCE=lineage_b` — so the fixtures are no longer what a deployed refresh
reaches for, by configuration as well as by refusal. The first deployed refresh
wrote 2,759 rows with `added 0, changed 0, rejected 0`, and the fixture keys
`milk-2l` and `butter-500g` stayed at 0 on GSI1 throughout.

**The schedule stays DISABLED by choice**, now for a different reason than when
this note was written. It is no longer blocked on a decision: the catalogue
simply cannot get any fresher, because `LineageBSource.CAPTURED_AT` is the
constant `2026-08-28`. The agreed cadence when it returns is WEEKLY, as a
liveness check rather than a refresh — see `config/data-sources.json`
`_what_the_weekly_refresh_is_actually_for`.

## How it relates to the plane/data-source work

The 2026-09-01 architecture work (source-priority config, plane roles, parity
re-run) did not cause this; the parity run *surfaced* it. And the new
`config/data-sources.json` states the posture that keeps it from returning: the
serving table is refreshed from Lineage B (the primary input), and the fixtures
are a fallback for offline use, not something that belongs in the deployed table
alongside real data. The two guards in `ingestion/guard.py` enforce that
boundary at both places the fixtures could get back in — and the count is two
because the first attempt assumed it was one.
