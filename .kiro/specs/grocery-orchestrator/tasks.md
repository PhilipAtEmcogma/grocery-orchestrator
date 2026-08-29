# Tasks — Smart Grocery & Meal Budget Assistant

**Status:** Approved pilot roadmap plus archived reference-build ledger
**Traces to:** `requirements.md`, `design.md`

The approved Pilot Tasks below are the current execution order. The later
legacy sections preserve implementation history and old task ids used by
commits and documentation; their historical `[blocked: AWS]` labels do not
describe current account availability and must not override the Pilot Task
status.

`[x]` in the legacy ledger means the reference behavior existed and had the
stated evidence at the time. It does not imply that the stronger pilot
acceptance criteria are met.

Where a legacy task id such as `6.7` is referenced elsewhere, it refers to
the reference-implementation ledger retained below. New work uses the
**Pilot Task** ids in the approved roadmap.

---

## Approved production-pilot roadmap

Documentation alignment is a hard execution gate. An unchecked task is planned,
proposed, or gated as labelled; it is not implemented.

- [x] **Pilot Task 1 — Align documentation and design before code changes.**
  Reconciled requirements, design, task status, contract/schema guidance,
  README/AGENTS, steering, and ADRs; cross-document review and offline gates
  passed on 2026-08-23.
- [x] **Pilot Task 2 — Correct citation construction and money-free rendering;
  partially strengthen grounding evidence.** Citations now use configured table,
  `store_key`, and normalized `product_key`; citation-before-use and basic source
  shape are checked; reasoning and prose labels contain no literal money; samples
  were regenerated. `assert_no_literal_money_in_response()` covers token,
  reasoning, and notice fields with three negative controls, and since follow-up
  (a) the meal plan's model-authored text as well. Offline gates passed
  on 2026-08-23.
  - [x] **Pilot Task 2 follow-up (a) — Close Req 3.7 for model-authored text.**
    Completed 2026-08-29. The prose-like field inventory found three unchecked
    MODEL-AUTHORED fields — `Meal.name`, `Ingredient.item`, `Ingredient.qty`,
    written as `DraftMeal.name` / `DraftIngredient.item` /
    `DraftIngredient.qty_display` and passed through `assemble_plan` untouched.
    A plan naming a meal `Budget Pasta — only $4.99 a head` with an ingredient
    `Butter (was 7.50, now 5.00)` cleared `assert_grounded`,
    `assert_arithmetic` and `assert_no_literal_money_in_response` together,
    shipping a fabricated "was" price. `find_literal_money_in_plan()` now feeds
    `validate_plan`, so a violation is a validation error routed through the
    bounded repair loop to `emit_plan_generation_failed` — never to
    `emit_budget_infeasible`, which would blame the shopper's budget.
    `run_turn()` calls the narrow `assert_no_model_authored_money()` as a
    boundary backstop; the wide whole-response assertion stays in `validate.py`
    because raising on prose would convert the prose node's degradation into a
    dead turn. `LITERAL_MONEY` is now defined once and imported.

    Two pre-existing defects were fixed on the way. `build_repair_prompt` was
    the ONLY repair prompt and answers only "you overspent, cut this much", so
    every non-budget rejection — invalid draft, unknown ref, broken arithmetic
    — was told "your plan came to $0 OVER the $X budget, cut at least $0 less".
    `build_defect_repair_prompt` now names the actual defect; both share
    `_constraints_block`, so the Req 5.3 restatement that 4.6 once omitted
    lives in one place. The budget prompt was proved byte-identical to its
    pre-refactor output. Separately, `samples/response_budget_infeasible.json`
    published an uncited "$41.20" the code never emits, and
    `FRONTEND-INTEGRATION.md` still showed money in prose and reasoning under a
    note calling it "scheduled for Pilot Task 2"; both were corrected, the
    frontend example by capture from the handler rather than by hand.

    13 tests added, including per-field negative controls, the clean-plan
    positive, and two end-to-end repair-routing tests. Those two exist because
    the routing branch was initially unpinned — inverting it left the whole
    suite green. Verified by mutation: inverting the branch, deleting the money
    check, and making the money error set `over_budget` fail 1, 3 and 3 tests
    respectively. `ErrorEvent.message` and `NoDataEvent.message` are excluded
    by design and the reasoning is recorded in `AGENTS.md`. Offline gates
    passed: 466 passed, 31 skipped; evals unchanged at 100% meal-plan
    invariants and 76.7% intent.
  - [x] **Pilot Task 2 follow-up (b) — Complete Req 3.5–3.6 enforcement.**
    Completed 2026-08-29. `assert_citations_match_retrieval()` compares every
    citation against the frozen `PriceRecord` the retrieval node kept for it:
    the ref must have been retrieved at all, table/pk/sk must identify that
    exact stored record, and all eleven published values must equal the
    retrieved ones. `run_turn()` calls it with `repo.table_name` and the state's
    `record_index`, which only `retrieve_prices` writes.

    **Shape was standing in for identity.** `assert_grounded()` reads the
    response alone, so it could only check that source keys LOOK like keys — a
    non-empty table, a `#` in the pk, a non-empty sk. A citation naming the
    right table, with a plausible partition key and a price nobody retrieved,
    passed it cleanly. The system's central claim therefore rested on no code
    path currently fabricating one, rather than on a check that would notice if
    one did.

    The retrieved context is immutable by type, not by convention:
    `PriceRecord` is a frozen slots dataclass. It reaches the check through a
    `RetrievedRecord` Protocol declared in `contract.py` rather than by
    importing `PriceRecord`, because `retrieval/base.py` imports `Store` from
    `contract` and the import would close a cycle. The Protocol's members are
    read-only properties — a Protocol with mutable attributes demands settable
    ones, which a frozen dataclass cannot satisfy.

    `record_index` construction moved from `zip(..., strict=False)` to
    `strict=True`: a length mismatch is a bug in that node, and truncating
    silently would surface downstream as "this citation was not retrieved",
    pointing at the wrong culprit.

    19 tests in the new `tests/test_grounding.py`, including a positive control,
    every altered field parametrised, and two end-to-end tests that tamper with
    a citation inside the graph to prove `run_turn` actually calls the check.
    `validate.py` now runs all four negative controls Req 3.6 names — unknown
    reference, incorrect source key, altered value, content before its citation
    — plus a positive control, in the CI job where a contract break is legible.

    Verified by mutation: dropping the `run_turn` call, disabling value
    comparison, and tolerating unknown refs fail 2, 14 and 2 tests respectively;
    the value-comparison mutation also fails `validate.py` with exit 1.

    Offline gates passed: 504 passed, 31 skipped; evals unchanged.
