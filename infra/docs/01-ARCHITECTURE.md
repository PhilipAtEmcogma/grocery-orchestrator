# 01 — Architecture Plan

> **Status: Design documentation for Pilot Tasks 9–12. Not yet implemented.**
>
> This describes the *target* AWS architecture the CDK app will provision. The
> application components it references exist today; the AWS resources that host
> them are partly built (see [00-OVERVIEW §3](00-OVERVIEW.md)).

## 1. The one-paragraph version

A shopper's browser loads a **static chat UI from S3 through CloudFront**. The
UI calls a single **`POST /chat` REST endpoint on API Gateway**, which invokes
the **orchestrator Lambda** (Python 3.13, published behind a **SnapStart
alias**). The Lambda runs a deterministic **LangGraph** — classify intent,
**retrieve prices from DynamoDB first**, then call **Amazon Bedrock** (through a
numbered **Guardrail**) to produce a grounded answer, verifying the arithmetic
and the citations in code before returning. Separately, on a schedule,
**EventBridge** starts a **Step Functions** state machine that fans out to an
**ingestion Lambda** per retailer to refresh prices in DynamoDB. Everything is
watched by **CloudWatch, X-Ray and AWS Budgets**, and everything is provisioned
by one **AWS CDK app**.

## 2. Services and their roles

| # | AWS service | Role in this system | Pilot Task | Cost posture |
|---|-------------|---------------------|-----------|--------------|
| 1 | **Amazon S3** (frontend) | Hosts the static chat UI (HTML/JS or SPA build) | later | Free-tier |
| 2 | **Amazon CloudFront** | HTTPS + CDN in front of the S3 UI; TLS termination | later | Free-tier |
| 3 | **Amazon API Gateway (REST)** | The `POST /chat` edge: CORS, throttling, usage plan, (later) Cognito authorizer | 10 | Free-tier (1M calls/mo) |
| 4 | **AWS Lambda — orchestrator** | Runs the LangGraph turn; entry `src.handler.lambda_handler`; zip package + SnapStart alias | 10 | Free-tier (1M req + 400k GB-s) |
| 5 | **Amazon Bedrock (Converse)** | Grounded generation — Claude Haiku/Sonnet, Nova Lite/Pro — routed from `config/models.json` | (live) | Pay-per-token (small) |
| 6 | **Amazon Bedrock Guardrail** | Content-safety + prompt-attack + PII + denied-topics filter on every model call | (live, v1) | Per-unit (small) |
| 7 | **Amazon DynamoDB — products** | `grocery-products-dev`: the price catalogue; base table + `GSI1`; PITR on | 9 (adopt) | On-demand free-tier |
| 8 | **Amazon DynamoDB — idempotency** | `grocery-idempotency-dev`: turn de-duplication; TTL on | 9 (adopt) | On-demand free-tier |
| 9 | **Amazon EventBridge** | Cron rule that starts the daily price refresh | 13 | Free |
| 10 | **AWS Step Functions** | Inline Map fan-out, one branch per retailer, isolating failures | 13 | Free-tier (4k transitions/mo) |
| 11 | **AWS Lambda — ingestion** | Refreshes one retailer's prices into DynamoDB; entry `ingestion.handler.lambda_handler` | 13 | Free-tier |
| 12 | **Amazon CloudWatch** | Logs, EMF metrics, dashboards, and the two day-one alarms | 12 | Free-tier |
| 13 | **AWS X-Ray** | Traces retrieval and every model call, including repairs | 12 (live SDK) | Free-tier (100k traces/mo) |
| 14 | **Amazon SNS** | Delivers alarm notifications to operators | 12 | Free-tier |
| 15 | **AWS Systems Manager Parameter Store (SSM)** | Runtime config the operator can retune without a redeploy (model routing, feasibility floor) | 10 | Free (standard params) |
| 16 | **AWS Secrets Manager** *(only if a real secret appears)* | Any genuine secret; none exists in the anonymous pilot | later | ~$0.40/secret/mo → deferred |
| 17 | **AWS Budgets** | A cost ceiling with an alarm — the backstop on Bedrock spend | 12 | 2 budgets free |
| 18 | **AWS IAM** | Two least-privilege execution roles + the deploy role | 10 | Free |
| 19 | **AWS CloudFormation** (via CDK) | The engine CDK drives; holds the deployed state | 9–12 | Free |

