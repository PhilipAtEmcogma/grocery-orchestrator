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
  - [x] **Pilot Task 1 follow-up (a) — Answer the external audit of
    2026-08-30.** Completed 2026-08-31. An outside technical and product audit
    was taken at commit `5ace934`; `docs/AUDIT-RESPONSE-2026-08-30.md` replies
    to it finding by finding, with every claim re-checked against the code
    rather than accepted.

    **Four merges landed between the audited commit and the reply**, closing
    three headline findings before they were published: the CDK "stubs plus
    TODO comments" (`7eab93a` deployed two stacks), the recipe blocker as a
    head-term problem (`0146263` curated 29 recipes), and GSI2 missing from
    `stateful-stack.ts`. Recorded because it is the ordinary condition of a
    review — a finding needs a commit attached, not a date.

    **D3, and the tripwire behind it.** The audit found that
    `scripts/check_recipe_coverage.py` resolved through the 26-product fixture
    file while this ledger, `src/recipes/base.py` and the README all described
    the real catalogue. It did not find that the forcing test guarding the 15b
    decision had the same defect: it watched the fixture catalogue, which
    cannot grow, so its trigger was unreachable. Fixed by making the instrument
    name its inputs — `src/recipes/catalogue.py`, a script that prints
    catalogue and recipe-set identity every run and refuses `--fail-under`
    from the fixtures, and a forcing test pointed at `datasets/` that skips
    rather than falling back. The 15b conclusion survived, which was luck and
    is now a control.

    **The streaming rejection's stated reason was obsolete; the conclusion was
    not.** `design.md` §8 rejected streaming as a gateway bypass costing
    throttling, usage plans and authentication. API Gateway REST added response
    streaming in Nov 2025 and that trade is gone. But the Python managed
    runtime does not support response streaming and SnapStart supports managed
    runtimes only — so streaming costs SnapStart, which is the trade `AGENTS.md`
    already refuses for containers, and meal-plan p95 is 12.2s against a 29s
    ceiling that is therefore not binding. §8 and §9 now record the runtime as
    the blocker. Req 7.9 stays a GAP. **Every latency-derived decision the
    audit proposed re-opening — the Sonnet exclusion, `MAX_ITEMS_PER_TURN`, the
    repair bound, the AgentCore p99 contingency — waits on a load run, because
    p99 is undefined at n=3.**

    Also fixed: `dynamo.py`'s module header said "scans the base table" two
    commits after GSI2 replaced it (D5); `FRONTEND-INTEGRATION.md`'s event
    tables still showed money in prose the orchestrator stopped emitting on
    2026-08-29, re-derived from a real turn (D7); `build_graph()` compiled per
    request (17.16ms → 2.81ms per offline turn, memoised on the dependency
    pair, keyed on identity so two fixture catalogues can never share a graph);
    and a set of stale counts and statuses across README, AGENTS and this file.

    Declined with reasons: the second retrieval query on the empty path, the
    MCP protocol bump, and D9's cost figures (the audit compares Bedrock config
    against Anthropic first-party list prices, which are different price
    lists). **D1 — the provenance of the 2,759 served rows — remains open and
    is not an engineering task**: the tripwire in `ingestion/sources.py`
    protects the ingestion Lambda, not the serving table.

    Account-side recommendations are a runbook in §5 of the response rather
    than actions: the API key on usage plan `v4yd7d`, arming Req 12.5, the
    200-turn load run, and the restore drill.

    Offline gates passed: 811 passed, 31 skipped; evals unchanged at intent
    76.7%, meal plan 100%, prose 100%, repair 100%; 19/19 demos, demo 11 output
    byte-identical.
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
  - [x] **4a — Ask for a missing constraint instead of guessing it.** Completed
    2026-08-29. `classify_intent` wrote `constraints["household_size"] =
    household if household is not None else 1`, and the same for `days`. That
    contradicts Req 6.3 — reject inference of unstated constraints — and worse,
    it destroyed the only evidence the user had not said. A plan for one person
    over one day is a real answer to a question nobody asked, and downstream it
    is indistinguishable from a plan the shopper requested.

    A new additive `clarification` event carries `missing`, naming `ClientHints`
    fields exactly so a frontend can raise the control that collects the value
    rather than parsing English. Deliberately NOT an `ErrorEvent`: nothing
    failed, and `retryable` cannot express "retry with more information" — a
    client reading `retryable: true` resends the identical request and loops.
    Emitted before retrieval, like the dietary refusal, so no model call is
    spent on a plan we have already decided we cannot build. An unsupported
    dietary exclusion still takes precedence, because a restriction we cannot
    honour is the more important thing to report.

    **The teammate's dataset made the gap concrete.** `datasets/DATA_SCHEMA.md`
    Scenario 5 — "Plan a quick dinner for 2 people that is completely
    dairy-free" — states no budget and returned `PLAN_GENERATION_FAILED`, "I
    couldn't put together a plan I trust this time", blaming us for a request
    that was merely incomplete. It now asks.

    Two pre-existing extraction defects fixed on the way, both of which would
    have made clarification fire on facts the user plainly stated. "We are 3
    university flatmates" extracted no household at all, because an adjective
    sat between the number and the noun. And "dinners for 5 days on $90"
    extracted a household of FIVE from the phrase "for 5 days" — inventing a
    constraint from words that mean something else, the same Req 6.3 violation
    from the other direction. Extraction also now reads a single meal as a
    stated duration: "tonight" is one day, so Scenarios 2 and 4 still plan in a
    single turn.

    14 tests added; 531 -> 545 passing. All five eval baselines unchanged.
    `samples/response_clarification.json` added and validated in CI.
  - [x] **4b — Verified consumption and payable arithmetic.** Completed
    2026-08-29. `assert_arithmetic` checked that four sums agreed WITH EACH
    OTHER — meals sum to the total, baskets sum to the payable. Worth having,
    and structurally unable to catch the case design.md §14 named as unproven: a
    line cost wrong by construction propagates consistently through all four and
    passes every one, and nothing checked a basket total against anything.

    `assert_costed_from_citations()` re-derives every figure from the cited
    prices: line cost equals price times packs, pack counts aggregate per
    product ACROSS meals and round up ONCE, basket totals equal whole packs at
    shelf price, and a basket's citations really are at the store it names.

    **`Ingredient` gained `packs`**, because the plan could not previously
    verify its own arithmetic — `qty` is a display string, so nothing downstream
    could re-derive a line cost. Response-side only, so no client breaks:
    readers ignore unknown fields and nobody constructs an Ingredient to send us.

    9 tests, parametrised over the three real ways to get pack counting wrong —
    one pack per appearance, the fractional figure, and rounding per meal rather
    than once at the end. Each produces a plan whose four internal sums agree
    perfectly, and each is asserted to pass `assert_arithmetic` before failing
    the new check, so the tests demonstrate the gap rather than assert it.
    Verified by mutation: dropping the aggregation fails 4, dropping line-cost
    re-derivation fails 1.

    **`samples/response_meal_plan.json` was stale and is regenerated.** It
    published a successful plan for "a flat of 3 for under $30 this week" — a
    request the current code refuses, because the scripted planner spends $51.18
    against that budget. The drift predates this session. The sample request now
    carries a feasible $90, and the response is captured from the handler rather
    than hand-written; it also now illustrates the two-totals distinction
    directly, consumption $37.21 against payable $60.14.

    Offline gates passed: 573 passed, 31 skipped.
