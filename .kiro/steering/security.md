# Security rules — apply to all generated code

AWS security controls are **opt-in**. Assume nothing is enabled by default.
It is our responsibility to select and correctly implement them. Controls land
with the component they protect, not in a phase at the end.

## Always

- **IAM:** least privilege only. Never `"*"` in Action or Resource. Scope
  DynamoDB permissions to specific table ARNs and specific actions.
- **Secrets:** AWS Secrets Manager or SSM Parameter Store. Never hardcoded,
  never in environment variables, never in a committed `.env`.
- **Guardrails:** every Bedrock generation call passes through a Bedrock
  Guardrail. No direct model invocation without one. Not enabled by default,
  not implied by using Bedrock.
- **Untrusted input:** user text is untrusted. Validate and sanitise at the
  intent node. Never interpolate raw user input into a prompt without
  delimiting it. Assume prompt-injection attempts.
- **Money:** `Decimal` only. Never `float`.
- **Grounding:** every price in output carries a `citation_ref` traceable to a
  DynamoDB record. No price may originate from model generation. Enforced by
  `assert_grounded()` in CI.
- **Arithmetic:** never trust model-computed totals. Verify with
  `assert_arithmetic()` before emitting. Failure triggers a repair cycle.
- **DynamoDB:** encryption at rest plus point-in-time recovery. PITR is NOT on
  by default.
- **API Gateway:** throttling and a usage plan on every stage.
- **Logging:** no PII in logs. Log `session_id`, never raw location or free
  text that could identify a user.
- **Sessions:** scope by session id with a TTL. Privacy Act 2020 applies.
- **Exact provenance:** every citation identifies the exact DynamoDB table,
  partition key, and sort key; values must equal the retrieved record and the
  citation must precede use.
- **MCP tools:** validate inputs and outputs, use strict allowlists, cap rows and
  calls, enforce timeouts/rate limits, sanitize results, and audit operation
  names without logging arguments that contain personal data. Initial tools
  are read-only and expose no raw AWS, database, filesystem, network, scraping,
  or generation primitive.
- **Agents:** bounded specialist agents have least-privilege read-only tools and
  no price-publication or production-write authority. Human approval is
  required before acting on a review finding.
- **Production mode:** reject wildcard CORS, draft/missing Guardrails,
  in-memory stores, scripted models, fixture repositories, and unnamed
  resources in a production stage.

## Schedule

| Week | Component | Control |
|---|---|---|
| 1 | Local env | SSO not root, secret scanning, `pip-audit` |
| 1 | Tool Lambdas | Least-privilege roles, scoped table ARNs |
| 2 | DynamoDB | PITR on, encryption verified, no PII in price tables |
| 2 | Generation node | **Bedrock Guardrail created and attached** |
| 2 | Intent node | Input validation, prompt-injection delimiting |
| 3 | API Gateway | Strict CORS, throttling, and usage plan for anonymous pilot |
| Later ownership phase | API Gateway | Cognito authoriser before user-owned data or preferences |
| 3 | Sessions | TTL, scoping, Privacy Act review |
| 4 | Observability | Structured logging, tracing, alarms |

Week 4's logging and tracing are done and verified offline (Task 6.7). Alarms
are not: they need metrics from a deployment to alarm on. The logging half
carries a Privacy Act constraint of its own — Req 11.5, design.md §12.4 — and
the enforcement point is `src/observability/base.py`, not each call site.