Services deliberately **not** used, and why, are in
[07-COST-AND-SCALING §4](07-COST-AND-SCALING.md) (RDS, VPC/NAT, Cognito-now,
WAF-now, containers, CodePipeline-now).

## 3. Request path (the shopper turn)

```
Browser
  │  GET /  (static assets)
  ▼
CloudFront ──▶ S3 (frontend bucket, OAC-restricted; not public)
  │
  │  POST /chat  { message, session_id, location }   [HTTPS, CORS-checked]
  ▼
API Gateway (REST)
  • CORS: echoes the one configured origin, never "*", in production mode
  • Throttling + usage plan: caps burst/steady rate
  • (later) Cognito authorizer
  │  AWS_PROXY integration
  ▼
Lambda: orchestrator  (SnapStart alias :live, Python 3.13)
  • src.handler.lambda_handler — the "always returns a valid ChatResponse" boundary
  • Powertools logger / tracer / metrics attach ONLY here
  │
  ├─▶ DynamoDB grocery-products-dev  (GetItem / Query GSI1 / Scan)   ── retrieval BEFORE generation (graph invariant)
  │
  ├─▶ DynamoDB grocery-idempotency-dev (conditional Put / Update / Get)
  │
  └─▶ Bedrock Converse  (via langchain-aws)  WITH Guardrail applied
        • model chosen by registry from SSM/config, never hardcoded
        • arithmetic + citations verified in code before emission
  │
  ▼
API Gateway ──▶ CloudFront ──▶ Browser   (contract-valid JSON, always)
```

Two properties of this path are **invariants**, enforced in application code and
not by infrastructure — the infrastructure must simply never make them
impossible:

- **Retrieval before generation.** Prices come from DynamoDB, never from the
  model. IAM gives the orchestrator *read* on products and *no write*.
- **The response is always contract-valid.** Even a bug answers HTTP 500 with a
  parseable `ChatResponse` body. Infrastructure keeps the Lambda's timeout
  (see §6) longer than the worst-case turn so the failure is *our* structured
  error, not API Gateway's raw 504.

## 4. Data-refresh path (ingestion)

```
EventBridge (cron: daily 06:00 NZST)
  │  StartExecution
  ▼
Step Functions state machine  (config/ingestion-state-machine.json)
  • Type: Map (Inline), ItemsPath: $.retailers, MaxConcurrency: 3
  • one branch per retailer → Task: invoke Lambda ingestion
  • Retry on transient Lambda errors (3×, backoff 2, 5s)
  • Catch(States.ALL) INSIDE the item processor → RecordRetailerFailure (Pass)
  ▼
Lambda: ingestion  (entry ingestion.handler.lambda_handler)
  • fixture / recorded adapters first (no live retailer traffic yet)
  • Query-then-write DynamoDB (diff before overwrite)
  ▼
DynamoDB grocery-products-dev   (Query / PutItem / BatchWriteItem)
```

The **per-retailer isolation** is the whole point of using Step Functions
rather than one fan-out Lambda: a retailer that is slow, rate-limited or broken
fails *alone*, and the retailers that already succeeded are still written. The
`Catch` sits *inside* the item processor for exactly this reason (see the
`_comment` fields in the state-machine JSON).

## 5. Trust boundaries and separation of duties