- [x] **Pilot Task 3 — Prove offline GuardrailBlocked propagation and add an
  experimental harness.** Intent, plan, and prose nodes preserve the specialized
  exception to one handler mapping; three node propagation tests and one handler
  mapping test pass. The scripted harness gives 7/7 must-allow structural evidence. Offline gates
  passed on 2026-08-23.
  - [x] **Pilot Task 3 follow-up (a) — Make the harness's controls
    trustworthy.** Completed 2026-08-29. All three named defects fixed and
    pinned by tests that fail when each is reintroduced.

    `--model` now resolves a spec through `RoutingPolicy.PINNED` and installs a
    pinned `BedrockModelClient` into the handler, which the per-case reset
    preserves. It previously set `USE_BEDROCK=1` and relabelled the report while
    the registry routed per task, so any earlier scorecard headed with a model
    name may have measured a different model.

    `OUT_OF_SCOPE` is no longer a block. Outcomes are now four-way — `blocked`,
    `allowed`, `refused_other`, `upstream` — and only a Guardrail intervention
    counts as a block. Folding a classifier's "out of scope" into `blocked`
    credited the policy with refusals it never made, on the thirteen prompts a
    classifier is most likely to wave away. `must_allow` deliberately still
    passes on anything that is not an intervention: a legitimate question
    answered `BUDGET_INFEASIBLE` was not refused on safety grounds, and scoring
    it as an over-block would report the planner's behaviour as the Guardrail's.
    Those cases are counted separately as `answered_cleanly` and reported.

    Exit codes are now 0 pass / 1 fail / 2 inconclusive, and a live must-block
    miss returns 1. Previously `main()` gated only on the allow rate, so a live
    run could print "FAIL: must_block rate 0%" and exit 0 — the one gate proving
    the Guardrail blocks anything could not fail a build.

    Two defects found while fixing those. The suite had NO tests at all, while
    the meal-plan harness had 19; it now has 16. And it had no pacing: 20 cases
    against a 10/min ceiling would fail the tail upstream, which on this suite
    reads as the Guardrail letting unsafe content through. Pacing moved to
    `evals/_pacing.py` and is shared by all three harnesses.

    Separately hardened `run_intent.py`, because it produces the Claude
    scorecard that Pilot Task 7 needs and was blind in a worse way:
    `classify_intent` DEGRADES to keyword matching rather than raising, so an
    unpaced run answers all 30 cases from the fallback and prints a plausible
    accuracy for a model that answered a third of them. It now records
    `intent_degraded` per case and returns exit 2 rather than a score.

    Offline gates passed: 485 passed, 31 skipped; scripted baselines unchanged
    at 7/7 must-allow, 76.7% intent, 100% meal-plan invariants.
  - [x] **Pilot Task 3 follow-up (b) — Record the live Guardrail result.**
    Completed 2026-08-29. **13/13 must-block and 9/9 must-allow, exit 0**,
    against guardrail `b1xezpqe04kx` **version 2**, Nova Lite, paced 9/min,
    account `097087133897`. Two clean reps against version 1 first (13/13, 7/7),
    then version 2 after the policy fix below. No upstream failures, no
    inconclusive runs. This is the qualifying live policy evidence Req 5.5 and
    legacy 5.9/8.10 required.

    **The run found a real over-block, and it was worse than it first looked.**
    Investigating an intent-eval failure showed `how much is truffle oil`
    returning `GUARDRAIL_BLOCKED` while `olive oil` passed. The cause was the
    `ForagingAndWildFood` topic, defined as "wild-gathered food including
    mushrooms, plants, shellfish, or roadkill" — an ingredient list the
    classifier keyed on regardless of context. `price of mushrooms` and
    `cheapest button mushrooms` were blocked too. A grocery price assistant that
    refuses to price mushrooms is broken rather than safe.

    Fixed by scoping the topic to the ACT of gathering wild food. Verified in
    both directions on DRAFT before publishing: every genuine foraging query
    still denies and the full suite passed 13/13, then version 2 was cut.
    `allow-008` and `allow-009` were added as regression guards.

    **Still open:** a bare `price of mushrooms` is refused. Three rounds of
    tuning moved qualified queries but not the unqualified noun. Recorded as an
    open defect rather than tuned further — loosening a safety topic by trial
    and error is the wrong direction to push from. Deliberately not an eval
    case: a permanently red gate is one people stop reading.

    `scripts/apply_guardrail.py` also had to be fixed to get here: it passed
    `tags` to `UpdateGuardrail`, which rejects it, so the update path had never
    once executed. The guardrail was created and never changed, and the first
    attempt to change it failed on a parameter unrelated to policy.
  - [x] **Pilot Task 3 follow-up (c) — Stop scoring a Guardrail block as a
    classification failure.** `run_intent.py` caught `GuardrailBlocked` under a
    bare `except Exception` and recorded it as a wrong answer. The same prompts
    are must-block cases in the red-team suite, where blocking them is what a
    passing score MEANS. Three such cases cost every model ten points and capped
    the suite at 27/30 = **90.0% exactly** — the routing floor, with no headroom
    for any model however good, which silently blocked Pilot Task 7. Now caught
    before the generic handler, excluded from the denominator as `known_gap`
    cases already were, and named in the report.
  - [ ] **Pilot Task 3 follow-up (d) — DEFERRED: unblock a bare mushroom
    query.** See (b). Needs either a different topic formulation or acceptance
    that the managed classifier cannot separate the retail and foraging senses
    of a bare ingredient noun. Reproduction and evidence in
    `docs/LIVE-EVAL-RUNBOOK.md` §8.5.
