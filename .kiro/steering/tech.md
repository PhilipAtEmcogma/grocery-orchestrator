# Tech constraints — do not deviate without asking

## Locked decisions (30 Jul 2026)

These are target constraints for implementation and deployment, not claims that
the service plane, CDK application, ingestion pipeline, MCP façade, or agents
already exist. Current live evidence is limited to the resources and adapters
listed in `AGENTS.md`.

- **Language:** Python 3.13. **Region:** `ap-southeast-2` (Sydney), ALL resources.
- **Orchestration:** LangGraph inside the orchestrator **Lambda**.
  This matches the mentor's specified pattern: receive question -> retrieve
  price data from DynamoDB -> construct grounded prompt -> call Bedrock ->
  return to frontend. LangGraph makes retrieval-before-generation a graph
  invariant rather than a convention.
- **Packaging:** Lambda **zip**, not container. Measured at ~47 MB with
  `numpy` and `zstandard` excluded (transitive, unused at import) and
  boto3/botocore taken from the Lambda runtime. Well under the 250 MB limit.
- **SnapStart:** must be enabled on a published alias. Requires zip — container
  images are NOT SnapStart-eligible. This is why we are not containerising.
- **Model access:** Bedrock Converse API via `langchain-aws`. Haiku is the
  intended classification/repair candidate and Sonnet the intended meal-plan
  candidate, but neither is qualified until account access and task-specific
  scorecards meet the 90% floor. The development catalogue currently marks all
  Claude and Nova candidates `enabled`; this is a known non-production state
  that Pilot Task 7 must reconcile by disabling every unqualified active route.
  Current Nova evidence does not qualify every route.
- **Transport:** API Gateway REST synchronous for the initial pilot. API Gateway
  **WebSocket** streaming is a later approved upgrade; the contract is
  event-shaped so the swap does not change the interface.
- **Ingestion:** **Step Functions** state machine (Inline Map, per-store error
  handling) triggered by EventBridge -> source-adapter Lambdas -> DynamoDB.
  NOT a single monolithic scraper Lambda. Fixture/recorded adapters come first;
  live acquisition remains separately gated.
- **IaC:** AWS CDK (TypeScript) in `infra/`; planned under Pilot Tasks 9–10.
- **Contract:** `src/schemas/contract.py` is the single source of truth. Changes
  there require regenerating samples and passing `validate.py`.

## MCP and agentic boundaries

- The safety-critical shopper path remains the deterministic LangGraph
  workflow. An agent never decides whether retrieval, arithmetic, dietary
  validation, or final grounding checks run.
- The first MCP server is local and read-only. It exposes coarse application
  operations that invoke the complete service; never raw DynamoDB, AWS SDK,
  filesystem, arbitrary network, retailer acquisition, writes, or unguarded
  generation.
- A bounded data-quality agent may review a capped ingestion snapshot and
  produce a human review artefact. It has no publication authority.
- Remote MCP, persistent agent memory, and additional agent runtimes are later
  decisions requiring identity, retention, rate-limit, timeout, audit, and cost
  controls.
- AgentCore remains subject to the meal-path p99 contingency and mentor
  sign-off below; MCP interest is not a separate approval to use it.

## Production mode

Local fixture/scripted dependencies must be selected explicitly. A production
stage fails closed unless DynamoDB, Bedrock, a numbered Guardrail version,
stored idempotency, strict CORS, and named resources are configured. Missing
settings must never silently select demo adapters.

## Dependency rules

- Use `langgraph`, `langchain-core`, `langchain-aws`. Do NOT add the umbrella
  `langchain` package — it adds weight without benefit here.
- Exclude `numpy` and `zstandard` from the deployment package. Neither is
  imported by our code; both are transitive pulls (`langchain-aws`,
  `langsmith`).
- Do not bundle boto3/botocore unless a specific new Bedrock feature requires
  a newer version than the runtime provides. Document it if you do. `s3transfer`
  goes with them — it exists only to support boto3, and the runtime brings its
  own copy.
- **Bundle everything our dependency tree declares, except what the runtime
  provides.** `jmespath` was excluded on the reasoning that it only ever served
  boto3; that stopped being true when Powertools arrived, because
  `aws_lambda_powertools.logging.logger` imports it unguarded. A transitive of
  a runtime-provided package may also be a *direct* dependency of one we
  bundle, and then it is ours. It is ~50 KB.
- `scripts/build_lambda.py` is the source of truth for this list
  (`UNUSED_TRANSITIVE` / `RUNTIME_PROVIDED`) and checks the "never imported"
  half of it against `src/` on every build rather than trusting this file.
  That check only reads `src/`, so it cannot see what a bundled third-party
  package imports — which is exactly how the `jmespath` exclusion survived
  until the archive failed to import in CI. `verify_import()` is what covers
  that half, and it now runs the handler against the archive plus *only* the
  packages `RUNTIME_PROVIDED` names, so the runtime claim is itself tested
  rather than assumed.

## Forbidden

- DO NOT use Bedrock Agents Classic, `CreateAgent`, or `InvokeInlineAgent`.
  It entered maintenance mode 30 July 2026 and is closed to new accounts.
- DO NOT suggest `ap-southeast-6` (Auckland). Neither AgentCore nor SnapStart
  are available there.
- DO NOT containerise the orchestrator Lambda. It forfeits SnapStart.
- DO NOT use Lambda Function URLs to get streaming. It bypasses API Gateway
  and costs us throttling, usage plans, and the Cognito authoriser.
- DO NOT use `float` for money anywhere. `Decimal` in Python, strings on the
  wire.

## Contingency (not built — requires mentor sign-off)

AgentCore Runtime is documented as a fallback for the meal-plan path only.
**Trigger:** measured p99 latency on the meal-plan path exceeding ~25s after
the mitigations below are exhausted.
Mitigations to try first, in order: repair pass on Haiku not Sonnet; constrain
plan size; pre-filter the candidate basket to affordable items before
generation; split JSON generation from prose generation.
