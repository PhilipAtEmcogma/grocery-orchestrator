# AgentCore Runtime data-quality reviewer — design, architecture, and reasoning record

**Status:** design approved (autonomous delegation, 2026-09-01); offline code and
this document land first, the live prototype follows behind an explicit
confirmation. ADR 0002 remains *Proposed* — this is the reviewer Runtime it
recommends, and nothing here changes shopper behaviour.

**Scope of this document.** It is three things at once, deliberately:

1. A **design spec** — what the reviewer is, what it is allowed to touch, and
   the trust boundary that makes it safe.
2. An **implementation spec** — the concrete pieces of code and infrastructure,
   in the order they get built, so someone (including future-me) can follow it.
3. A **reasoning record** — *why* each decision went the way it did, dated, so
   that when the workshop is over this file explains the experience rather than
   just the result. The project's convention is that the "why" outlives the
   "what"; this file is written to that convention.

Read alongside [`adr/0002-staged-agentcore-and-managed-ai-services.md`](adr/0002-staged-agentcore-and-managed-ai-services.md)
(the decision this implements), [`OPEN-REVIEW-adr-0002.md`](OPEN-REVIEW-adr-0002.md)
(why the reviewer is the one component worth asking for), and requirements
13.7-13.8 (the hard boundary).

---

## 1. What this is, in one paragraph

An isolated AgentCore Runtime hosts a **data-quality reviewer**: it is handed a
capped, sanitised snapshot of catalogue rows and it reports which rows look
*wrong* — a price far from its own history, a meat filed under produce, a key
whose name disagrees with its display — so a human can check them. It has no
shopper data, no database read path, no write authority, and no place on the
shopper path. Everything it produces is validated by deterministic code
**outside** the Runtime before a person ever sees it. It is the least agentic
possible use of an agent runtime, and that is the point.

---

## 2. Why this exists (the hypothesis, and the honest case against)

The project already catches the one catalogue defect it knows about — a
`unit_price_nzd` of `2490.00` against a $2.49 broccoli — in six lines of
deterministic arithmetic (`implausible_unit_price`). That rule will always beat
a model at the thing it was written for.

So the reviewer's entire value proposition is a **hypothesis**: *does a language
model find catalogue defects that nobody thought to write a rule for?* Price
drift against a product's own history, a mislabelled category, a name/key
mismatch — none of these is caught by arithmetic, because each row is internally
consistent. The reviewer earns its place only on those, and the offline eval
(`evals/run_review.py`) is built to measure exactly that and to give **no
credit** for re-finding what the rules already catch.

**The case against, kept honest** (from `OPEN-REVIEW-adr-0002.md`):

- Service count is not a product outcome. A shopper gets nothing directly.
- The value is unproven — it is a hypothesis, and it might be that a model finds
  nothing a cheap rule could not.
- It is another surface to secure and pay for.

The reason we build it anyway: it is the one proposal here whose answer this
project cannot get any other way, and it is a **cheap, reversible, well-fenced
test** of a real question. If the hypothesis fails, we have learned that — and
learned the AgentCore Runtime mechanics — for a few dollars and a teardown.

**Learning objective (equal partner to the product question).** This is
deliberately an AWS-learning exercise: session-isolated microVMs, the
`/invocations` + `/ping` protocol contract, an execution role that trusts
`bedrock-agentcore.amazonaws.com`, CodeZip build/deploy, the `invoke_agent_runtime`
session lifecycle, and Runtime-boundary observability. We keep the product value
as high as the fence allows, but the primary driver is hands-on AgentCore.

---

## 3. The trust boundary — Option A, and why

**The model proposes; deterministic code disposes — and the code that disposes
lives OUTSIDE the thing being disposed.**

There are two places the deterministic validation (`validate_findings`) could
run:

- **Option A (chosen):** the Runtime calls the model and returns the *raw*
  findings. The caller — on our side, outside the microVM — rebuilds the
  snapshot it sent, runs `validate_findings` against it, and only then does a
  human see anything.
- **Option B (rejected):** the Runtime validates its own output before
  returning.

Option B is simpler wiring and it is wrong for one reason that this project has
already been burned by: **the validator must not live inside the component it is
there to check.** A hijacked or malfunctioning Runtime that both invents a
finding *and* validates it has defeated the check. Keeping validation on our
side means a compromised Runtime can only ever return *claims*, and every claim
is checked against the snapshot we actually sent — the row must be in it, the
quoted values must match it exactly, and no value may be prescribed. That is the
same "shape is not identity" lesson that `assert_citations_match_retrieval`
exists for on the shopper path.

