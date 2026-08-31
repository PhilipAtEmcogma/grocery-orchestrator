# Smart Grocery & Meal Budget Assistant — orchestrator

AWS AI Innovation Mentorship Workshop (AUT). Six-week sprint. This repo is the
**backend orchestration and AI layer**; teammates own the frontend chatbot and
the data/S3 side.

A conversational assistant for budget-conscious New Zealand shoppers: compare
grocery prices across Pak'nSave, Woolworths and New World, and generate meal
plans that provably fit a budget.

A second, subordinate objective is broad hands-on AWS learning, especially
Bedrock and AgentCore. Every service needs a product purpose, bounded scope,
acceptance evidence, security/cost controls, and rollback/removal criterion.
No learning objective may weaken the shopper invariants or turn a proposed
service into an implementation claim.

---

## Read these first

- `.kiro/specs/grocery-orchestrator/requirements.md` — numbered requirements in
  EARS notation. Cite these in commits and PRs.
- `.kiro/specs/grocery-orchestrator/design.md` — architecture, and **§8 lists
  what was decided against and why**. Read it before proposing an alternative.
- `.kiro/steering/tech.md`, `security.md`, `ai-quality.md` — hard constraints.
- `CONTRACT-v1.md` — the published interface the frontend builds against.
- `ACQUISITION-RISK.md` — terms-of-service assessment for live price
  acquisition (Task 7.9). **Read before touching acquisition.** §8 is the
  condition list Task 11.4 is gated on.
- `docs/OPEN-REVIEW-min-grams-per-person-day.md` — the one judgement in the
  planning path that has NOT had domain review. Self-contained, needs no
  code reading, and says what would change the answer. Read it if you know
  anything about food budgeting.
- `docs/THROUGHPUT-AND-SCALING.md` — the request-per-minute ceiling (10
  meal-plan turns/min, 5 with repairs), why it was accepted for workshop
  scale, and the two options for production with their costs. Never assume a
  Bedrock quota increase is available: Nova's request limits are not
  adjustable and Claude's are, which is the opposite of what most people
  guess. `scripts/check_quotas.py` answers it in one command.
- `docs/CI-GATE-HEALTH.md` — latent gaps in the gate: where it can go red for
  a reason unrelated to your change, and where a green local run does not mean
  a green CI run. Read before widening the evals or bumping a checker pin.
- `docs/ARCHITECTURE.md` — the **deployment record**: what exists in
  `ap-southeast-2`, its identifiers, the IAM shapes that took two attempts to
  get right, and two defects that only appeared once the thing was deployed.
  This file and the specs describe intent; that one describes an account. Read
  it before touching deployed resources, and before assuming a manual test
  result is fresh — the idempotency cache returns stored outcomes for a reused
  session/turn pair, which is how two fixes looked inert for an afternoon.

---

## The three invariants

These are the point of the whole design. Do not weaken them for convenience.

**1. No price may originate from model generation (Req 3).**
Enforced three ways, any one of which would suffice:
- *Topology*: generation nodes are unreachable except through
  `retrieve_prices`. No edge skips it.
- *Schema*: `PlanDraft` has no price field. The model returns citation refs
  and pack multipliers; every dollar figure is computed in Python.
- *Assertion*: two checks, and the split matters. `assert_grounded()` reads the
  response alone — declaration-before-use, ordering, and that source keys are
  SHAPED like keys. `assert_citations_match_retrieval()` compares every citation
  against the frozen `PriceRecord` retrieval kept for it: the ref was retrieved
  at all, table/pk/sk identify that exact record, and every published value
  equals the retrieved one. `run_turn` calls both.

  **Shape is not identity, and for a long time only shape was checked.** A
  citation naming the right table with a plausible partition key and a price
  nobody retrieved passed cleanly, so the central claim rested on no code path
  fabricating one rather than on a check that would notice. Closed 2026-08-29
  (Req 3.5–3.6) with 19 tests and five negative controls in `validate.py`.

For prose, the model writes `[[c1]]` placeholders and rejects model-supplied
money. Pilot Task 2 changed rendering to non-monetary product/store labels,
removed literal money from comparison reasoning, and regenerated samples.

The prose node checks for money TWICE: once on the model's template, and again
on the rendered string, because placeholders are expanded between the two and
the text the user reads is not the text that was validated. Both are inside the
node's try, so a failure degrades — the sentence is dropped and the cited table
still ships.

**Where money is rejected, and what happens when it is found.** One rule; the
consequence depends on who wrote the text and whether it is essential. That
split is what Req 3.7 states, and neither this file nor `tasks.md` implemented
it until the two were reconciled:

| Text | Author | On a violation |
|---|---|---|
| Prose sentence (`TokenEvent.text`) | model | drop the sentence, ship the table (`generate_prose`) |
| `Meal.name`, `Ingredient.item`, `Ingredient.qty` | model | validation error → bounded repair → `emit_plan_generation_failed` |
| Comparison reasoning, notice messages | code | CI, via `validate.py` over `samples/` |
| `ErrorEvent.message`, `NoDataEvent.message` | code | **not checked, deliberately** |

`run_turn()` calls `assert_no_model_authored_money()`, NOT the wider
`assert_no_literal_money_in_response()`. The narrow one covers the plan's
model-authored free text and can only fire on a bug, because `validate_plan`
already rejects those fields and the router discards a plan that never came
back clean. The wide one also covers prose, and raising on prose in `run_turn`
would turn "you lose the sentence" into "you lose the turn" — contradicting
the rule in `tests/test_prose.py` that a table with no sentence beats a
sentence with a wrong price. `validate.py` runs the wide one over `samples/`
in CI, which is where it earns its keep.

**This file previously claimed prose was "the only model-authored user-visible
text in the graph". That was false**, and the false claim was the argument for
leaving the plan's own text unchecked. `DraftMeal.name`,
`DraftIngredient.item` and `DraftIngredient.qty_display` are model-authored
free text that `assemble_plan` passes through untouched. A plan naming a meal
`Budget Pasta — only $4.99 a head` with an ingredient `Butter (was 7.50, now
5.00)` cleared `assert_grounded`, `assert_arithmetic` and
`assert_no_literal_money_in_response` together, shipping a fabricated "was"
price. `SYSTEM_PROMPT` already said "NEVER state a price"; nothing verified
it, and an instruction a model can ignore is precisely what this codebase
replaces with a check everywhere else.

