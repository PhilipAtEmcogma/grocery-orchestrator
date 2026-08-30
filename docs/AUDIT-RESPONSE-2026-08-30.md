# Response to the technical and product audit of 2026-08-30

**Audit under review:** *"The grocery assistant is not the product. The
verification harness is."* — a 21-section technical and product audit of this
repository, dated 30 August 2026, taken at commit `5ace934` on branch
`feat/per-task-model-exclusion`.

**This response:** 2026-08-31, against `main` at `d22008d`.

---

## Why this document exists

The audit is an outside document making claims about this repository. This
repository's own rule is that when two documents disagree, you check the thing
they describe rather than picking a document — so every finding below was
re-checked against the code at HEAD before it was accepted, closed or declined.

**Four merges landed between the audited commit and HEAD**, and they close
several of the audit's headline findings. That is not a criticism of the audit;
it is what happens when a repository moves during a review, and it is exactly
why a finding needs a commit attached rather than a date.

| Merge | What it changed |
|---|---|
| `7eab93a` | CDK: adopt the tables, deploy the service plane |
| `0146263` | 29 curated recipes, deterministic assembly (Task 15b) |
| `fba0fac` | The reviewer's boundary (Task 14a) |
| `82fe31f` | Twelve more demos, three modes |

Dispositions used below:

- **CLOSED** — was true when audited, is no longer true. Names the commit.
- **ACCEPTED** — true at HEAD, fixed in this change.
- **ACCEPTED (deferred)** — true at HEAD, not fixed here, with the reason.
- **DECLINED** — the recommendation is not being taken, with the argument.
- **NOT OURS** — real, and not an engineering task.

---

## 1. The six headline findings

### Finding 1 · The grounding guarantee — **AGREED, no action**

The audit's description is accurate, including the shape-versus-identity
distinction and the fifth control over model-authored free text. It ran the
negative controls rather than reading about them. Nothing to do.

### Finding 2 · "The 29-second ceiling no longer exists" — **ACCEPTED IN PART, RECOMMENDATION DECLINED**

This is the audit's #2 of 20 and its highest-leverage claim, so it got the
closest reading.

**What is right.** API Gateway REST added response streaming in November 2025.
It uses `responseTransferMode: STREAM` over a `/response-streaming-invocations`
integration URI with `InvokeWithResponseStream`, extends the integration
timeout to 15 minutes, and keeps the stage in front — so throttling, usage
plans, authorizers and WAF all still apply. `design.md` §8 rejected streaming
as a gateway *bypass* costing "rate limiting, usage plans, and authentication".
**That trade is genuinely gone, and §8 was stale.** Corrected in this change.

**What the audit missed.** It costed the work as "Complexity M · Depends on:
handler output format." Two facts make it much more expensive here:

- **The Python managed runtime does not support response streaming.** Streaming
  is native on Node.js; Python needs a custom runtime with a bespoke Runtime
  API integration, or the Lambda Web Adapter.
- **SnapStart supports Java 11+, Python 3.12+ and .NET 8+ managed runtimes
  only.** OS-only runtimes and container images are not supported.

Those sets do not intersect. A custom runtime buys streaming and forfeits
SnapStart — the same trade `AGENTS.md` already refuses for containers
("forfeits SnapStart, which is zip-only"), and accepting it here while refusing
it there would be incoherent. The Lambda Web Adapter keeps the managed runtime
but puts an ASGI web server inside a handler whose value is having no framework
in it, and its SnapStart compatibility is unestablished.

**And the ceiling is not binding.** The audit itself quotes the deployed
baseline: meal-plan p95 **12.2s**, price-check p95 **2.21s**, against 29s. The
constraint it proposes to delete is at roughly 2.4× the slowest measured path.

**Disposition.** The *reason* recorded in `design.md` §8 and §9 was wrong and is
fixed. The *conclusion* stands, on better grounds: Req 7.9 remains a GAP,
blocked on the runtime rather than on the gateway. Revisit if p99 approaches
the ceiling under real concurrency, if Lambda ships native Python streaming, or
if SnapStart reaches OS-only runtimes.

