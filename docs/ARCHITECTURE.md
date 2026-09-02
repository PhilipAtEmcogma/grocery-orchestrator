# Deployed architecture — dev

Reconciliation of the reviewed architecture diagram against
`.kiro/specs/design.md` and the `ap-southeast-2` account, plus the record of
what is deployed. Dated 2026-08-27, amended 2026-08-29.

**What changed on 2026-08-29:** the Guardrail moved to **version 2** after the
live red-team run found it refusing benign grocery queries (the foraging topic
was scoped to an ingredient rather than an activity); PITR was enabled on the
two upstream data tables and deliberately left off the idempotency cache; and
the idempotency table's claims became owner-fenced, verified against the real
table. The application-layer Pilot Tasks 1-7 are closed.

**Corrected 2026-08-30.** The 2026-08-29 amendment previously claimed "nothing
here is deployed as a service yet — the Lambda, API Gateway and alias in the
diagram below are the TARGET shape, not the current account." **That was
false.** It contradicted §3, §5 and §6 of this same file, which were correct,
and it propagated into the README, `AGENTS.md` and `infra/docs/00`. Everything
in §3 was re-verified against the account on 2026-08-30 and all of it exists.
Nothing in the diagram below is aspirational except the dashed retailer link
and the S3/CloudFront frontend.

The correction is worth more than the fact. A document describing intent was
edited to overrule a document describing an account, and four files then agreed
with each other and disagreed with AWS — the same shape as every other finding
in this repository: **a claim that looked verified because other claims matched
it.** Check the account.

This file is the **deployment record**: what exists in the account, what it is
wired to, and what was learned deploying it. `AGENTS.md` remains the working
agreement and `.kiro/specs/` remains the specification — neither is superseded
here. When they disagree with this file, they are describing intent and this
file is describing an account, and both are worth reading.

The diagram was a **presentation view of the architecture already specified**,
not a change to it. Four things needed correcting before it could be built;
those are §2.

---

## 1. Shape

```mermaid
flowchart LR
  U[User Browser] -->|HTTPS| FE["React Frontend<br/>S3 + CloudFront<br/><i>teammates' scope</i>"]
  FE -->|"POST /chat"| AGW["API Gateway REST<br/>grocery-orchestrator-api-dev"]

  subgraph AWS["AWS Security Boundary"]
    AGW -->|invoke alias| ORC["Lambda Orchestrator<br/>grocery-orchestrator-dev:live<br/>SnapStart, deterministic LangGraph"]
    ORC -->|"query"| P[("grocery-products-dev<br/>+ GSI1")]
    ORC -->|"conditional write"| I[("grocery-idempotency-dev<br/>TTL")]
    ORC -->|"grounded prompt/response"| BR["Bedrock<br/>Nova Lite / Nova Pro<br/>Guardrail b1xezpqe04kx v2"]
    ORC -->|EMF + traces| CW["CloudWatch<br/>Logs, Metrics, Alarms<br/>X-Ray"]
    IAM["IAM least-privilege<br/>4 roles, one per principal"] -.-> ORC

    EB["EventBridge Scheduler<br/>daily 03:00 NZ"] --> SF["Step Functions<br/>Inline Map, 3 branches"]
    SF --> ING["grocery-ingestion-dev<br/>one retailer per invocation"]
    ING -->|writes prices| P
  end

  ING -.->|"GATED — ACQUISITION-RISK §8"| SRC["NZ Retailers<br/>Pak'nSave · Woolworths · New World"]

  style SRC stroke-dasharray: 5 5
```

Only the retailer link is dashed. Everything else is deployed and was exercised.

## 2. Corrections applied to the diagram

**The price arrow terminated at Bedrock.** Drawn literally, ingestion would
feed prices into the model rather than into storage, inverting invariant 1 —
no price may originate from model generation. Prices land in
`grocery-products-dev`; Bedrock reads only the prompt the orchestrator builds
from already-retrieved records.

**Step Functions was missing.** The diagram went `EventBridge -> one Lambda ->
three retailers`. `tech.md`, `design.md:33` and `tasks.md:94` all specify
`EventBridge -> Step Functions Inline Map -> per-source adapters`. The specs
win, and the reason is now load-bearing in the deployed definition: `Catch`
sits *inside* the item processor, so a retailer that fails does not abort the
Map and discard the retailers that already succeeded.

**"Sessions" is the idempotency table.** The account has products and
idempotency; there is no sessions table and none is planned for the pilot.
`grocery-idempotency-dev` is already session-scoped with TTL. A genuine
conversation-state store would open a Privacy Act 2020 workstream first —
`security.md` line 25.

**EventBridge and ingestion sat outside the security boundary.** They are AWS
services inside the account. Only the retailer sources are external, and that
is the boundary worth drawing, because it is where untrusted data enters.

## 3. What is deployed

**Every row below was re-verified against the account on 2026-08-30** with
`aws apigateway get-rest-apis`, `lambda list-aliases`, `apigateway get-stages`
and `scheduler list-schedules`, plus a live `POST /dev/chat` returning HTTP 200.
All of it exists.

| Resource | Identifier | Notes |
|---|---|---|
| Orchestrator Lambda | `grocery-orchestrator-dev` | python3.13, x86_64, 1024 MB, 30 s, X-Ray Active |
| Published version / alias | `8` / `:live` | SnapStart `OptimizationStatus: On`; v6 (current code), v7 (freshness 45), v8 (GSI2 candidates) all 2026-08-30 |
| Orchestrator role | `grocery-orchestrator-dev-role` | `config/iam-orchestrator-role.json` |
| REST API | `grocery-orchestrator-api-dev` (`woqmel35lk`) | regional, stage `dev`, throttle 5 rps / burst 10, X-Ray tracing ON (enabled 2026-08-30) |
| Endpoint | `POST /dev/chat` | unauthenticated; see §7 |
| Ingestion Lambda | `grocery-ingestion-dev` | 512 MB, 120 s, handler `ingestion.handler.lambda_handler` |
| Ingestion role | `grocery-ingestion-dev-role` | `config/iam-ingestion-role.json`; read+write on products only, no Bedrock, no idempotency |
| State machine | `grocery-ingestion-dev` | `config/ingestion-state-machine.json` |
| Schedule | `grocery-price-refresh-dev` | `cron(0 3 * * ? *)` Pacific/Auckland, ENABLED |
| Products table | `grocery-products-dev` | **2,759 items**, the real catalogue only, GSI1 + GSI2, PAY_PER_REQUEST |
| Idempotency table | `grocery-idempotency-dev` | TTL ACTIVE |
| Guardrail | `b1xezpqe04kx` version `2` | v2 published 2026-08-29; DRAFT deliberately not granted in IAM |
| CDK stacks (deployed) | `Grocery-Stateful-dev`, `Grocery-Service-dev` | bootstrapped 2026-08-30; service plane deployed in parallel under `-cdk`, see §3m. `Grocery-Obs`, `-Ingestion`, `-Frontend`, `-Reviewer` are DEFINED in `infra/` but NOT deployed (the Reviewer stack, ADR 0002 gate 5, also waits on the `AWS::BedrockAgentCore::Runtime` CFN type reaching ap-southeast-2 — `infra/lib/reviewer-stack.ts`). |
| SNS topic | `grocery-orchestrator-alarms-dev` | **8 alarms**; 2 confirmed email subscribers; Budgets granted publish |
| Dashboard | `grocery-orchestrator-dev` | 9 widgets over the EMF metrics and the gateway |
| Budget | `grocery-orchestrator-monthly-dev` | $25/month, alerts at 50/80/100% actual + 100% forecast |
| Usage plan | `grocery-orchestrator-dev-plan` (`v4yd7d`) | 5 rps / burst 10 on stage `dev`; created 2026-08-30 |

**One artefact, two functions.** `scripts/build_lambda.py` now includes
`ingestion` in `INCLUDE_DIRS`, and the same `build/lambda.zip` is deployed to
both functions with different handlers. Two zips would mean two builds to keep
in step and two artefacts for the CI `package` job to verify, for about 10 KB
of Python. The functions stay separate — separate roles, separate invocation
paths — and only the artefact is shared.

**x86_64, not arm64.** `build_lambda.py` pins
`--platform manylinux2014_x86_64` and the package carries compiled wheels
(`pydantic_core`, `orjson`, `xxhash`). Architecture is immutable after create,
so it was matched to what CI verifies rather than guessed at.

**The alias is what gets invoked, not `$LATEST`.** SnapStart applies to
published versions only. An integration pointed at the unqualified function ARN
silently forfeits it while still working — nothing breaks, it just gets slower.

## 3a. Code refreshed to current `main` — 2026-08-30

**Resolved.** Alias `live` served version `5` (published 2026-08-27) until
2026-08-30, which predated Pilot Tasks 4-7. The defect that mattered is gone:
the endpoint no longer invents a `$0` budget from a message that never
mentioned money.

| Request | v5 (until 2026-08-30) | v7 onwards |
|---|---|---|
| `feed my flat of 3 this week` | `BUDGET_INFEASIBLE`: *"I couldn't build a plan within $0"* | `clarification` asking what they want to spend |
| `cheapest butter` | five citations, presented as current | `STALE_DATA` naming the 2026-07-31 capture date |

### The published versions, and why no document states the current one

| version | what it added | recorded in |
|---|---|---|
| 5 | `main` at 2026-08-27, predating Pilot Tasks 4-7 | §3a |
| 6 | `main` at commit `2412ac3` | §3a |
| 7 | the freshness decision, 14 -> 45 days | §3c |
| 9 | `BEDROCK_GUARDRAIL_VERSION` corrected from `1` to `2` | §3f |
| 11 | the real 2,759-row catalogue, GSI2, Scan revoked | §3i, §3d |

**A VERSION NUMBER IN PROSE IS A CLAIM THAT EXPIRES AND NOTHING RE-CHECKS IT.**
Until 2026-08-31 four numbers described one alias across three documents at
once: this section said 7 while its own table header said "v6 (now)", §3f said
9 forty lines later, and `README.md` said 7 twice and 11 once, forty lines
apart in the same section. None of them was a lie when it was written; each was
a snapshot nobody went back to. It is the same shape as
`infra/test/service-stack.test.ts` saying "SKIPPED until ServiceStack is
implemented", and it has the same fix -- state the condition, not the answer.

So the table above is a HISTORY, which cannot go stale, and no document states
which version is live. One command does:

```bash
aws lambda get-alias --function-name grocery-orchestrator-dev --name live     --query FunctionVersion --output text
```

`AGENTS.md` already required running it before quoting a live behaviour as
current. What has changed is that no document now offers a number to quote
instead.

### How it was done, and why not the four commands this section used to give

The procedure previously written here -- build, update code, publish, move the
alias -- moves the alias before anything has invoked the new version. Two
reasons that is the wrong order here:

- **`build_lambda.py` cannot verify its own archive on Windows.** It says so and
  skips the import check, because the manylinux wheels will not load on the build
  host. So a locally built archive is *unverified* until something runs it, and
  the first thing to run it should not be live traffic.
- **SnapStart publishes asynchronously.** A freshly published version sits in
  `State: Pending` while the snapshot is built. Pointing an alias at it before it
  is `Active` is a race.

The order actually used, which keeps live traffic on the old version throughout:

```bash
python scripts/build_lambda.py
aws lambda update-function-code --function-name grocery-orchestrator-dev     --zip-file fileb://build/lambda.zip          # changes $LATEST only; alias untouched
aws lambda wait function-updated-v2 --function-name grocery-orchestrator-dev
aws lambda publish-version --function-name grocery-orchestrator-dev   # -> 6, State: Pending
aws lambda wait published-version-active     --function-name grocery-orchestrator-dev --qualifier 6            # SnapStart snapshot
aws lambda invoke --function-name grocery-orchestrator-dev --qualifier 6     --cli-binary-format raw-in-base64-out --payload file://probe.json out.json
# ONLY after that returns a sane body:
aws lambda update-alias --function-name grocery-orchestrator-dev     --name live --function-version 6
```

The direct invoke against `--qualifier 6` is the step that earns the cutover: it
proved the archive imports at all, which the build could not. Rollback is one
command -- `update-alias ... --function-version 5`.

