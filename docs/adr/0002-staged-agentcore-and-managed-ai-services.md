# ADR 0002: Stage AgentCore and managed AI services around the deterministic core

- **Status:** Reviewer Runtime approved under autonomous delegation and
  PROTOTYPED 2026-09-02 — deployed to AgentCore Runtime in `ap-southeast-2`,
  measured against the labelled anomaly set, and torn down. It is an
  EXPERIMENT, not a retained service: the prototype ended in teardown by
  design, and CDK codification (gate 5) is the next step before anything is
  "retained". Findings and the full reasoning record are in
  [`docs/AGENTCORE-RUNTIME-REVIEWER.md`](../AGENTCORE-RUNTIME-REVIEWER.md) §13.
  Gateway and the managed evaluations remain **withdrawn from the request**,
  not declined. The mentor gave full autonomy over ADR 0002; this records the
  decision taken under it.
- **Requested scope, narrowed 2026-08-31:** the **AgentCore Runtime reviewer
  only**. Gateway and the managed evaluations are described in full below and
  are **withdrawn from the request**, not declined — Gateway because a managed
  auth layer over two working coarse operations gets a shopper nothing, and the
  evaluations because gate 4 blocks them on us rather than on the reviewer. See
  [`docs/OPEN-REVIEW-adr-0002.md`](../OPEN-REVIEW-adr-0002.md). Either may be
  raised again as a new request.
- **Decision date:** 2026-08-23
- **Scope:** AgentCore Gateway, isolated reviewer Runtime, managed evaluations,
  and companion AWS services
- **Related requirements:** 3, 5, 9, 10, 11, 12, 13, 14
- **Relationship:** If approved, partially supersedes ADR 0001 only for
  AgentCore Gateway staging, the isolated Runtime reviewer, and companion
  managed evaluations. ADR 0001 remains controlling until approval.

## Context

The workshop should deliberately create hands-on experience with broad relevant
AWS services, especially Bedrock and AgentCore. Service count is not a product
outcome, however. Each service needs a product purpose, bounded scope,
acceptance evidence, cost and security ownership, and a rollback or removal
criterion.

The implemented reference core is the deterministic LangGraph shopper workflow,
handler, and tested adapters. Its authoritative production-pilot deployment
target remains API Gateway REST -> published Python 3.13 zip Lambda/SnapStart
alias -> LangGraph -> DynamoDB and Bedrock Converse with a numbered Guardrail;
that service plane is not yet built. Code, not an agent, owns retrieval,
dietary checks, arithmetic, repair, final grounding, and honest failure.

ADR 0001 approved local read-only MCP first and bounded specialist review, but
made all AgentCore use contingent on shopper-path p99. That is too broad for
three distinct proposals: Gateway can govern exposure of existing coarse tools;
a separate Runtime can host a non-shopper data-quality reviewer; managed
evaluations can assess deployed paths. None requires moving shopper orchestration
out of Lambda. Because this ADR is proposed, current behavior does not change.

## Decision

If approved, adopt services in stages:

1. Keep the deterministic Lambda shopper workflow as the authoritative service.
2. Build and prove a local read-only MCP façade first. Its coarse operations
   invoke the complete application service; no raw AWS, data, filesystem,
   network, acquisition, write, citation, or generation primitive is exposed.
3. Add AgentCore Gateway only as a managed exposure, authentication, policy,
   and mediation layer over those same coarse operations. Gateway does not call
   internal graph nodes and is never a bypass around LangGraph.
4. Deploy the data-quality reviewer separately in AgentCore Runtime. It reads a
   capped sanitised ingestion snapshot, produces cited schema-checked findings,
   and ends at deterministic post-validation plus human approval. It has no
   production write, price-publication, or shopper-path authority.
5. Use Bedrock Model Evaluation and AgentCore Evaluations as companion evidence
   for deployed model/Gateway/Runtime behavior. Version-controlled local tests,
   golden sets, negative controls, scorecards, and CI remain release gates.
6. Introduce companion services only under the matrix below and only in
   `ap-southeast-2`.

## Staged architecture

