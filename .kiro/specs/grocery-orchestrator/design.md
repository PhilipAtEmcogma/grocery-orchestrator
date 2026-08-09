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

So the model writes `[[c1]]` placeholders instead of figures, and application
code expands them from the retrieved records after generation. Three checks
run before the text is delivered:

1. Any money-shaped string in the model's output rejects the text. Both
   `$2.97` and "71 cents" count; quantities like "500g pack" and "3 days" do
   not, since over-rejection would leave nothing worth reading.
2. A placeholder referring to something that was never retrieved rejects the
   text — the same rule as §2.3, applied to prose.
3. Expansion of an unknown placeholder raises rather than leaving `[[c9]]`
   visible or dropping it silently. A dropped placeholder produces a sentence
   missing its subject; a visible one shows the user a defect.

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
| `validate_input` | no | Emit session event, initialise state |
| `classify_intent` | yes, low-cost | Classify and extract constraints |
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

**The payload is fingerprinted.** The same identifier arriving with different
content is a client bug. Returning the cached response would answer a question
nobody asked, so it is rejected as a non-retryable client error instead.

**In-flight requests are detected.** A retry usually arrives while the first
attempt is *still running* — that is what a timeout means. A store that only
records completed work would let both run and lose the point entirely. The
in-progress marker is honoured for longer than the gateway ceiling, so a
slow-but-alive request is not duplicated, but short enough that a crashed
invocation does not block retries until the expiry.

**Only terminal outcomes are cached.** Caching a transient failure would make
the client's retry permanently useless: it would receive the same failure
forever. A retryable error releases the claim instead.

**The store failing does not fail the turn.** If the store is unreachable the
handler runs the work anyway. A duplicated response is a worse outcome than a
single one and a much better outcome than none.

The operation this rests on is an atomic claim, not a read followed by a write
— see `DYNAMODB-SCHEMA.md`, Table 3. Two invocations racing on the same key
would both read "absent" and both proceed, which is exactly the case the
mechanism exists to prevent and exactly the case that testing on one machine
will not surface.

**Single-process today.** The fixture store is correct in one process and
silently wrong across many: Lambda execution environments share no memory, so
a deployment on it would deduplicate nothing while appearing to work. This is
why the stored implementation is not optional in production, and why it raises
rather than returning plausible results (§7).

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

This is currently an intention, not a fact, and the distinction matters enough
to record. Every test constructs the fixture repository directly, so nothing
enforces the property — the phrase above has been asserted in this document and
in the fixture module's own docstring while no shared suite existed to back it.

What makes it real is one suite written against the *protocol* and
parameterised over its implementations, so adding the stored repository means
running the existing tests rather than writing new ones. Written now, it
becomes the specification the stored implementation is built to satisfy;
written afterwards, it becomes a description of whatever the stored
implementation happened to do. Task 2.10.

The same argument applies to the idempotency store (§6.1), whose fixture and
stored implementations sit behind one protocol for the same reason.

**Unimplemented adapters raise rather than returning empty results.** Both
stored implementations exist as scaffolding so the wiring is proven, and every
method raises. An empty price list would be indistinguishable from a genuine
"no data" outcome, and a store that never deduplicates is indistinguishable
from one where nothing was retried — both would look like working software. A
misconfigured deployment should fail loudly at the first call.

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
| 11.5 No personal data in logs | Identifiers and counts only; never message text or location |
| 11.6 Expiry | Time-to-live on stored plans and sessions |
| 11.7 Data protection | Point-in-time recovery and encryption enabled explicitly |
| 12.3 Exactly-once turns | Atomic claim on a session-scoped key (§6.1) |
| 5.5 Content safety (policy) | Guardrail policy as version-controlled data, validated in CI |
| 5.5 Content safety (enforcement) | Attached to every generation call; call refuses to run without one |
| 5.5 Content safety (verification) | **Not met** — requires a live endpoint |
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

### 10.4 What is not yet verified

Everything in §10.1–10.2 describes code that is built and tested offline. None
of it has been observed working against a live service. A twenty-case red-team
set exists for that — thirteen cases that must be blocked across prompt
injection, unsafe preparation, disordered eating, medical advice,
age-restricted goods and payment data, and seven ordinary grocery questions
that must be *allowed*.

The must-allow half is not padding. Over-blocking is the usual failure mode of
an aggressive policy, and a filter that refuses legitimate grocery questions
has produced a broken product rather than a safe one. A verification set
containing only attacks cannot detect that.

One specific thing to check first: the request shape used to mark untrusted
regions is unverified against the live API. If the guardrail reports zero
prompt-attack evaluations on a known-malicious input, that is where to look —
and it would mean the control has been inert the whole time while appearing
configured.

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
