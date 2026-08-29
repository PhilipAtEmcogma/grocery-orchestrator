# Smart Grocery & Meal Budget Assistant — Orchestrator

A conversational assistant for New Zealand shoppers that answers "what's the
cheapest butter near me?" and "feed a flat of 3 for under $60 this week"
questions by grounding every answer in real, retrieved supermarket price
data. Built for the AWS AI Innovation Mentorship Workshop as a reference
implementation of the **retrieve-then-generate** pattern on Amazon Bedrock.

This repository is the **orchestrator and AI application layer**. The reference
workflow classifies a turn, retrieves prices, invokes Bedrock through a
task-based model plane, and returns validated events. DynamoDB adapters,
products/idempotency tables, Nova calls, and a numbered Guardrail have limited
live evidence in `ap-southeast-2`.

It is **not yet a deployable production pilot**. Pilot Tasks 2–3 corrected
citation construction, citation ordering, money-free comparison/prose labels,
samples, and offline Guardrail intervention propagation. Exact retrieved-record
and value equality, qualifying live Guardrail evaluation, location/freshness,
production fail-closed startup, CDK/API controls, and deployed SLOs remain
open.

The project also has an explicit learning objective: gain hands-on experience
with broad relevant AWS services, especially Bedrock and AgentCore, without
collecting services for their own sake. Every service needs a product purpose,
bounded scope, acceptance evidence, security/cost controls, and a
rollback/removal criterion. It cannot weaken grounding, dietary, arithmetic,
Guardrail, or honest-failure invariants.

Stages are explicit: the deterministic Lambda shopper workflow is implemented;
local read-only MCP is planned first; AgentCore Gateway over the same coarse
complete-application operations and a separately deployed Runtime data-quality
reviewer are proposed and require ADR 0002 mentor approval. Bedrock Model
Evaluation and AgentCore Evaluations are proposed companions to local tests,
not replacements. No proposed managed service is a current capability.

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
  v
retrieve_prices            <-- the ONLY source of prices
  |--- no citations -----> emit_no_data -------------> finalise
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

ingestion/                 Price ingestion: sources, normalise, handler.
                           Deployed to ap-southeast-2; live retailer acquisition
                           stays gated on ACQUISITION-RISK.md §8
Philip_demo/               Seven runnable feature demos, offline, no AWS.
                           run_all.py exits non-zero if any drifts from the code
