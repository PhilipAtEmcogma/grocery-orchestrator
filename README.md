# Smart Grocery & Meal Budget Assistant — Orchestrator

A conversational assistant for New Zealand shoppers that answers "what's the
cheapest butter near me?" and "feed a flat of 3 for under $60 this week"
questions by grounding every answer in real, retrieved supermarket price
data. Built for the AWS AI Innovation Mentorship Workshop as a reference
implementation of the **retrieve-then-generate** pattern on Amazon Bedrock.

This repository is the **orchestrator and AI application layer**: it classifies
a turn, retrieves prices, invokes Bedrock through a task-based model plane, and
returns validated events.

---

## Where this is right now

**If you are picking this up cold, read this section and nothing else until you
need to.** Everything below it is detail.

The application layer is built and evidenced, and **a working service is
deployed in `ap-southeast-2`** — REST API `woqmel35lk`
(`grocery-orchestrator-api-dev`), stage `dev`, `POST /dev/chat`, integrated
against Lambda alias `grocery-orchestrator-dev:live`. Re-verified live on
2026-08-30: HTTP 200, a real Nova Lite call, five grounded citations, prices as
strings. Details and identifiers in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §3.

**Until 2026-08-30 this section said "nothing is deployed — there is no CDK
stack, no API Gateway, no Lambda alias".** Two of those three were false, and
had been since 2026-08-27; `docs/ARCHITECTURE.md` §3 recorded the API and the
alias correctly the whole time and was not believed. What remains true is the
CDK half: there is **no IaC**, so nothing about the deployment is reproducible,
drift-detectable, or reviewable as a unit.

**The code is current as of 2026-08-30.** The alias served version `5` from
2026-08-27 — before Pilot Tasks 4–7 — and now serves **version `7`**, built from
`main`. The defect that mattered is gone: the endpoint no longer invents a `$0`
budget from a message that never mentioned money and then refuses it.

Enforcing freshness then made every priced query return `STALE_DATA` — the
seeded fixtures are dated 2026-07-31 against a 14-day threshold. **Decision
2026-08-30: `max_price_age_days` raised 14 → 45** as an explicit, reversible
dev-stage stopgap, recorded in `config/freshness.json` and
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §3c. Re-stamping the fixtures'
capture date was rejected as fabricated provenance. All four paths — comparison,
named regions, clarification and meal plan — verified working live on version 7.

So the remaining distance to a pilot is **real ingested data, IaC adoption, and
operational evidence** — not first deployment.

| Pilot Task | State |
|---|---|
| 1 · Documentation alignment | ✅ done |
| 2 · Citation construction, money-free rendering, retrieved-record proof | ✅ done |
| 3 · Guardrail propagation, harness, **live 13/13 + 9/9** | ✅ done · one deferral (3d) |
| 4 · Clarification, payable arithmetic | ✅ done |
| 5 · Location scope, freshness, named regions | ✅ done |
| 6 · Idempotency fencing, canonical hashing, pagination, PITR | ✅ done · one deferral (6b) |
| 7 · Scorecards, route qualification, prose/repair evals | ✅ done · one deferral (7b) |
| 8 · Local read-only MCP | ✅ done — 2 coarse tools, default-off, capped, parity-tested |
| 9–12 · CDK, service plane, deploy, operations | 🟡 **12 substantially done** (8 alarms, dashboard, Budget, first deployed latency + cost baselines). 9–11 = IaC, not started; the service is deployed imperatively |
| 13 · Controlled ingestion | ⬜ not started |
| 14 · AgentCore reviewer | ⬜ proposed, needs ADR 0002 approval |
| 15 · Recipe catalogue | 🟡 catalogue + coverage gate built; **planning blocked on data** (0 of 175 recipes fully priceable) |
| 16 · Release gates | ⬜ not started |

**Two deliberate deferrals remain** (6b closed 2026-08-30), each with the
reasoning recorded in `tasks.md`:

- **3d** — the Guardrail refuses a bare `price of mushrooms`. The foraging topic
  was scoped to an ingredient rather than an activity; version 2 fixed truffle
  oil and qualified mushrooms, but not the unqualified noun. Not tuned further
  because loosening a safety topic by trial and error is the wrong direction.
- **6b** — **CLOSED 2026-08-30.** `candidates_for_budget` now queries **GSI2**
  (partition by `category`, sort by zero-padded price) instead of scanning. The
  deferral required the replacement be chosen from real access patterns and load
  evidence; the data team's 2,939-row catalogue supplied both, and their own
  table independently carries the same `CategoryPriceIndex` shape. The forcing
  test did its job and is now an assertion that the Scan never returns.
- **7b** — SSM routing belongs with the CDK stacks, where a parameter is
  declared as infrastructure rather than clicked into an account.

**Deployed and operating** (2026-08-30): alias `live` → **v11** serving current
`main`, the **real 2,759-row catalogue**, Guardrail **v2** applied, GSI2 for
meal-plan candidates with `Scan` revoked, **8 alarms** + dashboard + a $25
Budget, API-stage X-Ray, and the first latency baseline measured against the
endpoint rather than a laptop — price check p95 **2.21s** (target 5s), meal plan
p95 **12.2s** (target 20s). Detail in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §3a–§3l.

