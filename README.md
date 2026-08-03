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
  the expensive/quality model only for the first meal-plan draft. The
  mapping lives in one place (`ModelTier` / `src/models/base.py`) so it's a
  reviewable decision, not something scattered across call sites.

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
  models/
    base.py                ModelClient protocol + ModelTier policy
    bedrock.py              Bedrock Converse API implementation (untested — no AWS account yet)
    scripted.py              Deterministic stand-in model used by all current tests
  prompts/
    intent.py               System/user prompt + schema for intent classification
    meal_plan.py             System/user prompt + price-free draft schema for meal planning
  retrieval/
    base.py                 PriceRepository protocol + PriceRecord type
    memory.py                Fixture-backed repository used for all local dev and tests
  runner.py                  ChatRequest -> graph -> validated ChatResponse

tests/                      Fast, deterministic, no AWS/network — see Running it locally
scripts/generate_fixtures.py  Generates fixtures/products.json (deliberately messy product naming)
validate.py                  Validates samples/*.json against the contract; runs in CI
samples/                     Example request/response payloads used by validate.py
fixtures/products.json       Generated seed price data (six NZ stores, ~26 products)
CONTRACT-v1.md               Human-readable version of the wire contract, for the frontend team
.kiro/steering/               Locked technical and security decisions for this project
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
- ✅ 55 passing tests covering classification, extraction, arithmetic,
  grounding, injection resistance, and the repair loop's bounds.
- ✅ `validate.py` / sample payloads wired up as a CI-style contract check,
  including a negative test that an ungrounded price is rejected.
- 🚧 Bedrock-backed `ModelClient` (`src/models/bedrock.py`) is written but
  **unexercised** — it needs a live AWS account and model access to test.

## Not yet built

- **DynamoDB-backed `PriceRepository`.** Retrieval currently only has the
  in-memory fixture implementation; a `dynamo.py` adapter satisfying the
  same protocol/tests is the next increment.
- **Ingestion pipeline** (`ingestion/`) — the Step Functions/EventBridge
  scraper pipeline that would populate DynamoDB from real store data.
- **Infrastructure as code** (`infra/`) — the AWS CDK stack (Lambda,
  API Gateway, DynamoDB, Bedrock Guardrail, IAM).
- **Bedrock Guardrail** attachment and enforcement (required before any
  production generation call, per `.kiro/steering/security.md`).
- **WebSocket streaming transport** — the contract is event-shaped
  specifically so this upgrade from the current REST-shaped flow doesn't
  require changing the payloads.
- **Cognito authoriser, API Gateway throttling/usage plans, structured
  logging/tracing/alarms** — see the security steering doc's week-by-week
  schedule for what's planned versus done.

## Running it locally

No AWS account or network access is required for any of the following.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt -r requirements-dev.txt

pytest                        # run the test suite (fast, deterministic)
python validate.py            # validate samples/*.json against the contract
python scripts/generate_fixtures.py   # regenerate fixtures/products.json
ruff check .                  # lint (see pyproject.toml for the enabled rule set)
```

## Further reading

- [`CONTRACT-v1.md`](CONTRACT-v1.md) — the frontend-facing write-up of the
  wire contract, including full request/response examples.
- [`.kiro/steering/tech.md`](.kiro/steering/tech.md) — locked architecture
  and infrastructure decisions (region, packaging, model tiering, transport
  roadmap, forbidden approaches).
- [`.kiro/steering/security.md`](.kiro/steering/security.md) — security
  controls that apply to all code in this repo, and the week-by-week
  schedule for when each lands.
