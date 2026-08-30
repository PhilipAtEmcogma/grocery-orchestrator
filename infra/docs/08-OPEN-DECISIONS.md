# 08 — Open Decisions

> **Status: Design documentation. Not yet implemented.**
>
> These are the choices the team (and, where noted, the mentor) must settle
> before or during implementation of the CDK app. Each states the question, the
> options, the trade-off, and a **recommendation** — but the decision is yours.
> Record the outcomes as ADRs or in the Kiro spec so they don't get re-litigated.

## 1. Which DynamoDB tables are authoritative? — ✅ DECIDED (2026-08-29)

**The problem.** Two naming lineages exist:

- The **running service** uses `grocery-products-dev` (base + GSI `GSI1`) and
  `grocery-idempotency-dev` — proven by the orchestrator IAM policy and the
  README's "deployed" section.
- [`datasets/dynamodb_schema/`](../../datasets/dynamodb_schema/) describes
  `SmartGroceryProducts` (PK `primary_key`, GSI `CategoryPriceIndex`) and
  `SmartGroceryRecipes`, tagged `Owner: AUT-AWS-DataPipeline`.

These are **not the same tables named differently — they are two different data
*models*, built by two sub-teams for two jobs** (confirmed by reading the
retrieval code, the seed loader, and the actual data batches):

- **Lineage A — the orchestrator *serving* schema (`grocery-*-dev`).** Authored
  in [`DYNAMODB-SCHEMA.md`](../../DYNAMODB-SCHEMA.md). `grocery-products-dev`:
  PK `store_key`, SK `product_key`, **GSI1** (`product_key`/`gsi1_sk` =
  zero-padded price), money as a **String**, rich attributes (`canonical_name`,
  `unit_price_nzd`, `pack_grams`, `lat`/`lon`, `valid_date`, `on_special`).
  **Every line of running code reads this** — `src/retrieval/dynamo.py`,
  `ingestion/handler.py`, both IAM roles, the tests, the citation/grounding
  logic. Seeded today from `fixtures/products.json` (~150 curated items).
- **Lineage B — the data team's *raw collected dataset* (`SmartGrocery*`).**
  Described in [`datasets/DATA_SCHEMA.md`](../../datasets/DATA_SCHEMA.md).
  `SmartGroceryProducts`: PK `primary_key`, GSI `CategoryPriceIndex`
  (`category`/`price`), money as a **Number**, flat fields. Holds the **real**
  collected data — 285 Pak'nSave + 300 New World products + 175 TheMealDB
  recipes — in `batch-write-item` format. **No orchestrator code reads it.**

**The relationship is input → serving store, not two candidates for one slot.**
B is the real upstream data; A is the schema the orchestrator can actually
serve from.

**Consequences of the choice:**

- **Adopt Lineage A (recommended).** The existing service runs **unchanged**;
  IAM, retrieval, ingestion, citations, GSI1 cheapest-first, string-money
  exactness and meal-plan candidate search already target it. The real data in
  B becomes an **ingestion input**: transform B→A (map `primary_key`→
  `store_key`/`product_key`, `price` Number→String, derive `gsi1_sk`/
  `canonical_name`/`pack_grams`, etc.) in `ingestion/normalise.py`, then load.
  **Low risk.**
- **Adopt Lineage B.** You'd hold the real data in-table immediately, but you'd
  have to **rewrite the backend**: new retrieval keys, no GSI1 cheapest-first,
  reintroduce/fight the **float-money bug** (price is a Number), and **lose**
  `canonical_name`/`unit_price`/`pack_grams`/`lat`/`lon`/`valid_date`/
  `on_special` that grounding, location filtering, freshness and the meal
  planner require; citations (exact table+PK+SK) need re-specifying.
  **High cost; contradicts the authored schema and the grounding invariants.**

