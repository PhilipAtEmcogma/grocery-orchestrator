# Response to the second technical and product audit, 2026-08-31

**Audit under review:** *"The gates moved faster than the thing they guard."* —
a 21-section re-audit of this repository, taken at commit `06d3252`.

**This response:** against `main` at `178e8fb`, on branch
`feat/phase-0-gates-under-what-exists`.

Same method as [the first response](AUDIT-RESPONSE-2026-08-30.md), and for the
reason that one gives: the audit is an outside document making claims about
this repository, and this repository's rule is that when two documents disagree
you check the thing they describe. Every finding below was re-checked against
the code before it was accepted, closed or declined. Two were understated. One
recommendation is wrong on a point that matters, and is declined in part.

Dispositions: **CLOSED** (was true, now fixed), **ACCEPTED (deferred)**,
**DECLINED**, **NOT OURS**, **UNDERSTATED** (true, and worse than reported).

---

## 1. The six findings

### Finding 1 · "The newest code is the least verified" — **UNDERSTATED, CLOSED**

The audit is right that `infra/test/service-stack.test.ts` was `describe.skip`
under a header calling a deployed 230-line stack a stub, that one assertion had
inverted, and that no CI job touched `infra/` at all.

It hedged on what un-skipping would show: *"Un-skipped as written, that test
either fails, or passes and thereby proves the Scan came back."* **It passes.**
`dynamodb:Scan` was back on the products table in the deployed
`Grocery-Service-dev`, one day after Pilot Task 6b removed it, put there by:

```ts
tables.products.grantReadData(role);          // + Scan, + index/*, + Streams
tables.idempotency.grantReadWriteData(role);  // + DeleteItem, + BatchWriteItem
```

A CDK grant helper does not CHECK the JSON policy the stack loads three
constructs earlier — it appends a second statement using the CDK's idea of
"read" and "write". `config/iam-orchestrator-role.json` had predicted it in as
many words: *"a Scan permission nothing needs is a Scan somebody can reintroduce
without noticing."*

**And the audit missed the part inside its own recommendation.** Un-skipping the
suite *as written* would have produced an eighth instance of the pattern it is
about:

- `it('the only Resource:"*" is X-Ray')` had an **empty body** — a comment and
  no expectation. Un-skipped, it passes while verifying nothing.
- The write test matched `/dynamodb:PutItem[\s\S]*grocery-products/` over
  `JSON.stringify(policies)`. That pattern spans unrelated statements, so it
  **failed on a policy with no write on products at all**: `PutItem` appears in
  the idempotency statement and `grocery-products` appears later in the blob. A
  false positive and a false negative in one suite.

Both grants removed; assertions rewritten to parse the policy document and
compare action sets per resource. 36 assertions across four infra suites, each
watched to fail against a mutated stack. New CI job `infra` — `npm ci`, build
the Lambda asset, `tsc --noEmit`, `jest`, `cdk synth` — wired into
`summary.needs`, so `tests/test_ci_workflow.py` gates it like every other job.

*Also fixed in the same pass, all found by writing the assertions:*
`APP_STAGE` was never set, so Req 12.5's runtime check stayed inert under CDK
too; `config/stages.json` now gives the Python and TypeScript halves of that
check one definition of "production"; adopted table names moved off the stage
axis, so `stage=prod` no longer references tables that do not exist.

**One guard was loosened, deliberately, and it should be reviewed as such.**
`bin/grocery.ts` threw when `CDK_DEFAULT_REGION !== ap-southeast-2`. That
variable is set by the CDK CLI from the resolved AWS profile, not by the
operator — so the guard refused `cdk synth`, which touches no account, for
anyone whose default region differed, and in CI, where there are no
credentials, it never ran at all. Blocking where it was harmless, asleep where
it was gated. Replaced by the `env` pin every stack already had plus a test
asserting it over all five, so CI now checks what the guard only claimed.

### Finding 2 · "A silent truncation, deployed" — **CLOSED**

Verified: `json.loads(models.json[:4096])` fails at line 132. `publishJson`
now throws at synth rather than slicing, and what is published is the **routing
block** rather than the whole file.

Two additions to the audit's account. The `feasibility` parameter carried the
same unguarded `.slice(0, 4096)` and fits *today* at 2,859 bytes — so the guard
is the fix and the shape is a consequence, and fixing only `models` would have
left a second silent truncation armed for the day that file grew. And the
routing block is the right slice for a reason beyond size: `scorecards` is
4,868 of the 10,930 bytes and is measured evidence, not an operator knob. An
operator who could edit it could qualify a route by typing, which is the one
thing the qualification gate exists to prevent.

### Finding 3 · "The CDK deploy doubled the unauthenticated surface" — **CORRECTED IN PART, ACCEPTED**

The security half is right and unchanged: two public, unauthenticated,
Bedrock-invoking REST APIs exist, and the deferred cutover doubled the first
audit's top finding rather than changing it.

