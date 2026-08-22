# Requirements — Smart Grocery & Meal Budget Assistant

**Status:** Approved production-pilot baseline; implementation gaps remain
**Author:** Philip (Backend/Orchestration, AI/Prompt Lead)
**Scope:** Whole system. Sections 7–9 cover layers owned by other team members
and remain integration requirements rather than claims of ownership.
**Architecture decision:** `docs/adr/0001-deterministic-core-bounded-agent-extensions.md`

Acceptance criteria use EARS notation:
- **WHEN** `<trigger>` **THE SYSTEM SHALL** `<response>` — event-driven
- **IF** `<condition>` **THEN THE SYSTEM SHALL** `<response>` — unwanted behaviour
- **WHILE** `<state>` **THE SYSTEM SHALL** `<response>` — state-driven
- **THE SYSTEM SHALL** `<response>` — ubiquitous

Requirements marked **[P0]** are non-negotiable. Requirements marked
**[GAP]** are approved but not yet implemented. Notes marked **Current defect**
describe behavior that exists in the reference implementation and must be
corrected before deployment; they are not accepted exceptions to the
requirement.

The first release target is a reproducible workshop demonstration followed by
a small anonymous production pilot. The design must preserve a documented path
to Cognito ownership, streaming transport, controlled live acquisition, and
commercial scale without building those components prematurely.

---

## 1. Price comparison

**User story:** As a budget-conscious shopper, I want to ask which store has
the cheapest version of an item near me, so that I can buy it there instead of
defaulting to the closest store.

### Acceptance criteria

1.1 **WHEN** a user asks for the price of a grocery item **THE SYSTEM SHALL**
return prices from every store in the dataset that stocks it, ordered cheapest
first.

1.2 **WHEN** returning a price comparison **THE SYSTEM SHALL** identify exactly
one option as cheapest and state the saving against the dearest option.

1.3 **WHEN** returning a price comparison **THE SYSTEM SHALL** include the store
name, store location, product name as the retailer writes it, pack size, and
the date the price was captured.

1.4 **WHERE** a product is on special **THE SYSTEM SHALL** indicate this
alongside the price.

1.5 **IF** the user's location is provided **THEN THE SYSTEM SHALL** restrict
results to stores within the requested radius.

1.6 **IF** no location is provided **THEN THE SYSTEM SHALL** return national
results rather than refusing the request.

1.7 **WHEN** a user asks about several items in one message **THE SYSTEM
SHALL** return a comparison for each item, up to a configured maximum, and
**IF** any item is not answered — because it could not be resolved, or because
it exceeded that maximum — **THEN THE SYSTEM SHALL** name it rather than
omitting it in silence.
*No longer a gap — implemented and tested. `multi-001` and `multi-002` in the
intent golden set were the tracked cases and now pass as ordinary cases.*
*Implemented with a maximum of five comparisons per turn, which this
requirement did not originally state. The cap is a latency decision against the
synchronous integration ceiling (design.md §9). Extraction is bounded higher
than the comparison cap on purpose: if both used the same limit, the
orchestrator would never see the items it failed to answer and could not name
them, which is the failure the second clause exists to prevent.*

---

## 2. Meal planning

**User story:** As someone feeding a household on a fixed budget, I want a meal
plan whose checkout cost provably fits my budget, so that I can shop without
recalculating or discovering that fractional ingredient use hid the cost of
whole packs.

### Acceptance criteria

2.1 **WHEN** a user requests a meal plan with a stated budget, household size,
and duration **THE SYSTEM SHALL** produce a plan whose authoritative payable
total does not exceed the budget.

2.2 **WHEN** producing a meal plan **THE SYSTEM SHALL** compute every ingredient
use, meal consumption subtotal, required pack count, shopping-list line total,
and plan payable total arithmetically from retrieved prices.

2.3 **WHEN** producing a meal plan **THE SYSTEM SHALL** verify the arithmetic
before delivery and **IF** verification fails **THEN THE SYSTEM SHALL** discard
the plan and regenerate rather than deliver it.

2.4 **THE SYSTEM SHALL** bound regeneration to a configured maximum number of
attempts.

2.5 **WHEN** producing a meal plan **THE SYSTEM SHALL** aggregate repeated use
of the same cited product, round the aggregate quantity up to the required
whole-pack count, list the product once in its store basket, and calculate the
amount payable as full pack price multiplied by that pack count.

2.6 **WHEN** producing a meal plan **THE SYSTEM SHALL** set each meal's serving
count to the stated household size.

2.7 **THE SYSTEM SHALL** favour reusing a single product across several meals
over introducing additional products, to reduce both cost and waste.