```text
  OUR SIDE (trusted)                 THE RUNTIME (untrusted)
  ------------------                 -----------------------
  build sanitised snapshot  ──────►  receive rows (data, delimited)
  (allowlist, capped, no PII)        call the model
                                     map reply -> raw findings
  validate_findings   ◄──────────    return RAW findings (unvalidated)
  (row in snapshot? quotes           
   match? not prescriptive?)         
  write artefact / show a human      
```

The offline reviewer we already shipped (`src/review/review_snapshot`) validates
*inside* the function, which is correct for a single-process offline run. For
the Runtime we split that: the microVM runs the **model half only**, and the
**validation half** runs on the invoke side. Both halves are the same code from
`src/review/` — nothing is reimplemented.

---

## 4. What the reviewer can and cannot see (Req 13.8)

The boundary is already built and tested in `src/review/snapshot.py`. The
Runtime does not get to widen it, because the invoke side builds the snapshot
and the snapshot is built from an **allowlist**, not by stripping a rich object.

**It sees exactly these fields, and nothing else:**

- Identity/product: `store_key`, `product_key`, `store`, `store_location`,
  `display_name`, `canonical_name`, `category`
- Price/pack: `price_nzd`, `unit`, `unit_price_nzd`, `pack_grams`, `on_special`,
  `valid_date`
- Baseline (from the append-only price history): `baseline_avg_nzd`,
  `baseline_min_nzd`, `baseline_max_nzd`, `baseline_samples`,
  `baseline_window_days`, `deviation_ratio`

Money is strings. `store_location`/`lat`/`lon` — note the snapshot carries
`store_location` (a suburb name) but not coordinates; both are *supermarket*
geography, never a shopper's.

**It shall NOT receive** (Req 13.8, guaranteed by there being *no field* for
them): shopper messages, shopper locations, dietary data, session ids,
credentials. **It shall NOT** treat a price field as publication authority,
publish a price, mutate production, invoke the shopper path, or have a finding
acted on without deterministic validation and human approval.