- [ ] **Pilot Task 5 — Enforce location, store scope, and freshness.** Extend
  repository contracts and route stale-only data to an honest outcome.
  - [x] **5a — Radius scope and freshness, enforced in the repository.**
    Completed 2026-08-29. Both were DECLARED and unimplemented: `Location`
    carried `lat`, `lon` and `radius_km`, `PriceRecord` carried `lat` and `lon`,
    and nothing read either — a shopper in Wellington was served Auckland
    prices. `STALE_DATA` existed in the error enum and appeared nowhere in
    `src/`.

    `PriceRepository` now takes `near` and `freshness` on both
    `cheapest_for_product` and `candidates_for_budget`, and all three
    implementations — in-memory, DynamoDB and the instrumented wrapper — apply
    them BEFORE the limit. That ordering is the requirement, not an
    optimisation: filtering afterwards returns nothing for a product whose five
    cheapest rows are all out of radius or out of date, and the graph reads
    nothing as `no_data`, telling a shopper we have no price for something
    stocked fresh down the road. It is the same truncation defect Task 6 fixed
    for the store filter, pushed down the same seam so it cannot come back.

    Stale-only data returns `STALE_DATA` naming the capture date, retryable
    because ingestion resolves it. Distinguished from `no_data` by a second
    unfiltered query issued ONLY when the filtered one is empty: "everything I
    hold is out of date" and "I hold nothing" are different facts and only one
    is about the product. No location still means national results (Req 1.6),
    and a location never silently widens back.

    **The threshold is config** (`config/freshness.json`; 14 days when this
    task closed, raised to 45 on 2026-08-30 — see the Task 13 note) and is
    measured against an INJECTABLE reference date. That is not a testing
    convenience. Committed fixtures carry a fixed capture date, so under a wall
    clock they drift into staleness as calendar time passes: judged against
    today the meal-plan eval dropped to 18% and a demo failed, for reasons
    nothing to do with the code. `pin_to_fixture_snapshot()` derives the date
    from the fixture data itself — not a duplicated constant that can go stale —
    and is called explicitly by the eval harnesses, the demos and the dev
    server. Production sets nothing and gets the wall clock.

    17 tests added; 547 -> 564 passing. Verified by mutation: ignoring the
    location fails 3, presenting stale data fails 5, and filtering after the
    limit fails 1 — that last one only after the test was strengthened to use
    Devonport, the DEAREST butter, because the original used Mangere which
    happens to hold the national cheapest and so passed against a deliberately
    broken implementation.

    Two pieces of collateral honesty: the observability tests carried a
    Wellington location ~490km from every fixture store, which had been
    harmless only because the filter did not exist; and `Philip_demo/06` still
    used the pre-fencing `complete()` signature from Task 6, caught by
    `run_all.py`'s drift gate.
  - [x] **5b — Named regions.** Completed 2026-08-29 on the proposed default
    recorded in `CONTRACT-v1.md`, since the teammate's scenarios ask for "near
    Albany", "North Shore" or "West Auckland" in 4 of 5 cases and the frontend
    answer had not arrived.

    `Location.lat`/`lon` are now optional with a validator requiring EITHER
    coordinates OR a region — additive, because coordinates alone still
    validate, and a location expressing neither is refused rather than silently
    widening back to national.

    A region resolves to a SET OF STORE LOCATIONS, not a centre and a radius.
    Two reasons, and the second is binding. "North Shore" means the shops on the
    Shore, not everything within N km of a midpoint — a radius around Takapuna
    reaches across the harbour bridge. And the 3,000-record dataset carries NO
    lat or lon on any row, so a coordinate filter cannot run against it however
    well specified. `config/regions.json` holds the mapping as reviewable data;
    the repository gained a `locations` scope alongside `near`, since its
    existing `stores` filter is by CHAIN rather than location.

    Regions arrive two ways: structurally (`location.region`, the shape a
    dropdown produces) or spoken ("cheapest butter near Albany"), which is what
    the scenarios do. An unmappable region is REFUSED and names what we do
    cover, rather than being ignored — ignoring it would answer a question about
    Whangarei with Auckland prices and give no sign the location was dropped.

    **A latent defect surfaced immediately.** "cheapest butter near Albany"
    extracted the item as "butter albany", which resolves to nothing, so the
    turn returned `no_data` for a stocked product. The region is now stripped
    from the message before the classifier sees it, resolved separately from the
    original. Nobody had noticed because there was no way to ask for a region.

    22 tests; 575 -> 597 passing. Verified by mutation: ignoring an unmappable
    region fails 1, dropping region scope entirely fails 5.
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
  - [x] **6b — Queryable candidate pattern. CLOSED 2026-08-30.**
    `candidates_for_budget` queried the products table with a full `Scan` on
    every meal-plan turn. Deferred deliberately because `DYNAMODB-SCHEMA.md`
    requires the replacement be chosen from real access patterns and load
    evidence, and there was neither — 152 rows and no deployment. A GSI is
    expensive to change once written, so guessing a partition key from zero
    traffic was the wrong move.

    **The forcing test worked exactly as designed.** The data team's catalogue
    brought 2,939 rows past the 1,000-row ceiling, which made the decision
    unavoidable and supplied the evidence it needed. Resolved as **GSI2** —
    partition `category`, sort `gsi2_sk` (zero-padded cents + product key +
    store key). Three lines of evidence, all pointing the same way: the access
    pattern IS partition-by-category/sort-by-price; a Scan reads every row to
    return two dozen and DynamoDB bills rows read; and the data team
    independently built the same `CategoryPriceIndex` shape on their own table.

    Live: GSI2 created on `grocery-products-dev`, table re-seeded so the index
    is populated (a sparse GSI is silent — DynamoDB just omits rows with no
    sort key), IAM updated, deployed as version 8. **`dynamodb:Scan` was
    REMOVED from the orchestrator role**, so a live meal plan succeeding is
    positive proof the query path is the index and not a scan.

    `test_the_scan_ceiling_is_asserted_rather_than_assumed` is replaced by
    `test_meal_plan_candidates_are_queried_by_category_not_scanned`, which
    asserts on the CALLS rather than the row count — a row-count ceiling could
    only ever say "still small enough to get away with it".
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
- [x] **Pilot Task 8 — Deliver local read-only MCP first.** Completed
  2026-08-30. `src/mcp/` speaks MCP over stdio JSON-RPC with **no new
  dependency** — an SDK would put a package in `requirements.txt` that the
  Lambda archive then has to exclude, and that exclusion list has to stay
  honest. 22 tests.

  **Two coarse tools, and the list IS the surface** (Req 13.2): `grocery_ask`
  (a natural-language turn) and `grocery_dietary_terms`. No raw DynamoDB, SDK,
  filesystem, network, acquisition, write, citation or unguarded-generation
  primitive. A test asserts no tool name contains a primitive verb, so a
  reviewer can check the claim without reading the implementation.

  **Parity is asserted on the bytes, not argued** (Req 13.4). Every call goes
  through the same `lambda_handler` API Gateway invokes, so grounding, dietary
  fail-closed behaviour, arithmetic and the contract are the same assertions on
  the same code path. If the façade ever grew its own retrieval, that claim
  would quietly stop being true.

  **Caps** (Req 13.3): default-OFF (`MCP_ENABLED=1`, matched exactly, like
  `USE_DYNAMODB`), 6 calls/minute, 60 calls/session, 500-character messages
  refused *before* the service is invoked, 200-event responses. Rate and session
  caps both, because a rate cap stops a burst and a session cap stops a slow
  loop that never bursts. The disable path is exercised as a real subprocess.

  **The audit records that a call happened, never what was asked.** A tool
  argument here IS a shopper's message. A test plants a message containing
  "gluten", "coeliac" and a suburb and asserts none of it reaches the audit.

  **A real bug the subprocess test found:** the service writes Powertools logs
  and EMF metrics to stdout *by design* — that is where CloudWatch reads them —
  and stdout is also the MCP protocol channel. A real run interleaved
  `{"_aws": ...}` blobs with JSON-RPC and no client could parse it. `sys.stdout`
  is now rebound to stderr before the handler is imported, with the true stdout
  kept privately for responses. In-process tests could not have caught it: they
  share loggers already bound to a stream.

  - [ ] **Pilot Task 8 proposed extension — AgentCore Gateway hybrid.** After
    local MCP proof and ADR 0002 mentor approval, expose the same operations via
    AgentCore Gateway with Identity, Policy, WAF/Cognito or approved workload
    identity, privacy-safe audit, quotas, cost evidence, and rollback drill.
    Gateway must never bypass the deterministic Lambda graph.
