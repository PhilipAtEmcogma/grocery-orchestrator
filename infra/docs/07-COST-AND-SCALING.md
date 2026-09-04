# 07 — Cost & Scaling

> **Status: Design documentation. Not yet implemented.**
>
> The workshop budget is **$0**. The rule: use free-tier services; if a service
> needs upfront/recurring cost, don't implement it — note it here for the market
> stage. This doc is the cost ledger and the scaling plan.

## 1. The free-tier cost model (per month, workshop scale)

Workshop scale means single-digit-to-low-hundreds of chat turns per day and one
scheduled ingestion run. Against that, every core service sits inside its free
tier:

| Service | Free tier | Workshop usage | Est. cost |
|---------|-----------|----------------|-----------|
| **Lambda** | 1M requests + 400,000 GB-s/mo | thousands of invocations | **$0** |
| **API Gateway (REST)** | 1M calls/mo (first 12 mo) | thousands of calls | **$0** (see note) |
| **DynamoDB (on-demand)** | 25 GB storage + 25 WCU/RCU-equiv free; on-demand billed per request | tiny catalogue, few reads | **~$0** |
| **Step Functions (Standard)** | 4,000 state transitions/mo | ~1 run/day × few states | **$0** |
| **EventBridge** | scheduled rules free | 1 rule | **$0** |
| **CloudWatch** | 10 metrics, 10 alarms, 5 GB logs free | **12 alarms** (2026-09-04), small logs | **~$0.20/mo** — two alarms past the free ten, at $0.10 each. Named rather than rounded to zero: the point of this table is that a number nobody checked is how a bill surprises you |
| **X-Ray** | 100,000 traces/mo free | well under | **$0** |
| **SNS** | 1,000 email notifications/mo free | a handful of alarms | **$0** |
| **S3 (artefacts + frontend)** | 5 GB + 20k GET + 2k PUT free (first 12 mo) | tiny | **~$0** |
| **CloudFront** | 1 TB egress + 10M requests/mo **always-free** | tiny | **$0** |
| **SSM Parameter Store** | standard params free | a few params | **$0** |
| **IAM / CloudFormation / STS / OIDC** | free | — | **$0** |
| **Secrets Manager** | none free (~$0.40/secret/mo) | **not provisioned** | **$0** |
| **Amazon Bedrock** | **no free tier** — pay per token | small volumes, cheap models | **see §2** |

> **API Gateway REST note:** the 1M-calls free tier is a first-12-months
> benefit; after that REST is ~$3.50/M calls. HTTP API (not REST) is ~$1.00/M
> and *has no 12-month cliff*. The project mandates **REST** (`tech.md`, for
> usage plans + Cognito authorizer + throttling), which is correct; at workshop
> volume the post-12-month cost is still cents. Noted, not a reason to change.

**Bottom line: the pilot runs at effectively $0**, with Bedrock tokens the only
real spend, bounded by an AWS Budget.

## 2. Bedrock — the one real cost, and how it's bounded

Bedrock is pay-per-token with no free tier. Three things keep it near-zero:

1. **The model plane prefers the cheapest capable model.**
   [`config/models.json`](../../config/models.json) routes `classify_intent`,
   `repair_plan` and `generate_prose` to the *fast* tier (Nova Lite:
   **$0.00006/1k input, $0.00024/1k output**) and only `generate_plan` to the
   *quality* tier. A full meal-plan turn is fractions of a cent.
2. **A throughput ceiling already exists and is measured.** The README records
   *6.7 meal-plan turns/minute (4.0 when repair fires), bound by a Nova Lite quota
   that cannot be raised by request.* `scripts/check_quotas.py` derives it live.
   That ceiling doubles as a natural cost cap — you cannot spend fast.
3. **An AWS Budget with an alarm** (Observability stack, [03](03-STACK-SPECS.md)):
   a small monthly USD limit with 80% / 100% SNS notifications. Two budgets are
   free. This is the backstop if something loops.

Illustrative worst case: 1,000 meal-plan turns/day at, say, 4k input + 1k output
tokens on Nova Lite ≈ 1000 × (4×$0.00006 + 1×$0.00024) ≈ **$0.48/day**. Even
100× the expected workshop traffic is lunch money — and the Budget catches a
runaway before it matters.

