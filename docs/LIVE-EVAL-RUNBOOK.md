# Live evaluation runbook

**Status:** not yet run. Three pieces of evidence are outstanding, all of them
needing AWS credentials, and all three are batched here deliberately so they
happen in one credentialed session rather than three.

**Read this before running anything against Bedrock.** Every trap listed under
[What has already gone wrong](#what-has-already-gone-wrong) has actually
happened on this project and cost real time. The harness code now defends
against each one, but the defences only work if you use them.

---

## 1. What is outstanding, and what it blocks

| # | Evidence | Command | Blocks |
|---|---|---|---|
| 1 | Live Guardrail 13/13 must-block **and** 7/7 must-allow | `evals/run_guardrail.py --model …` | Pilot Task 3 follow-up; legacy 5.9 / 8.10; Req 5.5 |
| 2 | Claude intent scorecard | `evals/run_intent.py --model claude-haiku` | Pilot Task 7; legacy 5.7; **any** Claude route |
| 3 | Prompt-cache utilisation per model | see §5 | Legacy 3.9; Req 9.6 |

Until (1) exists there is **no qualifying live evidence that the Guardrail
blocks anything**. Guardrail `b1xezpqe04kx` version `1` has basic invocation
evidence only.

Until (2) exists, no Claude model may be routed for `classify_intent`. Clearing
the meal-plan floor qualified Claude Haiku 4.5 for `generate_plan` **and for
nothing else**. Every model in `config/models.json` is nonetheless still marked
`enabled` — a known Pilot Task 7 configuration defect, not qualification.

---

## 2. Preconditions

Three environment variables. The third is the one people miss:

```bash
export AWS_PROFILE=grocery
export AWS_REGION=ap-southeast-2
export BEDROCK_GUARDRAIL_ID=b1xezpqe04kx   # grocery-assistant-guardrail-dev
export BEDROCK_GUARDRAIL_VERSION=1         # pin the NUMBERED version, not DRAFT
```

`REQUIRE_GUARDRAIL` defaults to `1`, so a missing `BEDROCK_GUARDRAIL_ID` makes
every model call fail closed. That is deliberate — silently running generation
without content safety is the worse outcome — but it means a forgotten variable
looks like a total model outage.

**Pin `BEDROCK_GUARDRAIL_VERSION=1`, not `DRAFT`.** The acceptance claim is
about the numbered version. A result measured against `DRAFT` is evidence about
whatever the console happened to hold that day and cannot be reproduced.

Confirm the guardrail exists and check Anthropic access before spending a run:

```bash
aws bedrock list-guardrails --region ap-southeast-2 \
  --query 'guardrails[].{Id:id,Name:name,Status:status}' --output table

aws bedrock get-use-case-for-model-access --region ap-southeast-2
```

Anthropic models need the account's one-time use-case form submitted. It was
submitted on 2026-08-28 and is account-wide, so this should pass; check anyway,
because the failure mode is every Claude call erroring in a way that reads as a
model problem.

Finally, derive the live rate ceiling rather than trusting any document,
including this one:

```bash
python scripts/check_quotas.py
```

---

## 3. Pacing — the single most important paragraph here

**PACE THE HARNESS OR THE NUMBER IS THE QUOTA.**

This account allows **10 cross-region requests per minute** for either Claude
model and **25** for Nova Pro. All three harnesses now pace at 9/min by default
(`evals/_pacing.py`), leaving headroom for the retry the Bedrock client makes
internally, which also counts against the limit.

Do not pass `--max-rpm 0` unless you have confirmed headroom for that specific
model. Expect a paced run to take roughly:

| Suite | Cases | Model calls | Rough wall time at 9/min |
|---|---|---|---|
| Guardrail | 20 | ~1–3 per case | 4–7 min |
| Intent | 30 | 1 per case | ~3.5 min |

Budget half an hour for the session including reruns. That is the price of a
number you can quote.

---

## 4. The runs

### 4.1 Guardrail — the acceptance gate

```bash
python evals/run_guardrail.py --model nova-lite
```

Run it against the model the production route would actually use. Repeat for
any other model you intend to enable: **a Guardrail result is per-model**,
because the policy sits on the invocation.

**Exit codes:**

| Code | Meaning | What to do |
|---|---|---|
| `0` | must_block 13/13 and must_allow 7/7 | Record it (§6) |
| `1` | A real miss | Do **not** record as evidence. Investigate the named cases |
| `2` | **Inconclusive** — some case failed upstream | Re-run. Neither a pass nor a failure |

Exit `2` is not a nuisance, it is the point. On this suite an unanswered case
reads as *"the Guardrail let this through"*, so an outage would otherwise
manufacture a safety finding — or, worse, a passing run assembled from cases
the policy never saw.

**Read the report, not just the exit code.** Two sections matter:

- `declined, but NOT by the Guardrail` — cases refused by something else
  (`OUT_OF_SCOPE`, `BUDGET_INFEASIBLE`, `PLAN_GENERATION_FAILED`). A must_block
  case here is **not** a block; the harness used to count `OUT_OF_SCOPE` as one
  and credited the policy with refusals it never made.
- `of those, N/7 produced a real answer` — reported, not gated. If this drops
  well below the allow rate on a live run, legitimate questions are failing for
  a non-safety reason and that is worth chasing even though the gate passed.

### 4.2 Claude intent scorecard

```bash
python evals/run_intent.py --model claude-haiku --verbose
```

**This one is quietly the most dangerous.** `classify_intent` degrades to
keyword matching when a model call fails, by design — so a throttled run does
not error. It answers all 30 cases from the fallback and prints a perfectly
plausible accuracy for a model that answered a third of them.

The harness now detects this and returns exit `2` with an `INCONCLUSIVE`
message naming how many cases degraded. **If you see that, the number is
partly the keyword heuristic's score. Discard it and re-run.**

Compare against the recorded baselines: scripted 76.7%, Nova Lite 83.3%, Nova
Pro 100%. A route needs **≥90% on its applicable golden set**.

### 4.3 Cache utilisation

Prompt caching is inserted only where the model declares support and the prompt
clears the minimum (`cache_min_tokens` in `config/models.json`). The adapter
already records `cache_read_tokens` from the Bedrock response and the telemetry
layer emits it as the `CacheReadTokens` EMF metric.

Read it off any live run above — no separate harness exists. Either watch the
metric records on stdout, or capture `last_usage` directly. What is being
verified is that the figure is **non-zero for a model that declares caching**,
on a second call with the same system prompt; a flat zero means the markers are
not reaching the API and the capability flag is decorative.

---

## 5. What has already gone wrong

Each of these happened. None is hypothetical.

1. **An unpaced comparison measured the account, not the models.** Three
   consecutive bands scored Claude Haiku 4.5 at 82–91% with every rep
   contaminated, while Nova Pro scored 100% clean on the same suite. Paced,
   Haiku scores 100% too. The gap was the request budget.
2. **An unset `BEDROCK_GUARDRAIL_ID` produced an identical, entirely plausible
   27% for two different models** — a measurement of nothing, twice.
3. **A scorer that was wrong in three ways** moved Nova Pro from 64% to 100%
   without the model changing. Read a gain as the harness first.
4. **The guardrail harness could not fail.** `main()` returned `1` only on the
   allow rate, so a live run could print *"FAIL: must_block rate 0%"* and exit
   `0`. Fixed 2026-08-29, and pinned by a test that fails if it regresses.
5. **`--model` did not pin.** It set `USE_BEDROCK=1` and relabelled the report;
   the registry still routed per task. Any earlier guardrail scorecard headed
   with a model name may have measured a different model. Fixed 2026-08-29.

---

## 6. Recording the result

A result that is not written down has to be re-bought. Update, in this order:

1. **`AGENTS.md` → Current state.** The live-evidence paragraph and the model
   table. State the date, the model, the guardrail version, and the pacing
   used.
2. **`.kiro/specs/grocery-orchestrator/tasks.md`.** Tick the Pilot Task 3
   follow-up only on a `0` exit with 13/13 and 7/7. For the scorecard, Pilot
   Task 7 and legacy 5.7.
3. **`README.md` → Progress to date.** Move the 🚧 to ✅ only when the
   evidence exists, not when the run is scheduled.
4. **`docs/CI-GATE-HEALTH.md`** if a gate's behaviour changed.
5. **This file** — replace *Status: not yet run* with the date and result.

Record the **exit code**, not a prose summary of it. "Exit 0, 13/13 must_block,
7/7 must_allow, nova-lite, guardrail version 1, paced 9/min, 2026-xx-xx" is
evidence. "Guardrail verified" is not.

**Do not lower a floor to make a run pass.** `MUST_ALLOW_FLOOR` is 1.0 because
over-blocking is the usual failure mode of an aggressive policy, and a filter
that refuses ordinary grocery questions is a broken product rather than a safe
one.

---

## 7. Recommendations for whoever picks this up

- **Do the guardrail run first.** It is the only one on the pilot's critical
  path, and it is the only one whose absence is a safety claim rather than a
  routing decision.
- **Run each suite twice.** These models are non-deterministic and repeat runs
  of the same model have differed by ~18 points on an 11-case suite, which is
  wider than the gap between models. One run cannot rank anything.
- **Expect exit 2 at least once** and treat it as the system working. Re-run;
  do not reach for `--max-rpm 0` to make it go away.
- **Don't enable a route in the same sitting.** Producing a scorecard and
  changing `config/models.json` are separate decisions; Pilot Task 7 owns the
  second, including disabling the models that have no scorecard at all.
- **Known gap, deliberately not closed:** `run_intent.py` detects a degraded
  run but does not distinguish *why* the model call failed — a throttle and a
  malformed request both surface as `intent_degraded`. Adding
  `UPSTREAM_CODES`-style classification there would make a rerun decision
  faster. Not on the critical path.
- **Costs are small but not zero.** `run_intent.py` reports cost per model from
  `config/models.json`; read it off the comparison table rather than guessing.
