# 06 — Deployment Guide (runbook)

> **Status: Design documentation. Not yet implemented.** This is the runbook the
> team will follow once the CDK app is written. It is written for a developer or
> DevOps engineer who has the app in front of them; steps that don't exist yet
> are marked *(after CDK is built)*.

## 0. Prerequisites

| Requirement | Notes |
|-------------|-------|
| AWS account | the workshop account; region **`ap-southeast-2`** (never `ap-southeast-6`) |
| AWS CLI v2, authenticated | `aws sts get-caller-identity` must return the deploy account |
| Node.js 20+, npm | for CDK |
| Python 3.13, venv | for `scripts/build_lambda.py` |
| CDK CLI | `npm i -g aws-cdk` or use `npx cdk` |
| Bedrock model access | Nova Lite/Pro + Claude Haiku/Sonnet **enabled** in the account (Bedrock console → Model access). Verify with `aws bedrock list-foundation-models --region ap-southeast-2` |
| Existing tables present | `grocery-products-dev` (seeded), `grocery-idempotency-dev` |

Before touching CDK, capture the ground truth:

```bash
aws sts get-caller-identity
aws dynamodb describe-table --table-name grocery-products-dev    --region ap-southeast-2
aws dynamodb describe-table --table-name grocery-idempotency-dev --region ap-southeast-2
aws dynamodb scan --table-name grocery-products-dev --select COUNT --region ap-southeast-2   # baseline row count
python scripts/check_quotas.py     # the live Bedrock throughput ceiling
```

Record the key schema and the baseline row count — you will verify the count is
unchanged after adoption.

## 1. One-time account setup

```bash
# CDK bootstrap (creates the toolkit stack: assets bucket + exec roles). Human action.
npx cdk bootstrap aws://<account-id>/ap-southeast-2
```

Set up the **OIDC deploy role** and (optionally) the GitHub Environment now if
using CD — see [05-CICD §3](05-CICD.md). Not required for a first manual deploy.

## 2. Build the Lambda archive (the one authoritative packager)

```bash
python scripts/build_lambda.py        # → build/lambda.zip  (manylinux wheels, size-budgeted)
```

The CDK's `fromAsset('../build/lambda.zip')` picks this up. In CI this runs on
ubuntu-latest, which is the authoritative build; a Windows dev build is fine for
a manual deploy.

## 3. Deploy order — the safe sequence

> The golden rule: **stateful first, adopted, verified — then everything else.**

### Step 3a — Stateful stack (adopt the tables) *(after CDK is built)*

Follow the strategy chosen in [08 §2](08-OPEN-DECISIONS.md).

**Strategy A (reference, unmanaged — recommended for pilot):** nothing to
import. The stack only produces handles.
```bash
npx cdk synth Grocery-Stateful-dev --context stage=dev      # sanity
```
There is no `deploy` risk because CloudFormation manages no table.

**Strategy B (true adoption via `cdk import`):**
```bash
npx cdk diff  Grocery-Stateful-dev --context stage=dev      # should be "table to be IMPORTED", not created
npx cdk import Grocery-Stateful-dev --context stage=dev     # brings existing physical tables under management
npx cdk diff  Grocery-Stateful-dev --context stage=dev      # MUST now show NO changes
```
> 🛑 **Stop condition:** if the post-import `cdk diff` shows *any* change to a
> table (a differing key schema, GSI, or billing mode), do **not** deploy. A
> deploy would attempt to reconcile the difference, and for a key-schema
> mismatch that means **table replacement = data loss**. Fix the CDK definition
> to match `describe-table` output exactly, re-diff, and only proceed at zero
> diff.

Verify: `aws dynamodb scan --table-name grocery-products-dev --select COUNT`
returns the **same** baseline count as in step 0.

### Step 3b — Service stack

```bash
npx cdk diff   Grocery-Service-dev --context stage=dev
npx cdk deploy Grocery-Service-dev --context stage=dev
```
Creates: orchestrator Lambda + SnapStart alias, REST API, Guardrail, IAM roles,
SSM params, log group. Note the **API invoke URL** and the **Guardrail id** from
the outputs.

### Step 3c — Observability stack

```bash
npx cdk deploy Grocery-Obs-dev --context stage=dev
```
Creates: SNS topic, metric filter, two alarms, Budget, artefact bucket. Then —
**by hand, because email needs out-of-band confirmation** — subscribe an
operator:
```bash
aws sns subscribe --topic-arn <topic-arn> --protocol email --notification-endpoint you@example.com
# confirm via the email link; apply_alarms.py's warning about unsubscribed topics applies here too
```