**Caps:** at most `MAX_SNAPSHOT_ROWS = 500` rows per review; asking for more
*raises* rather than truncating (a truncated snapshot makes a finding about "the
catalogue" really a finding about whichever rows arrived first). Token cap on
the model call. Session timeout bounds runtime.

---

## 5. Architecture

### 5.1 The minimal prototype (what we build first)

Deliberately the smallest thing that proves the Runtime end to end. **No
DynamoDB Streams, no SQS, no S3 artefact store yet** — those are the
production-shaped event plumbing and they are a clean second increment.

```text
  LOCAL / OPERATOR MACHINE                         AWS ap-southeast-2
  ------------------------                         ------------------
  scripts/review_runtime.py
    1. build snapshot from a source
       (fixtures, or DynamoPriceHistory
        baselines + products rows)
    2. invoke_agent_runtime(payload) ───────────►  AgentCore Runtime (microVM)
                                                      agentcore/reviewer entrypoint
                                                      - /ping  -> Healthy
                                                      - /invocations:
                                                          rows -> prompt ->
                                                          Bedrock (Nova Lite) ->
                                                          raw findings
    3. raw findings   ◄─────────────────────────    return raw findings JSON
    4. validate_findings (OUR side)
    5. print / write local artefact
       (recall vs the labelled dataset,
        cost, latency)
    6. delete_agent_runtime (teardown)
```

### 5.2 The event-driven shape (the ADR's target, a LATER increment)

Recorded here so the direction is on the record, but **not built in the
prototype**:

```text
  Ingestion -> DynamoDB Streams -> SQS (+DLQ) -> snapshot builder
            -> invoke Runtime -> raw findings -> validate -> S3 artefact
            -> SNS operator notification -> human approval
```

The prototype's manual invoke is the same `invoke_agent_runtime` call this shape
would make; only the *trigger* and the *artefact sink* change. Proving the
manual path first means the event plumbing is added against a Runtime already
known to work.

---

## 6. Component inventory

| Piece | Where | What it does | New? |
|---|---|---|---|
| `review_snapshot` core | `src/review/reviewer.py` | model call + map + validate (offline single-process) | exists |
| snapshot allowlist + caps | `src/review/snapshot.py` | the sanitised boundary | exists |
| `validate_findings` | `src/review/findings.py` | the 3-way deterministic check | exists |
| labelled dataset | `evals/cases/review_anomalies.json` | ground truth, 11 cases | exists |
| offline eval | `evals/run_review.py` | reviewer-only recall + false positives | exists |
| **Runtime entrypoint** | `agentcore/reviewer/` (new) | the code the microVM runs: `/ping`, `/invocations`, model-half-only | **new** |
| **invoke-side client** | `scripts/review_runtime.py` (new) | build snapshot, invoke, validate on our side, artefact | **new** |
| **model-half helper** | `src/review/reviewer.py` (small add) | `propose_findings(...)` — model call + map, NO validate, for the entrypoint to reuse | **new (small)** |
| design + reasoning | `docs/AGENTCORE-RUNTIME-REVIEWER.md` | this file | **new** |

The split in `reviewer.py`: `review_snapshot` (validate inside — offline use)
stays as is; add `propose_findings(rows, *, model) -> ReviewReport` which does
the model call + prompt only, so the Runtime entrypoint and the offline path
share the exact same model-facing code with the validation boundary drawn where
each needs it.

---

## 7. The Runtime entrypoint contract

From the AgentCore Runtime guide (HTTP protocol):

- Listens on `0.0.0.0:8080`, ARM64, CodeZip build.
- `GET /ping` -> `{"status": "Healthy"}`.
- `POST /invocations` -> JSON in, JSON out.

**Request payload** (what the invoke side sends — already the sanitised
allowlist form, so the microVM never receives anything wider):

```jsonc
{
  "table_name": "grocery-products-dev",
  "rows": [ { /* one SNAPSHOT_FIELDS dict */ }, ... ]   // <= 500
}
```

**Response payload** (raw, unvalidated findings — validation is our side):

```jsonc
{
  "ran": true,
  "findings": [
    {
      "store_key": "...", "product_key": "...",
      "kind": "price_deviation",
      "observation": "price is far above its own history",
      "quoted": { "deviation_ratio": "10.00" }
    }
  ],
  "error": ""
}
```

The entrypoint imports `build_review_prompt`, the `ReviewReport` schema, and a
`ModelClient` (the real Bedrock one inside the Runtime). It does **not** import
`validate_findings` — that dependency deliberately does not cross into the
microVM, so the trust boundary is visible in the import graph.

---

## 8. Threat model and IAM (ADR 0002 gate 3)

**Data classification.** Everything the Runtime receives is a supermarket price
row. No shopper PII by construction (§4). The one sensitivity is that catalogue
`display_name` is external text and therefore **untrusted** — a prompt-injection
vector. Mitigations: the rows are delimited in the prompt as data; the response
schema has no field for an invented value; and every finding is checked against
the snapshot on our side. The worst a hijacked review can do is emit findings, all
of which are rejected if they cite a row we did not send or quote a value the row
does not have.

**Execution role (least privilege).** Trusts `bedrock-agentcore.amazonaws.com`.
Grants ONLY:

- `bedrock:InvokeModel` on the **specific** Nova Lite model ARN it routes to —
  not `bedrock:*`, not `Resource: *`.
- CloudWatch Logs create-group/stream/put on its **own** log group.
- X-Ray `PutTraceSegments` / `PutTelemetryRecords`.

It grants **no** DynamoDB, **no** S3, **no** SQS, **no** shopper-table access,
**no** write anywhere. The Runtime cannot read the catalogue itself — it only
ever sees the rows pushed into a single invocation. That is the isolation Req
13.8 demands, expressed as an IAM boundary rather than a promise.

**Caller role (operator).** The invoke side needs
`bedrock-agentcore:InvokeAgentRuntime` (+ `CreateAgentRuntime` /
`DeleteAgentRuntime` / `StopRuntimeSession` for the prototype lifecycle), scoped
to the reviewer Runtime ARN. During the MCP-tool prototype this is the
operator's own credentials; when codified in CDK it becomes a named role.

**Region.** `ap-southeast-2`, structurally, like everything else. No cross-Region
inference profile.

---

## 9. Cost, and the removal criterion

**Bounded by how often we run it, not by traffic** — the reviewer runs over a
capped snapshot on demand, off the shopper path. The prototype cost is: a few
`invoke_agent_runtime` sessions (microVM compute while active) plus the Nova Lite
tokens per invocation, then teardown. August spend was $17.63 against a $25
budget, so a handful of experimental invokes is comfortably inside it.

**Removal criterion (ADR matrix):** stop/delete the Runtime if findings are not
useful, caps fail, data isolation fails, or cost exceeds approval. The prototype
*ends* in teardown by default — retention is a separate, later decision that
comes only after CDK codification.

---

## 10. Rollback and the teardown drill (ADR 0002 gate 7)

Because the prototype has nothing subscribed to it (no Streams/SQS), teardown is
trivial and is part of the prototype itself:

1. `stop_runtime_session` — end any active session (stops compute charge).
2. `delete_agent_runtime_endpoint` then `delete_agent_runtime` — remove the
   infrastructure.
3. Confirm with `list_agent_runtimes` that it is gone.

The drill *is* the last step of the prototype run, and its success (no resource
left, no shopper behaviour touched — there is none to touch) is the gate-7
evidence. Nothing about the shopper path can change, because the reviewer was
never connected to it.

---

## 11. Why MCP-tool prototype first, then CDK (gate 5)

ADR 0002 gate 5 says *CDK defines the resource* before it is retained. We are
**not** skipping that — we are sequencing it:

- **Prototype with the MCP `create_agent_runtime` tools.** This is the fastest
  way to learn the moving parts (build shape, entrypoint contract, session
  lifecycle, IAM trust) and to prove the hypothesis is even worth codifying. The
  first live deploy is treated as an **explicitly experimental, torn-down-after**
  artefact — created, invoked, measured, deleted.
- **Codify in CDK before calling it "retained".** Once the prototype proves the
  Runtime works and the findings are worth having, a new CDK stack
  (`ReviewerStack`, `ap-southeast-2`, `NAME_SUFFIX` convention, policy-as-data
  IAM) becomes the source of truth — the same discipline every other deployed
  resource in this repo follows. Until then, nothing is "retained": the
  prototype exists to be deleted.

This ordering is a deliberate learning choice: build by hand to understand it,
then make it reproducible. Recording it here so the sequence reads as intent, not
as a CDK step that was skipped.

---

## 11a. Cost-free simulation and preflight (before a single live cent)

Two pieces exist so the "several iterations to get a clean deploy" problem is
paid for in local CPU rather than in AWS charges:

- **`scripts/review_runtime.py --sim`** boots the ACTUAL entrypoint
  (`agentcore/reviewer/app.py`) as an HTTP server on localhost and calls it over
  a real socket — `GET /ping`, then `POST /invocations` — with a scripted model
  injected. It exercises the true HTTP contract the deployed microVM serves
  (payload shape, JSON in/out, raw-findings response, invoke-side parsing), so a
  serialization or contract bug shows up here against a socket rather than in a
  billed deploy iteration. The only things it cannot exercise are AWS itself:
  the microVM, the IAM role, and the real Bedrock call. Two tests
  (`tests/test_review_runtime.py`) pin this contract in CI.

- **`scripts/reviewer_runtime_preflight.py`** is the non-billable gate: it builds
  the CodeZip and checks it is under the AgentCore limits, confirms the
  entrypoint parses and declares its handlers, loads
  `config/iam-reviewer-runtime-role.json` and asserts it grants ONLY the
  reviewer's actions (no DynamoDB/S3/write — the Req 13.8 isolation invariant,
  machine-checked), and — only with credentials — does a read-only model
  reachability check. Exit 0 means `create_agent_runtime` is very likely to
  succeed first try. It creates no billable resource and can be run as many times
  as the iteration needs.

