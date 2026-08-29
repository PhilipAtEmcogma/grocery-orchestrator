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
- *Assertion today*: Pilot Task 2 added declaration-before-use and basic source
  checks using configured table, `store_key`, and normalized `product_key`.
  `assert_grounded()` still has no immutable retrieved-record context, so it
  does not independently prove exact key/value equality or satisfy all Req
  3.5–3.6 negative controls.

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
                    `-- retrieve_prices
                         +-- no citations -------> emit_no_data
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

**Three protocol boundaries** make everything testable without AWS:
- `src/retrieval/base.py` — `PriceRepository`; fixture and DynamoDB
  implementations exist.
- `src/models/base.py` — `ModelClient`; scripted and live-verified Bedrock
  implementations exist.
- `src/observability/base.py` — `Telemetry`; no-op locally and Powertools at
  the handler boundary.

### MCP, AgentCore, and bounded agents

Pilot Task 8 delivers local read-only MCP first. Its coarse tools invoke the
complete deterministic service and expose no raw DynamoDB, AWS SDK, filesystem,
network, scraping, write, citation, or unguarded-generation primitive.

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
routes from `config/models.json`. Capability-aware routing exists, but the
production route is not approved until every enabled model has a scorecard and
every active task reaches the 90% threshold.

### Current pilot blockers

Do not describe the current reference implementation as pilot-ready. Task 2–3
construction/rendering/propagation work is implemented, but exact immutable
retrieved-record/value proof,
and a qualifying live Guardrail result remain open. Other blockers include
clarification, location/freshness, idempotency ownership,
production fail-closed configuration, model qualification, CDK/API controls,
published SnapStart alias, and deployed SLO/cost evidence. MCP, AgentCore, and
managed-evaluation stages are planned or proposed, not built.

---

## Conventions

- **Money is `Decimal` in Python, string on the wire and in storage.** Never
  `float`. The numeric DynamoDB type round-trips through float in most paths.
- **User input is untrusted.** Delimited in prompts; guardrail input tagging
  via `src/models/guardrail.py`. Note the prompt-attack filter does *nothing*
  without tagging.
- **Nodes are `state → partial state`.** Pure functions, independently
  testable.
- Python 3.13, region `ap-southeast-2` (Sydney).
- Line length 100. Ruff with bandit (`S`) rules enabled.

---

## Commands

```bash
python -m pytest -q                              # 485 passed, 31 skipped, no AWS
ruff check . && ruff format --check .            # both gated in CI
python validate.py                               # contract samples + grounding
UPDATE_FIXTURES=1 python -m pytest \
    tests/test_sample_fixtures.py                # rewrite samples/ from the server
python evals/run_intent.py                       # 76.7% scripted baseline
python evals/run_intent.py --model nova-lite     # 83.3% live (Nova Lite)
python evals/run_intent.py --model nova-pro      # 100% live (Nova Pro)
python evals/run_meal_plan.py                    # 100% invariants baseline
python evals/run_guardrail.py                    # must_allow structural (scripted)
# EXPERIMENTAL/non-qualifying: --model does not yet pin the requested model
python evals/run_guardrail.py --model nova-lite
python scripts/generate_fixtures.py              # regenerate seed data
python scripts/dev_server.py                     # localhost:8000 for frontend
python scripts/apply_guardrail.py --dry-run      # validate guardrail policy
python scripts/build_lambda.py                   # build/lambda.zip, ~30 MB unzipped
python scripts/apply_iam.py --dry-run     --config config/iam-<role>.json              # execution roles, policy-as-data
python scripts/apply_state_machine.py --dry-run  # ingestion Step Functions
python -m pytest tests/test_ingestion.py         # ingestion; no AWS
python scripts/check_quotas.py                    # throughput ceiling, live
python Philip_demo/run_all.py                     # seven feature demos, offline
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
idempotency outcomes; Nova Lite/Pro invocation; and Guardrail
`b1xezpqe04kx` version `1` basic attachment. This does not prove exact retrieved
record/value equality, stale ownership, or live red-team quality.

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
Nova Lite intent 83.3%, Nova Pro intent 100% — both unchanged and measured by
the intent harness, which has had none of the above scrutiny.

**Open for human review:** `min_grams_per_person_day` in
`config/feasibility.json` decides when a meal-plan request is refused as
impossible. It is the only figure in the planning path that is a judgement
rather than derived from the catalogue, and it was set by inspecting
fixtures, not by anyone who knows about food. Bounded by tests to a
525g-1197g window and not blocking anything — but it decides which requests
get refused outright, so it should not stay unreviewed indefinitely. Brief:
`docs/OPEN-REVIEW-min-grams-per-person-day.md`.

**Live evidence still to take:** three items, all needing credentials, batched
into one session — the live Guardrail result, the Claude intent scorecard, and
cache utilisation. The harness controls were repaired on 2026-08-29 and are now
trustworthy; the runs have not been done. **`docs/LIVE-EVAL-RUNBOOK.md` is the
runbook**: preconditions, exit-code meanings, the five traps that have already
cost this project time, and where to write the result down.

**Known pilot blockers:** Task 2 exact record/value follow-up (the runtime
money half closed 2026-08-29);
Task 3 qualifying live Guardrail follow-up; clarification (payable totals
are DONE — MealPlan carries payable_total_nzd and within_budget follows it);
location/freshness; idempotency fencing/candidate scale; model qualification;
CDK/service/API/SnapStart adoption; and deployed security, SLO, cost, recovery,
and operations evidence.

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