The two exclusions are exclusions, not oversights. `ErrorEvent.message`
restates the user's OWN budget — "I couldn't build a plan within $15" — the
constraint they supplied, not a price we are claiming, and dropping it makes
the refusal harder to act on. `NoDataEvent` echoes their search term; a
blanket check there would also let a user fail their own turn by typing a
dollar sign. Both are safe only while those messages stay code-authored.

`LITERAL_MONEY` has exactly one definition, in `src/schemas/contract.py`;
`src/prompts/prose.py` imports it. It used to hold a byte-for-byte equivalent
copy — equivalent copies being the dangerous kind, since nothing is wrong and
so nothing flags the day one of them is tuned.

**2. Honest failure over plausible answers (Req 4).**
- No confident match → return nothing, never the nearest match. Substring
  matching once resolved "truffle oil" to canola oil.
- No data → a `no_data` success outcome, not an error.
- Budget impossible → say so with alternatives; never ship the failing plan.
- Every failure path returns a contract-valid response. No bare 500s.

**3. Dietary exclusions are safety-critical (Req 5).**
- Additive, never overridden. Message plus hints, union of both.
- **Restated on every regeneration.** Model calls are stateless; an
  instruction to "keep all exclusions" without naming them is unfollowable.
  This was a real bug the eval harness caught.
- Verified against retrieved products, not against what the model claims.
- **Mapped from a reviewable table, or refused.** The mapping from user
  terms ("vegan", "no dairy") to fixture categories lives in
  `src/graph/dietary.py`. Anything unmapped — "gluten-free" against a
  fixture with no gluten tag — routes to `emit_dietary_unsupported` and
  returns `UNSUPPORTED_EXCLUSION`. Filtering an incomplete map would
  produce a plan we cannot verify; silent drop is the exact shape of the
  bug that used to serve dairy to a vegan user, so it fails closed.

---

## Architecture

```text
API Gateway REST -> published Lambda alias (zip + SnapStart)
                 -> deterministic LangGraph -> Bedrock Guardrail + DynamoDB
```

The published alias, API controls, and CDK definitions are approved targets,
not current deployment claims. The reference workflow itself is implemented.
It remains a **workflow, not an autonomous agent**: models make bounded
judgements at fixed nodes and code owns retrieval, routing, validation, repair,
and final emission.

```text
validate_input -> classify_intent
                    +-- meal_plan + unsupported exclusion -> emit_dietary_unsupported
                    +-- meal_plan + missing constraint ---> emit_clarification
                    `-- retrieve_prices
                         +-- no citations -------> emit_no_data
                         +-- all prices stale ---> emit_stale_data
                         +-- budget impossible --> emit_budget_infeasible
                         +-- price_check -> generate_comparison -> generate_prose
                         `-- meal_plan -> generate_plan -> validate_plan
                                          ^ bounded repair (2) |
                                          `--------------------'
                                                              |
   over budget after repairs -> emit_budget_infeasible <------+
   drafts never validated ----> emit_plan_generation_failed <-+
   model unreachable ---------> emit_upstream_failure <-------+