## 3. Scaling levers (when it grows beyond a workshop)

The architecture scales without redesign because it is serverless and
scale-to-zero. Levers, cheapest-first:

| Lever | What it does | When |
|-------|--------------|------|
| **Lambda memory tuning** | more memory = more CPU = shorter turns; sometimes *cheaper* net (GB-s ↓ despite MB ↑). Tune with AWS Lambda Power Tuning. | measure p50/p99 first |
| **SnapStart** | cuts cold-start latency (already in the design) | on from day one |
| **DynamoDB on-demand** | absorbs traffic spikes automatically, no capacity planning | already chosen |
| **Model routing** | shift more traffic to Nova Lite; promote to Sonnet only where a scorecard justifies it | via SSM, no deploy |
| **API caching** | API Gateway response cache for hot identical queries | costs $ — market stage |
| **DynamoDB DAX** | microsecond read cache | only if reads dominate — market stage |
| **Provisioned concurrency** | eliminate cold starts entirely | costs $ — market stage |
| **WebSocket streaming** | perceived latency for long meal plans; contract unchanged (`tech.md`) | approved later upgrade |

The **binding constraint** at scale is the **Bedrock quota**, not the AWS
plumbing. The README is explicit that the Nova Lite quota *cannot be raised by
request* — so real scale means qualifying and routing to additional
models/regions (Bedrock cross-Region inference profiles, gated in `tech.md`),
not adding compute. That is a model-plane decision, documented as
[Pilot Task 7](../../.kiro/specs/grocery-orchestrator/tasks.md).

## 4. Services deliberately NOT used (and the cost reason)

Per the $0 rule, these are named so the team knows they were considered and
why they're out — each is a market-stage candidate:

| Service | Why not now | When it earns its place |
|---------|-------------|-------------------------|
| **RDS / Aurora (PostgreSQL)** | The boilerplate brief mentioned RDS, but the app uses **DynamoDB**, which is already live, on-demand, and free-tier. RDS means an always-on instance (min ~$12–15/mo) and a VPC. | never, unless a relational access pattern appears |
| **VPC + NAT Gateway** | Not needed — Lambda reaches DynamoDB/Bedrock over AWS's network. NAT Gateway is ~$32/mo + data — the classic silent serverless cost. | only if private networking is required |
| **Cognito** | The pilot is anonymous. Cognito has a free tier but adds a login surface, WAF pressure, and privacy obligations (`security.md` gates it before owned/public surfaces). | before any user-owned data or public launch |
| **AWS WAF** | ~$5/mo web ACL + per-rule + per-request. No public authenticated surface yet. | with Cognito / a public surface |
| **CodePipeline + CodeBuild** | ~$1/pipeline/mo + build minutes. GitHub Actions + OIDC does dev CD free. | multi-account promotion / market stage — see [05 §4](05-CICD.md) |
| **Secrets Manager** | ~$0.40/secret/mo and there is no secret in the anonymous pilot. | when live retailer creds / a Cognito secret appear |
| **Bedrock Knowledge Bases / OpenSearch** | KB is gated to cited recipe/catalogue retrieval only, never prices; OpenSearch Serverless has a meaningful floor cost. | Pilot Task 15, and only if evaluated worthwhile |
| **AgentCore (Gateway/Runtime/Memory)** | Proposed under [ADR 0002](../../docs/adr/0002-staged-agentcore-and-managed-ai-services.md), mentor approval required; some components have real runtime cost. | after approval + purpose/cost/rollback evidence |
| **DynamoDB provisioned capacity** | On-demand is cheaper at this volume and needs no capacity planning. | steady high-volume, predictable load |

## 5. The cost-governance summary

- **Now:** ~$0/month. Bedrock tokens (cents) are the only spend; an AWS Budget
  bounds them; on-demand everything else scales to zero.
- **The one recurring cost to watch:** Bedrock. The Budget + the measured quota
  ceiling + cheap-model routing make it self-limiting.
- **Every paid service is deferred with a note**, so a future market build has a
  ready checklist of what to switch on and roughly what it costs.
