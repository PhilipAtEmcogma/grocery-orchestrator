# Tech constraints — do not deviate without asking

## Locked decisions (30 Jul 2026)

- **Language:** Python 3.13. **Region:** `ap-southeast-2` (Sydney), ALL resources.
- **Orchestration:** LangGraph inside the orchestrator **Lambda**.
  This matches the mentor's specified pattern: receive question -> retrieve
  price data from DynamoDB -> construct grounded prompt -> call Bedrock ->
  return to frontend. LangGraph makes retrieval-before-generation a graph
  invariant rather than a convention.
- **Packaging:** Lambda **zip**, not container. Measured at ~47 MB with
  `numpy` and `zstandard` excluded (transitive, unused at import) and
  boto3/botocore taken from the Lambda runtime. Well under the 250 MB limit.
- **SnapStart:** enabled, on a published alias. Requires zip — container
  images are NOT SnapStart-eligible. This is why we are not containerising.
- **Model access:** Bedrock Converse API via `langchain-aws`.
  Haiku for intent classification and repair passes. Sonnet for meal planning.
- **Transport:** API Gateway REST synchronous for weeks 1-2, upgrading to
  API Gateway **WebSocket** streaming in week 3. Contract is event-shaped so
  the swap does not change the interface.
- **Ingestion:** **Step Functions** state machine (Map state, per-store error
  handling) triggered by EventBridge -> scraper Lambdas -> DynamoDB.
  NOT a single monolithic scraper Lambda.
- **IaC:** AWS CDK (TypeScript) in `infra/`.
- **Contract:** `schemas/contract.py` is the single source of truth. Changes
  there require regenerating samples and passing `validate.py`.

## Dependency rules

- Use `langgraph`, `langchain-core`, `langchain-aws`. Do NOT add the umbrella
  `langchain` package — it adds weight without benefit here.
- Exclude `numpy` and `zstandard` from the deployment package. Neither is
  imported by our code; both are transitive pulls (`langchain-aws`,
  `langsmith`).
- Do not bundle boto3/botocore unless a specific new Bedrock feature requires
  a newer version than the runtime provides. Document it if you do. Excluding
  boto3 also means excluding `jmespath` and `s3transfer` — they exist only to
  support boto3, and the runtime's own boto3 brings its own copies of both.
- `scripts/build_lambda.py` is the source of truth for this list
  (`UNUSED_TRANSITIVE` / `RUNTIME_PROVIDED`) and checks the "never imported"
  half of it against `src/` on every build rather than trusting this file.

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