**Decision (Philip, 2026-08-29).** Adopt **Lineage A** as authoritative
(`grocery-products-dev`, `grocery-idempotency-dev`, later `grocery-meals-dev`).
**Use Lineage B as the raw upstream dataset now** — its value is the 585 real
products + 175 recipes — routed in through a **B→A transform in ingestion** (see
[03 → IngestionStack → Data source](03-STACK-SPECS.md)), not as a serving table.
If the physical `SmartGrocery*` tables exist in AWS, keep them as a raw-data
staging store or export to S3 and retire them; the CDK **does not** adopt
`SmartGrocery*` as serving tables. Rationale: this is a demo with a path to
production, so pulling in the real data early (via the transform) is worth it,
while the serving schema and all grounding invariants stay on Lineage A.

**Still required before Task 9 (implementation, not decision):** confirm the
live key schema with `aws dynamodb describe-table` and confirm which physical
tables exist with `aws dynamodb list-tables` ([06 §0](06-DEPLOYMENT-GUIDE.md)).

**Decided by:** Philip (service owner). Recorded in
[ADR 0003](../../docs/adr/0003-infrastructure-as-code-and-resource-adoption.md).

## 2. Table adoption strategy: reference (A) or `cdk import` (B)? (Task 9)

**Options** (full detail in [03 StatefulStack](03-STACK-SPECS.md)):

- **A — `fromTableAttributes`, unmanaged.** CDK only holds handles; zero
  replacement risk; not "full IaC" for the tables.
- **B — real `Table` + `RETAIN` + `cdk import`.** Full IaC; the tables are in
  CloudFormation; higher risk if the CDK definition doesn't exactly match the
  live schema.

**Recommendation.** **A for the pilot**, upgrade to **B later** once the team has
run `cdk import` on something disposable first. A delivers everything the CDK
needs from the tables (grantable handles) with no chance of data loss.

**Decision needed from:** the implementing engineer + reviewer.

## 3. Guardrail: create new in CDK (A) or adopt existing `b1xezpqe04kx` (B)? (Task 10)

**Options** (see [03 Guardrail](03-STACK-SPECS.md), [04 §8](04-SECURITY.md)):

- **A — CDK creates and owns a new Guardrail** from `config/guardrail.json`. New
  id; IAM follows the CDK token automatically; the existing `b1xezpqe04kx` is
  retired after cutover.
- **B — adopt the existing Guardrail id** and keep the hardcoded ARN.

**Recommendation.** **A.** The Guardrail is cheap to recreate, and CDK ownership
means the security policy is versioned with the stack and reproducible in
another account — which is the whole point of IaC. Retire the old one once the
new one is verified (13/13 must-block, 7/7 must-allow — Pilot Task 3 follow-up).

**Decision needed from:** security reviewer.

## 4. Step Functions: reuse the ASL JSON (A) or rebuild with L2 constructs (B)? (Task 13)

**Options** (see [03 IngestionStack](03-STACK-SPECS.md)):

- **A — pass [`config/ingestion-state-machine.json`](../../config/ingestion-state-machine.json)
  through as an ASL string**, substituting `${AWS_*}` with CDK tokens. Reuses the
  carefully-commented definition verbatim.
- **B — rebuild with `stepfunctions` / `stepfunctions-tasks` L2 API.**
  Type-checked, but re-expresses logic that already exists and is commented.

**Recommendation.** **A**, at least initially — the JSON's comments encode
*why* the Catch is inside the item processor and *why* `ResultPath` is null;
re-deriving that in TypeScript risks losing the reasoning. Move to B only if the
definition starts changing often.

**Decision needed from:** the implementing engineer.

## 5. Config-as-data: read `config/*.json` at synth (A) or port into TypeScript (B)?

**Options** (see [02 §6](02-CDK-SCAFFOLD.md)):

- **A — CDK reads the JSON at synth**, keeping one reviewable source of truth
  shared with the (still-present) apply scripts.
