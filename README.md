# Smart Grocery & Meal Budget Assistant — Orchestrator

A conversational assistant for New Zealand shoppers that answers "what's the
cheapest butter near me?" and "feed a flat of 3 for under $30 this week"
questions by grounding every answer in real, retrieved supermarket price
data. Built for the AWS AI Innovation Mentorship Workshop as a reference
implementation of the **retrieve-then-generate** pattern on Amazon Bedrock.

This repository is the **orchestrator**: the Lambda-hosted brain that takes a
chat turn from the frontend, classifies it, fetches prices, calls a model,
and returns a validated response. It does not include the frontend, the
price-ingestion pipeline, or the AWS infrastructure — those are separate,
not-yet-built pieces described under [Not yet built](#not-yet-built) below.

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
- Every price in the response carries a `citation_ref` pointing back to a
  `Citation` that was emitted *before* anything that uses it. A response
  where a price has no matching citation is a contract violation, checked
  automatically by `assert_grounded()`.
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
  v
retrieve_prices            <-- the ONLY source of prices
  |--- no citations -----> emit_no_data -------------> finalise
  |--- price_check ------> generate_comparison ------> finalise
  v (meal_plan)
generate_plan  <----------------+
  v                             |
validate_plan                   | repair (bounded)
  |--- errors ---> repair_plan +
  |--- attempts exhausted ---> emit_budget_infeasible -> finalise
  v ok
finalise -> END
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
- **Model tiering is an explicit policy.** Cheap/fast model for
  classification and repair passes (high volume, low creative demand);
  the expensive/quality model only for the first meal-plan draft. Nodes
  request a *tier* (`ModelTier` / `src/models/base.py`), never a model id.
  `src/models/registry.py` resolves that request against
  `config/models.json` — a per-task preference order plus, per model,
  which Bedrock features it actually supports (tool use, prompt caching,
  JSON mode). That capability data is what lets `BedrockModelClient` adapt
  its call shape per model instead of assuming every model on Bedrock
  behaves like Claude — Llama, for instance, gets the schema embedded in
  the prompt because it has no tool-use support. Routing policy is data,
  not code, so retiering a task or enabling a new model is a config change,
  not a deploy.

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
      prose.py             Placeholder-based explanatory text; renders figures from
                            citations, rejects any literal money that slips through
  models/
    base.py                ModelClient protocol + ModelTier policy
    registry.py              Reads config/models.json; routes a task to a concrete
                              model by tier, preference order and capability
    bedrock.py              Bedrock Converse API implementation (untested — no AWS account yet)
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
    dynamo.py                DynamoDB adapter — scaffolded, raises NotImplementedError (see DYNAMODB-SCHEMA.md)
  store/
    idempotency.py           Session-scoped dedup: acquire/complete/release, in-memory
    dynamo_idempotency.py    DynamoDB adapter — scaffolded, raises NotImplementedError
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
DYNAMODB-SCHEMA.md           Proposed two-table DynamoDB schema (products + meals) and the
                              open design decision on how strongly recipes constrain generation
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

- ✅ Contract v1.0 frozen (`src/schemas/contract.py`, `CONTRACT-v1.md`) —
  event shape, grounding invariants, request/response schemas.
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
- ✅ Prose generation node (`src/graph/nodes/prose.py`): explanatory text
  written entirely in `[[c1]]`-style placeholders, rendered into real figures
  from citations after generation, with `assert_no_literal_money()` rejecting
  any money-shaped string that slips through. Degrades to no prose — never
  fails the turn — if generation or rendering fails.
- ✅ Multi-item price queries: "cheapest for butter, milk and eggs" resolves
  and compares every item asked about, with partial resolution (`no_data` per
  unresolved item) rather than silently answering about only the first one.
- ✅ Idempotency (`src/store/idempotency.py`): resending a `turn_id` replays
  the cached response rather than re-running generation. Session-scoped keys,
  payload fingerprinting (a reused `turn_id` with different content is
  rejected, not silently answered), in-flight detection so a retry that
  arrives mid-request doesn't trigger a second run, and only terminal
  outcomes are cached — a retryable failure is never cached as permanent.
- ✅ Guardrail input tagging (`src/models/guardrail.py`): fresh per-request
  tags so the PROMPT_ATTACK filter actually evaluates untrusted content (it
  silently evaluates nothing without this), plus fail-closed enforcement in
  `BedrockModelClient` — a generation call refuses to run with no guardrail
  configured unless that's an explicit, visible opt-out. The Guardrail
  *resource* itself is not yet created against a live account; see
  [Not yet built](#not-yet-built).
- ✅ Lambda handler (`src/handler.py`): API Gateway proxy integration that
  maps every failure mode (bad input, guardrail block, model error, grounding
  violation, unhandled exception) to a contract-valid response — never a bare
  500 or a leaked stack trace/secret.
- ✅ Local dev server (`scripts/dev_server.py`): stdlib-only HTTP wrapper
  around the same `lambda_handler`, so the frontend team can integrate
  against real, contract-valid responses before the AWS account exists.
- ✅ 150 passing tests covering classification, extraction, arithmetic,
  grounding, injection resistance, the repair loop's bounds, multi-model
  routing/capability branching, multi-item queries, idempotency, guardrail
  tagging, and the Lambda handler's error mapping.
- ✅ `validate.py` / sample payloads wired up as a CI-style contract check,
  including a negative test that an ungrounded price is rejected.
- ✅ Eval harnesses (`evals/run_intent.py`, `evals/run_meal_plan.py`) —
  separate from the unit tests on purpose: unit tests check the code is
  correct given fixed input, evals check a *model* is good enough, and let
  you compare models on accuracy, latency and cost before picking one for
  production. Run against the scripted client with no AWS account, or
  `--compare claude-haiku claude-sonnet nova-lite` once Bedrock is live.
  Baselines against the scripted client: 76.7% intent accuracy, 91% meal-plan
  invariant pass rate — floors enforced in CI, not targets to read as model
  quality.
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
- ✅ DynamoDB schema proposed (`DYNAMODB-SCHEMA.md`) — three tables, GSI design
  for "cheapest near me", money-as-string, TTL as a Privacy Act control on
  saved plans, and the idempotency table's conditional-put claim. Team review
  pending.
- 🚧 Bedrock-backed `ModelClient` (`src/models/bedrock.py`) is written but
  **unexercised** — it needs a live AWS account and model access to test.

## Not yet built

- **DynamoDB-backed `PriceRepository` and `IdempotencyStore`.** The products
  schema is designed (`DYNAMODB-SCHEMA.md`) and `src/retrieval/dynamo.py` /
  `src/store/dynamo_idempotency.py` are scaffolded against their protocols,
  but every method still raises `NotImplementedError` — deliberately, so a
  misconfigured deployment fails loudly instead of silently behaving like
  working software (an empty, indistinguishable-from-"no data" price list; a
  store that never deduplicates). Both run on their in-memory fixture
  implementations until the AWS account lands.
- **Ingestion pipeline** (`ingestion/`) — the Step Functions/EventBridge
  scraper pipeline that would populate DynamoDB from real store data.
- **Infrastructure as code** (`infra/`) — the AWS CDK stack (Lambda,
  API Gateway, DynamoDB, Bedrock Guardrail, IAM).
- **The Bedrock Guardrail resource itself.** The code-side enforcement is
  built and tested — input tagging (`src/models/guardrail.py`), fail-closed
  behaviour when no guardrail id is configured, `config/guardrail.json` plus
  `scripts/apply_guardrail.py` to create/update it — but no Guardrail has
  been created against a live account yet, so the actual filtering is
  unverified. Task 8.10 in `.kiro/specs/grocery-orchestrator/tasks.md` (8.9,
  the offline half, is done).
- **SnapStart on a published alias** (Task 10.2) — the deployment archive
  itself is built (see Progress to date); enabling SnapStart and publishing
  an alias is the next step, once there's somewhere to deploy it to.
- **WebSocket streaming transport** — the contract is event-shaped
  specifically so this upgrade from the current REST-shaped flow doesn't
  require changing the payloads.
- **Cognito authoriser, API Gateway throttling/usage plans, alarms** — see the
  security steering doc's week-by-week schedule for what's planned versus
  done. Structured logging, tracing and metrics are done (Task 6.7, below);
  the alarms built on those metrics still need a deployment to alarm on.

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
python evals/run_intent.py            # eval harness, scripted client (see Progress to date)
python evals/run_meal_plan.py
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
switches individual dependencies to their AWS-backed implementations once
those exist (`USE_DYNAMODB=1` currently raises `NotImplementedError` — see
[Not yet built](#not-yet-built)).

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
- [`DYNAMODB-SCHEMA.md`](DYNAMODB-SCHEMA.md) — proposed two-table DynamoDB
  schema, the GSI design behind "cheapest near me", and the open decision on
  how strongly the recipe catalogue should constrain meal generation.
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