2.8 **IF** budget, household size, or duration required for a meal plan is not
stated **THEN THE SYSTEM SHALL NOT** infer it and shall return a contract-valid,
actionable clarification instead.
*Current defect: the reference implementation defaults missing household size
and duration and can route a missing budget through the infeasible-plan path.
Pilot Task 4 corrects this before deployment.*

2.9 **[GAP]** **WHEN** producing a meal plan **THE SYSTEM SHALL** select meals
from a curated recipe catalogue rather than composing them freely.
*Not yet built. The approved direction is catalogue-constrained selection:
the model selects recipe ids and product citations while deterministic code
owns scaling, dietary verification, arithmetic, and payable totals (Pilot Task
15).*

---

## 3. Grounding [P0]

**User story:** As a user, I need every price I am shown to be a real price
from a real store, so that I can trust the tool enough to shop from it.

This is the system's central safety property. It is stated as a structural
requirement, not a behavioural one: the design must make violation impossible
rather than unlikely.

### Acceptance criteria

3.1 **THE SYSTEM SHALL NOT** present any source price that did not originate
from the price store. Derived savings and totals shall be computed only from
those source prices.

3.2 **THE SYSTEM SHALL** make every source price and derived monetary value in
a response traceable to the exact price-store records from which it was
computed.

3.3 **THE SYSTEM SHALL** reach content generation only via a path that has
first performed retrieval. No execution path shall exist that generates a
priced response without retrieval.

3.4 **THE SYSTEM SHALL** provide the language model with no mechanism for
emitting a price. The model's structured output schema shall contain no price,
cost, saving, or total field.

3.5 **WHEN** assembling a response **THE SYSTEM SHALL** verify that every
referenced record was actually retrieved, every citation's table/partition
key/sort key identifies that exact stored record, and every cited value equals
the retrieved value. **IF** any check fails **THEN THE SYSTEM SHALL** refuse
the response.

3.6 **THE SYSTEM SHALL** enforce 3.5 as an automated check in continuous
integration, with negative cases for unknown references, incorrect source
keys, altered values, and content emitted before its citation.

3.7 **WHEN** generating or assembling free text about prices **THE SYSTEM
SHALL** reject literal monetary values in every user-visible prose-like field.
The model may use citation placeholders for reference validation, but the
renderer shall resolve them only to non-monetary product/store labels; source
prices remain in citation events and structured content with `citation_ref`.
Non-essential text that violates this rule shall be discarded. Essential
structured content that violates it shall fail the response rather than
degrade.
*Current defect: the prose renderer expands placeholders into figures and
comparison reasoning contains a literal price. Neither token events nor the
reasoning field carries its own `citation_ref`, so both violate the target
wire-level rule. Pilot Task 2 removes literal money from all prose-like fields
and broadens the assertion accordingly.*

3.8 **THE SYSTEM SHALL** emit a citation before every event that references it
and shall reject a response whose event ordering violates this rule.

3.9 **THE SYSTEM SHALL** require every published price citation to include the
exact source key, store location, and capture date.
*Current defect: the source partition key is derived from store and category,
while the products table key is store and location. Documentation describes
the required key; code and generated samples remain non-conforming until Pilot
Task 2.*

---

## 4. Honest failure [P0]

**User story:** As a user, I would rather be told the tool cannot help than be
given a plausible answer that is wrong.

### Acceptance criteria

4.1 **IF** no price data exists for the requested item **THEN THE SYSTEM SHALL**
say so explicitly and offer to check something else.

4.2 **THE SYSTEM SHALL** treat "no data" as a successful outcome distinct from
an error.

4.3 **IF** free-text input cannot be confidently matched to a known product
**THEN THE SYSTEM SHALL** return no match rather than the nearest match.
*A confident wrong price is worse than no answer. Under-matching is
recoverable; mis-matching silently misleads.*

4.4 **IF** no plan can be produced within the stated budget after the maximum
regeneration attempts **THEN THE SYSTEM SHALL** report this and suggest
concrete alternatives — raising the budget, reducing the duration, or seeing
the cheapest available option.

4.5 **IF** a plan is reported as infeasible **THEN THE SYSTEM SHALL NOT** also
deliver the failing plan.

4.6 **WHEN** any error occurs **THE SYSTEM SHALL** return a response conforming
to the published contract. No failure path shall return an unparseable body.

4.7 **THE SYSTEM SHALL NOT** expose internal error detail, stack traces, or
configuration values in user-facing messages.

---

## 5. Dietary safety [P0]

