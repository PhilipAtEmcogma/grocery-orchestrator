# Pilot Task 16 — the release gate battery

**Status: RUN 2026-09-04. Ten gates executed; results in section 10.**
**Scope:** the nine mandatory gates `tasks.md` Pilot Task 16 names, plus the MCP
demonstration gate, measured against the deployed dev plane.

This document exists because Task 16 is the one task whose output is *evidence*
rather than code, and evidence that was not planned before it was gathered is
evidence shaped by whatever happened to be easy to collect.

---

## 1. What Task 16 actually asks for

Verbatim from `tasks.md`:

> Run mandatory **offline, live-adapter, infrastructure, security, evaluation,
> load, privacy, recovery, and cost** gates. Local MCP has its own planned
> demonstration gate. For each optional managed service actually approved and
> adopted, also run parity, privacy, cost, and rollback/removal gates; an
> unapproved optional service is not a release prerequisite.

And the acceptance targets, which are what the gates are measured against:

| # | Target |
|---|---|
| T1 | 100% pass for grounding, literal-money rejection, arithmetic, dietary fail-closed, Guardrail propagation, and their negative controls |
| T2 | Every **enabled** model has a published scorecard; every **active route** scores at least 90% on its applicable golden set |
| T3 | p95 price check under 5s, p95 meal plan under 20s, p99 meal plan under the ~25s escalation trigger |
| T4 | At least 99% successful responses (excluding intentional contract-valid refusals); unhandled 5xx below 1% |
| T5 | No message, raw location, dietary value, credential or model prompt in logs, traces, managed evaluations, review snapshots or notifications |
| T6 | Every published price carries an exact source key, store location and capture date, independently compared against immutable retrieved context |
| T7 | Cost per successful task recorded; alerts at 50/80/100% of budget; unit-cost regressions over 20% reviewed |

---

## 2. The constraint that shapes everything: the ceiling

Measured live by `scripts/check_quotas.py` on 2026-09-04, not quoted from a
document:

| Turn | Turns/min | Calls | Bound by |
|---|---|---|---|
| price check | **10.0** | Nova Lite x2 | Nova Lite |
| meal plan, from a recipe | **6.7** | Nova Lite x3 | Nova Lite |
| meal plan, free composition | 6.7 | Nova Lite x3 + Nova Pro x1 | Nova Lite |
| meal plan, 2 repairs | 4.0 | Nova Lite x5 + Nova Pro x1 | Nova Lite |

**Nova Lite is 20 requests/min and the quota is NOT adjustable.** Claude's is;
Nova's is not, and Nova is what production routes to. The reflex answer — ask
AWS to raise it — is unavailable for exactly the models in the path.

**This makes "load test" mean something specific here.** The ceiling is roughly
0.17 requests per second. The API Gateway throttle (5 rps, burst 10) never
binds; the model quota binds two orders of magnitude earlier. A conventional
concurrency ramp would measure the Bedrock quota, not this service.

**`docs/THROUGHPUT-AND-SCALING.md` section 1 is stale** and this work corrects
it: its table still says meal plan = 10.0/min, which was true before Task 15c
added a third Nova Lite call. `AGENTS.md` and `check_quotas.py` already say 6.7.

### A cost finding from the same measurement

The 15c recipe path calls **Nova Lite x3 and no Nova Pro at all** — the plan is
assembled deterministically from the chosen recipes, so `generate_plan` drops
out of the turn entirely. Meal plans became roughly **five times cheaper** per
turn on 2026-09-04, at the same throughput ceiling.

---

## 3. The cost basis, measured rather than assumed

From EMF metrics over the five turns run during the 2026-09-04 deploys
(`GroceryOrchestrator/InputTokens`, `OutputTokens`, `ModelCalls`,
`TurnsProcessed`):

```
5 turns · 13 model calls · 17,186 input tokens · 1,085 output tokens
```

The call mix decomposes exactly — two price checks at 2 calls each, one pre-15c
free-composition plan at 3, two recipe plans at 3 — which is the check that the
metric means what it claims. Average is about **1,322 input / 83 output tokens
per call**.

Against `config/models.json` `cost_per_1k`:

| Turn | Calls | Estimated cost |
|---|---|---|
| price check | Lite x2 | **$0.0002** |
| meal plan (recipe path) | Lite x3 | **$0.0003** |
| meal plan (free composition) | Lite x2 + Pro x1 | **$0.0015** |
| intent eval case, Nova Lite | x1 | $0.0001 |
| intent eval case, Nova Pro | x1 | $0.0013 |
| intent eval case, Claude Haiku | x1 | $0.0014 |

**Estimated cost of the entire battery is well under US$1** (section 7). The $25
monthly budget is not the constraint; the non-adjustable quota and wall-clock
pacing are.

---

## 4. Decisions taken before drafting

Put to the owner on 2026-09-04 and answered:

| Decision | Answer |
|---|---|
| What the load gate proves | **Sustained at ceiling AND honest over-ceiling degradation.** The repo claims a throttle degrades honestly and has never watched it happen |
| Throttles against the 99% target | **Measure separately, report both.** Under-ceiling success must be at least 99%; over-ceiling behaviour is judged on contract-validity and absence of corruption, not on success rate |
| Which models get re-scored | **All three enabled** — Nova Lite, Nova Pro, Claude Haiku 4.5 |
| Budget | Estimate first, then approve (section 7) |

---

## 5. The gates

Each gate states what it ASSERTS, how it is RUN, what EVIDENCE it leaves, and
its PASS criterion. "Have" means the evidence exists and needs only re-running
at the release commit; "RUN" means it does not exist yet.

### G1 · Offline — *have, re-run at the release commit*

- **Asserts:** the deterministic core is correct without an account.
- **Run:** `pytest -q` (945 passed / 31 skipped today; 937 when G1 ran, before
  the Phase B fix added eight), `ruff check`,
  `ruff format --check`, `pyright`, `validate.py`, contract and grounding
  validation, both eval floors, the secret scan, and
  `cd infra && npm test` (52 assertions across 6 suites).
- **Evidence:** a CI run on the release commit, linked.
- **Pass:** all green. **T1** is partly discharged here — the negative controls.

### G2 · Live-adapter — *RUN*

- **Asserts:** every adapter works against real AWS rather than a fake — the
  DynamoDB repository, the Bedrock model plane, the Guardrail, the idempotency
  store.
- **Run:** live turns through `POST /dev/chat`; an idempotency replay (the same
  `turn_id` twice returns an identical body and increments `IdempotentReplay`);
  a Guardrail block; a `STALE_DATA` and a `NO_DATA` path.
- **Evidence:** request and response pairs with citation source keys, recorded.
- **Pass:** every adapter exercised live, and **T6** discharged — each published
  price carries a source key, store location and capture date matching the
  record it came from.

### G3 · Infrastructure — *have, plus a reconciliation*

- **Asserts:** what is declared is what is deployed.
- **Run:** the `infra` suite; `cdk synth`; `apply_iam.py --dry-run`,
  `apply_alarms.py --dry-run`, `apply_state_machine.py --dry-run`; then an
  **account reconciliation** covering tables, alarms (12), metric filters, IAM
  statements, schedule state, alias version, and `CodeSha256` equal across both
  Lambda functions.
- **Evidence:** the reconciliation table, taken from the account.
- **Pass:** zero drift, or every difference explained and recorded.

### G4 · Security — *RUN*

- **Asserts:** least privilege holds in the account and nothing regressed.
- **Run:** dump both roles' effective policies and assert no `dynamodb:Scan` on
  the orchestrator, no `DRAFT` guardrail grant, and append-only on
  price-history; `pip-audit`; the secret scan over the tree; confirm the applied
  Guardrail version is a numbered version rather than DRAFT.
- **Evidence:** the policy dump plus the assertions run over it.
- **Pass:** no finding. **Known accepted exception:** `CORS_ORIGIN=*` on the dev
  plane (ARCHITECTURE section 3h). It cannot be closed until there is an origin
  to name, so it is recorded as a release exception rather than as a pass.
- **Also recorded, not owned by this project:** the account holds four REST APIs.
  Two are ours (`woqmel35lk` serving, `crm1xkrk34` the CDK plane); `Chatbot`
  (`gxbx2006zc`) is the unowned resource from ARCHITECTURE section 3b, and
  `GroceryMockApi` (`loca1ylytf`) belongs to the frontend team. Naming them is
  the gate; removing them is not this task's call.

### G5 · Evaluation — *RUN, and the largest item*

