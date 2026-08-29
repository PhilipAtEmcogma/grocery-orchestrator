# 04 — Security & Best Practices

> **Status: Design documentation for Pilot Tasks 9–12. Not yet implemented.**
>
> This is the security heart of the design. It maps the existing
> [`config/iam-*.json`](../../config/) policies into CDK constructs and states
> the controls the CDK must enforce. It follows
> [`.kiro/steering/security.md`](../../.kiro/steering/security.md) — *"AWS
> security controls are opt-in; assume nothing is enabled by default; controls
> land with the component they protect, not in a final cleanup phase."*

## 1. The principle: two roles, no overlap

The most important security property in the whole system is that the **two
Lambdas have two separate least-privilege roles that cannot do each other's
job**:

- **Orchestrator** — *reads* prices, invokes Bedrock, applies the Guardrail,
  writes idempotency records, writes X-Ray. It **cannot write prices.**
- **Ingestion** — *writes* prices (after reading to diff). It **cannot invoke a
  model, cannot touch the idempotency table.**

From the ingestion role's own comment: *"a defect in one cannot express itself
through the other's permissions."* This is a blast-radius control. The CDK must
never collapse them into a shared role for convenience.

## 2. Orchestrator role — statement by statement

Source: [`config/iam-orchestrator-role.json`](../../config/iam-orchestrator-role.json).
Role name `grocery-orchestrator-dev-role`. Managed policy: only
`AWSLambdaBasicExecutionRole` (CreateLogGroup/Stream + PutLogEvents). Inline
statements:

| Sid | Actions | Resource | Why (verbatim intent) |
|-----|---------|----------|-----------------------|
| `BedrockInvokeConfiguredModels` | `bedrock:InvokeModel`, `InvokeModelWithResponseStream` | the 4 inference-profile ARNs **plus** the 4 region-wildcarded foundation-model ARNs | Cross-region inference profiles need **both** the profile ARN *and* the underlying foundation model in every region the profile can route to; granting only the profile yields an opaque AccessDenied naming a region nobody configured. Foundation-model ARNs are account-less by design (AWS-owned). |
| `BedrockApplyGuardrail` | `bedrock:ApplyGuardrail` | the numbered Guardrail ARN (v1) | Guardrail is applied on every call when `REQUIRE_GUARDRAIL=1`. **DRAFT is deliberately not granted** so a console edit to DRAFT cannot silently change runtime behaviour. |
| `DynamoReadProducts` | `GetItem`, `BatchGetItem`, `Query`, `Scan` | `grocery-products-dev` **and** `.../index/GSI1` | Read-only. `GSI1` is a **distinct resource ARN** and must be listed explicitly — omitting it is the classic cause of a working GetItem and a failing cheapest-price Query. `Scan` is required (and easy to miss) because meal-plan candidate search pages the base table; without it every meal-plan turn fails AccessDenied while price checks keep working. |
| `DynamoIdempotency` | `GetItem`, `PutItem`, `UpdateItem` | `grocery-idempotency-dev` | Conditional writes for the atomic claim + terminal-outcome record + retry read. **No Delete** — expiry is by TTL, which needs no permission. |
| `XRayTracing` | `xray:PutTraceSegments`, `PutTelemetryRecords` | `*` | X-Ray segment writes take **no resource**, so `*` is the only expressible form. This is the *one* justified wildcard. |

### CDK translation

