# Throughput: the ceiling, and what to do about it in production

**Decision taken 2026-08-28: accept the ceiling.** The target is a workshop and
a demo, where it is ample. This document exists so the number is written down
rather than discovered, and so the two options worth taking to production are
recorded while the reasoning is fresh.

Nothing here is a task. Read it if throughput starts to bind, or before anyone
puts this in front of real users.

---

## 1. The measured ceiling

Production routing never touches Claude. The registry resolves:

| Task | Model |
|---|---|
| `classify_intent` | Amazon Nova Lite |
| `generate_plan` | Amazon Nova Pro |
| `repair_plan` | Amazon Nova Lite |
| `generate_prose` | Amazon Nova Lite |

Measured Bedrock calls per turn:

| Turn | Calls | of which Nova Lite |
|---|---|---|
| price check | 2 | 2 |
| meal plan, no repair | 3 | 2 |
| meal plan, one repair | 4 | 3 |

Account quotas in `ap-southeast-2`, cross-region inference profiles (which is
what the `apac.*` and `au.*` model ids use):

| Model | requests/min | adjustable? | tokens/min | adjustable? |
|---|---|---|---|---|
| Amazon Nova Lite | **20** | **no** | 400,000 | yes |
| Amazon Nova Pro | 25 | **no** | 2,000,000 | yes |
| Claude Haiku 4.5 | 10 | **yes** | 5,000,000 | yes |
| Claude Sonnet 4.5 | 10 | **yes** | 5,000,000 | yes |

**Nova Lite binds first.** Derived by `scripts/check_quotas.py` against the
live account rather than by hand:

| Turn | Turns/min | Bound by |
|---|---|---|
| meal plan, no repair | **10.0** | Amazon Nova Lite |
| meal plan, 2 repairs | **5.0** | Amazon Nova Lite |
| price check | 10.0 | Amazon Nova Lite |

So 5-10 meal-plan turns per minute service-wide, depending on how often repair
fires — roughly 300-600 an hour. Nova Pro is not the constraint at one call
per turn against 25/min.

An earlier draft of this document said "roughly 8/min", derived by hand. It
was a middle estimate of a range, and stating one number hid the fact that a
turn needing repair costs twice as much headroom as one that does not. Run the
script rather than quoting a figure from here.

Token limits are nowhere near binding. A plan turn uses on the order of 1,300
input and 120 output tokens; the request count runs out first by two orders of
magnitude.

### The part that surprises people

**Nova's request-per-minute limits are not adjustable. Claude's are.**

So the reflex answer — "ask AWS to raise the quota" — is unavailable for the
models actually in the route, and available only for models that are not.
Check `Adjustable` before planning around a quota increase:

```bash
aws service-quotas list-service-quotas --service-code bedrock \
  --region ap-southeast-2 \
  --query "Quotas[?contains(QuotaName,'requests per minute')].{Name:QuotaName,Value:Value,Adj:Adjustable}" \
  --output table
```

### What hitting it looks like

A throttled call raises `ModelError`, which `generate_plan` routes to
`emit_upstream_failure`: the user gets `UPSTREAM_TIMEOUT` or `INTERNAL_ERROR`,
`retryable: true`, and an honest "couldn't reach the service" message. Nothing
corrupt is emitted and no plan is invented.

It is worth knowing what this looked like from the outside before that path
existed: in the eval harness, throttling hit the *tail* of a run, so the last
cases failed and it read as *"the model failed those cases"* rather than
*"the account stopped answering"*. Three model bands were scored that way
before anyone checked the quota. Under load the same confusion is available in
production — a dashboard showing errors clustered late in a busy minute is
throttling, not a bad model.

`evals/run_meal_plan.py` now paces at 9 requests/min by default for this
reason. Production does not pace: a queued request would spend the API
Gateway's 29-second budget waiting, which is a worse failure than an honest
retryable error.

---

## 2. Why accepting it is right for now

- A workshop and a demo will not approach 8 turns/minute. The observed usage is
  a handful of people taking turns.
- The failure mode is honest and retryable, not corrupting.
- Both alternatives cost something real — a product feature or 13x the token
  price — to lift a ceiling nothing is currently pressing against.
- Neither alternative changes the order of magnitude. If this ever needs to
  serve hundreds of concurrent users, the answer is a different conversation
  (Provisioned Throughput, or a different inference plane), not these.

