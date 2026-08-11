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
- *Assertion*: `assert_grounded()` fails any response citing a ref that was
  never retrieved. Runs in CI with a negative test.

For prose the same idea applies differently: the model writes `[[c1]]`
placeholders, and `assert_no_literal_money()` rejects any money-shaped string.

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

---

## Architecture

```
API Gateway (REST) → Lambda → LangGraph state machine → Bedrock + DynamoDB
```

A **workflow, not an agent**. The model makes bounded judgements at two fixed
points (classify intent, select products); code owns every control-flow
decision. This is deliberate — see design.md §8.

```
validate_input → classify_intent → retrieve_prices
                                    ├─ no citations → emit_no_data → finalise
                                    ├─ price_check → generate_comparison → generate_prose → finalise
                                    └─ meal_plan → generate_plan → validate_plan
                                                      ↑ repair (bounded, 2) ┘
                                                      └─ ok → generate_prose → finalise
```

**Three protocol boundaries** make everything testable without AWS:
- `src/retrieval/base.py` — `PriceRepository`; fixtures now, DynamoDB later
- `src/models/base.py` — `ModelClient`; scripted now, Bedrock later
- `src/observability/base.py` — `Telemetry`; a no-op by default, Powertools
  when the handler installs it

The scripted client has knobs (`plan_packs`, `hallucinate_ref`,
`prose_writes_money`, `force_error`) to drive failure paths that a live model
cannot be made to produce on demand.

**Observability stops at the handler.** Powertools' Logger, Tracer and
Metrics are wired in `src/handler.py`, and `src/observability/powertools.py`
is the only module that imports the library. Tracing reaches inside the graph
by *wrapping* the repository and model client, not by importing anything into
a node. A test walks the import graph and fails if that escapes — see
design.md §12.

**Model plane, not a Claude endpoint.** Nodes request a *task*;
`src/models/registry.py` routes it using `config/models.json`. Capabilities are
explicit — tool use, prompt caching, output limits — and code branches on them.
A model without tool use gets JSON-in-prose with parsing. Unroutable raises
rather than substituting silently.

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
python -m pytest -q                              # 218 tests, ~4s, no AWS
ruff check .                                     # must be clean
python validate.py                               # contract samples + grounding
UPDATE_FIXTURES=1 python -m pytest \
    tests/test_sample_fixtures.py                # rewrite samples/ from the server
python evals/run_intent.py                       # 76.7% scripted baseline
python evals/run_meal_plan.py                    # 89% invariants baseline
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

It runs everything CI runs that is fast and offline: ruff, pytest, the secret
scan on staged files, contract and grounding validation, guardrail policy
validation, fixture drift, and both eval floors. About five seconds. It first
puts the project venv on PATH and prints which one — see *Tool version drift*
below for why that step exists.

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

**Done, tested, no AWS needed:** contract v1.0 · LangGraph orchestrator ·
intent classification with extraction · meal planning with bounded repair ·
prose generation · multi-item queries · multi-model registry · guardrail config
and input tagging · idempotency · Powertools observability (Req 12.1–12.2,
Task 6.7) · two eval suites · handler · local dev server · CI · Lambda
deployment archive (Task 10.1) · Kiro specs, steering and hooks ·
terms-of-service assessment for live acquisition (Task 7.9).

**Blocked on AWS account (not yet provisioned):**
- `src/retrieval/dynamo.py` — raises `NotImplementedError` by design
- `src/store/dynamo_idempotency.py` — same; `acquire` must be a conditional
  put on `attribute_not_exists`, not read-then-write
- Live Bedrock verification, including whether the `guardContent` block shape
  is right — flagged in `src/models/guardrail.py`
- Deployment

**Not started:** SnapStart on a published alias (Task 10.2), recipe catalogue
(Req 2.9), streaming transport (Req 7.9), per-retailer acquisition (Task 7.5
— unblocked by 7.9, fixtures only).

---

## Working style

- Iterative and step-by-step. Confirm the approach before large changes.
- Genuine pushback with reasoning is wanted over agreement.
- This is treated as commercial-grade product development, not coursework.
- The project will later be **rebuilt in Kiro from the specs**, and converted
  to CDK. Current code is the reference implementation that proves the specs
  are achievable — keep the specs current when behaviour changes.