```ts
const role = new iam.Role(this, 'OrchestratorRole', {
  roleName: 'grocery-orchestrator-dev-role',
  assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
  managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole')],
});
role.addToPolicy(new iam.PolicyStatement({
  sid: 'BedrockInvokeConfiguredModels',
  actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
  resources: [
    `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/apac.amazon.nova-lite-v1:0`,
    `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/apac.amazon.nova-pro-v1:0`,
    `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/au.anthropic.claude-haiku-4-5-20251001-v1:0`,
    `arn:aws:bedrock:${this.region}:${this.account}:inference-profile/au.anthropic.claude-sonnet-4-5-20250929-v1:0`,
    'arn:aws:bedrock:*::foundation-model/amazon.nova-lite-v1:0',
    'arn:aws:bedrock:*::foundation-model/amazon.nova-pro-v1:0',
    'arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0',
    'arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0',
  ],
}));
role.addToPolicy(new iam.PolicyStatement({
  sid: 'BedrockApplyGuardrail',
  actions: ['bedrock:ApplyGuardrail'],
  resources: [guardrail.attrGuardrailArn],   // CDK token → follows the CDK-owned Guardrail automatically
}));
props.tables.products.grantReadData(role);          // Get/BatchGet/Query/Scan on table + indexes
props.tables.idempotency.grant(role, 'dynamodb:GetItem','dynamodb:PutItem','dynamodb:UpdateItem');
role.addToPolicy(new iam.PolicyStatement({
  sid: 'XRayTracing', actions: ['xray:PutTraceSegments','xray:PutTelemetryRecords'], resources: ['*'],
}));
```

> **Watch item:** `grantReadData` includes `dynamodb:Scan` and grants on
> `table + all indexes`, which matches the hand-written policy. Confirm in the
> assertion test that the generated policy contains `Scan` and the `GSI1` index
> ARN — a CDK version that dropped `Scan` from `grantReadData` would silently
> break meal plans. Better still, keep the explicit statement to match the
> reviewed JSON exactly, and assert on it.

## 3. Ingestion role — statement by statement

Source: [`config/iam-ingestion-role.json`](../../config/iam-ingestion-role.json).
Role name `grocery-ingestion-dev-role`. Managed: `AWSLambdaBasicExecutionRole`.

| Sid | Actions | Resource | Why |
|-----|---------|----------|-----|
| `DynamoDiffAndWriteProducts` | `Query`, `PutItem`, `BatchWriteItem` | `grocery-products-dev` (base table only, **no GSI1**) | `Query` is here so ingestion can **diff before it writes** — a write-only writer cannot know what it is about to change, which is how `unit_price_nzd "2490.00"` once reached six live rows with no signal. Scoped to the base table because the diff queries by `store_key` (base PK); GSI1 is not needed and not granted. |
| `XRayTracing` | `xray:PutTraceSegments`, `PutTelemetryRecords` | `*` | as above |

Note what is **absent**: no `bedrock:*`, no idempotency-table access, no
products-table read via GSI1. That absence is the control.

## 4. The deploy role (CI/CD identity)

