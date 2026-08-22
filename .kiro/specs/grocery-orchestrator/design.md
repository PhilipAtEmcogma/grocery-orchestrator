# Design — Smart Grocery & Meal Budget Assistant

**Status:** Draft for team review
**Traces to:** `requirements.md`

This document records how the requirements are met, and — equally important —
what was tried and rejected. A design that only lists what was chosen invites
a reviewer to re-propose the options that were already ruled out.

---

## 1. Architecture and delivery status

This document distinguishes four states:

- **Implemented:** present in the workspace and covered by offline tests.
- **Live verified:** observed against AWS in `ap-southeast-2`; the exact scope
  of verification is stated.
- **Planned:** approved target behavior with an unchecked Pilot Task.
- **Blocked:** cannot be completed until a named external condition clears.

The production-pilot target is:

```text
Browser
   | HTTPS
Static site (S3 + CloudFront)
   | POST /chat
API Gateway REST (strict CORS, throttling, usage plan; Cognito seam)
   |
Published Lambda alias (Python 3.13 zip + SnapStart)
   |
Deterministic LangGraph workflow
   |-- Price and recipe repositories (DynamoDB)
   |-- Model plane (Bedrock Converse + numbered Guardrail)
   |-- Grounding, dietary and arithmetic assertions
   |
Ordered contract events
```

Separately, for controlled ingestion:

```text
EventBridge -> Step Functions Inline Map -> per-source adapters
            -> provenance/normalisation validation -> DynamoDB
                                      `-> bounded data-quality review -> human queue
```

The approved agentic extension is outside the safety-critical control path:

```text
Kiro or approved MCP client -> local read-only MCP facade
                            -> coarse application tools
                            -> complete deterministic application service
