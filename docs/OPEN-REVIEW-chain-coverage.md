# Open review — we say we compare three supermarket chains. We compare two.

**Status:** open, and wants the data teammates plus a product call from the owner.
**Raised:** 2026-09-02 · **Effort:** about fifteen minutes · **You need no code.**

Companion to [`OPEN-REVIEW-head-terms.md`](OPEN-REVIEW-head-terms.md) and
[`OPEN-REVIEW-min-grams-per-person-day.md`](OPEN-REVIEW-min-grams-per-person-day.md),
and deliberately the same shape: a gap the build cannot close on its own,
written up so the people who can decide it are looking at the real numbers.

---

## The question, in one line

Every document this project publishes opens by promising a price comparison
across **Pak'nSave, Woolworths and New World**. The data we serve covers
**Pak'nSave and New World only** — two banners of one company, Foodstuffs.
**Do we get Woolworths data, or do we stop saying we have it?**

## What is actually true

| | rows | chains |
|---|---|---|
| `datasets/data/dynamodb_products/` (what we serve) | 3,000 raw → 2,759 stored | New World 1,500 · Pak'nSave 1,500 · **Woolworths 0** |
| `fixtures/products.json` (the 26-product seed) | 152 | Pak'nSave 51 · **Woolworths 51** · New World 50 |

Ten store files, five New World and five Pak'nSave. There is no Woolworths file,
no Woolworths row, and no Woolworths price anywhere in the served catalogue.

**Pak'nSave and New World are both Foodstuffs.** So the comparison a shopper
gets today is between two banners of a single company — which is a materially
different product from the one described, and it is the specific comparison a
shopper is least likely to find surprising or useful. Woolworths NZ is the other
half of a market the Commerce Commission has already found against.

## How this happened, which is the part worth carrying

Nobody removed Woolworths. It was **never in the collected data**, and the
fixtures were hiding it.

1. The fixtures carry all three chains, by construction.
2. Until 2026-09-01 the live table held **both** catalogues, so head-term
   queries were answered from the fixtures and a demo did show three chains.
3. `tasks.md` recorded the gap honestly on 2026-08-30: *"the Woolworths branch
   of the state machine fetches 0 rows — the dataset covers two chains — which
   is honest but means the product's 'three chains' claim is currently true only
   of the fixtures."*
4. On 2026-09-01 the fixture rows were removed from the live table
   (`ARCHITECTURE.md` §3t), for good reasons that had nothing to do with this.

**Deleting the fixtures converted a recorded caveat into a false headline
claim,** and nothing anywhere noticed, because the caveat lived in a task ledger
and the claim lived in a README. The removal was right; the consequence was
unreviewed. That is the same shape as every other finding in this repository —
a statement that was true when written, invalidated by a change somewhere else,
with no control connecting the two.

## What a shopper sees today

**A query scoped to Woolworths returns "I don't have price data for butter."**
That sentence is true about our data and misleading about the world: it reads as
a claim about the *product*, when what we mean is that we hold nothing for that
*chain*. `cheapest_for_product(key, stores=[WOOLWORTHS])` returns zero rows, and
the graph correctly reads zero rows as `no_data`.

**And a shopper cannot tell which stores were searched.** The response carries
citations for the stores that had results; there is no field saying what was in
scope. So "searched Woolworths, found nothing" and "never searched Woolworths"
are indistinguishable on the wire. That is the gap option C closes.

## The owner's framing, which matters here

Recorded 2026-09-02, and it is the strongest argument for the lightest option:

> *We are trying to be as accurate and as helpful as possible, but in the end it
> is up to the user to decide where to get the produce and whether to follow our
> recommendations.*

That is right, and it does real work in this decision. The product is
**advisory**: it reports prices it retrieved, and the shopper chooses. Nothing
here auto-purchases, nothing routes an order, and a shopper who prefers
Woolworths is free to shop there regardless of what we say. So this is **not** a
case of steering someone into a bad transaction, and it does not warrant
blocking the pilot.