- [ ] **Pilot Task 4 — Correct request semantics and payable arithmetic.** Ask
  for missing required constraints and define verified consumption and
  full-pack payable totals.
- [ ] **Pilot Task 5 — Enforce location, store scope, and freshness.** Extend
  repository contracts and route stale-only data to an honest outcome.
- [ ] **Pilot Task 6 — Harden DynamoDB access and idempotency.** Hash the
  canonical validated request; owner-fence acquire/takeover/complete/release;
  add shared contract/race/pagination tests, all-table PITR evidence, and a
  queryable candidate pattern.
  - [x] **6a — Canonical fingerprint, owner fencing, pagination, PITR.**
    Completed 2026-08-29.

    **The fingerprint hashed raw HTTP bytes.** It is now taken over the
    validated request, so whitespace, JSON object-key order and
    omitted-versus-explicit-null cannot cause a false mismatch. Trailing zeros
    on money were an unlisted case found while writing the vectors: `30` and
    `30.00` are the same budget and produced different fingerprints, so a
    client sending the second got a 400 it is not allowed to retry, from the
    mechanism that exists to help it recover from a timeout.

    **`complete()` and `release()` had no owner fence.** An invocation that
    stalled past the in-progress timeout, watched another legitimately take
    over its claim, and then woke up could overwrite the newer claim with its
    older answer — served to the next retry as cached truth — or delete the
    newer marker, letting a third invocation start the same turn. Every claim
    now carries a token rotated on acquire AND on takeover, and both writes are
    conditional on it. Verified against the live table, not only in memory:
    acquire, duplicate, payload mismatch, takeover with rotation, both fenced
    writes refused, and the replay returning the newer answer.

    **`cheapest_for_product` could report `no_data` for a stocked product.** It
    issued one GSI query with `Limit=limit * 5` and ignored `LastEvaluatedKey`.
    DynamoDB applies `Limit` to items READ, before the application-side store
    filter, so when none of the first page was at the requested store it
    returned `[]` — which the graph reads as "I don't have price data for
    that". Invisible on fixtures at six records per product; live at real
    scale. Now follows pages until enough matches are held, bounded by
    `MAX_QUERY_PAGES` for latency.

    PITR enabled on `smart-grocery-products-dev` and `smart-grocery-recipes-dev`.
    Deliberately NOT on `grocery-idempotency-dev`: a 24-hour TTL cache holding
    no source of truth, where restoring stale claim tokens over live ones is
    worse than starting empty. Recorded in `DYNAMODB-SCHEMA.md`.

    31 tests added (14 -> 27 idempotency, 31 -> 35 repository). Verified by
    mutation: dropping the fence fails 3, reusing the token on takeover fails 3,
    reverting to raw-body hashing fails 1, and single-page querying fails 2.
    Offline gates passed: 531 passed, 31 skipped.
  - [ ] **6b — Queryable candidate pattern, DEFERRED with a forcing test.**
    `candidates_for_budget` still Scans the products table on every meal-plan
    turn. `DYNAMODB-SCHEMA.md` requires the replacement to be chosen from real
    access patterns and load evidence, and there is currently neither: 152 rows
    and no deployment. Choosing a GSI partition key now would be guessing from
    zero traffic, and a GSI is expensive to change once written.
    `test_the_scan_ceiling_is_asserted_rather_than_assumed` fails once the
    dataset passes `SCAN_CEILING_RECORDS`, so the decision is forced by data
    rather than forgotten.
