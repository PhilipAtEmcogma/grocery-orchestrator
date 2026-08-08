# Tasks — Smart Grocery & Meal Budget Assistant

**Status:** Draft for team review
**Traces to:** `requirements.md`, `design.md`

Status reflects the reference implementation built before the Kiro rebuild.
`[x]` items exist and are tested; they are the evidence the specification is
achievable, and their tests are the acceptance criteria for the rebuild.

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

*Naming in 1.5 is inconsistent on purpose. A tidy dataset hides the
normalisation problem until real data arrives.*

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
  — *Req 8.1, 8.2*

*2.9 is the only orchestrator task blocked on cloud access. Everything above it
was built and tested without it.*

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
- [ ] **4.7** Prose generation prompt for plan explanation

*4.6 was originally implemented restating only the budget. Evaluation revealed
that a plan for a user with a stated allergy was being regenerated with no
knowledge of the allergy. Unit tests did not catch it; only end-to-end
evaluation against realistic constraint combinations did.*

---

## Phase 5 — Evaluation

- [x] **5.1** Golden set for classification and extraction — *Req 10.1*
- [x] **5.2** Runner scoring accuracy, latency, and cost per model
- [x] **5.3** Report known limitations separately from failures — *Req 10.3*
- [x] **5.4** Meal plan cases with invariants and reported metrics — *Req 10.2*
- [x] **5.5** Budget floor check, not only the ceiling
- [ ] **5.6** Subjective quality scoring for variety and appeal
- [ ] **5.7** Score every candidate model and publish the comparison
  — *Req 9.5*

*5.5 exists because the ceiling check is one-sided: a plan spending a fraction
of the budget passes it and is probably under-feeding the household.*

---

## Phase 6 — Service layer

- [x] **6.1** Request handler returning a contract-valid response on every
  path — *Req 4.6*
- [x] **6.2** Map every failure class to a defined error code
- [x] **6.3** Exclude internal detail from user-facing messages — *Req 4.7*
- [x] **6.4** Resolve dependencies inside the error boundary
- [x] **6.5** Local development server for frontend integration
- [ ] **6.6** Idempotent handling of repeated request identifiers — *Req 12.3*
- [ ] **6.7** Structured logging and metrics — *Req 12.1, 12.2*

*6.4: dependency construction was originally outside the error boundary, so a
misconfigured store returned an unparseable failure — the exact outcome the
handler exists to prevent.*

---

## Phase 7 — Data and ingestion

- [ ] **7.1** Create the products table with the price-ordered index
  — *Req 8.2*
- [ ] **7.2** Create the meals table with expiry — *Req 11.6*
- [ ] **7.3** Enable recovery and verify encryption — *Req 11.7*
- [ ] **7.4** Load the seed dataset
- [ ] **7.5** Implement per-retailer acquisition with isolated failure
  — *Req 8.5*
- [ ] **7.6** Orchestrate acquisition with parallel per-retailer error handling
- [ ] **7.7** Implement name normalisation in ingestion — *Req 8.3*
- [ ] **7.8** Schedule the refresh — *Req 8.4*
- [ ] **7.9** Assess terms-of-service risk before live acquisition — *Req 8.8*

*7.9 gates 7.5. Legal assessment precedes implementation, not the reverse.*

---

## Phase 8 — Security

- [x] **8.1** Static security analysis in the linter
- [x] **8.2** Dependency vulnerability scanning
- [x] **8.3** Secret scanning
- [x] **8.4** Exclude personal data from logs — *Req 11.5*
- [ ] **8.5** Per-function roles scoped to named resources — *Req 11.1*
- [ ] **8.6** Managed secret storage — *Req 11.2*
- [ ] **8.7** Gateway throttling and usage plans — *Req 11.4*
- [ ] **8.8** Authentication on the endpoint
- [ ] **8.9** Content safety filter on every generation call — *Req 5.5*

*8.9 is the highest-priority remaining security item. It is not enabled by
default, is not implied by using the model service, and addresses a failure
mode with a documented precedent in this market.*

---

## Phase 9 — Automation

- [x] **9.1** Pre-commit gate running formatting, linting, and tests
- [x] **9.2** Agent hooks for save-time verification
- [x] **9.3** Blocking hook on contract changes
- [x] **9.4** Blocking hook verifying grounding invariants
- [x] **9.5** Hook requiring evaluation after prompt changes — *Req 10.5*
- [ ] **9.6** Continuous integration on pull requests
- [ ] **9.7** Require review on contract and orchestrator changes

---

## Phase 10 — Deployment

- [x] **10.1** Build the deployment archive excluding unused transitive
  packages
- [ ] **10.2** Enable snapshot-based cold-start optimisation on a published
  alias
- [ ] **10.3** Deploy the endpoint with cross-origin support
- [ ] **10.4** Export every manually created resource's configuration and
  commit it — *Req 12.4*
- [ ] **10.5** Measure latency on the plan path and record percentiles
- [ ] **10.6** Convert to version-controlled infrastructure definitions
  — *Req 12.4*
- [ ] **10.7** Adopt existing resources rather than recreating them

*10.5 produces the evidence for any decision about the timeout constraint. That
decision should follow measurement, not precede it.*

---

## Phase 11 — Deferred

Specified so they are tracked rather than forgotten. None are required for the
initial delivery.

- [ ] **11.1** Multi-item price queries — *Req 1.7*
- [ ] **11.2** Recipe catalogue constraining plan composition — *Req 2.9*
- [ ] **11.3** Streaming transport — *Req 7.9*
- [ ] **11.4** Live price acquisition — *Req 8.8*, gated on 7.9
- [ ] **11.5** Conversation memory across turns
- [ ] **11.6** Retailer basket hand-off

---

## Critical path

```
1.6 contract circulated        -> unblocks frontend
7.1, 7.2, 7.4 store created    -> unblocks 2.9
2.9 stored repository          -> unblocks 10.3
8.9 content safety             -> required before any public exposure
10.5 latency measured          -> informs 11.3
```

Task 1.6 is the only item that blocks another team and should be completed
first regardless of other sequencing.