```
┌─────────────────────────── AWS account (ap-southeast-2) ───────────────────────────┐
│                                                                                     │
│  PUBLIC EDGE                     SHOPPER PLANE                    DATA PLANE          │
│  ┌───────────┐                   ┌─────────────────┐             ┌────────────────┐  │
│  │ CloudFront│──HTTPS──▶ API GW ─▶│ orchestrator λ  │──read────▶ │ products table │  │
│  │  + S3 UI  │                    │ role: READ only │            │  (PITR)        │  │
│  └───────────┘                    │ + Bedrock invoke│            └────────────────┘  │
│                                   │ + Guardrail     │            ┌────────────────┐  │
│                                   └─────────────────┘──cond.────▶│ idempotency    │  │
│                                                        write     │  (TTL)         │  │
│                                   ┌─────────────────┐            └────────────────┘  │
│  EventBridge ─▶ Step Functions ──▶│ ingestion λ     │──write───────────▲             │
│                                   │ role: WRITE prods│                 (same products │
│                                   │ NO model, NO idem│                  table)        │
│                                   └─────────────────┘                                │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

The **two Lambdas never share a role**. From
[`config/iam-ingestion-role.json`](../../config/iam-ingestion-role.json): the
orchestrator *reads* prices and cannot write them; ingestion *writes* prices and
cannot invoke a model or touch the idempotency table. *"Neither can do the
other's job, so a defect in one cannot express itself through the other's
permissions."* The CDK must preserve this split — it is a security property, not
an accident of how the scripts were written. Full IAM detail is in
[04-SECURITY](04-SECURITY.md).

Note the two Lambdas share **one deployment artefact** (`build/lambda.zip`) but
are **two functions** with two entry points and two roles. The CDK defines two
`Function` constructs from the same asset.

## 6. Key non-negotiable parameters

These come from [`.kiro/steering/tech.md`](../../.kiro/steering/tech.md) and the
running code; the CDK must encode them exactly.

| Parameter | Value | Source / reason |
|-----------|-------|-----------------|
| **Region** | `ap-southeast-2` (Sydney), **all** resources | `tech.md`; **not** `ap-southeast-6` Auckland |
| **Runtime** | Python 3.13 | `tech.md`, `build_lambda.py` |
| **Packaging** | Lambda **zip**, not container | container forfeits SnapStart |
| **SnapStart** | Enabled on a **published alias** | zip-only; cuts cold start |
| **Orchestrator timeout** | 30 s | design.md; longer than worst-case turn |
| **Orchestrator memory** | 512 MB–1 GB | design.md; tune with the cost/perf trade in [07](07-COST-AND-SCALING.md) |
| **Handler (orchestrator)** | `src.handler.lambda_handler` | `src/handler.py` |
| **Handler (ingestion)** | `ingestion.handler.lambda_handler` | `build_lambda.py` |
| **API transport** | API Gateway **REST**, synchronous | `tech.md`; WebSocket is a later upgrade, contract unchanged |
| **Money** | `Decimal` in code, strings on the wire | never `float` |
| **CORS (prod mode)** | one configured origin, never `*` | `security.md`; `CORS_ORIGIN` env |
| **Guardrail** | numbered version, never `DRAFT` | `security.md`; env `BEDROCK_GUARDRAIL_VERSION` |

## 7. Environment-variable contract (orchestrator Lambda)

The CDK sets these on the function. Defaults are what the code falls back to;
production mode requires the non-default values (see
[04-SECURITY §6](04-SECURITY.md)).

| Env var | Prod value | Default in code | Set from |
|---------|-----------|-----------------|----------|
| `AWS_REGION` | `ap-southeast-2` | `ap-southeast-2` | Lambda runtime provides |
| `USE_DYNAMODB` | `1` | *(unset → memory repo)* | CDK |
| `USE_BEDROCK` | `1` | *(unset → scripted)* | CDK |
| `REQUIRE_GUARDRAIL` | `1` | `1` | CDK |
| `BEDROCK_GUARDRAIL_ID` | `b1xezpqe04kx` (or CDK-created id) | `""` | CDK (from Guardrail construct) |
| `BEDROCK_GUARDRAIL_VERSION` | `1` | `DRAFT` | CDK |
| `CORS_ORIGIN` | the CloudFront domain | `*` | CDK (from frontend stack) |
| `LOG_LEVEL` | `INFO` | `INFO` | CDK |
| `POWERTOOLS_SERVICE_NAME` | `grocery-orchestrator` | `grocery-orchestrator` | CDK |
| `POWERTOOLS_METRICS_NAMESPACE` | `GroceryOrchestrator` | `GroceryOrchestrator` | CDK |
| `BEDROCK_MODEL_CLAUDE_HAIKU` / `…SONNET` / `…NOVA_LITE` / `…NOVA_PRO` | model ids from `config/models.json` | `""` | SSM → CDK, or SSM at runtime |

`POWERTOOLS_LOGGER_LOG_EVENT` is **deliberately never set to true** — it would
dump the user's message into CloudWatch. The CDK must not set it. This is a
privacy control (Req 11.5), documented in `src/handler.py`.

## 8. How this maps to the diagram in the project space

The project's architecture SVG
([`Smart grocerty & meal budget assistant architecture diagram.html`](../../))
shows: User → Frontend (S3+CloudFront) → API Gateway → Lambda orchestrator →
Bedrock + DynamoDB, with EventBridge → scraper Lambda → supermarkets, and
CloudWatch across it. This design is faithful to that diagram; it adds the
detail the diagram abstracts — SnapStart, the Guardrail, the idempotency table,
Step Functions (in place of a bare "scraper"), SSM, Budgets, SNS, and the IAM
boundaries — because those are what "production-grade" and "least-privilege"
mean in practice.