The reviewer CodeZip measured **0.24 MB zipped / 0.62 MB unzipped** — pure
Python, with boto3 provided by the runtime — comfortably under the 250 MB / 750
MB CodeZip limits.

## 12. Build order (the implementation plan)

1. **`propose_findings` helper** in `src/review/reviewer.py` — model-half-only,
   no validation. Offline-testable with the scripted client.
2. **Runtime entrypoint** under `agentcore/reviewer/` — `/ping`, `/invocations`,
   reuses `propose_findings` + a Bedrock `ModelClient`. No `validate_findings`
   import.
3. **Invoke-side client** `scripts/review_runtime.py` — build snapshot, invoke
   (or a local in-process stub for tests), `validate_findings` on the response,
   print/write artefact.
4. **Tests** — entrypoint maps payload -> report; invoke-side validates and
   rejects fabrication; no-AWS offline path via a stub transport.
5. **Verify** offline (suite, ruff, format, pyright, validate).
6. **PR** the offline pieces + this doc; pause.
7. **Live prototype** via MCP tools: create -> invoke over the labelled dataset
   -> measure recall/cost -> record here -> delete (teardown drill).
8. **Record** the outcome in this doc + ADR 0002 Status line; note CDK
   codification as the next step before "retained".

---

## 13. Reasoning record (dated)

*Written so a future reader can absorb the experience, not just the result.*

**2026-09-01 — Option A over Option B (validate outside the Runtime).**
The decision that shapes everything else. A validator inside the component it
validates is defeated the moment that component is compromised. Keeping
`validate_findings` on the invoke side means a hijacked Runtime returns only
*claims*, checked against the exact snapshot we sent. Cost: two round-trips of
data shape (rows out, findings back) instead of one clean call. Worth it — the
same reasoning that split `assert_grounded` from `assert_citations_match_retrieval`
on the shopper path.

**2026-09-01 — minimal prototype (no Streams/SQS/S3) before the event-driven
shape.** The ADR's target architecture is event-driven, but building that first
would mean debugging Runtime, IAM, entrypoint, *and* four pieces of event
plumbing at once, with the Runtime the least understood of them. A manual invoke
proves the Runtime alone. The event plumbing is the same `invoke_agent_runtime`
call behind a different trigger, so nothing is thrown away. Smaller blast radius,
cheaper, and the teardown drill is trivial when nothing is subscribed.

