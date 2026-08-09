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
- [ ] **2.10** Build one test suite, parameterised over every implementation of
  the price store protocol, and run it against both
- [x] **2.11** Resolve and compare every item in a multi-item request, naming
  both the items that could not be resolved and the items that exceeded the
  per-turn cap — *Req 1.7*

*2.9 is the only orchestrator task blocked on cloud access. Everything above it
was built and tested without it.*

*2.10 is the acceptance criteria for 2.9 and does not depend on it. `design.md`
§7 asserts that the stored implementation must satisfy the same tests as the
fixture one; today that is an assertion, not a fact, because every test
constructs the in-memory repository directly. The suite should be written
against the protocol now and applied to the stored implementation the moment it
exists, so "it passes" means the same thing for both.*

*2.11 was **moved out of Deferred** — it is implemented and tested. It was
specified as unbounded ("a comparison for each item") and implemented with a
cap of five comparisons per turn, which is a latency decision against the
gateway's 29-second ceiling. Items past the cap were originally dropped in
silence; they are now named in a notice, because the requirement's second
clause applies to them as much as to items that failed to resolve. Extraction
is deliberately bounded higher than the comparison cap so the overflow is
knowable at all — see the note on `MAX_EXTRACTED_ITEMS`.*

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
- [ ] **6.7** Structured logging and metrics — *Req 12.1, 12.2*
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
- [ ] **7.9** Assess terms-of-service risk before live acquisition — *Req 8.8*
- [ ] **7.10** Create the idempotency table with expiry — *Req 12.3*
  — **[blocked: AWS]**

*7.9 gates 7.5. Legal assessment precedes implementation, not the reverse. It
needs no cloud access and is not started.*

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

- [x] **9.1** Pre-commit gate running formatting, linting, and tests
- [x] **9.2** Agent hooks for save-time verification
- [x] **9.3** Blocking hook on contract changes
- [x] **9.4** Blocking hook verifying grounding invariants
- [x] **9.5** Hook requiring evaluation after prompt changes — *Req 10.5*
- [x] **9.6** Continuous integration on pull requests
- [ ] **9.7** Require review on contract and orchestrator changes

*9.6 runs six jobs on every pull request and every push to the default branch —
linting and tests, contract and grounding validation with a fixture drift
check, security scanning, both evaluation floors, and the deployment archive
build — behind a single aggregate job so branch protection needs configuring
once. No credentials are used anywhere, which is a consequence of the protocol
boundaries rather than a limitation.*

*9.7 is partly served by a pull request template that prompts for the right
checks, but nothing enforces reviewer assignment: there is no code-owners file.
A template asks; it does not require.*

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
decision should follow measurement, not precede it.*

---

## Phase 11 — Deferred

Specified so they are tracked rather than forgotten. None are required for the
initial delivery.

- [ ] **11.2** Recipe catalogue constraining plan composition — *Req 2.9*
- [ ] **11.3** Streaming transport — *Req 7.9*
- [ ] **11.4** Live price acquisition — *Req 8.8*, gated on 7.9
- [ ] **11.5** Conversation memory across turns
- [ ] **11.6** Retailer basket hand-off

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
6.7  structured logging          Req 12.1-12.2, no cloud dependency
7.9  terms-of-service assessment gates 7.5, needs no code
9.7  code owners file            makes 9.7's intent enforceable
```

Task 1.6 still blocks another team and should be completed first regardless of
other sequencing.

The shape worth noticing: most of what remains outside the AWS-blocked column
is verification rather than construction. 2.10 and 5.9 both exist to make a
claim checkable that is currently only asserted — that the stored repository
behaves like the fixture one, and that the content safety policy does what its
configuration says. Writing both before the account lands turns the first day
of cloud access into running tests rather than writing them.
