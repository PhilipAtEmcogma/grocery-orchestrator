# Design — Smart Grocery & Meal Budget Assistant

**Status:** Draft for team review
**Traces to:** `requirements.md`

This document records how the requirements are met, and — equally important —
what was tried and rejected. A design that only lists what was chosen invites
a reviewer to re-propose the options that were already ruled out.

---

## 1. Architecture

```
Browser
   |  HTTPS
Static site (S3 + CloudFront)
   |  POST /chat
API Gateway (REST)
   |
Orchestrator Lambda
   |-- LangGraph state machine
   |-- Price store (DynamoDB)
   |-- Model plane (Bedrock, per-task routing)
   |
Response (ordered typed events)
```

Separately, on a schedule:

```
EventBridge -> Step Functions -> scraper Lambdas -> DynamoDB
```

**Region:** `ap-southeast-2` (Sydney). Auckland lacks the required Bedrock and
Lambda features.

---

## 2. The grounding mechanism (Req 3)

Three independent barriers, in order of strength. Each would be sufficient on
its own; together they mean a hallucinated price requires all three to fail.

### 2.1 Topology (Req 3.3)

The orchestrator is a directed graph. Generation nodes are reachable only
through the retrieval node — there is no edge that skips it. Retrieval-before-
generation is therefore a property of the graph's shape, not a convention a
developer must remember.

### 2.2 Schema (Req 3.4)

The model returns a *draft* containing, per ingredient: a reference
identifier, a pack multiplier, and display text. There is no price field. Every
monetary value is computed in application code from stored prices.

A hallucinated price is not unlikely. It is unrepresentable — the model has
nowhere to put one.

### 2.3 Assertion (Req 3.5, 3.6)

Before delivery, every referenced identifier is checked against the identifiers
actually retrieved. An unresolved reference fails the response rather than
silently dropping the line. A negative test in CI proves the check fires.

---

## 3. State machine

| Node | Model? | Responsibility |
|---|---|---|
| `validate_input` | no | Emit session event, initialise state |
| `classify_intent` | yes, low-cost | Classify and extract constraints |
| `retrieve_prices` | no | Query price store; **only** creator of references |
| `emit_no_data` | no | Honest "no data" outcome (Req 4.1) |
| `generate_comparison` | yes | Assemble comparison from references |
| `generate_plan` | yes | Produce price-free draft |
| `validate_plan` | no | Verify arithmetic and budget (Req 2.3) |
| `repair_plan` | no | Increment attempt counter |
| `emit_budget_infeasible` | no | Honest refusal (Req 4.4) |
| `finalise` | no | Assemble response, emit terminal event |

**The cycle** `generate_plan -> validate_plan -> repair_plan -> generate_plan`
is the reason a graph library is used rather than sequential code. Bounded at a
configured maximum (Req 2.4).

**Failed drafts are discarded** on the infeasible path (Req 4.5). Delivering an
over-budget plan beside a message saying no plan was possible is incoherent.

---

## 4. Model plane (Req 9)

Nodes request a **task**; a registry resolves it to a model. Model identifiers
never appear in node code.

The catalogue is configuration, not code, for three reasons: identifiers and
prices change faster than releases; under infrastructure-as-code it becomes a
parameter store entry, so routing can be retuned without deploying; and adding
a model becomes a change a non-engineer can review.

**Capabilities are explicit** (Req 9.2). Each entry records tool-use support,
caching support, and output limits. Request construction branches on these:

- Tool use available: the schema is sent as a forced tool call, so the model
  cannot prepend prose and break parsing.
- Tool use unavailable: the schema is embedded in the prompt and the reply is
  parsed, tolerating code fences and preamble. This path is weaker, which is
  why the evaluation suite measures the difference rather than assuming it is
  acceptable.
- Caching available and prefix large enough: a cache marker is inserted after
  the static portion. Below the model's minimum the request succeeds and caches
  nothing, so the marker is omitted rather than added hopefully.

**Routing failure raises** (Req 9.3). Substituting a weaker model silently
would change output quality with no signal.

---

## 5. Price store (Req 8)

Two tables. Single-table design was considered and rejected: it pays off when
related entities are fetched together, and prices and meal data are queried at
different moments by different access patterns.

### Products

| | Partition key | Sort key |
|---|---|---|
| Base | store + location | product identifier |
| Index 1 | product identifier | zero-padded price + store + location |

The index is the design work. The primary question — cheapest version of one
product across all stores — partitions by product, and the zero-padded price in
the sort key means results arrive cheapest-first from a single query (Req 8.2).

Cost: a price change requires rewriting the index entry, since the price is
part of the key. With a daily full refresh this is immaterial.

### Meals

Two entity types separated by key prefix: recipes, and saved plans. Saved plans
carry an expiry attribute (Req 11.6).

**Money is stored as a string** (Req 8.6). The numeric type round-trips through
floating point in most access paths, which is how a shopping list acquires a
total of `$23.159999999998`.

