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

**Two protocol boundaries** make everything testable without AWS:
- `src/retrieval/base.py` — `PriceRepository`; fixtures now, DynamoDB later
- `src/models/base.py` — `ModelClient`; scripted now, Bedrock later

The scripted client has knobs (`plan_packs`, `hallucinate_ref`,
`prose_writes_money`, `force_error`) to drive failure paths that a live model
cannot be made to produce on demand.

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
python -m pytest -q                              # 150 tests, ~1s, no AWS
ruff check .                                     # must be clean
python validate.py                               # contract samples + grounding
python evals/run_intent.py                       # 76.7% scripted baseline
python evals/run_meal_plan.py                    # 89% invariants baseline
python scripts/generate_fixtures.py              # regenerate seed data
python scripts/dev_server.py                     # localhost:8000 for frontend
python scripts/apply_guardrail.py --dry-run      # validate guardrail policy
python scripts/build_lambda.py                   # build/lambda.zip, ~26 MB unzipped
```

A pre-commit hook runs `ruff --fix`, `ruff check` and `pytest`. Commits fail if
any of those fail. CI (`.github/workflows/ci.yml`) adds eval floors and
security scanning — **no AWS credentials needed anywhere**, which is a design
outcome of the protocol boundaries.

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

---

## Current state

**Done, tested, no AWS needed:** contract v1.0 · LangGraph orchestrator ·
intent classification with extraction · meal planning with bounded repair ·
prose generation · multi-item queries · multi-model registry · guardrail config
and input tagging · idempotency · two eval suites · handler · local dev server ·
CI · Lambda deployment archive (Task 10.1) · Kiro specs, steering and hooks.

**Blocked on AWS account (not yet provisioned):**
- `src/retrieval/dynamo.py` — raises `NotImplementedError` by design
- `src/store/dynamo_idempotency.py` — same; `acquire` must be a conditional
  put on `attribute_not_exists`, not read-then-write
- Live Bedrock verification, including whether the `guardContent` block shape
  is right — flagged in `src/models/guardrail.py`
- Deployment

**Not started:** SnapStart on a published alias (Task 10.2), Powertools
observability (Req 12.1–12.2), recipe catalogue (Req 2.9), streaming
transport (Req 7.9).

---

## Working style

- Iterative and step-by-step. Confirm the approach before large changes.
- Genuine pushback with reasoning is wanted over agreement.
- This is treated as commercial-grade product development, not coursework.
- The project will later be **rebuilt in Kiro from the specs**, and converted
  to CDK. Current code is the reference implementation that proves the specs
  are achievable — keep the specs current when behaviour changes.