- **Asserts:** T1 and T2.
- **Run:**
  1. **Re-score `classify_intent` live against the current 47-case suite for all
     three enabled models** — one run:
     `python evals/run_intent.py --compare nova-lite nova-pro claude-haiku`.
     The scorecards in `config/models.json` carry `_source: "30 cases"`. `docs/CI-GATE-HEALTH.md` section 1 names this exact
     citation as what the release gate should refuse.
  2. Re-run `run_meal_plan.py`, `run_prose.py`, `run_repair.py` and
     `run_recipe_select.py` against the current suites. **The meal-plan path
     changed on 2026-09-04** when 15c reached production, so every prior number
     describes a path that is no longer the one serving shoppers.
  3. **Task 3's qualifying live Guardrail RESULT**, which the acceptance targets
     record as still open.
- **Evidence:** new scorecards written into `config/models.json` with honest
  `_source` fields; eval reports under `reports/`.
- **Pass:** every enabled model scored against the current suite and every
  active route at 90% or better. **A score below a floor is a finding, not a
  failure of this gate** — what this gate asserts is that the measurement is
  current and honest, not that it flatters the service.

### G6 · Load — *RUN*

Two phases, because two different claims are being tested.

**Phase A — sustained at the ceiling.** Establishes T3 and T4 with a sample that
means something. The current baseline is n=8 and n=3, and `ARCHITECTURE.md`
section 3l says in terms: *do not quote these as qualification*.

- The mix is paced to the **binding Nova Lite budget**, not to a turn count.
  `scripts/measure_latency.py` was **fixed on 2026-09-04 to do this** — see the
  cross-check in section 9; its flat 9 turns/min default had become 27 Lite
  calls/min against a cap of 20, and would have measured throttling.
- Run at `--model-rpm 18` rather than the true quota of 20, leaving headroom: a
  meal plan that fires a repair costs **five** Lite calls, not three, and the
  pacing assumes three. Any run where `repair_attempts` is non-zero must say so
  next to its percentiles.
- Target **n of at least 50 per turn type**: about 100 turns, about 17 minutes
  at that pace.
- **Pass:** p95 price under 5s, p95 meal plan under 20s, p99 meal plan under
  25s, at least 99% successful, unhandled 5xx below 1%.

**Phase B — honest degradation above the ceiling.** The repository claims a
throttled call becomes an honest retryable error with nothing corrupt emitted
and no plan invented. That claim has never been watched.

- Deliberately breach the ceiling: about 40 turns inside 60 seconds.
- **Pass:** 100% of responses are contract-valid; every failure carries
  `retryable: true` with `UPSTREAM_TIMEOUT` or `INTERNAL_ERROR`; **zero**
  invented plans, zero malformed bodies, zero 5xx without a contract body.
- **Not** measured against T4 — that is the decision recorded in section 4.

### G7 · Privacy — *RUN*

- **Asserts:** T5, Req 11.5.
- **Run:** issue turns carrying **distinctive canary values** — an unusual
  dietary exclusion, a precise lat/lon, a recognisable phrase in the message —
  then search CloudWatch Logs (both log groups) and X-Ray traces for those exact
  strings.
- **Two surfaces the first draft named and this does not.** The review snapshot
  path is not live: the reviewer Runtime was prototyped and torn down, and
  nothing on the shopper path constructs it. And SNS notification *bodies* are
  not searchable after the fact — alarm text is generated by CloudWatch from the
  static descriptions in `config/alarms.json`, so that surface is verified by
  **inspecting those descriptions** for personal data, which is a read of a file
  rather than a search of a stream.
- **Evidence:** the canaries used and the searches run, with counts.
- **Pass:** zero occurrences. A search that finds nothing must be shown to have
  actually searched: record the number of log events scanned, not just the zero.
  A grep over an empty stream also returns nothing.

### G8 · Recovery — *RUN*

- **Asserts:** Req 11.7, and that recovery is a drill rather than a setting.
**Measured 2026-09-04, and this gate is larger than the first draft assumed:**

| Table | PITR | Encryption at rest |
|---|---|---|
| `grocery-products-dev` | ENABLED | AWS-owned key (DynamoDB default) |
| `grocery-idempotency-dev` | **DISABLED** | AWS-owned key |
| `grocery-price-history-dev` | **DISABLED** | AWS-owned key |