Two Windows traps hit on the way, both the same shape as the `bash -c` finding
in `AGENTS.md` -- the tooling altering what was tested:

- `--payload file:///tmp/p.json` and an output path of `/tmp/out.json` do not
  refer to the same place for Git Bash and for the Windows `aws.exe`. Use a real
  Windows path for both.
- The first attempt suppressed stderr with `2>&1 >/dev/null`, so the failure
  surfaced as a confusing `FileNotFoundError` from the *reader* rather than the
  actual CLI error. Do not silence the tool you are trying to verify.

### Dependencies moved too

The rebuild resolved newer versions than the 2026-08-27 build, because
`requirements.txt` pins nothing: boto3 1.43.81 -> 1.43.83, pydantic 2.13.4 ->
2.13.5, langchain-core 1.6.0 -> 1.6.1, wrapt 2.3.0 -> 2.4.0, and others. So
version 6 differs from version 5 by more than this repository's own commits, and
a future rebuild will differ again. Pinning is worth doing before the pilot;
until then, a redeploy is not a reproducible operation.

## 3b. Two resources in the account that this project does not own

`aws apigateway get-rest-apis` and `lambda list-functions` on 2026-08-30 also
returned, in the same account (`097087133897`, `ap-southeast-2`):

| Resource | Identifier | Created | Runtime |
|---|---|---|---|
| REST API | `Chatbot` (`gxbx2006zc`) | 2026-08-26T16:32 +12:00 | — |
| Lambda | `Chatbot` | 2026-08-26T04:58 UTC | **python3.14** |

**Nothing in this repository references either.** They predate
`grocery-orchestrator-api-dev` by a day, and the python3.14 runtime is not this
project's pinned 3.13, which is some evidence they are not a stray artefact of
our own deployment scripts.

**Status: open — Philip is asking the team.** Until someone claims them, treat
them as unidentified: another `Chatbot` REST API is a second public endpoint in
a shared account, and an unowned Lambda is an unowned execution role.

Three things worth settling when an owner is found:

- **Whose are they, and are they still wanted?** If they are a teammate's
  frontend spike, they belong in that teammate's documentation, not deleted by
  us.
- **What can they reach?** The relevant question is the execution role, not the
  function — an unowned role with broad DynamoDB or Bedrock grants is a larger
  finding than an idle endpoint.
- **Do they belong in the CDK adoption scope (Pilot Task 9)?** Almost certainly
  not, but that is a decision to record rather than an assumption to make.
  Tracked in [`infra/docs/08-OPEN-DECISIONS.md`](../infra/docs/08-OPEN-DECISIONS.md) §10.

**Do not delete either without an owner's agreement.** They cost nothing idle,
and a deletion nobody asked for is worse than an endpoint nobody uses.

## 3c. Freshness threshold raised 14 -> 45, and why — 2026-08-30

**The problem.** Version 6 began enforcing price freshness. Every one of the 152
seeded rows carries `valid_date: 2026-07-31`, so at 30 days of age against a
14-day `max_price_age_days` the endpoint answered `STALE_DATA` to every priced
query. Correct behaviour -- presenting a 30-day-old comparison as "the cheapest
price" is the exact claim `ACQUISITION-RISK.md` finds the Fair Trading Act
attaches to -- but it left the deployed service unable to demonstrate anything
priced.

**Decision (Philip, service owner, 2026-08-30): raise `max_price_age_days` to
45.** Recorded in full in `config/freshness.json` under `_decision_2026_08_30`,
which is the durable copy; this is the deployment-side summary.

- **Why 45:** clears the 30-day fixture snapshot with a fortnight of headroom,
  so the demo does not break again part-way through the sprint.
- **What it costs:** 45 days spans roughly six weekly special cycles rather than
  two. A comparison drawn at the limit of that window can be wrong in exactly the
  way the 14-day figure existed to prevent.
- **Why that is acceptable here:** the dev stage serves *fixture* prices to the
  project team, not real prices to real shoppers.
- **Revert when:** real ingested prices with genuine capture dates back the
  serving table (Pilot Task 13). Do not carry 45 into any stage a shopper can
  reach.

**The rejected alternative was re-stamping the fixtures' `valid_date` to today.**
That fabricates provenance -- those prices were invented on 2026-07-31, and a
later stamp asserts a capture that never happened. `AGENTS.md` lists "publish a
price without its capture date" under **Do not**, and the point of that rule is
the date being *true*, not merely present. Raising a documented threshold is
visible and reversible; rewriting a capture date is neither, and it would make
the staleness path untestable against real conditions.

**`config/` is bundled into the Lambda archive**, so a config change is a
deploy, not a live setting. This one shipped as version `7`. That is also the
argument for the SSM work in Pilot Task 7b: an operator retuning a threshold
should not need a Lambda release.

### Verified live after the change

All four paths, through `POST /dev/chat` on version 7:

| Request | Result |
|---|---|
| `cheapest butter` | `price_comparison`, 5 citations across 5 stores |
| `cheapest butter near Albany` | `price_comparison`, **1 citation, Devonport only** -- named regions working in production for the first time |
| `feed my flat of 3 this week` | `clarification` asking for the budget |
| `feed 3 people for 5 days on $80` | `meal_plan`, 18 citations, 2 stores |

## 3d. GSI2 added, and the Scan permission removed — 2026-08-30

`candidates_for_budget` ran a full-table `Scan` on every meal-plan turn. It now
issues one `Query` per category against **GSI2** (partition `category`, sort
`gsi2_sk` = zero-padded cents + product key + store key). This closes Pilot Task
6b, which was deferred until there was load evidence to choose the index on --
`DYNAMODB-SCHEMA.md` has the full reasoning.

Applied in this order, which matters:

1. **Create the index.** Safe while the alias still served the old code, since
   nothing queried it yet.
2. **Re-seed the table.** The 152 existing rows had no `gsi2_sk`, so the
   backfill produced an ACTIVE index holding **zero items**. A sparse GSI is
   silent -- DynamoDB simply omits an item with no sort-key attribute, with no
   error anywhere -- so a deploy at this point would have produced meal plans
   with no candidates and nothing to explain why. `scripts/load_seed_data.py`
   now writes the attribute.
3. **Update IAM**, then deploy version 8 and move the alias.

**`dynamodb:Scan` was removed from the orchestrator role**, not merely left
unused. That turns the deploy into its own proof: a live meal plan succeeded
after the permission was gone, which cannot happen if anything still scans. A
permission nothing needs is one somebody can quietly start using again.

Verified live on version 8: `feed 3 people for 5 days on $80` returns a 5-meal
plan at $57.25 payable, and `vegetarian dinner plan for 2 for 3 days on $50`
returns 3 meals at $27.92.

## 3e. Store coordinates, and a sentinel that was not one — 2026-08-30

The data team's catalogue carries no geography, so the first version of
`ingestion/lineage_b.py` wrote `lat`/`lon` as `0.0` and described it in a
comment as fail-closed.

**0.0/0.0 is a real position in the Atlantic.** `NearFilter.covers()` computed a
genuine ~18,000km distance and excluded every record, so a shopper who sent
coordinates matched nothing and the graph reported `no_data` -- "I don't have
price data near you" about a supermarket in the same suburb. That is exactly the
silent-exclusion defect Pilot Task 5a fixed for the store filter, reintroduced
through a different door, and it is the shape this repository keeps meeting: a
wrong value that produces plausible behaviour is worse than a missing one,
because nothing distinguishes it from the value being right.

Now `config/store-locations.json` -- thirteen Auckland suburbs, config-as-data
alongside `regions.json`, and an unknown store **raises** rather than
defaulting. The coordinates are suburb centroids accurate to roughly a
kilometre, flagged as unreviewed with the same standing as the region
membership; an error costs a shopper one option at the edge of a radius, which
is the under-matching direction this project prefers everywhere. A test asserts
they agree with the fixture catalogue's own per-record coordinates, so the two
cannot drift about where a suburb is.

## 3f. Guardrail version drift — FIXED 2026-08-30

`grocery-orchestrator-dev` applied `BEDROCK_GUARDRAIL_VERSION=1` while the
resource was at version 2, every document quoted version 2, and the qualifying
13/13 + 9/9 evidence was measured against version 2. **The running service
applied a version nothing had signed off.**

It was not cosmetic. `how much is truffle oil` is a `must_allow` case in
`evals/cases/guardrail.json`, and under v1 the live endpoint returned
`GUARDRAIL_BLOCKED` for it. A documented must-allow was failing in production
while the recorded evidence said 9/9.

**Fixed:** the environment variable is now `2`, published as version `9`, alias
moved. Both `must_allow` mushroom cases verified live afterwards:
`how much is truffle oil` and `price of dried porcini mushrooms` both pass.

`update-function-configuration` REPLACES the entire environment map, so the
current set was read first and rewritten whole; dropping a key here would have
been the §3g failure exactly.

### What this cost to find, and the correction it forced

Chasing it produced a wrong intermediate conclusion worth recording, because
the mistake is instructive. Testing `cheapest button mushrooms` through the
endpoint showed it blocked, and that looked like more evidence of the drift. It
is not: applying both guardrail versions directly shows v1 and v2 block that
phrase IDENTICALLY. It was never a v2 fix and is not in the must-allow set.

The precise behaviour of version 2, measured with `apply-guardrail` rather than
inferred:

| Input | v2 |
|---|---|
| `mushrooms` | blocked |
| `price of mushrooms` | blocked |
| `button mushrooms` | blocked |
| `cheapest button mushrooms` | blocked |
| `mushroom soup` | blocked |
| `how much are mushrooms at Pak n Save` | allowed |
| `price of dried porcini mushrooms` | allowed |
| `how much is truffle oil` | allowed |

So **deferral 3d is broader than "the unqualified noun" as recorded**: a light
qualifier like "button" does not help either, and only a strong retail context
-- naming a retailer, or a specific culinary product like dried porcini -- gets
through. The topic definition explicitly says "Shop-bought mushrooms are not
this topic" and the managed classifier does not honour it. That is the finding
3d anticipated: the classifier cannot separate the retail and foraging senses,
and no amount of definition wording has moved it.

### Why no gate caught the drift

**Nothing offline can read a deployed environment variable.** The eval harness
goes through `lambda_handler`, so it does measure the real path -- but it
measures the path in the environment it is run in, which was a laptop with
`BEDROCK_GUARDRAIL_VERSION=2` exported by hand. Production had `1`. Same code,
same harness, different answer, and nothing compared the two.

The general form: evidence is only about the configuration it was collected
under, and this repository had no way to state which configuration that was.
§3g is the structural fix.

## 3g. Production fail-closed check (Req 12.5) — IMPLEMENTED 2026-08-30

`_dependencies()` selects by environment: `USE_DYNAMODB=1` picks DynamoDB,
`USE_BEDROCK=1` picks Bedrock, **and anything else falls through to the fixture
repository and the scripted model.** Drop one variable in production and the
endpoint keeps returning HTTP 200 with well-formed, grounded, arithmetically
verified citations -- computed from 26 invented products by a rule-based
stand-in. Every invariant holds. No metric looks wrong. The answers are simply
not about real prices. That is worse than an outage, because an outage is
visible.

`assert_production_configuration()` in `src/handler.py` now runs **before any
fallback is selected** -- checking afterwards would report a misconfiguration
the process had already worked around. When `APP_STAGE` is `prod`, `production`
or `pilot`, it requires `USE_DYNAMODB=1`, `USE_BEDROCK=1`, a guardrail id, a
**numbered** guardrail version, and a non-wildcard `CORS_ORIGIN`. 21 tests.

Three details that are the point rather than decoration:

- **It compares `USE_DYNAMODB` against `"1"` exactly**, because the selector
  does. `USE_DYNAMODB=true` reads as enabled to a human and picks fixtures in
  code, and that gap is the whole failure mode.
- **`DRAFT` is refused.** It moves, so evidence gathered against it describes
  whatever the policy was that day -- the same reason IAM deliberately does not
  grant DRAFT.
- **Every problem is listed, not just the first.** One deploy, one fix, rather
  than a sequence of failed deployments.