```

The repair arrow is `repair_plan`, which only increments the attempt counter;
regeneration happens on the loop back. Every path ends at `finalise`, which
always emits `done` — including after an error. The four terminals differ
because they are four different facts: the budget genuinely does not stretch,
we could not build a plan we trust, the model plane failed, or the request was
impossible before we started.

**Location and freshness are repository parameters, not post-filters.** `near`
and `freshness` go into `cheapest_for_product` and `candidates_for_budget` and
are applied BEFORE the limit, by all three implementations. Filtering the
returned list instead would drop an in-radius, in-date price behind five that
are neither, and the graph reads an empty list as `no_data` — the same
truncation defect Task 6 fixed for the store filter.

Freshness is judged against an INJECTABLE date (`FRESHNESS_AS_OF`), not the wall
clock. The committed fixtures are a snapshot with a fixed capture date; judged
against today they rot, and the meal-plan eval drops to 18% for reasons nothing
to do with the code. `pin_to_fixture_snapshot()` derives the date from the
fixture data and is called by the evals, the demos and the dev server.
Production sets nothing.

**Three protocol boundaries** make everything testable without AWS:
- `src/retrieval/base.py` — `PriceRepository`; fixture and DynamoDB
  implementations exist.
- `src/models/base.py` — `ModelClient`; scripted and live-verified Bedrock
  implementations exist.
- `src/observability/base.py` — `Telemetry`; no-op locally and Powertools at
  the handler boundary.

### MCP, AgentCore, and bounded agents

Pilot Task 8 is DONE (2026-08-30). `src/mcp/` exposes two coarse tools over
stdio JSON-RPC — `grocery_ask` and `grocery_dietary_terms` — which invoke the
complete deterministic service through the same `lambda_handler` API Gateway
calls, and expose no raw DynamoDB, AWS SDK, filesystem, network, scraping,
write, citation, or unguarded-generation primitive. Default-off
(`MCP_ENABLED=1`), rate and session capped, audit that records that a call
happened and never what was asked.

**If you add a tool, it must invoke the whole service.** A fine-grained tool
("query the products table") would be a database with extra steps, and every
invariant this project has lives ABOVE that layer. And keep stdout clean: it is
the protocol channel, and the service's own Powertools output goes there by
design — `serve()` rebinds `sys.stdout` to stderr before importing the handler
for exactly that reason.

Proposed ADR 0002 would permit two separately approved stages if a mentor
approves it. AgentCore Gateway with Identity and Policy could mediate the same coarse tools after local parity,
identity/WAF/Cognito, cap, audit, cost, and rollback evidence; it is never a
bypass around LangGraph. A separately deployed AgentCore Runtime reviewer may
read only capped sanitised ingestion snapshots and emit cited schema-checked
findings for deterministic validation and human approval. It receives no
shopper PII and has no write, publication, or shopper-path authority.

Bedrock Model Evaluation and AgentCore Evaluations may accompany local evals,
not replace them. Companion services follow ADR 0002's purpose/evidence/removal
matrix. ADR 0002 is proposed and mentor approval is required; ADR 0001 remains
controlling until then. Moving the shopper meal path to AgentCore Runtime is a
separate p99 contingency and approval.

**Observability stops at the handler.** Powertools' Logger, Tracer and Metrics
are wired in `src/handler.py`, and `src/observability/powertools.py` is the
only other module that imports the library. Protocol wrappers carry tracing
inside the graph without node imports.

**Model plane, not a Claude endpoint.** Nodes request a task and the registry
routes from `config/models.json`.

**A model may not serve a task it was never scored on.** Scorecards are data in
that same file, and `ModelRegistry.unscored_routes()` is the gate: it walks
every task and every model that could actually reach it — the `prefer` list AND
the cheapest-first `available(tier)` fallback, which is the part that bites —
and reports any pair with no qualifying evidence.
`tests/test_multimodel.py` fails the build when it is non-empty, so adding a
model, enabling one, or adding a task forces a scorecard or an explicit,
reasoned exemption.

Both previously unmeasured tasks now have suites, and **as of 2026-08-30 every
task is gated — `unscored_tasks()` is empty for the first time.**
`evals/run_prose.py` scores prose-protocol compliance; `evals/run_repair.py`
scores the repair pass, budget and defect kinds separately.

Repair was ungated because six cases could not support a threshold — all three
routable models scored 83.3%, each failing a different case. **The fix was to
expand the suite to twelve, not to lower the bar.** Every case was then verified
to DISCRIMINATE against a model built to fail it, and re-measured over three
reps per model. The failures turned out to be structured and opposite: Nova Lite
91.7% (perfect on budget repair), Claude Haiku 83.3% (perfect on defect repair,
71.4% on budget). The six-case reading of "variance" was right about that suite
and wrong about the models.

**A routing rule can now say a model MUST NOT serve a task.** `exclude` in
`config/models.json`'s routing rule is honoured by `route()` and by
`routable_models()` alike -- they must agree, or the qualification gate reports
a pair no turn can reach and a gate that cries wolf gets switched off.

It exists because per-task scoring implies per-task exclusion and the config
could not express it. A model can clear the floor on one task and fall below it
on another; `available(tier)` would still hand it the second task as a
cost-ordered fallback, and `enabled: false` was the only lever -- which removes
the model everywhere, including from tasks it is good at. `claude-sonnet` only
fitted that lever because it was unfit for everything.

`unevidenced_models()` stops the remaining exemption becoming a hole — a model
may be unscored for a task nobody gates, but not unscored everywhere and still
routable.

**A repair prompt must never read as an attack.** It is assembled entirely from
our own code, config and validation errors, so `run_repair.py` scores a
Guardrail block as a FAILURE rather than excusing it the way the intent and
prose suites do. That rule exists because `build_defect_repair_prompt` shipped
as a stack of imperatives ("Never write a price ... ANYWHERE") and PROMPT_ATTACK
refused every defect repair against a live model, while offline tests stayed
green because the scripted client has no guardrail.

### Current pilot blockers

Do not describe this as pilot-ready. Pilot Tasks 1–7 are done and evidenced,
including the live Guardrail result.

**A service IS deployed**, and this section said otherwise until 2026-08-30.
REST API `woqmel35lk` (`grocery-orchestrator-api-dev`), stage `dev`,
`POST /dev/chat`, wired to alias `grocery-orchestrator-dev:live`, plus the
ingestion Lambda, state machine and an ENABLED daily schedule. Verified
answering on 2026-08-30. `docs/ARCHITECTURE.md` §3 had the identifiers right
since 2026-08-27; this file, the README and `infra/docs/00` all contradicted it
and were all wrong. **When a document describing intent disagrees with the
deployment record about an account, the deployment record wins — and the fix is
to check the account, not to pick a document.**

What is genuinely absent is the CDK stack, and with it any reproducibility,
drift detection, or deployed SLO/cost/recovery evidence. No latency or
throughput figure in this repository has been measured against the endpoint
under load; they are all laptop measurements.

**The deployed code is current as of 2026-08-30** — the alias moved off version
`5` (2026-08-27, predating Tasks 4–7) and has been republished several times
since; `docs/ARCHITECTURE.md` §3a holds the history and deliberately does not
say which version is live. The `$0`-budget defect is gone. Still: **check which
version the alias points at before quoting a live behaviour as current** —
`aws lambda get-alias --function-name grocery-orchestrator-dev --name live` —
and cut over the way
`docs/ARCHITECTURE.md` §3a describes — publish, wait for SnapStart, invoke the
new version *directly*, and only then move the alias. `build_lambda.py` cannot
verify its own archive on Windows, so the first thing to execute a locally built
zip must not be live traffic.

**`max_price_age_days` is 45, not 14, and that is a dated decision rather than a
calibration.** Enforcing freshness on fixtures dated 2026-07-31 made every priced
query return `STALE_DATA`; the threshold was raised on 2026-08-30 as a reversible
dev-stage stopgap so the deployed endpoint can demonstrate something. Full
reasoning and the revert condition are in `config/freshness.json`
(`_decision_2026_08_30`) and `docs/ARCHITECTURE.md` §3c. **Return it to 14 when
real ingested prices land, and never carry 45 into a stage a shopper can reach.**
**Do not "fix" staleness by re-stamping a capture date**: those prices were
invented on 2026-07-31, and a later stamp fabricates provenance — the *Do not*
rule about publishing a price without its capture date, wearing a different hat.

**`config/` ships inside the Lambda archive**, so retuning a threshold is a
deploy. That is the argument for Task 7b's SSM work.

Blockers are now **Tasks 13, 15c and 16**, plus Task 14's Runtime behind ADR
0002. Closed 2026-08-30: Task 8 (local MCP, `src/mcp/`), Task 12 substantially
(8 alarms, dashboard, Budget, first deployed latency and cost baselines), Task
13's first half (the real 2,759-row catalogue is loaded), and deferral 6b
(GSI2). Closed 2026-08-30/31: **Tasks 9–11** (two CDK stacks deployed, tables
adopted by reference, service plane at verified parity — the cutover is
deferred by decision, `ARCHITECTURE.md` §3m), **Task 14a** (the reviewer's
boundary), and **Task 15b** (29 curated recipes, 29/29 costable against the
real catalogue, assembled by `src/recipes/planning.py`).

**Task 15 is no longer blocked on data; 15c is blocked on nothing but work** —
the selection prompt, the graph branch, and an eval suite. The *imported* 175
recipes remain unusable and that is now measured against both catalogues: 0 at
100% against `datasets/` (best 75%, median 17%) and against `fixtures/` (best
75%, median 12%). Until 2026-08-31 only the fixture figure existed while every
document described the real catalogue — see *Name the catalogue a measurement
used* below. AgentCore and the managed-evaluation stages remain proposed, not
built.

Two deliberate deferrals carry their reasoning in `tasks.md`: a bare
`price of mushrooms` is still refused by the Guardrail (3d), and SSM routing
waits for the CDK stacks (7b). **6b closed 2026-08-30** — candidate retrieval
queries GSI2 (category / zero-padded price) instead of scanning, chosen on the
load evidence the deferral required.

**2026-08-31: gates went under the last fortnight's work, and two of the three
capabilities it added turned out to need them.** `infra/` had no CI job of any
kind — no `tsc`, no `jest`, no `cdk synth` — while `service-stack.ts` was the
file that DEFINES the security posture, and its test suite was `describe.skip`
under a header calling the deployed stack a stub. Running it found
`dynamodb:Scan` reintroduced on the products table by `grantReadData()`, plus
`DeleteItem` on idempotency, plus two assertions that verified nothing.
`implausible_unit_price` had been written, tested and called by nothing, so the
one defect class known to have reached the live table was still undetected;
wired into ingestion and run over the real catalogue it rejects 0 rows clean and
**522 of 2,759** with the historical defect reintroduced — not the six the
incident is usually quoted as, because six was the size of the *fixture* set.
Details in `docs/ARCHITECTURE.md` §3o–§3p.

**15c is still the differentiating capability and is still not on the shopper
path.** That is the remaining half of what the second audit asked for.

---

## Conventions

- **Money is `Decimal` in Python, string on the wire and in storage.** Never
  `float`. The numeric DynamoDB type round-trips through float in most paths.
- **User input is untrusted.** Delimited in prompts; guardrail input tagging
  via `src/models/guardrail.py`. Note the prompt-attack filter does *nothing*
  without tagging.
- **Nodes are `state → partial state`.** Pure functions, independently
  testable.
- **The compiled graph is memoised, so clear the cache if you patch a node.**
  `compiled_graph()` in `src/graph/build.py` keys on the `(repo, model)` pair —
  the compile measured 13.4 ms and was 78% of an offline turn, on a path where
  the handler already caches both dependencies. A graph resolves its node
  functions from `src.graph.nodes` AT BUILD TIME, so a test that monkeypatches
  one is only testing anything if the graph is built after the patch.
  `tests/conftest.py` calls `clear_graph_cache()` around every test so nothing
  depends on that by accident; anything outside the suite has to do it itself.
  Never key that cache on anything but identity — `InMemoryPriceRepository`
  takes a fixture path, so collapsing two would answer a turn from the wrong
  catalogue with every assertion passing.
- Python 3.13, region `ap-southeast-2` (Sydney).
- Line length 100. Ruff with bandit (`S`) rules enabled.

### Name the catalogue a measurement used

**A coverage or resolution number is not quotable unless the thing it resolved
against is named in the same breath.** `scripts/check_recipe_coverage.py`
resolved through `InMemoryPriceRepository()` — the 26-product fixture file —
while `src/recipes/base.py`, `tasks.md` and the README all described the
measurement as being against the real 2,939-row catalogue. Three documents
agreed with each other and none of them agreed with the code, and a blocking
decision on Req 2.9 rested on the result.

Worse, the forcing test guarding that decision had the same defect. Its
condition is "the product catalogue grew enough to price whole recipes" and it
watched the **fixture** catalogue, which `scripts/generate_fixtures.py`
regenerates to a fixed shape and which therefore cannot grow. The trigger was
unreachable. It read as a working control and guarded nothing — the same shape
as the secret scan that never ran and the privacy test that read one stream.

So: `src/recipes/catalogue.py` holds a `Catalogue` that carries its own source
path and size and `describe()`s itself; the script prints both catalogue and
recipe-set identity on every run and **refuses to gate** (`--fail-under`) from
the fixtures when the real catalogue is available; and `tests/test_recipes.py`
asserts both catalogues reach the same conclusion rather than trusting one.

The conclusion did survive, which was luck and not evidence. If you add a
measurement here, make the instrument name its inputs.

---

## Commands

```bash
python -m pytest -q                              # 841 passed, 31 skipped, no AWS
ruff check . && ruff format --check .            # both gated in CI
python validate.py                               # contract samples + grounding
UPDATE_FIXTURES=1 python -m pytest \
    tests/test_sample_fixtures.py                # rewrite samples/ from the server