Req 11.7 asks for PITR "on all stored data", and `tasks.md` has been carrying
*"all-table PITR evidence remains in Pilot Tasks 6/9/16"* and *"idempotency
table created (owner fencing/PITR still open)"* since Task 6. **This is that
task.** Encryption needs no action: DynamoDB always encrypts at rest, and an
absent `SSEDescription` means an AWS-owned key rather than an unencrypted table.

- **Run:** **enable PITR on `grocery-idempotency-dev`** (decided 2026-09-04 — it
  holds claim state that cannot be recreated); **record price-history as an
  explicit Req 11.7 exception**, because PR #80 chose no PITR on the reasoning
  that every row is reproducible by re-running ingestion, and an exception has
  to be written down rather than left as a silent difference; **perform a
  restore drill** into a temporary table, verify row counts, then delete it;
  exercise the idempotency replay path; confirm the alias rollback (`live` back
  to version 11) is a single call.
- **Evidence:** the PITR state of all three tables before and after, restore
  timings, row counts, and the teardown.
- **Pass:** idempotency PITR enabled, the price-history exception recorded with
  its reasoning, the drill completed and the temporary table removed.

### G9 · Cost — *RUN*

- **Asserts:** T7.
- **Run:** compute cost per successful task from the `InputTokens` and
  `OutputTokens` EMF metrics over the load window multiplied by `cost_per_1k`;
  confirm the $25 Budget's 50/80/100% alerts exist and are subscribed; record
  the unit cost so that a regression over 20% is detectable next time.
- **Evidence:** a cost-per-turn table by turn type, with the token counts it was
  derived from.
- **Pass:** the number exists, is derived from measured tokens rather than list
  prices applied to guesses, and is recorded.

### G10 · MCP demonstration — *have, re-run*

- **Run:** `MCP_ENABLED=1 python scripts/mcp_server.py` with the parity test.
- **Pass:** the two coarse tools answer, default-off is respected, caps enforced.

### Optional managed services

Only services **actually approved and adopted** need parity, privacy, cost and
rollback gates. The AgentCore reviewer Runtime was prototyped and **torn down**,
and its CDK stack cannot deploy until the `AWS::BedrockAgentCore::Runtime` CFN
type reaches ap-southeast-2 — so it is not an adopted optional service and **not
a release prerequisite**. Recorded here so its absence is a decision rather than
an omission.

---

## 6. What this plan expects to expose

Stated in advance, because a gate battery that finds nothing usually means the
gates were chosen to pass:

1. **The intent scorecards will move.** They were measured on 30 cases; the
   suite is now 47 and includes cases added precisely because the old suite
   missed a live defect.
2. **The meal-plan numbers will move**, because 15c changed the path on the day
   this was written. Prior latency and eval figures describe a different
   service.
3. **CORS stays `*`** and will be a recorded exception, not a pass.
4. **The two service planes** — `woqmel35lk` (hand-made, serving) and
   `crm1xkrk34` (`Grocery-Service-dev`, CDK) — make "the deployed service"
   ambiguous. **These gates run against the serving plane**, and the CDK plane's
   parity is a separate question this battery does not answer.

---

## 7. Estimated cost and time

| Gate | Turns / calls | Est. cost | Wall clock |
|---|---|---|---|
| G5 re-score intent, 3 models | 141 calls | ~$0.13 | ~50 min (paced 9/min) |
| G5 other eval suites | ~150 calls | ~$0.20 | ~20 min |
| G6 Phase A | ~100 turns | ~$0.03 | ~17 min |
| G6 Phase B | ~40 turns | ~$0.01 | ~2 min |
| G2 / G7 / G8 live turns | ~30 turns | ~$0.01 | ~10 min |
| **Total** | | **about $0.40** | **about 1.5–2 h**, mostly pacing |

The dominant cost is **time, not money**, and the reason is the non-adjustable
quota. Nothing here threatens the $25 budget: the 50% Budget alarm would need
this battery run about thirty times in one month.

---

## 8. Execution order

1. **G1** offline at the release commit — cheapest, and a red here stops the rest.
2. **G3, G4** infrastructure and security — read-only against the account.
3. **G5** evaluation — the long pole, so start it early.
4. **G2** live-adapter.
5. **G7** privacy — needs turns, so it follows G2.
6. **G6** load, Phase A then Phase B. **Last of the live work**, because Phase B
   deliberately exhausts quota and would corrupt any measurement running beside
   it.
7. **G8** recovery, then **G9** cost — cost is computed from G6's window.
8. **G10** MCP.