**Verified live in `ap-southeast-2`** (account `097087133897`, 2026-08-29):
Guardrail `b1xezpqe04kx` **version 2** at 13/13 must-block and 9/9 must-allow;
intent scorecards Nova Pro 100.0%, Claude Haiku 4.5 96.4%, Nova Lite 92.9%;
DynamoDB products and idempotency tables with owner-fenced claims proven against
the real table. Procedure and traps: [`docs/LIVE-EVAL-RUNBOOK.md`](docs/LIVE-EVAL-RUNBOOK.md).

**Offline gates:** 763 tests passing, 31 skipped. Five eval suites — intent
76.7%, meal plan 100%, prose 100%, repair 100% (12 cases), guardrail 9/9
must-allow — all gated in CI and the pre-commit hook. Repair is measured live
too — Nova Lite 91.7%, Claude Haiku 83.3% — and deliberately not gated on model
choice; see `config/models.json` `_measured_not_gated`.

**Two defects found and fixed on 2026-08-30**, both backend, both invisible to
every offline gate because nothing offline can read a deployed environment
variable:

- ~~**Guardrail version drift**~~ — **fixed 2026-08-30.** The Lambda applied
  version `1` while all evidence described version `2`, so `how much is truffle
  oil` — a documented `must_allow` case — was refused live while the record said
  9/9. Now version `2` (alias v9), both must-allow mushroom cases verified.
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §3f.
- ~~**Silent demo mode**~~ — **check implemented 2026-08-30** (Req 12.5).
  Dropping `USE_DYNAMODB` or `USE_BEDROCK` used to fall back to fixtures and the
  scripted model, returning grounded, arithmetically valid citations about 26
  fake products with no error anywhere.
  `assert_production_configuration()` now refuses to start a `prod`/`pilot`
  stage without them. **Not yet armed in the account** — `APP_STAGE` is unset,
  because `CORS_ORIGIN=*` would fail it and needs the frontend origin first.
  §3g.

**Three decisions waiting on a person** — each has its evidence gathered and its
options written down; none needs more code first:

1. **The recipe catalogue and the product catalogue do not meet.** Zero of 175
   recipes are fully priceable, so Req 2.9 cannot be delivered as written. Widen
   the data collection, re-source recipes to fit the catalogue, or narrow the
   requirement — `tasks.md` Pilot Task 15b.
2. **Gate repair at 90%?** Measured live at 12 cases: Nova Lite 91.7%, Claude
   Haiku 83.3%, failing in opposite halves. A 90% floor passes one and fails the
   other, and both are in `repair_plan`'s prefer list — so gating removes the
   fallback. `config/models.json` `_measured_not_gated`.
3. **Who owns the `Chatbot` API and Lambda** in the same account?
   `docs/ARCHITECTURE.md` §3b — untouched pending an owner.

**Known open questions that want a human**, not more code:

- `min_grams_per_person_day` decides which meal-plan requests are refused
  outright and has never been reviewed by anyone who knows about food —
  [`docs/OPEN-REVIEW-min-grams-per-person-day.md`](docs/OPEN-REVIEW-min-grams-per-person-day.md).
- Which product a one-word query returns — "cheapest butter" against fourteen
  butters — was decided by reading the catalogue, not by anyone who shops there:
  [`docs/OPEN-REVIEW-head-terms.md`](docs/OPEN-REVIEW-head-terms.md).
- **The recipe catalogue and the product catalogue do not meet.** No recipe is
  fully priceable, so Req 2.9 cannot be delivered as written. Widening the
  product collection, choosing recipes to fit the catalogue, or narrowing the
  requirement are all defensible — and the choice belongs to the team, not this
  repository. `tasks.md` Pilot Task 15b has the evidence and the three options.
- The frontend team's response shape in `datasets/DATA_SCHEMA.md` is flat JSON
  with different intent names; ours is an event list. Both are reasonable, they
  are not the same thing, and nobody has reconciled them.
- Three of four frontend contract questions remain unanswered; question 2
  (location shape) was resolved on our recorded default after it blocked four
  separate pieces of work.

## The core idea

**A price can never reach the user unless it was actually retrieved.**

That guarantee is structural, not a prompt instruction the model could
ignore:

- The orchestration graph (LangGraph) only has one path into generation, and
  that path runs through a `retrieve_prices` node first. There is no edge
  that skips it.
- The meal-planning model never writes a number. It selects product
  references and pack quantities; every price, subtotal and total is
  computed afterwards in plain Python from the retrieved records.
- Every structured priced item carries a `citation_ref` to a declared
  `Citation`, and every `Citation` is compared against the frozen record
  retrieval actually returned — the ref must have been retrieved, its
  table/partition/sort keys must identify that exact stored record, and every
  published value must equal the retrieved one. Until 2026-08-29 only the
  *shape* of those keys was checked, so a citation naming the right table with
  a plausible key and a price nobody retrieved passed cleanly.