An unset `APP_STAGE` is NOT production. Defaulting the other way would break
every offline test, both eval harnesses, the demos and the dev server on the
day it landed, which is a good way to have the check deleted. Setting the stage
is the deploy's job -- Pilot Task 10, and part of the env-var contract in
`infra/docs/01`.

**Not yet set in the account.** The live function has no `APP_STAGE`, so the
check is inert there today. That is deliberate: `CORS_ORIGIN` is currently `*`
and would fail the check, and tightening it needs the frontend's origin to
exist. Setting `APP_STAGE=pilot` is the last step of Pilot Task 10, and the
check is what makes that step meaningful.

### A gap this surfaced, and did not close

A `ConfigurationError` is caught by the handler's error boundary and mapped to a
contract-valid `INTERNAL_ERROR` -- correct, because "no path out without a
contract-valid body" is a hard invariant here. But it logs `unhandled_exception`,
and the `HandlerEscaped` metric filter binds to `{ $.message = "handler_escaped" }`,
which only the OUTERMOST boundary emits.

So a fully misconfigured production stage would return `INTERNAL_ERROR` on every
turn, at HTTP 200, and **fire no alarm at all**: not `handler-escaped` (wrong
message) and not `api-5xx` (not a 5xx). The two deployed alarms do not cover the
most consequential failure the service has.

That is alarm coverage, not a defect in this check -- Req 12.8 already asks for
measured alarms beyond the first two, and it is Pilot Task 12 work. Recorded
here so the two facts stay attached to each other.

## 3h. CORS is still `*`, and cannot be fixed here yet — BLOCKED

`security.md` and Req 12.5 both require a production stage to reject wildcard
CORS, and `assert_production_configuration()` enforces it. The deployed function
still sets `CORS_ORIGIN=*`.

**This is not an oversight and cannot be closed from this repository.** Strict
CORS means naming ONE origin, and the origin is the frontend's CloudFront
domain, which does not exist -- the S3 + CloudFront stack is
`infra/docs/09-FRONTEND.md`, unbuilt, and teammates' scope. There is nothing to
name.

`infra/docs/03-STACK-SPECS.md` already permits this precisely: dev may use `*`
**only** while the stage is non-production. That is why `APP_STAGE` is unset --
arming the check today would fail startup on a value that has no correct
setting yet.

The fix, when the CloudFront domain exists, is one variable and one alias move:

```bash
# read the current map first: update-function-configuration REPLACES it
aws lambda update-function-configuration --function-name grocery-orchestrator-dev     --environment "Variables={...,CORS_ORIGIN=https://dxxxx.cloudfront.net,APP_STAGE=pilot}"
```

Setting `APP_STAGE=pilot` in the same change is deliberate: it arms Req 12.5 at
the moment the last thing blocking it is gone, rather than leaving an inert
check nobody remembers to turn on.

## 3i. The real catalogue is loaded — 2026-08-30

2,759 rows from the data team's collected catalogue are now in
`grocery-products-dev`, via `LineageBSource` and `refresh()`. Provably
idempotent: the second dry run reports **0 added, 0 changed, 2,759 unchanged**,
which is what idempotent looks like from the outside rather than a claim.

From 3,000 raw rows: **61 dropped** as non-food (pet food), **180 collapsed** as
duplicates, **74 re-classified** by the dietary safety override, leaving 2,759.
A conservation test asserts kept + dropped + collapsed equals the input, because
a row that vanishes unaccounted for is a product nobody can be shown and nobody
is told about.

### The duplicate collision, found by loading rather than by reading

`BatchWriteItem` refused the first load: *"Provided list of item keys contains
duplicates"*. The base table key is `(store_key, product_key)` and one store
stocks two BRANDS of the same product at the same size -- `Pams Mixed Berries`
and `Frozen Harvest Mixed Berries`, both 500g, both Albany. `derive_product_key`
ignores brand deliberately, so the same product compares across Pak'nSave and
New World; the cost is that it also collapses two brands within one store.

96 collisions in Pak'nSave alone. **Nothing offline had exercised it**, because
the fixtures carry exactly one product per key by construction -- a shape the
real catalogue does not have.

Resolved by keeping the cheapest per (store, product), which is the answer the
product already gives: the dearer brand of an identical product at the same
store is never the answer to "what is the cheapest X", and nothing
brand-specific is reachable since `resolve_product_key` matches on name and
size. Ties break on display name, so a re-run cannot report `changed` on a day
nothing changed.

## 3j. One catalogue — fixture rows removed 2026-08-30

> **THE FIXTURES CAME BACK, the 2026-09-01 parity re-run caught it (§3s), and it
> is now fixed.** This section describes a removal that happened, was then
> silently undone, and has now been redone and guarded. On 2026-09-01 the live
> endpoint again returned the fixture answers below inverted
> (`cheapest milk near Albany` → New World **Devonport** $4.94, `cheapest butter`
> → Pak'nSAVE **Mangere** $2.97) — fixture rows at fixture-only suburbs, matched
> byte-for-byte. Mechanism (full detail in
> `docs/OPEN-REVIEW-near-filter-drift.md`): `scripts/load_seed_data.py` with no
> flag LOADS, so a plain run had re-added all 152 fixture rows, which shadow the
> real catalogue through the synonym candidate order. **Fixed 2026-09-01:** the
> 152 fixture rows were removed (`--remove`; dry-run reported 152 of 152, all
> deleted, verified by GSI1 counts and a live endpoint check returning the
> Albany prices below), and `load_seed_data.py` is now **guarded** — it refuses
> to load over the real catalogue without `--force`, with a regression test, so
> this cannot recur silently. The worked examples below are true again and were
> re-verified live.

The load was additive, so the table briefly held 152 fixture rows AND 2,759 real
ones, and answered inconsistently: head terms hit the fixtures while meal plans
drew on the real data. `cheapest milk near Albany` returned a *Devonport*
fixture price though Albany had real data.

The fixture rows are gone. `scripts/load_seed_data.py --remove` deletes exactly
the `(store_key, product_key)` pairs the fixture file names -- never a
scan-and-filter, so every other row is untouched by construction rather than by
a predicate someone has to get right. `--dry-run` reports what is present first,
because "deleted 0 rows" and "the table was already clean" are different facts.
Reverse with the loader itself; that symmetry is the point, since an operation
you can undo is one you can afford to try.

Live afterwards, every head term falling through to its Lineage B answer exactly
as `config/product-synonyms.json`'s candidate ordering was built to do:

| Request | Answer |
|---|---|
| `cheapest butter` | Pak'nSAVE Albany, $9.49, Mainland Salted Butter |
| `cheapest milk near Albany` | Pak'nSAVE **Albany**, $4.79, Pams Value Standard Milk |
| `feed 3 people for 5 days on $80` | 5 meals, $33.34 payable |

**Consequence to carry:** `tests/test_price_repository_contract.py` run against
the live table with `PRICE_REPO_DYNAMO_TABLE` expects fixture products. Those 31
tests are skipped by default and their expectations now belong to the real
catalogue.

## 3k. A dietary term the extractor produced and the table did not know — FIXED

Removing the fixtures surfaced this; it was never about the catalogue.
`vegetarian dinner for 2 for 3 days on $50` was refused live with
`UNSUPPORTED_EXCLUSION`, and the refusal listed "no meat" among the terms it
supports **while refusing "meat"**.

The extractor had returned the exclusion as the bare noun `meat`.
`SUPPORTED_EXCLUSIONS` held `no meat` and not `meat`. The same request phrased as
`vegetarian meal plan ...` produced a plan on the next call, so this was
INTERMITTENT -- the worst shape for a safety control, because it passes review
and fails a user.

Fixed by adding `meat`, `dairy` and `eggs`, each mapping *exactly* as its
negated form already does. That equality is asserted rather than written out: a
bare noun excluding something different from its negation would be a second
policy decision smuggled in as a synonym. A second test asserts every term
`supported_terms()` advertises actually maps, which is the shape the defect took.

The system behaved correctly throughout -- it failed closed and said so. The
table was simply missing a spelling.

### It also exposed a repair eval case that tested nothing

`rb-003` is a budget-repair case whose exclusion was `dairy` -- the very term
that was unmapped. It had been *failing via the unsupported path*, not by
overspending, so it was scored as a budget case while testing nothing about
budgets. With `dairy` mapped it reached the planner and passed at both one pack
per product and three: at $90 for 3 people over 5 days it could not be made to
overspend at all.

Budget lowered to $50, where a normal plan fits and a 3x-pack plan does not,
which is the discrimination a budget case owes. Recorded in the case `note` per
the eval-discipline rule -- and worth being precise that this is not lowering a
bar a model failed to clear: the case never exercised its own kind.

## 3l. Operational gates — Pilot Task 12, 2026-08-30

### Alarm coverage: from two to eight

The two shipped alarms did not cover the most consequential failure the service
has. A production stage silently configured as a demo raises
`ConfigurationError`, which maps to a contract-valid `INTERNAL_ERROR` at HTTP
200 -- firing neither `handler-escaped` (it logs `unhandled_exception`, a
different message) nor `api-5xx` (it is a 200). Req 12.8 asked for the rest and
they were outstanding.

Six added, each bound to a metric **confirmed present in CloudWatch with the
dimensions named** -- an alarm on a metric that never reports looks exactly like
a healthy service:

| Alarm | Watches | Fires at |
|---|---|---|
| internal-error | `TurnError` [code=INTERNAL_ERROR] | 3 in 5 min |
| idempotency-unavailable | `IdempotencyUnavailable` | 5 in 5 min |
| turn-latency | `TurnLatency` p95 | > 20s over 2 periods |
| repair-exhausted | `RepairExhausted` | 5 in 15 min |
| guardrail-interventions | `GuardrailIntervened` | 10 in 15 min |
| silent-turns | `TurnWithoutContent` [intent=meal_plan] | 10 in 15 min |

**`internal-error` is dimensioned on the code, and that is the whole design.**
`BUDGET_INFEASIBLE` and `NO_DATA` share the `TurnError` metric and are the
product working correctly. An alarm without the dimension would page somebody
every time a shopper asked for a plan that genuinely does not fit their budget,
and an alarm people mute is worse than no alarm.

**`guardrail-interventions` is not a safety alarm.** An intervention is the
control working; alarming on one would page on every success. It is a CHANGE
detector, and it exists because of §3f: the function applied Guardrail version 1
for days while every document described version 2, refusing benign queries with
nothing to show for it. A policy change that starts over-blocking looks exactly
like this.

Deliberately still absent: throttling and stale-data alarms. Neither has a
metric yet, and adding the alarm before the metric adds the appearance of
coverage rather than coverage.

### The validator had to learn two things, and kept its teeth

`apply_alarms.py` refused all six. Its rules encoded the assumptions of the
original two as universal law: every metric comes from a log metric filter
declared in this config, and every alarm is Sum/1-datapoint/fires-immediately.

Both are wrong in general and were right for what existed. Rather than loosen
them, the config now DECLARES what the application emits (`emf_metrics`) and
each alarm declares its `kind` (`count` or `statistic`), with per-kind rules. A
mistyped metric name still fails; a count alarm still cannot silently become
statistical. `tests/test_alarms.py` binds `emf_metrics` to the `METRIC_`
constants in `src/observability/base.py`, so renaming a metric in code without
updating an alarm fails the build.

### Alarm drill

Not trusted -- watched. `set-alarm-state` drove `internal-error` to ALARM; it
transitioned, carried the reason, and published to the topic with two confirmed
subscribers. Reset to OK afterwards.

### Cost baseline (Req 12.6, 12.7), and what it revealed

Budget `grocery-orchestrator-monthly-dev`: **$25/month**, notifying at 50%, 80%
and 100% actual plus 100% forecast. The SNS topic policy was extended to let
`budgets.amazonaws.com` publish -- without it the budget is a dashboard widget.

August spend, which is the first time anyone looked:

| | |
|---|---|
| Claude Sonnet 4.5 | $5.40 |
| Claude Haiku 4.5 | $5.21 |
| Amazon Bedrock (Nova) | $2.92 |
| Tax | $2.30 |
| AWS Lambda | $1.77 |
| CloudWatch / DynamoDB | $0.03 |
| **total** | **$17.63** |

**60% of the spend is two models the service does not route to.** `models.json`
routes to Nova, and Sonnet is *disabled* on latency grounds. That $10.61 is the
live evaluation sessions of 2026-08-28/29 -- experimentation, not serving.
Serving is Nova plus Lambda, about $4.70 for the month.

That distinction matters for the limit: $10 would have alarmed permanently on a
month containing normal eval work, and a permanently-alarming budget is one
nobody reads. $25 leaves room for evals while catching a runaway within days.

### Latency baseline — the first one measured against the deployed service

Every latency figure in this repository had been a laptop measurement.
`scripts/measure_latency.py` measures the endpoint over HTTPS, including the
gateway hop, paced at 9/min because the binding Nova Lite quota cannot be raised
and an unpaced run measures the quota rather than the service.

| | n | p50 | p95 | target |
|---|---|---|---|---|
| price check (warm) | 8 | 1.80s | **2.21s** | p95 < 5s ✅ |
| meal plan | 3-4 | 6.6s | **11.7-12.2s** | p95 < 20s ✅ |

**The first run reported price-check p95 at 5.97s and failed the target.** The
entire difference was the cold start: request one took 5.97s, every other took
1.6-2.0s, and at n=8 the p95 IS the cold start. Warm p95 is 2.21s. Both figures
are true and they answer different questions -- a shopper's first request of the
day pays it, and SnapStart's `Restore` subsegment (~0.6s, visible in X-Ray since
§9) is only part of it.

