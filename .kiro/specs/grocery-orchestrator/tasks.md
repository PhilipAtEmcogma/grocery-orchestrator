# Tasks — Smart Grocery & Meal Budget Assistant

**Status:** Draft for team review
**Traces to:** `requirements.md`, `design.md`

Status reflects the reference implementation built before the Kiro rebuild.
`[x]` items exist and are tested; they are the evidence the specification is
achievable, and their tests are the acceptance criteria for the rebuild.

Items that cannot be completed without the AWS account are marked
**[blocked: AWS]**. They are not "not started" — in most cases the code-side
half is built and tested, and what remains is verification against a live
service. Where a task had both halves, it has been split so the completed half
is not hidden behind the blocked one.

Where a task was implemented differently from how it was specified, the
difference is recorded in the note beneath it rather than by editing the task
to match the code. The specification is the input to the rebuild; a quietly
adjusted task teaches the rebuild nothing.

---

## Phase 1 — Interface and foundations

- [x] **1.1** Define the request and response contract as executable schemas
  — *Req 7.1–7.6*
- [x] **1.2** Publish worked examples for every response shape, including
  failures — *Req 4.6*
- [x] **1.3** Implement the grounding assertion with a negative test
  — *Req 3.5, 3.6*
- [x] **1.4** Implement arithmetic verification — *Req 2.3*
- [x] **1.5** Generate a seed price dataset with deliberately inconsistent
  retailer naming — *Req 8.3*
- [ ] **1.6** Circulate the contract to the frontend team and resolve the open
  questions in `CONTRACT-v1.md`
- [x] **1.7** Record the locked technical, security and AI-quality decisions as
  steering documents
- [x] **1.8** Publish a repository working agreement and a README describing
  the build for someone picking it up cold

*Naming in 1.5 is inconsistent on purpose. A tidy dataset hides the
normalisation problem until real data arrives.*

*1.6 remains the only open item that blocks another team. Its four questions
are still unanswered in `CONTRACT-v1.md`.*

*1.7–1.8 were built but never specified. They are the artefacts that carry the
three invariants and the eval discipline forward into the rebuild, so they are
tracked here rather than treated as incidental.*

---

## Phase 2 — Orchestrator

- [x] **2.1** Define the state object with required and optional fields
- [x] **2.2** Implement the state machine topology — *Req 3.3*
- [x] **2.3** Implement the retrieval node as the sole creator of references
  — *Req 3.1, 3.2*
- [x] **2.4** Implement the no-data path as a success outcome — *Req 4.1, 4.2*
- [x] **2.5** Implement the regeneration cycle with a bound — *Req 2.4*
- [x] **2.6** Implement honest refusal on exhausted attempts, discarding the
  failing draft — *Req 4.4, 4.5*
- [x] **2.7** Define the price store protocol with a fixture implementation
- [x] **2.8** Implement exact-match product resolution with no fuzzy fallback
  — *Req 4.3*
- [ ] **2.9** Implement the stored price repository against the same protocol
  — *Req 8.1, 8.2* — **[blocked: AWS]**
- [x] **2.10** Build one test suite, parameterised over every implementation of
  the price store protocol, and run it against both
- [x] **2.11** Resolve and compare every item in a multi-item request, naming
  both the items that could not be resolved and the items that exceeded the
  per-turn cap — *Req 1.7*
- [x] **2.12** Refuse a meal plan whose stated dietary exclusion cannot be
  reliably mapped, with the mapping table as a single reviewable source of
  truth — *Req 5.6*

*2.9 is the only orchestrator task blocked on cloud access. Everything above it
was built and tested without it.*

*2.10 is the acceptance criteria for 2.9 and does not depend on it —
`tests/test_price_repository_contract.py`, 31 properties over the protocol.
Written before the stored implementation on purpose: written first it is the
specification 2.9 is built to satisfy; written afterwards it would only
describe whatever 2.9 happened to do.*