**2026-09-01 — MCP-tool prototype, then CDK.** Chosen for the learning value:
build it by hand with the AgentCore MCP tools to understand the mechanics, prove
the hypothesis is worth codifying, and only then make it reproducible in CDK. The
first deploy is a throwaway — created to be deleted. This is not skipping gate 5;
it is deferring "retained" until there is something worth retaining.

**2026-09-01 — the model call lives INSIDE the microVM, not on our side.**
An alternative was to call Bedrock from our side and use the Runtime only as a
shell. Rejected: that would make the Runtime pointless (it would host nothing),
and the whole learning objective is the Runtime *doing the model work* in
isolation, with its own least-privilege `bedrock:InvokeModel` on a specific model
ARN. The isolation is the lesson.

**2026-09-01 — `TASK_REVIEW_SNAPSHOT` is unscored, and that is a known gap to
close before any qualification claim.** The task constant exists and routes FAST,
but there is no scorecard for a model on it yet. The prototype measures a model
live for the *first* time; that measurement becomes the scorecard. The reviewer
is off the shopper path and qualifies no shopper route, so the 90% task floor
that governs shopper tasks does not gate it — but the eval discipline (record
model/region/date/dataset/pass-rate, separate infra failure from quality) still
applies, and the result is recorded here.

**2026-09-02 — live prototype run, and what it cost to learn.**

Deployed via the AWS CLI / boto3 (not the AgentCore MCP tools — those could not
see the SSO credentials from a separate process). Identifiers, since torn down:

- Runtime: `grocery_reviewer_dev-4HWlXa4VWd`, version 1, HTTP protocol,
  `PYTHON_3_13`, CodeZip from S3, role `grocery-reviewer-runtime-dev-role`.
- Reached `READY` on the FIRST create — the cost-free preflight and the HTTP
  simulation did their job. Nothing about the deploy itself needed iterating.

**Two real learnings, both the point of doing it live:**

1. *The CodeZip runtime provides no dependencies.* Confirmed against the AWS
   docs and then in practice: the zip must bundle arm64 Linux wheels of
   everything imported (pydantic + pydantic-core + boto3 + botocore here — the
   entrypoint has no langchain dependency, bedrock.py calls Converse directly).
   `scripts/build_reviewer_runtime.py` fetches them with
   `uv pip install --python-platform aarch64-manylinux2014 --python-version 3.13`.
   Result: 19 MB zipped / 30 MB unzipped, far under the 250/750 limits.

2. *The `apac.` inference profile fans out across APAC regions, and IAM must
   allow the model in each.* The first live invoke returned
   `AccessDeniedException` on `bedrock:InvokeModel` for
   `arn:aws:bedrock:ap-northeast-1::foundation-model/amazon.nova-lite-v1:0` — the
   cross-region profile had routed the call to TOKYO, and the role only granted
   the Sydney foundation-model ARN. Fix: wildcard the REGION on the
   foundation-model ARN (`arn:aws:bedrock:*::foundation-model/amazon.nova-lite-v1:0`)
   while keeping the model id pinned. This is a genuine, documented AgentCore +
   cross-region-inference interaction, and it is exactly the kind of thing the
   live prototype exists to surface — the offline simulation cannot, because it
   has no IAM.

**The measurement (the hypothesis result), first `TASK_REVIEW_SNAPSHOT`
scorecard:**

- Model: Amazon Nova Lite (`apac.amazon.nova-lite-v1:0`), region ap-southeast-2
  (routed to ap-northeast-1 for the model), 2026-09-02, one full-dataset run.
- **Reviewer-only recall 60% (3/5)** — caught rev-001/002/005 (all
  `price_deviation`, quotes correct, validated clean). Missed rev-003 (beef
  under produce, not reported) and scored rev-004 as a miss on an EXACT-KIND
  basis: the model flagged the milk/orange-juice row but called it
  `suspect_category` where the label is `name_mismatch` — it saw the anomaly,
  classified it differently.