Meal plans sit at roughly half the 20s target with clear room under the ~25s p99
escalation trigger.

**Do not quote these as qualification.** n=8 and n=3 are a first baseline, and a
p99 over three samples is just the maximum. Re-run before the pilot with enough
turns to mean something, and once the recipe/plan path changes.

## 3m. CDK is deployed — Pilot Tasks 9 and 10, 2026-08-30

Two CloudFormation stacks now exist, and the environment is bootstrapped.

| Stack | What it does |
|---|---|
| `Grocery-Stateful-dev` | Adopts the seeded tables, Strategy A. Contains `CDKMetadata` and three outputs -- **no table resource at all** |
| `Grocery-Service-dev` | The whole service plane under a `-cdk` name suffix: Lambda, `live` alias with SnapStart, REST API `crm1xkrk34`, scoped IAM, SSM parameters, 14-day log retention, throttling, usage plan |

**The adoption evidence is an absence.** `Grocery-Stateful-dev`'s template
contains no `AWS::DynamoDB::Table`, so CloudFormation cannot create, replace or
delete the tables holding 2,759 real price records. Before and after the deploy:
products 2,759 → 2,759, idempotency 74 → 74, `TableId` unchanged, and the live
endpoint still answered 200.

**The service plane deploys BESIDE the hand-made one, not over it.** Its names
carry `-cdk`, because deploying with identical names would not adopt anything --
CloudFormation would try to CREATE resources that already exist and fail. The
alternative, `cdk import`, needs every property of an eight-resource API Gateway
tree to match exactly, and a mismatch there is not a failed import but a
REPLACEMENT of a resource that is serving. It also proves less: an import
inherits whatever the hand-made resource has, including the parts nobody wrote
down, whereas a fresh deploy proves the definition is *sufficient*.

### Parity, checked before anything was cut over

| Request | hand-made | CDK |
|---|---|---|
| `cheapest butter` | paknsave Albany $9.49 | *identical* |
| `cheapest milk near Albany` | paknsave Albany $4.79 | *identical* |
| `how much is truffle oil` | `no_data` | *identical* |
| `feed 3 people for 5 days on $80` | 5 meals, $37.32 | 5 meals, $31.74 |

The meal-plan difference looked like a discrepancy and is not one. The same
question against the SAME endpoint three times returned $35.75, $31.74, $31.74:
plan composition varies run to run, and the CDK figure sits inside the hand-made
one's range. **A difference between two systems is only evidence if the same
system does not produce it on its own** -- checking that is the difference
between a finding and a false alarm, and this file has enough of the latter in
its history.

### Three things CDK fixes that the hand-made plane has wrong

- **Log retention.** `/aws/lambda/grocery-orchestrator-dev` is `null` -- never
  expire. The CDK group is 14 days. `infra/docs/04-SECURITY.md` requires finite
  retention, and a log that never expires turns any future logging mistake into
  a permanent one.
- **The API Gateway account CloudWatch role**, which §7 records as unset. CDK
  sets it. Note this is ACCOUNT-LEVEL, so the hand-made API gains it too --
  a CDK deploy changing state outside its own stack is worth knowing about.
- **Stage tracing on from the start**, rather than patched in by hand.

### The cutover is DEFERRED, deliberately -- 2026-08-31

**Two service planes keep running until a frontend exists.** The cutover's only
real cost is the URL change and its only real question is who that breaks, and
nobody knows yet: the frontend is teammates' scope, `CONTRACT-v1.md` is what
they build against, and its open questions do not auto-adopt defaults until
2026-09-11. Moving a URL to spare a consumer nobody has written yet is work that
would have to be re-done against the consumer they actually write.

Both planes are scale-to-zero, so the duplicate costs essentially nothing. The
hand-made one is the one alarmed and the one the frontend contract names, so it,
not the CDK one, is still production.

**The cost of waiting, stated so it does not get forgotten:** production is the
plane with `null` log retention on `/aws/lambda/grocery-orchestrator-dev` and
tracing added by hand rather than on from the start. The CDK plane fixes both.
Neither is urgent; both are reasons not to let "stay dual" become permanent by
default.

**Revisit when the frontend is built** -- not on a date. Ask which URL it wired
to, RE-RUN the parity table rather than reading the 2026-08-30 one (parity is a
measurement, not a property, and the service has gained a recipe catalogue
since), then choose. `infra/docs/08-OPEN-DECISIONS.md` §10 carries the full
reasoning and the corrected sequence.

**THE API KEY LANDS IN THE SAME CHANGE. Decided by the owner, 2026-08-31.**
Both `POST /chat` endpoints are public and unauthenticated, and the account
holds no API keys at all (`aws apigateway get-api-keys` returns nothing; both
methods report `apiKeyRequired: false`, `authorizationType: NONE`). The usage
plans exist and throttle, but a plan with no key throttles everyone as one
anonymous pool and cannot tell a shopper from a script.

Requiring a key is minutes of CDK. What it costs is a required `x-api-key`
header in `CONTRACT-v1.md`, API Gateway's own 403 body instead of the
contract-valid `ChatResponse` this service guarantees on every other path, and
a working client that has been CALLING this endpoint since 2026-08-21.
So the decision was to take it WITH the cutover rather than before it: the URL
change and the header change are one coordinated break instead of two.

The exposure while waiting was costed rather than asserted. Bounded by the Nova
Lite quota -- which cannot be raised and is therefore acting as an accidental
cost ceiling -- an abuser spamming meal plans 24/7 reaches roughly **$2,030 a
month**, price checks roughly **$140**. The $25 budget alarms, but AWS Budgets
refresh about three times a day, so expect to hear about it $25-70 in. **The
money is the smaller problem**: an abuser consuming the 20/min quota makes the
service unusable for real shoppers while they do it, and no budget bounds that.

Acceptable only because nobody outside the team has either URL. Move
immediately on any of: a demo outside the team, either URL published anywhere,
or the budget alarm firing for a reason nobody on the team caused.

**It is a test, not a note.** `infra/test/app.test.ts` fails the moment
`FrontendStack` creates its first resource, with the review document and the
two options in the failure message. A note saying "revisit when the frontend
lands" is the same shape as "SKIPPED until ServiceStack is implemented", and
this repository has spent two audits finding those.

**A frontend exists, and this section did not know -- 2026-08-31.** The branch
`frontend-infra-setup` has carried a working Vite/React client since
2026-08-21: four commits by a teammate, never mentioned, 120 commits behind
`main`. **Merged into `main` on 2026-08-31** by owner decision, with the
contract reconciliation still open — see `docs/OPEN-REVIEW-frontend-contract.md`
§0, which records what merging it cost. Its `VITE_API_URL` defaults to `http://localhost:8000/chat`
and it has **no deployed URL wired into it at all**, so the condition this
deferral was waiting on is half met -- there is a consumer to coordinate with,
and it has not yet chosen a URL to be coordinated.

That is the good case, and it argues for asking now rather than waiting: a
consumer that has not committed to a URL is the cheapest possible moment to
pick one, and the CDK plane is the one with finite log retention and tracing on
from the start. **The blocker is no longer "there is no frontend"; it is that
nobody has asked the frontend teammate which URL they want.**
`docs/OPEN-REVIEW-frontend-contract.md` §3 question 5 puts that question in
front of them, alongside the contract divergences that matter more.

**The sequence this section used to give was wrong.** It said `NAME_SUFFIX=''`,
deploy, repoint, retire. Step two fails: with an empty suffix the CDK function
is named `grocery-orchestrator-dev`, which is the hand-made function's name, and
CREATE collides -- the `-cdk` suffix exists precisely because of that. Consumers
have to be repointed at the `-cdk` endpoint and the hand-made resources deleted
BEFORE the unsuffixed deploy, which means two URL changes and a gap where the
old name serves nothing. There is no zero-downtime path, and the old wording
hid that.

## 3n. The reviewer's boundary, built without the reviewer — 2026-08-31

`src/review/` is the deterministic half of Pilot Task 14: the sanitised
snapshot a data-quality reviewer would sit behind, and the validation its
findings must survive. **Nothing is deployed and no model reviews anything.**
ADR 0002 is still *Proposed — mentor approval required*, and that gate is about
deploying an AgentCore Runtime, not about writing the constraints one would run
inside.

Building this half early is not working around the gate. The reviewer is the
untrusted component whether it is a model or a person with a spreadsheet, so
the boundary and the check are needed either way — and if the ADR is declined,
this is what a human reviewer uses.

### The snapshot is an allowlist, not a redaction

Req 13.8 forbids shopper messages, locations, dietary data, sessions and
credentials reaching the reviewer. The tempting implementation is to strip
those fields from a `PriceRecord`. The honest one is to construct the snapshot
from `SNAPSHOT_FIELDS` — 13 named fields on a `SnapshotRow` type that is
deliberately *not* `PriceRecord`.

The difference shows up later. A field added to retrieval joins a redacted
object silently and joins an allowlisted one never. `snapshot_to_dicts`
iterates the allowlist rather than calling `dataclasses.asdict`, for the same
reason: `asdict` serialises whatever the dataclass happens to carry, which puts
the decision in the wrong place.

`lat`/`lon` are excluded even though they are store coordinates from
`config/store-locations.json` and not a shopper's position. A reviewer checking
a price does not need geography, and a field that is not there cannot leak.

### It raises rather than truncating

`build_snapshot` refuses more rows than the cap instead of taking the first
500. Truncating would make the reviewer's view depend on the caller's ordering,
so a finding about "the catalogue" would really be a finding about whichever
rows arrived first — and nobody reading the finding would know. The caller
chooses the slice, and then the record says what was reviewed.

### The validation is `assert_citations_match_retrieval` in different clothes

That check exists because a citation naming the right table, with a plausible
key and a price nobody retrieved, passed cleanly. **Shape is not identity.** A
finding carries exactly the same risk: "row X has a bad unit price" is worth
nothing unless row X was in the snapshot and its unit price really is what the
finding says.

So every finding is checked three ways — the reference exists in the snapshot,
the values it quotes match that row exactly, and it reports rather than
prescribes. A finding failing any of them is not low-confidence; it is a
fabrication, dropped with the reason recorded. `fabrication_rate` is the number
that shows a reviewer has stopped referring to real rows, before a human
notices the findings have become useless.

`Finding` has no field for a proposed value (Req 13.8: candidate prices are not
publication authority). Because a reviewer denied the field would write it in
prose instead, `_PRESCRIPTIVE` also refuses "should be $2.49" in the
observation — the same authority arriving through the back door.

### The one rule we already know stays as code