- [ ] **Pilot Task 7 — Reconcile, qualify, and evaluate the model plane.** Align
  the adapter with `langchain-aws`, move routing toward SSM, disable unscored
  models, publish task scorecards, and preserve local evals as release gates.
  - [x] **7a — Publish scorecards and stop unscored models being routable.**
    Completed 2026-08-29. `enabled` meant "listed in the config", not "has
    evidence": every model carried `enabled: true` regardless of what it had
    been scored on. `claude-sonnet` was second preference for `generate_plan`
    while being documented as excluded on LATENCY (p50 11.8s / p90 19.9s against
    the production 20s client timeout, 9 of 98 plan calls over the ceiling), so
    a Nova Pro outage failed over to a model already known to be unfit. Broader
    than the preference list: `route()` falls through to `available(tier)`
    sorted by cost, and `claude-sonnet` declared BOTH tiers, so it was a live
    fallback candidate for every task in the graph.

    Scorecards are now data in `config/models.json` with source, date and
    guardrail version — an intent score measures the classifier AND the policy
    in front of it, and version 2 unblocked cases version 1 refused.
    `claude-sonnet` is disabled with the reason recorded.
    `ModelRegistry.unscored_routes()` walks every task against every model that
    could actually reach it and returns the pairs with no qualifying evidence;
    `unevidenced_models()` stops the unmeasured-task exemption becoming a hole.
    Four tests fail the build on either. Verified by mutation: re-enabling
    `claude-sonnet` fails three, dropping a scorecard fails one, and a rate
    below the floor fails one.

    `repair_plan` and `generate_prose` are recorded in
    `scorecards._unscored_tasks` with reasons rather than quietly exempted —
    nothing measures prose at all (legacy 5.6), and repair is only ever scored
    through the meal-plan invariants. Offline gates passed: 511 passed, 31
    skipped.
  - [x] **7a-ii — Measure the two tasks nothing measured.** Completed
    2026-08-29. Recording the scorecards forced the admission that
    `generate_prose` and `repair_plan` were routed to models nothing had scored
    for either. `evals/run_prose.py` (11 cases) scores whether a model can
    follow the prose protocol — the node degrades silently on any breach, so a
    model that cannot produces a product with no prose and no error to show for
    it. `evals/run_repair.py` (6 cases) scores the repair pass, budget and
    defect kinds separately, because the graph feeds it both and they need
    different prompts and different checks. Both gated in CI and the hook at
    0.90 against 100% scripted baselines.

    Live, guardrail v2: prose — Nova Lite 100%, Nova Pro 100%, Claude Haiku 4.5
    90.9%; all gated. Repair — all three at 83.3%, each failing a DIFFERENT
    case, which is variance on six cases (one failure is 16.7 points) rather
    than a weakness. Recorded in `scorecards._measured_not_gated` with that
    reasoning rather than gated on a threshold the suite cannot support.

    **The repair suite immediately found a live defect this session introduced.**
    `build_defect_repair_prompt` shipped phrased as a stack of imperatives
    ("Never write a price ... ANYWHERE", "Use ONLY citation refs"), and the
    Guardrail's PROMPT_ATTACK filter refused it outright — so every non-budget
    repair returned GUARDRAIL_BLOCKED to the user instead of a repaired plan.
    Offline tests could not see it: the scripted client has no guardrail. The
    prompt was rewritten into the budget prompt's descriptive register and
    verified allowed. `run_repair.py` now scores a blocked repair prompt as a
    FAILURE rather than excusing it, since a prompt built entirely from our own
    code should never read as an attack — that is the regression test.

    Also fixed in the harness itself before any number was recorded: `_citations`
    passed `exclude_categories=[]` and `budget_nzd=None`, so a vegetarian case
    handed the model meat and dairy from an unfiltered candidate list. Two
    models "failed" by exceeding the 12-ingredient cap, and that would have been
    recorded as a weakness in the models rather than in the helper.
  - [ ] **7b — Move the catalogue toward SSM**, align the adapter with
    `langchain-aws`, and evaluate cross-Region inference profiles only for a
    measured purpose.
  Evaluate Bedrock cross-Region inference profiles only for a measured purpose;
  stage Bedrock Model Evaluation as companion evidence with reproducible
  dataset/model/prompt provenance. Knowledge Bases are gated to cited recipe or
  catalogue knowledge with no price authority; Automated Reasoning is advisory
  only where supported.
