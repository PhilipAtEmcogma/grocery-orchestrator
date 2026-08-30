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
| SNS topic | `grocery-orchestrator-alarms-dev` | alarms: handler-escaped, api-5xx; one confirmed email subscriber |
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
2026-08-30, which predated Pilot Tasks 4-7. It now serves **version `7`** -- v6 was `main` at commit `2412ac3`, v7 adds
the freshness decision in section 3c. The defect that mattered is gone: the endpoint
no longer invents a `$0` budget from a message that never mentioned money.

| Request | v5 (until 2026-08-30) | v6 (now) |
|---|---|---|
| `feed my flat of 3 this week` | `BUDGET_INFEASIBLE`: *"I couldn't build a plan within $0"* | `clarification` asking what they want to spend |
| `cheapest butter` | five citations, presented as current | `STALE_DATA` naming the 2026-07-31 capture date |

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
meal-plan turns per minute, falling to 5 when the repair loop fires** —
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
