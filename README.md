# Smart Grocery & Meal Budget Assistant — Orchestrator

A conversational assistant for New Zealand shoppers that answers "what's the
cheapest butter near me?" and "feed a flat of 3 for under $30 this week"
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
and value equality, whole-response runtime money enforcement, qualifying live
Guardrail evaluation, location/freshness, payable totals, production fail-closed
startup, CDK/API controls, and deployed SLOs remain open.

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
  `Citation`. Pilot Task 2 added citation-before-use and basic source-shape
  checks using configured table, `store_key`, and normalized `product_key`.
  `assert_grounded()` still lacks immutable retrieved-record context and cannot
  independently prove exact key/value equality; wrong-key and altered-value
  controls remain open.
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

```
src/
  schemas/contract.py     Frontend <-> orchestrator wire contract (single source of truth)
  graph/
    build.py              Assembles the LangGraph state machine above
    state.py              GroceryState — what every node reads/writes
    nodes/
      __init__.py          Retrieval, validation, repair-loop bookkeeping, routing, finalise
      intent.py            Intent classification + constraint extraction node
      plan.py              Meal-plan generation + deterministic cost assembly node
      prose.py             Placeholder-based explanatory text; renderer emits
                            money-free product/store labels after Pilot Task 2
  models/
    base.py                ModelClient protocol + ModelTier policy
    registry.py              Reads config/models.json; routes a task to a concrete
                              model by tier, preference order and capability
    bedrock.py              Bedrock Converse implementation; live-verified with Nova
                              Lite/Pro, pending langchain-aws alignment and production
                              model qualification
    scripted.py              Deterministic stand-in model used by all current tests
    guardrail.py             Per-request input tagging so the PROMPT_ATTACK filter
                              actually evaluates untrusted content
  prompts/
    intent.py               System/user prompt + schema for intent classification
    meal_plan.py             System/user prompt + price-free draft schema for meal planning
    prose.py                 System/user prompt + placeholder-only schema for explanatory text
  retrieval/
    base.py                 PriceRepository protocol + PriceRecord type
    memory.py                Fixture-backed repository used for all local dev and tests
    dynamo.py                DynamoDB-backed repository — implemented, passing 31 contract tests
  store/
    idempotency.py           Session-scoped dedup: acquire/complete/release, in-memory
    dynamo_idempotency.py    DynamoDB-backed idempotency store — implemented, verified live
  observability/
    base.py                 Telemetry protocol + no-op default, and the three
                              functions that decide what may appear in a log (Req 11.5)
    instrumented.py          Repository/model decorators that emit spans and
                              per-model latency without the graph importing anything
    powertools.py            The only module that imports aws-lambda-powertools
  runner.py                  ChatRequest -> graph -> validated ChatResponse
  handler.py                  Lambda entrypoint (API Gateway proxy integration) — every
                               failure path maps to a contract-valid ErrorEvent, never a bare 500

tests/                      Fast, deterministic, no AWS/network — see Running it locally
evals/
  run_intent.py              Scores classify_intent against evals/cases/intent.json:
                              deterministic pass/fail, since intent has a correct answer
  run_meal_plan.py            Checks meal-plan output against evals/cases/meal_plan.json:
                              hard invariants (budget, exclusions, grounding) scored
                              pass/fail, plus reported-not-scored quality metrics
                              (budget utilisation, ingredient reuse, meal variety)
  cases/*.json                 The golden sets each harness runs against
scripts/
  generate_fixtures.py      Generates fixtures/products.json (deliberately messy product naming)
  dev_server.py              Stdlib-only local HTTP server wrapping lambda_handler, so the
                              frontend team can integrate against real responses pre-AWS
  apply_guardrail.py         Creates/updates the Bedrock Guardrail from config/guardrail.json;
                              --dry-run validates the policy with no AWS call (runs in CI)
  build_lambda.py            Builds build/lambda.zip: manylinux wheels regardless of host OS,
                              unused-transitive/runtime-provided packages excluded, size and
                              import verified — see Task 10.1 in .kiro/specs
validate.py                  Validates samples/*.json against the contract; runs in CI
samples/                     Example request/response payloads used by validate.py
fixtures/products.json       Generated seed price data (six NZ stores, ~26 products)
config/
  models.json                Model catalogue: ids (by env var), capabilities, cost,
                              and per-task routing preference — read by models/registry.py
  guardrail.json              Bedrock Guardrail policy — read by scripts/apply_guardrail.py
CONTRACT-v1.md               Human-readable version of the wire contract, for the frontend team
DYNAMODB-SCHEMA.md           Current products/idempotency schema plus planned meals,
                              owner-fenced idempotency, candidate access, and CDK adoption
AGENTS.md                    Working agreement for anyone (human or agent) picking up this repo —
                              the three invariants, conventions, and current state at a glance
.github/workflows/ci.yml     Lint, tests, contract/grounding validation, security scanning,
                              eval regression floors, and the Lambda package build — all
                              credential-free, per the protocol-boundary design above
.kiro/specs/grocery-orchestrator/   Numbered requirements, design (incl. what was decided
                              against and why), and task-by-task build status
.kiro/steering/               Locked technical, security and AI-quality decisions for this project
infra/                       AWS CDK (TypeScript) — not started yet
ingestion/                   Step Functions price-scraping pipeline — not started yet
```