```

The MCP façade does not expose raw DynamoDB, AWS SDK, filesystem, arbitrary
network, retailer scraping, production writes, or unguarded generation. The
bounded data-quality agent can report cited anomalies over a capped snapshot,
but deterministic code and a human remain the publication authority. See
`docs/adr/0001-deterministic-core-bounded-agent-extensions.md`.

**Region:** `ap-southeast-2` (Sydney) for every AWS resource. The orchestrator
remains a zip Lambda; containerising it would forfeit SnapStart. API Gateway
REST is the first transport. WebSocket delivery, Cognito ownership, remote MCP,
and persistent preferences are later phases with explicit gates.

### 1.1 Current release blockers

The reference implementation is not yet a deployable pilot. The following are
known defects or missing controls, not accepted design trade-offs:

1. Citation source partition keys are currently derived from store and category
   rather than the products table's store-and-location key.
2. Comparison reasoning currently contains a literal price despite the
   citation-only wire rule, and final assertions do not yet validate exact
   source keys, citation ordering, source equality, or every prose-like field.
3. `GuardrailBlocked` is a `ModelError` and can be swallowed by graph-node
   fallback handling instead of reaching the handler's `GUARDRAIL_BLOCKED`
   mapping.
4. Location is accepted but not enforced by the repository, and capture dates
   are surfaced but not evaluated against a freshness policy.
5. Meal-plan totals do not yet distinguish consumed ingredient cost from the
   full-pack amount payable at checkout strongly enough for every multipack and
   reuse case.
6. Production dependency selection can fall back to fixture/scripted adapters
   when environment settings are absent.
7. CDK resource adoption, least-privilege IAM, API controls, the published
   SnapStart alias, dashboards, budgets, and deployment verification are not
   implemented.
8. Active model routing and enabled-model scorecards do not yet satisfy the
   approved 90% production threshold for every task.

Pilot Tasks 2–12 close these blockers before public traffic.

---

## 2. The grounding mechanism (Req 3)

Three independent barriers over structured output, in order of strength. Each
would be sufficient on its own; together they mean a hallucinated price
requires all three to fail. A fourth (§2.4) covers free text, where the first
three do not apply.

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

### 2.4 Free text (Req 3.7)

The three barriers above protect structured output. Free text needs a different
mechanism, because §2.2 does not transfer: a prose field is a string, and a
model can always type a number into a string. There is no schema shape that
makes a price unrepresentable in a sentence.

So the model writes `[[c1]]` placeholders instead of figures. The target
renderer resolves a known placeholder only to a non-monetary product/store
label; the source price remains in the citation event and in structured fields
that carry `citation_ref`. Token text and comparison reasoning have no
field-level citation reference, so they remain money-free. Three checks run
before text is delivered:

1. Any money-shaped string in either model output or rendered user-visible text
   rejects the text. Both `$2.97` and "71 cents" count; quantities like "500g
   pack" and "3 days" do not, since over-rejection would leave nothing useful.
2. A placeholder referring to something that was never retrieved rejects the
   text — the same rule as §2.3, applied to prose.
3. An unknown placeholder raises rather than remaining visible or disappearing
   silently. A dropped placeholder produces a sentence missing its subject; a
   visible one shows the user a defect.

**Current gap:** the implemented renderer expands placeholders into monetary
figures. Pilot Task 2 changes rendering and validates every prose-like field;
until then, generated token text is not evidence of the target wire invariant.

**Failure degrades rather than propagating.** All three checks discard the
prose and let the turn deliver its structured payload. A comparison table with
no sentence above it is a correct, if plainer, answer. Failing the whole turn
because an explanation would not render is the wrong trade — and it is a trade
worth naming, because the instinct with a safety check is to fail closed on the
entire response.

This is the weakest of the four barriers: it is a rejection check on generated
output rather than a structural impossibility. That is why it is tested
directly, including with a model deliberately scripted to write a literal
price.

---

## 3. State machine

| Node | Model? | Responsibility |
|---|---|---|
| Node | Model? | Responsibility |
|---|---|---|
| `validate_input` | no | Emit session event, initialise state |
| `classify_intent` | yes, low-cost | Classify, extract constraints, record any unmappable dietary terms |
| `emit_dietary_unsupported` | no | Honest refusal for a dietary term we cannot honour (Req 5.6) |
| `retrieve_prices` | no | Query price store; **only** creator of references |
| `emit_no_data` | no | Honest "no data" outcome (Req 4.1) |
| `generate_comparison` | **no** | Assemble comparison from references |
| `generate_plan` | yes | Produce price-free draft |
| `validate_plan` | no | Verify arithmetic and budget (Req 2.3) |
| `repair_plan` | no | Increment attempt counter |
| `emit_budget_infeasible` | no | Honest refusal (Req 4.4) |
| `generate_prose` | yes, low-cost | Explanatory text in placeholders (Req 3.7) |
| `finalise` | no | Assemble response, emit terminal event |

**`generate_comparison` makes no model call.** The name is misleading and this
document previously described it as a generation node. It is a pure function of
the retrieved citation index: it groups references per item, marks the cheapest,
and computes the saving. That is a grounding property worth stating rather than
discovering — a comparison cannot contain an invented price because nothing in
the path that produces it can invent one. If a model is ever introduced here,
the property has to be preserved by construction, not by instruction.

**`generate_prose` sits between the content nodes and `finalise`** on both
paths — after `generate_comparison`, and after `validate_plan` succeeds. It is
the only node that can fail without failing the turn: if generation, validation
or placeholder expansion fails, it returns no prose and the turn delivers its
structured payload alone. See §2.4.

**`retrieve_prices` reports what it did not do.** Items that fail to resolve
produce a `no_data` event; items past the per-turn cap produce a `notice`. The
two are deliberately different events — the first says there is no price, the
second says we did not look — and conflating them would put a false claim in
the user's hands. Both exist because a partial answer that does not announce
itself is indistinguishable from a complete one.

**The cycle** `generate_plan -> validate_plan -> repair_plan -> generate_plan`
is the reason a graph library is used rather than sequential code. Bounded at a
configured maximum (Req 2.4).

**Failed drafts are discarded** on the infeasible path (Req 4.5). Delivering an
over-budget plan beside a message saying no plan was possible is incoherent.

**Unsupported dietary exclusions refuse before retrieval** (Req 5.6). The
mapping from user terms to fixture categories is data — `SUPPORTED_EXCLUSIONS`
in `src/graph/dietary.py` — and `classify_intent` records any terms it could
not map. Meal-plan routing sees the list is non-empty and goes to
`emit_dietary_unsupported`, which returns `ErrorCode.UNSUPPORTED_EXCLUSION`
with a message naming the terms we can honour. The graph does not do the
work for a plan we cannot verify: filtering an incomplete map would ship a
plan whose safety was probabilistic rather than checkable, which is the exact
shape of the bug that used to serve dairy to a vegan user. Price checks are
not gated the same way — a dietary term does not apply to a single-product
query and blocking one would refuse a legitimate question for no safety
benefit.

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

Three tables: products, meals, and turn idempotency (§6.1). Single-table design
was considered and rejected: it pays off when related entities are fetched
together, and these are queried at different moments by different access
patterns. The idempotency table is separate for a further reason — it is
operational state with a short expiry and a write pattern (one conditional
claim per turn) that has nothing in common with either of the others.

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

### 6.1 Idempotent turns (Req 12.3)

Every turn carries a client-generated identifier, and resending it must return
the same answer without redoing the work. The plan path runs close to the
gateway's synchronous ceiling, so a client timeout followed by a retry is an
expected event rather than an edge case — and without deduplication it means
paying for generation twice and possibly returning a different plan than the
first attempt would have.

Four decisions the requirement does not imply:

**Keyed by session and turn, not turn alone.** Clients generate turn
identifiers and nothing makes them globally unique. A collision across sessions
would serve one user another user's shopping list — a privacy failure produced
by an optimisation.

**The validated payload is canonically fingerprinted.** The same identifier
arriving with different validated content is a client bug. Whitespace, object-key
order, and omitted-versus-explicit-null optional fields do not create different
fingerprints. Returning the cached response for genuinely different content
would answer a question nobody asked, so it is rejected as a non-retryable
client error instead.

**In-flight requests are detected.** A retry usually arrives while the first
attempt is *still running* — that is what a timeout means. A store that only
records completed work would let both run and lose the point entirely. The
in-progress marker is honoured for longer than the gateway ceiling, so a
slow-but-alive request is not duplicated, but short enough that a crashed
invocation does not block retries until the expiry.

**Claim ownership is fenced.** Every successful acquire or stale takeover
returns a fresh opaque owner token/version. Completion and release are
conditional on that token and `in_progress` status, so an old invocation that
resumes after takeover cannot overwrite or delete the new owner's claim.

**Only terminal outcomes are cached.** Caching a transient failure would make
the client's retry permanently useless: it would receive the same failure
forever. A retryable error releases only the caller's owned claim instead.

**The store failing does not fail the turn.** If the store is unreachable the
handler runs the work anyway. A duplicated response is a worse outcome than a
single one and a much better outcome than none.

The operation this rests on is an atomic claim, not a read followed by a write
— see `DYNAMODB-SCHEMA.md`, Table 3. Two invocations racing on the same key
would both read "absent" and both proceed, which is exactly the case the
mechanism exists to prevent and exactly the case that testing on one machine
will not surface.

**Current versus target.** The in-memory and DynamoDB stores exist, and the
five current DynamoDB outcomes have live evidence. Production readiness still
requires canonical validated request hashing, a shared protocol contract suite,
and stale-owner fencing in both implementations. Production startup must reject
the in-memory store rather than silently selecting it.

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

**The stored implementations must satisfy the same tests as the fixture ones.**

This was an assertion in this document for a long time while nothing enforced
it — every test constructed the fixture repository directly. It is now backed
by one suite written against the *protocol* and parameterised over its
implementations, so adding the stored repository means running the existing
tests rather than writing new ones (Task 2.10).

It was written **before** the stored implementation, deliberately. Written
first, it is the specification that implementation is built to satisfy. Written
afterwards, it would be a description of whatever that implementation happened
to do — a different and much less useful artefact.

Two constraints keep it honest. It touches **protocol members only**: anything
convenient that is not on the Protocol is precisely what will not exist on the
stored side, so test data is *discovered* through `candidates_for_budget`
rather than read out of the fixture file. And it asserts **properties, not
transcripts** — ordering, limits, types, exclusions — because specific prices
differ between a seed fixture and live scraped data.

The DynamoDB parameter skips unless a table is configured, so CI stays
credential-free. A skip is reported as *unverified*, never as a pass.

**The suite is checked against deliberately broken implementations** — a
substring matcher, a dearest-first store, float money, a leaking exclusion
filter — and must catch all of them. A conformance suite that cannot fail
certifies nothing, which §10.3 records the cost of learning.

The same argument applies to the idempotency store (§6.1), whose fixture and
stored implementations sit behind one protocol for the same reason. It has no
shared suite yet.

**Writing it surfaced an ambiguity the protocol had left open**, which is the
sort of thing a second implementation would otherwise have resolved by
coin-flip. `cheapest_for_product(stores=[])` was accepted via `if stores:`, so
an explicit empty filter meant "no filter" and returned every store. None and
`[]` are now specified as distinct — None is "any store", `[]` is "no store
qualifies" — because silently *widening* a constraint is the dangerous
direction: an empty intersection of "preferred" and "nearby" would have
returned exactly the stores the user ruled out. No caller passed `[]`, so
tightening it changed no behaviour; it removed a trap.

**Implemented adapters fail loudly on misconfiguration.** The fixture and
DynamoDB price repositories and the in-memory and DynamoDB idempotency stores
are implemented; the shared price-repository suite has been live-verified
against DynamoDB. Production still needs a shared idempotency contract suite,
claim-owner conditions, and startup validation that rejects demo adapters.
Returning an empty result for a misconfigured repository remains forbidden
because it is indistinguishable from a genuine no-data outcome.

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
1. Regeneration on the low-cost model — **implemented**
2. Prompt caching on the repeated product context — **implemented in request
   construction** (§4); utilisation unverified against a live endpoint
3. Pre-filtering candidates to affordable items before composition —
   **implemented** in the retrieval layer's candidate query
4. Splitting structured generation from prose generation — **implemented**;
   prose is a separate node on the low-cost tier (§3), and its failure degrades
   rather than costing a retry (§2.4)
5. Streaming transport (Req 7.9) — not built

Escalation beyond these requires measured evidence, not anticipation. Note that
four of the five are now in place and none of them has been measured, because
measurement needs a live endpoint (Task 10.5). The ordering above was a
prediction; it should be replaced by percentiles rather than defended.

**Multi-item requests are capped at five comparisons per turn** for the same
reason. A request naming twenty items would multiply retrieval and comparison
work on the path already closest to the ceiling.

Two caps are involved and they must not be collapsed into one. Extraction is
bounded higher — it only stops a pathological reply from being unbounded —
while the comparison cap is the latency control. If extraction capped at the
comparison limit, the items past it would never reach the orchestrator, which
could then neither answer them nor say it had not. Truncation is a control-flow
decision and belongs in code, where the discarded items are still in hand, not
in a prompt where they are already gone.

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
| 11.5 No personal data in logs | Identifiers and counts only; never message text or location. Enforced in one place and tested against a real turn — §12.4 |
| 11.6 Expiry | Time-to-live on stored plans and sessions |
| 11.7 Data protection | Point-in-time recovery and encryption enabled explicitly |
| 12.3 Exactly-once turns | Atomic claim on a session-scoped key (§6.1) |
| 5.5 Content safety (policy) | Guardrail policy as version-controlled data, validated in CI |
| 5.5 Content safety (enforcement) | Attached to every generation call; call refuses to run without one |
| 5.5 Content safety (verification) | **Partially verified** — numbered Guardrail creation and basic attached invocation are live; intervention propagation and the 20-case must-block/must-allow harness remain unmet |
| 6.5 Untrusted input | User text delimited; system instruction declares it data |
| 6.5 Untrusted input (filter) | Per-request input tagging, so the prompt-attack filter evaluates it |

**Content safety is separate from grounding.** The guardrail's grounding check
is a probabilistic score and its documentation excludes conversational use
cases; the grounding requirement is met by sections 2.1–2.4 instead. The
guardrail's role is unsafe food advice — a distinct risk with a documented
precedent in this market.

### 10.1 The guardrail is configuration, not console state

The policy lives in a file for the same reasons the model catalogue does: it is
reviewable in a pull request, diffable over time, and reproducible in another
account. None of that is true of settings clicked into a console, and a
security control nobody can review is one nobody can check.

A validator runs in CI and fails the build on the configurations that produce a
guardrail which *silently does nothing* — the prompt-attack filter below
maximum strength, a denied topic with too few examples to classify reliably,
refusal messaging too short to leave the user anywhere to go. These are not
hypothetical mistakes; they are the ones that pass review because the policy
looks enabled.

**Publishing matters.** The draft version changes underneath you. Anything that
needs reproducing pins a numbered version.

### 10.2 Input tagging is what makes the prompt-attack filter work

This is the step most easily missed, and the failure is silent: the filter can
be enabled, report healthy, and never evaluate anything. Without tagging, the
prompt-attack filter has no way to tell our instructions from the user's — "you
are a grocery assistant" and "you are now a chemistry expert" are the same
shape. Tagging marks which region is untrusted, so the filter applies there and
does not flag our own system prompt as an attack on itself.

**Tags carry a fresh random suffix per request.** A fixed tag is guessable, and
a guessed tag can be closed early to smuggle text into the trusted region. The
implementation also strips any occurrence of its own tag from the input before
wrapping, so guessing the format is not enough either.

**Retrieved content is untrusted too.** The product table is built from scraped
retailer data we do not control, and a product name is somewhere an instruction
could be placed. Indirect injection through retrieved content is the vector
that gets missed when "untrusted input" is read as "the user's message".

**Enforcement fails closed.** A generation call with no content safety filter
configured refuses to run. Opting out is possible for local work, but only as a
deliberate, visible configuration choice — never as the accidental consequence
of an unset identifier. This is the opposite of the platform default, which is
what makes it worth stating.

### 10.3 A gate nobody reads

Secret scanning was wired into CI and marked complete. It then failed on four
consecutive commits to the default branch, and nothing happened. The baseline
it compared against was excluded from version control, so the scan exited with
`Invalid path` every time.

The instructive part is not the missing file — that is a two-character fix in
`.gitignore`. It is that a red build on the default branch persisted across
four commits without being treated as a defect. A gate that fails and is
ignored provides precisely the assurance of no gate at all, while looking on
paper like a control.

There was a second defect underneath, and it would have surfaced only after the
first was fixed: `detect-secrets scan --baseline` rewrites the baseline in
place and exits zero when it finds something new. It is a maintenance command,
not a gate; the hook subcommand is the one that exits non-zero. Committing the
baseline without also changing the command would have converted a loudly
failing job into a silently passing one — a strictly worse position, and one
nobody would have gone looking for, because the build would finally have been
green.

Three things to carry into the rebuild. A gate is done when it has been *seen
to fail* on a planted defect and pass without one, not when it is wired.
Anything a gate reads must be in version control, or it is reading something it
just wrote. And a check whose failure does not block or notify anyone is
documentation, not enforcement — which is the same argument §8 makes for
structural guarantees over behavioural ones, applied to the build.

### 10.4 What remains to be verified

The Guardrail `b1xezpqe04kx`, version `1`, has been created and observed on a
basic attached Bedrock invocation in `ap-southeast-2`. That verifies the live
resource, numbered attachment, and basic request shape; it does **not** verify
policy quality or graph-level intervention behavior.

A twenty-case red-team set defines the missing evidence — thirteen cases that
must be blocked across prompt injection, unsafe preparation, disordered eating,
medical advice, age-restricted goods and payment data, and seven ordinary
grocery questions that must be *allowed*. Pilot Task 3 builds the harness,
runs every case through an accessible model path, and proves that an
intervention crosses every graph node unchanged to the single
`GUARDRAIL_BLOCKED` service outcome.

The must-allow half is not padding. Over-blocking is the usual failure mode of
an aggressive policy, and a filter that refuses legitimate grocery questions
has produced a broken product rather than a safe one. A verification set
containing only attacks cannot detect that.

The first diagnostic on a failed prompt-attack case is the live evaluation
metadata for the tagged untrusted region. Basic invocation evidence does not
prove that each attack class is evaluated or that the policy blocks and allows
the intended cases.

---

## 11. Path to infrastructure as code (Req 12.4)

The products and idempotency tables already exist outside CDK. Pilot Task 9
adopts/imports them into a stateful TypeScript stack before any service-plane
deployment; replacement is forbidden. Regenerated live configuration is local
review evidence because it contains account-bearing ARNs, while sanitized CDK
assertions and review outcomes are committed. New resources are CDK-first.
Stateful adoption and service deployment are separate reviewed operations. See
§16 and `DYNAMODB-SCHEMA.md` for the target stack split and import sequence.

---

## 12. Observability (Req 12.1, 12.2)

AWS Lambda Powertools, attached at the handler and nowhere else.

### 12.1 Why the boundary matters more than the library

The graph, the model plane, the retrieval layer and both eval harnesses run
with no AWS account. That is not a convenience — it is why CI needs no
credentials and why every failure path is testable. An observability library
imported by a node would end that quietly, so Powertools stays behind a
Protocol (`src/observability/base.py`) whose default implementation discards
everything. Exactly one module imports `aws_lambda_powertools`, and a test
walks the import graph of `src/` and `evals/` to keep it that way.

Tracing still has to reach inside the graph, because that is where the time
goes. It does so without the graph knowing: `PriceRepository` and
`ModelClient` are already Protocols with swappable implementations, so
decorators implementing the same interfaces slot in at the handler and are
invisible to every node. The same seam that lets fixtures stand in for
DynamoDB carries the instrumentation.

| Signal | Mechanism |
|---|---|
| 12.1 Structured logs | JSON to stdout, correlation id from `session_id`, turn id and Lambda context injected, cold-start flagged |
| 12.2 Latency | Subsegment per retrieval call and per model call; `TurnLatency`, `RetrievalLatency` and `ModelLatency` metrics |
| 12.2 Tokens | `InputTokens`, `OutputTokens`, `CacheReadTokens`, summed per turn from the model client's usage |
| 12.2 Model used | `ModelLatency` dimensioned by model and task; the registry key, not the raw id |
| 12.2 Regeneration attempts | `RepairAttempts`, plus `RepairExhausted` when the loop gives up |
| Silent turns | `TurnWithoutContent`, dimensioned by intent |
| Exactly-once (12.3) | `IdempotentReplay`, `TurnIdReused`, `IdempotencyUnavailable` |

### 12.2 The repair loop is measured per attempt, not as a block

The loop spans four nodes, so a single span around it would have to be opened
by the graph. What is emitted instead is one subsegment per attempt —
`model.generate_plan` at attempt 0, then `model.repair_plan` at 1 and 2 — and
the total on the metric. For the 29-second question this is the more useful
shape: the decision turns on what a second and third generation cost, which a
combined figure hides.

Repair attempts are counted from the model calls rather than read off the
finished plan, because on the infeasible path the failing plan is discarded
and there is nothing left to read.

### 12.3 A turn that answers nobody is a metric, not a silence

`out_of_scope` and `general_chat` return session, intent and done, and no
content event. So does a generation path that has started dropping its
output. `TurnWithoutContent` is dimensioned by intent so the two are
distinguishable: a baseline on the conversational intents, an alarm on any
other. Without it the first report of a model change breaking generation is a
user complaint.

### 12.4 Req 11.5 constrains all of this

Logs, and traces on the same rule. Three functions in
`src/observability/base.py` produce every field derived from a request, a
response or an exception, so the property is reviewable in one place and
tested against a real turn rather than asserted.

Three specific traps, each of which was live:

- `log_event` is passed `False` explicitly. Left to its default, the
  `POWERTOOLS_LOGGER_LOG_EVENT` environment variable dumps the whole API
  Gateway event — message included — and a configuration change becomes a
  privacy incident.
- `capture_response` is `False` on the tracer. A meal-plan response carries
  the applied dietary exclusions.
- `logger.exception()` is never called. A traceback ends with `str(exc)`, and
  a pydantic `ValidationError` embeds the input that failed — which, for a
  malformed request, is the user's message. Exceptions are rendered as a
  type and a list of `file:line` frames; the message survives only for
  exception types whose text is known to be internal.

Hint *keys* are withheld along with hint values, and the count reported
instead: a key list would report that this user has dietary restrictions,
which may imply health information (Req 11.6).

### 12.5 Powertools' idempotency utility was not adopted

Req 12.3 is already implemented (§6.1) with four decisions the utility does
not share: session-scoped keys, payload fingerprinting that rejects rather
than replays, in-flight detection, and caching only terminal outcomes. Each is
tested. Swapping in a library default would be a behaviour change wearing the
costume of a dependency upgrade.

### 12.6 The two alarms, as configuration

The alarm definitions are version-controlled in `config/alarms.json` and
validated by `scripts/apply_alarms.py --dry-run`. They are not deployed because
the service plane does not yet exist; that is a deployment gap, not absence of
an AWS account. Offline validation runs in CI and the pre-commit hook so the
definitions cannot rot while they wait.

These are the two worth having on day one, and they are cheap because the
signals already exist:

- **`handler_escaped` in the logs.** A metric filter on this log line, alarm on
  `>= 1`. `src/handler.py` maps every anticipated failure to an error event, so
  this line is emitted only when an exception got past all of them. It carries
  the exception type and `file:line` frames, which is enough to open the right
  file before opening the dashboard. It has fired for three distinct bugs so
  far, all the same shape — code above the `try`, or inside an `except`, where
  a raise cannot reach the clause written for it.
- **Any 5xx from the API.** Alarm on the gateway's own `5XXError` metric, no
  instrumentation needed. This is why `_last_resort` answers 500 rather than
  the 200 a *handled* internal error returns: at 200 an unanticipated crash is
  indistinguishable at the HTTP layer from one we predicted, so the only way to
  learn the net fired is to already be reading logs — and the reason the net
  exists is that reading was not enough. The body is a contract-valid
  `ChatResponse` either way, so the status is free to carry the signal
  (CONTRACT-v1.md documents it as additive for clients).

The two overlap deliberately. The log filter says what broke and where; the 5xx
alarm fires even if logging is the thing that broke. `test_alarms.py` asserts
that separation holds — the 5xx alarm must stay on an `AWS/` namespace, because
an alarm we publish ourselves cannot survive our own logging failing.

**What the validator is for.** Not schema-checking. An alarm fails quietly in
more ways than it fails loudly, and each check in `apply_alarms.py` is one of
them: an alarm on a metric no filter publishes (a metric-name typo is not an
error anywhere in AWS — it is an alarm that sits in INSUFFICIENT_DATA looking
calm); `GreaterThanThreshold` with threshold 1, which needs a *second* crash to
fire; `Average` instead of `Sum`, which dilutes a single event across the
period; `treatMissingData: breaching`, which pages on an idle system until
someone mutes it; a substring filter pattern rather than a JSON selector, which
matches any log line quoting the text; and no notification topic, which makes
the alarm a dashboard widget. `apply_alarms.py` also refuses to finish quietly
when the topic it just created has no confirmed subscriber — the same failure
one level further out.

**The check AWS cannot do.** The alarm is a string in a JSON file pointing at a
string in a Python file. Rename the event in `src/handler.py` and the filter
still deploys, still looks right in the console, and matches nothing forever —
indistinguishable from a service that never crashes. So `test_alarms.py` drives
a real turn into the last-resort path, captures what Powertools actually wrote,
and applies the shipped filter pattern to it. It also asserts the pattern does
*not* match an ordinary turn, because an alarm broad enough to fire on success
is one that gets muted in a week.

Everything else can wait for a dashboard. `TurnWithoutContent` (§12.3) is the
next candidate, but it needs a baseline on the conversational intents first,
and there is no traffic to take one from.

Not deployed. The AWS account and base resources exist; after the service
plane is deployed, someone must subscribe to the topic and confirm it. The
script says so, loudly, and exits non-zero until that is true.

---

## 13. Location, store scope, provenance and freshness (planned)

The request schema already accepts location and every citation already carries
store location and capture date, but the repository contract does not yet make
location or freshness a selection constraint. Until Pilot Task 5 lands, the
assistant must not claim that results are nearby or current merely because
those fields are present.

The target repository request carries an explicit store scope, optional
location/radius, and an `as_of`/freshness policy. Price checks may query GSI1 by
product and filter the bounded result set by eligible store locations. Meal
candidate retrieval must not retain the current full-table scan at production
scale; Pilot Task 6 selects either a category/location/freshness index or a
materialized candidate view. Stale-only results route to a contract-valid
honest outcome rather than being labelled current.

Every citation identifies the exact base-table record using
`store_key = <chain>#<location-slug>` and `product_key`. The current
store/category source key is a release-blocking implementation defect, not an
alternative schema.