python evals/run_intent.py                       # 76.7% scripted baseline
python evals/run_intent.py --model nova-lite     # 92.9% live, guardrail v2
python evals/run_intent.py --model nova-pro      # 100% live (Nova Pro)
python evals/run_meal_plan.py                    # 100% invariants baseline
python evals/run_guardrail.py                    # must_allow structural (scripted)
# EXPERIMENTAL/non-qualifying: --model does not yet pin the requested model
python evals/run_guardrail.py --model nova-lite
python scripts/generate_fixtures.py              # regenerate seed data
python scripts/generate_synonyms.py              # rebuild the generated synonym block
python scripts/generate_synonyms.py --check      # CI/gate: is that block current?
python scripts/generate_synonyms.py --candidates butter milk   # what a head term could mean
python scripts/dev_server.py                     # localhost:8000 for frontend
python scripts/apply_guardrail.py --dry-run      # validate guardrail policy
python scripts/build_lambda.py                   # build/lambda.zip, ~30 MB unzipped
python scripts/apply_iam.py --dry-run     --config config/iam-<role>.json              # execution roles, policy-as-data
python scripts/apply_state_machine.py --dry-run  # ingestion Step Functions
python -m pytest tests/test_ingestion.py         # ingestion; no AWS
python scripts/check_quotas.py                    # throughput ceiling, live
python Philip_demo/run_all.py                     # nineteen demos, offline; DEMO_MODE=aws|integration
MCP_ENABLED=1 python scripts/mcp_server.py        # local read-only MCP over stdio
python scripts/check_recipe_coverage.py --missing 20   # imported recipes vs the REAL catalogue
python scripts/check_recipe_coverage.py --recipes curated              # 29/29 costable
python scripts/check_recipe_coverage.py --recipes curated --catalogue fixtures  # 14/29
python scripts/measure_latency.py                 # latency against the DEPLOYED endpoint
python scripts/check_ingestion_anomalies.py       # deterministic rules over the REAL catalogue
python scripts/check_ingestion_anomalies.py --catalogue fixtures
cd infra && npm ci && npm test                    # 24 CDK security assertions, no AWS
cd infra && npx tsc --noEmit && npx cdk synth --quiet   # what the `infra` CI job runs
```

The pre-commit hook lives in `scripts/hooks/pre-commit` — **version
controlled**, not in `.git/hooks`. A fresh clone must enable it once:

```bash
git config core.hooksPath scripts/hooks
```

It runs everything CI runs that is fast and offline: ruff, **pyright**, pytest,
the secret scan on staged files, contract and grounding validation, guardrail
policy validation, fixture drift, and both eval floors. About ten seconds — the
type check is roughly four of them. It first puts the project venv on PATH and
prints which one — see *Tool version drift* below for why that step exists.

**Types are a gate, not a suggestion.** `pyright` is pinned in
`requirements-dev.txt` and configured once in `pyproject.toml` under
`[tool.pyright]`, so the hook, CI and your editor check the same files under
the same rules — it is the engine Pylance embeds, so a failure here is the
error already underlined in your editor. Ruff does not check types, and the
gap was not theoretical: the `Telemetry` protocol declared `span()` as
returning `None` while both implementations returned a context manager, so
neither satisfied the protocol that exists to define them. Ten errors, and
neither ruff nor the tests could see any of them, because a Protocol is
verified statically or not at all — `isinstance()` against a runtime-checkable
Protocol only tests that attribute *names* exist, never their signatures.

That is also why `src/observability/base.py` and `powertools.py` carry
`_..._conforms:` bindings under `if TYPE_CHECKING:`. Assigning an
implementation to a protocol-annotated name is the thing that makes a checker
verify it; without one, a protocol nothing is assigned to is checked by
nobody. Add one for any new protocol implementation.

**It deliberately does not run `pip-audit` (~16s, needs network) or the Lambda
package build.** Those stay in CI, and the hook says so on every run, so a pass
means "CI will not fail for any of the fast, offline reasons" — not "CI will
pass". Keep that list honest if either side changes.

CI (`.github/workflows/ci.yml`) runs the same checks plus those two — **no AWS
credentials needed anywhere**, which is a design outcome of the protocol
boundaries.

### A CDK grant helper ADDS to a policy; it does not check one

`ServiceStack` builds the orchestrator role from
`config/iam-orchestrator-role.json` verbatim, and then called
`tables.products.grantReadData(role)` two constructs later. That helper does not
compare itself to the JSON — it appends a second statement using the CDK's idea
of "read", which includes `dynamodb:Scan`, widens explicit index ARNs to
`index/*`, and adds Streams permissions on a table with no stream.
`grantReadWriteData` on the idempotency table added `DeleteItem`, against a
config comment reading "No Delete -- expiry is by TTL".

**Pilot Task 6b removed the Scan on 2026-08-30. The CDK deploy put it back on
2026-08-31, in a plane that was already serving.** The config file's own comment
had called it: *"a Scan permission nothing needs is a Scan somebody can
reintroduce without noticing."*

So: **policy-as-data means the data is the whole policy.** If a stack loads a
policy from `config/`, it must not also grant. `infra/test/service-stack.test.ts`
asserts the action set per resource and fails on any statement the JSON does not
declare — not by grepping `JSON.stringify`, which is how two of that suite's
original assertions managed to be a false positive and a false negative at once.

### A skip must carry a condition, not a sentence

`@pytest.mark.skipif(not DATASET.exists(), …)` stops skipping the moment the
dataset is checked out. `describe.skip` under "SKIPPED until ServiceStack is
implemented" never stops, because nothing evaluates the English — and that one
sat over seven security assertions for a day after the stack was deployed, by
which time one of the assertions had inverted.

`tests/test_skip_markers.py` fails on any skip without a machine-checkable
condition, in Python and TypeScript alike, and refuses `.only` for the same
reason — it silently disables every other test in the file, so the suite shrinks
without the word "skipped" appearing anywhere. Use
`(cond ? describe : describe.skip)(…)` in TypeScript, or delete the suite.

### Do not quote a deployed version number in prose

Four numbers described one Lambda alias across three documents at once — `7`,
`9` and `11`, with one table header saying "v6 (now)" directly under prose
saying 7. None was wrong when written. `docs/ARCHITECTURE.md` §3a now holds the
version HISTORY, which cannot go stale, and deliberately does not say which
version is live. `aws lambda get-alias --function-name grocery-orchestrator-dev
--name live` does.

### Tool version drift is a known failure mode here

**If a check fails locally but CI is green — or passes locally and fails in CI
— suspect the tooling before the code.** This has cost the project real time
three times: a `py` launcher selecting a different interpreter, a stray
`runner.py` earlier on PATH, and a ruff 0.13.3 from an *unrelated project's
venv* reporting an S603 on `scripts/build_lambda.py` that this project's ruff
does not raise and CI has never seen. The last one produced a detailed
investigation of a defect that did not exist.

The failure is nastier than an ordinary red build because it is bidirectional.
A gate reporting on tools other than the ones CI runs yields both false
failures and false passes, and nothing on the surface distinguishes them.

Two mitigations, and they cover different halves of the problem:

- **`ruff` is pinned in `requirements-dev.txt`.** Unpinned, CI installs
  whatever is newest that day, so a linter release can turn `main` red with no
  repo change. Pinning fixes *which version* the project means.
- **The hook puts `.venv/Scripts` (Windows) or `.venv/bin` (POSIX) first on
  PATH.** Every tool it calls is invoked bare, so without this it lints and
  tests with whatever venv the shell happens to have active. This fixes *whose
  copy* gets run. If no `.venv` exists it warns and falls back, because a gate
  that refuses to run teaches less than one that runs and says what it could
  not guarantee.

Neither helps if you run the tools by hand. `ruff check .` in a shell with
another project's venv active is measuring that project. Check
`(Get-Command ruff).Source` or `command -v ruff` before believing a surprising
local result.

### When verifying that a gate FAILS, assert on the failure, not the exit code

**A non-zero exit can mean the tool never ran.** Checking that a gate catches
something is only worth doing if the check can tell "it caught it" apart from
"it crashed", and an exit code alone cannot.

This produced a false green while verifying the secret scan. The verification
planted a credential and asserted a non-zero exit — but it invoked the scan
through `bash -c`, and on Windows `bash` resolves to the WSL launcher, which
failed to start and exited non-zero. Every planted-credential check passed
without detect-secrets running once. The bug was only visible because a later
assertion in the same run expected exit *zero* and got the same WSL error.

So assert on the content: which file the finding names, which rule fired. Two
supporting habits, both of which would have caught this one on their own:

- **Check the input, not just the output.** The corrected version asserts the
  scan was handed the expected number of files — 91 for the whole tree, 2 for
  the staged set — because a scan of nothing also exits zero.
- **Prefer invoking the tool directly over routing it through a shell.** The
  fix was to build the file list in Python and call `detect-secrets-hook` with
  it, which removed the failure mode rather than detecting it.

The general form of this is the same one the section above is about: a signal
that looks like the one you wanted, produced by something else entirely.

**Check `git status` after a mutation run, even when the content is
byte-identical.** A scratch harness that plants a defect and restores the
original with Python's `pathlib.write_text` does not restore the original on
Windows: text mode translates `\n` to `\r\n`, so every restored file comes back
CRLF. It happened here to `src/runner.py` and `src/graph/nodes/plan.py`, and
the content diff was empty — only the line endings moved. `.gitattributes`
normalises on commit so nothing reached history, but the files sat modified in
the tree, and an unexamined `git status` before `git add -A` is how that gets
committed on a repo without that safety net. Pass `newline="\n"` when a harness
writes a file back, and glance at `git status` before staging regardless.

This is the sixth finding in a row of one shape: a control that looked like it
was working — a privacy test that read one stream, a secret scan that never
ran, a protocol nothing checked, a restore that did not restore. Assume the
check is the thing that is broken until you have watched it fail.

**`main` is protected. Direct pushes are rejected, for everyone.** Work on a
branch and open a pull request; the `All checks` job must pass before merge.

---

## Eval discipline

Prompt changes are unmeasured until the eval suite has run. Record the score
before and after in the commit message.

- Cases marked `known_gap` are reported separately and **never counted**. Do
  not delete one to raise the score.
- Do not edit an expectation to match observed model output without saying so
  explicitly. **Done once, on 2026-08-28:** `plan-001` ($30 -> $40) and
  `plan-005` ($25 -> $35) in `evals/cases/meal_plan.json`, each carrying a
  `note` explaining why. Not because the planner underperformed — because the
  targets were arithmetically impossible. `plan-001` asked for 3 people over 7
  days on $30, which is $1.43 per person per day, while the cheapest eight
  distinct packs in the catalogue cost $15.13 and half a pack cannot be
  bought. Both cases passed only while `within_budget` was computed from
  fractional consumption instead of money payable. Raising a target that the
  arithmetic rules out is not the same as lowering a bar the code failed to
  clear; the second is still forbidden.
- Never lower a CI floor to make a build pass. Raise one when the baseline
  genuinely improves.
- The meal-plan budget check is **two-sided**: `min_budget_used` catches
  under-feeding, which `within_budget` alone would pass.
- Bedrock Model Evaluation and AgentCore Evaluations are complementary evidence,
  never replacements for local tests, golden sets, negative controls, or the
  90% task floor. Record model/profile, region, prompt, dataset, evaluator,
  per-case, trace, latency, token, cost, and S3 object-version provenance.
- **A single live run does not qualify a model.** The meal-plan suite is 11
  cases against a non-deterministic model: repeated runs of Claude Sonnet 4.5
  on an unchanged suite returned 73%, 64% and 55%. That ±18-point spread is
  wider than the gap between candidate models, so one run can neither clear
  the 90% floor nor rank two models. Repeat each model and record the band,
  not a point estimate. (Those three figures came from a scorer since found
  wrong in three ways — see the model evidence section. The lesson holds;
  the numbers are kept only as an illustration of spread. Note also that
  back-to-back reps throttle the account and the throttling reads as poor
  quality: cool down between them, and discard any rep with upstream
  failures rather than averaging it in.)
- **Separate infrastructure failure from model quality before scoring.** A run
  where the model was never reached is not a low score, it is a void
  measurement — `run_meal_plan.py` now aborts on a total outage and returns
  exit code `2` (inconclusive) from `--min-pass-rate` when any case failed
  upstream. Never report or compare a rate carrying upstream failures.
- Running any eval against a live model requires `BEDROCK_GUARDRAIL_ID` in the
  environment; `REQUIRE_GUARDRAIL` defaults on and fails every call closed
  without it. See the README's "Running an eval against a live model".

---

## Do not

- Use Bedrock Agents Classic, `CreateAgent` or `InvokeInlineAgent` — in
  maintenance mode since 30 Jul 2026, closed to new accounts.
- Suggest `ap-southeast-6` (Auckland) — no AgentCore, no SnapStart.
- Containerise the orchestrator Lambda — forfeits SnapStart, which is
  zip-only. Measured dependency size fits well under the archive limit.
- Loosen `resolve_product_key` to fuzzy matching. Under-matching is
  recoverable; mis-matching produces a confident wrong price.
- Hardcode an AWS account id into `config/`. This repo is public. Use
  `${AWS_ACCOUNT_ID}` / `${AWS_REGION}`; `scripts/apply_*.py` resolve them
  from STS at apply time, and `tests/test_config_placeholders.py` fails the
  build if a literal twelve-digit id reappears.
- Add a price field to any model output schema.
- Use Lambda Function URLs for streaming — loses throttling, usage plans and
  auth.
- Trust model-reported constraint compliance. Verify against retrieved data.
- Import `aws_lambda_powertools` outside `src/observability/powertools.py`
  and `src/handler.py`. It ends the no-AWS property CI depends on.
- Replace the idempotency implementation with Powertools' utility. Ours has
  session-scoped keys, payload fingerprinting, in-flight detection and
  terminal-only caching, each deliberately tested — design.md §12.5.
- Log an exception object, or call `logger.exception()`. A traceback ends
  with `str(exc)`, and a pydantic `ValidationError` embeds the rejected
  input — which is the user's message. Use `exception_fields()`.
- Point acquisition at live retailer sites. Task 11.4 is gated —
  `ACQUISITION-RISK.md` §8. Build against fixtures and recorded responses.
- Run ingestion at the products table without diffing first. `refresh()`
  reports `added`/`changed`/`unchanged` on every run and takes
  `{"dry_run": true}`; use it whenever the normaliser changed. Skipping this
  is how `unit_price_nzd` became "2490.00" on a $2.49 item across six live
  rows with no signal — `docs/ARCHITECTURE.md` §8.
- Circumvent a retailer's technical controls — bot mitigation, rate limits, an
  undocumented internal endpoint. This is the one path in the whole assessment
  with criminal exposure attached (§4.2). A block is an answer, not an
  obstacle.
- Request the Foodstuffs search endpoints. Their `robots.txt` disallows them;
  the published product sitemaps are the sanctioned traversal.
- Publish a price without its capture date. Fair Trading Act exposure attaches
  to our comparison, not to the retailer's price (§4.5).

---

## Current state

**Implemented and tested offline:** contract v1 shape; deterministic LangGraph;
bounded repair; fail-closed dietary mapping; Task 2 configured citation
construction, citation-before-use/basic-source checks, money-free labels and
regenerated samples; Task 3 Guardrail intervention propagation and experimental
harness; multi-item queries; model registry; Guardrail policy/tagging;
idempotency; Powertools observability; handler; local server; CI; zip build.

**Live verified in `ap-southeast-2`:** products and idempotency DynamoDB tables;
152 seeded records; Dynamo price repository contract; five current stored
idempotency outcomes; Nova Lite/Pro invocation; Guardrail
`b1xezpqe04kx` version `2` verified 13/13 + 9/9; and, re-confirmed 2026-08-30,
the deployed service plane — REST API `woqmel35lk` returning HTTP 200 on
`POST /dev/chat` in ~7s with a real Nova Lite call and grounded citations,
Lambda alias `live` cut over from `5` on 2026-08-30 and republished since, and
schedule `grocery-price-refresh-dev` ENABLED. This is evidence about the
resources, not about behaviour: it does not prove live red-team quality.
(Stale-claim ownership IS now proven against the live idempotency table.) (Retrieved-record/value equality is now proven offline on
every turn, against the record the repository returned.)

**Model evidence (meal-plan invariants, 2026-08-28).** Anthropic access is no
longer blocked: the account's one-time Anthropic use case form was submitted
and every configured model now answers. Three reps each, 90s apart, at the
production 20s client timeout, reported from reps with zero upstream failures:

| Model | clean band | reps | latency median / p90 | over 20s ceiling |
|---|---|---|---|---|
| Amazon Nova Pro | **100%** | 3/3 | 1.2s / 5.9s | 0 of 90 |
| Claude Haiku 4.5 | **100%** | 3/3 | 2.5s / 7.8s | 0 of 83 |
| Claude Sonnet 4.5 | not requalified | — | 11.8s / 19.9s | **9 of 98** |

**PACE THE HARNESS OR THE NUMBER IS THE QUOTA.** This account allows 10
cross-region requests per minute for either Claude model and 25 for Nova Pro.
One rep fires 25-40 requests as fast as the harness can issue them, so an
unpaced Claude run hits the wall part-way through and the TAIL of the case
list fails with INTERNAL_ERROR — which reads as "the model failed those cases"
and is really "the account stopped answering". Three consecutive bands scored
Haiku at 82-91% with every rep contaminated while Nova Pro scored 100% clean
on the same suite; paced, Haiku scores 100% too. An unpaced comparison between
an Anthropic and an Amazon model on this account compares their request
budgets. `evals/run_meal_plan.py` now paces at 9/min by default.

Haiku's 91% was also real, and separately fixed. Two paced 3-rep bands
differing only in the prompt:

| Prompt | band | failures |
|---|---|---|
| before | 91% / 91% / 91% | `plan-001` 3/3, `BUDGET_INFEASIBLE` |
| after | 100% / 100% / 100% | none |

The model was asking for fractional multi-packs — 1.5 packs is charged as two
— because nothing had told it partial packs round up. Worth +9 points, and the
controlled pair is the only reason that can be said rather than guessed.

Both clear the 90% floor **on this task**. Sonnet is excluded on latency
rather than quality: its p90 sat on the ceiling and roughly one plan call in
eleven exceeded it, so it fails real turns in `ap-southeast-2` before plan
quality is considered.

This is not route approval. The rule is a scorecard per task for every enabled
model, and **neither Claude model has an intent scorecard** —
`evals/run_intent.py --model claude-haiku` has never been run. Clearing the
meal-plan floor qualifies a model for `generate_plan`, and for nothing else.

**READ THE GAIN AS THE HARNESS, NOT THE MODELS.** Nova Pro went from 64% to
100% without changing. Everything that moved was ours, and every earlier
figure was measured by a scorer that was wrong in at least one way:

* the budget invariant compared CONSUMPTION, a number the shopper never pays,
  so a plan whose basket cost $65.01 scored as fitting a $60 budget
* the dietary exclusion check compared store locations against category names
  and could not fail at all
* `PlanDraft`'s reasoning cap rejected valid plans, so scores were measured
  through a repair loop that should not have been running
* candidates are now pre-filtered to the budget, and impossible requests are
  refused before generation, so the model is handed an easier and better-posed
  problem

These numbers supersede every earlier one and are not comparable to them.
They are evidence the system got correct, not that the models got better.

**The suite has run out of headroom, and what 100% means is narrower than it
looks.** Both models score 100% across three clean reps, so it cannot rank
candidates at all.

Every check in it is a RULE VIOLATION check — `exclude_categories`,
`min_budget_used`, `min_distinct_meals`, `serves_matches_household`. They ask
"did you break a rule", and neither model breaks rules. Nothing asks "is this a
good plan". So 100% means **both models produce valid plans**, and says nothing
about which produces better ones. Do not cite it as evidence that two models
are interchangeable.

Coverage is not the problem: 11 cases span households of 1-5, 3-7 days, budgets
$15-$200, and vegetarian, vegan, dairy-free, seafood, a combination, and an
unsupported term. The gap is that there is no quality gradient to measure.

When ranking does matter, two routes, and only the second is recommended:

* **Promote the reported metrics to scored** (reuse ratio, budget utilisation,
  variety). Cheapest, and argued against here: they are deliberately "reported,
  not scored" because no threshold is right for every case — 40% of budget is
  excellent for one request and under-feeding for another. Scoring them
  manufactures a gradient without establishing it means anything.
* **Add harder validity cases** — feasible but demanding budgets, three
  simultaneous exclusions, larger households. Extends what the suite already
  does well instead of changing its nature. Do the feasibility-floor review
  first (`docs/OPEN-REVIEW-min-grams-per-person-day.md`), or difficulty gets
  calibrated against a number nobody has checked.

An LLM-judge on plan quality was considered and rejected: it puts a
non-deterministic scorer inside a suite whose value is being deterministic, and
this session was four separate cases of a scorer being confidently wrong.

Local scripted baselines are 76.7% intent and 100% meal-plan (was 91%; the
same harness fixes lifted it), plus 7/7 Guardrail must-allow structure.
Live intent figures were re-measured on 2026-08-29 against guardrail v2 with
GuardrailBlocked excluded: Nova Pro 100.0%, Claude Haiku 4.5 96.4%, Nova Lite
92.9%. The earlier 83.3%/100% pair counted Guardrail refusals as wrong answers.

**Open for human review:** `min_grams_per_person_day` in
`config/feasibility.json` decides when a meal-plan request is refused as
impossible. It is the only figure in the planning path that is a judgement
rather than derived from the catalogue, and it was set by inspecting
fixtures, not by anyone who knows about food. Bounded by tests to a
525g-1197g window and not blocking anything — but it decides which requests
get refused outright, so it should not stay unreviewed indefinitely. Brief:
`docs/OPEN-REVIEW-min-grams-per-person-day.md`.

**Live evidence taken 2026-08-29.** All three outstanding items are done; full
detail in `docs/LIVE-EVAL-RUNBOOK.md` §8.

* **Guardrail: 13/13 must-block, 9/9 must-allow, exit 0** against
  `b1xezpqe04kx` **version 2**, Nova Lite, paced 9/min. The qualifying live
  policy evidence Req 5.5 needed.
* **Intent scorecards, guardrail v2:** Nova Pro 100.0% (28/28), Claude Haiku
  4.5 96.4% (27/28), Nova Lite 92.9% (26/28). All clear the 90% floor. These
  supersede the older 83.3%/100% figures, which counted Guardrail refusals as
  wrong answers.
* **Prompt cache: zero on every path, and that is correct.** `cachePoint`
  attaches to the ~500-token system prompt against Claude's 4096 minimum; the
  large repeated content is in the user prompt. Implemented, honestly gated,
  structurally inert.

**The run found and fixed a real over-block.** `ForagingAndWildFood` was defined
as "wild-gathered food including mushrooms, plants, shellfish, or roadkill", and
the classifier keyed on the ingredient noun: `how much is truffle oil`,
`price of mushrooms` and `cheapest button mushrooms` were all refused. Version 2
scopes the topic to the ACT of gathering. **A bare `price of mushrooms` is still
refused and remains open** — three rounds of tuning moved qualified queries but
not the unqualified noun.

**Two open defects on the deployed service, both backend, found 2026-08-30.**
The Lambda sets `BEDROCK_GUARDRAIL_VERSION=1` while every document and all the
qualifying evidence describe version 2, so benign mushroom and truffle-oil
queries are refused live (`docs/ARCHITECTURE.md` §3f). And nothing implements
Req 12.5: dropping `USE_DYNAMODB` or `USE_BEDROCK` silently selects fixtures and
the scripted model, and the service would keep returning valid-looking grounded
citations about fake products (§3g). **No offline gate can see a deployed
environment variable** — that is why both survived every check.

**Known pilot blockers:** IaC, a code refresh, and operational evidence — not
deployment itself, which happened on 2026-08-27 and was mis-recorded here until
2026-08-30. Pilot Tasks 1–7 closed on 2026-08-29 — grounding proof, live
Guardrail evidence, clarification, payable arithmetic, location and freshness,
idempotency fencing, and route qualification. What remains is Tasks 8–16: local
MCP, CDK adoption of the resources that already exist, the service plane,
operational gates, ingestion, the recipe catalogue, and the release run. There
is still no deployed security, SLO, cost, recovery or operations evidence — not
because nothing is deployed, but because nothing deployed has been measured,
alarmed or budgeted. (The running code IS now the code in this repository, as
of 2026-08-30; what it lacks is fresh data and any operational instrumentation.)

**Not a blocker, but on the record before production:** the deployment is
capped at 10 meal-plan turns per minute, falling to 5 when the repair loop
fires, and the binding quota (Nova Lite, 20/min) cannot be raised by
request. Accepted for workshop scale.

Run `python scripts/check_quotas.py` rather than quoting a figure from any
document, including this one. It derives the ceiling from the live account
and the current routing, names the model that BINDS it, and says whether
that one is adjustable — which is the only useful form of the question, as
a raisable limit on a model that is not the constraint is not a way out.
`docs/THROUGHPUT-AND-SCALING.md` holds the two options for lifting it and
what they cost.

**Planned/proposed AWS learning:** local read-only MCP first. AgentCore Gateway
with Identity/Policy and the isolated Runtime reviewer require proposed ADR 0002
mentor approval. Bedrock Model Evaluation, AgentCore Evaluations, inference
profiles, recipe/catalogue Knowledge Bases, advisory Automated Reasoning, S3
artefacts, Streams/SQS/DLQ, SNS, WAF/Cognito, and CloudWatch/X-Ray/Budgets are
companions gated by product purpose and evidence. None is claimed built.
AgentCore Memory remains later-only after identity, consent, TTL, deletion, and
privacy design. Shopper-path Runtime remains the separate p99 contingency.

---

## Working style

- Iterative and step-by-step. Confirm the approach before large changes.
- Genuine pushback with reasoning is wanted over agreement.
- This is treated as commercial-grade product development, not coursework.
- The project will later be **rebuilt in Kiro from the specs**, and converted
  to CDK. Current code is the reference implementation that proves the specs
  are achievable — keep the specs current when behaviour changes.