**User story:** As someone with an allergy, I need my exclusions honoured in
every part of the response, because a mistake could harm me.

### Acceptance criteria

5.1 **WHEN** a user states a dietary exclusion **THE SYSTEM SHALL** exclude
matching products from every meal in the plan.

5.2 **WHEN** exclusions are supplied both in the message and by the client
interface **THE SYSTEM SHALL** apply the union of both.
*Exclusions are additive and never overridden. Dropping a restriction is the
dangerous direction of error.*

5.3 **WHEN** regenerating a plan **THE SYSTEM SHALL** restate every constraint,
including dietary exclusions, in the regeneration request.
*Model invocations are stateless. An instruction to "keep all exclusions"
without naming them is not followable.*

5.4 **THE SYSTEM SHALL** verify exclusion compliance against the products
actually retrieved, not against the model's report of what it applied.

5.5 **THE SYSTEM SHALL** route all generated content through a content safety
filter configured to block unsafe food advice, and **IF** no filter is
configured **THEN THE SYSTEM SHALL** refuse to invoke the model.
*Partially met. The code-side half is built and tested: the filter policy is
version-controlled data rather than console state, a validator rejects
configurations that would silently do nothing, untrusted input is tagged, and
generation fails closed when no filter is configured. Guardrail
`b1xezpqe04kx`, version `1`, has basic attached live-invocation evidence. That
does not prove policy quality or intervention propagation: Pilot Task 3 must
run the twenty-case must-block/must-allow harness through an accessible model
path and prove every graph node preserves `GuardrailBlocked` to the single
service outcome.*

5.6 **IF** a stated dietary exclusion cannot be reliably mapped to the
retrieval filter **THEN THE SYSTEM SHALL** refuse the meal plan and report
the terms it can honour, rather than produce a plan that filters an
incomplete map.
*Silently ignoring an unmappable term was the shape of the bug that used to
serve dairy to a vegan user: "vegan" was extracted, no mapping existed, the
term was dropped, and no downstream check caught it. Honest refusal is safer
than a best-effort plan. Enforced structurally by routing to
`emit_dietary_unsupported` before retrieval, with the mapping table in
`src/graph/dietary.py` reviewable in one place. Returns
`ErrorCode.UNSUPPORTED_EXCLUSION`; the message names the terms we can honour
so the user has an actionable next step.*

---

## 6. Conversational interface

**User story:** As a user, I want to describe what I need in plain language
rather than filling in a form.

### Acceptance criteria

6.1 **WHEN** a message is received **THE SYSTEM SHALL** classify it as a price
check, a meal plan request, general conversation, or out of scope.

6.2 **WHEN** a message is received **THE SYSTEM SHALL** extract any stated
budget, household size, duration, dietary exclusions, and store preferences.

6.3 **IF** a constraint is not stated **THEN THE SYSTEM SHALL NOT** infer a
value for it.

6.4 **IF** a constraint stated in the message conflicts with one supplied by
the client interface **THEN THE SYSTEM SHALL** use the message value and inform
the user of the override.

6.5 **THE SYSTEM SHALL** treat all user text as untrusted data and shall not
act on instructions contained within it.

6.6 **IF** a message contains an instruction directed at the system rather than
a grocery request **THEN THE SYSTEM SHALL** classify it on its grocery content
alone, or as out of scope if it has none.

---

## 7. Frontend interface

*Owned by the frontend team. Specified here because the contract is the seam
between layers.*

7.1 **THE SYSTEM SHALL** exchange messages using a versioned contract, with the
version present in every request and response.

7.2 **THE SYSTEM SHALL** structure every response as an ordered sequence of
typed events with a monotonic sequence number.

7.3 **THE SYSTEM SHALL** emit a terminal event on every turn, including turns
that failed.

7.4 **THE SYSTEM SHALL** transmit monetary values as decimal strings, never as
floating-point numbers.

7.5 **THE SYSTEM SHALL** emit a reference record before any event that cites it.

7.6 **THE SYSTEM SHALL** emit a classification event before any content event,
so the interface can select its presentation before content arrives.

7.7 **WHEN** the contract changes additively **THE SYSTEM SHALL** retain the
existing major version, and the client shall ignore unrecognised event types
rather than failing.

7.8 **THE SYSTEM SHALL** display the price capture date and special status to
the user.

7.9 **[GAP]** **WHEN** a response takes longer than a few seconds **THE SYSTEM
SHALL** stream partial results as they become available.
*Contract is already event-shaped to permit this. Transport upgrade not yet
built.*

---

## 8. Price data

*Owned jointly. Ingestion design offered for review.*