**Nothing in this battery writes to the products table.** The only mutation is
G8's restore drill, which creates and then deletes its own temporary table.

---

## 9. The cross-check, and what it changed

The plan above was drafted, then reviewed against the account and the code
rather than against memory. Six things changed. They are recorded because a
plan that survived its own review unaltered usually was not reviewed.

**1. The load gate would have measured throttling, not latency.**
`scripts/measure_latency.py` paced at a flat **9 turns/min**. That was inside
the Nova Lite quota when a meal plan made two Lite calls; Task 15c made it
three, so the default had become **27 calls/min against a cap of 20** — and the
script runs all price checks first, so the entire breach lands in the meal-plan
half, at the tail, exactly where `THROUGHPUT-AND-SCALING.md` says throttling is
mistaken for slowness.

Fixed rather than worked around (decided 2026-09-04): pacing is now derived from
the per-turn model-call cost and the binding quota, so it re-derives itself the
next time the graph gains a call. `--rpm` still forces a flat rate, because
gate G6's second phase needs to breach the quota deliberately. Six tests in
`tests/test_measure_latency.py` pin the property, including one that fails if
the old 9/min default ever stops being a breach.

**2. Recovery is a bigger gate than drafted.** PITR is enabled on products only.
`tasks.md` has been carrying all-table PITR as open since Task 6 and pointing at
this task. See G8.

**3. Encryption needed no gate at all, and the first draft implied it did.**
DynamoDB always encrypts at rest; an absent `SSEDescription` means an AWS-owned
key, not a missing control.

**4. Two privacy surfaces named in the draft do not exist.** The reviewer is not
on the shopper path, so there is no live review snapshot; and SNS notification
bodies cannot be searched after the fact, so that surface is verified by reading
`config/alarms.json` instead.

**5. Two unowned REST APIs** live in the account and are now named in G4.

**6. Re-scoring three models is one command,** not three:
`run_intent.py --compare`.

Confirmed unchanged by the cross-check: the $25 budget exists with 50/80/100%
ACTUAL and 100% FORECASTED notifications, which satisfies T7's alerting half
before the battery starts.

---

## 10. Results — run 2026-09-04

Release commit `c0a7c83`, clean tree, against the serving plane (`woqmel35lk`).

| Gate | Result | Headline evidence |
|---|---|---|
| G1 Offline | **PASS** | 937 tests / 31 skipped at the time of the run, 52 infra assertions, pyright 0 over 125 files |
| G2 Live-adapter | **PASS** | 5/5 adapters live; T6 discharged |
| G3 Infrastructure | **PASS** | 11/11 reconciliation, zero drift |
| G4 Security | **PASS** | least privilege intact; one recorded exception |
| G5 Evaluation | **PASS** | all three enabled models re-scored on the 47-case suite |
| G6 Load | **PASS + finding** | p95 1.94s / 3.51s, 100/100; degradation defect found |
| G7 Privacy | **PASS** | zero canaries, with a positive control |
| G8 Recovery | **PASS** | PITR enabled; restore verified in 222s and torn down |
| G9 Cost | **PASS** | $0.000128 per turn |
| G10 MCP | **PASS** | 22 tests; default-off refuses; selftest exit 0 |

### G5 — the scorecards moved, as predicted

| Model | 47 cases | Recorded (30) | Cost/suite | p50 |
|---|---|---|---|---|
| Amazon Nova Pro | 97.8% (44/45) | 100.0% | $0.0649 | 6678 ms |
| Claude Haiku 4.5 | 97.8% (44/45) | 96.4% | $0.0868 | 6652 ms |
| **Amazon Nova Lite** (active) | **95.6%** (43/45) | 92.9% | **$0.0049** | 6672 ms |

Nova Pro's 100% did not survive the larger suite. Nova Lite, the route that
actually runs, improved — and is 13x cheaper than Nova Pro on a call made every
turn. All three clear the 90% floor. Written into `config/models.json` with an
honest `_source`; **T2 discharged.**

The three p50s sit within 26ms of each other, which is the Guardrail hop and
the harness network path, not a model property. Not latency evidence.

### G6 Phase A — the real latency baseline

| | n | p50 | p95 | p99 | Target |
|---|---|---|---|---|---|
| price check | 50 | 1.73s | **1.94s** | 2.36s | p95 < 5s |
| meal plan | 50 | 3.10s | **3.51s** | **6.30s** | p95 < 20s, p99 < 25s |

