# Philip_demo

Nineteen runnable demonstrations of the grocery orchestrator, without a UI.

Each file takes one seam of the system, opens it, and shows what is actually
behind it — using the project's own modules, never a second implementation.
Demo 19 puts them back together end to end.

**Start with the demo table below, or jump straight to
[`03_grounding_and_safety.py`](03_grounding_and_safety.py)** — the central
design decision is that the model never produces a number that reaches the
user, and that file is where you can watch it hold.

---

## The three modes

The distinction that matters most in this suite is between *"this ran"* and
*"this reached AWS"*. They are different claims, so every demo declares which
one it is making, in a banner printed before it does any work:

```
MODE        LOCAL
REQUIRES    nothing - fixtures/products.json
MOCKED      the price store (fixtures) and the model plane (ScriptedModelClient)
```

| Mode | What it uses | Needs credentials? | Needs network? | Costs money? |
|---|---|---|---|---|
| `local` *(default)* | fixtures + the scripted model client | no | no | no |
| `integration` | the deployed HTTPS endpoint; the MCP server over a real stdio subprocess | **no** — the dev stage is unauthenticated | yes | a few cents |
| `aws` | deployed AWS resources through boto3 — DynamoDB, Lambda config, Bedrock | yes | yes | cents, and only where a demo says so |

Select one with `DEMO_MODE`:

```bash
DEMO_MODE=integration python Philip_demo/15_deployed_endpoint.py
```

Three rules the suite keeps:

- **Asking for a mode a demo does not implement is an error, not a downgrade.**
  It exits 1 having run nothing. Silently answering from fixtures when you
  asked for Bedrock is the exact defect `docs/ARCHITECTURE.md` §3g exists to
  prevent, and reproducing it in a demo suite would be worse than not having
  one.
- **A mode that cannot run is BLOCKED, and blocked is not passed.** No
  credentials, no network — the demo says so, in full, and exits **2**.
- **A mocked component says it is mocked.** "Using the scripted model client"
  and "called Bedrock" are different sentences, and only one of them is ever
  true at a time.

`local` is the default because the whole orchestrator genuinely runs offline.
That is a property of the project rather than a concession made for the demos:
the graph depends on protocol boundaries (`PriceRepository`, `ModelClient`)
with fixture implementations behind them, so nothing above those two seams
needs an AWS account.

---

## Running

From the repository root:

```bash
python Philip_demo/run_all.py
```

One at a time:

```bash
python Philip_demo/01_price_check.py
```

On Windows without activating the virtualenv:

```
.venv\Scripts\python.exe Philip_demo/run_all.py
```

To page through it:

```bash
python Philip_demo/run_all.py | more
```

`run_all.py` runs every demo that supports the selected mode, reports the rest
as SKIPPED, and exits:

| exit | meaning |
|---|---|
| 0 | every demo that ran, passed |
| 1 | a demo **FAILED** — it broke, and that is a defect |
| 2 | a demo was **BLOCKED** — a dependency was missing. Nothing broke, and nothing was proven |

So in `local` mode it doubles as a smoke test that the demos still match the
code they describe.

---

## Prerequisites

**For `local` — the default — nothing beyond the project's own dependencies.**

```bash
pip install -r requirements.txt
```

No AWS account, no credentials, no network access, no environment variables.
Seventeen of the nineteen demos run here.

**For `integration`:** network access, and nothing else. Override the endpoint
with `CHAT_ENDPOINT_URL` if you are pointing at a different stage:

```bash
CHAT_ENDPOINT_URL=https://.../dev/chat DEMO_MODE=integration python Philip_demo/15_deployed_endpoint.py
```

**For `aws`:** credentials for the deployment account in `ap-southeast-2`, with
the grants in `config/iam-orchestrator-role.json` and
`config/iam-ingestion-role.json`. Every AWS-mode section in this suite is
**read-only** — `Query`, `GetFunctionConfiguration`, and `refresh(dry_run=True)`
which computes a diff and writes nothing.

Demo 14 additionally needs the Guardrail configured, and **refuses to run
without it** rather than calling a model with no content safety:

```bash
export BEDROCK_GUARDRAIL_ID=...
export BEDROCK_GUARDRAIL_VERSION=2      # a NUMBER, never DRAFT
DEMO_MODE=aws python Philip_demo/14_bedrock_model_plane.py
```