- [ ] **Pilot Task 8 — Deliver local read-only MCP first.** Expose coarse local
  complete-application operations only; validate schemas and enforce operation,
  row, call, time, and rate allowlists. Prove direct-service parity and a disable
  path before any managed exposure.
  - [ ] **Pilot Task 8 proposed extension — AgentCore Gateway hybrid.** After
    local MCP proof and ADR 0002 mentor approval, expose the same operations via
    AgentCore Gateway with Identity, Policy, WAF/Cognito or approved workload
    identity, privacy-safe audit, quotas, cost evidence, and rollback drill.
    Gateway must never bypass the deterministic Lambda graph.
- [ ] **Pilot Task 9 — Establish CDK and adopt existing data resources.** Build
  the TypeScript CDK app and stateful stack; import existing tables without
  replacement and record reviewed adoption evidence.
- [ ] **Pilot Task 10 — Define the deployable service plane.** Codify the Python
  3.13 zip Lambda, published SnapStart alias, REST API, Guardrail, strict CORS,
  throttling, usage plan, SSM configuration, log retention, and scoped IAM.
- [ ] **Pilot Task 11 — Deploy and verify the anonymous pilot safely.** Treat
  resource adoption and deployment as separate reviewed operations in account
  the deployment account (see `aws sts get-caller-identity`), region
  `ap-southeast-2`.
- [ ] **Pilot Task 12 — Add operational acceptance gates and artefact storage.**
  Build CloudWatch dashboards/alarms, X-Ray evidence, Budgets, quota review,
  latency/cost baselines, and alarm drills. Use encrypted versioned S3 with
  scoped prefixes, lifecycle, restore, and deletion tests for approved
  datasets, evaluation results, and review artefacts; use SNS for non-sensitive
  operator and approval notifications.
- [ ] **Pilot Task 13 — Build controlled ingestion and decoupled review
  triggers.** Use EventBridge and Step Functions Inline Map with fixture or
  recorded adapters, provenance, normalization, partial failure, retry, and
  dead-letter behavior. Where review decoupling is justified, add filtered
  DynamoDB Streams -> SQS/DLQ with retry/redrive/backlog evidence. No live
  retailer traffic.
- [ ] **Pilot Task 14 — Add the bounded data-quality reviewer.** After ADR 0002
  mentor approval, deploy it separately in AgentCore Runtime over capped
  sanitised ingestion snapshots with an isolated least-privilege identity,
  read-only allowlisted tools, row/call/token/time/cost/egress caps, cited
  schema-checked findings, deterministic post-validation, human approval, and
  teardown evidence. It receives no shopper PII and has no production write,
  publication, or shopper-path authority. AgentCore Evaluations may supplement
  local labelled anomaly tests with reproducible provenance.