The audit's downstream inferences weaken accordingly. Re-opening the Claude
Sonnet exclusion, raising `MAX_ITEMS_PER_TURN`, and dissolving the AgentCore
p99 contingency were all argued from "the ceiling is gone". The ceiling is
liftable in principle and not lifted here, and none of those should move on
latency grounds until a load run defines p99 — which is undefined at n=3.

### Finding 3 · "The recipe blocker is a name-resolution problem" — **CLOSED (`0146263`), one part ACCEPTED**

Superseded by Task 15b, which landed the day the audit was taken. The blocker
was not resolved by curating head terms; it was routed around by writing 29
recipes against the catalogue we have. Measured at HEAD: **29/29 costable
against the real catalogue**, 14/29 against the offline fixtures.

The audit's underlying observation is still true and still worth acting on:
`sugar`, `plain flour` and `olive oil` do not resolve even against the real
528-product catalogue, because head terms are hand-curated by design and there
are 29 of them. That is now a **shopper-query** problem (`docs/OPEN-REVIEW-head-terms.md`),
not a recipe-planner blocker, and the audit's conflation of the two is what has
gone stale. Filed as ACCEPTED (deferred) — it needs somebody who shops these
stores, which is what that open review is for.

### Finding 4 · Provenance of the 2,759 served rows — **NOT OURS, and the strongest finding in the audit**

Verified unchanged at HEAD. `datasets/DATA_SCHEMA.md` §1 states the prices were
"Sourced from Foodstuffs online shopping catalog across 10 Auckland physical
store locations (Captured: 28 August 2026)". `ACQUISITION-RISK.md` §8 permits
acquisition only under thirteen conditions and records condition 1 — a human
reading three unretrieved terms-of-service sources — as unmet.

The audit's sharpest sentence is the one worth carrying: **the tripwire in
`ingestion/sources.py` protects the ingestion Lambda, not the serving table.**
It is a control on a door the data did not come through.

No code change can settle this. It is a conversation with the data teammates
and then a recorded answer. Surfaced in the README's open-questions list in
this change so it stops being invisible; it should be answered before any demo
outside the team.

### Finding 5 · "Everything is deployed and nothing is declared" — **CLOSED (`7eab93a`)**

The audit describes `infra/` as "design docs plus stub classes containing only
`TODO` comments and an `Annotations.addInfo` call." At HEAD, `service-stack.ts`
is 230 lines with zero `TODO`s, `stateful-stack.ts` 84 lines with zero, and two
stacks are deployed: `Grocery-Stateful-dev` (which contains no table resource
at all, so a stack delete cannot take the data) and `Grocery-Service-dev`.

Three stacks *are* still stubs — `frontend`, `ingestion`, `observability` — and
the hand-made service plane is still production, with the cutover deferred by
an explicit decision (`ARCHITECTURE.md` §3m). So "nothing is declared" was true
when written and is now wrong; "not everything is declared, and the declared
one is not yet serving" is the accurate version.

The audit's *conclusion* — "if only one thing, ship the CDK" — was right, and
was being acted on as it was written.

### Finding 6 · Behind the NZ market as a consumer product — **AGREED, no action**

The competitive research is the part of the audit this repository could not
have produced for itself, and the judgement is sound: competing on coverage,
history, alerts and a mobile app against two free incumbents is not a credible
plan. Recorded, no code implication.

---

## 2. Drift findings D1–D9

| # | Finding | Status at HEAD | Disposition |
|---|---|---|---|
| D1 | Provenance contradiction | **True** | NOT OURS — see Finding 4 |
| D2 | `DATA_SCHEMA.md` "100% match" recipes use substring semantics `design.md` §8 rejects | **True** | ACCEPTED (deferred) — below |
| D3 | Coverage instrument measures the wrong catalogue | **True** | **ACCEPTED — fixed** |
| D4 | Blocker is partly resolution, not data | Superseded by 15b | CLOSED — see Finding 3 |
| D5 | `dynamo.py` module docstring still says it Scans | **True** | **ACCEPTED — fixed** |
| D6 | `stateful-stack.ts` declares only GSI1 | **Fixed** in `7eab93a` | CLOSED |
| D7 | `FRONTEND-INTEGRATION.md` 20 days stale | **True, and worse than reported** | **ACCEPTED — fixed** |
| D8 | MCP pinned to `2024-11-05` | **True** | DECLINED — agree with the audit's own advice |
| D9 | `models.json` unit costs slightly stale | **Unverified** | ACCEPTED (deferred) — below |