- **B — port policies/guardrail into TypeScript** and delete the JSON + apply
  scripts.

**Recommendation.** **A during migration** (both paths agree on one file), then
**B once the apply scripts are retired**, so there's no dead code. Don't do B
while the scripts are still the fallback — you'd have two sources drifting.

**Decision needed from:** the implementing engineer.

## 6. Does the running code read model ids / feasibility from SSM or env today?

**The question.** [03 ServiceStack](03-STACK-SPECS.md) writes `config/models.json`
and the feasibility floor to **SSM** (as the file headers predict). But the code
today reads model ids from **env vars** (`BEDROCK_MODEL_*`) and may read
feasibility from the bundled `config/feasibility.json`. If so, "operators retune
via SSM without a deploy" isn't true yet — it needs a small application change to
read from SSM at cold start.

**Recommendation.** For the pilot, **bake the values as env vars** (simple, works
with today's code) **and also publish them to SSM** as the forward path. Wire the
code to read SSM as a **separate, small application task** (not infra), then flip
the source. Track it as a follow-up on Pilot Task 7 (model plane) / Task 10.

**Decision needed from:** the application owner.

## 7. Frontend framework for the S3 + CloudFront chat UI (later)

The UI doesn't exist yet (there's a `FRONTEND-INTEGRATION.md` contract only).
Options, all static-hostable on S3+CloudFront (no server cost):

| Option | Pros | Cons |
|--------|------|------|
| **Static HTML/JS** | matches the original architecture doc; zero build; cheapest; fastest to demo | least structure; hand-rolled state |
| **React SPA (Vite)** | component structure; good DX; still a static export | a build step; more deps |
| **Next.js static export + Tailwind** | matches the boilerplate brief; Tailwind; familiar | heaviest tooling for a single chat page; static-export caveats |

**Recommendation (research-backed — full analysis in
[09-FRONTEND](09-FRONTEND.md)).** A **React (Vite) SPA**, or **plain static
HTML/JS** for the absolute minimum. Both are single-bundle SPAs that host on
S3+CloudFront (OAC, SPA error-response fallback) at $0. **Next.js static export
is the weakest fit** for a single anonymous chat page: its value is
SSR/SSG/SEO/routing (none needed here) and its multi-file export forces a
CloudFront URL-rewrite you'd otherwise avoid ([09 §2, §4](09-FRONTEND.md)).
Whatever the choice, it consumes the `POST /chat` contract in
[`FRONTEND-INTEGRATION.md`](../../FRONTEND-INTEGRATION.md) and its CloudFront
domain becomes the API's `CORS_ORIGIN`.

**Decision needed from:** Philip / the team (this was one of the original
clarifying questions). See [09-FRONTEND](09-FRONTEND.md) for the researched
comparison and references.

## 8. CI/CD: GitHub Actions + OIDC (A), CodePipeline (B), or both (C)?

**Options (research-backed — full spectrum + references in
[05-CICD §5](05-CICD.md)):**

- **A — hand-written GitHub Actions + OIDC.** Free; no long-lived keys; stable;
  most widely understood; reuses the existing credential-free CI.
- **B — `cdk-pipelines-github`.** Pipeline defined in CDK but *synthesised into*
  GitHub Actions workflows, still $0 + OIDC — but **experimental** (API may
  change).
- **C — CodePipeline / CDK Pipelines.** In the brief; ~$1/mo + build minutes;
  AWS-native; best for multi-account promotion at market stage.

**Recommendation.** Implement **A now** (free; adds OIDC to the team's AWS
experience; current best-practice consensus favours it over self-mutating CDK
Pipelines for most teams). Keep **C** documented ([05 §4](05-CICD.md)) for the
market build, and note **B** as the CDK-native $0 path to graduate to once the
team wants the pipeline in TypeScript and can track an experimental API.

**Decision needed from:** Philip / DevOps owner. See [05-CICD §5](05-CICD.md).