Demo 19 in `aws` mode uses DynamoDB but keeps the **scripted** model unless you
opt in explicitly, so the storage question is isolated from the model question
and neither is assumed:

```bash
USE_BEDROCK=1 BEDROCK_GUARDRAIL_ID=... BEDROCK_GUARDRAIL_VERSION=2 \
    DEMO_MODE=aws python Philip_demo/19_end_to_end.py
```

Never export a real credential into a shell you are pasting from. Nothing in
this directory reads an access key, and nothing prints one.

---

## The catalogue

| # | Demo | Capability | Modes | AWS | Network | Mocked |
|---|---|---|---|---|---|---|
| 01 | [`01_price_check.py`](01_price_check.py) | Term resolution, cross-store comparison, multi-item turns, honest gaps, the per-turn cap | local | no | no | prices, model |
| 02 | [`02_meal_planning.py`](02_meal_planning.py) | Budgeted plans, Python-computed arithmetic, the bounded repair loop, dietary exclusions, per-store baskets | local | no | no | prices, model |
| 03 | [`03_grounding_and_safety.py`](03_grounding_and_safety.py) | Why a hallucinated price is unrepresentable; prose degradation; the contract assertions; injection fencing; graph topology; guardrail tagging | local | no | no | prices, model |
| 04 | [`04_failure_modes.py`](04_failure_modes.py) | Every terminal error path and why each says something true; retryability | local | no | no | prices, model |
| 05 | [`05_model_routing.py`](05_model_routing.py) | The catalogue as data, per-task routing, tiers, capabilities, pinning, cost per turn, unroutable tasks | local | no | no | nothing — reads real config |
| 06 | [`06_http_api_and_idempotency.py`](06_http_api_and_idempotency.py) | The real Lambda handler over API Gateway events, status codes, malformed input, idempotent replay, payload conflicts | local | no | no | prices, model, store |
| 07 | [`07_observability.py`](07_observability.py) | Per-turn stats, latency attribution, instrumentation as wrappers, what is not logged, EMF metrics | local | no | no | prices, model |
| 08 | [`08_mcp_tool_surface.py`](08_mcp_tool_surface.py) | The bounded read-only MCP façade: two tools, the JSON-RPC handshake, default-OFF, caps, the privacy-safe audit, stdout as the protocol channel | local, **integration** | no | no | prices, model |
| 09 | [`09_ingestion_pipeline.py`](09_ingestion_pipeline.py) | Source → normalise → diff → write; the acquisition gate; GSI sort keys; the unit-price sentinel; idempotence shown, not claimed | local, **aws** | optional | optional | sources always; the write in local |
| 10 | [`10_dataset_transform.py`](10_dataset_transform.py) | Lineage B → Lineage A over the real 3,000-row collected catalogue: category mapping, the fail-closed name override, duplicate collapse, conservation | local | no | no | nothing |
| 11 | [`11_recipe_coverage_gate.py`](11_recipe_coverage_gate.py) | Why the imported 175 recipes were abandoned, measured; the staples experiment that made things worse; the forcing gate; and the curated catalogue that replaced them — resolved against **both** catalogues, because which one you measure against is the answer | local | no | no | nothing |
| 12 | [`12_location_and_freshness.py`](12_location_and_freshness.py) | Named regions, `strip_region`, unmapped regions refused, haversine radius, filters inside the repository, the staleness threshold and the STALE_DATA turn | local | no | no | prices, model |
| 13 | [`13_retrieval_backends.py`](13_retrieval_backends.py) | The `PriceRepository` Protocol with two implementations; term resolution and the refused substring match; GSI1 and GSI2; citation provenance | local, **aws** | optional | optional | model always; prices in local |
| 14 | [`14_bedrock_model_plane.py`](14_bedrock_model_plane.py) | Guardrail input tagging, per-request tag suffixes, the real Converse request, forced tool calls, fail-closed with no guardrail, usage accounting | local, **aws** | optional | optional | the whole model plane in local |
| 15 | [`15_deployed_endpoint.py`](15_deployed_endpoint.py) | The deployed service over HTTPS: the wire contract, region scoping, clarification, malformed input, idempotency across the network, latency | local, **integration** | no | optional | everything in local; nothing in integration |
| 16 | [`16_prompt_and_structured_output.py`](16_prompt_and_structured_output.py) | Every stage from sentence to validated object: intent extraction, the priceless context table, the plan prompt, `PlanDraft`, two repair prompts, the prose protocol | local | no | no | model |
| 17 | [`17_configuration_and_fail_closed.py`](17_configuration_and_fail_closed.py) | The dependency selector, the invisible production failure, `assert_production_configuration`, `USE_DYNAMODB=true`, DRAFT, wildcard CORS — and the deployed function's real environment | local, **aws** | optional | optional | nothing — synthetic environments, real check |
| 18 | [`18_evaluation_and_qualification.py`](18_evaluation_and_qualification.py) | All five eval harnesses run offline; scorecards as data; the qualification gate; pacing; the fields that keep the golden sets honest | local | no | no | model |
| 19 | [`19_end_to_end.py`](19_end_to_end.py) | One question through every layer, stage by stage, with a ledger of what was real | local, **aws**, **integration** | optional | optional | depends on mode — the demo prints which |