### D3 — the instrument, and the tripwire behind it

The audit's version: `scripts/check_recipe_coverage.py` resolved through
`InMemoryPriceRepository()`, which defaults to `fixtures/products.json` — 26
products, 152 rows — while `src/recipes/base.py` described the measurement as
being against "300 items per store across 17 categories". The docstring named
the real catalogue; the code read the fixture one; the output named neither.

Confirmed, and **the audit understated it.** The same defect was in the forcing
test. `tests/test_recipes.py::test_the_imported_catalogue_still_cannot_be_planned_from`
guards the Task 15b decision by failing when the product catalogue grows enough
to price whole imported recipes — and it resolved through the *fixture*
catalogue, which is generated to a fixed shape by `scripts/generate_fixtures.py`
and **cannot grow**. The trigger could never fire. It read as a working control
and guarded nothing, which is the sixth-in-a-row shape this repository keeps
finding: a check that looks like it is working.

Fixed:

- `src/recipes/catalogue.py` (new) — a `Catalogue` that carries its own source
  path and size and `describe()`s itself. It also replaces three near-copies of
  the dataset-loading loop that had accumulated in the demo, the test and
  nowhere useful.
- `scripts/check_recipe_coverage.py` — defaults to the real catalogue, prints
  both catalogue and recipe-set identity on every run, gains
  `--catalogue`/`--recipes`, and **refuses to gate** (`--fail-under`, exit 2)
  from the fixture catalogue when the real one is available.
- `tests/test_recipes.py` — the forcing test resolves against `datasets/` and
  *skips* rather than falling back when it is absent; a new test asserts both
  catalogues agree that the imported recipes are unusable.

**The conclusion survived the correction**, which is the outcome that matters
and was not guaranteed:

| catalogue | best | median | at 100% |
|---|---|---|---|
| `datasets/` (528 products, 2,939 rows) | 75% | 17% | **0** |
| `fixtures/` (26 products, 152 rows) | 75% | 12% | **0** |

The 15b decision was sound. It was *reached* by luck, and it is now held by a
control. That distinction is the whole of D3.

### D7 — worse than the audit reported

The audit noted the guide's header was 20 days stale. Checking the body found
that the JSON captures had in fact been regenerated by Task 2 — but the two
hand-written event tables in §6 and §7 had not, and still showed
`The cheapest option is $2.97 at Pak'nSave Mangere.` The orchestrator has not
emitted money in prose since 2026-08-29; it emits
`The cheapest option is Pams Butter 500g at Pak'nSave Mangere.`

So the document a frontend team builds against was showing them a field
containing a price that the field no longer contains. Both tables were
re-derived from a real turn through `run_turn`, not edited by hand, and the
header now records what the warning said and why it is gone.

The general lesson is recorded in the guide: **a worked example is code that
nothing executes.** `samples/` has a regeneration command and a CI gate;
prose tables in Markdown have neither.

### D2 — the data team is building on matching semantics we refuse

`DATA_SCHEMA.md` §4 advertises two "100% ingredient match" star recipes whose
matches include `Cooking Oil → Canola Oil` and `Beef → Slow Cooker Roast Beef
Recipe Base`. That is substring matching, which `design.md` §8 rejects by name
with the example "substring matching resolves 'truffle oil' to canola oil — a
confident, cited, wrong price."

Not fixed here because the fix is not in this repository: it is telling the
data teammates which semantics govern, and agreeing what their demo scenarios
should say instead. Left as a named conversation rather than a silent
disagreement.

### D8 — MCP protocol version

Agreed with the audit's own recommendation: leave it. `2024-11-05` is harmless
for a local, default-off stdio façade with one approved consumer, and it only
becomes a blocker if remote MCP is ever approved — which §16 argues against and
this response agrees with.

### D9 — model unit costs