- If a product genuinely can't be found, the assistant says so
  (`no_data` / `budget_infeasible`) instead of guessing. Honest failure is a
  first-class outcome, not an error to paper over.

```
START
  v
validate_input
  v
classify_intent
  |--- general_chat / out_of_scope ------------------> finalise
  |--- meal_plan + unsupported exclusion ------------> emit_dietary_unsupported -> finalise
  |--- meal_plan + missing constraint ---------------> emit_clarification -------> finalise
  v
retrieve_prices            <-- the ONLY source of prices
  |--- no citations -----> emit_no_data -------------> finalise
  |--- all prices stale --> emit_stale_data ---------> finalise
  |--- price_check ------> generate_comparison -> generate_prose -> finalise
  v (meal_plan)
generate_plan  <----------------+
  v                             |
validate_plan                   | repair (bounded)
  |--- errors ---> repair_plan +
  |--- attempts exhausted ---> emit_budget_infeasible -> finalise
  v ok
generate_prose -> finalise -> END
```

## Design principles

- **Event-shaped contract.** The response is always a list of typed events
  (`session`, `intent`, `citation`, `price_comparison`, `meal_plan`,
  `notice`, `no_data`, `error`, `done`), defined once in
  `src/schemas/contract.py`. Over REST the whole list returns at once; the
  planned WebSocket upgrade emits the same events one at a time, so the
  contract doesn't change when the transport does.
- **Grounding is structural.** See above — enforced by
  `assert_grounded()`, and `assert_arithmetic()` re-derives every subtotal
  and total to make sure a plan's numbers actually add up.
- **Honest degradation, not failure.** If the classification model call
  fails, the graph falls back to keyword heuristics with a lower confidence
  score rather than failing the turn — wrong UI treatment is recoverable,
  a fabricated price is not. The fallback is recorded in state
  (`intent_degraded`) so it's visible in logs.
- **Message wins over stale UI hints.** `ClientHints` (budget slider,
  household size, etc.) supplement natural-language extraction; if the two
  disagree, the user's typed message wins and a `notice` event explains the
  override.
- **Money is `Decimal`, always.** Never `float`, in memory or on the wire.
- **Boundaries are protocols, not AWS SDK calls.** `ModelClient` and
  `PriceRepository` are `Protocol`s. Every node depends on those, never on
  `boto3` directly, which is what lets the whole graph run and be tested
  with no AWS account and no network.
- **Model routing is explicit policy data.** Nodes request a task and a
  capability/tier need, never a model id. `src/models/registry.py` resolves the
  task against `config/models.json` using per-task preference and each model's
  declared support for tool use, prompt caching, and structured output.
  `BedrockModelClient` therefore adapts call shape instead of assuming every
  Bedrock model behaves like Claude. A model is eligible for pilot routing only
  after a task-specific scorecard reaches the 90% floor; Pilot Task 7 disables
  currently unqualified entries and moves the catalogue toward SSM.

## Repository layout

Top level, with the files worth knowing about by name. `AGENTS.md` carries the
architecture in detail; this is the map, not the territory.

```
src/
  schemas/contract.py      The wire contract — single source of truth
  graph/                   LangGraph state machine
    build.py                 Topology; the shape IS two of the invariants
    state.py                 GroceryState — what every node reads and writes
    dietary.py               Exclusion term -> category, or an honest refusal
    feasibility.py           Is this budget possible at all (see docs/OPEN-REVIEW-*)
    nodes/                   intent, plan, prose, retrieval, routing, terminals
  models/                  Model plane: base protocol, registry, bedrock,
                           scripted stand-in, guardrail tagging
  prompts/                 System/user prompts and the price-free draft schemas
  retrieval/               PriceRepository protocol; fixture and DynamoDB impls
  store/                   Idempotency: in-memory and DynamoDB
  observability/           Telemetry protocol, instrumented wrappers, Powertools
  runner.py                ChatRequest -> graph -> validated ChatResponse
  handler.py               Lambda entrypoint; no path out without a valid body

ingestion/                 Price ingestion: sources, normalise, handler, and
                           lineage_b.py — the data team's 3,000-row catalogue
                           transformed into the serving schema, with a
                           fail-closed dietary re-classifier. Deployed to
                           ap-southeast-2; live retailer acquisition stays
                           gated on ACQUISITION-RISK.md §8
Philip_demo/               Seven runnable feature demos, offline, no AWS.
                           run_all.py exits non-zero if any drifts from the code
tests/                     Fast, deterministic, no AWS or network
evals/                     Scored golden sets; cases/*.json are the sets
scripts/                   Fixture generation, dev server, Lambda build, AWS
                           appliers (guardrail, IAM, alarms, state machine),
                           check_quotas.py, and the pre-commit hook
config/                    Config-as-data, applied rather than hardcoded:
                           models, guardrail, feasibility, freshness, regions,
                           alarms, IAM, ingestion, product synonyms, store
                           locations. Each file carries its own reasoning
samples/                   Example payloads; validate.py checks them in CI
fixtures/products.json     Generated seed data: 3 chains, 6 store locations,
                           26 products, 152 records, deliberately messy naming
datasets/                  Recipe and product source data plus its schema notes
docs/                      Deployment record, CI gate health, throughput
                           ceiling, an open review, ADRs — see Further reading
infra/                     AWS CDK (TypeScript). Design docs (infra/docs/00-09)
                           and a reviewable scaffold skeleton now exist; the
                           stacks are stubs — no CDK stack deployed, though the
                           service itself IS deployed by hand (Pilot Tasks 9-12)
```