**What it does not resolve is the accuracy of a claim about ourselves.**
"Up to the user" answers *who bears the choice*; it does not answer *whether we
described our own coverage correctly*. A shopper deciding freely is still
deciding on the basis of what we told them we looked at — and if they believe we
checked Woolworths, "cheapest" means something to them that it does not mean to
us. `ACQUISITION-RISK.md` §4.5 makes the same point in legal terms: Fair Trading
exposure attaches to **the comparison we publish**, not to the shopper's
decision, and condition 13 of §8 says no superlative claim beyond what the
retrieved data supports.

So the advisory framing correctly downgrades this from *urgent* to *cheap to fix
properly*. It does not make it nothing, and the fix it points at is option B or
C rather than A.

## The options

### A — Get Woolworths data

The data teammates collect a Woolworths slice the way the Foodstuffs one was
collected. Everything downstream already exists: `KNOWN_RETAILERS` lists it,
`ingestion/lineage_b.py` already maps both `Woolworths` and `Countdown` (renamed
in 2023), the state machine already has the branch, and `config/store-locations.json`
is keyed by suburb rather than by chain, so no config changes shape.

**Cost:** entirely theirs, and unknown to us. **Gated by
[`ACQUISITION-RISK.md`](../ACQUISITION-RISK.md) §8 exactly as the existing data
is** — this review does not authorise collection, and §7's recommendation
(ask for permission first) applies to Woolworths NZ as much as to Foodstuffs.
**Best product outcome**, and the only option that makes the current claim true.

### B — Say what we cover (recommended, and cheap)

Change the claim, not the data. README, `AGENTS.md` and the demo script say
"Pak'nSave and New World" until Woolworths data exists; `KNOWN_RETAILERS` keeps
Woolworths as a *supported* retailer with a note that no rows are loaded.

**Cost:** an hour of documentation. **Honest immediately**, and it is the option
the advisory framing points at: we are not obliged to compare every chain, only
to be accurate about which ones we compared. Combines with C.

### C — Tell the shopper the scope, in the answer

Add the searched scope to the response — a `notice` naming the chains covered,
or a scope field on the comparison. Then "we checked two chains" travels with
the price instead of living in a README the shopper never reads, and a
Woolworths-scoped query can say *"I don't hold Woolworths prices"* instead of
*"I don't have price data for butter."*

**Cost:** small and additive — a `notice` needs no contract change at all, and
clients already ignore unknown fields. **This is the one that survives contact
with a real shopper**, because it is the only option where the caveat is in
front of the person making the decision. It also fixes the misleading `no_data`
above, which is a defect on its own terms.

### D — Do nothing, knowingly

Defensible only if the demo audience is told out loud, every time, and it lasts
exactly as long as nobody screenshots a "cheapest across three chains" claim.
**Not recommended**, because it is the only option that leaves a false statement
standing where a stranger can read it.

## What I would do

**B now, C with the frontend work, A if the data teammates can get it.** B is an
hour and removes the false claim today. C is the durable fix and lands naturally
alongside the frontend cutover, which is already touching the response path. A
is the real answer and is not ours to schedule.

## What would change the answer

- **Woolworths data arriving.** Everything above closes; the claim becomes true.
- **A demo outside the team, or any public URL.** Raises B from "should" to
  "before that happens" — a false comparison claim in front of strangers is the
  §4.5 exposure, and it is the same trigger `docs/OPEN-REVIEW-api-key.md` names.
- **A decision that two chains is the product.** Legitimate — the assistant is
  useful with two — but then the three-chain claim has to go everywhere,
  including the demo narration, not just the README.
- **Someone establishing that the collected Foodstuffs data was permissioned.**
  It bears on A's feasibility, and it is the same open question as the
  provenance of the 2,759 rows (`README.md`, "Where the 2,759 served rows
  actually came from").

## How to answer

A sentence is plenty. Say which option, and if it is A, whether Woolworths
collection is something the data teammates can take on and under what
permission. If B or C, I can make the change.

Record the answer here — update **Status** at the top with the date and the
choice — and, if it is A, add the acquisition question to
[`ACQUISITION-RISK.md`](../ACQUISITION-RISK.md) §8's condition 1, which is
already unmet for the three sources it names.
