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

## Schedule

| Week | Component | Control |
|---|---|---|
| 1 | Local env | SSO not root, secret scanning, `pip-audit` |
| 1 | Tool Lambdas | Least-privilege roles, scoped table ARNs |
| 2 | DynamoDB | PITR on, encryption verified, no PII in price tables |
| 2 | Generation node | **Bedrock Guardrail created and attached** |
| 2 | Intent node | Input validation, prompt-injection delimiting |
| 3 | API Gateway | Throttling, usage plan, Cognito authoriser |
| 3 | Sessions | TTL, scoping, Privacy Act review |
| 4 | Observability | Structured logging, tracing, alarms |