`implausible_unit_price` catches the defect that actually reached the live
table: `unit_price_nzd` of "2490.00" against a $2.49 sold-each broccoli, six
rows, shipped with no signal. A model might notice it; a comparison cannot fail
to. The reviewer's value is the anomalies nobody thought to write a rule for,
and handing it the ones we did think of would be paying a language model to do
arithmetic.

Swept across all 152 catalogue rows: 0 false positives, and the 6 sold-each
rows exercise the `pack_grams == 1` branch that produced the defect. The
tolerance is an order of magnitude, not a cent — a check that fires on rounding
differences is a check that gets switched off.

**Still open, and needs ADR 0002:** the Runtime, the isolated least-privilege
identity, the call/token/time/cost/egress caps, teardown evidence, and the
labelled anomaly evaluation. 20 tests cover what exists.


## 3o. The infrastructure suite was run for the first time, and found two live IAM regressions — 2026-08-31

`infra/test/service-stack.test.ts` was `describe.skip(…)` under a header saying
"SKIPPED until ServiceStack is implemented (it is a stub today)". The stack had
been 230 lines with zero TODOs and **deployed as `Grocery-Service-dev`** for a
day. No CI job touched `infra/` at all — no `tsc`, no `jest`, no `cdk synth` —
so the file that DEFINES this project's security posture was the only code in
the repository with no gate under it.

Removing `.skip` was three hours of work and it was not the interesting part.

### What the run found

**1. `dynamodb:Scan` was back on the products table, in the deployed plane.**

Pilot Task 6b removed that permission on 2026-08-30 when `candidates_for_budget`
moved to GSI2, and `config/iam-orchestrator-role.json` carries a paragraph
saying why, ending: *"a Scan permission nothing needs is a Scan somebody can
reintroduce without noticing."* Two lines in `service-stack.ts` reintroduced it
the next day:

```ts
tables.products.grantReadData(role);        // + Scan, + index/*, + Streams
tables.idempotency.grantReadWriteData(role);  // + DeleteItem, + BatchWriteItem
```

The grant helpers do not CHECK the JSON policy the stack loads three
constructs earlier — they ADD a second statement beside it, using the CDK's
idea of "read" and "write" rather than this project's. `grantReadData` also
widened the explicit `index/GSI1` and `index/GSI2` ARNs to `index/*` and granted
Streams reads on a table with no stream. `grantReadWriteData` granted
`DeleteItem`, against a config comment reading *"No Delete -- expiry is by TTL,
which requires no permission."*

Fixed by deleting both calls. The role already carries exactly what the JSON
declares; anything a grant helper adds is by definition something nobody wrote
down.

**2. One assertion had inverted, and passed BECAUSE of finding 1.**

`it('orchestrator role CAN Scan products')` asserted the permission was present.
It is the assertion the second audit predicted would "either fail, or pass and
thereby prove the Scan came back". It passed.

**3. Two assertions were theatre, and un-skipping them would have shipped a
green check that verifies nothing.**

- `it('the only Resource:"*" is X-Ray')` had an **empty body** — a comment and
  no expectation.
- The write test matched `/dynamodb:PutItem[\s\S]*grocery-products/` over
  `JSON.stringify(policies)`. That pattern spans unrelated statements, so it
  FAILED on a policy with no write on products at all: `PutItem` appears in the
  idempotency statement and `grocery-products` appears later in the blob. A
  false negative and a false positive in one suite.

The rewritten assertions parse the policy document and compare action sets per
resource. A security check that cannot say which statement it matched is not a
security check.

### What else the same pass fixed

| | |
|---|---|
| **SSM published invalid JSON** | `readFileSync(models.json).slice(0, 4096)` of a 10,930-byte file. `json.loads` on the result fails at line 132. Nothing broke because nothing reads it, which is the worst reason for a defect to survive. `publishJson` now THROWS at synth, and what is published is the routing block — scorecards are measured evidence, and an operator who can edit them can qualify a route by typing. |
| **`APP_STAGE` was never set** | So Req 12.5's runtime check returned immediately and stayed inert under the CDK plane too. Now set from `cfg.stage`. |
| **Two definitions of "production"** | `src/handler.py` had `{prod, production, pilot}`; `infra/lib/config.ts` had `stage === 'prod'`. A `pilot` stage passed synth with wildcard CORS and then failed at startup — the earlier, cheaper guard was the one that did not fire. Both now read `config/stages.json`. |
| **The prod path adopted nothing** | Adopted table names were derived from the stage, so `stage=prod` referenced `grocery-products-prod`, which does not exist. Adoption points at something already there, so the name is an input (`DATA_SUFFIX`) on its own axis. |
| **The region guard fired in the wrong places** | `bin/grocery.ts` threw when `CDK_DEFAULT_REGION !== ap-southeast-2`. That variable is set by the CDK CLI from the resolved AWS profile, so the guard refused `cdk synth` — which touches no account — for anyone whose default region differed, and in CI, where there are no credentials, it never ran at all. The pin on every stack's `env` is the real control; `infra/test/app.test.ts` now asserts it, so CI checks what the guard only claimed. |

### The gate

CI job `infra`: `npm ci`, build the Lambda asset synth points at, `tsc
--noEmit`, `npm test`, `cdk synth`. Wired into `summary.needs`, so
`tests/test_ci_workflow.py` covers it like every other job. 47 assertions
across five suites (`app`, `config`, `service-stack`, `observability-stack`,
`reviewer-stack`; 24 when this was written, before the observability and
reviewer suites landed), and each was watched to fail against a mutated stack
before being kept.

**And a control against the recurrence.** `tests/test_skip_markers.py` fails when
a skip carries no machine-checkable condition, in Python and TypeScript alike —
`@pytest.mark.skip`, condition-less `xfail`, `describe.skip`, `it.only`. The
distinction it enforces is the only one that matters: `skipif(not
DATASET.exists())` stops skipping the moment the dataset appears, and "SKIPPED
until X is implemented" never stops, because nothing evaluates the English.

## 3p. The anomaly rule was switched on, and measured — 2026-08-31

`implausible_unit_price()` was written on 2026-08-31 with the $2,490 broccoli in
its docstring, tested, and **called by nothing**. `ingestion/handler.py` diffed
before writing and did not validate, so the one defect class known to have
reached the live products table was still undetected in production — while an
AgentCore Runtime was being proposed, in ADR 0002, to find the anomalies nobody
had thought of. The rules that HAD been thought of were not running.

They are now. `ingestion.handler.reject_implausible` refuses the row, the count
and a sample land in the Step Functions execution, and
`config/alarms.json` derives `IngestionRowRejected` from a structured log line.

### The run, over the whole catalogue

```
$ python scripts/check_ingestion_anomalies.py
catalogue: datasets (datasets/data/dynamodb_products) -- 3000 source rows,
           2759 after transform (61 non-food dropped, 180 duplicates collapsed)
rule:      implausible_unit_price, factor 10x

  rows checked  2759
  accepted      2759
  REJECTED      0
```

**Zero findings, and zero is not the interesting number.** A clean result from a
rule nobody has watched fail is indistinguishable from a rule that cannot fire —
which is the defect this whole fortnight has been about. So the historical
defect was reintroduced and the run repeated: remove the `pack_grams <= 1`
sold-each guard from `ingestion/normalise.py::unit_price`, exactly as the first
version of that function omitted it, and

```
  rows checked  2759
  accepted      2237
  REJECTED      522

  new_world#albany/broccoli-ea   Broccoli
      price 1.79  stored unit 1790.00  derived 1.79  pack_grams 1
```

**522 of 2,759, not six.** The original incident hit six rows, and that number
has been quoted in this repository ever since as the size of the class. It is
not: six was the number of sold-each products in the *seeded fixture set* at the
time. Against the real catalogue the same one-line omission corrupts **19% of
every shopper-facing unit price**, and it does so on a first write, where the
diff — the only control that existed — reports nothing, because a defect on a
first write is not a change.

That also settles the threshold question. 0.2% and 19% cannot both be caught by
one percentage gate, so there is no percentage gate: the alarm fires at one row.

### What the deterministic rules can and cannot see

Recorded because ADR 0002 gate 4 asks for acceptance data, and because the
argument FOR a reviewer — "its value is the anomalies nobody thought to write a
rule for" — only becomes evidence once the rules that were thought of are
running and observably missing things. Half of that is now true.

**Caught:** a unit price that disagrees with its own pack size by an order of
magnitude, in either direction, including every misuse of the sold-each
sentinel.

**Structurally invisible to this rule**, and the honest list:

- a price that is simply wrong but internally consistent — $12.99 for a $1.29
  item, with a matching unit price, passes every check here;
- a `pack_grams` that is wrong at SOURCE, since the unit price is then correctly
  derived from a wrong weight;
- a mis-categorised product — the vegan-safety class — which
  `ingestion/lineage_b.py` handles separately and fail-closed;
- a stale capture date, which `src/retrieval/filters.py` owns;
- **anything needing a baseline.** "This price doubled overnight" is the largest
  category here and it is not a rule problem: it needs the append-only
  price-history table, which did not exist when this was written. That is a
  cheaper and better-defined piece of work than a reviewer, and it is a
  prerequisite for one.

So the ADR 0002 decision now has a measurement under it rather than a belief,
and it points somewhere specific: the next thing worth building is the history
table, not the Runtime.

> **Update (2026-09-02): the history table recommendation was acted on.** The
> append-only price-history module was subsequently built —
> `src/history/` (`to_history_item`, `summarise`, `PriceBaseline`,
> `DynamoPriceHistory`), wired into `ingestion/handler.refresh()`, documented as
> Table 4 in `DYNAMODB-SCHEMA.md`, and used to enrich the reviewer's snapshot
> with a `deviation_ratio`. **The table `grocery-price-history-dev` is defined in
> code but is NOT deployed** — `aws dynamodb list-tables` (2026-09-02) shows only
> `grocery-products-dev` and `grocery-idempotency-dev` (plus the data team's
> `smart-grocery-*`). So "this price doubled overnight" is now *catchable in code*
> and was the enrichment the reviewer prototype (§ below / `docs/AGENTCORE-RUNTIME-REVIEWER.md`)
> actually ran against, but it is not yet *live*, because the ingestion write
> path that would populate the table has not been deployed. The recommendation
> ("history before Runtime") held: the history module landed first and the
> reviewer used it.

## 3q. ObservabilityStack, and how much of the second plane was actually unwatched — 2026-08-31

The second audit's Finding 3 says the CDK plane is "unalarmed, undashboarded,
and equally invocable by anyone who finds the URL". Two of those three are
right. The middle one is more precise than that, and the precise version is the
one worth acting on.

**Six of the nine alarms already covered both planes.** They watch EMF metrics
dimensioned on `service`, and `POWERTOOLS_SERVICE_NAME` is `grocery-orchestrator`
on both — `service-stack.ts` does not suffix it. A handler error, a latency
breach, an exhausted repair loop or a guardrail spike on either plane fires the
same alarm and always did.

**Two were bound to a physical name, and those were the gap:** the API 5xx alarm
(`ApiName = grocery-orchestrator-api-dev`) and the handler-escaped metric filter
(`/aws/lambda/grocery-orchestrator-dev`). `ObservabilityStack` creates both per
plane, derived from `cfg.suffix`, and collapses to one set when the suffix is
empty — so the deploy that retires the hand-made plane needs no edit here, which
is the property that stops the list going stale.

**The shared dimension is itself worth recording, and it is half a win.** Six
alarms covering both planes also means a metric cannot say WHICH plane produced
it: while dual-running, a latency spike on the unused CDK plane is
indistinguishable from one on the plane serving shoppers. Splitting the
dimension would fix that and split every historical series with it, so it is
deliberately not done — the dual-run is temporary and the cutover is the fix.
If dual-running becomes permanent, this is a reason it should not.

### Verified against the account, 2026-08-31, AFTER the analysis above

The paragraph above was reasoned from `config/alarms.json` and the CDK source.
Checked against the live account afterwards, because this file's own rule is
that a deployment claim is about an account rather than about a document:

```
describe-alarms          8 alarms. ONE carries an ApiName dimension
                         (grocery-orchestrator-api-5xx-dev -> grocery-orchestrator-api-dev).
                         The other seven carry none -- they are the EMF
                         alarms on `service`, which both planes share.
describe-metric-filters  ONE filter, on /aws/lambda/grocery-orchestrator-dev.
list-stacks              Grocery-Stateful-dev, Grocery-Service-dev. NO Grocery-Obs-dev.
describe-log-groups      /aws/lambda/grocery-orchestrator-dev      retention None
                         /aws/lambda/grocery-orchestrator-dev-cdk  retention 14
```

Three things follow, and only the first was already written down:

1. **The analysis was right.** Six of eight alarms cover both planes; the two
   bound to a physical name cover the hand-made plane only.
2. **`ObservabilityStack` IS NOT DEPLOYED.** It is written, tested and merged,
   and the account has never seen it. Until `cdk deploy Grocery-Obs-dev` runs,
   the CDK plane's gateway has no 5xx alarm and its log group has no
   handler-escaped filter. **Do not point a consumer at the CDK plane before
   deploying it.**
3. **The hand-made log group still never expires.** `retentionInDays: None`
   against the CDK plane's 14. That is the cost-of-waiting §3m names, still
   being paid, and it is one of the two reasons the CDK plane is the better
   cutover target.

### PAUSED, waiting on the frontend teammate — decided 2026-08-31

**`Grocery-Obs-dev` is written, tested, merged and DELIBERATELY NOT DEPLOYED.**
The owner's decision: the teammate who owns the frontend is working on
something related, and the sensible order is to let that work land on GitHub
first, then re-evaluate this whole area once rather than twice.

That is the right call and worth stating why, so nobody "helpfully" deploys it:
this stack, the URL choice, the plane retirement and the API key are **one
decision wearing four hats**. Deploying the alarms now would commit to alarm
names and a second budget before knowing which plane survives, and every one of
those is cheaper to decide after the frontend exists than before.

**What is true while paused**, so nobody mistakes intent for an account:

- The CDK plane's gateway has **no 5xx alarm** and its log group has **no
  handler-escaped filter**. Six of eight alarms cover it via the shared
  `service` dimension; the two bound to a physical name do not.
- The hand-made plane is fully covered and is still the one serving.
- **The cost tripwire is real and is not this stack's.**
  `grocery-orchestrator-monthly-dev` at $25 exists, created by hand (§3l),
  confirmed live 2026-08-31. `ObservabilityStack` declares its own; deploying it
  would create a SECOND budget. Two are free, so that is untidy rather than
  costly, but it is a duplicate somebody should collapse at cutover.

### The checklist for when this comes back

In order, because two of these are prerequisites rather than preferences:

1. **Read the teammate's work.** Which host, which URL, and whether they call
   from a browser — that decides whether `CORS_ORIGIN` stops being `*`, which is
   the second trigger on the API-key tripwire.
2. **`cdk deploy Grocery-Obs-dev`.** Before any consumer is pointed at the CDK
   plane, not after. Collapse the duplicate budget while doing it.
3. **Re-run the parity table.** Parity is a measurement, not a property, and
   the service has gained recipe planning since the 2026-08-30 run.
4. **Choose the URL**, and record which and why. `-cdk` never appears in the
   URL, so choosing the CDK plane costs nothing cosmetically and needs no later
   rename.
5. **Take the API key in the same change** (option A, decided —
   `docs/OPEN-REVIEW-api-key.md`). `infra/test/app.test.ts` fails at this point
   by either route, so it cannot be missed.
6. **Retire the other plane**, and record the teardown — including the
   account-level API Gateway CloudWatch role §3m notes, which a destroy does not
   obviously restore.

### What else the stack carries

| | |
|---|---|
| SNS topic | From `config/alarms.json`, which refuses an alarm with no action. **No subscription is declared**: an SNS email subscription needs out-of-band confirmation, so a declared one sits `PendingConfirmation` and reads, in a console and in a template, exactly like somebody who would be paged. |
| Dashboard | Turns and errors, p95 latencies, tokens (the Bedrock bill before it is a bill), repair/guardrail/idempotency. |
| Budget | $25/month, notifying the alarm topic at 80% and 100%. Two budgets are free, and this is the control that does not depend on our own instrumentation working — the same reason the 5xx alarm watches the gateway's metric rather than one we publish. |
| Artefact bucket | Encrypted, versioned, public access blocked, SSL enforced, **RETAIN**. Eval results and latency baselines live in Markdown today, which makes a measurement's provenance a commit message. The point of keeping baselines is that they outlive the stack that made them. |

12 assertions in `infra/test/observability-stack.test.ts`, each watched to fail
against a mutated stack — dropping the per-plane 5xx alarm fails one, removing
the 0-fill from a metric filter fails another.

### The identity gap is still open, and is now designed rather than merely noted

Alarming both planes makes abuse VISIBLE. It does not BOUND it. An API key plus
a usage-plan quota is what turns an unbounded Bedrock bill into a number chosen
in advance, and it is minutes of CDK — but it adds a required `x-api-key`
header to `CONTRACT-v1.md`, returns API Gateway's own 403 body rather than the
contract-valid `ChatResponse` this service guarantees everywhere else, and
breaks a teammate's working client that has been calling this endpoint since
2026-08-21. Nobody has agreed who holds the key.

So it is written down and not applied: `docs/OPEN-REVIEW-api-key.md` carries the
design, the three options with what each costs, and the four things that would
change the answer. **Recorded as a holding position rather than a resolution** —
the gap is real, the deferred cutover doubled it, and monitoring is not a bound.

## 3r. `select_recipes` scored live, and what the run cost the ceiling — 2026-08-31

Two things came out of one 10-minute session against the live account, and the
second was not what the session was for.

### The scorecards

`evals/run_recipe_select.py`, 12 cases, guardrail version 2, paced at 9/min,
three reps per model, zero upstream failures and zero fallbacks in any rep.

| model | rate | reps | distinct mains |
|---|---|---|---|
| Amazon Nova Lite | **100%** | 3/3 identical | 3.4 |
| Claude Haiku 4.5 | **100%** | 3/3 identical | 3.8 |

Total spend: under two cents.

**BOTH AT 100% MEANS THE SUITE CANNOT RANK THEM**, and that is the same ceiling
the meal-plan suite hit. Every check here is a rule-violation check — did you
invent an id, repeat one while alternatives remained, breach a stated exclusion,
choose enough meals. Neither model breaks rules. Nothing asks whether the MENU
is good, so 100% means "both select validly" and says nothing about which
selects better.

The one measured difference is `distinct mains`: Haiku 3.8, Nova Lite 3.4,
stable across all three reps. Haiku picks more varied menus. It is reported and
NOT scored, because no threshold on variety is right for every request — three
meals from a seven-recipe shortlist cannot beat four from a twelve-recipe one —
and scoring it would manufacture a gradient without establishing what it means.
Nova Lite is preferred on cost (~13x cheaper on a call every meal-plan turn
makes); Haiku is the qualified fallback.

### The gate caught a third model within minutes

With both scorecards recorded, `unscored_routes()` returned
`[('select_recipes', 'nova-pro')]`. Nova Pro declares the FAST tier as well as
quality, so `available(tier)` offered it as a cost-ordered fallback for a task
nothing had scored it on — **exactly** the defect the registry documents about
`claude-sonnet` sitting as a live fallback for every task while documented as
unfit. Excluded as a routing decision rather than scored: selection is a cheap
judgement over a shortlist code has already validated, and paying 13x for it is
a cost regression, not a quality win.

`unscored_routes()`, `unscored_tasks()` and `unevidenced_models()` are all empty
again.

### THE THROUGHPUT CEILING MOVED, AND NOTHING HAD NOTICED

`scripts/check_quotas.py` was run first, as the runbook requires. It printed:

```
  repair_plan        UNROUTABLE: No routing rule for task 'repair_plan'
```

Its task list was hand-written, so the repair split had left it naming a task
that no longer exists and omitting both replacements — and with them Claude
Haiku, which meant the tool whose whole job is naming the binding model had
stopped listing one of the models that binds. Fixed to enumerate from
`ModelRegistry.tasks`.

With the list correct, the real finding:

| | before 15c | after 15c |
|---|---|---|
| meal plan, no repair | 10.0/min | **6.7/min** |
| meal plan, 2 repairs | 5.0/min | **4.0/min** |
| price check | 10.0/min | 10.0/min |

**Pilot Task 15c cost a third of the meal-plan throughput.** `select_recipes`
adds a THIRD Nova Lite call to every meal-plan turn, and Nova Lite is the
binding, unraisable quota. The feature that made the plan better made the
ceiling lower, and the figure quoted in five documents (10/min, 5 with repairs)
was measured before the node existed.

The recipe path also drops the Nova Pro call entirely — `select_recipes` builds
the plan, so `generate_plan` never runs — which is a cost saving of roughly 13x
on that call and a throughput loss, because it moves work onto the model that
binds. Both paths are now modelled separately by the script rather than
averaged.

**Neither number was measured by anything before this run**, which is the point
worth keeping: a feature can move a documented ceiling by a third and no gate in
this repository would say so. `check_quotas.py` derives it from the live account
and is the only thing that knows — so run it after any change to the routing
table, and never quote a throughput figure from a document, including this one.

## 4. IAM notes worth keeping

**Cross-region inference profiles need two grants.** `config/models.json`
routes through `apac.*` and `au.*` profiles spanning multiple APAC regions.
Granting only the profile ARN produces an `AccessDeniedException` naming a
region nobody configured. The policy grants the profile ARN *and* the
underlying `arn:aws:bedrock:*::foundation-model/...` — account-less because
foundation models are AWS-owned, region-wildcarded because the profile chooses
the region.

**No `cloudwatch:PutMetricData`.** Powertools Metrics emits Embedded Metric
Format to stdout and CloudWatch extracts the metrics from the log records.
Granting PutMetricData would be permission for a call the code never makes.

**GSI1 is a separate resource ARN.** Omitting it yields a working `GetItem` and
a failing cheapest-price `Query` — the exact access pattern the GSI exists for.

**Ingestion cannot read the model or the idempotency table**, and the
orchestrator cannot write prices. Four roles, one per principal.

## 5. Two defects found by deploying, and fixed

Neither was visible offline. Both were found because the deployed system was
exercised against live Bedrock and a real table.

### The prose named a different store than the comparison

`_placeholder_list` deliberately carries no prices — that is the mechanism that
stops the model writing a dollar figure. But `PRICE_CHECK_SYSTEM` also told the
model to "say which store is cheapest", so it was being asked to state a fact it
had been denied the data for. It guessed. Against live Nova the sentence named
Pak'nSave Sylvia Park while `price_comparison` flagged Pak'nSave Mangere.

Both were $2.97, so the tie hid the general defect: **nothing tied the model's
choice to the retrieved prices at all**. On a non-tie it could have named a
dearer store as cheapest — a confident wrong answer, which is what invariant 2
exists to prevent.

Fixed by computing the winner in code and naming it in the prompt
(`cheapest_refs`), and by rejecting prose that cites anything else. The check
is against retrieved records, not against what the model claims — Req 5.4's
rule applied to the price claim. `test_prose_is_dropped_when_it_cites_a_dearer_option`
guards it, and was mutation-tested: with the check disabled that test fails,
which is the only evidence that a guard guards anything. The first test written
for this passed with the check disabled — see §8.

### `usage` was empty on every response

`state["usage"]` was read by `emit_done` and written by nobody. The Bedrock
client recorded per-call usage into `last_usage`; no node lifted it into graph
state, so every deployed response reported `model_ids: []` and null tokens.

Fixed with a `merge_usage` reducer on the state field — a turn makes several
model calls and the contract reports one block, so without a reducer the last
writer would win and a plan turn would report only the prose call. Tokens and
latency sum, model ids deduplicate, `guardrail_intervened` is sticky. Live
responses now carry `["apac.amazon.nova-lite-v1:0"]`, ~2,514 input and ~75
output tokens per price-check turn.

### A third thing worth recording: the guardrail caught the first fix