### Step 3d — Frontend stack *(after a UI exists)*

```bash
# build the UI first (framework per 08 §7), producing frontend/dist
npx cdk deploy Grocery-Frontend-dev --context stage=dev
```
Note the **CloudFront domain** from the outputs. Then close the CORS loop:
re-deploy the service stack with `CORS_ORIGIN` set to that domain (two-pass, per
[03 FrontendStack](03-STACK-SPECS.md)):
```bash
npx cdk deploy Grocery-Service-dev --context stage=dev --context corsOrigin=https://<dist>.cloudfront.net
```

### Step 3e — Ingestion stack

```bash
npx cdk deploy Grocery-Ingestion-dev --context stage=dev
```
Creates: ingestion Lambda, Step Functions state machine, EventBridge daily rule.
Uses fixture adapters — no live retailer traffic.

## 4. Verify the deployment

```bash
# Smoke test the shopper path (price check — exercises DynamoDB read + Bedrock + Guardrail)
curl -sS -X POST "$API_URL/chat" \
  -H 'Content-Type: application/json' \
  -d @samples/request_price_check.json | jq .

# Meal plan (exercises the Scan path — the one a naive smoke test misses)
curl -sS -X POST "$API_URL/chat" \
  -H 'Content-Type: application/json' \
  -d @samples/request_meal_plan.json | jq .
```

Checklist:

- [ ] Both return a **contract-valid** `ChatResponse` (compare shape to
      `samples/response_*.json`).
- [ ] The meal-plan turn succeeds — proves the `Scan` permission on products.
- [ ] Prices in the response carry a `citation_ref` (grounding intact).
- [ ] A deliberately unsafe prompt (e.g. an age-restricted product) is refused —
      proves the Guardrail is applied with the numbered version.
- [ ] CloudWatch shows structured logs with **no message text / location /
      dietary data**; X-Ray shows subsegments for retrieval and model calls.
- [ ] Manually fire the ingestion state machine once and confirm a failed
      retailer branch is *reported*, not fatal:
      `aws stepfunctions start-execution --state-machine-arn <arn> --input '{"retailers":["pakn_save","broken_retailer"]}'`

## 5. Rollback

- **Stateless stacks (service/ingestion/obs/frontend):** roll back by
  redeploying the previous git commit, or `cdk deploy` after
  `git checkout <prev>`. CloudFormation keeps the last-good template; a failed
  deploy auto-rolls-back. These are safe to destroy and recreate.
- **A bad Lambda version:** because the API points at the `live` alias, you can
  shift the alias back to the prior version instantly (alias routing) without a
  full redeploy — the fastest rollback.
- **Stateful stack:** the tables are `RETAIN` (Strategy B) or unmanaged
  (Strategy A). A `cdk destroy` of the stateful stack **does not** delete the
  tables. Data rollback is **PITR** — restore the products table to a timestamp
  (`aws dynamodb restore-table-to-point-in-time`) into a new table, verify, then
  cut over. Never restore in place over live data.
- **Guardrail:** if a new Guardrail version misbehaves, repoint
  `BEDROCK_GUARDRAIL_VERSION` to the prior number and redeploy the service
  stack. DRAFT is never used.

## 6. Mapping to the Pilot roadmap

| Runbook step | Pilot Task |
|--------------|-----------|
| 3a adopt tables | **Task 9** — Establish CDK and adopt existing data resources |
| 3b service plane | **Task 10** — Define the deployable service plane |
| 4 verify shopper path | **Task 11** — Deploy and verify the anonymous pilot |
| 3c observability + Budgets + artefact bucket | **Task 12** — Operational acceptance gates and artefact storage |
| 3e ingestion | **Task 13** — Controlled ingestion and decoupled review triggers |

## 7. Teardown (for a clean workshop reset)

```bash
# Stateless only — safe. Leaves the seeded tables untouched.
npx cdk destroy Grocery-Frontend-dev Grocery-Ingestion-dev Grocery-Obs-dev Grocery-Service-dev --context stage=dev
```
Do **not** `destroy` the stateful stack unless you intend to abandon the tables;
even then, `RETAIN`/unmanaged means the tables survive and must be deleted by
hand — which is the safety property working as designed.