**The trigger to revisit:** someone can state a required turns-per-minute
figure above 8. Until that number exists, work here is speculative.

---

## 3. Option B — remove a call from the turn

**Take a meal-plan turn from 3 Nova Lite-bound calls to 2, lifting the ceiling
to roughly 10–13 turns/minute.**

`generate_prose` is a separate Nova Lite call that produces one explanatory
sentence with `[[c1]]` placeholders. Two ways to remove it:

1. **Drop prose entirely** on meal-plan turns. The plan payload is already
   complete and renderable; the sentence is commentary.
2. **Fold prose into the plan call** — have `PlanDraft` carry a `summary`
   field the same way it carries `reasoning`, and emit that as the token
   event.

### Why it is attractive

- Also cuts per-turn latency by one model round trip, and per-turn cost.
- Roughly a 50% throughput improvement for one deletion.
- Reduces the failure surface: one fewer call that can throttle or time out.

### Why it was not done

- **The prose is the conversational half of a conversational assistant.** A
  price table with no sentence is a worse product, and the project's own
  degradation rule ("better a table with no sentence than a sentence with a
  wrong price") treats losing prose as a *degraded* outcome, not a neutral one.
- Folding it into the plan call is not free either. It puts user-facing prose
  in the same generation as the plan, where today the prose node is a separate
  smaller-tier call with its own money-rejection check. Merging them means the
  quality model writes the sentence and the existing prose degradation path
  has to move with it.
- 50% does not change the order of magnitude. It buys headroom, not scale.

### If you do it

Keep the money rejection. `assert_no_literal_money_in_response` and the prose
node's degradation behaviour exist because a model wrote a price into a
sentence; whichever call produces the sentence has to keep that guard, and
`tests/test_prose.py` should follow it rather than be deleted with the node.

---

## 4. Option C — move the fast tier to an adjustable model

**Route the fast tier to Claude Haiku 4.5 and request a quota increase, which
is the only path to a ceiling that is not fixed.**

Haiku's request-per-minute limit is adjustable where Nova's is not. The
increase must be requested and granted, and the starting point is *lower* (10
vs 20), so this is worse until the request succeeds.

### The cost, which is the whole argument

| Model | input $/1k | output $/1k | est. cost per plan turn |
|---|---|---|---|
| Amazon Nova Lite | 0.00006 | 0.00024 | $0.000432 |
| Claude Haiku 4.5 | 0.0008 | 0.004 | $0.006400 |

**Roughly 13x more per fast-tier call.** With 2–3 fast calls per turn, moving
the tier is the dominant cost change in the system.

### Why it is the only real ceiling-lifter

Every other option nudges. This one removes the fixed cap: with an approved
increase the limit becomes whatever was justified to AWS, which can be well
above 20/min.

### Why it was not done

- It inverts the routing rationale that `config/models.json` is built on:
  cheap models for cheap work, quality where it counts. Explanatory prose and
  intent classification do not need a frontier model, and the eval evidence
  says Nova Lite handles them (intent 83.3%).
- It spends real, recurring money against a limit nothing is currently
  hitting.
- The increase is a request, not a setting. Planning around capacity that has
  not been granted is how a launch date slips.
- Claude models have no intent scorecard, so this would also require
  qualifying them on `classify_intent` and `generate_prose` before the route
  could be approved — see the model evidence section in `AGENTS.md`.

### If you do it

Request the increase **first** and confirm it is granted, then change routing,
then re-run the scorecards for every task the tier serves. In that order — the
routing change is worthless without the quota, and unqualified without the
evals.

---

## 5. Option D — Provisioned Throughput, and why it is not here

Dedicated model capacity with no request-per-minute limit, billed hourly
whether or not it is used, typically with a term commitment.

It is the correct answer for sustained production load and the wrong answer
for a six-week workshop project: it converts a free constraint into a standing
bill, and the constraint is not currently binding. Recorded so that a future
reader knows it was considered rather than missed.

---

## 6. Related open items

- `docs/OPEN-REVIEW-min-grams-per-person-day.md` — the feasibility floor still
  awaits domain review. Unrelated to throughput, but the other decision on the
  meal-plan path that a human should make.
- The meal-plan eval suite cannot discriminate between models: both Nova Pro
  and Claude Haiku 4.5 score 100% across three clean reps. See the eval
  discipline section in `AGENTS.md`. Relevant here because Option C's
  "qualify the new tier" step depends on a suite that can tell models apart,
  and today's cannot.