8.1 **THE SYSTEM SHALL** store prices for every tracked product at every
tracked store location.

8.2 **THE SYSTEM SHALL** answer "cheapest version of one product across all
stores" in a single indexed query, without scanning.

8.3 **THE SYSTEM SHALL** normalise differing retailer product names to a single
canonical product identifier.
*Retailers write the same product differently. Without normalisation the
comparison silently compares nothing.*

8.4 **THE SYSTEM SHALL** refresh prices on a schedule and record the capture
date with each price.

8.5 **IF** refreshing one retailer fails **THEN THE SYSTEM SHALL** complete the
refresh for the others and record the failure.

8.6 **THE SYSTEM SHALL** store monetary values in a form that does not lose
precision.

8.7 **THE SYSTEM SHALL** retain no personal information in the price store.

8.8 **[GAP]** **THE SYSTEM SHALL** obtain prices from live retailer sources.
*Current build uses a seed dataset. The terms-of-service risk is now assessed
— `ACQUISITION-RISK.md`, Task 7.9 — and live acquisition is permitted only
under the conditions in its §8. Note §4.5: the binding constraint is the Fair
Trading Act, and it attaches to the comparison we publish rather than to the
act of collection, which makes the capture date required by 8.4 a
user-facing obligation and not merely a stored field.*

---

## 9. Model selection

9.1 **THE SYSTEM SHALL** select a model per task from a configurable catalogue,
rather than referencing model identifiers in application code.

9.2 **THE SYSTEM SHALL** record each model's capabilities and shall adapt its
request format to them.
*Models differ in tool-use support, caching support, and output limits.
Assuming uniform behaviour fails on the first model from another provider.*

9.3 **IF** no configured model can serve a task **THEN THE SYSTEM SHALL** raise
an error rather than substituting a different model.
*Silent substitution changes output quality with no signal.*

9.4 **THE SYSTEM SHALL** use a lower-cost model for classification and
regeneration, and a higher-capability model for initial plan composition.

9.5 **THE SYSTEM SHALL NOT** route production traffic to a model that has not
been scored against the evaluation suites.

9.6 **WHERE** a model supports prompt caching **THE SYSTEM SHALL** structure
requests with static content first, and shall verify cache utilisation rather
than assume it.

---

## 10. Evaluation

10.1 **THE SYSTEM SHALL** maintain a golden set of representative user messages
with expected classifications and extracted constraints.

10.2 **THE SYSTEM SHALL** maintain a set of meal plan cases with invariants that
must hold and quality metrics that are reported.

10.3 **THE SYSTEM SHALL** report known limitations separately from failures, and
shall not count them toward the score.

10.4 **THE SYSTEM SHALL NOT** have expectations altered to match observed model
output without an explicit record of why.

10.5 **WHEN** a prompt is modified **THE SYSTEM SHALL** have its evaluation
suite re-run, and the score recorded before and after.

---

## 11. Security and privacy

11.1 **THE SYSTEM SHALL** grant each component only the permissions it requires,
scoped to specific named resources.

11.2 **THE SYSTEM SHALL** hold no credential in source, in configuration files,
or in environment variables.

11.3 **THE SYSTEM SHALL** validate and constrain all input at the system
boundary.

11.4 **THE SYSTEM SHALL** rate-limit inbound requests.

11.5 **THE SYSTEM SHALL** record no personal information in logs, including
message content and location.

11.6 **THE SYSTEM SHALL** expire stored conversation and plan data
automatically.
*Household size, budget, and dietary restrictions are personal information;
dietary restrictions may imply health information. Retaining them longer than
needed is the risk to avoid under the Privacy Act 2020.*

11.7 **THE SYSTEM SHALL** enable point-in-time recovery and encryption at rest
on all stored data.

11.8 **THE SYSTEM SHALL** apply security controls as each component is built,
not as a final phase.

---

## 12. Operability

12.1 **THE SYSTEM SHALL** emit structured logs correlated by session and turn.

12.2 **THE SYSTEM SHALL** record per-turn latency, token consumption, model
used, and regeneration attempts as queryable metrics.

12.3 **THE SYSTEM SHALL** process a repeated request identifier exactly once,
and **IF** the same identifier arrives with different validated content **THEN
THE SYSTEM SHALL** reject it rather than answer from cache.

12.3.1 **THE SYSTEM SHALL** scope an idempotency key by session and turn and
fingerprint the canonical validated request, not raw transport bytes; object-key
order, insignificant JSON whitespace, and omitted-versus-explicit-null optional
fields shall not produce different fingerprints.