## Progress to date

- ✅ Contract v1.0 published as the compatibility baseline
  (`src/schemas/contract.py`, `CONTRACT-v1.md`) — event shape, grounding
  invariants, and request/response schemas. Additive pilot hardening remains;
  breaking changes require v2.
- ✅ Graph topology built and wired end-to-end in LangGraph
  (`src/graph/build.py`), including the bounded repair loop.
- ✅ Intent classification node: model-backed with keyword-heuristic
  fallback, constraint extraction, hint reconciliation (message-wins),
  prompt-injection defences.
- ✅ Meal-plan generation node: price-free draft schema, deterministic
  cost assembly in Python, tiered repair loop (quality tier first attempt,
  fast tier for bounded repairs), honest `budget_infeasible` failure.
- ✅ In-memory fixture price repository with deliberately inconsistent
  cross-store product naming, used to stress-test the retrieval
  normaliser.
- ✅ `ScriptedModelClient` — a deterministic model stand-in that lets the
  whole graph, including the repair loop and induced failures, be tested
  without any AWS account or network access.
- ✅ Multi-model routing (`src/models/registry.py`, `config/models.json`) —
  see Design principles above for why this exists.
- ✅ Prose generation node (`src/graph/nodes/prose.py`): the model emits
  `[[c1]]` placeholders, model-supplied money is rejected, and Pilot Task 2
  changed rendering to money-free product/store labels. Comparison reasoning is
  money-free and samples were regenerated. The whole-response
  `assert_no_literal_money_in_response()` covers token, reasoning, and notice
  fields with negative controls, but `run_turn()` does not yet call it.
- ✅ Multi-item price queries: "cheapest for butter, milk and eggs" resolves
  and compares every item asked about, with partial resolution (`no_data` per
  unresolved item) rather than silently answering about only the first one.
- 🚧 Idempotency (`src/store/idempotency.py`): the current stores replay
  completed turns, scope keys by session, detect in-flight work, reject reused
  ids with a different raw-body fingerprint, and cache only terminal outcomes.
  Canonical validated-request hashing and stale-owner fencing are not yet
  implemented; Pilot Task 6 is required before the exactly-once production
  claim is valid.
- 🚧 Guardrail policy/tagging and fail-closed attachment are implemented. The
  numbered resource has basic live-invocation evidence. Pilot Task 3 proved
  offline propagation through intent, plan, and prose and the single handler
  mapping. `GuardrailBlocked` is now a provider-neutral subtype at
  `src/models/base.py`; providers raise it and nodes preserve it.
- 🚧 `evals/run_guardrail.py` provides 7/7 scripted must-allow structural
  evidence, not live policy qualification. `--model` does not yet pin the
  requested model, `OUT_OF_SCOPE` can count as blocked, and live must-block
  misses do not make the process fail. Live 13/13 must-block plus 7/7
  must-allow evidence remains open.
- ✅ Lambda handler (`src/handler.py`) maps propagated Guardrail intervention,
  bad input, ordinary model errors, grounding violations, and escaped failures
  to contract-valid bodies.
- ✅ Local dev server (`scripts/dev_server.py`): stdlib-only HTTP wrapper
  around the same `lambda_handler`, so the frontend team can integrate
  against real, contract-valid responses before the AWS account exists.
- ✅ 308 passing, 31 skipped tests covering classification, extraction,
  arithmetic, grounding, injection resistance, bounded repair, model routing,
  multi-item queries, idempotency, Guardrail propagation/tagging, dietary
  fail-closed behavior, and handler mappings.
- ✅ `validate.py` and regenerated samples check contract shape, declaration,
  citation order, basic source shape, and arithmetic. They do not independently
  compare citation keys/values with immutable retrieved records.
- ✅ Local eval harnesses remain separate from unit tests. Scripted baselines:
  76.7% intent accuracy, 91% meal-plan invariant pass rate, and 7/7 Guardrail
  must-allow structural cases. The Guardrail live path is experimental as noted
  above. Claude comparisons remain blocked on Anthropic account verification.
  Proposed Bedrock Model Evaluation and AgentCore Evaluations may add deployed
  evidence with reproducible provenance, but cannot replace these local gates
  or the 90% task floor.
- ✅ CI (`.github/workflows/ci.yml`): lint, tests, contract/grounding
  validation, dependency and secret scanning, guardrail policy validation,
  eval regression floors, and the Lambda package build — five jobs, all
  credential-free, gated behind one `summary` job for branch protection.