Moving the cheapest-ref rule into the *user* prompt made every price-check turn
return `GUARDRAIL_BLOCKED`. `src/models/guardrail.py` wraps the user block in
Bedrock input tags precisely so the PROMPT_ATTACK filter applies there — and
imperative sentences inside that region are indistinguishable from an injection
attempt. The rule moved to `PRICE_CHECK_SYSTEM`; the tagged block carries data
only. **Instructions in the system prompt, data in the tagged block.**

This is also the clearest evidence so far that the guardrail is doing real
work, though it is not the qualifying live result Task 3 still needs.

## 6. Verified end to end

`POST /dev/chat` returns HTTP 200 with the contract-valid sequence: `session`,
`intent` (`price_check`, 0.95), five `citation` events each carrying
`source.table/pk/sk`, a `token`, a `price_comparison`, and `done`. Prices
serialise as strings, so the `Decimal`-on-wire convention survived deployment.
Cold ~7.6 s before SnapStart optimisation; ~1.5–5 s after.

The state machine refreshed all three retailers in one execution — 51, 51 and
50 records, totalling the seeded 152 — and the shopper path was re-verified
against the rewritten table.

Gates after the changes: **324 passed, 31 skipped**, ruff clean, intent eval
**76.7% (23/30)** and meal-plan **91% (10/11)** — both unchanged from baseline,
guardrail structural PASS, `validate.py` exit 0. Sample fixtures were
regenerated twice, deliberately: once because `usage` became populated and once
because the system prompt grew by the added rule. Both are intentional
expectation changes, recorded here per the eval-discipline rule.

**Beware the idempotency cache when testing.** Re-posting
`samples/request_price_check.json` returns the stored outcome for that
session/turn pair, not a fresh run. Two fixes appeared inert for a while
because every verification was reading a cached pre-fix response — identical
prose, ~1.5 s latency, empty usage. Use a fresh `session_id` and `turn_id` per
manual test. The cache was working exactly as designed; the verification was
not.

## 6a. Throughput ceiling, measured

The account's Bedrock request-per-minute quotas cap this deployment at **10
meal-plan turns per minute, falling to 5 when the repair loop fires** — RE-MEASURED 2026-08-31 as 6.7 and 4.0 after Pilot Task 15c added a third Nova Lite call to every meal-plan turn; see §3r —
service-wide across all users, so roughly 300-600 an hour. The binding limit is
Amazon Nova Lite at 20 cross-region requests per minute, against the 2 Nova
Lite calls a clean meal-plan turn makes and the 4 a fully repaired one makes.

Do not quote those figures from here. `python scripts/check_quotas.py` derives
them from the live account and the current routing; this paragraph is a summary
that goes stale the moment either changes.

**Nova's request-per-minute quotas are NOT adjustable; Claude's are.** So the
reflex answer to a throughput problem — ask for an increase — is unavailable
for the models this deployment actually routes to. `scripts/check_quotas.py`
ends by saying whether the BINDING quota can be raised, which is the only form
of that question worth asking: a raisable limit on a model that is not the
constraint is not a way out.

Accepted deliberately: the target is a workshop and a demo, where 5-10/min is
ample, and a throttled call already fails honestly as a retryable
`UPSTREAM_TIMEOUT` rather than producing anything wrong.

Two options for lifting it, with costs and trade-offs, are recorded in
`docs/THROUGHPUT-AND-SCALING.md` for whoever takes this to production. Read
that before assuming a quota request is the fix.

One operational note worth carrying: throttling hits the TAIL of a busy
period, so errors cluster late rather than spreading evenly. In the eval
harness that pattern read as "the model failed those cases" and cost three
model bands before anyone checked the quota. A dashboard showing the same
shape is throttling, not model quality.

## 7. What is still not built, and why

**Live retailer acquisition stays gated** on the thirteen conditions in
`ACQUISITION-RISK.md` §8. Condition 1 — a human reading the three unretrieved
sources — is not met. `ingestion/sources.py` enforces this in code:
`resolve_source` raises `NotImplementedError` if `LIVE_ACQUISITION=1` rather
than falling back quietly, because a misconfiguration that silently starts
requesting retailer sites is the §4.2 exposure. Nothing in the repo sets that
variable. The tripwire exists so adding a live adapter requires deleting a line
that says why it is there.

**No S3 bucket.** Ingestion returns counts and writes to DynamoDB; nothing
produces a snapshot artefact yet. Creating the bucket now would be
infrastructure that reads as a capability and does nothing.

**Frontend hosting is teammates' scope.** `AGENTS.md` line 4 still holds. The
S3 + CloudFront box is an external consumer of `POST /chat`, and
`CONTRACT-v1.md` remains the interface they build against.

**`POST /dev/chat` is unauthenticated**, protected only by stage throttling at
5 rps / burst 10. Adequate for a dev stage with a public sample payload and
nothing more. Task 8.7 covers usage plans; WAF and Cognito are ADR 0002
companions.

**API Gateway execution logging is off.** It needs an account-level CloudWatch
Logs role ARN that is not set. Stage metrics and throttling work without it.

**Claude routes are open as of 2026-08-28.** They were blocked, and the block
was not visible where you would look: `au.anthropic.*` inference profiles
showed ACTIVE while invoking returned `ResourceNotFoundException: Model use
case details have not been submitted for this account`. Profile availability is
not account entitlement — worth remembering for the next provider.

The gate was the account-wide Anthropic use case form, submitted through the
Bedrock console's Playground (the Model access page that used to host it has
been retired). It is one-time and account-wide, not per-model: every Anthropic
model failed identically until it was submitted, and all of them answered
afterwards.

`models.json` still routes to Nova, which is unaffected either way.

**The pilot blockers in `AGENTS.md` are not discharged by any of this.**
Deployment proves wiring, not correctness.

*Updated 2026-08-30:* the two blockers this paragraph used to name — exact
retrieved-record equality, and a qualifying live Guardrail result — were both
closed on 2026-08-29 (Req 3.5–3.6, and 13/13 + 9/9 against version 2). What
replaces them is listed in §3a, §3b and §3c: the alias now serves current code
(§3a, resolved), two resources in the account have no identified owner (§3b),
and the freshness threshold was raised to keep the fixture-seeded endpoint
usable (§3c).

**API Gateway stage tracing was off, and is now on** — see §9.


## 8. What the review round changed

`/code-review` over the working tree returned eleven findings. Three were high
severity and one had already reached the account. All are fixed; the account
was reconciled before anything else.

**`unit_price()` corrupted live data.** It dropped
`scripts/generate_fixtures.py`'s `if grams > 1` guard, so `pack_grams: 1` --
the sentinel for "sold each", not "weighs one gram" -- was divided into rather
than passed through. The first scheduled-shape run wrote
`unit_price_nzd: "2490.00"` against a $2.49 broccoli, across six rows, into
`grocery-products-dev`. `unit_price_nzd` is read straight into the Citation the
shopper sees, so this was a wrong price on the wire, which is the one class of
error this project is built to make impossible.

Handled in that order: schedule disabled so 03:00 could not repeat it, table
restored from `fixtures/products.json`, `unit_price()` fixed (guard restored,
rounding changed from ROUND_HALF_UP to the generator's default ROUND_HALF_EVEN
so a refresh is genuinely idempotent), ingestion re-run, all 152 live rows
diffed field-by-field against the fixtures -- zero mismatches -- and only then
the schedule re-enabled.

The guard that now exists is `test_ingestion_reproduces_the_seeded_records_exactly`,
which compares every field of every record ingestion produces against the seed.
It did not exist before; the unit tests all passed while the output was wrong,
because none of them compared ingestion's output to the thing it reproduces.

**`usage_from` double-counted on failed calls.** `BedrockModelClient` assigns
`self._usage` only after `converse` returns, so a call raising `ModelError`
leaves the previous call's numbers in place -- and `merge_usage` added them
again. A meal plan whose generation throttled through two repairs billed
`classify_intent`'s tokens four times, over-reporting on exactly the turns that
failed. `usage_from` now takes the reading captured before the call and drops an
unchanged one, the same guard `InstrumentedModelClient._call` already applied to
its telemetry. A guardrail block is deliberately not that case: `converse`
returned and wrote fresh usage before the stop reason was inspected.

**The prose guard rejected output the prompt asked for.** `PRICE_CHECK_SYSTEM`
still offered "how it compares with the dearest option" while the new check
forbade citing any non-cheapest ref, so a sentence taking that branch was
silently dropped. The prompt now directs the comparison through `[[savings]]`,
which renders to a non-monetary label and cites no store.

**`dynamodb:Scan` was missing from the orchestrator role**, so every meal-plan
turn would have failed `AccessDenied` -- `candidates_for_budget` pages the base
table. It went unnoticed because the smoke test only ever exercised a price
check. Granted, and the meal-plan path verified end to end for the first time.

**The Step Functions `Catch` could not fire.** `ResultPath: "$.error"` against a
scalar Map item raises `States.ResultPathMatchFailure`, which aborts the Map --
the exact coupling the Catch exists to prevent. It never showed because no
branch had failed. Now `ResultPath: null`.

Also fixed: a vacuous test that passed with the code it claimed to guard
disabled (removed, replaced by the mutation-tested one above); `latency_ms: null`
published beside real token counts because the fixture carry-forward preserved a
null over a newly-populated field; two hand-authored samples still teaching
`model_ids: []` to the frontend, now carrying observed live values; the archive's
second entrypoint going unverified by `verify_import`; a duplicated config note
key; and `scripts/apply_iam.py`, which the config file had claimed as its applier
before it existed -- the policy had been hand-applied twice, which is how the
missing `Scan` survived review of a file that looked complete.

### The process fix: ingestion diffs before it writes

The code defect was one thing; the reason it became a *data* incident was that
the refresh was run straight at the live table with no dry-run and no diff.
Nothing compared what was about to be written against what was there, so six
rows changed value with no signal at all.

`refresh()` now queries the rows it is about to overwrite and reports
`added`/`changed`/`unchanged` plus a sample of which fields moved and from what
to what. `{"retailer": ..., "dry_run": true}` does the whole job and writes
nothing. The counts land in the Step Functions execution history, so the
scheduled run is now self-evidencing: three branches reporting `changed=0`
against unchanged fixtures is idempotency demonstrated rather than claimed.

It is deliberately **not** a threshold interlock. With live acquisition a
genuine special can move a real share of a retailer's catalogue, so a
percentage gate would either be too loose to catch a defect or would refuse
legitimate refreshes. Visibility after the fact is the honest control; a gate
that cries wolf gets disabled.

This cost the ingestion role one permission. It was write-only, and a
write-only writer cannot know what it is about to change, which is the shape of
the original problem stated as an IAM policy. It now has `dynamodb:Query` on
the base table — the smallest grant that makes the write reportable.

### Config carries placeholders, not an account id

This repository is public, and the config files this work added originally
hardcoded the account id into every ARN. The id is not a credential, and it was
already present in `DYNAMODB-SCHEMA.md` and `tasks.md`, so nothing was newly
exposed — but it is the wrong default twice over: it pins each file to one
account, contradicting the "reproducible in another account" line every config
header carries, and it hands a reader a concrete enumeration target for
nothing in return.

Config now carries `${AWS_ACCOUNT_ID}` and `${AWS_REGION}`.
`scripts/aws_placeholders.py` resolves them at apply time — the account from
STS, so it is by construction the account being deployed to and cannot drift
from the file the way a literal can; the region from the config's own `region`
field. `assert_resolved()` refuses to apply a half-substituted document,
because some AWS APIs accept `${AWS_ACCOUNT_ID}` as a literal ARN segment and
fail later at use rather than at apply.

`tests/test_config_placeholders.py` fails the build if a twelve-digit id
reappears in `config/`. That guard exists because this is exactly the kind of
rule that decays: the next person adding a resource pastes the ARN from the
console, and it reads as correct — because it is correct, for one account.

`scripts/apply_state_machine.py` was added at the same time, for the same
reason `apply_iam.py` was: the definition had been applied by hand, and the
`Catch`/`ResultPath` defect survived precisely because nothing re-derived the
deployed definition from the file.

**This is hygiene, not redaction.** The id is in this repository's git history
and history is not meaningfully rewritable on a public repo with forks. Treat
the existing value as public, because it is. What changes is that new work does
not add more, and CI now says so.

