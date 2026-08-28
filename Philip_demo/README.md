# Philip_demo

Runnable demonstrations of the grocery orchestrator, without a UI.

Every file runs **offline** — `fixtures/products.json` plus the scripted model
client. No AWS account, credentials, or network access. That is a property of
the project rather than a concession made for the demos: the orchestrator
depends on protocol boundaries (`PriceRepository`, `ModelClient`) with fixture
implementations behind them, so the whole graph runs on a laptop.

## Running

From the repository root:

```bash
python Philip_demo/run_all.py        # everything, in order
python Philip_demo/01_price_check.py # or one at a time
```

On Windows without activating the virtualenv:

```
.venv\Scripts\python.exe Philip_demo/run_all.py
```

`run_all.py` exits non-zero if any demo fails, so it doubles as a smoke test
that the demos still match the code they describe.

Full instructions are in the docstring at the top of each file.

## The files

| File | Feature area |
|---|---|
| `01_price_check.py` | Term resolution, cross-store comparison, multi-item turns, honest gaps for missing data, the per-turn lookup cap |
| `02_meal_planning.py` | Budgeted plans, Python-computed arithmetic, the bounded repair loop, dietary exclusions checked against retrieved products, per-store baskets |
| `03_grounding_and_safety.py` | Why a hallucinated price is unrepresentable, prose degradation, the two contract assertions, hallucinated citation refs, prompt-injection fencing, graph topology, guardrail tagging |
| `04_failure_modes.py` | Every terminal error path and why each says something true — `NO_DATA`, `UNSUPPORTED_EXCLUSION`, `BUDGET_INFEASIBLE`, `PLAN_GENERATION_FAILED`, upstream failures — and what retryability means |
| `05_model_routing.py` | The catalogue as data, per-task routing, tiers and capabilities, pinning for evals, cost per turn, unroutable tasks failing loudly |
| `06_http_api_and_idempotency.py` | The real Lambda handler over API Gateway events, status codes, malformed input, idempotent replay, payload-mismatch conflicts, the published samples |
| `07_observability.py` | Per-turn stats, latency attribution, instrumentation as wrappers, what is deliberately not logged, EMF metrics |

`_demo_support.py` holds shared printing helpers so each demo stays about its
feature rather than about formatting. It is not itself a demo.

## Suggested order for a walkthrough

1 → 2 shows what the system **does**. 3 → 4 shows what it **guarantees**, and
what it says when it cannot. 5 → 7 is the operational layer: routing and cost,
the deployed surface, and what an operator sees.

If you only have time for one, read `03_grounding_and_safety.py` — the central
design decision is that the model never produces a number that reaches the
user, and that file is where you can watch it hold.

## A note on the noisy JSON

Demo 6 prints structured log lines and EMF metric records mid-run. That is not
debug output left in by accident: the dev server and the test suite go through
the same instrumented handler Lambda does, and those lines are exactly what
CloudWatch ingests in production. An observability layer that only runs in
production is one nobody has tested. Demo 7 explains what they contain.