*It uses protocol members only — test data is discovered through
`candidates_for_budget` rather than read from `fixtures/products.json`, because
anything convenient that is not on the Protocol is exactly what will not exist
on the DynamoDB side. The stored parameter skips unless
`PRICE_REPO_DYNAMO_TABLE` is set, so CI stays credential-free, and a skip
reports as **unverified** rather than passing quietly.*

*Verified to have teeth against five deliberately broken implementations — a
substring matcher, a dearest-first store, float money, a leaking exclusion
filter, a widening store filter — all caught, with no false positives on the
conforming one. A conformance suite that cannot fail certifies nothing; 8.3
records what that costs.*

*Writing it surfaced a real ambiguity: `cheapest_for_product(stores=[])` was
accepted through `if stores:`, so an explicit empty filter meant "no filter"
and returned every store. None and `[]` are now specified as distinct in the
protocol, because silently widening a constraint is the dangerous direction. No
caller passed `[]`, so the fix changed no behaviour — it closed a trap that the
second implementation would otherwise have resolved by guesswork, in either
direction, with nothing to catch the disagreement.*

*2.11 was **moved out of Deferred** — it is implemented and tested. It was
specified as unbounded ("a comparison for each item") and implemented with a
cap of five comparisons per turn, which is a latency decision against the
gateway's 29-second ceiling. Items past the cap were originally dropped in
silence; they are now named in a notice, because the requirement's second
clause applies to them as much as to items that failed to resolve. Extraction
is deliberately bounded higher than the comparison cap so the overflow is
knowable at all — see the note on `MAX_EXTRACTED_ITEMS`.*

*2.12 closes the safety bug that a "vegan" or "gluten-free" user could get
meat, dairy or gluten in their meal plan. Extraction produced the term, no
mapping honoured it, and no downstream check caught the silent drop.
`src/graph/dietary.py::SUPPORTED_EXCLUSIONS` is now the single reviewable
source of truth for what an exclusion means; `classify_intent` records any
term it cannot map; the router sends meal-plan turns with a non-empty
"unsupported" list to `emit_dietary_unsupported` before doing retrieval or
generation work; and the response carries `ErrorCode.UNSUPPORTED_EXCLUSION`
with a message naming the terms we can honour. Enforced structurally rather
than by verification: a plan we cannot filter reliably is never built. A
larger fix (per-product allergen tagging in fixtures, enabling gluten-free
and nut-free support) is future work — see Phase 11.*

---

## Phase 3 — Model layer

- [x] **3.1** Define the model protocol with task-based selection — *Req 9.1*
- [x] **3.2** Implement a scriptable client able to force specific failures
- [x] **3.3** Build the model catalogue as configuration — *Req 9.1*
- [x] **3.4** Implement capability-aware request construction — *Req 9.2*
- [x] **3.5** Implement the fallback path for models without tool use
- [x] **3.6** Raise rather than substitute when no model can serve a task
  — *Req 9.3*
- [x] **3.7** Route classification and regeneration to the low-cost tier
  — *Req 9.4*
- [x] **3.8** Insert cache markers only where supported and above the minimum,
  and record utilisation — *Req 9.6*
- [ ] **3.9** Verify cache utilisation against a live endpoint
  — **[blocked: AWS]**
- [x] **3.10** Implement the managed-inference adapter behind the model
  protocol

*3.10 was built but never specified. The adapter is written and structurally
complete — capability branching, cache markers, guardrail attachment, usage
capture, and a distinct blocked-by-safety-filter error — but it has never run
against a live endpoint, so it carries no test coverage beyond stubbed
transport. Everything above it is proven by the scripted client, which is why
it is the only new surface when the account lands.*

---

## Phase 4 — Prompts

- [x] **4.1** Classification and extraction prompt with delimited untrusted
  input — *Req 6.1, 6.2, 6.5*
- [x] **4.2** Reject inference of unstated constraints — *Req 6.3*
- [x] **4.3** Reconcile message and interface constraints, message winning,
  with the override reported — *Req 6.4*