> **Account audit, 2026-08-30 — the starting position for Tasks 9–12 is not
> what these tasks assumed.** A service plane already exists in
> `ap-southeast-2`, created imperatively on 2026-08-27: REST API
> `grocery-orchestrator-api-dev` (`woqmel35lk`) with stage `dev` and
> `POST /chat`, both Lambdas, alias `live` → version `6`, the ingestion state
> machine, and an ENABLED daily schedule. It answers requests. The README,
> `AGENTS.md` and `infra/docs/00` all said it did not exist; `docs/ARCHITECTURE.md`
> §3 said it did, and was right. All four are corrected.
>
> Consequences for these tasks, none of which change their acceptance criteria:
>
> - Task 9's adoption surface is larger than "data resources" — see
>   `infra/docs/08` §10 for the adopt-or-replace decision now owed per resource.
> - Task 10 codifies things that mostly exist rather than creating them.
> - Task 11 is a **cutover** on a live endpoint, not a first deployment.
> - Task 12 is the largest genuine gap: the service runs with no dashboard,
>   alarm coverage, budget or latency baseline attached.
> - The alias was cut over to current code on 2026-08-30 (`docs/ARCHITECTURE.md`
>   §3a, versions 6 then 7). Live behaviour now evidences `main`, and all four
>   paths — comparison, named regions, clarification, meal plan — were verified
>   working through the deployed endpoint.
> - **Decision 2026-08-30: `max_price_age_days` raised 14 → 45** so the
>   fixture-seeded endpoint can demonstrate priced paths. Reversible dev-stage
>   stopgap; revert when Task 13 lands real ingested prices. Full reasoning in
>   `config/freshness.json` `_decision_2026_08_30` and `docs/ARCHITECTURE.md` §3c.

- [x] **Pilot Task 9 — Establish CDK and adopt existing data resources.**
  Completed 2026-08-30. Environment bootstrapped, `Grocery-Stateful-dev`
  deployed. **Strategy A**: the template contains NO `AWS::DynamoDB::Table`
  resource, so CloudFormation cannot create, replace or delete the tables — the
  adoption evidence is the absence, not an assertion.

  Schema confirmed with `describe-table` against the account, not from a
  document (`infra/docs/06` §0 warns off `datasets/dynamodb_schema/*.json`,
  which describes the data team's separate lineage). Before/after evidence
  recorded: products 2,759 → 2,759, idempotency 74 → 74, `TableId` unchanged
  (`7ce1af63…`, so no replacement), stack resources = `CDKMetadata` only, and
  the live endpoint still answered 200 afterwards.

- [x] **Pilot Task 10 — Define the deployable service plane.** Completed
  2026-08-30. `Grocery-Service-dev`: Python 3.13 zip Lambda from
  `build/lambda.zip`, SnapStart on a published `live` alias, REST API with
  `POST`/`OPTIONS` on `/chat` proxying the alias, scoped IAM built from
  `config/iam-orchestrator-role.json` with `${AWS_*}` resolved from the deploy
  identity, SSM parameters, 14-day log retention, throttling and a usage plan.

  CORS is handled by the HANDLER, not an API-level MOCK preflight —
  `src/handler.py` emits its own headers and answers `OPTIONS`, so a second
  mechanism would produce duplicate `Access-Control-Allow-Origin` headers that
  browsers reject.
- [ ] **Pilot Task 10 — Define the deployable service plane.** Codify the Python
  3.13 zip Lambda, published SnapStart alias, REST API, Guardrail, strict CORS,
  throttling, usage plan, SSM configuration, log retention, and scoped IAM.
- [ ] **Pilot Task 11 — Deploy and verify the anonymous pilot safely.**
  **Deployed and verified 2026-08-30; the CUTOVER is what remains.** Adoption
  and deployment were kept as separate reviewed operations exactly as this task
  asks: `Grocery-Stateful-dev` first (creates nothing), then
  `Grocery-Service-dev` under a `-cdk` name suffix so it stands BESIDE the
  hand-made plane rather than colliding with it.

  Parity verified before any cutover: price checks byte-identical across both
  endpoints; the meal-plan total differed until the same question was run three
  times against the SAME endpoint and returned $35.75/$31.74/$31.74 — inherent
  variance in plan composition, not a configuration difference. Checking that
  before reporting a discrepancy is the difference between a finding and a false
  alarm.

  The CDK plane also fixes three things the hand-made one has wrong: log
  retention (`null`, never expire → 14 days), the account-level API Gateway
  CloudWatch role that `docs/ARCHITECTURE.md` §7 records as unset, and stage
  tracing on from the start.

  **The cutover is DEFERRED, deliberately — decided 2026-08-31: stay dual until
  a frontend exists.** Its only real cost is the URL change and its only real
  question is who that breaks, and nobody knows yet — the frontend is teammates'
  scope and `CONTRACT-v1.md`'s open questions do not auto-adopt defaults until
  2026-09-11. Moving a URL to spare a consumer nobody has written is work that
  would be re-done against the one they actually write.

  Both planes are scale-to-zero so the duplicate costs essentially nothing. The
  hand-made one stays production, which means production keeps `null` log
  retention and hand-added tracing — not urgent, and the reason not to let
  "stay dual" become permanent by default.

  **Revisit when the frontend is built**, not on a date: ask which URL it wired
  to, RE-RUN the parity table rather than reading the 2026-08-30 one, then
  choose.

  **The sequence previously recorded here was wrong and is corrected in
  `infra/docs/08` §10.** `NAME_SUFFIX=''` then deploy fails: the CDK function
  would be named `grocery-orchestrator-dev`, the hand-made function's name, and
  CREATE collides — the `-cdk` suffix exists because of exactly that. Consumers
  must be repointed at `-cdk` and the hand-made resources deleted BEFORE the
  unsuffixed deploy, so it is two URL changes and a gap, not one clean move.
  There is no zero-downtime path and the old wording hid it.
- [ ] **Pilot Task 12 — Add operational acceptance gates and artefact storage.**
  Build CloudWatch dashboards/alarms, X-Ray evidence, Budgets, quota review,
  latency/cost baselines, and alarm drills. Use encrypted versioned S3 with
  scoped prefixes, lifecycle, restore, and deletion tests for approved
  datasets, evaluation results, and review artefacts; use SNS for non-sensitive
  operator and approval notifications.
  - **Substantially done 2026-08-30 — see `docs/ARCHITECTURE.md` §3l.** Alarm
    coverage went from 2 to 8, each bound to a metric confirmed present in
    CloudWatch; the `internal-error` alarm closes the gap where a production
    stage silently configured as a demo fired nothing at all. A 9-widget
    dashboard over the EMF metrics and the gateway. AWS Budget at $25/month
    with 50/80/100% actual and 100% forecast notifications, and the SNS policy
    extended so Budgets can publish. An alarm drill run and reset. First cost
    baseline: $17.63 for August, of which **60% is two models the service does
    not route to** — the live eval sessions, not serving. First latency baseline
    measured against the DEPLOYED endpoint rather than a laptop: price check p95
    2.21s warm against a 5s target, meal plan p95 11.7-12.2s against 20s.
    `apply_alarms.py`'s validator was taught two new cases (EMF-published
    metrics, and statistic-kind alarms) without loosening its existing rules.
    **Still open in this task:** the encrypted versioned S3 artefact bucket with
    lifecycle and restore tests, throttling and stale-data metrics (the alarms
    are deliberately absent until the metrics exist), and a larger latency run —
    n=8 and n=3 are a baseline, not a qualification.
  - **Partial, 2026-08-30: end-to-end X-Ray tracing now exists.** API Gateway
    stage tracing was enabled on `woqmel35lk`/`dev`, so a trace's entry point is
    the gateway rather than the Lambda and the gateway hop is measurable for the
    first time; the SnapStart `Restore` subsegment is also now visible, which
    the cold-path latency baseline needs. Verified against a real trace, not
    just the flag — `docs/ARCHITECTURE.md` §9. This is the *tracing* half of
    "X-Ray evidence"; dashboards, Budgets, alarm drills and the latency/cost
    baselines remain open, and the task stays unchecked.