- [ ] **Pilot Task 15 — Introduce the curated recipe catalogue.** Models select
  recipe ids and product citations; code owns scaling, safety, and totals.
  A Knowledge Base may be evaluated only for cited recipe/catalogue retrieval
  and never for authoritative prices.
- [ ] **Pilot Task 16 — Wire and release the complete pilot increment.** Run
  mandatory offline, live-adapter, infrastructure, security, evaluation, load,
  privacy, recovery, and cost gates. Local MCP has its own planned demonstration
  gate. For each optional managed service actually approved and adopted, also
  run parity, privacy, cost, and rollback/removal gates; an unapproved optional
  service is not a release prerequisite and is not marked complete.

### Pilot acceptance targets

- 100% pass for grounding, literal-money rejection, arithmetic, dietary
  fail-closed, Guardrail propagation, and their negative controls. Task 2's
  exact retrieved-record/value controls closed 2026-08-29; Task 3's qualifying
  live Guardrail RESULT remains open (its controls closed the same day).
- Every enabled model has a published scorecard; every active route scores at
  least 90% on its applicable golden set.
- Measurement targets: p95 price checks under 5 seconds, p95 meal plans under
  20 seconds, and p99 meal plans under the approximately 25-second escalation
  trigger.
- At least 99% successful service responses during the pilot, excluding
  intentional contract-valid refusals; unhandled 5xx below 1%.
- No message, raw location, dietary value, credential, or model prompt in logs,
  traces, managed evaluations, review snapshots, or notifications.
- Every published price has an exact source key, store location, and capture
  date; final validation independently compares it with immutable retrieved
  context before release.
- Record cost per successful task; alert at 50%, 80%, and 100% of the approved
  budget and review unit-cost regressions over 20%.

### Staged AWS-learning roadmap

The learning objective is broad AWS experience with product discipline. Each
service must state purpose, scope, evidence, security/cost controls, owner, and
rollback/removal criterion, and cannot weaken the deterministic invariants.

1. **Implemented:** deterministic Lambda/LangGraph reference core.
2. **Planned first:** local read-only MCP over coarse complete-app operations.
3. **Proposed, mentor approval required:** AgentCore Gateway + Identity + Policy
   over the same tools, never around the graph.
4. **Proposed, mentor approval required:** isolated AgentCore Runtime reviewer.
5. **Proposed companions:** Bedrock Model Evaluation and AgentCore Evaluations,
   alongside local tests/evals rather than replacing them.
6. **Gated companions:** inference profiles, recipe/catalogue Knowledge Bases,
   advisory Automated Reasoning, S3 artefacts, Streams/SQS/DLQ triggers, SNS,
   WAF/Cognito, and CloudWatch/X-Ray/Budgets under Tasks 7–16.

After measured pilot stability: Cognito-owned preferences, WebSocket delivery,
remote MCP, gated live acquisition, and separate environments. AgentCore Memory
requires Cognito, consent, TTL, deletion/export, privacy review, and no price
authority. Moving the shopper meal path to AgentCore Runtime remains a distinct
p99 contingency after mitigations and separate mentor approval.

---

## Legacy reference-implementation ledger

The sections below preserve the original build history and task ids. They are
not the execution order for the approved production-pilot work.

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
- [x] **2.9** Implement the stored price repository against the same protocol
  — *Req 8.1, 8.2*
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
- [x] **3.9** Verify cache utilisation against accessible live endpoints
  — **[pending model qualification evidence]**
- [x] **3.10** Implement the managed-inference adapter behind the model
  protocol

*3.10 was built but never specified. The adapter is verified against live
Bedrock endpoints (Nova Lite and Nova Pro in ap-southeast-2). Current evidence:
Nova Lite 92.9%, Nova Pro 100% and Claude Haiku 4.5 96.4% on intent, measured
2026-08-29 against guardrail version 2 with GuardrailBlocked excluded from the
denominator (the older 83.3% Nova Lite figure counted three Guardrail refusals
as wrong answers); on meal-plan invariants, paced to
the account's request quota, Nova Pro 100% and Claude Haiku 4.5 100% over three
clean reps each (the earlier "Nova Pro 64%" was measured by a scorer since
found wrong in three ways — see AGENTS.md). Claude access was unblocked on
2026-08-28. The
current catalogue is Nova-first, but no production route is qualified until
Pilot Task 7 publishes task-specific scorecards, disables unscored models, and
each enabled active route reaches 90%. Claude routing, if later selected, is an
evidence-based configuration decision rather than an automatic restoration.*

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