- [x] **4.4** Apply exclusions additively — *Req 5.2*
- [x] **4.5** Plan composition prompt with a price-free output schema
  — *Req 3.4*
- [x] **4.6** Regeneration prompt restating **all** constraints — *Req 5.3*
- [x] **4.7** Prose generation prompt for plan explanation — *Req 3.7*

*4.6 was originally implemented restating only the budget. Evaluation revealed
that a plan for a user with a stated allergy was being regenerated with no
knowledge of the allergy. Unit tests did not catch it; only end-to-end
evaluation against realistic constraint combinations did.*

*4.7 was specified as a prompt and delivered as considerably more: a prompt, a
placeholder-only output schema, a graph node, a renderer that expands
placeholders into retrieved figures, a rejection check for money-shaped
strings, and a degradation path that drops the prose rather than failing the
turn. It also covers price checks, not only plans as the task states. The node
half of that work has no Phase 2 entry; it is recorded here because that is
where it was specified, and in `design.md` §3 where the topology is.*

---

## Phase 5 — Evaluation

- [x] **5.1** Golden set for classification and extraction — *Req 10.1*
- [x] **5.2** Runner scoring accuracy, latency, and cost per model
- [x] **5.3** Report known limitations separately from failures — *Req 10.3*
- [x] **5.4** Meal plan cases with invariants and reported metrics — *Req 10.2*
- [x] **5.5** Budget floor check, not only the ceiling
- [ ] **5.6** Subjective quality scoring for variety and appeal
- [ ] **5.7** Score every candidate model and publish the comparison
  — *Req 9.5* — **[blocked: AWS]**
- [x] **5.8** Red-team case set for content safety, covering both content that
  must be blocked and content that must be allowed
- [ ] **5.9** Harness that runs the red-team set against a live endpoint and
  reports each case's outcome — **[blocked: AWS]** for execution only

*5.3's mechanism is built and currently has nothing to report: no case in
either golden set carries a known-gap marker any more. The last two were the
multi-item cases, which now pass (see 2.11). The mechanism should stay — it is
what stops the next gap from being deleted to raise a score.*

*5.8 was built but never specified. Twenty cases: thirteen that must be blocked
across prompt injection, unsafe food preparation, disordered eating, medical
advice, age-restricted goods and payment data, and seven legitimate grocery
questions that must be allowed. The must-allow half matters as much as the
must-block half — over-blocking is the usual failure mode of an aggressive
policy, and a filter that refuses ordinary grocery questions is a broken
product rather than a safe one.*

*5.9 does not exist. The case set is currently data that nothing consumes. The
harness itself can be written offline; only running it needs an endpoint.
Writing it before the account lands means live verification is one command
rather than a manual afternoon.*

---

## Phase 6 — Service layer

- [x] **6.1** Request handler returning a contract-valid response on every
  path — *Req 4.6*
- [x] **6.2** Map every failure class to a defined error code
- [x] **6.3** Exclude internal detail from user-facing messages — *Req 4.7*
- [x] **6.4** Resolve dependencies inside the error boundary
- [x] **6.5** Local development server for frontend integration
- [x] **6.6** Idempotent handling of repeated request identifiers, against a
  single-process store — *Req 12.3*
- [x] **6.7** Structured logging, tracing and metrics — *Req 12.1, 12.2*
- [ ] **6.8** Implement the stored idempotency store against the same protocol
  — *Req 12.3* — **[blocked: AWS]**

*6.4: dependency construction was originally outside the error boundary, so a
misconfigured store returned an unparseable failure — the exact outcome the
handler exists to prevent.*

*6.6 was specified as one line — "process a repeated request identifier exactly
once" — and implemented with four decisions the requirement does not mention:
keys are scoped by session because clients generate turn identifiers and two
sessions can collide; the payload is fingerprinted so a reused identifier with
different content is rejected rather than answered from cache; in-flight
requests are detected, because a retry usually arrives while the first attempt
is still running; and only terminal outcomes are cached, because caching a
transient failure would make the client's retry permanently useless. A store
outage degrades to running the work rather than failing the turn.*