> **Scoping note added 2026-08-30 — what the teammates' 3,000 prices actually
> need.** The data team's tables are live in the account:
> `smart-grocery-products-dev` (3,000 items, PK `primary_key`, GSI
> `CategoryPriceIndex`, created 2026-08-28) and `smart-grocery-recipes-dev` (175
> recipes). Loading them into the serving table is **not a data load** — it is
> this task's normaliser, plus a change to a safety-critical mapping. Concretely,
> the B→A transform must supply seven things Lineage B does not carry:
>
> 1. **`product_key`, and a synonym table to reach it.** `resolve_product_key` is
>    exact-match with no substring fallback, by design (a substring match once
>    resolved "truffle oil" to canola oil). The 26 fixture products have a curated
>    synonym table; 3,000 arbitrary product names have none, so without one
>    essentially every query returns `no_data`.
> 2. **`pack_grams`** — parsed from a free-text `size`. Only **68.1%** (2,044 of
>    3,000) parse to a mass or volume; the rest are `kg`, `ea`, `6pk`, `12pk`.
>    `unit_price_nzd` and the meal planner's gram-based feasibility both need it.
> 3. **A dietary re-map.** `src/graph/dietary.py` maps exclusion terms to *fixture*
>    categories and is documented as exact against the current catalogue. Lineage B
>    has 17 different category names, so dietary exclusions would fail closed
>    against all of them. **This is invariant 3 and must not be done casually.**
> 4. **`valid_date`** — absent from every Lineage B record. `DATA_SCHEMA.md`
>    documents the dataset as an August 28 2026 snapshot, which is a usable
>    provenance claim *from the data team*; it must be recorded as their stated
>    capture date, not silently defaulted.
> 5. **`price` Number → String.** Lineage B stores money as a DynamoDB Number,
>    which is the float round-trip the whole codebase avoids.
> 6. **`lat`/`lon`** — absent. `RawOffer` requires them. Named regions already
>    cover location for this dataset (`config/regions.json` was written knowing
>    this), but the field still needs a decision rather than a zero.
> 7. **`on_special`** — absent; would become `False` for every row, which changes
>    what the comparison reasoning can say.
>
> Two consequences beyond the transform: 3,000 rows trips
> `SCAN_CEILING_RECORDS` (1,000) and so **forces deferral 6b**, which is that
> test working as designed; and the dataset covers only PAK'nSAVE and New World,
> while the product claims three chains including Woolworths.

> **Started 2026-08-30 — the B→A transform exists and is tested.**
> `ingestion/lineage_b.py` with 42 tests in `tests/test_lineage_b.py`, run over
> all 3,000 real rows rather than a sample. It plugs into the existing
> `PriceSource`/`RawOffer` seam, so `normalise.to_item` and `handler.refresh`
> are untouched. Result over the real dataset: **2,939 kept, 61 dropped as
> non-food, 74 re-classified by the safety override.**
>
> **The finding that justified doing this properly: the source `category` field
> is not a safety control.** Mapped straight through it would have breached
> Invariant 3 in six distinct category pairs — 74 products a vegetarian or vegan
> would have been served:
>
> | Source category | Forced to | Rows | Example |
> |---|---|---|---|
> | `Rice, Pasta & Noodles` | meat | 26 | Buldak Carbonara Hot Chicken Ramen |
> | `Frozen Foods` | meat | 21 | **Frozen Whole Chicken** |
> | `Breakfast Cereals, Oats & Spreads` | meat | 11 | **Beef Manuka Honey & Hickory Sausages** |
> | `Cheese, Butter & Yoghurt` | meat | 7 | **Chunky Cheese Sausages** |
> | `Frozen Foods` | seafood | 5 | Raw Frozen Prawns Cutlet |
> | `Pantry Staples` | seafood | 4 | Bluefin Tuna Loins |
>
> `classify()` therefore maps the category and then overrides on the product
> name, **fail-closed — it may only ever move a product to a MORE restricted
> category.** An over-match costs a vegetarian a product they could have eaten;
> an under-match serves them chicken. `dietary.py` is untouched: the messy
> upstream vocabulary stops at the ingestion boundary and one category
> vocabulary reaches the serving table.
>
> Also settled, each with its reasoning in the module: plant milk maps to
> `dairy` (over-exclusion is the safe direction); pet food is dropped at
> ingestion (it satisfies every check in the system while being obviously not
> dinner); `size: "kg"` means priced-per-kilogram and maps to 1000g; `ea`/`6pk`
> use the existing `pack_grams == 1` sold-each sentinel; money goes through
> `str()` before `Decimal`; `captured_at` is required and must be the data
> team's stated 2026-08-28 collection date, recorded as their claim.
>
> **Synonym table done, 2026-08-30.** `config/product-synonyms.json`
> (config-as-data, like regions.json), `scripts/generate_synonyms.py`, and 31
> tests in `tests/test_synonyms.py`. `SYNONYMS` moved out of
> `src/retrieval/memory.py`; both repositories now load it, and the fixture
> vocabulary is asserted unchanged by the migration.
>
> **The table has two halves, and the split is the safety boundary.** 389
> generated product names — a restatement of the catalogue, safe to derive,
> regenerated by script, with 56 ambiguous names (same name, different sizes)
> DROPPED rather than guessed. And 29 hand-curated head terms, because a bare
> noun is a judgement no rule gets right: "butter" matches 14 products
> including `Salted Butter Frozen Dessert`, "cheese" matches 22 including
> `Chunky Cheese Sausages`, "chicken" matches 42. A "cheapest match" rule
> answers "cheapest butter" with a frozen dessert.
>
> Four terms are **deliberately omitted** with reasons recorded — `cheese`,
> `tuna`, `salmon` (no plain staple in the catalogue, only specific or premium
> products) and `peas` (none at all). They return `no_data`, which is honest;
> the full product names still resolve.
>
> `test_a_head_term_resolves_to_something_of_the_right_kind` asserts the
> *category* of each curated key, so an entry pointing at a novelty product
> fails a test rather than a demo.
>
> `load_synonyms()` returns term → candidate keys because the file describes
> both catalogues; each repository picks the first key its data actually holds.
> The in-memory one filters at construction, the DynamoDB one by querying GSI1 —
> the shared contract suite holds them to the same answer.
>
> **All three follow-ups closed 2026-08-30.**
>
> (a) **Coordinates.** `lat`/`lon` were written as `0.0` with a comment calling
> it fail-closed. It is not a sentinel, it is the Atlantic: `NearFilter`
> computed a real ~18,000km distance and excluded every row, so a radius query
> returned nothing and the graph said "I don't have price data near you" about
> a supermarket in the same suburb — the Task 5a silent-exclusion defect through
> a different door. Now `config/store-locations.json` (suburb centroids, flagged
> approximate and unreviewed, same standing as `regions.json`), and an unknown
> store RAISES rather than defaulting. A test asserts the coordinates agree with
> the fixture catalogue, so the two cannot drift about where a suburb is.
>
> (b) **The Scan ceiling** — see 6b above, now closed with GSI2.
>
> (c) **Head-term review** — this one is not ours to close.
> `docs/OPEN-REVIEW-head-terms.md` is the brief: self-contained, fifteen
> minutes, no code reading, written for somebody who shops at these stores. The
> three close calls are named, the four deliberate omissions are listed with
> their reasons, and every answer is a one-line config change. Lower stakes than
> the feasibility-floor review — a wrong entry is an unhelpful answer, not a
> refusal — but these are the words a demo audience types first.

- [ ] **Pilot Task 13 — Build controlled ingestion and decoupled review
  triggers.** Use EventBridge and Step Functions Inline Map with fixture or
  recorded adapters, provenance, normalization, partial failure, retry, and
  dead-letter behavior. Where review decoupling is justified, add filtered
  DynamoDB Streams -> SQS/DLQ with retry/redrive/backlog evidence. No live
  retailer traffic.