## Progress to date, and what it cost

**Skippable.** The table above is what exists; this is *why it looks like that*
— the defects found, the reasoning behind each decision, and the several
occasions a number turned out to be measuring something other than what it
claimed. Read it before changing any of this, and not before.

✅ built and evidenced   🚧 measured but deliberately not gated

### The contract and the graph

- ✅ **Contract v1.0** (`src/schemas/contract.py`, `CONTRACT-v1.md`) — event
  shape, grounding invariants, request/response schemas. Additive pilot
  hardening continues; breaking changes require v2.
- ✅ **Graph topology** wired end to end in LangGraph, including the bounded
  repair loop. Two of the three invariants are properties of its shape.
- ✅ **Intent classification** — model-backed with a keyword fallback,
  constraint extraction, message-wins hint reconciliation, injection defences.
- ✅ **Multi-item price queries** — every item asked about is resolved and
  compared, with a `no_data` event per unresolved item rather than a silent
  answer about only the first.
- ✅ **Lambda handler** — Guardrail interventions, bad input, model errors,
  grounding violations and escaped failures all map to contract-valid bodies.
  There is no path out without one.

### Money, and why it took several passes

- ✅ **Prices cannot originate from the model.** Structural, three ways: the
  topology, a draft schema with no price field, and assertions.
- ✅ **Deterministic cost assembly** — every dollar figure is computed in
  Python from retrieved records.
- ✅ **Two totals, because they are different questions.** `total_nzd` is value
  consumed at fractional pack multipliers; `payable_total_nzd` is what the
  shopper hands over, whole packs at shelf price. `within_budget` follows the
  second. It used to follow the first, so plans reported fitting a $60 budget
  with a $65.01 shopping list — including in the published sample.
- ✅ **Whole packs round up per product.** 1.2 packs costs two. A basket that
  counted each product once shipped a plan consuming $221 of food against a
  $40 budget.
- ✅ **And the arithmetic is now verified against the citations, not against
  itself.** The old check confirmed four sums agreed with each other — which a
  consistently wrong line cost also satisfies, and which said nothing at all
  about basket totals. Every figure is now re-derived from the cited price:
  line cost, pack counts aggregated across meals and rounded up once, and each
  basket at shelf price. `Ingredient` carries `packs` so the plan can audit
  itself.
- ✅ **Candidates are pre-filtered to the budget**, so a price-blind model can
  only choose from a set it can afford — the only lever available when the
  model never sees a price.
- ✅ **Impossible requests are refused before generation** (`graph/feasibility.py`).
  Pre-filtering makes affordability true by construction, so it stopped being
  evidence the request was sane; "feed 5 people for 7 days on $15" was
  returning a tidy plan.
- ✅ **Prose is checked for money twice** — the model's template, and the
  rendered string, since placeholders expand between them and only the second
  reaches the user. Both degrade: the sentence is dropped, the cited table
  ships.
- ✅ **A meal name is model-authored text too.** `PlanDraft` has no price
  field, so a price cannot reach a *structured* slot — but the meal name, the
  ingredient name and the quantity are free text the model writes and the user
  reads, and nothing checked them. A plan naming a meal `Budget Pasta — only
  $4.99 a head` with an ingredient `Butter (was 7.50, now 5.00)` passed every
  assertion in the system, shipping a fabricated "was" price. The prompt had
  said "NEVER state a price" since the beginning; that is the kind of promise
  this codebase replaces with a check. Now a validation error, repaired
  through the bounded loop, and refused honestly if repair cannot fix it.

### Honest failure

- ✅ **Each terminal path says something true.** An unreachable model, a
  genuinely unaffordable basket, a draft that never validated, and a dietary
  term we cannot verify are four different facts with four different codes and
  correct `retryable` values. They used to collapse into `BUDGET_INFEASIBLE`,
  which told users whose Bedrock call had failed to raise their budget.
- ✅ **Dietary exclusions fail closed** — mapped from a reviewable table or
  refused, restated on every regeneration, verified against retrieved products
  rather than against what the model claims.

### Model plane

- ✅ **Task-based routing** (`models/registry.py`, `config/models.json`) —
  capability-aware, cost-aware, config-as-data.
- ✅ **`ScriptedModelClient`** — deterministic stand-in that lets the whole
  graph, including induced failures, run with no AWS account.
- ✅ **Bedrock adapter** verified live against Nova Lite, Nova Pro, Claude
  Haiku 4.5 and Claude Sonnet 4.5 in `ap-southeast-2`.