*6.7 is Powertools — Logger, Tracer and Metrics — attached at the handler
boundary only. The graph and both eval harnesses import none of it, so they
still run with no AWS account, which is the property CI depends on; a test
walks the import graph and fails if it escapes. Tracing reaches inside the
graph without the graph knowing, by wrapping the `PriceRepository` and
`ModelClient` protocols in decorators that implement the same interfaces —
the same seam that already lets fixtures stand in for DynamoDB.*

*6.7 also declined Powertools' idempotency utility. Ours (6.6) fingerprints
the payload, scopes keys by session, detects in-flight requests and caches
only terminal outcomes; the utility's model is close but not the same, and
replacing four tested decisions with a library default would be a behaviour
change dressed as a dependency upgrade.*

*The repair loop is traced as one subsegment per attempt rather than one span
around the loop, because the loop spans four graph nodes and wrapping it
would put tracing inside the graph. Per-attempt timings are the better input
to 10.5 anyway: the question is what a second and third generation cost
against the 29-second ceiling, not what they cost together.*

*Found while wiring the per-model metric: `generate_plan` never passed a
`task`, so it took the parameter's default of `classify_intent` and every
plan call routed to the FAST model — contradicting the QUALITY tier the node
asks for and leaving the `generate_plan` and `repair_plan` rules in
`config/models.json` unreachable. Invisible under the scripted client, which
ignores routing. Fixed here because the metric is keyed on the same label.*

*6.6 and 6.8 are split because the completed half is correct only in a single
process. Lambda execution environments share no memory, so in deployment the
in-memory store deduplicates nothing while appearing to work. 6.6 is genuinely
done and genuinely insufficient on its own; 6.8 is what makes the requirement
hold in production.*

---

## Phase 7 — Data and ingestion

- [ ] **7.1** Create the products table with the price-ordered index
  — *Req 8.2* — **[blocked: AWS]**
- [ ] **7.2** Create the meals table with expiry — *Req 11.6*
  — **[blocked: AWS]**
- [ ] **7.3** Enable recovery and verify encryption — *Req 11.7*
  — **[blocked: AWS]**
- [ ] **7.4** Load the seed dataset — **[blocked: AWS]**
- [ ] **7.5** Implement per-retailer acquisition with isolated failure
  — *Req 8.5*
- [ ] **7.6** Orchestrate acquisition with parallel per-retailer error handling
- [ ] **7.7** Implement name normalisation in ingestion — *Req 8.3*
- [ ] **7.8** Schedule the refresh — *Req 8.4*
- [x] **7.9** Assess terms-of-service risk before live acquisition — *Req 8.8*
  — `ACQUISITION-RISK.md`
- [ ] **7.10** Create the idempotency table with expiry — *Req 12.3*
  — **[blocked: AWS]**

*7.9 gated 7.5 and is now done. Legal assessment precedes implementation, not
the reverse — and having run it, the two halves separate. **7.5 is unblocked**:
it is the acquisition structure, built against fixtures, sending no production
traffic. **11.4 stays gated**, but on the named conditions in
`ACQUISITION-RISK.md` §8 rather than on an open question. A gate reading "risk
unassessed" is unfalsifiable and never clears.*

*Three findings change how 7.5 and 11.4 should be built. Foodstuffs
`robots.txt` disallows the search endpoints — the catalogue sitemaps are the
sanctioned traversal, and they are the better engineering anyway. The
Woolworths NZ terms could not be retrieved and are treated as prohibitive until
a human confirms them. And the binding constraint is the Fair Trading Act,
which attaches to the comparison we publish rather than to the fetch: capture
date must be surfaced to the user, not merely stored under Req 8.4.*