**The monitoring half is more precise than "unalarmed, undashboarded".** Six of
the nine alarms already covered both planes: they watch EMF metrics dimensioned
on `service`, and `POWERTOOLS_SERVICE_NAME` is `grocery-orchestrator` on both —
`service-stack.ts` does not suffix it. A handler error, a latency breach, an
exhausted repair loop or a guardrail spike on either plane fires the same alarm
and always did. The gap was the **two** bound to a physical name: the API 5xx
alarm and the handler-escaped metric filter.

`ObservabilityStack` is written and creates both per plane, derived from
`cfg.suffix`, collapsing to one set when the suffix is empty — so the deploy
that retires the hand-made plane needs no edit. Plus a dashboard, a $25 budget,
and the encrypted versioned artefact bucket (the audit's #15). 12 assertions.

**And the correction cuts the other way too.** Six alarms covering both planes
also means a metric cannot say *which* plane produced it: while dual-running, a
spike on the unused CDK plane is indistinguishable from one on the plane
serving shoppers. Splitting the dimension would fix that and split every
historical series with it, so it is deliberately not done — recorded in
`ARCHITECTURE.md` §3q as a reason the dual-run should not become permanent.

**The identity gap is ACCEPTED (deferred), designed rather than closed.**
Alarming both planes makes abuse visible; it does not bound it. The audit calls
an API key "minutes", and the CDK is. What it costs is not the CDK: a required
`x-api-key` header in `CONTRACT-v1.md`, API Gateway's own 403 body instead of
the contract-valid `ChatResponse` this service guarantees on every other path,
and a broken Vite/React client that has been building against the contract
since 2026-08-21. Nobody has agreed who holds the key, and a key in a public
bundle bounds cost rather than authenticating anybody.
[`docs/OPEN-REVIEW-api-key.md`](OPEN-REVIEW-api-key.md) carries the design, the
three options with what each costs, and the four things that would change the
answer. **A holding position, not a resolution.**

### Finding 4 · "Gating repair removed the only fallback" — **CLOSED, and the recommendation is wrong on one point**

The split is done: `repair_budget` to Nova Lite (7/7), `repair_defect` to Claude
Haiku (5/5), each perfect at its half and below the 90% floor on the other.

**"Which gives both a qualified route and a fallback" is not true, and the
distinction matters.** Each half still has exactly **one** qualified model,
because the model that fails each half fails it *below the floor* — so `exclude`
removes it and nothing takes its place. What actually improves is blast radius:
a Nova Lite quota event used to take out all repair and now degrades budget
repair only, while defect repair keeps working on Haiku. That is worth having
and it is a different claim, recorded in `config/models.json`
`scorecards._split_note` rather than left as the stronger one.

**Two things the audit did not mention.** `generate_plan` has one routable
model too (`nova-pro`), and always has — two of five tasks have no alternative,
not one. And `repair_defect`'s scorecard rests on **five cases**: at n=5 a
single failure reads as 80%, so that half cannot distinguish "below the floor"
from "one bad draw" as confidently as the seven-case budget half. Recorded in
the scorecard, and it is the argument for the audit's #17.

**The split also found a live metrics bug.** `src/observability/base.py` held
its own copy of the task names. Splitting repair left that copy matching
nothing, so `TurnStats.record_model` stopped counting: `RepairAttempts` reported
**0 on a turn that repaired twice**, and a metric reading zero is
indistinguishable from a healthy turn. The names now have one definition, in
`src/models/base.py`, with a test asserting the routing table and the constants
agree in both directions.

### Finding 5 · "Two things built and not connected" — **CLOSED, both**

**Recipe planning (15c).** Wired. A meal-plan turn is built from named curated
recipes, and the audit's sketch — *"a selection prompt returning recipe ids, a
branch in build.py"* — is the shape that was **not** taken. Read literally it
puts a model call before `retrieve_prices`, and "no edge skips retrieval" is one
of the three independent enforcements of Invariant 1. `select_recipes` produces
no price and could not, so the exception would have been harmless today and
load-bearing the day the node grew.

Instead retrieval gained a recipe mode: it resolves the catalogue's 27 distinct
ingredient terms, cites them, and shortlists recipes that are costable,
dietary-viable *against the resolved products*, and affordable as a set. The
model is offered only what survives, so it cannot select an uncostable, unsafe
or unaffordable recipe — a stronger guarantee than checking the selection
afterwards.

*Two designs were tried and the eval suite caught both, which is the argument
for the audit's insistence on building the suite alongside the branch:*

- **A day is not a meal.** One recipe per day took meal-plan invariants from
  100% to **45%** and budget used from 69% to **24%**. The count now comes from
  `min_grams_per_person_day`, the same figure the feasibility refusal uses.
- **A per-recipe budget cap rejects on a number no plan ever pays.**
  `assemble_plan` aggregates packs across meals and rounds up once, so recipes
  sharing rice and onions cost far less together than apart; the cap collapsed a
  29-recipe shortlist to **one**. The budget is enforced on the set, verified
  against exhaustive search.

`evals/run_recipe_select.py`, 12 cases, in CI at 0.90, every case verified to
discriminate. And the scorecard reads what the **model** returned rather than
what the node served — with the eval scoring the served list, a planted model
returning one meal every time scored 100%.

**Anomaly detection (14a).** Wired. `refresh()` now validates, then diffs, then
writes. Run over the real catalogue: **0 rejections across 2,759 rows**, which
is the uninteresting number. Reintroducing the historical defect — removing the
sold-each guard from `unit_price()` — rejects **522 of 2,759**, broccoli
included.

**522, not six.** Six is the figure this repository has quoted since the
incident, and it was the size of the *seeded fixture set*, not of the defect
class. Against the real catalogue that one-line omission corrupts 19% of every
shopper-facing unit price, on a first write, where the diff reports nothing
because a defect on a first write is not a change. It also settles the threshold
question the audit raised: 0.2% and 19% cannot both be caught by one percentage
gate, so there is none — the alarm fires at one row.

**On ADR 0002, the audit's argument is accepted and it points somewhere
specific.** What the rules structurally cannot see is now recorded
(`ARCHITECTURE.md` §3p), and the largest invisible category needs a **baseline**
— "this price doubled overnight". That is the append-only price-history table
(the audit's #13), not a reviewer, and it is a prerequisite for one.

### Finding 6 · "What genuinely closed" — **AGREED, no action**

Accurate, and it ran the gates rather than quoting them.

---

## 2. The drift findings E1–E8

| | Disposition |
|---|---|
| E1, E2 | **CLOSED**, and understated — see Finding 1 |
| E3 | **CLOSED.** `bin/grocery.ts`, `package.json` and `jest.config.js` all called deployed stacks scaffolding |
| E4 | **CLOSED** — see Finding 2 |
| E5 | **CLOSED**, and worse than reported: **four** numbers, not two. §3a said 7 while its own table header said "v6 (now)", §3f said 9 forty lines later, README said 7 twice and 11 once. Replaced by a version HISTORY that cannot go stale, plus the command that answers the question. No document now offers a number to quote |
| E6 | **CLOSED.** The Finding 3 disposition now states what it does and does not cover |
| E7 | **CLOSED.** All 19 demos run and pass in local mode |
| E8 | **CLOSED.** The reasoning moved to `scorecards.repair_plan._history`; `_measured_not_gated` is empty |

**On the process fix the audit proposed** — a test that fails when a skip
outlives its reason — `tests/test_skip_markers.py` does it, in Python and
TypeScript alike, and refuses `.only` because it disables every other test in a
file without the word "skipped" appearing anywhere. Verified against planted
defects in both languages.

Its first version had the defect it exists to prevent: it scanned raw lines and
flagged the header of `service-stack.ts`, which necessarily contains the marker
it describes removing. A false positive with a cost — it teaches people to stop
writing down what went wrong, in a repository whose best artefacts are exactly
those write-ups. It blanks comments now.

---

## 3. What was NOT done, and why

**The 200-turn load run (#11).** Needs credentials and real spend. It remains
the gating input for four parked decisions and the audit is right that leaving
them parked indefinitely is not the same as parking them.

**API keys applied (#6).** Designed, not applied — see Finding 3.

**The live scorecard for `select_recipes`.** The suite exists and
discriminates; the measurement needs a paced Bedrock run. `select_recipes` is
`config/models.json`'s **one** exemption from the scoring gate, which had been
empty for the first time as of 2026-08-30, and it is exempt from the *live run*
rather than from having a suite. `tests/test_multimodel.py` pins the exemption
set **exactly**, so adding a second fails the build and removing this one
without a scorecard fails too. Close it with
`python evals/run_recipe_select.py --model nova-lite`.

**The three remaining CDK stubs.** Ingestion and Frontend, per the audit's own
§16 — building all three would repeat the pattern this audit is about.

**The four human decisions.** Provenance, `min_grams_per_person_day`, head
terms, ADR 0002 — plus a fifth now: who holds an API key. The audit's
observation stands and has not moved: *"the rate-limiting resource on this
project is no longer engineering time."*

---

## 4. The one thing worth carrying forward

The audit's own summary of the pattern is the right one, and this round found
it three more times — twice inside the fix for it.

> a check that reads as working and guards nothing

The infra suite was skipped **and** two of its assertions verified nothing.
`test_alarms.py` read `metric_filters[0]` and silently covered half of what its
name claimed the moment a second filter arrived. `test_sample_fixtures.py`
covered two of the four samples it could, so the published contract the
frontend reads still said *"Scripted Dinner 1"*. And the first version of the
skip-expiry test — written to catch exactly this — was itself a false positive
generator.

The lesson is not "write more checks". It is the one this repository already
states and keeps having to relearn: **assert on the input as well as the
output, and watch the check fail once before believing it.** Every assertion
added in this round was mutation-tested, and three of them found something.