- ✅ **A model cannot serve a task it was never scored on.** `enabled` used to
  mean "listed in the config": every model carried `enabled: true` regardless of
  evidence, and Claude Sonnet was second preference for `generate_plan` while
  being documented as excluded on latency — p90 19.9s against a 20s client
  timeout. A Nova Pro outage failed over to it. Worse, `route()` falls back to
  the cheapest enabled model at the tier, and Sonnet declared both tiers, so it
  was reachable from every task. It is now disabled with the reason recorded,
  scorecards live in `config/models.json` as data, and a test fails the build if
  any routable model lacks qualifying evidence for the task it would serve.
- ✅ **Scorecards, measured 2026-08-29 against guardrail version 2.** Intent:
  Nova Pro 100.0% (28/28), Claude Haiku 4.5 96.4% (27/28), Nova Lite 92.9%
  (26/28). Meal-plan invariants, paced, three clean reps each: Nova Pro 100%,
  Claude Haiku 4.5 100%. All clear the 90% floor.
- ✅ **Prose and repair are now measured**, closing the two tasks that were
  routed with nothing scoring them. `evals/run_prose.py` (11 cases) asks whether
  a model can follow the prose protocol at all — the node degrades silently on
  any breach, so a model that cannot produces a product with no prose in it and
  no error to show for it. Nova Lite 100%, Nova Pro 100%, Claude Haiku 4.5
  90.9%. `evals/run_repair.py` (6 cases) scores the repair pass, separating
  budget repairs from defect repairs because the graph feeds it both and they
  need different prompts. Both are gated in CI and the pre-commit hook.
- 🚧 **Repair is measured but not gated on model choice.** All three routable
  models scored 83.3%, each failing a *different* case — variance on a six-case
  suite where one failure is 16.7 points, not a weakness any of them has. A 90%
  floor there would fail every model for noise; a lower one would be a number
  picked to fit the answer. Recorded in `scorecards._measured_not_gated` with
  that reasoning. Expand the case set before gating.
- 🚧 **Subjective prose quality is still unmeasured** (legacy 5.6). Everything
  the prose suite checks is a rule violation, deliberately: an LLM judge would
  put a non-deterministic scorer inside a suite whose value is being
  deterministic.
- ✅ **Guardrail verified live: 13/13 must-block, 9/9 must-allow**, exit 0,
  against `b1xezpqe04kx` **version 2** on 2026-08-29. Getting there took fixing
  the harness first — `--model` did not pin, `OUT_OF_SCOPE` counted as a block,
  and a must-block miss exited zero, so no result it produced was quotable.
  The run then found a real over-block: the foraging topic was defined as an
  ingredient list, so `truffle oil`, `mushrooms` and `button mushrooms` were all
  refused. Version 2 scopes it to the act of gathering. **A bare "price of
  mushrooms" is still refused and remains open** —
  [`docs/LIVE-EVAL-RUNBOOK.md`](docs/LIVE-EVAL-RUNBOOK.md) §8.5.

### Data and storage

- ✅ **Fixture repository** with deliberately inconsistent cross-store naming,
  to stress the retrieval normaliser.
- ✅ **Location and freshness are enforced in the repository, not after it.**
  A radius filter and a capture-date filter are parameters of
  `cheapest_for_product` and `candidates_for_budget`, applied *before* the
  limit. Filtering afterwards would return nothing for a product whose five
  cheapest rows are all out of radius or out of date — and the graph reads
  nothing as "I don't have price data for that", about a product stocked fresh
  down the road. Both were previously declared and unread: a shopper in
  Wellington got Auckland prices.
- ✅ **Stale-only data is refused, not presented.** `STALE_DATA` naming the
  capture date, retryable. The claim is not "here is a price" but "here is the
  *cheapest* price", and that comparison can be wrong in a way a stale price
  alone is not, because the winner changes when a special rotates. Freshness is
  judged against an injectable date, so the committed fixture snapshot does not
  rot into staleness on a day nobody chose.
- ✅ **DynamoDB tables** created and seeded (`grocery-products-dev` with PITR,
  `grocery-idempotency-dev` with TTL); the price repository passes its shared
  live contract suite.
- ✅ **Schema and migration plan** documented (`DYNAMODB-SCHEMA.md`).
- ✅ **Idempotency** replays completed turns, scopes keys by session, detects
  in-flight work and rejects reused ids with a different payload. The
  fingerprint is taken over the *validated request*, not the raw HTTP bytes, so
  whitespace, key order, omitted-versus-null — and trailing zeros on money,
  since `30` and `30.00` are the same budget — cannot turn a correct retry into
  a 400 the client is forbidden to retry.
- ✅ **A superseded invocation cannot overwrite a newer claim.** Every claim
  carries an owner token, rotated on acquire *and* on takeover, and
  `complete()`/`release()` are conditional on it. Without that, an invocation
  that stalled past the timeout and woke up after another had taken over could
  write its older answer over the newer claim — served to the next retry as
  cached truth — or delete the newer marker and let a third invocation start
  the same turn. Verified against the live table, not just in memory.