*7.10 is new: the idempotency store needs a table of its own, and it was absent
from both this phase and the schema document. Its `acquire` operation is a
conditional write, not a read followed by a write — see `DYNAMODB-SCHEMA.md`.
It gates 6.8.*

---

## Phase 8 — Security

- [x] **8.1** Static security analysis in the linter
- [x] **8.2** Dependency vulnerability scanning
- [x] **8.3** Secret scanning
- [x] **8.4** Exclude personal data from logs — *Req 11.5*
- [ ] **8.5** Per-function roles scoped to named resources — *Req 11.1*
  — **[blocked: AWS]**
- [ ] **8.6** Managed secret storage — *Req 11.2* — **[blocked: AWS]**
- [ ] **8.7** Gateway throttling and usage plans — *Req 11.4*
  — **[blocked: AWS]**
- [ ] **8.8** Authentication on the endpoint — **[blocked: AWS]**
- [x] **8.9** Define the content safety policy as version-controlled data and
  validate it offline — *Req 5.5*
- [ ] **8.10** Verify the content safety policy against a live endpoint using
  the red-team set from 5.8 — *Req 5.5* — **[blocked: AWS]**
- [x] **8.11** Tag untrusted input so the prompt-attack filter evaluates it
  — *Req 6.5*
- [x] **8.12** Fail closed when no content safety filter is configured
  — *Req 5.5*
- [x] **8.13** Commit an audited secret-scanning baseline and use a command
  that fails the build on a new finding

*8.3 was briefly returned to `[ ]` on the belief that the scan silently passed.
That was wrong, and the real history is worse. Because the baseline was
excluded from version control, `detect-secrets scan --baseline` did not
regenerate it and carry on — it exited 2 with `Invalid path`, failing the job.
**Continuous integration on the default branch was red for four consecutive
commits** and the failure was not acted on. The gate was not silent; it was
shouting, and nobody was listening. That is the more useful finding, because a
gate nobody reads is worth exactly as much as a gate that cannot fail.*

*8.13 fixed both defects. The baseline is committed and audited — two findings,
both confirmed false positives: a guardrail policy entity type named
`PASSWORD`, and a deliberately fake connection string in the test that asserts
the handler does not leak it. Neither is a real credential, so nothing genuine
has been whitelisted. Paths are stored with forward slashes, because a baseline
generated on Windows would otherwise present every known finding as new to the
Linux runner.*

*The command was changed too, and this defect was real and would have surfaced
the moment the first one was fixed: `detect-secrets scan --baseline` **rewrites
the baseline in place and exits zero** when it finds something new. It is a
maintenance command, not a gate. `detect-secrets-hook` is the one that exits
non-zero. Fixing only the missing file would have turned a loudly failing job
into a silently passing one — strictly worse.*

*8.3 is `[x]` because the gate has now been observed doing both halves of its
job: exit 1 against a planted AWS key, and green on run 31308163941, the first
passing run on the default branch in four commits. A security gate is complete
when it has been seen to fail on a planted defect and pass without one — not
when it is wired, and not on the word of whoever wired it.*

*8.4 is verified by reading the handler, not by a test. Every log statement
records identifiers, counts and durations only — never message text, never
location. A test asserting that property would be worth adding, since the
failure mode is a single careless log line added later.*

*8.9 and 8.10 are split because the offline half is genuinely finished and the
live half genuinely cannot start. What exists: the policy as a reviewable,
diffable configuration file rather than console state; a validator that fails
the build on the mistakes which produce a policy that silently does nothing —
a prompt-attack filter below maximum strength, a denied topic with too few
examples to classify well, refusal messaging too short to help a user; and
tests over the policy content itself. What does not exist: any evidence the
policy behaves as intended, because a filter's behaviour is only observable
against the live service.*