## 9. When does Cognito / WAF land?

Not in the pilot (anonymous). `security.md` gates them *before* any user-owned
or public managed surface. The API's authorizer seam is left explicit so adding
a Cognito authorizer later is a one-line method change ([03 API Gateway](03-STACK-SPECS.md)).

**Recommendation.** Defer both to the first non-anonymous milestone; note the
cost of WAF ([07 §4](07-COST-AND-SCALING.md)). No action now beyond keeping the seam.

**Decision needed from:** product owner / mentor, at the public-launch milestone.

## 10. The service plane already exists — adopt it, or replace it? (Tasks 9–11)

**Raised 2026-08-30**, after an account audit found that
[00-OVERVIEW](00-OVERVIEW.md)'s "what exists" table was wrong: the REST API
`grocery-orchestrator-api-dev` (`woqmel35lk`), the Lambdas, the `live` alias,
the state machine and an ENABLED daily schedule all exist and have since
2026-08-27. The docs said they did not, so every plan written before this date
assumed CDK would *create* them.

**The question.** For each existing service-plane resource, does CDK adopt it or
create a replacement and retire the original?

- **Adopt.** The URL survives, so the frontend integration and anything already
  pointed at `POST /dev/chat` keeps working. Cost: you inherit hand-made state
  nobody wrote down, and `cdk import` requires the CDK definition to match the
  live resource exactly — harder for an API with methods, integrations and a
  stage than for a table.
- **Replace.** A clean, fully-declared stack. Cost: a **new API id and therefore
  a new URL**, coordinated with whoever is consuming the old one, plus a cutover
  window on a live endpoint.

**Recommendation.** Split it, because the resources are not alike:

| Resource | Suggested | Why |
|---|---|---|
| DynamoDB tables | **Adopt** (already decided, §1–2) | Data loss risk dominates everything else |
| Lambdas + `live` alias | **Adopt** | Cheap to import; the alias is the SnapStart seam and the API points at it |
| REST API + stage | **Replace**, once a consumer is identified | It is small, fully specified in [03](03-STACK-SPECS.md), and its manual state is the least documented thing in the account. Replace *before* the frontend hard-codes the URL, not after |
| State machine + schedule | **Adopt** | The ASL is already config-as-data (§4) |
| Guardrail | Per §3 | Unchanged by this finding |

The recommendation to replace the REST API is contingent on it having no
consumer yet. **If the frontend team has already wired to `woqmel35lk`, adopt
instead** — a URL change imposed on a teammate to tidy our stack is the wrong
trade.

**RESOLVED 2026-08-30 — and the resolution is "not yet, and here is why".**

Importing the existing API Gateway tree means matching RestApi, Resource, two
Methods, two Integrations, Deployment, Stage and UsagePlan exactly. A mismatch
in `cdk import` is not a failed import; it is a REPLACEMENT of a resource that
is serving traffic.

And an import proves less than it looks. It inherits whatever the hand-made
resource has — including the parts nobody wrote down — so a stack built by
import can be complete-looking and untested. A fresh deploy proves the
definition is *sufficient*, which is the property IaC is actually for.

So `Grocery-Service-dev` deploys the whole plane under a `-cdk` name suffix,
beside the running one, and **parity was verified before anything was cut
over**:

| Request | hand-made | CDK |
|---|---|---|
| `cheapest butter` | paknsave Albany $9.49 Mainland Salted Butter | *identical* |
| `cheapest milk near Albany` | paknsave Albany $4.79 Pams Value Standard Milk | *identical* |
| `how much is truffle oil` | no_data | *identical* |
| `feed 3 people for 5 days on $80` | 5 meals, $37.32 | 5 meals, $31.74 |

The meal-plan difference is NOT a configuration difference. The same question
against the *same* endpoint three times returned $35.75, $31.74, $31.74 — plan
composition varies run to run, and the CDK figure sits inside the hand-made
one's range. Checking that before reporting a discrepancy is the difference
between a finding and a false alarm.

