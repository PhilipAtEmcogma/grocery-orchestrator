r"""
DEMO 7 - Observability: what an operator sees
=============================================

HOW TO RUN
----------
    python Philip_demo/07_observability.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/07_observability.py

Offline. Structured logs and EMF metric records print to stdout, which is
exactly where CloudWatch reads them from in production. X-Ray switches itself
off outside Lambda, so no daemon is needed.

MODES
-----
    local  (default and only)  structured logs and EMF records print to stdout, which
                               is exactly where CloudWatch reads them in
                               production. X-Ray switches itself off outside
                               Lambda, so no daemon is needed.

    Asking for another mode exits without running anything, rather than
    quietly answering from fixtures. See Philip_demo/README.md.

WHAT THIS DEMONSTRATES
----------------------
  1. Per-turn stats: model calls, tokens, latency split, repair attempts
  2. Instrumented wrappers - telemetry added without touching the graph
  3. Why the counters are per-turn rather than global
  4. What is deliberately NOT logged
  5. The EMF metric records the handler emits
  6. Latency attribution: model time vs retrieval time

THE POINT
---------
An observability layer that only runs in production is an observability layer
nobody has tested. The dev server and the test suite go through the same
instrumented path Lambda does, which is why the noisy JSON below appears at
all when you run a local demo.
"""

from __future__ import annotations

import json

from _demo_support import (
    LOCAL,
    ModeUnavailable,
    heading,
    mode_banner,
    request,
    resolve_mode,
    section,
)

from src.models.scripted import ScriptedModelClient
from src.observability.base import NULL_TELEMETRY, TurnStats, request_fields
from src.observability.instrumented import (
    InstrumentedModelClient,
    InstrumentedPriceRepository,
)
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn

try:
    mode = resolve_mode(supports=(LOCAL,))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

heading("DEMO 7 - Observability")
mode_banner(
    mode,
    requires="nothing - no AWS account, credentials or network access",
    mocked="the price store (fixtures) and the model plane (ScriptedModelClient)",
)

# ------------------------------------------------------------ a plan turn
section("1. One meal-plan turn, instrumented")
stats = TurnStats()
repo = InstrumentedPriceRepository(InMemoryPriceRepository(), NULL_TELEMETRY, stats)
model = InstrumentedModelClient(ScriptedModelClient(), NULL_TELEMETRY, stats)

resp = run_turn(
    request("feed 3 people for a week on $70", household_size=3, budget_nzd=70, days=5),
    repo,
    model,
)

print(f"  model calls        {stats.model_calls}")
print(f"  models used        {stats.models_used}")
print(f"  plan calls         {stats.plan_calls}  (is_plan_turn={stats.is_plan_turn})")
print(f"  repair attempts    {stats.repair_attempts}")
print(f"  retrieval calls    {stats.retrieval_calls}")
print(f"  input tokens       {stats.input_tokens}")
print(f"  output tokens      {stats.output_tokens}")
print(f"  guardrail fired    {stats.guardrail_intervened}")

# ------------------------------------------------------ latency attribution
section("2. Latency attribution")
print(f"  model time         {stats.model_ms:.3f} ms")
print(f"  of which plan      {stats.plan_ms:.3f} ms")
print(f"  retrieval time     {stats.retrieval_ms:.3f} ms")
print("\n  Durations are FLOATS on purpose. A fixture lookup and a DynamoDB")
print("  query both round to zero as integer milliseconds, and a latency")
print("  breakdown whose fast half reads as 0 cannot attribute anything.")

# ------------------------------------------------------- per-turn, not global
section("3. Per-turn, never global")
second = TurnStats()
repo2 = InstrumentedPriceRepository(InMemoryPriceRepository(), NULL_TELEMETRY, second)
model2 = InstrumentedModelClient(ScriptedModelClient(), NULL_TELEMETRY, second)
run_turn(request("cheapest milk", turn="turn-obs02"), repo2, model2)

print(f"  turn 1 model calls: {stats.model_calls}")
print(f"  turn 2 model calls: {second.model_calls}")
print("\n  A Lambda execution environment serves many turns. A counter that")
print("  survived between them would report the wrong number from the second")
print("  invocation onwards - the same reasoning that makes clear_state=True")
print("  mandatory on the logger.")

# ------------------------------------------------------ instrumentation shape
section("4. Telemetry is a wrapper, not a change to the graph")
print("  InstrumentedPriceRepository and InstrumentedModelClient implement the")
print("  same protocols as the things they wrap, so the graph cannot tell the")
print("  difference and no node contains a metrics call.\n")
print(f"  repo  type: {type(repo).__name__}")
print(f"  inner type: {type(repo._inner).__name__}")
print(f"  same table_name reported: {repo.table_name!r}")
print("\n  One span per retrieval call rather than one per turn: a price check")
print("  resolves each item separately, and 'which lookup was slow' is a")
print("  question the per-turn aggregate cannot answer.")

# ---------------------------------------------------------- what is logged
section("5. What gets logged about a request")
fields = request_fields(request("cheapest butter for my flat", turn="turn-obs03", household_size=3))
print(f"  {json.dumps(fields, indent=2, default=str)}")
print("\n  Note what is ABSENT: the user's message text. Counts, lengths and")
print("  ids only. The session id is the correlation key, and the message")
print("  itself is the one field most likely to carry something personal.")

# --------------------------------------------------------------- the metrics
section("6. The EMF records")
print("  Running any handler demo prints lines like the ones above the")
print("  section headings in 06_http_api_and_idempotency.py:\n")
print('    {"_aws": {"CloudWatchMetrics": [...]}, "TurnsProcessed": [1.0],')
print('     "InvalidRequest": [1.0], ...}')
print("\n  That IS the metric. CloudWatch parses EMF straight out of the log")
print("  stream, so there is no separate PutMetricData call to fail, and the")
print("  metric cannot silently diverge from the log line beside it.")
print("\n  config/alarms.json defines what fires on these, and")
print("  tests/test_alarms.py checks each alarm's filter pattern against the")
print("  log line the handler really emits - an alarm watching a metric no")
print("  filter publishes deploys clean and reads as a healthy service.")
print("\nDone.")