*8.11 was built but never specified, and it is the step most easily missed. The
prompt-attack filter evaluates nothing unless untrusted regions of the prompt
are tagged — it can be enabled, appear healthy, and never fire once. Tags carry
a fresh random suffix per request, because a fixed tag is guessable and a
guessed tag can be closed early to smuggle text into the trusted region. The
implementation strips any occurrence of its own tag from the input first.
Retrieved product data is untrusted too: it originates from scraped retailer
content, and a product name is somewhere an instruction could be placed.*

*8.12 was built but never specified. A generation call refuses to run when no
content safety filter is configured. Opting out is possible for local work but
must be an explicit, visible configuration choice, never the accidental result
of forgetting to set an identifier.*

*8.10 remains the highest-priority security item that is blocked.*

---

## Phase 9 — Automation

- [x] **9.1** Pre-commit gate running every check that is fast and offline,
  version controlled so a fresh clone gets the same gate
- [x] **9.2** Agent hooks for save-time verification
- [x] **9.3** Blocking hook on contract changes
- [x] **9.4** Blocking hook verifying grounding invariants
- [x] **9.5** Hook requiring evaluation after prompt changes — *Req 10.5*
- [x] **9.6** Continuous integration on pull requests
- [ ] **9.7** Require review on contract and orchestrator changes
- [x] **9.8** Protect the default branch behind the aggregate check

*9.1 originally ran lint and tests only, while CI additionally ran a secret
scan, contract validation, guardrail policy validation and both eval floors.
A local gate that is a strict subset of the remote gate is worse than none: it
trains you to read a green signal as meaning something it does not. That is not
hypothetical — it is why the secret scan failure went unnoticed for four
commits (see 8.3). The hook now runs every CI check that is fast and offline,
in about five seconds, and **names the two it does not run** (`pip-audit`, ~16s
and networked; the package build, minutes) so passing is never read as "CI will
pass".*

*It also moved out of `.git/hooks` into `scripts/hooks/`, enabled by
`core.hooksPath`. A hook that exists only in one developer's `.git` directory
is not a project gate — it is a personal habit that a teammate does not have
and cannot review. This one is now diffable in a pull request like any other
control.*

*Each gate was verified by making it fail: a planted credential in a staged
file, a hand-edited fixture, an unstaged auto-fix, and an impossible eval
floor. The fixture check regenerates into a backup and restores on every exit
path, verified by checksum — a hook must not leave the tree different from how
it found it.*

*9.6 runs six jobs on every pull request and every push to the default branch —
linting and tests, contract and grounding validation with a fixture drift
check, security scanning, both evaluation floors, and the deployment archive
build — behind a single aggregate job so branch protection needs configuring
once. No credentials are used anywhere, which is a consequence of the protocol
boundaries rather than a limitation.*

*9.7 is partly served by a pull request template that prompts for the right
checks, but nothing enforces reviewer assignment: there is no code-owners file.
A template asks; it does not require.*

*9.8 is what 8.3's history argued for. The `All checks` aggregate job is now a
required status check on the default branch, with administrators included and
force-pushes and deletions disabled. Verified by pushing directly and being
rejected — `GH006: Required status check "All checks" is expected` — not by
reading the settings page.*

*Two consequences to be deliberate about. **Direct pushes to the default branch
are no longer possible, including for the repository owner.** All changes go
through a pull request; that is the cost of the guarantee, and admin exemption
was declined precisely because the four red commits this fixes were the owner's
own. And the repository was made **public** to obtain the feature, which is
free only for public repositories on the current plan — a licensing constraint
driving a visibility decision, worth naming as such. The history was audited
for credentials and personal data before publication.*

*The aggregate job exists so this rule names one check. Adding a CI job later
does not mean editing the protection rule, which is the kind of maintenance
step that gets skipped.*

---

## Phase 10 — Deployment

- [x] **10.1** Build the deployment archive excluding unused transitive
  packages
- [ ] **10.2** Enable snapshot-based cold-start optimisation on a published
  alias — **[blocked: AWS]**
- [ ] **10.3** Deploy the endpoint with cross-origin support
  — **[blocked: AWS]**