> **Pilot Task 13, first half done 2026-08-30: the real catalogue is loaded.**
> `LineageBSource` (recorded, not live — ACQUISITION-RISK.md §8 untouched) feeds
> the existing `refresh()`, so the diffing, dry-run and per-retailer isolation
> already built all apply. 2,759 rows written to `grocery-products-dev`, and
> **proved idempotent**: the second dry run reports 0 added, 0 changed.
>
> From 3,000 raw: 61 dropped as non-food, 180 collapsed as duplicates, 74
> re-classified by the safety override.
>
> **The duplicate collision is the finding.** `BatchWriteItem` refused the first
> load — one store stocks two BRANDS of the same product at the same size, and
> `derive_product_key` ignores brand so that products compare across chains. 96
> collisions in Pak'nSave alone, and nothing offline had exercised it because the
> fixtures carry one product per key by construction. Resolved by keeping the
> cheapest per (store, product) — the answer the product already gives — with a
> deterministic tiebreak so a re-run cannot report a false `changed`.
>
> **CLOSED 2026-09-01 — one catalogue.** This entry read "Still open: the table
> now holds BOTH catalogues" until 2026-09-02, by which time the deletion had
> happened twice. `docs/ARCHITECTURE.md` §3j removed the 152 fixture rows on
> 2026-08-30; a plain `scripts/load_seed_data.py` run silently re-added them
> (its default action LOADS), the 2026-09-01 parity re-run caught the endpoint
> serving fixture answers again, and §3t removed them a second time and
> **guarded the loader** — it now refuses to load over the real catalogue
> without `--force`, with a regression test. The live table holds Lineage B
> only.
>
> **What did NOT close, and is now worse rather than better:** the Woolworths
> branch of the state machine fetches 0 rows, because the dataset covers two
> chains — 5 New World and 5 Pak'nSave store files, both Foodstuffs banners,
> zero Woolworths. That was recorded here on 2026-08-30 as "honest but means
> the product's 'three chains' claim is currently true only of the fixtures",
> and then the fixtures were deleted. **Deleting them converted a caveat into a
> false headline claim**: `README.md` and `AGENTS.md` still open by promising a
> comparison across Pak'nSave, Woolworths and New World, and `KNOWN_RETAILERS`
> still lists a retailer with no rows behind it. A cross-chain comparison
> spanning two banners of one company is a materially weaker product than the
> one described, and `ACQUISITION-RISK.md` §4.5 attaches Fair Trading exposure
> to **the comparison we publish**. This is a conversation with the data
> teammates, in the same category as the provenance of the 2,759 rows, and it
> belongs before any demo outside the team rather than inside Task 16.
> **Written up 2026-09-02 as `docs/OPEN-REVIEW-chain-coverage.md`** — the
> numbers, the four options and a recommendation (say what we cover now; put the
> searched scope in the response with the frontend work; get Woolworths data if
> the teammates can, gated on ACQUISITION-RISK §8 like everything else).

> **Pilot Task 13, anomaly rejection wired 2026-08-31.** `refresh()` now
> VALIDATES, then diffs, then writes. `ingestion.handler.reject_implausible`
> applies `src/review/snapshot.py`'s rule to every normalised row and refuses the
> ones that fail; the count and a sample land in the Step Functions execution,
> and `config/alarms.json` derives `IngestionRowRejected` from a structured log
> line at a threshold of one row.
>
> **The rule existed for a day with no caller.** It was written on 2026-08-31
> with the $2,490 broccoli in its docstring, tested, and invoked by nothing — so
> the one defect class known to have reached the live products table was still
> undetected in production, while ADR 0002 requested a Runtime to find the
> anomalies nobody had thought of. The rules that HAD been thought of were not
> running.
>
> **Measured over the real catalogue, and the clean number is the less useful
> one.** `python scripts/check_ingestion_anomalies.py` reports 0 rejections
> across 2,759 rows. A clean result from a rule nobody has watched fail is
> indistinguishable from a rule that cannot fire, so the historical defect was
> reintroduced — remove the `pack_grams <= 1` sold-each guard from `unit_price()`
> — and the same run rejects **522 of 2,759**, broccoli included.
>
> **522, not six.** Six is the number this repository has quoted since the
> incident, and it was the size of the *seeded fixture set*, not of the defect
> class. Against the real catalogue that one-line omission corrupts 19% of every
> shopper-facing unit price, on a first write, where the diff reports nothing
> because a defect on a first write is not a change. 0.2% and 19% also settle
> the threshold question: no percentage gate catches both, so there is none.
>
> **What the rules cannot see is recorded too** (`docs/ARCHITECTURE.md` §3p), and
> it points somewhere: the largest invisible category needs a BASELINE — "this
> price doubled overnight" — which is the append-only price-history table, not a
> reviewer. That is ADR 0002 gate 4's acceptance data, and it argues for building
> the history table first.

- [x] **Pilot Task 12a — Put a gate under the CDK. Done 2026-08-31.**
  `infra/` had no CI job of any kind — no `tsc`, no `jest`, no `cdk synth` —
  while `infra/lib/service-stack.ts` is where the security posture is declared,
  and `infra/test/service-stack.test.ts` was `describe.skip` under a header
  reading "SKIPPED until ServiceStack is implemented (it is a stub today)". The
  stack had been 230 lines and deployed for a day.

  **Running the suite found two IAM regressions in a plane that was serving.**
  `tables.products.grantReadData(role)` had reintroduced `dynamodb:Scan` — the
  permission Pilot Task 6b removed the day before, with
  `config/iam-orchestrator-role.json` warning in as many words that "a Scan
  permission nothing needs is a Scan somebody can reintroduce without noticing".
  It also widened `index/GSI1`/`GSI2` to `index/*` and added Streams reads.
  `grantReadWriteData` on idempotency added `DeleteItem`, against a config
  comment saying expiry is by TTL. A CDK grant helper does not CHECK a policy
  loaded from `config/`; it appends a second one.

  **And two assertions were theatre.** `it('the only Resource:"*" is X-Ray')`
  had an empty body. The write test matched a regex spanning unrelated
  statements and FAILED on a policy with no write on products at all. Rewritten
  to parse the policy document and compare action sets per resource; 24
  assertions now, each watched to fail against a mutated stack.

  Also closed in the same pass: the SSM parameter that published
  `models.json[:4096]` — invalid JSON, since the file is 10,930 bytes and
  neither SSM tier holds it — now publishes the routing block and THROWS at
  synth rather than slicing; `APP_STAGE` is set from `cfg.stage`, so Req 12.5's
  runtime check is no longer inert under CDK; `config/stages.json` gives the
  Python and TypeScript halves of that check one definition of "production";
  adopted table names moved off the stage axis, so `stage=prod` no longer
  references tables that do not exist; and the region guard, which refused
  `cdk synth` locally and never ran in CI, became a pin plus a test.