- ✅ Lambda deployment archive (`scripts/build_lambda.py`): cross-platform
  build (manylinux wheels regardless of host OS), unused packages (`numpy`,
  `zstandard`) and runtime-provided ones (`boto3`, `botocore`, `s3transfer`)
  excluded, unzipped size measured against a 240MB budget, and the packaged
  archive's importability verified against the archive plus *only* the
  runtime-provided packages — so "the runtime supplies this" is a tested
  claim rather than an assumption. ~30MB unzipped today, well under the
  budget that justifies zip-over-container (SnapStart is zip-only).
- ✅ Observability (`src/observability/`, Req 12.1–12.2): AWS Lambda
  Powertools for structured JSON logs correlated by `session_id`, X-Ray
  subsegments around retrieval and every model call — including each repair
  attempt separately, which is what the 29-second-ceiling decision needs —
  and EMF metrics for latency, tokens, cache reads, repair attempts,
  guardrail interventions, idempotent replays and turns that produce no
  content event. Powertools is imported by exactly two files; the graph and
  both eval harnesses stay free of it, and a test walks the import graph to
  keep it that way. Logs are asserted to contain no message text, location or
  dietary information on a real turn (Req 11.5).
- ✅ DynamoDB schema and migration plan documented (`DYNAMODB-SCHEMA.md`) —
  current products/idempotency tables, planned meals catalogue, GSI/candidate
  access, money-as-string, owner-fenced idempotency target, and CDK adoption.
- ✅ DynamoDB tables created and seeded (`grocery-products-dev` with PITR,
  `grocery-idempotency-dev` with TTL). The price repository passes its shared
  live contract suite; the idempotency store's five current outcomes were
  live-verified. Canonical hashing, stale-owner fencing, a shared idempotency
  suite, and idempotency-table PITR remain Pilot Task 6/9 work.
- ✅ Bedrock adapter verified live against Nova Lite and Nova Pro in
  `ap-southeast-2`. Intent evidence: Nova Lite 83.3%, Nova Pro 100%. Meal-plan
  evidence: Nova Pro 64%, below the 90% pilot floor.
- 🚧 Claude access remains pending Anthropic account verification. Haiku,
  Sonnet, Nova Lite, and Nova Pro are still marked `enabled` in the development
  catalogue even though no complete task-specific scorecard set exists; that
  is a documented Pilot Task 7 configuration defect, not production
  qualification. Pilot routing must disable every entry that has not met its
  active task's 90% floor.

## Not yet built

Planned/proposed items are not current capabilities:

- **Core follow-ups (Pilot Tasks 2–7):** immutable retrieved-record/key/value
  proof and runtime whole-response money enforcement; qualifying live Guardrail
  harness semantics/result; clarification/payable totals; location/freshness;
  idempotency ownership/candidate access; qualified SSM model routing.
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
  encrypted versioned S3 artefacts.
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

git config core.hooksPath scripts/hooks   # enable the pre-commit gate (once)

pytest                        # run the test suite (fast, deterministic)
python validate.py            # validate samples/*.json against the contract
python scripts/generate_fixtures.py   # regenerate fixtures/products.json
ruff check .                  # lint (see pyproject.toml for the enabled rule set)
python evals/run_intent.py            # 76.7% scripted baseline
python evals/run_meal_plan.py         # 91% scripted invariant baseline
python evals/run_guardrail.py         # 7/7 scripted must-allow structure only
# --model is experimental and not qualifying live evidence until Pilot Task 3 follow-up
python scripts/build_lambda.py        # build build/lambda.zip; see Progress to date
```

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

- [`AGENTS.md`](AGENTS.md) — the working agreement for this repo: the three
  invariants the design exists to enforce, conventions, and a current-state
  snapshot. Start here if you're picking this repo up cold.
- [`CONTRACT-v1.md`](CONTRACT-v1.md) — the frontend-facing write-up of the
  wire contract, including full request/response examples.
- [`DYNAMODB-SCHEMA.md`](DYNAMODB-SCHEMA.md) — current products and
  idempotency schemas, the planned catalogue-constrained meals domain,
  production candidate-query options, and the CDK adoption sequence.
- [`.kiro/specs/grocery-orchestrator/`](.kiro/specs/grocery-orchestrator/) —
  numbered requirements, the design doc (`design.md` §8 records what was
  decided against and why — read it before proposing an alternative), and
  `tasks.md` for build status task-by-task.
- [`.kiro/steering/tech.md`](.kiro/steering/tech.md) — locked architecture
  and infrastructure decisions (region, packaging, model tiering, transport
  roadmap, forbidden approaches).
- [`.kiro/steering/security.md`](.kiro/steering/security.md) — security
  controls that apply to all code in this repo, and the week-by-week
  schedule for when each lands.
- [`.kiro/steering/ai-quality.md`](.kiro/steering/ai-quality.md) — rules for
  model selection, capability branching, and eval/golden-set discipline.
