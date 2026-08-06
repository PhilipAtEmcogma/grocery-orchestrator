# Requirements — Smart Grocery & Meal Budget Assistant

**Status:** Draft for team review
**Author:** Philip (Backend/Orchestration, AI/Prompt Lead)
**Scope:** Whole system. Sections 7–9 cover layers owned by other team members
and are offered as a starting point, not a decision.

Acceptance criteria use EARS notation:
- **WHEN** `<trigger>` **THE SYSTEM SHALL** `<response>` — event-driven
- **IF** `<condition>` **THEN THE SYSTEM SHALL** `<response>` — unwanted behaviour
- **WHILE** `<state>` **THE SYSTEM SHALL** `<response>` — state-driven
- **THE SYSTEM SHALL** `<response>` — ubiquitous

Requirements marked **[P0]** are non-negotiable. Requirements marked
**[GAP]** are known limitations of the current build, specified so they are
tracked rather than forgotten.

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

1.7 **[GAP]** **WHEN** a user asks about several items in one message **THE
SYSTEM SHALL** return a comparison for each item.
*Current build handles only the first item and lowers confidence. Tracked as
`multi-001` and `multi-002` in the intent golden set.*

---

## 2. Meal planning

**User story:** As someone feeding a household on a fixed budget, I want a meal
plan that provably fits my budget, so that I can shop without recalculating.

### Acceptance criteria

2.1 **WHEN** a user requests a meal plan with a stated budget, household size,
and duration **THE SYSTEM SHALL** produce a plan whose total cost does not
exceed the budget.

2.2 **WHEN** producing a meal plan **THE SYSTEM SHALL** compute every line
cost, meal subtotal, and plan total arithmetically from stored prices.

2.3 **WHEN** producing a meal plan **THE SYSTEM SHALL** verify the arithmetic
before delivery and **IF** verification fails **THEN THE SYSTEM SHALL** discard
the plan and regenerate rather than deliver it.

2.4 **THE SYSTEM SHALL** bound regeneration to a configured maximum number of
attempts.

2.5 **WHEN** producing a meal plan **THE SYSTEM SHALL** provide a shopping list
grouped by store, counting each product once at full pack price even where it
is used across several meals.

2.6 **WHEN** producing a meal plan **THE SYSTEM SHALL** set each meal's serving
count to the stated household size.

2.7 **THE SYSTEM SHALL** favour reusing a single product across several meals
over introducing additional products, to reduce both cost and waste.

2.8 **IF** the user states no budget **THEN THE SYSTEM SHALL NOT** infer one,
and shall ask for it instead.

2.9 **[GAP]** **WHEN** producing a meal plan **THE SYSTEM SHALL** select meals
from a curated recipe catalogue rather than composing them freely.
*Not yet built. Decision open — see DYNAMODB-SCHEMA.md "Open decision".
Rationale: a curated catalogue extends the grounding guarantee from prices to
meals, so the model cannot invent an unappetising or uncookable combination.*

---

## 3. Grounding [P0]

**User story:** As a user, I need every price I am shown to be a real price
from a real store, so that I can trust the tool enough to shop from it.

This is the system's central safety property. It is stated as a structural
requirement, not a behavioural one: the design must make violation impossible
rather than unlikely.

### Acceptance criteria

3.1 **THE SYSTEM SHALL NOT** present any monetary value that did not originate
from the price store.

3.2 **THE SYSTEM SHALL** make every monetary value in a response traceable to a
specific record in the price store, by reference.

3.3 **THE SYSTEM SHALL** reach content generation only via a path that has
first performed retrieval. No execution path shall exist that generates a
priced response without retrieval.

3.4 **THE SYSTEM SHALL** provide the language model with no mechanism for
emitting a price. The model's output schema shall contain no price, cost, or
total field.

3.5 **WHEN** assembling a response **THE SYSTEM SHALL** verify that every
referenced record was actually retrieved, and **IF** any reference is
unresolved **THEN THE SYSTEM SHALL** refuse the response rather than deliver it
with the unresolved item omitted.

3.6 **THE SYSTEM SHALL** enforce 3.5 as an automated check in continuous
integration, including a negative case proving an unresolved reference is
rejected.

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
filter configured to block unsafe food advice.

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
*Current build uses a seed dataset. Live acquisition carries terms-of-service
risk that must be assessed before implementation.*

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

12.3 **THE SYSTEM SHALL** process a repeated request identifier exactly once.
*Without this a client timeout retry re-runs generation and is charged twice.*

12.4 **THE SYSTEM SHALL** be deployable from version-controlled infrastructure
definitions.
*Initial build is manual by team decision. Every manually created resource
shall have its configuration exported and committed, so the later conversion is
transcription rather than reconstruction.*