The audit says `config/models.json` records Claude Haiku 4.5 at $0.80/$4.00 per
million against a $1.00/$5.00 list. **The $1.00/$5.00 figure is the Anthropic
first-party rate.** Claude on Amazon Bedrock is partner-operated and priced
separately, so the config may be correct as a Bedrock rate and the audit may be
comparing against the wrong price list.

Deferred rather than changed, because changing a cost figure to match the wrong
list would be worse than leaving it. Settling it needs the Bedrock
`ap-southeast-2` price list read directly, and it is immaterial to routing —
it only affects any cost-per-task claim derived from `cost_for()`.

---

## 3. Code-quality recommendations

### "`build_graph()` is called on every request" — **ACCEPTED, fixed**

True. `run_turn` compiled a fresh LangGraph per turn while the handler
carefully cached the repository and model for exactly the latency reason that
undid.

`compiled_graph()` in `src/graph/build.py` memoises on the dependency pair.
Measured contemporaneously, same machine, back to back:

| | before | after |
|---|---|---|
| `run_turn`, offline | 17.16 ms | **2.81 ms** |
| full test suite | ~30.5 s | **~21 s** |

**Kept in proportion, because the audit called this "Must fix" on latency
grounds and that overstates it.** A deployed price check is 2,210 ms and a meal
plan 12,200 ms, both dominated by Bedrock. 13 ms is well under 1% of a real
turn. This is free, not important — worth taking because it costs nothing, not
because latency needed it.

The load-bearing part is the cache key, not the cache. `InMemoryPriceRepository`
takes a fixture path, so two of them can hold different catalogues; a cache that
collapsed them would answer a turn from the wrong catalogue while every
assertion in the system passed, because the graph would be internally
consistent and simply about the wrong data. Keying on identity prevents that,
and three tests pin it — same pair reuses, different pair does not, clearing
forces a recompile. Verified by mutation: keying on the model alone fails 2
tests, removing memoisation fails 1, making `clear_graph_cache` a no-op fails 1.

`tests/conftest.py` now clears the cache around every test. Several tests
monkeypatch a node module and depend on the graph being built *after* the
patch; they happened to be safe because each constructs its own dependencies,
and that was luck rather than a guarantee.

### "`nodes/__init__.py` is 835 lines" — **ACCEPTED (deferred)**

Agreed that splitting terminals and routers out would make the topology more
reviewable. Deferred deliberately: it is a pure move of code with no behaviour
change, and putting it in the same change as a graph-caching change and a
measurement correction would make all three harder to review. Worth its own PR.

### "The double query on the empty-result path" — **DECLINED**

The audit proposes returning the newest capture date from the first query
instead of re-querying without the freshness filter. The cost is one extra
round trip **only on the path that is already returning nothing**, and the code
states that trade explicitly at `src/graph/nodes/__init__.py:319`. Carrying an
extra field through the repository protocol and all three implementations to
save a round trip on the no-answer path is a worse trade than the one that is
there. Reconsider if the empty path ever becomes common enough to measure.

### "`MAX_QUERY_PAGES = 5` is a silent truncation" — **ACCEPTED (deferred)**

Correct, and it matches this repository's own rule that a silent cap should be
logged. It belongs with the missing throttle and stale-data metrics (Task 12's
open half) rather than on its own, because all three are "emit the metric, then
alarm on it" and the alarm config validates as a set.

---

## 4. What was declined, and why

### The audit's §16 "do not build" list — **AGREED IN FULL**

The AgentCore Runtime reviewer, AgentCore Gateway, recipe Knowledge Bases,
WebSocket transport, agentic checkout, and live retailer acquisition should all
stay written down, unbuilt and gated. The argument that a z-score against a
product's own price history would catch the $2,490 broccoli at a fraction of a
reviewer agent's complexity is correct.

One qualification: Task 14a built the reviewer's *boundary* — the sanitised
snapshot and the finding validation — deliberately, because those are needed
whoever reviews, including a person with a spreadsheet. That is not the same as
building the reviewer, and ADR 0002 still gates the Runtime.

### "Extract the verification harness as a library" (§19 Direction A, #19)

The strategic case is the most interesting thing in the audit and it is not
declined — it is simply not a Phase 0 action. It needs a decision from the
mentor and the team about what this project is *for*, and that conversation
should happen with the audit in hand.

### The 90-day roadmap as sequenced