- **False positives 0/4** — no clean row flagged. It did not cry wolf.
- **Fabrication rate 33%** — the model misquoted `price_nzd` as "2490.00" on the
  code-caught broccoli row (the row's price is 2.49), and `validate_findings`
  REJECTED it on the caller's side. This is the Option-A trust boundary working
  live: a fabricated quote never reached a human, because the validator does not
  live inside the Runtime.
- **Non-determinism observed:** back-to-back runs returned 5 and 6 findings.
  Recorded, not averaged — a single run neither qualifies nor ranks a model
  (the eval-discipline rule), and this is why.

**Cost:** a handful of `invoke_agent_runtime` calls (Nova Lite over 1–11 rows,
~4–7s microVM active each) plus one create/delete cycle. Cents. The removal
criterion was met by design: the prototype ended in teardown.

**Teardown drill (ADR gate 7):** `delete_agent_runtime` → `list_agent_runtimes`
returned `[]`; `get_agent_runtime` returns `ResourceNotFoundException`; the S3
zip deleted. No shopper behaviour could change — the reviewer was never
connected to the shopper path. The execution role was left in place (roles are
free and inspectable); remove `grocery-reviewer-runtime-dev-role` if a clean
account is wanted.

**Verdict on the hypothesis:** promising but unproven at this scale. The model
caught anomalies the deterministic rules structurally cannot (3 price
deviations), with zero false positives — real signal. But it missed one,
mis-classified another, and fabricated a quote on a third, and 11 cases with a
non-deterministic model is too small to qualify anything. The honest next step
if pursued: expand the labelled set, run repeated reps, and record a banded
score — not promote this single run. And note the classification gap
(`suspect_category` vs `name_mismatch`): the scorer's exact-kind match may be
stricter than the product needs, since a human triaging findings cares that the
row was flagged more than which label it got. That is a scoring-design question
to settle before any qualification claim.

---

## 14. What would change the design

- **If the hypothesis fails** (a live model finds nothing the rules miss, or
  drowns real findings in false positives): the reviewer's deterministic half
  becomes a human workflow, the Runtime is deleted, and ADR 0002's reviewer line
  closes as not-pursued. Cheap to reach, by design.
- **If it succeeds and is retained:** codify in CDK, add the DynamoDB Streams ->
  SQS/DLQ trigger and the S3 artefact sink, and add SNS operator notification —
  the §5.2 shape. Each is its own increment with its own evidence.
- **If a managed evaluation is later wanted** (Bedrock Model Evaluation /
  AgentCore Evaluations): only as companion evidence, never replacing the local
  eval, and only once there are labelled findings worth evaluating — which is
  exactly why the ADR withdrew it from the current ask.

---

## 15. Teardown inventory and the redeploy runbook

*Written for whoever picks this up later — including a future maintainer taking
it to market. The prototype was deleted on 2026-09-02; this section is the exact
record of what went away, what stayed, and how to reconstruct it.*

### 15.1 What the teardown removed, and what persists

The teardown was `delete_agent_runtime` plus deleting the S3 code object. That
is deliberately narrow — it removes everything that costs money or runs, and
leaves the cheap, inspectable definitions in place.

| Resource | Identifier | State after teardown | Why |
|---|---|---|---|
| AgentCore Runtime | `grocery_reviewer_dev-4HWlXa4VWd` (version 1, DEFAULT endpoint) | **DELETED** — `get_agent_runtime` returns `ResourceNotFoundException`, `list_agent_runtimes` returns `[]` | The only billable, running resource. Deleting the runtime removes its DEFAULT endpoint and the auto-created workload identity with it. |
| Runtime session(s) | e.g. `reviewerfull000…` | **GONE** — microVMs auto-terminate on idle timeout (300s here) and are destroyed with the runtime | Session compute is the per-second charge; nothing persists a session. |
| S3 code object | `s3://bedrock-agentcore-code-097087133897-ap-southeast-2/reviewer/reviewer-runtime.zip` | **DELETED** | The deployment artefact. Rebuildable from source in one command (§15.3), so it is not worth keeping. |
| S3 bucket | `bedrock-agentcore-code-097087133897-ap-southeast-2` | **KEPT, empty** (block-public, versioned, AES256) | The standard AgentCore code-bucket name for the account/region. Empty costs ~nothing; reusable for the next deploy. **Now REFERENCED by `ReviewerStack`** (`infra/lib/reviewer-stack.ts`) as the CodeZip location — no longer an orphan. |
| Execution role | `grocery-reviewer-runtime-dev-role` (+ inline policy `grocery-reviewer-runtime-dev-policy`) | **KEPT** | IAM roles are free and inspectable. **Now DEFINED by `ReviewerStack`** from the same `config/iam-reviewer-runtime-role.json` — the live role and the CDK definition are one identity, so keeping it means the account already matches what the stack synthesises. |
| CloudWatch log group | `/aws/bedrock-agentcore/grocery-reviewer-runtime-dev*` | **KEPT if created** (may not exist — no invoke logged enough to create it) | Logs are tiny and carry no PII by design (the reviewer logs THAT a review ran, never what it reviewed). Delete for a clean account. |
| Source, IAM config, build/preflight scripts | `agentcore/reviewer/`, `config/iam-reviewer-runtime-role.json`, `scripts/build_reviewer_runtime.py`, `scripts/reviewer_runtime_preflight.py`, `scripts/review_runtime.py` | **IN THE REPO** | The whole point of the record: the runtime is reconstructable from these, so the deployed copy is disposable. |

**To fully clean the account** (optional, if no redeploy is planned):

```powershell
# Role + inline policy
aws iam delete-role-policy --role-name grocery-reviewer-runtime-dev-role `
  --policy-name grocery-reviewer-runtime-dev-policy
aws iam delete-role --role-name grocery-reviewer-runtime-dev-role
# Log group, if it exists
aws logs delete-log-group --log-group-name /aws/bedrock-agentcore/grocery-reviewer-runtime-dev
# Bucket (must be empty first; --force empties it)
aws s3 rb s3://bedrock-agentcore-code-097087133897-ap-southeast-2 --force
```

**Decision, 2026-09-02 — KEEP BOTH the role and the bucket.** When the prototype
was torn down these were orphans, and "clean them up" was the natural call. The
CDK codification (`Grocery-Reviewer-dev`, ADR gate 5) changed that: the stack now
**defines** the role from the same `config/iam-reviewer-runtime-role.json` and
**references** the bucket as the CodeZip location. So they are no longer leftover
prototype artefacts — they are the CDK stack's own resources that happen to
already exist. Both cost effectively nothing (an IAM role is free; an empty,
block-public bucket is ~free), keeping them means the account already matches
what the stack synthesises, and deleting them would only force a recreate before
any future deploy. The clean-up commands above remain here for a genuinely fresh
account, but they are deliberately NOT run.

### 15.2 Prerequisites to redeploy

- AWS credentials for the target account, region `ap-southeast-2`. For SSO:
  `aws sso login --profile <profile>` then `$env:AWS_PROFILE = "<profile>"` and
  `$env:AWS_REGION = "ap-southeast-2"` in the shell. Verify with
  `aws sts get-caller-identity`.
- `uv` on PATH (for the arm64 wheel fetch) and the project `.venv`.
- Bedrock model access to Amazon Nova Lite enabled in the account.
- The numbered Guardrail exists (`b1xezpqe04kx` version `2` in the dev account;
  substitute the real one for a new account).

### 15.3 The redeploy runbook (exact steps and parameters)

Every value below is a real parameter used in the 2026-09-02 deploy. Substitute
the account id / bucket / guardrail for a new account.

**Step 0 — preflight (free, catches most problems before spending):**
```powershell
.\.venv\Scripts\python.exe scripts/reviewer_runtime_preflight.py
# Exit 0 = build, entrypoint, IAM, and model reachability all OK.
```

**Step 1 — execution role** (skip if `grocery-reviewer-runtime-dev-role` still
exists):
```powershell
.\.venv\Scripts\python.exe scripts/apply_iam.py --config config/iam-reviewer-runtime-role.json
# Placeholders ${AWS_REGION}/${AWS_ACCOUNT_ID} resolve from STS at apply time.
```
Role ARN pattern: `arn:aws:iam::<account>:role/grocery-reviewer-runtime-dev-role`.

**Step 2 — build the deployable arm64 CodeZip:**
```powershell
.\.venv\Scripts\python.exe scripts/build_reviewer_runtime.py
# Out: build/reviewer-runtime.zip  (~19 MB zipped)
```

**Step 3 — S3 bucket (skip if it exists) and upload:**
```powershell
$b = "bedrock-agentcore-code-<account>-ap-southeast-2"
aws s3api create-bucket --bucket $b --region ap-southeast-2 `
  --create-bucket-configuration LocationConstraint=ap-southeast-2
aws s3api put-public-access-block --bucket $b --public-access-block-configuration `
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-versioning --bucket $b --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket $b --server-side-encryption-configuration `
  '{\"Rules\":[{\"ApplyServerSideEncryptionByDefault\":{\"SSEAlgorithm\":\"AES256\"}}]}'
aws s3api put-object --bucket $b --key "reviewer/reviewer-runtime.zip" `
  --body build/reviewer-runtime.zip --expected-bucket-owner <account>
```

**Step 4 — create the runtime.** The full `create-agent-runtime` input, which
is the authoritative parameter set:
```jsonc
{
  "agentRuntimeName": "grocery_reviewer_dev",
  "agentRuntimeArtifact": {
    "codeConfiguration": {
      "code": { "s3": { "bucket": "bedrock-agentcore-code-<account>-ap-southeast-2",
                        "prefix": "reviewer/reviewer-runtime.zip" } },
      "runtime": "PYTHON_3_13",
      "entryPoint": ["main.py"]
    }
  },
  "roleArn": "arn:aws:iam::<account>:role/grocery-reviewer-runtime-dev-role",
  "networkConfiguration": { "networkMode": "PUBLIC" },
  "protocolConfiguration": { "serverProtocol": "HTTP" },
  "lifecycleConfiguration": { "idleRuntimeSessionTimeout": 300, "maxLifetime": 1800 },
  "environmentVariables": {
    "AWS_REGION": "ap-southeast-2",
    "USE_BEDROCK": "1",
    "REVIEWER_MODEL_KEY": "nova-lite",
    "BEDROCK_GUARDRAIL_ID": "b1xezpqe04kx",
    "BEDROCK_GUARDRAIL_VERSION": "2",
    "REQUIRE_GUARDRAIL": "1"
  },
  "description": "ADR 0002 WS2 data-quality reviewer."
}
```
```powershell
aws bedrock-agentcore-control create-agent-runtime --cli-input-json file://<that-file>.json
# Poll until READY:
aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id <id> `
  --query "{status:status,reason:statusReason}"
```

**Parameter notes that cost real time to learn (do not re-derive them):**
- `runtime` must be `PYTHON_3_13` (matches the wheels the build fetches).
- `entryPoint` is `["main.py"]` — a **filename at the zip root**, not a module
  path. `main.py` is the shim that calls `agentcore.reviewer.app.main`.
- `serverProtocol` must be `HTTP` (the entrypoint speaks `/invocations` + `/ping`),
  **not** the skeleton's default `MCP`.
- The execution role's `bedrock:InvokeModel` resource **must region-wildcard the
  foundation-model ARN** (`arn:aws:bedrock:*::foundation-model/amazon.nova-lite-v1:0`)
  because the `apac.` inference profile routes across APAC regions. A
  region-pinned ARN fails with `AccessDeniedException` the moment it routes to
  Tokyo. This is already correct in `config/iam-reviewer-runtime-role.json`.

**Step 5 — invoke and measure:**
```powershell
.\.venv\Scripts\python.exe scripts/review_runtime.py --arn <runtime-arn> `
  --session-id ("reviewer" + "0" * 28)   # session id must be >= 33 chars
```

**Step 6 — teardown (always, unless retaining):**
```powershell
aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id <id>
aws s3 rm s3://bedrock-agentcore-code-<account>-ap-southeast-2/reviewer/reviewer-runtime.zip
# Confirm gone:
aws bedrock-agentcore-control list-agent-runtimes --query "agentRuntimes[].agentRuntimeId"
```

### 15.4 Bringing it to market (what changes from prototype to product)

The prototype is a manual invoke. A production reviewer is the §5.2 event-driven
shape, and the honest gaps between here and there:

1. **CDK codification (ADR gate 5) is mandatory before "retained".** The manual
   CLI steps above become a `ReviewerStack` in `infra/`: the role from the same
   `config/iam-reviewer-runtime-role.json`, the runtime as an
   `aws_bedrockagentcore` (or an escape-hatch `CfnResource` until L2 constructs
   exist), region structural at `ap-southeast-2`, `NAME_SUFFIX` convention. The
   CLI parameter set in §15.3 is the CDK prop set.
2. **Qualify the model properly.** The prototype produced ONE non-deterministic
   run (60% recall, 5–6 findings across reps). Before it gates anything: expand
   `evals/cases/review_anomalies.json`, run repeated reps per model, record a
   BANDED score with the eval-discipline provenance (model/region/date/dataset/
   pass-rate/latency/tokens/cost), and settle the exact-kind scoring question
   (`suspect_category` vs `name_mismatch` — a human triaging cares the row was
   flagged, maybe not which label).
3. **Wire the trigger and sink** (each its own increment with its own evidence):
   DynamoDB Streams on the products table → SQS (+DLQ, least-privilege, bounded
   retries) → snapshot builder → invoke → validate → **S3 artefact** (encrypted,
   versioned, lifecycle) → **SNS** operator notification → human approval. The
   invoke and validate steps are unchanged from the prototype; only the trigger
   and the artefact sink are new.
4. **Observability.** CloudWatch metrics/alarms for the reviewer (invocation
   errors, fabrication-rate trend, cost), a Budget line, X-Ray traces — the same
   `src/observability/base.py` privacy-safe posture (log `session`/counts, never
   row contents or findings text).
5. **Move the model off the shared dev Guardrail** if the reviewer's imperative
   prompt ever trips `PROMPT_ATTACK` at scale (it did not in the prototype, but
   the repair task's history says watch for it). A reviewer-specific Guardrail
   version is the clean fix, not loosening the shared one.
6. **Cost model.** The reviewer's cost is bounded by how often it runs, not by
   shopper traffic. In the event-driven shape that is "per ingestion batch",
   which is predictable — put a Budget alarm on it and the removal criterion (ADR
   matrix) stays enforceable.