### Tests, evals and CI

- ✅ **763 passing, 31 skipped** — classification, extraction, arithmetic,
  grounding, injection resistance, bounded repair, routing, idempotency,
  Guardrail propagation, dietary fail-closed behaviour, handler mappings, and
  the CI workflow's own wiring.
- ✅ **Eval harnesses are separate from unit tests**, and refuse to report a
  score they did not measure: a run where the model was never reached aborts,
  and `--min-pass-rate` returns *inconclusive* rather than pass or fail. They
  pace requests to the account's quota by default, because an unpaced run
  measures the quota rather than the model.
- ✅ **Scripted baselines** — 76.7% intent, 100% meal-plan invariants, 7/7
  Guardrail must-allow structure.
- ✅ **Seven runnable demos** (`Philip_demo/`), offline; `run_all.py` exits
  non-zero if any has drifted from the code it describes.
- ✅ **CI** — lint, format, types, tests, contract and grounding validation,
  dependency and secret scanning, guardrail and alarm policy validation, eval
  floors, and the Lambda package build. Five jobs behind one required
  `summary` check, all credential-free, and a test asserts every job is
  actually wired into it.
- ✅ **Lambda deployment archive** — manylinux wheels regardless of host OS,
  unused and runtime-provided packages excluded, size measured against a 240MB
  budget, and the archive's importability verified rather than assumed. ~30MB
  unzipped.

### Deployed and observable

- ✅ **Observability** (Req 12.1–12.2) — structured logs correlated by
  `session_id`, X-Ray subsegments around retrieval and every model call
  including each repair attempt, EMF metrics for latency, tokens, cache reads,
  repairs, guardrail interventions, idempotent replays and contentless turns.
  Powertools is imported by exactly two files and a test walks the import
  graph to keep it that way. Logs are asserted to carry no message text,
  location or dietary information.
- ✅ **Throughput ceiling measured**: 10 meal-plan turns/minute, 5 when repair
  fires, bound by a Nova Lite quota that **cannot be raised by request**.
  Accepted for workshop scale; `scripts/check_quotas.py` derives it live
  rather than trusting this paragraph.

## What is left, and in what order

Everything below is **not built**. Nothing here is a current capability.

**The critical path is reproducibility and operational evidence, not first
deployment.** A running service already exists — see *Where this is right now*
— but it was created imperatively, serves code two days older than `main`, and
has no dashboards, alarms, budget or latency baseline attached. So Tasks 9–12
now mean *bring the running thing under IaC and make it observable*, which is a
different job from standing it up. Latency and cost figures quoted anywhere in
this repository are still laptop measurements: nothing has been measured
against the deployed endpoint under load.

1. ~~**Task 8 — local read-only MCP.**~~ **Done 2026-08-30.** `src/mcp/`, two
   coarse tools over stdio JSON-RPC with no new dependency, default-off, rate
   and session capped, privacy-safe audit, and parity asserted against the same
   `lambda_handler` API Gateway invokes. Run it with
   `MCP_ENABLED=1 python scripts/mcp_server.py`.
2. **Tasks 9–12 — CDK, service plane, deployment, operations.** Adopt the
   existing tables *and the existing API, Lambda, alias, roles and schedule*
   without replacement; zip Lambda on a published SnapStart alias; REST
   controls, SSM, strict IAM and CORS; then dashboards, alarms, Budgets, X-Ray
   and the latency/cost baselines everything else is waiting on. Design
   documentation and a reviewable scaffold already exist under
   [`infra/`](infra/) — design only, no stack implemented. Note the adoption
   surface is larger than `infra/docs/00` says: that table lists the API and
   alias as "not yet", and they exist.
3. **Task 13 — controlled ingestion.** EventBridge and Step Functions over
   fixture or recorded adapters, with provenance, partial-failure and
   dead-letter behaviour. **No live retailer traffic**, which stays gated on
   [`ACQUISITION-RISK.md`](ACQUISITION-RISK.md) §8.
4. **Task 15 — recipe catalogue.** Models select recipe ids and product
   citations; code owns scaling, safety and totals. The catalogue, its dietary
   classification and a coverage gate are built (`src/recipes/`). **The planner
   is deliberately not wired: zero of the 175 recipes have every ingredient
   priceable** against the product catalogue (best 75%, median ~12%), so a plan
   built from one would state a payable total derived from a fraction of the
   shopping list. `python scripts/check_recipe_coverage.py --missing 20` names
   what is absent; a forcing test fails when the data becomes sufficient.
5. **Task 16 — release gates.** The integrated run of every gate above.

**Requires mentor approval before starting** (ADR 0002, still proposed):

- **Task 8 extension — AgentCore Gateway** over the same coarse tools with
  Identity, Policy, WAF and Cognito. Never a bypass around LangGraph.
- **Task 14 — isolated AgentCore Runtime reviewer** over capped sanitised
  ingestion snapshots, emitting cited schema-checked findings for deterministic
  validation and human approval. No shopper PII, no writes, no publication, no
  shopper-path authority.