- [x] **Pilot Task 12c — ObservabilityStack, watching both planes. Written
  2026-08-31. NOT DEPLOYED, paused by decision — see the note at the end.**
  Real, tested, and NOT YET DEPLOYED — deploy it before the cutover, not after.
  SNS, metric filters and alarms built from `config/alarms.json`, a dashboard, a
  $25 budget, and the encrypted versioned artefact bucket (audit top-10 #15).

  **A correction to the second audit's Finding 3.** It says the CDK plane is
  "unalarmed, undashboarded". Two of three are right: SIX of the nine alarms
  already covered both planes, because they watch EMF metrics dimensioned on
  `service` and `POWERTOOLS_SERVICE_NAME` is `grocery-orchestrator` on both.
  The gap was the two alarms bound to a PHYSICAL name — the API 5xx alarm and
  the handler-escaped metric filter. Those are now created per plane, derived
  from `cfg.suffix`, and collapse to one set when the suffix is empty, so the
  deploy that retires the hand-made plane needs no edit.

  **And the shared dimension is half a win, recorded as such.** Six alarms
  covering both planes also means a metric cannot say which plane produced it:
  while dual-running, a spike on the unused CDK plane is indistinguishable from
  one on the plane serving shoppers. Splitting the dimension would split every
  historical series with it, so it is deliberately not done — the cutover is
  the fix, and if dual-running becomes permanent this is a reason it should not.

  **No SNS subscription is declared**, and a test asserts that. An email
  subscription needs out-of-band confirmation, so a declared one sits
  `PendingConfirmation` and reads — in a console and in a template — exactly
  like somebody who would be paged.

  12 assertions, each watched to fail against a mutated stack.

  **PAUSED 2026-08-31, waiting on the frontend teammate.** The owner's call: the
  teammate who owns the frontend is working on something in this area, so let
  that land on GitHub and re-evaluate once rather than twice. This stack, the
  URL choice, the plane retirement and the API key are one decision wearing four
  hats, and deploying the alarms first would commit to alarm names and a second
  budget before knowing which plane survives.

  **While paused, the CDK plane's gateway has no 5xx alarm** and its log group
  has no handler-escaped filter — six of eight alarms cover it through the
  shared `service` dimension, the two bound to a physical name do not. The cost
  tripwire is real and is NOT this stack's: `grocery-orchestrator-monthly-dev`
  at $25 exists, made by hand (`ARCHITECTURE.md` §3l), confirmed live.

  The ordered checklist for coming back is `docs/ARCHITECTURE.md` §3q. Step 2 is
  "deploy this stack BEFORE pointing a consumer at the CDK plane, not after".

- [ ] **Pilot Task 12d — Bound the endpoints, not just watch them.** DECIDED
  2026-08-31 by the owner, and DEFERRED TO A TRIGGER rather than to a date:
  **option C now, option A in the same change that repoints the frontend at the
  CDK plane.** `docs/OPEN-REVIEW-api-key.md`.

  **The trigger is a test, not a note.** `infra/test/app.test.ts` fails the
  moment `FrontendStack` creates its first resource — which is when a frontend
  is being deployed and wired to a URL — and its failure message carries the
  review document and the two options. Verified by implementing a stub bucket in
  `FrontendStack` and watching it fire. A note saying "revisit when the frontend
  lands" is the same shape as "SKIPPED until ServiceStack is implemented", and
  two audits have now found those.

  Alarming both planes makes abuse VISIBLE; it does not BOUND it. An API key
  plus a usage-plan quota turns an unbounded Bedrock bill into a number chosen
  in advance, and it is minutes of CDK. What it costs is not the CDK: it adds a
  required `x-api-key` header to `CONTRACT-v1.md`, returns API Gateway's own 403
  body rather than the contract-valid `ChatResponse` this service guarantees on
  every other path, and breaks the working Vite/React client on
  `frontend-infra-setup` that has been building against the contract since
  2026-08-21. Nobody has agreed who holds the key, and a key in a public bundle
  bounds cost rather than authenticating anybody.

  The review carries the design, the three options with what each costs, and the
  four things that would change the answer — a frontend cutover date, any
  outside traffic, a demo outside the team, or the $25 budget firing for a
  reason nobody on the team caused.

- [x] **Pilot Task 12b — A skip that expires. Done 2026-08-31.**
  `tests/test_skip_markers.py` fails when a skip carries no machine-checkable
  condition, in Python and TypeScript alike, and refuses `.only` because it
  disables every other test in a file without the word "skipped" appearing
  anywhere. The sibling of `tests/test_ci_workflow.py`, and it would have caught
  all three instances of this pattern the two audits found: the coverage
  instrument, the forcing test behind it, and the infrastructure suite.

- [ ] **Pilot Task 14 — Add the bounded data-quality reviewer.** After ADR 0002
  mentor approval, deploy it separately in AgentCore Runtime over capped
  sanitised ingestion snapshots with an isolated least-privilege identity,
  read-only allowlisted tools, row/call/token/time/cost/egress caps, cited
  schema-checked findings, deterministic post-validation, human approval, and
  teardown evidence. It receives no shopper PII and has no production write,
  publication, or shopper-path authority. AgentCore Evaluations may supplement
  local labelled anomaly tests with reproducible provenance.
  - [x] **14a — The boundary and the post-validation. Done 2026-08-31.**
    `src/review/` builds the sanitised capped snapshot and validates findings
    against it. 20 tests. **No AgentCore surface is built and nothing is
    deployed** — ADR 0002 is still *Proposed, mentor approval required*, and
    that gate is about deploying a Runtime, not about writing the constraints
    it would run inside.

    **This half is required whoever reviews**, including a person with a
    spreadsheet, which is why it is worth building ahead of the decision. The
    reviewer is the untrusted component in both cases; the boundary and the
    check are what make its output usable.

    **The snapshot is an ALLOWLIST, not a redaction.** Req 13.8 forbids shopper
    messages, locations, dietary data, sessions and credentials reaching the
    reviewer. `SNAPSHOT_FIELDS` names the 13 fields it may see and `SnapshotRow`
    is a separate type from `PriceRecord`, so a field added to retrieval later
    cannot silently join the snapshot. Redaction has to be remembered;
    deny-by-default does not. `snapshot_to_dicts` iterates the allowlist rather
    than calling `asdict`, for the same reason.

    **`build_snapshot` raises rather than truncating.** Silently taking the
    first 500 rows would make the reviewer's view depend on the caller's
    ordering, so a finding about "the catalogue" would really be a finding about
    whichever rows arrived first. The caller chooses the slice, and then the
    record says what was reviewed.

    **The validation is the same shape as `assert_citations_match_retrieval`,
    for the same reason.** That check exists because a citation naming the right
    table with a plausible key and a price nobody retrieved passed cleanly:
    SHAPE IS NOT IDENTITY. A finding is the identical risk in different clothes,
    so every one is checked three ways — the reference exists in the snapshot,
    the values it quotes match that row exactly, and it reports rather than
    prescribes. A finding failing any of them is a fabrication, dropped with the
    reason recorded, and `fabrication_rate` is the number that shows a reviewer
    has stopped referring to real rows before a human notices the findings are
    useless.

    **There is no field for a proposed value** (Req 13.8: candidate prices are
    not publication authority). Because a reviewer inclined to prescribe would
    then write it in prose instead, `_PRESCRIPTIVE` refuses "should be $2.49" in
    the observation text — the same authority arriving through the back door.

    **The one anomaly rule we already know stays as CODE.**
    `implausible_unit_price` catches the live defect — `unit_price_nzd` of
    "2490.00" against a $2.49 sold-each broccoli, six rows, shipped with no
    signal. A model might notice it; a comparison cannot fail to. The reviewer's
    value is the anomalies nobody thought to write a rule for, and handing it
    the ones we did think of would be paying a language model to do arithmetic.
    Swept over all 152 catalogue rows: 0 false positives, and the 6 sold-each
    rows exercise the branch that produced the defect.

    **Still open (needs ADR 0002):** the Runtime itself, the isolated identity,
    the call/token/time/cost/egress caps, teardown evidence, and the labelled
    anomaly evaluation.
  - [x] **14b — Put ADR 0002 in front of the mentor. CLOSED 2026-09-02.**
    Written 2026-08-31.
    **Narrowed the same day to the reviewer Runtime only.** Gateway is withdrawn
    — a managed auth layer over two coarse operations that already work gets a
    shopper nothing, and it is the easy thing to approve precisely because its
    precondition is met, which is the wrong reason to ask. The managed
    evaluations are withdrawn because gate 4 blocks them on US: it wants
    versioned acceptance datasets and the labelled anomaly set does not exist.
    Withdrawn, not declined; either may be raised again.

    The reviewer is the one worth asking for because its value is a question
    nothing else here can answer — we know what our own rules catch, and not
    whether a model finds defects nobody wrote a rule for.
    `docs/OPEN-REVIEW-adr-0002.md` is the brief: twenty minutes, no code
    reading, same shape as the two other open reviews. It states what approval
    does *not* do (it does not move the shopper path onto AgentCore, and it does
    not authorise deployment — gates 3-7 still bind), records that gate 2 is now
    met by the shipped MCP façade, argues the case AGAINST as well as for, and
    offers four answers including "approve none". **A decline costs very
    little**, which is the point of having built 14a first. The ADR's own
    implementation status was rewritten the same day; it had been true of
    2026-08-23, when the service plane did not exist.

    **Approval is not something this repo can grant itself**, so this task
    closes when a person edits the Status line of the ADR — not when the brief
    is written.

    **That happened on 2026-09-02, and the criterion above is what closes this
    task.** The mentor gave full autonomy over ADR 0002; the decision taken
    under it was the brief's own recommendation — the reviewer Runtime only,
    with Gateway and the managed evaluations withdrawn rather than declined.
    The ADR's Status line records it, `docs/OPEN-REVIEW-adr-0002.md` is marked
    ANSWERED and kept for its reasoning, and the reviewer was then built,
    deployed, measured and torn down (`docs/AGENTCORE-RUNTIME-REVIEWER.md`
    §13-§15).

    **This box stayed unticked, and the brief stayed "open, and wants a
    mentor", for a day after the decision was taken and the prototype had been
    built and deleted.** Both were corrected on 2026-09-02. Worth recording
    because a ledger entry that under-reports is the same defect as one that
    over-reports: this one had the roadmap showing an unanswered question in
    front of a mentor who had already answered it.

    **What 14b does NOT close is Task 14 itself.** Approval was to run the
    experiment, and the experiment ended in teardown by design. Retention is a
    separate decision: the hypothesis is recorded as "promising but unproven at
    this scale", and `Grocery-Reviewer-dev` synths but cannot deploy until
    `AWS::BedrockAgentCore::Runtime` reaches ap-southeast-2.
- [ ] **Pilot Task 15 — Introduce the curated recipe catalogue.** Models select
  recipe ids and product citations; code owns scaling, safety, and totals.
  A Knowledge Base may be evaluated only for cited recipe/catalogue retrieval
  and never for authoritative prices.
  - [x] **15a — Catalogue, dietary classification, and the coverage gate.**
    Completed 2026-08-30. `src/recipes/` holds the 175 recipes behind a
    `RecipeRepository` protocol, classifies each recipe's dietary content from
    its INGREDIENTS rather than its label, and measures how much of each recipe
    this product catalogue can price. 12 tests.
  - [x] **15b — Curated catalogue and deterministic assembly. Done 2026-08-30.**
    `config/recipes.json` holds **29 recipes written against this catalogue**,
    every ingredient priceable **against the real catalogue** by construction —
    the property that made the imported 175 unusable.

    *Qualified 2026-08-31.* "Priceable" needs the catalogue named, because the
    two are different universes: 29/29 resolve against `datasets/` (the ~2,700
    rows the deployed table holds) and 14/29 against the 152-row offline
    fixture. `tests/test_curated_recipes.py` resolves against `datasets/` and
    its docstring says why; `Philip_demo/11` §7 now prints both numbers rather
    than the flattering one. The imported 175 are 0/175 against **both**, which
    is what makes the decision sound rather than convenient. `src/recipes/planning.py` converts selected recipes
    into a `PlanDraft`, which is the shape `assemble_plan`, `validate_plan`,
    `assert_arithmetic` and the bounded repair loop already consume. 19 tests.

    **Producing a `PlanDraft` rather than a new plan type is the design.** Each
    of those downstream checks was hardened by a real defect; a recipe planner
    with its own plan type would need its own versions of all of them, and the
    second copy is the one that goes wrong.

    **The pack multiplier is the only number this path invents.** 150g per
    serving × 3 people against a 500g pack is 0.9 packs; a count ingredient
    converts directly (3 eggs × 2 = 6 packs) because the catalogue records
    sold-each goods with `pack_grams == 1` — a sentinel meaning one unit, not
    one gram. Verified end to end: "Sausages and Mash" for three costs $7.14
    consumed and $17.14 payable, the two-totals distinction intact.

    **A gap found and closed while doing it.** `recipe_excluded_categories`
    scans ingredient NAMES for meat and seafood words, which is right for an
    imported recipe whose ingredients cannot be resolved — but it reports
    "Scrambled Eggs on Toast" and "Broccoli and Cheese Pasta" as excluding
    NOTHING, and a vegan would be served both. `recipe_categories` now derives
    the answer from the resolved products instead, and `is_viable_for` filters
    selection on it. Viability by diet: vegetarian 18/29, dairy-free 18/29,
    pescatarian 20/29, vegan 7/29.

    Vegan is tight on purpose: `lineage_b.py` maps the catalogue's combined
    "Fresh Milk & Plant Milk" to `dairy` wholesale, because the source does not
    separate oat milk from cow's milk and over-excluding is the safe direction.
    Per-product allergen tagging (legacy 11.7) is what lifts it; widening the
    category map would trade a safety property for a menu.

    **Still to wire:** the selection prompt (model returns recipe ids), the
    graph branch that uses it, and an eval suite for recipe-constrained plans.
    The deterministic half — which is what Req 2.9 actually specifies as code's
    responsibility — is done and tested.

  - [x] **15c — Selection wired into the graph. Done 2026-08-31.**
    Req 2.9 is delivered on the shopper path: a meal-plan turn is built from
    NAMED CURATED RECIPES, and falls back to free composition with a notice when
    it cannot be.

    **The topology decision is the one worth reviewing.** The obvious shape — a
    `select_recipes` node BEFORE retrieval, picking from the catalogue, with
    retrieval then fetching the chosen ingredients — was rejected. It puts a
    model call upstream of the only node allowed to create a Citation, and the
    topology ("no edge skips `retrieve_prices`") is one of three independent
    enforcements of Invariant 1. The node produces no price and could not, so
    the exception would have been harmless today and load-bearing later.

    Instead retrieval gained a recipe mode: it resolves the catalogue's **27
    distinct ingredient terms** (not ~120 recipe lines), cites them, and
    shortlists recipes that are (1) costable — every ingredient resolved, all or
    nothing; (2) dietary-viable judged from the RESOLVED products, not from the
    recipe's name; and (3) affordable AS A SET. The model is offered only what
    survives, so it cannot select an uncostable, unsafe or unaffordable recipe —
    those options never reach it. Stronger than validating the selection
    afterwards, and the same argument `candidates_for_budget` already makes.

    **Two designs were tried and measured, and the eval caught both.**

    *A day is not a meal.* The first version asked for one recipe per day. The
    meal-plan suite went from 100% invariants to **45%**, budget used from 69% to
    **24%**, and `min_budget_used` fired on four cases — which is exactly the
    under-feeding that check was added to catch. The count now comes from
    `min_grams_per_person_day` in `config/feasibility.json`, the same figure the
    feasibility refusal uses, so the two cannot disagree about how much a
    household eats.

    *A per-recipe budget cap rejects on a number no plan ever pays.* The second
    version capped each recipe at `budget / meals`. `assemble_plan` aggregates
    packs across meals and rounds up ONCE, so recipes sharing rice and onions
    cost far less together than apart: the cap collapsed a 29-recipe shortlist to
    **one** against the fixtures, and the suite read the result as under-feeding
    again. The budget is now enforced on the SET (`affordable_set`, greedy
    cheapest-first, skipping rather than stopping) plus a marginal-cost trim
    after selection. Verified against an exhaustive search: for "a week of
    dinners for one person on $35" the greedy set finds the same five meals that
    checking every combination finds, and no six-meal combination fits.

    **The scorecard reads what the MODEL returned, not what the node served.**
    `select_recipes` tops a short selection up from unused recipes and trims
    meals that do not fit. Both are right for a plan and fatal for a gate: with
    the eval scoring the served list, a planted model that returns one meal every
    time scored **100%**. `recipe_selection_model` carries the raw answer.

    **Gates.** `evals/run_recipe_select.py`, 12 cases, wired into CI at 0.90.
    Every case verified to DISCRIMINATE against a model built to fail it — a
    fabricated id scores 0%, a repeated selection 8.3%, an under-count 0%,
    against a 100% scripted baseline. `tests/test_recipe_selection.py` adds 13
    tests. `samples/response_meal_plan.json` regenerated and, for the first time,
    covered by `tests/test_sample_fixtures.py` — it had been stale and unguarded,
    which is how the published contract came to describe "Scripted Dinner 1".

    **`select_recipes` was `config/models.json`'s one exemption from the
    scoring gate, and it was CLOSED live on 2026-08-31** (commit `bc81fa0`) —
    it had been exempt from the LIVE measurement rather than from having a
    suite. Nova Lite 100% and Claude Haiku 100%, three identical reps each,
    zero fallbacks, so the suite cannot rank them; `distinct mains` (Haiku 3.8,
    Nova Lite 3.4) is the one measured difference and is reported rather than
    scored. Nova Lite is preferred on cost, Haiku is the qualified fallback.
    **`EXPECTED_EXEMPTIONS` in `tests/test_multimodel.py` is now the empty set**
    — every routable task carries a live scorecard — and it is pinned exactly,
    so adding an exemption fails the build and so does removing one without a
    scorecard. `AGENTS.md` §"Scored live 2026-08-31" carries the numbers.

    **What the fallback costs, stated rather than hidden.** Against the
    26-product offline fixtures many curated recipes do not fully resolve, so a
    narrow diet reaches the fallback often; against the real 528-product
    catalogue 29/29 are costable and vegan viability is 7/29. The notice names
    the reason, because "Tuesday: Sausages and Mash" and a list of cheap products
    are different products and the shopper should know which they got.

    ---

    *The original blocker, kept for the reasoning.* A recipe is usable only
    if EVERY ingredient can be priced: a payable total computed from part of a
    shopping list is a number the shopper cannot spend to, and `within_budget`
    derived from it is a false promise — the one failure this codebase exists to
    prevent.

    **Re-measured 2026-08-31 against BOTH product catalogues** (audit finding
    D3). The figures below used to be the fixture ones alone, while this entry,
    `src/recipes/base.py` and the README all described the real catalogue:

    | product catalogue | best | median | at 100% | at ≥90% |
    |---|---|---|---|---|
    | `datasets/` — 528 products, 2,939 rows | **75%** | 17% | **0** | **0** |
    | `fixtures/` — 26 products, 152 rows | **75%** | 12% | **0** | **0** |

    Zero under any staples assumption, either way. **The decision below was
    sound and was reached by luck**: it rested on an instrument pointed at the
    26-product fixture file and survived only because the answer happened to be
    the same against the real catalogue. It is now held by a control —
    `test_the_two_catalogues_agree_that_the_imported_recipes_are_unusable`.

    **The two datasets were built for different jobs.** TheMealDB recipes are
    international home cooking, median 11 ingredients, reaching for soy sauce
    (53 recipes), garlic (43), lime (36), fish sauce (36), ginger (34) and
    coriander (29). The product catalogue is weighted to fresh produce, meat and
    dairy, with no spice rack, no condiments and no long tail. `water` appears
    in 42 recipes and is not a grocery product at all.

    Widening "assumed on hand" from {water, salt, pepper} to a full spice rack
    and pantry — 40+ terms — moved usable recipes **from zero to zero**. The gap
    is not staples, and a generous staples list would only have hidden costs the
    shopper still has to pay.

    `test_the_imported_catalogue_still_cannot_be_planned_from` FAILS WHEN THE
    BLOCKER LIFTS, verified by simulating a complete catalogue. Same forcing
    shape as the Scan ceiling in 6b, pointed the other way, so "not enough data
    yet" cannot quietly become permanent.

    **It watches `datasets/` now, and until 2026-08-31 it watched the
    fixtures** — a catalogue `scripts/generate_fixtures.py` regenerates to a
    fixed shape and which therefore cannot grow. Its trigger condition was
    unreachable, so it read as a working control and guarded nothing. It skips
    rather than falling back when the dataset is absent, because a forcing test
    that quietly re-points at a catalogue which cannot change is the defect it
    was just corrected for.

    `python scripts/check_recipe_coverage.py --missing 20` reports the distance
    and names what is absent. It prints the catalogue and recipe set it
    resolved against on every run, and refuses `--fail-under` against the
    fixtures while the real catalogue is available.

    **DECIDED 2026-08-30 (Philip): option (b), sequenced AFTER the IaC work.**

    Curate roughly 20-30 recipes written against THIS catalogue rather than
    importing a general recipe API. The constraint is narrower than it first
    looks: TheMealDB's vocabulary does not overlap ours, but writing new recipes
    against our own does not have that problem — there are ~418 known terms (389
    generated product names plus 29 curated head terms), so a recipe reading
    "whole chicken, basmati rice, brown onions, carrots" is 100% costable by
    construction.

    This is legitimate curation, not gaming the requirement. *Curated* means
    chosen deliberately, and Req 2.9's actual point is that the model selects a
    recipe id while deterministic code owns scaling, dietary verification and
    totals. Choosing recipes the catalogue can price serves that; importing
    recipes it cannot price defeats it.

    **Sequenced after Pilot Tasks 9-11 deliberately.** Task 15 is prompt-and-data
    work with almost no AWS surface, and the sprint's stated second objective is
    broad hands-on AWS. The CDK work is unstarted and every reproducibility,
    drift-detection and multi-account claim waits on it. Task 15 has real demo
    value — a plan reading "Tuesday: Chicken & Rice Bake" presents far better
    than a list of cheap products the model named — but it is polish, and polish
    goes after the thing everything else is blocked on.

    **When it is built, treat the recipes as reviewable data**, like
    `config/regions.json` and the head terms: someone who cooks has not checked
    them, and that should be recorded rather than assumed.

    Rejected, with reasons: (a) the data team widening collection to condiments
    and spices — a teammate dependency on an unknown timeline, and TheMealDB's
    tail is 451 distinct ingredients, so it would take most of a supermarket;
    (c) narrowing Req 2.9 to partial pricing — it weakens the budget promise and
    contradicts invariants 1 and 2, which is too high a price for a feature.
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
- [ ] **5.6** Subjective quality scoring for variety and appeal — still open,
  and still deliberately. `design.md` §8 and AGENTS.md both argue an LLM judge
  puts a non-deterministic scorer inside a suite whose value is being
  deterministic. What changed on 2026-08-30 is the *other* half of that note:
  the repair suite is no longer too small to gate. See `5.9`/repair below
  — *deliberately still open; `evals/run_prose.py` (2026-08-29) scores RULE
  COMPLIANCE only. An LLM judge would put a non-deterministic scorer inside a
  suite whose value is being deterministic.*
- [x] **5.7** Score every candidate model and publish the comparison
  — *Req 9.5* — **[current blocker: model-specific access and missing scorecards]**
- [x] **5.8** Red-team case set for content safety, covering both content that
  must be blocked and content that must be allowed
- [x] **5.9** Harness that runs the red-team set against the numbered live
  Guardrail and reports each case's outcome — closed 2026-08-29 by Pilot Task 3
  follow-ups (a) and (b): the harness's own controls were fixed first, then
  13/13 must-block and 9/9 must-allow recorded against version 2

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
- [x] **7.7** Implement name normalisation in ingestion — *Req 8.3* — closed
  2026-08-30. `ingestion/lineage_b.py` derives one canonical `product_key` from
  a retailer's product name and size, so the same item at Pak'nSave and New
  World shares a key and GSI1 can compare them; 251 of ~400 distinct keys occur
  in both chains. Deriving it is what makes the comparison possible at all
- [ ] **7.8** Schedule the refresh — *Req 8.4* — the EventBridge schedule and
  state machine exist and are ENABLED; what they refresh from is now a choice
  (`PRICE_SOURCE`), and pointing the scheduled run at `lineage_b` is a
  deliberate act nobody has taken
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
- [x] **8.5** Per-function roles scoped to named resources — *Req 11.1* —
  four roles live in `ap-southeast-2`, one per principal: orchestrator,
  ingestion, Step Functions and scheduler. Read-only on products for the
  orchestrator with GSI1 and GSI2 named explicitly and `Scan` removed;
  ingestion cannot read the model or the idempotency table. Codifying them in
  CDK remains Pilot Tasks 9–10
- [ ] **8.6** Managed secret storage — *Req 11.2* — **[superseded by Pilot Task 10]**
- [x] **8.7** Gateway throttling and usage plans — *Req 11.4* — closed
  2026-08-30. Stage `dev` throttles at 5 rps / burst 10, and usage plan
  `grocery-orchestrator-dev-plan` (`v4yd7d`) now carries the same limits
  attached to that stage. `security.md` line 22 requires BOTH — the stage had
  throttling and there was no usage plan at all, so half the control was
  missing. No API key requirement: the pilot is anonymous, and a usage plan
  throttles without one
  — **[superseded by Pilot Task 10]**
- [ ] **8.8** Authentication on the endpoint — **[later roadmap after anonymous pilot]**
- [x] **8.9** Define the content safety policy as version-controlled data and
  validate it offline — *Req 5.5*
- [ ] **8.10** Verify the content safety policy against the numbered live
  Guardrail using the red-team set from 5.8 — *Req 5.5*. **The policy was
  verified (13/13 + 9/9 against version 2, 2026-08-29) but the SERVICE DOES NOT
  APPLY IT.** `grocery-orchestrator-dev` sets `BEDROCK_GUARDRAIL_VERSION=1`, so
  production runs the version whose foraging over-block version 2 fixed —
  confirmed live on 2026-08-30: `how much is truffle oil` and `cheapest button
  mushrooms` both return `GUARDRAIL_BLOCKED`. Verifying a policy and applying it
  are two different facts and this task needs both. See `docs/ARCHITECTURE.md` §3f
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
- [x] **10.2** Enable snapshot-based cold-start optimisation on a published
  alias — live: alias `grocery-orchestrator-dev:live`, SnapStart
  `OptimizationStatus: On`, and the X-Ray trace shows a `Restore` subsegment of
  ~0.6s. Codifying it in CDK remains Pilot Task 10
- [x] **10.3** Deploy the endpoint with cross-origin support — live:
  `POST /dev/chat` on `woqmel35lk`, CORS emitted by the handler and `OPTIONS`
  answered by it. **`CORS_ORIGIN` is `*`**, which Req 12.5 and `security.md`
  forbid for a production stage; that tightening is Pilot Task 10 and needs the
  frontend's origin to exist first
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
