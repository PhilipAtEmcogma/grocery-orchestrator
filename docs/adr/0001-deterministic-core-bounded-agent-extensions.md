# ADR 0001: Preserve the deterministic core and add bounded agentic extensions

- **Status:** Accepted; superseded in part only if proposed ADR 0002 is approved
- **Decision date:** 2026-08-23
- **Scope:** Orchestrator, MCP integration, specialist agents, and production-pilot evolution
- **Related requirements:** 3, 5, 9, 11, 12, 13, 14
- **Supersession note:** Proposed ADR 0002 would supersede only this ADR's
  AgentCore Gateway staging, isolated Runtime reviewer, and companion managed
  evaluation position. It does not rewrite this historical decision. Until
  mentor approval, this ADR remains controlling.

## Context

The reference implementation already enforces the product's highest-risk properties through code and graph topology: retrieval precedes priced output, model drafts contain no prices, arithmetic is recomputed in Python, dietary exclusions fail closed, and repair is bounded. A free-form agent that decides whether to retrieve or validate would turn these structural guarantees into behavioral expectations.

The production-pilot roadmap also needs practical exposure to MCP servers and agentic workflows. Rejecting autonomous control for the safety-critical shopper path does not require rejecting MCP or agents everywhere; it requires putting them at boundaries where they cannot bypass the invariants.

## Decision

1. The shopper request path remains a deterministic LangGraph workflow inside the Python 3.13 orchestrator Lambda.
2. Retrieval remains mandatory and is the sole creator of citation references.
3. Models make bounded judgments only at named graph nodes. They never control whether retrieval, dietary validation, arithmetic validation, or final grounding checks run.
4. The first MCP capability is a local, read-only façade for Kiro or another approved client. It exposes coarse application operations that invoke the complete deterministic service, not raw infrastructure or internal generation nodes.
5. Initial MCP tools are limited to grounded comparisons, grounded meal-plan requests, and provenance inspection. They do not expose arbitrary DynamoDB access, AWS SDK calls, filesystem or network access, retailer acquisition, production writes, or unguarded model invocation.
6. The first specialist agent is a bounded data-quality reviewer over a capped ingestion snapshot. It can produce cited findings for human review but cannot publish prices or mutate production data.
7. Remote MCP requires Streamable HTTP authorization, input/output schemas, rate limits, timeouts, result validation, sanitized audit logs, and a separate approval. It is not part of the first anonymous pilot.
8. Bedrock Agents Classic remains prohibited. AgentCore remains the documented meal-path contingency only: it requires mentor approval and measured p99 meal-plan latency above approximately 25 seconds after the existing mitigations are exhausted.

## Production boundaries

```text
Frontend -> API Gateway REST -> Lambda alias (SnapStart) -> deterministic LangGraph
                                                      |-> DynamoDB
                                                      `-> Bedrock Converse + Guardrail

Kiro/approved client -> local read-only MCP facade -> complete application service

EventBridge -> Step Functions Inline Map -> source adapters -> validation -> DynamoDB
                                                        `-> bounded reviewer -> human queue
```

The MCP façade may call the application service. It must not become a second path around the graph. The data-quality agent reads only capped snapshots and writes only a review artefact; deterministic ingestion code remains the publication authority.

## Consequences

### Positive

- Grounding, dietary safety, and arithmetic remain structurally enforceable.
- MCP and agentic workflows can be demonstrated without adding production risk to the shopper path.
- Coarse tools are easier to authorize, test, rate-limit, and explain than raw database or model tools.
- The same application capability can be exercised through REST and MCP without duplicating business rules.

### Costs

- The design is less autonomous than a general-purpose tool-calling agent.
- MCP does not reduce the need for the existing application contract or validation suite.
- A useful data-quality agent requires labelled anomaly cases and deterministic post-validation.
- Remote MCP and persistent agent memory are deferred until identity, retention, and operational controls exist.

## Rejected alternatives

- **Replace LangGraph with an autonomous agent:** rejected because retrieval-before-generation and validation would no longer be graph invariants.
- **Let the model call raw DynamoDB tools:** rejected because it exposes unrestricted data access and allows citation creation outside retrieval.
- **Put MCP between Lambda and DynamoDB by default:** rejected because it adds latency and failure modes without improving the in-process repository boundary.
- **Give a data-quality agent publication rights:** rejected because probabilistic review must not become the authority for prices shown to users.
- **Use AgentCore immediately to demonstrate agents:** rejected because the approved contingency is evidence-triggered and requires mentor sign-off.

## Current implementation status

This ADR records an accepted design, not completed functionality.

- Deterministic LangGraph workflow: **implemented**.
- Local read-only MCP façade: **implemented** (Pilot Task 8, stdio only).
- Bounded data-quality agent: **half implemented** (Pilot Task 14a). The
  sanitised snapshot boundary and the deterministic finding validation are
  built and tested in `src/review/`; they are required whoever reviews. No
  Runtime is deployed and no model reviews anything — that needs ADR 0002.
- Remote MCP/OAuth: **later roadmap**.
- AgentCore contingency: **not triggered and not approved**.