**Gated until there is evidence to justify them:** cross-Region inference
profiles; recipe/catalogue Knowledge Bases (never price authority); advisory
Automated Reasoning; Bedrock Model Evaluation and AgentCore Evaluations as
companions to the local suites rather than replacements; WebSocket delivery;
remote MCP; separate environments. AgentCore Memory needs Cognito, consent, TTL,
export and deletion, and a privacy review first, and never holds prices. Moving
the shopper meal path onto AgentCore Runtime is a separate contingency for a p99
above ~25 seconds, and needs its own approval.

### The learning objective, and its constraint

The project exists partly to gain hands-on experience with a broad set of AWS
services — Bedrock and AgentCore especially — **without collecting services for
their own sake**. Every service has to state a product purpose, a bounded scope,
acceptance evidence, security and cost controls, an owner, and a
rollback/removal criterion. None of it may weaken the grounding, dietary,
arithmetic, Guardrail or honest-failure invariants. Where a managed service
would replace a local gate, it accompanies it instead.

## Running it locally

No AWS account or network access is required for any of the following.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt -r requirements-dev.txt

# Two one-off git settings. The first runs the gate before you commit rather
# than after CI does; the second keeps `git blame` off the 2026-08-29 reformat.
git config core.hooksPath scripts/hooks
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

Then, to see it work:

```bash
python Philip_demo/run_all.py   # seven feature demos, offline, ~10 seconds
```

And to check it:

```bash
pytest                     # 763 passing, 31 skipped
python validate.py         # samples/*.json against the contract
ruff check . && ruff format --check .
python evals/run_intent.py       # 76.7% scripted baseline
python evals/run_meal_plan.py    # 100% scripted invariant baseline
python evals/run_guardrail.py    # 7/7 scripted must-allow structure only
```

`AGENTS.md` has the full command reference, including the AWS appliers and the
Lambda build. Deliberately not repeated here: the two lists drifted apart once
already, and the eval baseline in this file was wrong for a day because of it.

### Running an eval against a live model

`--model` / `--compare` call Bedrock, which needs three things in the
environment. The third is the one people miss:

```bash
export AWS_PROFILE=grocery
export AWS_REGION=ap-southeast-2
export BEDROCK_GUARDRAIL_ID=b1xezpqe04kx   # grocery-assistant-guardrail-dev
export BEDROCK_GUARDRAIL_VERSION=2         # pin the numbered version, not DRAFT

python evals/run_meal_plan.py --compare claude-sonnet nova-pro
```

`REQUIRE_GUARDRAIL` defaults to `1`, so a missing `BEDROCK_GUARDRAIL_ID` makes
every model call fail closed — deliberately, because silently running
generation without content safety is the worse outcome. List the deployed
guardrail with:

```bash
aws bedrock list-guardrails --region ap-southeast-2 \
  --query 'guardrails[].{Id:id,Name:name,Status:status}' --output table
```

The harness now aborts rather than reporting a pass rate when the model was
never reached, and `--min-pass-rate` returns exit code `2` (inconclusive, not
pass or fail) if any case failed upstream. Before that guard existed, an unset
`BEDROCK_GUARDRAIL_ID` produced an identical, entirely plausible 27% for two
different models — a measurement of nothing.

Anthropic models additionally need the account's one-time Anthropic use case
form submitted (Bedrock console → **Test → Playground** → pick a Claude model
→ Run). It is account-wide, not per-model, and the retired *Model access* page
no longer offers it. Check with
`aws bedrock get-use-case-for-model-access --region ap-southeast-2`.

**On reading the numbers:** this suite is 11 cases and the models are
non-deterministic. Repeat runs of the same model have differed by ~18 points,
which is wider than the gap between models. A single run cannot rank two
models; repeat each before concluding anything.

To exercise the Lambda handler over real HTTP (what the frontend team should
point at before the AWS account exists):

```bash
python scripts/dev_server.py
# in another shell:
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
     -d '{"version":"1.0","session_id":"sess-local01",
          "turn_id":"turn-local01","message":"cheapest butter"}'
```

Runs on fixtures + the scripted model, so responses are deterministic and no
AWS credentials are needed. Setting `USE_DYNAMODB=1` or `USE_BEDROCK=1`
switches individual dependencies to their AWS-backed implementations (requires
valid AWS credentials in the environment — SSO profile or env vars). Both
adapters are implemented and verified; the default remains fixtures + scripted
for offline development.

The dev server emits the same structured logs and EMF metric records the
Lambda does — they print to stdout, which is exactly where CloudWatch reads
them from in production. X-Ray tracing switches itself off outside Lambda, so
no daemon is needed. Namespacing is configurable via `POWERTOOLS_SERVICE_NAME`
and `POWERTOOLS_METRICS_NAMESPACE`; `LOG_LEVEL` sets log verbosity.
`POWERTOOLS_LOGGER_LOG_EVENT` is deliberately ignored — see design.md §12.4.

## Further reading

