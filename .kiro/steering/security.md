# Security rules — apply to all generated code

AWS security controls are **opt-in**. Assume nothing is enabled by default.
Controls land with the component they protect, not in a final cleanup phase.

## Always

- **IAM:** least privilege only. Never `"*"` in Action or Resource. Scope each
  component to named resources and required actions.
- **Secrets:** AWS Secrets Manager or SSM Parameter Store. Never hardcoded,
  placed in environment variables, or committed in `.env` files.
- **Guardrails:** every Bedrock generation call uses a numbered Bedrock
  Guardrail. No direct model invocation without one.
- **Untrusted input:** validate and sanitise user text at the intent node;
  delimit it before prompting and assume prompt-injection attempts.
- **Money:** `Decimal` only. Never `float`.
- **Grounding:** every output price carries a `citation_ref` traceable to a
  DynamoDB record. No price may originate from model generation.
- **Arithmetic:** verify with `assert_arithmetic()` before emission; never trust
  model-computed totals.
- **DynamoDB:** encryption at rest and point-in-time recovery are explicit.
- **API Gateway:** every stage has throttling and a usage plan.
- **Logging:** no PII. Log `session_id`, never message text, raw location,
  dietary data, prompts, or review snapshot contents.
- **Sessions:** scope by session id with TTL. Privacy Act 2020 applies.
- **Exact provenance:** citations identify exact table, partition key, and sort
  key; target validation compares values with retrieved records and requires
  citation-before-use. Current enforcement limits remain documented until the
  retrieved-record comparison follow-up lands.
- **Production mode:** reject wildcard CORS, draft/missing Guardrails, in-memory
  stores, scripted models, fixture repositories, and unnamed resources.

## MCP, Gateway, Runtime, and managed evaluation

- **Local MCP first:** strict input/output schemas, operation allowlists, row,
  call and time caps, rate limits, sanitised results, and privacy-safe operation
  audit. No raw AWS, database, filesystem, network, scraping, write, citation,
  or unguarded-generation primitive.
- **AgentCore Gateway:** proposed only. Before approval, require Cognito or an
  approved workload identity, AgentCore Identity, least-privilege resource and
  identity policies, AgentCore Policy enforcement, WAF for a public surface,
  target allowlists, quotas/timeouts, request/response validation, privacy-safe
  audit, and a tested disable/fallback path. Gateway may expose only the same
  coarse complete-application operations as local MCP and never bypass the
  deterministic graph.
- **AgentCore Runtime reviewer:** separate identity, deployment, logs, storage,
  and network boundary. It receives only capped sanitised ingestion snapshots,
  never shopper messages, location, dietary values, sessions, or credentials.
  Tools are read-only and allowlisted; egress, calls, rows, tokens, runtime, and
  cost are capped. Findings require citations, schema checks, deterministic
  post-validation, and human approval. The role has no production write,
  publication, or shopper-path permission.
- **Managed evaluations:** use versioned, non-PII datasets and immutable run
  metadata. Managed evaluators receive no shopper PII or secrets. Retention,
  access, export, deletion, and cost limits are defined before upload. Their
  results supplement local security and correctness gates and cannot override
  a failed invariant.
- **Artefacts and triggers:** S3 buckets use encryption, versioning, block public
  access, scoped prefixes, lifecycle/retention, and deletion procedures.
  DynamoDB Streams -> SQS uses least privilege, bounded retries, and a DLQ; SNS
  carries operator/approval notifications without sensitive payloads.
- **Memory:** AgentCore Memory is forbidden until Cognito ownership, explicit
  consent, purpose limitation, TTL, user deletion, privacy review, and
  revocation are designed and tested. It never contains authoritative prices.

## Schedule

| Stage | Component | Control shipped with it |
|---|---|---|
| 1 | Local environment | SSO not root, secret scanning, `pip-audit` |
| 1 | Tool Lambdas | Least-privilege roles and named resource ARNs |
| 2 | DynamoDB | PITR, encryption, and no PII in price tables |
| 2 | Generation/intent | Numbered Guardrail, tagging, validation, delimiting |
| 3 | REST API | Strict CORS, throttling, usage plan; WAF before public expansion |
| 3 | Sessions | TTL, scoping, Privacy Act review |
| 4 | Observability | Privacy-safe logs/traces, alarms, notifications, budgets |
| Local MCP | MCP façade | Read-only allowlists, caps, schemas, audit |
| Proposed Gateway | AgentCore Gateway | Cognito/Identity, Policy, WAF, quotas, fallback test |
| Proposed reviewer | AgentCore Runtime | Isolated role/data, caps, no writes, human approval |
| Proposed evaluations | Managed eval services | Versioned non-PII datasets, retention/deletion, provenance |
| Later ownership | Memory/preferences | Cognito, consent, TTL, export/deletion and privacy review |

Logging and tracing are verified offline; deployed alarms still require service
metrics. The enforcement point for Privacy Act-safe telemetry remains
`src/observability/base.py`, not individual call sites.