Phase 0 as the audit defines it is largely what this change does. Its Phase 1
opens with "Pilot Tasks 9–11: CDK app; adopt tables read-only", which were
completed before the audit was published. Anyone working from that roadmap
should re-read it against §3m of `ARCHITECTURE.md` first.

---

## 5. Account-side items — a runbook, not actions

Nothing in this change touches AWS. These are the audit's account-side
recommendations, recorded so they are not lost, each with the reason it is not
being done from here.

| # | Action | Why not here |
|---|---|---|
| 1 | **API key on usage plan `v4yd7d`.** An unauthenticated endpoint invoking Bedrock is the audit's top security risk, and the only control today is a 5 rps stage throttle. | A live change to a service currently being demoed. Needs a decision about who holds the key and how the frontend gets it. |
| 2 | **Arm Req 12.5** — set `CORS_ORIGIN` and `APP_STAGE=pilot` together. | Blocked on the frontend's CloudFront origin existing. `ARCHITECTURE.md` §3h has the exact command. |
| 3 | **One paced 200-turn load run.** p99 is undefined at n=3, so Req 13.13's ~25s escalation trigger cannot be evaluated — and, as §1 Finding 2 notes, no latency-derived decision should move without it. | Spends real Bedrock quota and money; wants a deliberate session. |
| 4 | **Restore drill** from PITR to a new table name, with the elapsed time recorded. | An untested restore is a plan, not a capability — agreed, and it is an account action. |
| 5 | **Verify the Bedrock `ap-southeast-2` price list** and settle D9. | Read-only, but it is an external price list rather than a repository fact. |

---

## 6. What this change actually did

| Area | Change |
|---|---|
| `src/recipes/catalogue.py` | **New.** A `Catalogue` that names its own source and size; replaces three near-copies of the dataset loader |
| `scripts/check_recipe_coverage.py` | Names both catalogues in its output; `--catalogue` / `--recipes`; refuses to gate from the fixtures (D3) |
| `tests/test_recipes.py` | Forcing test repointed at the catalogue that can actually grow; new test asserting both catalogues agree |
| `tests/test_curated_recipes.py`, `Philip_demo/11` | Use the shared loader; demo output byte-identical, verified by diff |
| `src/graph/build.py`, `src/runner.py` | `compiled_graph()` memoised on the dependency pair; 17.16 ms → 2.81 ms per offline turn |
| `tests/conftest.py`, `tests/test_graph.py` | Cold cache around every test; three tests pinning the caching contract |
| `src/retrieval/dynamo.py` | Module docstring said "scans the base table" two commits after it stopped (D5) |
| `src/recipes/base.py` | Coverage figures given for both catalogues, with the D3 correction recorded |
| `FRONTEND-INTEGRATION.md` | Stale money re-derived from a real turn; header records what the old warning said and why it is gone (D7) |
| `design.md` §8, §9 | The streaming rejection's stated reason was obsolete; the conclusion now rests on the runtime/SnapStart conflict |
| `README.md`, `AGENTS.md` | Test counts 763 → 811; Task 15 row said "blocked" when 15b had shipped; removed a bullet that contradicted a decision recorded 23 lines above it |

**Gates:** 811 passed, 31 skipped; ruff check and format clean; `validate.py`
with all five negative controls; 19/19 demos; pyright unchanged at 4
pre-existing errors (`aws_lambda_powertools` absent from this venv, identical
on the baseline).

---

## 7. The one thing worth carrying forward

The audit's own summary of the drift pattern is the most useful sentence in it,
and it applies to the audit as well as to the repository:

> a claim that looked verified because other claims matched it.

Three of the audit's own findings had gone stale by the time it was published,
because four merges landed during the review. Two more were understated because
the checking stopped at the symptom — D3's instrument was wrong *and* the
forcing test behind it could never fire; D7's header was stale *and* the
examples underneath it were worse. And its highest-ranked technical
recommendation rested on an AWS capability that is real but does not compose
with two properties this service already has.

None of that makes it a bad audit. It found real things, it ran the gates
instead of quoting them, and it was explicit about what it could not verify.
It is a reminder that an audit is evidence, not a verdict — and that the
correct response to one is to go and check.