## 14. Payable meal-plan arithmetic (planned)

The user budget applies to the amount payable at checkout, not a fractional
consumption estimate. The target design maintains two concepts:

- **Consumption subtotal:** ingredient quantity consumed by meals, useful for
  allocation and waste analysis.
- **Payable total:** full pack price multiplied by the whole packs required
  after aggregating repeated use of each cited product.

The shopping list contains each cited product once per store, with its required
pack count and payable line total. The plan budget check uses the payable total.
Both figures are derived from citations in Python; neither is accepted from a
model. The current arithmetic does not prove every reuse/multipack case and is
not pilot-ready until Pilot Task 4 closes that gap.

## 15. Guardrail intervention semantics (planned correction)

A Guardrail intervention is a safety outcome, not an ordinary model failure.
`GuardrailBlocked` must cross every graph-node boundary unchanged and be mapped
once to `GUARDRAIL_BLOCKED` at the service boundary. Intent heuristics, repair
cycles, and optional-prose degradation remain valid for ordinary model errors
only. The current shared `ModelError` catches can swallow the specialized
exception; Pilot Task 3 corrects this and adds node-by-node negative tests plus
the live must-block/must-allow harness.

The live Guardrail resource exists (`b1xezpqe04kx`, version `1`) in the Sydney
workshop account. That proves creation and basic invocation, not full policy
quality: the twenty-case red-team harness remains planned.