```text
IMPLEMENTED REFERENCE CORE
Deterministic LangGraph + handler + tested DynamoDB/Bedrock protocol adapters

AUTHORITATIVE PRODUCTION-PILOT TARGET — NOT YET DEPLOYED
Browser -> API Gateway REST -> published zip Lambda/SnapStart alias
                            -> deterministic LangGraph
                               |-> DynamoDB retrieved records
                               `-> Bedrock Converse + numbered Guardrail

PLANNED FIRST
Approved local client -> local read-only MCP -> coarse complete-app operations
                                                `-> deterministic Lambda service

PROPOSED — MENTOR APPROVAL REQUIRED
Approved managed client -> WAF/Cognito or workload identity
                        -> AgentCore Gateway + Identity + Policy
                        -> same coarse complete-app operations
                        -> deterministic Lambda service

EventBridge -> Step Functions -> validated ingestion -> DynamoDB
                           `-> Streams -> SQS/DLQ -> capped sanitised snapshot
                                                   -> isolated AgentCore Runtime
                                                   -> cited review artefact in S3
                                                   -> deterministic validation
                                                   -> human approval/notification

COMPANION EVIDENCE
Versioned S3 datasets -> local evals + Bedrock Model Evaluation
Gateway/Runtime traces -> AgentCore Evaluations -> versioned S3 results
```

## Service adoption matrix

| Service | Status and product purpose | Acceptance evidence | Rollback/removal criterion |
|---|---|---|---|
| Deterministic workflow/handler | Implemented reference core; authoritative shopper logic | Local invariant suites, contract validation, deployed SLOs when available | Retain unless a separately approved replacement proves parity |
| Local read-only MCP | Planned first; prove coarse tool contracts without managed exposure | Schema/cap/security tests and parity with direct application calls | Remove a tool if it cannot preserve contract or invariant parity |
| AgentCore Gateway, Identity, Policy | Proposed; managed auth, policy, mediation, and tool exposure | Mentor approval, local-MCP parity, least privilege, policy negative tests, quotas, audit, cost/latency, disable drill | Disable Gateway and return clients to local/direct approved path on parity, security, cost, or SLO failure |
| AgentCore Runtime reviewer | Proposed; isolated bounded ingestion-quality review | Capped non-PII snapshots, citation/schema validity, deterministic post-validation, human acceptance, no-write IAM, timeout/cost/teardown tests | Stop/delete Runtime if findings are not useful, caps fail, data isolation fails, or cost exceeds approval |
| Bedrock Model Evaluation | Proposed companion; managed model-quality evidence | Reproducible dataset/prompt/model provenance and exported per-case results | Remove if it duplicates local evidence without decision value |
| AgentCore Evaluations | Proposed companion; evaluate Gateway/Runtime traces and outcomes | Versioned evaluators, trace linkage, parity/safety/cap metrics | Remove if results cannot be reproduced or acted upon |
| Cross-Region inference profiles | Gated; measured availability/latency option | Approved Sydney-compatible profile, route scorecard, latency/cost/residency evidence | Revert to regional route if quality, residency, cost, or latency regresses |
| Bedrock Knowledge Bases | Gated; cited recipe/catalogue knowledge only | Citation accuracy and no-price-authority negative tests | Remove from route on uncited answers or price influence |
| Automated Reasoning | Gated advisory verification where supported | Agreement/disagreement study against deterministic checks | Remove if unsupported, noisy, or mistaken for enforcement |
| AgentCore Memory | Later gated; consented user preferences only | Cognito ownership, consent, TTL, deletion/export, privacy review, no-price tests | Disable and delete data on consent/privacy/control failure |
| S3 | Planned companion; versioned eval datasets/results/review artefacts | Encryption, versioning, public-access block, scoped IAM, lifecycle and restore/delete tests | Expire/delete artefacts when retention or decision value ends |
| DynamoDB Streams + SQS/DLQ | Planned companion; decoupled bounded review triggers | Filtered events, retry/DLQ/redrive tests, no shopper PII | Disable mapping and drain queue on loops, backlog, or excess cost |
| SNS | Planned companion; operator alerts and approval notifications | Confirmed subscription and non-sensitive payload tests | Remove unused topics/subscriptions or noisy notifications |
| WAF + Cognito | Required before owned/public managed surfaces | Authz negative tests, WAF rules, revocation and incident drill | Close public surface if controls fail; retain anonymous REST only within approved scope |
| CloudWatch/X-Ray/Budgets | Planned with deployed components; operations and cost evidence | Dashboards, traces, alarms, budget thresholds and drills | Remove orphaned telemetry; remove service if evidence cannot justify it |

**Accountability gate:** the backend/orchestration lead owns the implemented
core and local-stage planning. Cost and security owners for every proposed or
gated managed service are `TBD — blocks approval`; the mentor approval review
must name them before build or deployment. Shared services may name one owner
per adopted bundle, but no service proceeds with unassigned accountability.

## Preserved invariants

- No price originates from a model, Knowledge Base, Memory, reviewer, or
  Gateway. Only deterministic retrieval creates price citations.
- Gateway and MCP invoke the complete application operation; they cannot skip
  retrieval, dietary validation, arithmetic, Guardrails, repair, grounding, or
  contract assembly.
- AgentCore Runtime is outside the shopper path and cannot publish or mutate.
- Managed evaluations and Automated Reasoning are evidence, not authorities.
- Knowledge Bases may cite recipe/catalogue knowledge but never supply price
  truth. Memory may hold consented preferences only, never authoritative price.
- Every failure remains contract-valid and honest; managed-service failure must
  not produce a plausible fallback answer.

## Security and privacy boundaries

- All resources remain in `ap-southeast-2`; any cross-Region inference profile
  requires explicit residency review and approval.
- Gateway requires AgentCore Identity and Policy, least-privilege roles, strict
  target allowlists, authentication, quotas, timeouts, privacy-safe audit, and
  WAF/Cognito before a public or user-owned surface.
- Reviewer Runtime receives no shopper messages, locations, dietary data,
  sessions, prompts containing PII, or credentials. Snapshot size, rows, calls,
  tokens, runtime, egress, and cost are capped.
- S3 datasets and artefacts are encrypted, versioned, non-public, prefix-scoped,
  retained for a defined period, and deletable. Managed eval inputs are
  versioned and contain no shopper PII.
- Streams/SQS/SNS payloads contain identifiers and review status only where
  possible; DLQ and notification access is least privilege.
- Memory requires Cognito ownership, consent, purpose limitation, TTL, export,
  deletion, revocation, and Privacy Act review before adoption.

## Consequences

### Positive

- The team gains managed Bedrock and AgentCore experience without surrendering
  deterministic shopper safeguards.
- Local MCP provides a comparable baseline before Gateway complexity.
- Gateway, Runtime, and managed evaluation can be assessed and removed
  independently.
- Versioned datasets and artefacts make local and managed evidence comparable.

### Costs and risks

- More services add IAM, cost, observability, retention, and operational work.
- Gateway adds latency and a managed failure domain to an otherwise direct
  operation path.
- Reviewer findings need labelled cases and human review capacity.
- Managed evaluation can look authoritative while measuring the wrong thing;
  local deterministic gates must remain visible and controlling.
- Proposed services may be removed if their learning or product value does not
  justify complexity and cost.

## Rejected alternatives

- **Replace the shopper LangGraph with an autonomous agent:** breaks structural
  retrieval and validation guarantees.
- **Expose raw DynamoDB, Bedrock, AWS SDK, filesystem, or network tools:** makes
  policy and citation boundaries too broad to audit.
- **Skip local MCP and start with Gateway:** removes the simple parity baseline
  and confounds tool correctness with managed exposure.
- **Run the shopper workflow in AgentCore Runtime now:** rejected; this remains
  a separate p99 contingency after documented mitigations and mentor approval.
- **Give the reviewer production writes or publication authority:** rejected;
  probabilistic findings cannot become price truth.
- **Use Knowledge Bases or Memory as a price store:** rejected; neither is the
  authoritative immutable retrieved record for a shopper response.
- **Replace local evals with managed scores:** rejected; managed services do not
  prove deterministic grounding, dietary, arithmetic, or honest-failure paths.
- **Adopt services only to maximise service count:** rejected; each service must
  survive its product-purpose, evidence, cost, and removal review.

## Approval gates

Before any proposed component is built or deployed:

1. Mentor approves this ADR and the component-specific scope.
2. The local MCP operation contract is proven before Gateway work.
3. Threat model, IAM, identity/policy, PII classification, retention/deletion,
   quotas, timeout, cost budget, observability, and rollback plan are reviewed.
4. Acceptance datasets and negative controls are versioned before evaluation.
5. CDK defines the resource; deployment and public exposure are separate
   reviewed operations.
6. WAF and Cognito/approved workload identity exist before an owned or public
   managed surface.
7. A disable, teardown, or fallback drill succeeds without changing the
   authoritative REST/Lambda shopper behavior.

Moving the shopper meal-plan path to AgentCore Runtime is not approved by these
gates. It still requires measured p99 above approximately 25 seconds after
mitigations and a separate mentor decision.

## Rollback and removal

Gateway can be disabled and managed clients returned to the approved local or
direct deterministic service path. Reviewer event mappings can be disabled,
queues drained, Runtime stopped/deleted, and pending artefacts retained only for
the approved review period. Managed evaluation schedules can be stopped while
version-controlled local gates continue. Memory, if ever approved, must support
user deletion before use. Every stack must expose cost, error, and usage signals
that justify retention; a service with no measured product or learning value is
removed rather than normalised as permanent architecture.

## Current implementation status

*Rewritten 2026-08-31. The list below was written on 2026-08-23, when the
service plane did not exist. Three of this ADR's own preconditions have since
been met, which is the reason it is worth putting in front of a mentor now
rather than later. The decision brief is
[`docs/OPEN-REVIEW-adr-0002.md`](../OPEN-REVIEW-adr-0002.md).*

- Deterministic LangGraph reference workflow: **implemented, tested, and
  deployed**. REST -> published Lambda alias with SnapStart -> DynamoDB ->
  Bedrock Converse with Guardrail version 2, plus CDK stacks (ADR 0003).
- Corrected citation construction, citation-before-use checks, money-free prose
  labels, and GuardrailBlocked node propagation: **implemented**.
- Local read-only MCP: **implemented** (Pilot Task 8). Two coarse operations
  over stdio, invoking the complete application service; no raw AWS, data,
  filesystem, network, write, citation, or generation primitive is exposed.
  **This satisfies approval gate 2.**
- AgentCore Gateway/Identity/Policy hybrid: **proposed; mentor approval
  required**. Unchanged.
- AgentCore Runtime data-quality reviewer: **prototyped and torn down
  2026-09-02** under autonomous delegation. The deterministic half — the
  allowlisted snapshot boundary of Req 13.8 and the finding post-validation — is
  implemented and tested in `src/review/` (Pilot Task 14a). The model half runs
  in an isolated AgentCore Runtime microVM (`agentcore/reviewer/app.py`), behind
  the Option-A trust boundary: the Runtime returns raw findings and
  `validate_findings` runs on the CALLER's side, never inside the Runtime. The
  live prototype scored 60% reviewer-only recall / 0% false positives / 33%
  fabrication (the validator rejecting a misquote — the boundary working), on 11
  cases with a non-deterministic model, so it is promising but unproven at
  scale. Deployed via CLI/boto3, isolated least-privilege role, torn down after
  measuring. NOT retained: CDK codification (gate 5) precedes retention. Full
  record in `docs/AGENTCORE-RUNTIME-REVIEWER.md` §13.
- Bedrock Model Evaluation and AgentCore Evaluations: **proposed companions,
  not implemented**. Local scorecards, golden sets and negative controls are
  implemented and remain the release gates either way.
- Other matrix services: **planned or gated as labelled; no deployment claim**.
- Shopper-path AgentCore Runtime: **contingency not triggered or approved**.
  Measured meal-plan p95 is 11.7–12.2s against a ~25s p99 escalation trigger,
  so nothing is pressing on it.
