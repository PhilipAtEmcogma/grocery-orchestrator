# Tech constraints — do not deviate without asking

## Locked decisions (30 Jul 2026)

These are target constraints for implementation and deployment, not claims that
any planned service already exists. Current live evidence is limited to the
resources and adapters listed in `AGENTS.md`.

- **Language:** Python 3.13. **Region:** `ap-southeast-2` (Sydney), ALL resources.
- **Orchestration:** LangGraph inside the orchestrator **Lambda**. The shopper
  path remains API Gateway REST -> published zip Lambda/SnapStart alias ->
  deterministic LangGraph -> DynamoDB/Bedrock Guardrail. Retrieval before
  generation is a graph invariant, not an agent decision.
- **Packaging:** Lambda **zip**, not container. Measured at ~47 MB with `numpy`
  and `zstandard` excluded and boto3/botocore taken from the Lambda runtime.
- **SnapStart:** enabled on a published alias. Container images are not
  SnapStart-eligible.
- **Model access:** Bedrock Converse API via `langchain-aws`. Nodes request a
  task and the registry routes from `config/models.json`; no node hardcodes a
  model id. Haiku and Sonnet remain intended candidates, not qualified routes,
  until account access and task-specific scorecards meet the 90% floor.
- **Transport:** API Gateway REST synchronous for the initial pilot. API Gateway
  WebSocket streaming is a later approved upgrade; the event contract remains
  unchanged.
- **Ingestion:** EventBridge -> Step Functions Inline Map -> source-adapter
  Lambdas -> DynamoDB, with per-store errors. Fixture/recorded adapters come
  first; live acquisition remains separately gated.
- **IaC:** AWS CDK (TypeScript) in `infra/`; planned under Pilot Tasks 9–10.
- **Contract:** `src/schemas/contract.py` is the source of truth. Changes require
  regenerated samples and `validate.py`.

## Purpose-driven AWS learning

The project deliberately seeks hands-on experience with broad relevant AWS
services, especially Bedrock and AgentCore. Breadth is not a reason to add a
service. Every adoption must state its product purpose, bounded scope,
acceptance evidence, cost/security owner, and rollback or removal criterion.
No service may weaken grounding, dietary, arithmetic, Guardrail, or
honest-failure invariants. Planned and proposed services must never be
presented as implemented.

The approved/proposed sequence is:

1. **Implemented reference core:** deterministic Lambda shopper workflow.
2. **Planned first:** local read-only MCP over coarse complete-application
   operations.
3. **Proposed, mentor approval required:** AgentCore Gateway with Identity and
   Policy as a governed exposure/auth/policy/mediation layer over those same
   coarse operations. Gateway is never a path around LangGraph.
4. **Proposed, mentor approval required:** a separately deployed AgentCore
   Runtime data-quality reviewer over capped sanitized ingestion snapshots.
   It emits cited, schema-checked review artefacts, then deterministic checks
   and a human decide; it has no shopper-path, publication, or write authority.
5. **Proposed companion evidence:** Bedrock Model Evaluation and AgentCore
   Evaluations supplement version-controlled local deterministic tests and
   evals; they never replace them.

ADR 0002 is proposed. Until mentor approval, ADR 0001 and the current local-first
behavior remain controlling.

## Staged companion services

All remain planned or proposed until their own task and evidence say otherwise:

- Bedrock cross-Region inference profiles only for a measured availability or
  latency purpose and only where the approved profile keeps use in Sydney;
  local task scorecards still qualify every route.
- Knowledge Bases only for cited recipe/catalogue knowledge, never authoritative
  price data. Automated Reasoning may provide advisory verification where
  supported, never the final grounding/dietary/arithmetic decision.
- AgentCore Memory only after Cognito ownership, consent, TTL, deletion, and
  Privacy Act design; memory never stores or supplies authoritative prices.
- S3 for versioned datasets, evaluation results, and review artefacts;
  DynamoDB Streams plus SQS/DLQ for decoupled review triggers; SNS for operator
  alerts and approval notifications.
- WAF and Cognito before user-owned or public managed surfaces. CloudWatch,
  X-Ray, and Budgets accompany deployed components and provide removal evidence
  where cost, latency, or reliability value is not demonstrated.

## MCP and agentic boundaries

- The shopper path remains deterministic. An agent never decides whether
  retrieval, arithmetic, dietary validation, or final grounding checks run.
- Local MCP comes first and exposes no raw DynamoDB, AWS SDK, filesystem,
  arbitrary network, retailer acquisition, writes, or unguarded generation.
- A proposed AgentCore Gateway may mediate the same coarse tools only after ADR
  0002 approval, identity/policy design, contract parity evidence, rate limits,
  privacy-safe audit, and a tested disable/fallback path.
- A proposed AgentCore Runtime reviewer is isolated from shopper traffic and
  shopper PII. Its capped read-only snapshot and artefact output are the only
  permitted data paths; deterministic post-validation and human approval are
  mandatory.
- Remote MCP, persistent memory, and additional runtimes require explicit
  identity, retention, deletion, rate-limit, timeout, audit, cost, and rollback
  controls.

## Production mode

Local fixture/scripted dependencies must be selected explicitly. A production
stage fails closed unless DynamoDB, Bedrock, a numbered Guardrail version,
stored idempotency, strict CORS, and named resources are configured. Missing
settings must never silently select demo adapters.

## Dependency rules

- Use `langgraph`, `langchain-core`, `langchain-aws`. Do NOT add the umbrella
  `langchain` package.
- Exclude `numpy` and `zstandard`; neither is imported by our code.
- Do not bundle boto3/botocore unless a required Bedrock feature needs a newer
  version than the runtime provides. Document it. `s3transfer` goes with them.
- Bundle everything our dependency tree declares except runtime-provided
  packages. `jmespath` stays because Powertools imports it directly.
- `scripts/build_lambda.py` is the source of truth for `UNUSED_TRANSITIVE` and
  `RUNTIME_PROVIDED`. Its source scan proves only our imports; `verify_import()`
  tests the archive against only the named runtime-provided packages.

## Forbidden

- DO NOT use Bedrock Agents Classic, `CreateAgent`, or `InvokeInlineAgent`.
- DO NOT suggest `ap-southeast-6` (Auckland).
- DO NOT containerise the orchestrator Lambda; it forfeits SnapStart.
- DO NOT use Lambda Function URLs for streaming; they bypass API Gateway
  throttling, usage plans, and the Cognito authoriser.
- DO NOT use `float` for money. Use `Decimal` in Python and strings on the wire.

## Shopper-path Runtime contingency (not built — separate mentor approval)

Moving the meal-plan shopper path to AgentCore Runtime remains a distinct
fallback, not an approval implied by Gateway or reviewer work. Trigger: measured
meal-plan p99 above approximately 25 seconds after, in order, Haiku repair,
constrained plan size, affordable-candidate prefiltering, and split JSON/prose
generation are exhausted. It requires separate mentor approval and must preserve
the same deterministic graph and contract.