**What the CDK plane fixes that the hand-made one has wrong:**

- **Log retention.** `/aws/lambda/grocery-orchestrator-dev` is `null` — never
  expire. The CDK group is 14 days. `04-SECURITY` requires finite retention, and
  a log that never expires turns any future logging mistake into a permanent one.
- **The API Gateway account CloudWatch role**, which `docs/ARCHITECTURE.md` §7
  records as unset. CDK sets it — note this is ACCOUNT-LEVEL and therefore
  benefits the hand-made API too.
- **Stage tracing on from the start**, rather than added by hand afterwards.

**The cutover is the outstanding step and it is a decision, not a task.** It
changes the URL that `scripts/measure_latency.py`, `Philip_demo/_demo_support.py`
and several documents name. Set `NAME_SUFFIX=''`, deploy, repoint the consumers,
and delete the hand-made resources — in that order, with a `cdk diff` read
before each.

**Decision needed from:** Philip, once the demo work settles.

## 11. Two unidentified resources in the account — whose are they?

**Raised 2026-08-30.** The same audit found a REST API `Chatbot`
(`gxbx2006zc`) and a Lambda `Chatbot` (**python3.14**), both created
2026-08-26, in account `097087133897` / `ap-southeast-2`. Nothing in this
repository references either, and the runtime is not this project's pinned
3.13.

**Status: Philip is asking the team.** Detail in
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) §3b.

**What needs deciding once an owner is found:**

1. Are they in scope for CDK adoption? (Presumed **no** — but record it.)
2. What does the `Chatbot` Lambda's execution role grant? An unowned role with
   DynamoDB or Bedrock access is a larger question than an idle endpoint, and
   is the part worth checking first.
3. Keep, hand over, or retire — **the owner's call, not ours.** Do not delete
   either without agreement; idle they cost nothing, and an unrequested
   deletion is worse than an unused endpoint.

**Decision needed from:** whoever claims them, then Philip.

---

## Decision log (fill in as you go)

| # | Decision | Choice | Date | By | Recorded in |
|---|----------|--------|------|-----|-------------|
| 1 | Authoritative tables | Lineage A serving; B = upstream via B→A transform | 2026-08-29 | Philip | ADR 0003, §1, [03 IngestionStack](03-STACK-SPECS.md) |
| 2 | Adoption strategy | **A — reference, unmanaged** | 2026-08-30 | Claude/Philip | `lib/stateful-stack.ts`; template has NO table resource, so CDK cannot replace them |
| 3 | Guardrail create vs adopt | **Adopt `b1xezpqe04kx` v2 by id** | 2026-08-30 | Claude/Philip | Creating a second Guardrail means two policies to keep in step and a second thing to red-team. The id and NUMBERED version are Lambda env vars, asserted at synth for prod |
| 4 | Step Functions ASL vs L2 | | | | deferred with Task 13 |
| 5 | Config read vs port | **A — read `config/*.json` at synth** | 2026-08-30 | Claude/Philip | Both paths agree on one file during the migration; port to TypeScript once the apply scripts retire |
| 6 | SSM vs env for model/feasibility | **Both, honestly labelled** | 2026-08-30 | Claude/Philip | Env vars drive the running code; SSM parameters are published as the forward path and marked NOT read at runtime. Wiring the code to read SSM is a separate application task |
| 7 | Frontend framework | | | | teammates' scope |
| 8 | CI/CD approach | | | | not yet needed |
| 9 | Cognito/WAF timing | | | | after the anonymous pilot |
| 10 | Adopt vs replace the service plane | **Neither yet — deploy in parallel, verify, then decide** | 2026-08-30 | Claude/Philip | See §10 below; parity verified, cutover outstanding |
| 11 | Ownership of `Chatbot` API + Lambda | | | | still unowned |