**Cold start:** [Where this is right now](#where-this-is-right-now) above, then
this one file. That is enough to work; everything else is looked up when a
specific question arises.

- [`AGENTS.md`](AGENTS.md) — the working agreement: the three invariants, the
  conventions, the full command reference, eval discipline, and a current-state
  snapshot including live model evidence. **Read before writing code here.**

**Which file answers which question**

| Question | File |
|---|---|
| What am I allowed to change, and what must never break? | `AGENTS.md` |
| What does the API return? | `CONTRACT-v1.md`, `samples/` |
| How does the frontend consume it? | `FRONTEND-INTEGRATION.md` |
| What exists in AWS right now? | `docs/ARCHITECTURE.md` |
| What should I build next, and how? | `.kiro/specs/.../tasks.md`, `infra/docs/` |
| Why is this number what it is? | `config/*.json` — each carries its own reasoning |
| Why was it done this way? | `.kiro/specs/.../design.md` §8, ADRs |

**Building against it**

- [`CONTRACT-v1.md`](CONTRACT-v1.md) — the wire contract with full
  request/response examples and the error-code table.
- [`FRONTEND-INTEGRATION.md`](FRONTEND-INTEGRATION.md) — how to consume the
  event stream, which totals to render, and the failure modes worth handling
  distinctly.
- [`samples/`](samples/) — payloads `validate.py` checks in CI.
- [`Philip_demo/`](Philip_demo/) — seven runnable demos of the features,
  offline, with the run instructions in each file.

**How it is deployed and how it behaves**

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the deployment record: what
  exists in `ap-southeast-2`, its identifiers, the IAM shapes that took two
  attempts, defects that only appeared once deployed, and the measured
  throughput ceiling. Read before assuming a manual test result is fresh.
- [`docs/THROUGHPUT-AND-SCALING.md`](docs/THROUGHPUT-AND-SCALING.md) — the
  request-per-minute ceiling, why it was accepted for workshop scale, and the
  two options for production with their costs. Read before assuming a Bedrock
  quota increase is available; for the models in the route, it is not.
- [`DYNAMODB-SCHEMA.md`](DYNAMODB-SCHEMA.md) — current and planned tables,
  candidate-query options, and the CDK adoption sequence.
- [`infra/`](infra/) — the Infrastructure-as-Code plan: design docs
  (`infra/docs/00-09`), a reviewable CDK **scaffold skeleton**, and
  [`docs/adr/0003`](docs/adr/0003-infrastructure-as-code-and-resource-adoption.md).
  Read before starting Pilot Tasks 9–12 — it says what to build and in what
  order. Design/skeleton only — no CDK stack is deployed from it yet, but the
  service plane it describes already exists, created by hand; see
  `infra/docs/08` §10 for the adopt-or-replace decision that follows.

**Judgement calls, open and closed**

- [`docs/OPEN-REVIEW-head-terms.md`](docs/OPEN-REVIEW-head-terms.md) — **open,
  and wants somebody who shops these stores.** Which product a one-word query
  like "cheapest butter" should return, when the catalogue holds fourteen
  butters. Fifteen minutes, no code reading. Lower stakes than the review below
  — a wrong answer here is unhelpful rather than a refusal — but these are the
  words a demo audience types first.
- [`docs/OPEN-REVIEW-min-grams-per-person-day.md`](docs/OPEN-REVIEW-min-grams-per-person-day.md)
  — **open, and wants a human.** The one figure in the planning path that is a
  judgement rather than derived from the catalogue. Written for a reviewer who
  will not read code.
- [`docs/LIVE-EVAL-RUNBOOK.md`](docs/LIVE-EVAL-RUNBOOK.md) — the procedure for
  the credentialed evaluation session, and the results of the one run on
  2026-08-29. **Read before running anything against Bedrock**: every trap it
  lists has already happened here, and it is the checklist for the next run
  whenever the Guardrail policy or the model catalogue changes.
- [`docs/CI-GATE-HEALTH.md`](docs/CI-GATE-HEALTH.md) — where the gate can go
  red for reasons unrelated to your change. Five of six entries are resolved
  and kept for their reasoning; the open one is that the eval case counts are
  too small.
- [`ACQUISITION-RISK.md`](ACQUISITION-RISK.md) — the terms-of-service
  assessment gating live price acquisition. §8 is the condition list. **Read
  before touching acquisition.**
- [`docs/adr/`](docs/adr/) — 0001 the deterministic core, 0002 the staged
  AgentCore proposal (proposed; mentor approval required).

**Requirements and locked decisions**

- [`.kiro/specs/grocery-orchestrator/`](.kiro/specs/grocery-orchestrator/) —
  numbered requirements, the design doc (§8 records what was decided against
  and why — read it before proposing an alternative), and task-by-task status.
- [`.kiro/steering/`](.kiro/steering/) — locked technical, security and
  AI-quality decisions, including the formatting policy.
- [`docs/CLAUDE-CODE-PERMISSIONS.md`](docs/CLAUDE-CODE-PERMISSIONS.md) — the
  allowlist audit for agent tooling in this repo.