**Name normalisation** (Req 8.3) is owned by ingestion, not by read-time
matching. Free-text lookup uses exact matching after noise removal, with no
fuzzy fallback — see section 8.

---

## 6. Interface contract (Req 7)

Responses are an ordered sequence of typed events rather than a single object.

This is what makes the streaming upgrade (Req 7.9) nearly free for the client:
over a request/response transport the events arrive together; over a streaming
transport the same events arrive one at a time. A client written as an event
handler needs no change.

**Prices appear only in reference events.** Content events cite them by
identifier. This is the wire-level expression of section 2.2 — and it means the
grounding invariant is checkable from the response alone, without access to
internals.

---

## 7. Testability and boundaries

Two protocol boundaries: one over the price store, one over the model plane.
Nodes depend on the protocols, never on cloud SDKs.

Consequences:
- The full orchestrator is buildable and testable with no cloud account.
- Continuous integration needs no credentials.
- Failure modes that are difficult to trigger against a live model — a
  first plan deliberately over budget, a model returning an unresolvable
  reference — are scriptable.

The stored implementations must satisfy the same tests as the fixture ones.

---

## 8. Decisions made against

Recording these prevents them being re-proposed.

**Managed agent frameworks.** Rejected. An agent that decides for itself
whether to consult the price store can offer only a behavioural guarantee. The
requirement (Req 3.3) is structural. Also: the previous-generation service
entered maintenance mode and is closed to new accounts.

**Autonomous tool-calling loop.** Rejected for the same reason. The model makes
bounded judgements at fixed points; control flow is code. Knowing when not to
delegate control is the harder call.

**Orchestrator as a container image.** Rejected. Measured dependency size is
well within the archive limit once transitive packages that are never imported
are excluded. Containerising would forfeit the snapshot-based cold-start
optimisation, which is archive-only.

**Fuzzy product matching.** Rejected (Req 4.3). Substring matching resolves
"truffle oil" to canola oil — a confident, cited, wrong price. Exact matching
after noise removal returns nothing instead. The model is responsible for
reducing free text to a clean product term; the store matches strictly. This
trade is deliberate and must not be reversed to raise an evaluation score.

**Trusting model-reported constraint compliance.** Rejected (Req 5.4).
Exclusions are verified against retrieved products. Asking the model whether it
followed the rules tests the wrong thing.

**Bypassing the API gateway for streaming.** Rejected. It would obtain
streaming cheaply at the cost of rate limiting, usage plans, and
authentication — three security requirements traded for transport convenience.

---

## 9. Known constraints

**Synchronous integration timeout.** The gateway caps synchronous responses at
29 seconds. The plan path — classification, retrieval, composition, validation,
possible regeneration — is the only path that approaches it.

Mitigations, in order of preference:
1. Regeneration on the low-cost model (implemented)
2. Prompt caching on the repeated product context
3. Pre-filtering candidates to affordable items before composition
4. Splitting structured generation from prose generation
5. Streaming transport (Req 7.9)

Escalation beyond these requires measured evidence, not anticipation.

**Stateless model invocations.** Each call carries no memory of prior calls.
Every constraint must be restated on regeneration (Req 5.3). This was found by
evaluation, not by unit testing: the regeneration prompt originally restated
only the budget, leaving a plan for an allergic user regenerated with no
knowledge of the allergy.

---

## 10. Security posture

Cloud security controls are opt-in. The design assumes nothing is enabled by
default.

| Requirement | Mechanism |
|---|---|
| 11.1 Least privilege | Per-function roles scoped to named resource identifiers |
| 11.2 No embedded credentials | Managed secret storage; identity-based access |
| 11.3 Input validation | Schema validation at the boundary; length limits |
| 11.4 Rate limiting | Gateway throttling and usage plans |
| 11.5 No personal data in logs | Identifiers and counts only; never message text or location |
| 11.6 Expiry | Time-to-live on stored plans and sessions |
| 11.7 Data protection | Point-in-time recovery and encryption enabled explicitly |
| 5.5 Content safety | Guardrail on every generation call |
| 6.5 Untrusted input | User text delimited; system instruction declares it data |

**Content safety is separate from grounding.** The guardrail's grounding check
is a probabilistic score and its documentation excludes conversational use
cases; the grounding requirement is met by sections 2.1–2.3 instead. The
guardrail's role is unsafe food advice — a distinct risk with a documented
precedent in this market.

---

## 11. Path to infrastructure as code (Req 12.4)

The build is manual first by team decision. To keep the conversion mechanical:

1. Tag every resource on creation with project, environment, and owner.
2. Name consistently with an environment suffix, so generated resources can
   coexist with manual ones during migration.
3. Export each resource's configuration immediately after creation and commit
   it. That export is the specification the definitions are written from.
4. Adopt existing resources into the stack during migration rather than
   recreating them, so no data is lost.