`_demo_support.py` holds the shared printing helpers, the mode machinery and
the fixture-date pin. It is not itself a demo.

---

## Suggested reading order

**What the system does** — 1 → 2.
**What it guarantees, and what it says when it cannot** — 3 → 4.
**How the AI layer is actually built** — 16 → 14 → 5 → 18.
**Where the data comes from** — 10 → 9 → 13 → 12 → 11.
**What is deployed, and what an operator sees** — 6 → 7 → 17 → 15 → 8.
**All of it at once** — 19.

If you have time for one file, read **3**. If you have time for two, read
**3** and **19**.

---

## Two things that look like bugs and are not

**Noisy JSON mid-run.** Demos 6, 15 and 19 print structured log lines and EMF
metric records. That is not debug output left in by accident: the dev server,
the test suite and the demos go through the same instrumented handler Lambda
does, and those lines are exactly what CloudWatch ingests in production. An
observability layer that only runs in production is one nobody has tested.
Demo 7 explains what they contain.

**Negative results.** Demo 11 reports that **zero** of the 175 imported
recipes can be fully priced — against the offline fixture *and* against the
real catalogue — and demo 17 reports that the production configuration check is
currently **inert** in the account. Both are true, both are the point, and a
demo suite that only shows what works tells you nothing about where the edges
are.

Demo 11's section 7 adds the part that is easy to get wrong. The 29 curated
recipes that replaced the imported ones are **29/29 costable against the real
catalogue and 14/29 against the 152-row offline fixture**, and the demo prints
both rather than picking the flattering one. The fixture number describes the
fixture, not the recipes — the same lesson this repository keeps arriving at
from different directions, which is that evidence is only about the thing it
was collected from.

---

## Configuration reference

| Variable | Read by | Effect |
|---|---|---|
| `DEMO_MODE` | every demo | `local` (default), `integration`, `aws` |
| `CHAT_ENDPOINT_URL` | 15, 19 | override the deployed endpoint |
| `ORCHESTRATOR_FUNCTION` | 17 | override the Lambda function name |
| `USE_BEDROCK=1` | 19 (aws mode) | opt into real model calls; not assumed |
| `BEDROCK_GUARDRAIL_ID` | 14, 19 | required before any Bedrock call |
| `BEDROCK_GUARDRAIL_VERSION` | 14, 19 | a **number**. `DRAFT` is refused |
| `MCP_ENABLED=1` | `scripts/mcp_server.py` | demo 8 sets it for itself |
| `FRESHNESS_AS_OF` | every offline demo | pinned automatically to the fixture capture date |

