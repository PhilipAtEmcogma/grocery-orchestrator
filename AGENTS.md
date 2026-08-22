# Smart Grocery & Meal Budget Assistant — orchestrator

AWS AI Innovation Mentorship Workshop (AUT). Six-week sprint. This repo is the
**backend orchestration and AI layer**; teammates own the frontend chatbot and
the data/S3 side.

A conversational assistant for budget-conscious New Zealand shoppers: compare
grocery prices across Pak'nSave, Woolworths and New World, and generate meal
plans that provably fit a budget.

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

---

## The three invariants

These are the point of the whole design. Do not weaken them for convenience.

**1. No price may originate from model generation (Req 3).**
Enforced three ways, any one of which would suffice:
- *Topology*: generation nodes are unreachable except through
  `retrieve_prices`. No edge skips it.
- *Schema*: `PlanDraft` has no price field. The model returns citation refs
  and pack multipliers; every dollar figure is computed in Python.
- *Assertion today*: `assert_grounded()` fails a response whose structured
  payload cites a ref that was never declared. Pilot Task 2 must extend it to
  prove citation order, exact table/PK/SK, value equality, and every prose-like
  field; do not describe those stronger target checks as implemented.

For prose, the model writes `[[c1]]` placeholders and
`assert_no_literal_money()` rejects model-supplied money. The current renderer
then expands placeholders into figures, which is a known wire-level defect;
the target renderer emits only non-monetary labels and keeps prices in
citation-linked structured fields.

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
                         +-- no citations -> emit_no_data
                         +-- price_check -> generate_comparison -> generate_prose
                         `-- meal_plan -> generate_plan -> validate_plan
                                          ^ bounded repair (2) |
                                          `--------------------'
```

**Three protocol boundaries** make everything testable without AWS:
- `src/retrieval/base.py` — `PriceRepository`; fixture and DynamoDB
  implementations exist.
- `src/models/base.py` — `ModelClient`; scripted and live-verified Bedrock
  implementations exist.
- `src/observability/base.py` — `Telemetry`; no-op locally and Powertools at
  the handler boundary.

### MCP and bounded agents

The approved early MCP demonstration is a separate local, read-only façade for
Kiro or another approved client. Its coarse tools invoke the complete
application service; they do not expose raw DynamoDB, AWS SDK, filesystem,
network, scraping, writes, or unguarded generation. It is planned under Pilot
Task 8 and is not implemented yet.

The later data-quality agent is similarly bounded: read-only tools, capped
snapshots, deterministic reference validation, and human approval. It cannot
publish prices. AgentCore is not approved for this work unless the documented
p99 meal-path contingency is triggered and a mentor signs off. See
`docs/adr/0001-deterministic-core-bounded-agent-extensions.md`.

**Observability stops at the handler.** Powertools' Logger, Tracer and Metrics
are wired in `src/handler.py`, and `src/observability/powertools.py` is the
only other module that imports the library. Protocol wrappers carry tracing
inside the graph without node imports.

**Model plane, not a Claude endpoint.** Nodes request a task and the registry
routes from `config/models.json`. Capability-aware routing exists, but the
production route is not approved until every enabled model has a scorecard and
every active task reaches the 90% threshold.

### Current pilot blockers

Do not describe the current reference implementation as pilot-ready. Known
open work includes exact Dynamo source keys, removal of literal prices from
comparison reasoning, stronger final grounding checks, Guardrail intervention
propagation, missing-constraint clarification, payable basket totals,
location/freshness enforcement, idempotency ownership, production fail-closed
configuration, CDK adoption, API controls, and deployed SLO evidence.

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
python -m pytest -q                              # 304 tests, ~5s, no AWS
ruff check .                                     # must be clean
python validate.py                               # contract samples + grounding
UPDATE_FIXTURES=1 python -m pytest \
    tests/test_sample_fixtures.py                # rewrite samples/ from the server
python evals/run_intent.py                       # 76.7% scripted baseline
python evals/run_intent.py --model nova-lite     # 83.3% live (Nova Lite)
python evals/run_intent.py --model nova-pro      # 100% live (Nova Pro)
python evals/run_meal_plan.py                    # 91% invariants baseline
python scripts/generate_fixtures.py              # regenerate seed data
python scripts/dev_server.py                     # localhost:8000 for frontend
python scripts/apply_guardrail.py --dry-run      # validate guardrail policy
python scripts/build_lambda.py                   # build/lambda.zip, ~30 MB unzipped
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
  explicitly.
- Never lower a CI floor to make a build pass. Raise one when the baseline
  genuinely improves.
- The meal-plan budget check is **two-sided**: `min_budget_used` catches
  under-feeding, which `within_budget` alone would pass.

---

## Do not

- Use Bedrock Agents Classic, `CreateAgent` or `InvokeInlineAgent` — in
  maintenance mode since 30 Jul 2026, closed to new accounts.
- Suggest `ap-southeast-6` (Auckland) — no AgentCore, no SnapStart.
- Containerise the orchestrator Lambda — forfeits SnapStart, which is
  zip-only. Measured dependency size fits well under the archive limit.
- Loosen `resolve_product_key` to fuzzy matching. Under-matching is
  recoverable; mis-matching produces a confident wrong price.
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

**Implemented and tested offline:** contract v1 shape; deterministic LangGraph
orchestrator; bounded repair; fail-closed dietary mapping; placeholder prose;
multi-item queries; task-based model registry; Guardrail policy/tagging;
idempotency; Powertools observability; eval harnesses; handler; local server;
CI; Lambda zip build; specifications, steering, hooks, and acquisition-risk
assessment.

**Live verified in `ap-southeast-2`:** products and idempotency DynamoDB tables;
152 seeded records; Dynamo price repository contract; the five current stored
idempotency outcomes; Nova Lite/Pro Bedrock invocation; and Guardrail
`b1xezpqe04kx` version `1` for basic attached invocation. This does not prove
the pending exact-source, stale-owner, or full red-team controls.

**External access block:** Claude model access remains under Anthropic account
verification. Claude models are not qualified for routing until access exists
and task-specific scorecards pass. Current evidence is Nova Lite intent 83.3%,
Nova Pro intent 100%, and Nova Pro meal-plan invariants 64%; the last result is
below the 90% pilot requirement.

**Known pilot blockers:** Pilot Tasks 2–12—exact provenance and literal-money
checks; Guardrail propagation/harness; clarification and payable totals;
location/freshness; canonical idempotency fingerprints, stale-owner fencing,
and candidate-query scale; model plane alignment/scorecards; CDK adoption; service deployment; API controls;
SnapStart alias; dashboards, budgets, quotas, and measured SLOs.

**Planned agentic work:** local read-only MCP façade (Pilot Task 8) and bounded
data-quality reviewer (Pilot Task 14). Neither is implemented. Remote MCP,
Cognito, WebSocket, persistent preferences, live acquisition, and AgentCore are
later or gated work.

---

## Working style

- Iterative and step-by-step. Confirm the approach before large changes.
- Genuine pushback with reasoning is wanted over agreement.
- This is treated as commercial-grade product development, not coursework.
- The project will later be **rebuilt in Kiro from the specs**, and converted
  to CDK. Current code is the reference implementation that proves the specs
  are achievable — keep the specs current when behaviour changes.