Deployment needs its own identity — this is new (the apply scripts used the
developer's own credentials). See [05-CICD](05-CICD.md) for the OIDC setup. Key
points:

- A **GitHub Actions OIDC role** (`grocery-deploy-dev-role`) trusted only for
  this repo and (ideally) the `main` branch / a `deploy` environment. No
  long-lived access keys anywhere.
- Its permissions are the CDK deploy set: CloudFormation, and the
  create/update rights for the resource types in the stacks — **scoped by the
  CDK bootstrap's execution role**, not `AdministratorAccess`. The standard
  pattern is CDK's bootstrap `cfn-exec` role with a **permissions boundary**
  limiting it to this project's resources. Document the boundary; do not grant
  `*`.
- The deploy role is **never** the Lambda execution role. Three identities:
  deploy, orchestrator, ingestion.

## 5. Secrets and configuration

- **No secret exists in the anonymous pilot.** There are no API keys, no DB
  passwords (DynamoDB uses IAM, not credentials), no third-party tokens. So
  **AWS Secrets Manager is not provisioned** — it costs ~$0.40/secret/month and
  there is nothing to put in it. This is the correct $0 choice; the seam is
  noted for when live retailer credentials or a Cognito app secret appear.
- **Runtime configuration** (model routing, feasibility floor) lives in **SSM
  Parameter Store** standard parameters (free), per [03 §ServiceStack](03-STACK-SPECS.md).
- **Account id** is never hardcoded (per `aws_placeholders.py`'s reasoning);
  CDK derives it from the deploy identity.
- `.env` files are never committed and never used for secrets (`security.md`).
  The repo already runs **`detect-secrets`** with a maintained
  [`.secrets.baseline`](../../.secrets.baseline) and gates it in CI — the CDK
  work must keep that baseline green.

## 6. Production-mode fail-closed

`tech.md` and `security.md` require: *a production stage fails closed unless
DynamoDB, Bedrock, a numbered Guardrail version, stored idempotency, strict
CORS, and named resources are configured. Missing settings must never silently
select demo adapters.* The CDK enforces the infrastructure half of this:

| Control | Dev (pilot) | Production mode (future `prod` stage) |
|---------|-------------|----------------------------------------|
| `USE_DYNAMODB` / `USE_BEDROCK` | `1` | `1`, and synth **fails** if unset |
| `REQUIRE_GUARDRAIL` | `1` | `1` |
| `BEDROCK_GUARDRAIL_VERSION` | numbered (`1`) | numbered; synth **fails** on `DRAFT`/empty |
| `CORS_ORIGIN` | CloudFront domain (or `*` only while non-prod) | a real origin; synth **fails** on `*` |
| Resource names | present | present |

Implement these as `config.ts` assertions that throw at synth time for the
`prod` stage. A misconfiguration should fail *before* a deploy, loudly — the
same philosophy as `aws_placeholders.assert_resolved()`.

## 7. Data protection

- **Encryption at rest:** DynamoDB (AWS-managed keys), S3 artefact + frontend
  buckets (SSE-S3). PITR on the products table (already on; must survive every
  deploy).
- **Encryption in transit:** CloudFront redirects to HTTPS; API Gateway is
  HTTPS-only; all AWS SDK calls are TLS.
- **PII-safe logging (Req 11.5) is an infrastructure concern too.** The CDK must
  **not** set `POWERTOOLS_LOGGER_LOG_EVENT=true` (it would dump the user's
  message into CloudWatch). Access-log formats on API Gateway must exclude
  request bodies. Log retention is finite (14 days) rather than never-expire.
  The Guardrail additionally `ANONYMIZE`s email/phone/address/name on output and
  `BLOCK`s card/SSN/password on input (`config/guardrail.json`).
- **Privacy Act 2020 (NZ)** applies to sessions; the idempotency/session data is
  TTL-scoped. AgentCore Memory (persistent shopper memory) is explicitly gated
  behind Cognito + consent + TTL + deletion + privacy review — not in the pilot.

## 8. Guardrail as a security control (not just quality)

Every Bedrock generation call goes through a **numbered** Guardrail
(`security.md`: *"No direct model invocation without one"*). The Guardrail
(`config/guardrail.json`) provides: PROMPT_ATTACK (HIGH, input), content filters
(misconduct/violence/sexual/hate/insults), six domain-denied topics (unsafe food
prep, disordered-eating support, medical advice, age-restricted products,
foraging, system-prompt disclosure), a short word policy, and PII handling. The
IAM policy grants only the numbered version, never DRAFT — a deliberate control
so a console edit cannot change runtime behaviour without a code review.

## 9. Security acceptance checklist (for the CDK)

Assert these in `test/` (see [02 §7](02-CDK-SCAFFOLD.md)):

- [ ] Orchestrator role has **no** `dynamodb:PutItem`/`DeleteItem`/`BatchWriteItem` on products.
- [ ] Ingestion role has **no** `bedrock:*` and **no** access to the idempotency table.
- [ ] The only `Resource: "*"` anywhere is the two X-Ray actions.
- [ ] Both DynamoDB tables are `RETAIN` (Strategy B) or unmanaged (Strategy A).
- [ ] Guardrail version is numbered; no role grants DRAFT.
- [ ] API stage has throttling **and** a usage plan.
- [ ] `POWERTOOLS_LOGGER_LOG_EVENT` is never set true.
- [ ] Frontend + artefact buckets block all public access.
- [ ] No 12-digit account id appears in any synthesized template as a literal the deploy identity didn't provide.
- [ ] `detect-secrets` baseline stays green.