Nothing here reads or prints an AWS access key, a secret, or a token.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'src'`** — run from the repository
root, not from inside `Philip_demo/`. `_demo_support.py` inserts the repo root
on `sys.path`, so importing it first is what makes the `src.` imports resolve;
every demo does that before anything else.

**`DEMO_MODE='...' but this demo supports local, aws`** — you asked for a mode
this file does not implement. Nothing ran. The MODES block at the top of the
file lists what it does implement.

**`BLOCKED: ... NoCredentialsError`** — expected in `aws` mode with no
credentials configured. The demo ran everything it could offline and stopped at
the boundary. Exit code 2.

**Every price says `STALE_DATA`** — something cleared `FRESHNESS_AS_OF`.
Importing `_demo_support` pins it to the fixture capture date, because the
committed catalogue is a snapshot and judging it against the wall clock makes
every demo turn red on a day nobody chose. Demo 12 §7 has the reasoning.

**An integration run is slow, or a tail of turns fails** — that is the account's
Bedrock quota, not the service. The binding Nova Lite limit is 20 requests per
minute and cannot be raised; one turn costs 2–4 calls. Demos 15 and 19 pace
themselves at one turn every 8 seconds for exactly this reason. Do not remove
the pacing to "speed up the demo" — an unpaced run measures the quota.

**Demo 14 refuses to start in `aws` mode** — it needs `BEDROCK_GUARDRAIL_ID`
and a numbered `BEDROCK_GUARDRAIL_VERSION`. The client refuses to invoke a
model without content safety, and the demo refuses to pretend it did.

---

## Validation matrix

Every row below is the result of actually executing the demo, on
**2026-08-30** — `local` re-run on **2026-08-31** after demo 11 gained section
7 — on Windows 11 with Python 3.13.4. Nothing here was inferred from reading
the code.

The `aws` column is honest about this environment: **no AWS credentials were
configured on the machine these were run from**, so every AWS-mode section
reported BLOCKED and exited 2. That is the designed behaviour, and it is not a
pass.

| # | Demo | Imports | Runs | local | integration | aws |
|---|---|---|---|---|---|---|
| 01 | `01_price_check.py` | ✓ | ✓ | **PASS** | n/a | n/a |
| 02 | `02_meal_planning.py` | ✓ | ✓ | **PASS** | n/a | n/a |
| 03 | `03_grounding_and_safety.py` | ✓ | ✓ | **PASS** | n/a | n/a |
| 04 | `04_failure_modes.py` | ✓ | ✓ | **PASS** | n/a | n/a |
| 05 | `05_model_routing.py` | ✓ | ✓ | **PASS** | n/a | n/a |
| 06 | `06_http_api_and_idempotency.py` | ✓ | ✓ | **PASS** | n/a | n/a |
| 07 | `07_observability.py` | ✓ | ✓ | **PASS** | n/a | n/a |
| 08 | `08_mcp_tool_surface.py` | ✓ | ✓ | **PASS** | **PASS** | n/a |
| 09 | `09_ingestion_pipeline.py` | ✓ | ✓ | **PASS** | n/a | BLOCKED — no credentials |
| 10 | `10_dataset_transform.py` | ✓ | ✓ | **PASS** | n/a | n/a |
| 11 | `11_recipe_coverage_gate.py` | ✓ | ✓ | **PASS** | n/a | n/a |
| 12 | `12_location_and_freshness.py` | ✓ | ✓ | **PASS** | n/a | n/a |
| 13 | `13_retrieval_backends.py` | ✓ | ✓ | **PASS** | n/a | BLOCKED — no credentials |
| 14 | `14_bedrock_model_plane.py` | ✓ | ✓ | **PASS** | n/a | BLOCKED — no credentials |
| 15 | `15_deployed_endpoint.py` | ✓ | ✓ | **PASS** | **PASS** | n/a |
| 16 | `16_prompt_and_structured_output.py` | ✓ | ✓ | **PASS** | n/a | n/a |
| 17 | `17_configuration_and_fail_closed.py` | ✓ | ✓ | **PASS** | n/a | BLOCKED — no credentials |
| 18 | `18_evaluation_and_qualification.py` | ✓ | ✓ | **PASS** | n/a | n/a |
| 19 | `19_end_to_end.py` | ✓ | ✓ | **PASS** | **PASS** | BLOCKED — no credentials |

Suite totals:

```
DEMO_MODE=local         19 passed,  0 failed,  0 blocked,  0 skipped   exit 0
DEMO_MODE=integration    3 passed,  0 failed,  0 blocked, 16 skipped   exit 0
DEMO_MODE=aws            0 passed,  0 failed,  5 blocked, 14 skipped   exit 2
```

The integration runs were made against the live
`grocery-orchestrator-api-dev` endpoint and returned real data from
`grocery-products-dev` — capture date 2026-08-28, Bedrock-authored meal names,
warm price checks at 1.6–2.2s and meal plans at ~6s. Those are a handful of
samples and are **not** a latency baseline; `scripts/measure_latency.py` is.

Re-run the matrix at any time:

```bash
python Philip_demo/run_all.py
```

Repository gates, run after this suite was written, both green:

```
ruff check .                   All checks passed
python -m pytest -q            945 passed, 31 skipped
```

*(765 when this suite was first written; the rise is Tasks 14a, 15b and the
curated-recipe suite, not a change to the demos.)*
