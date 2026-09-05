r"""
DEMO 5 - Model routing, the registry, and cost
==============================================

HOW TO RUN
----------
    python Philip_demo/05_model_routing.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/05_model_routing.py

MODES
-----
    local  (default and only)  reads config/models.json and routes against it. No
                               Bedrock call is made, so no credentials are
                               needed. Demo 14 is the one that calls it.

    Asking for another mode exits without running anything, rather than
    quietly answering from fixtures. See Philip_demo/README.md.

WHAT THIS DEMONSTRATES
----------------------
  1. The model catalogue is DATA (config/models.json), not code
  2. Per-task routing: cheap models for cheap work, quality where it counts
  3. Tiers, capabilities, and what happens when a model lacks tool use
  4. Pinning a specific model - how the eval harness scores one at a time
  5. Cost estimation per model, from the same config
  6. A task that cannot be routed fails loudly rather than silently

WHY THE CATALOGUE IS DATA
-------------------------
Model ids and pricing change faster than releases; under IaC this file becomes
an SSM parameter the stack writes, so operators can retune routing without a
deploy; and adding a model becomes a config change a non-engineer can review.
"""

from __future__ import annotations

from _demo_support import LOCAL, ModeUnavailable, heading, mode_banner, resolve_mode, section

from src.models.base import ModelTier
from src.models.registry import ModelRegistry, RoutingPolicy, UnroutableTask

registry = ModelRegistry()

try:
    mode = resolve_mode(supports=(LOCAL,))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

heading("DEMO 5 - Model routing, the registry, and cost")
mode_banner(
    mode,
    requires="nothing - config/models.json is read from disk",
    mocked="nothing. No model is called at all; this is the routing decision alone.",
)

# ------------------------------------------------------------- the catalogue
section("1. The catalogue")
print(f"  {'key':<18} {'display name':<24} {'tiers':<18} enabled")
print(f"  {'-' * 18} {'-' * 24} {'-' * 18} -------")
for spec in registry.all_specs():
    tiers = ",".join(t.value for t in spec.tiers)
    print(f"  {spec.key:<18} {spec.display_name:<24} {tiers:<18} {spec.enabled}")

print("\n  Loaded from config/models.json. Verify ids against reality with:")
print("    aws bedrock list-foundation-models --region ap-southeast-2")

# ---------------------------------------------------------------- routing
section("2. Per-task routing")
print("  Different work deserves different models. Explanatory prose does not")
print("  need the expensive one; a meal plan does.\n")
for task in registry.tasks:
    try:
        spec = registry.route(task)
        print(f"  {task:<18} -> {spec.display_name} ({spec.key})")
    except UnroutableTask as exc:
        print(f"  {task:<18} -> UNROUTABLE: {exc}")

print("\n  The full routing table as the registry sees it:\n")
for line in registry.explain_routing().splitlines():
    print(f"    {line}")

# ------------------------------------------------------------ capabilities
section("3. Capabilities decide HOW structured output is obtained")
for spec in registry.all_specs():
    caps = spec.capabilities
    method = "forced tool call" if caps.tool_use else "schema in prompt, parse reply"
    print(f"  {spec.display_name:<24} tool_use={caps.tool_use!s:<6} -> {method}")

print("\n  Tool use is preferred: forcing a tool call means the model cannot")
print("  prepend 'Sure, here's the JSON:' and break parsing. Models without")
print("  it take a genuinely weaker path, which is why the eval harness")
print("  exists to measure the difference rather than assume it is fine.")

# ------------------------------------------------------------------ tiers
section("4. Tiers")
for tier in (ModelTier.FAST, ModelTier.QUALITY):
    available = registry.available(tier)
    names = ", ".join(s.display_name for s in available) or "(none configured)"
    print(f"  {tier.value:<10} {names}")

# ---------------------------------------------------------------- pinning
section("5. Pinning one model")
print("  How the eval harness scores a single model rather than the route:\n")
for key in ("claude-haiku", "claude-sonnet", "nova-pro"):
    spec = registry.route("generate_plan", policy=RoutingPolicy.PINNED, pinned_key=key)
    print(f"    pinned {key:<16} -> {spec.model_id}")
print("\n  This is what `--model` and `--compare` do in evals/run_meal_plan.py.")

# ------------------------------------------------------------------- cost
section("6. Cost, from the same config")
print("  Estimated cost of one plan turn at 4,000 input / 800 output tokens:\n")
print(f"  {'model':<24} {'in $/1k':<10} {'out $/1k':<10} turn cost")
print(f"  {'-' * 24} {'-' * 10} {'-' * 10} ---------")
for spec in registry.all_specs():
    if not spec.enabled:
        continue
    cost = spec.cost_for(4000, 800)
    print(
        f"  {spec.display_name:<24} {spec.input_cost_per_1k!s:<10} "
        f"{spec.output_cost_per_1k!s:<10} ${cost}"
    )
print("\n  Which is why routing matters: the cheapest and dearest configured")
print("  models differ by more than an order of magnitude per turn, and most")
print("  turns do not need the dearest one.")

# ------------------------------------------------------------- failing loud
section("7. An unroutable task fails loudly")
try:
    registry.route("summarise_the_news")
    print("  ...routed, which would be wrong.")
except UnroutableTask as exc:
    print(f"  UnroutableTask: {exc}")
print("\n  Silently falling back to 'some model' would make a routing typo")
print("  invisible until the bill or the eval scores moved.")
print("\nDone.")
