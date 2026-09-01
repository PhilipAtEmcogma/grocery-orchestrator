# Open review — the live table holds fixture rows again, shadowing the real catalogue

**Status:** DIAGNOSED 2026-09-01, and the durable code fix has LANDED. It is not
a near-filter bug and not a stale record — it is fixture data present in the
live serving table, hiding the real Lineage B prices for head-term queries. The
loader guard (option B) and its regression test (option C) are now in the code;
the one remaining step is removing the fixture rows from the live table, which
needs AWS credentials and owner sign-off. See "Status of the fix" below.
**Raised:** 2026-09-01, by the parity re-run (`docs/ARCHITECTURE.md` §3s).

---

## The finding, up front

The two drifts the parity run flagged (`cheapest milk near Albany` → New World
**Devonport** $4.94; `cheapest butter` → Pak'nSAVE **Mangere** $2.97) are both
**fixture rows**, matched byte-for-byte to `fixtures/products.json`. Devonport
and Mangere are fixture-only suburbs — they do not exist in the data team's
Lineage B catalogue at all. The only way the endpoint can serve them is if the
live `grocery-products-dev` table again contains the 152 fixture rows that
`ARCHITECTURE.md` §3j says were removed on 2026-08-30, and those rows are
**shadowing the real, cheaper, correctly-located prices**. (Proving the table
holds them *right now* with a direct query needs credentials this environment
lacks — see "The one gap" — but the byte-for-byte match is conclusive that the
served answers are fixtures.)

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

**4. So the real answer is being hidden, and it is cheaper and better located.**
`cheapest milk near Albany` should return Albany $4.79; it returns a Devonport
$4.94 fixture invention. `near Albany` correctly scopes to the North Shore
region (`{Albany, Devonport}` per `config/regions.json`), the filter ordering is
correct, the coordinates are fine — none of that is the problem. The problem is a
fabricated row sitting in the serving table.

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

## The one gap

The current *live* table state cannot be confirmed from the repository — the
byte-for-byte fixture match is conclusive that the answers ARE fixtures, but
proving the table still holds them today (versus, say, the answers being cached)
needs a direct query. `aws` CLI is installed but has **no credentials** in this
environment. Confirming it is one command once credentials exist:

```bash
aws dynamodb query --table-name grocery-products-dev \
  --key-condition-expression "store_key = :sk" \
  --expression-attribute-values '{":sk":{"S":"new_world#Devonport"}}' \
  --region ap-southeast-2 --output json
```

A non-empty result is the confirmation. (`store_key` is the base-table partition
key; Devonport rows can only be fixtures.)

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

- **A — Re-remove the fixtures from the live table. ⬜ PENDING (owner action).**
  This mutates live data and needs AWS credentials, which the fixing
  environment does not have. Run when ready:

  ```bash
  python scripts/load_seed_data.py --remove --dry-run   # reports how many of the 152 are present
  python scripts/load_seed_data.py --remove             # then remove them
  ```

  Reversible with the loader itself (now guarded). This restores the 2026-08-30
  state and the real Albany $4.79 answer to `cheapest milk near Albany`.

- **D — Reconcile `ARCHITECTURE.md` §3c/§3j with the account. ⬜ PENDING.** Those
  sections' worked examples (`cheapest milk near Albany` → Albany $4.79) are
  correct for the *intended* state and will be true again once A is run. §3j has
  been annotated to note the reintroduction and that the guard now prevents a
  recurrence; the worked examples should be re-verified against the account
  after A.

**So the code is safe against a recurrence now**; the live table still serves the
fixture answer until an operator runs A. The guard means that once A is run, a
stray `load_seed_data.py` cannot quietly undo it again.

## Why this is a separate note, not part of the plane/data-source work

The 2026-09-01 architecture work (source-priority config, plane roles, parity
re-run) neither caused nor fixes this. The parity run merely *surfaced* it — and
it is worth noting that the new `config/data-sources.json` work makes the correct
long-term posture explicit: the serving table should be refreshed from Lineage B
(the primary input), and the fixtures are a fallback for offline use, not
something that belongs in the deployed table alongside real data.