**100/100 turns succeeded**; zero unhandled 5xx. **T3 and T4 discharged** on a
sample that means something, replacing the n=8/n=3 baseline `ARCHITECTURE.md`
section 3l said not to quote.

Meal-plan p95 fell from **11.7-12.2s to 3.51s** — Task 15c replaced the Nova Pro
`generate_plan` call with deterministic assembly, so it made meal plans about
3.5x faster and 5x cheaper on the same day it reached production.

### G6 Phase B — the finding

At a deliberate 21x breach (480 model calls/min against a 20/min quota), the
stated criteria all held: **40/40 contract-valid bodies, zero malformed, zero
bodyless 5xx, 5/5 errors retryable, zero invented plans.**

But the gate's PURPOSE was to watch the service degrade honestly, and it did not.
24 turns carrying one unambiguous message — "feed 3 people for 5 days on $80":

| Terminal | Intent confidence | Count | Honest? |
|---|---|---|---|
| `clarification` | 0.45 (keyword fallback) | 14 | **no** |
| `error`, retryable | 0.9 | 8 | yes |
| `meal_plan` | 0.9 | 2 | — |

Throttle the FIRST call and `classify_intent` degrades to keyword heuristics
that extract no constraints at all, so `missing_plan_constraints` reads every
constraint as absent and the shopper is asked to rephrase a complete request.
The remedy offered cannot work: rephrasing does not fix a throttle. Throttle a
LATER call and the honest retryable error appears correctly — which is why this
went unnoticed, since the behaviour was right whenever anything had succeeded.

**Fixed in the same change.** `route_after_intent` now routes a degraded
meal-plan classification to `emit_upstream_failure` rather than
`emit_clarification`. Eight tests in `tests/test_degraded_intent_routing.py`,
two of which fail without the fix.

**And proven live.** v13 was published from `0467747`, tested by qualified
invoke, and the alias moved to it for a re-run of the identical breach against
the identical message:

| | v12 (before) | v13 (after) |
|---|---|---|
| `clarification` on a complete request | **14 of 24** | **0** |
| degraded turns (confidence 0.45) | asked to rephrase | **retryable error** |
| errors marked retryable | 5/5 | **14/14** |
| contract-valid bodies | 40/40 | **24/24** |

**The alias was then rolled back to 12**, deliberately: the frontend work is
unfinished and its cutover is where everything lands together. So v13 is built,
SnapStart-optimised, measured and NOT serving. Both functions' `$LATEST` carry
`0467747` and their `CodeSha256` still match, so the "one artefact, two
functions" check in G3 keeps passing — the divergence is between the alias and
`$LATEST`, which is what an alias is for.

Promote v13 with the frontend cutover. Until then the live service still tells a
throttled shopper to rephrase a complete request, and that is a known, recorded
state rather than an oversight.

### G9 — cost per successful task

213 turns, 412 model calls, 394,705 input and 15,134 output tokens over the load
window: **$0.0273 total, $0.000128 per turn.** The $25 budget buys ~195,000
turns; a 20% unit-cost regression is anything above $0.000154. The Budget's
50/80/100% ACTUAL and 100% FORECASTED notifications were confirmed present, so
**T7 is discharged**.

The denominator is turns PROCESSED, and Phase B's deliberately throttled turns
are in it. Phase A alone would be the cleaner unit cost.

### Exceptions and limits, recorded rather than absorbed

1. **`CORS_ORIGIN=*`** on the dev plane (section 3h). Cannot close until there
   is an origin to name. A recorded exception, not a pass.
2. **STALE_DATA was never exercised live.** The catalogue is dated 2026-08-28
   against a 45-day threshold, so nothing is stale until 2026-10-12. Covered
   offline; not demonstrated in the account.
3. **The ingestion log group contributed no privacy evidence** — zero events in
   the window, because nothing invoked it. Its zero is vacuous; the
   orchestrator's 63 events and 465,534 chars of X-Ray are the real evidence.
4. **Price-history has no PITR**, by the decision in PR #80 that every row is
   reproducible by re-running ingestion. An explicit Req 11.7 exception.
5. **Cost applies Nova Lite rates to all tokens** in the window, which the load
   mix justifies but which would understate a free-composition-heavy period.