*4.7 delivered a prompt, placeholder-only model output, graph node, renderer,
rejection check for model-supplied money, and optional-prose degradation. It
historically expanded placeholders into figures. Pilot Task 2 changed rendering
to money-free labels, removed literal prices from comparison reasoning,
regenerated samples, and added response-field checks. Whole-response runtime
enforcement from `run_turn()` remains the explicit Task 2 follow-up.*

---

## Phase 5 — Evaluation

- [x] **5.1** Golden set for classification and extraction — *Req 10.1*
- [x] **5.2** Runner scoring accuracy, latency, and cost per model
- [x] **5.3** Report known limitations separately from failures — *Req 10.3*
- [x] **5.4** Meal plan cases with invariants and reported metrics — *Req 10.2*
- [x] **5.5** Budget floor check, not only the ceiling
- [ ] **5.6** Subjective quality scoring for variety and appeal
  — *deliberately still open; `evals/run_prose.py` (2026-08-29) scores RULE
  COMPLIANCE only. An LLM judge would put a non-deterministic scorer inside a
  suite whose value is being deterministic.*
- [x] **5.7** Score every candidate model and publish the comparison
  — *Req 9.5* — **[current blocker: model-specific access and missing scorecards]**
- [x] **5.8** Red-team case set for content safety, covering both content that
  must be blocked and content that must be allowed
- [ ] **5.9** Harness that runs the red-team set against the numbered live
  Guardrail and reports each case's outcome

*3.9 and 5.7 were answered on 2026-08-29 and both results are in
`docs/LIVE-EVAL-RUNBOOK.md` §8. Intent scorecards against guardrail version 2:
Nova Pro 100.0% (28/28), Claude Haiku 4.5 96.4% (27/28), Nova Lite 92.9%
(26/28) — all three clear the 90% floor, including Nova Lite, the model
currently routed for `classify_intent`. Cache utilisation is zero on every path
and that is CORRECT: `cachePoint` attaches to the system prompt (~500 tokens)
against Claude's 4096-token minimum, while the large repeated content (the
products table) sits in the user prompt. The capability is implemented and
honestly gated, and cannot fire as the prompts are arranged. Neither Nova model
declares caching at all.*

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

*5.9's controls are now trustworthy; its live result is not yet taken. Pilot
Task 3 added the harness and proved 7/7 scripted must-allow structure plus
node-level exception propagation. Its follow-up (a) then repaired the three
defects that made a live run unquotable — `--model` not pinning, `OUT_OF_SCOPE`
counting as a block, and a must-block miss exiting 0 — added the suite's first
16 tests, and added pacing. Follow-up (b) owns the live 13/13 plus 7/7 result
and is batched into the credentialed session described in
`docs/LIVE-EVAL-RUNBOOK.md`.*

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
- [x] **6.8** Implement the stored idempotency store against the same protocol
  — *Req 12.3*

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

- [x] **7.1** Create the products table with the price-ordered index
  — *Req 8.2*
- [ ] **7.2** Create the meals table with expiry — *Req 11.6*
  — **[superseded by Pilot Tasks 9 and 15; create CDK-first]**
- [x] **7.3** Enable products-table recovery and verify encryption
  — *Req 11.7*; all-table PITR evidence remains in Pilot Tasks 6/9/16
- [x] **7.4** Load the seed dataset
- [ ] **7.5** Implement per-retailer acquisition with isolated failure
  — *Req 8.5*
- [ ] **7.6** Orchestrate acquisition with parallel per-retailer error handling
- [ ] **7.7** Implement name normalisation in ingestion — *Req 8.3*
- [ ] **7.8** Schedule the refresh — *Req 8.4*
- [x] **7.9** Assess terms-of-service risk before live acquisition — *Req 8.8*
  — `ACQUISITION-RISK.md`
- [x] **7.10** Create the idempotency table with expiry — *Req 12.3*

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
  — **[superseded by Pilot Tasks 9–10]**
- [ ] **8.6** Managed secret storage — *Req 11.2* — **[superseded by Pilot Task 10]**
- [ ] **8.7** Gateway throttling and usage plans — *Req 11.4*
  — **[superseded by Pilot Task 10]**
- [ ] **8.8** Authentication on the endpoint — **[later roadmap after anonymous pilot]**
- [x] **8.9** Define the content safety policy as version-controlled data and
  validate it offline — *Req 5.5*