### The lesson worth keeping

Every one of the three high-severity findings was invisible to a green test
suite, and two were invisible to a successful live invocation. The suite passed
324 tests while ingestion wrote a wrong price to production data. What caught
them was diffing output against the thing it was supposed to reproduce, and
disabling a guard to watch its test fail. `AGENTS.md` already says this --
"assume the check is the thing that is broken until you have watched it fail" --
and this round is the seventh entry in that list.

## 9. X-Ray tracing enabled on the API stage — 2026-08-30

**Change:** `tracingEnabled` on stage `dev` of `woqmel35lk`, `false` -> `true`.
Requested by the service owner; applied and verified the same day.

```bash
aws apigateway update-stage --rest-api-id woqmel35lk --stage-name dev \
    --patch-operations op=replace,path=/tracingEnabled,value=true
```

Stage settings apply immediately -- no `create-deployment` is needed, and the
deployment id was unchanged (`4x65ir`) before and after. `infra/docs/03-STACK-SPECS.md`
already specified `tracingEnabled: true`, so this closes a drift between the
spec and the account rather than adding anything new.

### Why it mattered

The Lambda had X-Ray Active from the start, so traces existed -- but they began
at the *function*. The gateway hop, which is where a throttle, a 5xx raised
before our code runs, and integration latency all live, produced no segment.
A trace that starts after the component you are debugging is not evidence about
it.

### Verified, not assumed

Enabling and re-reading the flag only proves the flag. The check that matters is
whether a trace now has the gateway as its **entry point**, so a fresh request
was traced end to end. Trace `1-6a93bebe-0d7cbc9d063d7f8117304383`:

```
segment: grocery-orchestrator-api-dev/dev      origin=AWS::ApiGateway::Stage   <- NEW
     - Lambda                          6.0s
segment: grocery-orchestrator-dev              origin=AWS::Lambda::Function
     - Restore                         0.593s     <- SnapStart restore
     - ## _observed_handler            6.02s
segment: DynamoDB      x4              origin=AWS::DynamoDB::Table
segment: bedrock-runtime x2            origin=AWS::bedrock-runtime
```

`EntryPoint.Name` is `grocery-orchestrator-api-dev/dev` and the whole trace is
6.761s. Two things are now visible that were not:

- **The gateway hop itself.** The stage segment reports a 6.0s Lambda
  subsegment inside a 6.761s trace, so the difference is gateway-side and was
  previously unmeasurable. Small, but it is the part a p95 target is judged on
  and it had never been in a number.
- **The SnapStart `Restore` subsegment**, 0.593s, which is the cold-start
  optimisation actually doing its job. Useful when Pilot Task 12 sets the
  latency baseline: restore cost belongs in the cold-path figure and not in the
  warm one.

### One Windows trap worth recording

On Git Bash, the first attempt failed with
`Invalid method setting path: C:/Program Files/Git/tracingEnabled`. MSYS rewrites
a leading `/` in an argument into a Windows path, so `path=/tracingEnabled`
never reached the API. Prefix the command with `MSYS_NO_PATHCONV=1` (or use
PowerShell). The error names a real API constraint and reads like a bad
argument, which is what makes it cost time -- the argument was correct and the
shell edited it in transit. Same family as the `bash -c` finding in
`AGENTS.md`: the tooling changed the thing being tested.

### Cost

Negligible at this scale. X-Ray's free tier covers 100,000 traces recorded per
month; this deployment is capped by a Bedrock quota at roughly 300-600 turns an
hour and is not serving traffic. Revisit under Pilot Task 12's Budgets work if
that changes.

## 3s. Parity re-run, plane roles recorded, and source priority made first-class — 2026-09-01

Three things settled on 2026-09-01, none of which deploys anything or lifts the
§3q pause. Frontend work has started (the `frontend-infra-setup` client merged
2026-08-31) but has not reported which URL it will use, so the cutover, the
`Grocery-Obs-dev` deploy and the API key all stay deferred exactly as §3q and
`infra/docs/08-OPEN-DECISIONS.md` §10 describe.

### The parity table was re-run, and it passes

The 2026-08-30 parity table (§3m) predated Pilot Task 15c (`select_recipes` and
the curated recipe catalogue), so `infra/docs/08-OPEN-DECISIONS.md` §10 required
re-running it before it could inform a cutover. Done, against both live
endpoints, paced at 9/min, with a fresh session/turn per request so the
idempotency cache is not measured:

| Request | hand-made (`woqmel35lk`) | CDK (`crm1xkrk34`) | verdict |
|---|---|---|---|
| `cheapest butter` | paknsave Mangere $2.97 Pams Butter 500g, refs c1–c5 | *identical* | MATCH |
| `cheapest milk near Albany` | new_world Devonport $4.94, 1 citation | *identical* | MATCH |
| `how much is truffle oil` | no_data (honest refusal) | *identical* | MATCH |
| `feed 3 people for 5 days on $80` (3 reps each) | 5 meals; payable $43.33 / $43.33 / $38.59; 24 citations | 5 meals; payable $31.74 / $40.76 / $32.86; 24 citations | parity |

**The meal-plan totals differ between the columns and that is not a divergence.**
Both planes return five meals and 24 citations every rep; the payable total
varies run to run on EACH plane (hand-made spans $38.59–$43.33 across its own
three reps), and the two ranges sit alongside each other. This is exactly the
run-to-run composition variance §3m documents — a cross-plane number is only
evidence of a real difference if the same plane does not produce it on its own,
and here it plainly does. Exit code 0 (parity).

The harness is `scripts/check_parity.py`, kept because parity is a measurement
that must be re-taken, not a property that stays true — it compares
deterministic requests byte-for-byte on the fields that matter (intent, error
code, cheapest store, price, citation refs) and compares meal-plan requests as
RANGES over repeated runs. Full output in
`reports/parity_rerun_2026-09-01.txt`. No AWS credentials are needed; both
endpoints are public today.

**Two served answers have DRIFTED from the 2026-08-30 record, and both planes
agree on the new answers** — so it is not a parity failure, but it is a real
change in what the service returns, tracked separately in
[`docs/OPEN-REVIEW-near-filter-drift.md`](OPEN-REVIEW-near-filter-drift.md):

| Request | 2026-08-30 record | 2026-09-01 |
|---|---|---|
| `cheapest butter` | paknsave **Albany** $9.49 Mainland | paknsave **Mangere** $2.97 Pams |
| `cheapest milk near Albany` | paknsave **Albany** $4.79 | new_world **Devonport** $4.94 |

**Diagnosed and fixed 2026-09-01 — it was not a near-filter bug.** Both answers
were fixture rows: Devonport and Mangere are fixture-only suburbs, matched
byte-for-byte to `fixtures/products.json`. The fixture rows had come back in the
live table (the 2026-08-30 removal in §3j was silently undone by a plain
`load_seed_data.py` run) and shadowed the real Lineage B prices through the
synonym candidate order, so `cheapest milk near Albany` served a fabricated
Devonport $4.94 instead of the real Albany $4.79. The near filter, region
mapping and coordinates were all correct. **Resolved the same day:** the 152
fixture rows were removed and the loader guarded against recurrence; the
endpoint now returns Pak'nSAVE Albany $4.79 for milk and $9.49 for butter. Full
record in [`docs/OPEN-REVIEW-near-filter-drift.md`](OPEN-REVIEW-near-filter-drift.md).
A number changing while both planes agree is exactly the "nothing alarmed
because everything matched" failure this file keeps recording.

### Plane roles recorded as a decision (Philip, 2026-09-01)

Until now "the hand-made plane is production" was an emergent fact — true
because it is alarmed and contract-named — rather than a recorded decision. It
is now recorded:

- **PRIMARY: the hand-made plane** (`grocery-orchestrator-dev` / `woqmel35lk`).
  It serves, it is alarmed (§3l), and `CONTRACT-v1.md` names it. It stays
  primary and is NOT retired.
- **Parallel: the CDK plane** (`grocery-orchestrator-dev-cdk` / `crm1xkrk34`).
  Exercised, at parity, not serving.

This does not contradict §3m's finding that the CDK plane is the better
*eventual* cutover target (finite log retention, tracing on from the start). It
records which plane is primary *now*.

**Budget-collapse rule (Philip, 2026-09-01):** once `ObservabilityStack`
deploys its own `$25` monthly budget beside the hand-made
`grocery-orchestrator-monthly-dev` (§3l), two will exist. Two budgets are free,
so collapsing one is tidiness, not cost. **Keep the budget the SURVIVING plane
owns; delete the other.** While the hand-made plane is primary, its budget
stays. "Collapse the hand-made one" refers to the BUDGET at cutover, not the
plane — the plane stays primary. Conflating the two would retire the serving
plane, the opposite of keeping it primary.

### Source priority is now first-class config

The 2026-08-29 decision (ADR 0003; `infra/docs/08-OPEN-DECISIONS.md` §1) that
the data team's collected catalogue (Lineage B) is the PRIMARY ingestion input
and the fixtures are the fallback lived only in an env var (`PRICE_SOURCE`) and
a decision doc. It is now `config/data-sources.json`: an ordered, reviewable
declaration that Lineage B is primary and fixtures are the fallback, read by
`ingestion/sources.py::resolve_source`.

- **Nothing about what the planes SERVE changes.** Both serve Lineage A
  (`grocery-products-dev`), selected by `USE_DYNAMODB`. This config chooses
  which recorded catalogue INGESTION refreshes that table from.
- **The acquisition tripwire is unchanged.** Both sources are recorded data on
  disk; `resolve_source` still raises `NotImplementedError` if
  `LIVE_ACQUISITION=1`, checked before the config is consulted. Precedence:
  `LIVE_ACQUISITION` (refuse) > `PRICE_SOURCE` env > `default_source` in config.
- **`default_source` is still `fixtures`, deliberately.** Priority (Lineage B is
  primary) and runtime default (what `resolve_source` picks with no env set) are
  separate questions. Promoting Lineage B to the automatic default changes what
  the deployed ingestion Lambda does by default and is left as an explicit,
  dry-run-evidenced follow-up in the config file — the same reason the real
  catalogue load on 2026-08-30 was an explicit operation, not a silent default
  flip.

Verified offline: full suite 868 passed / 31 skipped, ruff + format clean,
pyright clean, config placeholder guard clean.

## 3t. The fixture rows were removed from the live table — 2026-09-01

The §3s parity re-run found the live endpoint serving fixture prices again
(`cheapest milk near Albany` → New World Devonport $4.94), which meant the
2026-08-30 fixture removal (§3j) had been silently undone — a plain
`scripts/load_seed_data.py` run (its default action LOADS) re-added all 152
fixture rows, and they shadow the real catalogue through the synonym candidate
order. Full diagnosis in `docs/OPEN-REVIEW-near-filter-drift.md`.

**Removed and verified against the account** (SSO profile, `097087133897`):

```
load_seed_data.py --remove --dry-run   ->  152 of 152 present
load_seed_data.py --remove             ->  152 deleted
```

Confirmed after, three ways:

- **GSI1 `product_key` counts:** `milk-2l` = 0, `butter-500g` = 0 (fixtures
  gone); `standard-milk-2l` = 10 (real data intact).
- **Endpoint, fresh session ids:** `cheapest milk near Albany` → Pak'nSAVE
  Albany $4.79; `cheapest butter` → Pak'nSAVE Albany $9.49 — the real answers,
  and butter now matches the original 2026-08-30 record exactly.
- **The recurrence is now guarded** (PR #64): `load_seed_data.py` refuses to
  load fixtures over the real catalogue without `--force`, with a regression
  test, so a stray plain run cannot re-add them silently.

**A casing trap worth carrying.** The first live probe queried
`store_key = "new_world#Devonport"` (display casing) and got count 0, which
briefly read as "already clean". The stored key is slugged lowercase
(`new_world#devonport`); the authoritative, casing-independent check is a GSI1
query on `product_key`. Cross-checking the surprising zero against GSI1 is what
caught the mistake before it became a false "already fixed". When probing this
table by hand, use GSI1 `product_key` or the exact slugged `store_key`, never
the display-cased suburb.