12.3.2 **WHEN** an invocation acquires a new or stale claim **THE SYSTEM SHALL**
create and return a fresh opaque owner token/version.

12.3.3 **WHEN** an invocation completes or releases a claim **THE SYSTEM SHALL**
condition the operation on both `in_progress` status and the owner token/version
returned by its acquire; **IF** that condition fails **THEN THE SYSTEM SHALL
NOT** overwrite or delete the newer owner's claim.

12.3.4 **THE SYSTEM SHALL** detect requests still in flight, cache only terminal
outcomes, release its own claim after retryable failure, and degrade a store
outage to running the work rather than failing the turn.

*Without this a client timeout retry re-runs generation and is charged twice.
Session scoping prevents cross-user collisions. Owner fencing prevents an old
invocation that resumes after stale takeover from completing or deleting the
new owner's claim. The DynamoDB implementation is live-verified for the five
current outcomes, but canonical fingerprinting and owner fencing remain Pilot
Task 6 work and are not production-ready.*

12.4 **THE SYSTEM SHALL** be deployable from version-controlled infrastructure
definitions, adopting existing stateful resources rather than recreating them.
*CDK is not implemented. Pilot Tasks 9–10 define separate stateful and service
constructs, deterministic synthesis, and a reviewed import-before-deploy
sequence.*

12.5 **IF** a production stage is missing DynamoDB, Bedrock, a numbered
Guardrail version, stored idempotency, strict CORS, or named resource
configuration **THEN THE SYSTEM SHALL** fail startup rather than select a demo
implementation.

12.6 **THE SYSTEM SHALL** measure p50, p95, and p99 latency by task, successful
service response rate, unhandled 5xx rate, model/token/cache use, and estimated
cost per successful task.

12.7 **THE SYSTEM SHALL** alarm at 50%, 80%, and 100% of the approved monthly
pilot budget and require review of unit-cost regressions above 20%.

12.8 **THE SYSTEM SHALL** provide alarms for escaped handlers and API 5xx before
pilot traffic, then add measured alarms for latency, model errors, Guardrail
interventions, throttling, stale data, idempotency failures, and silent turns.

---

## 13. MCP and bounded agentic workflows

13.1 **[GAP]** **THE SYSTEM SHALL** provide a local read-only MCP façade for an
approved client, initially Kiro, whose coarse tools invoke the complete
deterministic application service.

13.2 **THE MCP FAÇADE SHALL NOT** expose raw DynamoDB operations, AWS SDK calls,
filesystem access, arbitrary network access, retailer acquisition, production
writes, citation creation, or unguarded model generation.

13.3 **WHEN** an MCP tool is called **THE SYSTEM SHALL** validate its input and
output schemas, enforce row/call/time limits, sanitize results, and record a
privacy-safe audit event.

13.4 **THE SYSTEM SHALL** preserve the same grounding, dietary, arithmetic,
Guardrail, idempotency, and contract assertions whether the application service
is invoked through REST, local code, or MCP.

13.5 **[GAP]** **THE SYSTEM SHALL** permit a bounded data-quality agent to
review only a capped ingestion snapshot with read-only tools and produce cited
findings for human approval.

13.6 **THE DATA-QUALITY AGENT SHALL NOT** publish prices, mutate production
data, act on a finding without deterministic reference validation, or receive
raw user messages, locations, or dietary data.

13.7 **THE SYSTEM SHALL NOT** use Bedrock Agents Classic. **THE SYSTEM SHALL
NOT** use AgentCore unless measured p99 meal-plan latency exceeds approximately
25 seconds after the documented mitigations and a mentor approves the change.

---

## 14. Production-pilot acceptance

14.1 **THE SYSTEM SHALL** pass 100% of grounding, literal-money, arithmetic,
dietary fail-closed, Guardrail-propagation, and negative-control tests before a
release candidate is deployed.

14.2 **THE SYSTEM SHALL NOT** enable a model for a production route unless it
has a published scorecard and scores at least 90% on the applicable golden set.

14.3 **THE SYSTEM SHALL** target p95 price-check latency below 5 seconds, p95
meal-plan latency below 20 seconds, and p99 meal-plan latency below the
approximately 25-second escalation trigger, subject to deployment measurement.

14.4 **THE SYSTEM SHALL** target at least 99% successful service responses
during the pilot, excluding intentional contract-valid refusals, and less than
1% unhandled 5xx responses.

14.5 **THE SYSTEM SHALL NOT** record message text, raw location, dietary
values, credentials, or model prompts in logs or traces.

14.6 **THE SYSTEM SHALL NOT** publish a price unless its exact source key,
store location, and capture date have been validated.
