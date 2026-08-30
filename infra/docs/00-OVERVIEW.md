# 00 — Overview

> **Status: Design documentation for Pilot Tasks 9–12. Not yet implemented.**

## 1. What we are building

The Smart Grocery & Meal Budget Assistant is a **serverless, grounded chatbot**
for New Zealand shoppers. A user asks in natural language — *"what's the
cheapest butter near me?"* or *"feed a flat of 3 for under $30 this week, no
seafood"* — and the assistant answers with a **price comparison** or a **budget
meal plan**, where **every price is grounded in real catalogue data and never
invented by the model**.

The application that does this already exists and is mature: a Python 3.13
LangGraph orchestrator (intent → plan → prose) behind a deterministic graph,
with a model plane over Amazon Bedrock, a DynamoDB price repository, a numbered
Bedrock Guardrail, idempotency, and full observability. See the root
[`README.md`](../../README.md), [`AGENTS.md`](../../AGENTS.md) and
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).

**This documentation set is not about the application.** It is about the
**infrastructure** that provisions and runs it on AWS in a reproducible,
reviewable, least-privilege way — the AWS CDK app that will live in `infra/`.

## 2. Scope

### In scope (what these docs specify)

- A **TypeScript AWS CDK** application (`infra/`) that provisions the system.
- **Adoption** of the DynamoDB tables that already exist and hold seeded data,
  *without recreating or emptying them* (Pilot Task 9).
- The **service plane**: zip Lambda + SnapStart alias, API Gateway REST API,
  Bedrock Guardrail, IAM roles, SSM configuration, log retention, strict CORS,
  throttling and a usage plan (Pilot Task 10).
- **Operational** resources: CloudWatch dashboards and alarms, X-Ray, AWS
  Budgets, an encrypted/versioned S3 artefact bucket, SNS notifications
  (Pilot Task 12).
- The **ingestion** control plane: EventBridge schedule + Step Functions Inline
  Map + the ingestion Lambda (Pilot Task 13), which already has a state-machine
  definition in [`config/ingestion-state-machine.json`](../../config/ingestion-state-machine.json).
- A **frontend** hosting stack: S3 + CloudFront for the static chat UI.
- **CI/CD**: how deployment is automated on top of the existing GitHub Actions
  CI, within a $0 budget.

### Out of scope (deliberately deferred)

- **Building the application logic** — it exists.
- **Live retailer scraping** — ingestion uses fixture/recorded adapters first;
  live acquisition is separately gated (`tech.md`).
- **AgentCore Gateway / Runtime reviewer / managed evaluations** — these are
  *proposed* under [ADR 0002](../../docs/adr/0002-staged-agentcore-and-managed-ai-services.md)
  and require mentor approval. They are named here only where the infrastructure
  must leave room for them.
- **Cognito authentication, WAF, WebSocket streaming, a `prod` stage** — all are
  planned *after* the anonymous pilot. Where they change a construct's shape
  (e.g. the API authorizer), the doc notes the seam but does not build it.

## 3. What already exists vs. what CDK adds

The single most important thing to understand before building the CDK app is
that **AWS resources already exist**, created imperatively. CDK's job is partly
to *create* new resources and partly to *take ownership of existing ones*.