- [ ] **10.4** Export every manually created resource's configuration and
  commit it — *Req 12.4* — **[blocked: AWS]**
- [ ] **10.5** Measure latency on the plan path and record percentiles
  — **[blocked: AWS]**
- [ ] **10.6** Convert to version-controlled infrastructure definitions
  — *Req 12.4* — **[blocked: AWS]**
- [ ] **10.7** Adopt existing resources rather than recreating them
  — **[blocked: AWS]**

*10.5 produces the evidence for any decision about the timeout constraint. That
decision should follow measurement, not precede it. The instrumentation it
needs is now in place (6.7): the plan path emits a subsegment per model call,
including each repair attempt separately, and `ModelLatency` is dimensioned by
model and task. What is still missing is a deployment to measure — the numbers
themselves, not the means of collecting them.*

---

## Phase 11 — Deferred

Specified so they are tracked rather than forgotten. None are required for the
initial delivery.

- [ ] **11.2** Recipe catalogue constraining plan composition — *Req 2.9*
- [ ] **11.3** Streaming transport — *Req 7.9*
- [ ] **11.4** Live price acquisition — *Req 8.8*, gated on the conditions in
  `ACQUISITION-RISK.md` §8
- [ ] **11.5** Conversation memory across turns
- [ ] **11.6** Retailer basket hand-off
- [ ] **11.7** Per-product allergen tagging in fixtures and stored records,
  extending `SUPPORTED_EXCLUSIONS` to cover gluten-free, nut-free and similar
  terms that the current category-based mapping cannot honour reliably —
  *Req 5.6 widened*

*11.1 (multi-item price queries) was implemented and has moved to **2.11**. The
number is retired rather than reused, so a reference to 11.1 elsewhere still
resolves to the right piece of work.*

---

## Critical path

What is blocked, and by what:

```
BLOCKED ON THE AWS ACCOUNT
  7.1, 7.2, 7.4 product store created  -> unblocks 2.9
  7.10 idempotency table created       -> unblocks 6.8
  2.9  stored price repository         -> unblocks 10.3
  8.10 live content safety verified    -> required before any public exposure
  3.9  cache utilisation verified      -> confirms a latency mitigation
  5.7  candidate models scored         -> required before production traffic
  10.5 latency measured                -> informs 11.3

BLOCKED ON ANOTHER TEAM
  1.6  contract circulated             -> unblocks the frontend
```

Secret scanning (8.3, 8.13) is closed: the gate was verified failing on a
planted credential and passing on run 31308163941, the first green run on the
default branch in four commits.

Everything else is unblocked and can proceed today. In rough order of value:

```
1.6  circulate the contract      the only item blocking another team
2.10 shared repository suite     the acceptance criteria 2.9 will be judged by
5.9  red-team harness            writable offline; makes 8.10 one command
7.5  per-retailer acquisition    unblocked by 7.9; fixtures, no live traffic
9.7  code owners file            makes 9.7's intent enforceable
```

6.7 is done. It was on this list as "no cloud dependency", and that held: the
logger, tracer and metrics are all asserted offline against the real
Powertools objects, so the first deployment inherits verified instrumentation
rather than instrumentation that has never run.

7.9 is done (`ACQUISITION-RISK.md`). It unblocked 7.5 and converted 11.4's gate
from an open question into a named condition list. Three of its six primary
sources could not be retrieved by automated fetch and are recorded as unknown
rather than absent — a human must complete that table before 11.4 proceeds.

Task 1.6 still blocks another team and should be completed first regardless of
other sequencing.

The shape worth noticing: most of what remains outside the AWS-blocked column
is verification rather than construction. 2.10 and 5.9 both exist to make a
claim checkable that is currently only asserted — that the stored repository
behaves like the fixture one, and that the content safety policy does what its
configuration says. Writing both before the account lands turns the first day
of cloud access into running tests rather than writing them.