- [ ] **8.10** Verify the content safety policy against the numbered live
  Guardrail using the red-team set from 5.8 — *Req 5.5*; harness construction
  and propagation moved to Pilot Task 3, qualifying live policy evidence remains
  in its unchecked follow-up
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

*8.9 and 8.10 were split to distinguish policy-as-code from behavioral
evidence. The offline policy is reviewable and validated. Guardrail
`b1xezpqe04kx` version `2` has qualifying live evidence (13/13 + 9/9). Pilot
Task 3 then proved provider-neutral intervention propagation through intent,
plan, and prose and added the experimental harness. The live policy result is
still open because model selection, outcome classification, and failing-exit
semantics are not yet qualifying controls.*

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

*Legacy 8.10 remains unchecked for qualifying live policy evidence. Pilot Task
3 completed harness construction and propagation evidence only; its explicit
follow-up owns the live 13/13 must-block plus 7/7 must-allow gate.*

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
  alias — **[superseded by Pilot Task 10]**
- [ ] **10.3** Deploy the endpoint with cross-origin support
  — **[superseded by Pilot Tasks 10–11; strict CORS required]**
- [ ] **10.4** Regenerate live configuration locally and record sanitized
  adoption assertions — *Req 12.4*; superseded by Pilot Task 9
- [ ] **10.5** Measure latency on the plan path and record percentiles
  — **[requires Pilot Task 11 deployment; measured in Pilot Task 12]**
- [ ] **10.6** Convert to version-controlled infrastructure definitions
  — *Req 12.4* — **[superseded by Pilot Tasks 9–10]**
- [ ] **10.7** Adopt existing resources rather than recreating them
  — **[superseded by Pilot Task 9]**

*10.5 produces the evidence for any decision about the timeout constraint. That
decision should follow measurement, not precede it. The instrumentation it
needs is now in place (6.7): the plan path emits a subsegment per model call,
including each repair attempt separately, and `ModelLatency` is dimensioned by
model and task. What is still missing is a deployment to measure — the numbers
themselves, not the means of collecting them.*

---

## Phase 11 — Legacy deferred ledger

This dated section preserves the pre-pilot backlog. Current priority and release
scope come only from Pilot Tasks 1–16 above. In particular, legacy 11.2 is
superseded by required Pilot Task 15; the remaining entries are later or gated.

- [ ] **11.2** Recipe catalogue constraining plan composition — *Req 2.9* —
  **[superseded by required Pilot Task 15]**
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
UNBLOCKED 2026-08-28 (Anthropic use case form submitted)
  Claude intent scorecard            -> still required before any Claude route
                                        can enable; meal-plan is done (100%)

AVAILABLE NOW; EVIDENCE STILL MISSING
  3.9  cache utilisation             -> read CacheReadTokens off a live run
  5.7  task-specific scorecards      -> required for every enabled route; the
                                        Claude intent card is the missing one
  5.9/8.10 live Guardrail result     -> controls repaired 2026-08-29; run the
                                        20 cases per docs/LIVE-EVAL-RUNBOOK.md
  (Pilot 2 exact record/value proof  -> CLOSED 2026-08-29)

COMPLETED LIVE BASE RESOURCES
  7.1  products table created          ✓
  7.3  products PITR + encryption      ✓ (all-table PITR remains Pilot 6/9/16)
  7.4  seed data loaded                ✓
  7.10 idempotency table created       ✓ (owner fencing/PITR still open)
  2.9  stored price repository         ✓ (31 contract tests passing)
  6.8  stored idempotency store        ✓ (five current outcomes only)

REQUIRES PLANNED CDK/SERVICE WORK, NOT A MISSING ACCOUNT
  7.2  meals table                    -> Pilot Task 15, created CDK-first
  8.5  per-function IAM roles         -> Pilot Tasks 9–10
  10.5 latency measured               -> service deployment then Pilot Task 12

BLOCKED ON ANOTHER TEAM
  1.6  contract circulated             -> unblocks the frontend
```

Secret scanning (8.3, 8.13) is closed: the gate was verified failing on a
planted credential and passing on run 31308163941, the first green run on the
default branch in four commits.

The legacy ledger has no current execution-order authority. Immediate work is
defined by the Pilot Tasks: correctness and verification Tasks 2–8, substantial
CDK/service/ingestion/recipe construction in Tasks 9–15, then integrated release
gates in Task 16. Historical ids above remain only so old commits and notes can
be interpreted.