tests/                     Fast, deterministic, no AWS or network
evals/                     Scored golden sets; cases/*.json are the sets
scripts/                   Fixture generation, dev server, Lambda build, AWS
                           appliers (guardrail, IAM, alarms, state machine),
                           check_quotas.py, and the pre-commit hook
config/                    Config-as-data, applied rather than hardcoded:
                           models, guardrail, feasibility, alarms, IAM, ingestion
samples/                   Example payloads; validate.py checks them in CI
fixtures/products.json     Generated seed data: 3 chains, 6 store locations,
                           26 products, 152 records, deliberately messy naming
datasets/                  Recipe and product source data plus its schema notes
docs/                      Deployment record, CI gate health, throughput
                           ceiling, an open review, ADRs — see Further reading
infra/                     AWS CDK (TypeScript). Design docs (infra/docs/00-09)
                           and a reviewable scaffold skeleton now exist; the
                           stacks are stubs — nothing deployed (Pilot Tasks 9-12)
```

## Progress to date

✅ built and evidenced   🚧 built but not yet qualifying evidence

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
- 🚧 **Two tasks are routed with nothing measuring them**, named in
  `scorecards._unscored_tasks` rather than left implicit: `repair_plan` is
  exercised inside the meal-plan eval but never scored alone, and
  `generate_prose` has no eval at all (legacy 5.6). Prose is bounded by
  construction — money rejected, placeholders verified, cheapest claim checked
  against retrieved records, degrading to the structured payload on failure —
  which is not the same as being measured.
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
- ✅ **DynamoDB tables** created and seeded (`grocery-products-dev` with PITR,
  `grocery-idempotency-dev` with TTL); the price repository passes its shared
  live contract suite.
- ✅ **Schema and migration plan** documented (`DYNAMODB-SCHEMA.md`).
- 🚧 **Idempotency** replays completed turns, scopes keys by session, detects
  in-flight work and rejects reused ids with a different payload. Canonical
  request hashing and stale-owner fencing are Pilot Task 6.

### Tests, evals and CI

- ✅ **511 passing, 31 skipped** — classification, extraction, arithmetic,
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

## Not yet built

Planned/proposed items are not current capabilities:

- **Core follow-ups (Pilot Tasks 4–7):** clarification; location/freshness;
  idempotency ownership/candidate access; qualified SSM model routing. Pilot
  Task 2 is closed; Task 3's harness controls are closed and only its live
  result remains — see [`docs/LIVE-EVAL-RUNBOOK.md`](docs/LIVE-EVAL-RUNBOOK.md).
- **Local read-only MCP first** (Pilot Task 8), proving coarse operation
  schemas, caps, audit, direct-service parity, and disable behavior.
- **Proposed AgentCore Gateway hybrid** (Task 8 extension; ADR 0002 mentor
  approval required): AgentCore Identity/Policy plus WAF and Cognito or approved
  workload identity over the same coarse tools, never around LangGraph.
- **Proposed isolated AgentCore Runtime reviewer** (Pilot Task 14; ADR 0002
  mentor approval required): capped sanitised ingestion snapshots, cited
  schema-checked findings, deterministic post-validation, human approval, no
  shopper PII, writes, publication, or shopper-path authority.
- **Proposed managed evaluation companions:** Bedrock Model Evaluation and
  AgentCore Evaluations with versioned S3 datasets/results and reproducible
  model/prompt/evaluator/trace/cost provenance; local tests/evals remain gates.
- **Controlled ingestion** (Pilot Task 13): EventBridge/Step Functions and,
  where justified, filtered DynamoDB Streams -> SQS/DLQ review triggers plus
  SNS operator/approval notifications. No live retailer traffic.
- **CDK/service/operations** (Pilot Tasks 9–12): adopted DynamoDB resources,
  zip Lambda/SnapStart alias, REST controls, SSM, strict IAM/CORS, CloudWatch,
  X-Ray, Budgets, alarms, WAF/Cognito before owned/public managed surfaces, and
  encrypted versioned S3 artefacts. Design documentation and a reviewable CDK
  scaffold skeleton now exist under `infra/` (see `infra/README.md` and
  `infra/docs/`); they are design/skeleton only — no stack is implemented or
  deployed.
- **Catalogue** (Pilot Task 15): meals table and curated recipes. A Knowledge
  Base may be evaluated for cited recipe/catalogue knowledge only, never price
  authority; Automated Reasoning is advisory where supported.
- **Gated later:** cross-Region inference profiles only with measured purpose
  and residency/quality/cost evidence; AgentCore Memory only after Cognito,
  consent, TTL, export/deletion, privacy review, and never for prices;
  WebSocket, remote MCP, live acquisition, and separate environments.

Moving the shopper meal path to AgentCore Runtime remains a separate p99 above
approximately 25 seconds contingency after mitigations and separate mentor
approval. Gateway or reviewer approval does not approve it. Guardrail version
`1` exists only with basic invocation evidence; qualifying live policy results
and CDK adoption remain open.

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
pytest                     # 511 passing, 31 skipped
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

**Start here if you are picking this repo up cold.**

- [`AGENTS.md`](AGENTS.md) — the working agreement: the three invariants, the
  conventions, the full command reference, eval discipline, and a current-state
  snapshot including live model evidence.

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
  order. Design/skeleton only; nothing is deployed from it yet.

**Judgement calls, open and closed**

- [`docs/OPEN-REVIEW-min-grams-per-person-day.md`](docs/OPEN-REVIEW-min-grams-per-person-day.md)
  — **open, and wants a human.** The one figure in the planning path that is a
  judgement rather than derived from the catalogue. Written for a reviewer who
  will not read code.
- [`docs/LIVE-EVAL-RUNBOOK.md`](docs/LIVE-EVAL-RUNBOOK.md) — **the three
  pieces of live evidence still outstanding**, batched into one credentialed
  session. Read before running anything against Bedrock: every trap it lists
  has already happened here.
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