> ⚠️ **Corrected 2026-08-30 — the adoption surface is bigger than this table
> used to say.** It previously listed the API Gateway REST API and the SnapStart
> alias as "❌ Not yet" and the Lambdas and state machine as merely defined.
> **All of them exist and have since 2026-08-27**, verified against the account
> on 2026-08-30. Almost the entire service plane is already standing and was
> created by hand.
>
> This changes Pilot Task 9–10 planning materially. The work is not "create the
> service plane"; it is **adopt or deliberately replace a service plane that is
> already serving traffic**, which is a harder review with a live endpoint in
> the middle of it. Decide per resource — see [08 §10](08-OPEN-DECISIONS.md) —
> and note that replacing the REST API changes the URL the frontend uses, while
> adopting it inherits whatever manual state it is in.
>
> The running Lambda was cut over to **version 6, built from `main`**, on
> 2026-08-30 ([`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) §3a). Note
> that it now answers `STALE_DATA` to every priced query, because the seeded
> data predates the freshness threshold (§3c) — correct behaviour, but it means
> a smoke test of the deployed stack will not return prices until the data is
> refreshed.

| Resource | Exists today? | Created by | Under CDK |
|----------|---------------|-----------|-----------|
| DynamoDB `grocery-products-dev` (PITR) | ✅ **Yes, seeded** | manual / `load_seed_data.py` | **Adopt (import), never replace** |
| DynamoDB `grocery-idempotency-dev` (TTL) | ✅ **Yes** | manual | **Adopt (import), never replace** |
| Bedrock Guardrail (`b1xezpqe04kx`, **v2**) | ✅ Yes | `apply_guardrail.py` from [`config/guardrail.json`](../../config/guardrail.json) | Re-create as a construct or adopt by id — see [08-OPEN-DECISIONS](08-OPEN-DECISIONS.md) |
| IAM roles (orchestrator, ingestion) | ✅ Yes | `apply_iam.py` from `config/iam-*.json` | Codify as `Role` constructs |
| CloudWatch alarms + metric filter + SNS | ✅ Yes | `apply_alarms.py` from [`config/alarms.json`](../../config/alarms.json) | Codify |
| Step Functions state machine | ✅ **Yes** — `grocery-ingestion-dev` (STANDARD) | `apply_state_machine.py` | **Adopt**, then codify |
| EventBridge schedule | ✅ **Yes, ENABLED** — `grocery-price-refresh-dev`, daily 03:00 NZ | manual | **Adopt**, then codify |
| Lambda function(s) | ✅ **Yes, deployed** — `grocery-orchestrator-dev`, `grocery-ingestion-dev` | `build_lambda.py`, deployed manually | **Adopt**, then codify function + alias |
| API Gateway REST API | ✅ **Yes** — `grocery-orchestrator-api-dev` (`woqmel35lk`), stage `dev`, `POST /chat` | manual | **Adopt or replace — decide** ([08 §10](08-OPEN-DECISIONS.md)) |
| SnapStart alias | ✅ **Yes** — `grocery-orchestrator-dev:live` → version `6` | manual | **Adopt**, then codify |
| S3 + CloudFront frontend | ❌ Not yet | — | Create |
| AWS Budgets | ❌ Not yet | — | Create |
| S3 artefact bucket | ❌ Not yet | — | Create |

The naming suffix `-dev` on every existing resource is deliberate: the config
headers state it lets *generated resources coexist with manually created ones
during the migration to IaC*. CDK will honour the same names.

> ✅ **Resolved (2026-08-29).** The orchestrator's live contract uses
> `grocery-products-dev` (GSI `GSI1`) and `grocery-idempotency-dev`; the
> differently-named tables in [`datasets/dynamodb_schema/`](../../datasets/dynamodb_schema/)
> (`SmartGroceryProducts`/`CategoryPriceIndex`, `SmartGroceryRecipes`, owner
> `AUT-AWS-DataPipeline`) are a **separate lineage**. **Decision:** the CDK
> adopts the `grocery-*-dev` tables as authoritative; the `SmartGrocery*` tables
> are the **raw upstream dataset**, brought in via a B→A transform in ingestion,
> never adopted as serving tables. See [08-OPEN-DECISIONS §1](08-OPEN-DECISIONS.md)
> and [03 → IngestionStack → Data source](03-STACK-SPECS.md).

## 4. The $0-budget posture

The workshop has a **$0 budget**. The guiding rule, from the project brief:
*use free-tier services; if a service requires upfront or non-trivial cost,
don't implement it — note it in the docs so it can be added at market stage.*
This shapes several infrastructure choices, developed fully in
[07-COST-AND-SCALING](07-COST-AND-SCALING.md):

- **DynamoDB** stays `PAY_PER_REQUEST` (on-demand): no idle cost, generous free
  tier, and the workshop's traffic is tiny.
- **Lambda + API Gateway REST + Step Functions + CloudWatch + SNS + S3 +
  CloudFront** all have free tiers that comfortably cover workshop-scale usage.
- **Bedrock** is pay-per-token with no free tier, but token volumes are small
  and the model plane already prefers the cheapest capable model (Nova Lite at
  ~$0.00006/1k input). An **AWS Budget** with an alarm is the guardrail.
- **CI/CD** uses **GitHub Actions + OIDC** (free) for deployment. **AWS
  CodePipeline** — which the boilerplate brief mentioned — costs ~$1/month per
  active pipeline plus CodeBuild minutes, so it is **specified but not
  implemented**, per the budget rule (see [05-CICD](05-CICD.md)).
- **NAT Gateways, VPCs, RDS, provisioned capacity, Aurora, OpenSearch,
  Kendra** — none are used. The architecture is deliberately VPC-less; Lambda
  reaches DynamoDB and Bedrock over AWS's network without a VPC, which also
  avoids the NAT Gateway that is the classic silent cost in a "serverless"
  bill.

## 5. Well-Architected framing

The design is organised around the six pillars of the
[AWS Well-Architected Framework](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html).
Each pillar is carried through the specific docs; this is the map.

| Pillar | Where it lives | Headline choices |
|--------|----------------|------------------|
| **Operational Excellence** | [06-DEPLOYMENT-GUIDE](06-DEPLOYMENT-GUIDE.md), [05-CICD](05-CICD.md) | Everything is code; deploys are `cdk diff`-reviewed; alarms and dashboards ship with the component they watch, not in a cleanup phase. |
| **Security** | [04-SECURITY](04-SECURITY.md) | Least-privilege IAM per component; two separate roles; secrets in SSM/Secrets Manager; a numbered Guardrail on every model call; PII-free logs; fail-closed production mode. |
| **Reliability** | [03-STACK-SPECS](03-STACK-SPECS.md), [01-ARCHITECTURE](01-ARCHITECTURE.md) | Managed, multi-AZ services only; idempotency; per-retailer isolation in ingestion; DynamoDB PITR; `RETAIN` on stateful resources. |
| **Performance Efficiency** | [03-STACK-SPECS](03-STACK-SPECS.md), [07-COST-AND-SCALING](07-COST-AND-SCALING.md) | SnapStart to cut cold starts; right-sized Lambda memory; on-demand DynamoDB that scales to zero; model routing to the cheapest capable model. |
| **Cost Optimization** | [07-COST-AND-SCALING](07-COST-AND-SCALING.md) | Free-tier-first; on-demand everything; a Budget with alarms; paid services explicitly deferred. |
| **Sustainability** | [07-COST-AND-SCALING](07-COST-AND-SCALING.md) | Scale-to-zero serverless means no idle compute; small models by default. |

## 6. Reading references

For the team to make fully-informed decisions, the primary sources behind these
designs:

- **AWS CDK v2 Developer Guide** — https://docs.aws.amazon.com/cdk/v2/guide/home.html
- **CDK API reference (aws-cdk-lib)** — https://docs.aws.amazon.com/cdk/api/v2/
- **Importing existing resources into a CDK stack** — https://docs.aws.amazon.com/cdk/v2/guide/use_cfn_template.html and the `cdk import` command: https://docs.aws.amazon.com/cdk/v2/guide/cli.html#cli-import
- **Lambda SnapStart** — https://docs.aws.amazon.com/lambda/latest/dg/snapstart.html
- **AWS Well-Architected Framework** — https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html
- **Serverless Applications Lens** — https://docs.aws.amazon.com/wellarchitected/latest/serverless-applications-lens/welcome.html
- **API Gateway REST throttling & usage plans** — https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-request-throttling.html
- **Bedrock Guardrails** — https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html
- **GitHub Actions OIDC to AWS (no long-lived keys)** — https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services
- **AWS Budgets** — https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html