## 16. Production configuration and CDK adoption (planned)

Local development may select fixtures and the scripted model explicitly.
Production mode must instead validate at startup that DynamoDB, Bedrock, a
numbered Guardrail version, strict CORS, and the stored idempotency adapter are
configured. Missing production dependencies must fail closed; absence of an
environment flag must never select a demo implementation.

The TypeScript CDK application is split by deployment lifecycle:

1. **Stateful construct/stack:** adopted products and idempotency tables, later
   meals/recipes, encryption, PITR, TTL, retention and deletion protection.
2. **Service construct/stack:** Python 3.13 zip Lambda, published version,
   SnapStart alias, REST API, throttling, usage plan, strict CORS, log
   retention, alarms, budgets, SSM model catalogue and least-privilege IAM.

Existing tables are adopted/imported before service deployment and must not be
recreated. Synthesis is deterministic, performs no account lookup or mutation,
and is covered by CDK assertions. Resource adoption and deployment are
separate reviewed operations.

## 17. MCP and bounded agentic extensions (planned)

Pilot Task 8 delivers a local, read-only MCP façade for Kiro or another approved
client. Tools are coarse application capabilities—grounded comparison,
grounded plan request, and provenance inspection—and call the complete
application service. The MCP layer cannot manufacture citations or invoke an
internal generation node directly.

Pilot Task 14 adds a bounded data-quality reviewer over a capped ingestion
snapshot. It has read-only tools, call/time limits, Guardrails, schema-checked
outputs, deterministic record-reference validation, and no publication role.
Findings enter a human review queue. See ADR 0001 for the accepted boundaries
and rejected alternatives.

## 18. Production-pilot acceptance gates

A release candidate requires all of the following evidence:

- 100% pass for grounding, literal-money, arithmetic, dietary fail-closed and
  Guardrail-propagation controls, including negative tests.
- A scorecard for every enabled model and at least 90% on each active route's
  applicable golden set.
- Measured targets of p95 under 5 seconds for price checks, p95 under 20 seconds
  for meal plans, and p99 under the approximately 25-second escalation trigger.
- At least 99% successful service responses during the pilot, excluding
  intentional contract-valid refusals, with unhandled 5xx below 1%.
- No message, raw location, dietary value, credential, or model prompt in logs
  or traces.
- Exact source key, store location, and capture date for every published price.
- Cost per successful task, budget alerts at 50/80/100%, and review of unit-cost
  regressions above 20%.

These are targets subject to deployment measurement, not claims about the
current reference implementation.
