"""
What throughput does this account actually allow, and can it be raised?

WHY THIS IS A SCRIPT AND NOT A SENTENCE IN A DOCUMENT

The throughput ceiling was worked out by hand once, written into
docs/THROUGHPUT-AND-SCALING.md, and is wrong the moment a quota moves or the
routing changes. Worse, the reflex answer to a throughput problem -- "ask AWS
to raise the quota" -- is WRONG HERE and looks right: Amazon Nova's
request-per-minute limits are not adjustable, and Anthropic Claude's are. That
was assumed the wrong way round until someone ran one command, after which the
recommendation changed.

So the check is the command. Run it before quoting a throughput figure, before
planning around a quota increase, and after any change to `config/models.json`
routing.

    python scripts/check_quotas.py

Read-only. Needs `servicequotas:ListServiceQuotas`, and nothing else.

WHAT IT DERIVES RATHER THAN ASSUMES

  * which model serves each task            -> the live ModelRegistry
  * how many calls a turn makes per model   -> the task sequence below
  * the per-model request ceiling           -> live Service Quotas
  * whether the binding limit can be raised -> the `Adjustable` flag

The one thing it cannot read is the shape of the graph, so the task sequence
is written down here. If the graph gains or loses a model call, update it --
there is a test that fails if the constant drifts from MAX_REPAIR_ATTEMPTS.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.graph.state import MAX_REPAIR_ATTEMPTS
from src.models.registry import ModelRegistry, UnroutableTask

# The model calls one turn makes, by task, as the graph executes them.
# `repair_plan` is bounded by MAX_REPAIR_ATTEMPTS; the best case is zero.
MEAL_PLAN_TASKS_MIN = ["classify_intent", "generate_plan", "generate_prose"]
MEAL_PLAN_TASKS_MAX = MEAL_PLAN_TASKS_MIN + ["repair_plan"] * MAX_REPAIR_ATTEMPTS
PRICE_CHECK_TASKS = ["classify_intent", "generate_prose"]

# Cross-region, because the configured model ids carry `apac.` / `au.` prefixes
# and therefore resolve through inference profiles. "Global cross-region" and
# "On-demand" are different quotas for calls this service does not make.
QUOTA_PREFIX = "Cross-region model inference requests per minute for "


def fetch_quotas(region: str) -> list[dict]:
    import boto3

    client = boto3.client("service-quotas", region_name=region)
    quotas: list[dict] = []
    paginator = client.get_paginator("list_service_quotas")
    for page in paginator.paginate(ServiceCode="bedrock"):
        quotas.extend(page["Quotas"])
    return quotas


def quota_for(quotas: list[dict], display_name: str) -> dict | None:
    """
    The request-per-minute quota for one model.

    Matched on the display name appearing in the quota name, because AWS
    qualifies it with the provider ("Anthropic Claude Haiku 4.5") and the
    catalogue does not.

    Where several match, the SHORTEST name wins. That is not cosmetic:
    "Claude Sonnet 4.5" also matches "Claude Sonnet 4.5 V1 1M Context Length",
    a different model with a 1/min limit, and taking the smallest value would
    report a ceiling twenty times too low for a model the service never calls.
    """
    matches = [
        q
        for q in quotas
        if q["QuotaName"].startswith(QUOTA_PREFIX)
        and display_name in q["QuotaName"]
    ]
    if not matches:
        return None
    return min(matches, key=lambda q: len(q["QuotaName"]))


def calls_per_model(registry: ModelRegistry, tasks: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for task in tasks:
        try:
            spec = registry.route(task)
        except UnroutableTask:
            continue
        counts[spec.display_name] = counts.get(spec.display_name, 0) + 1
    return counts


def ceiling(counts: dict[str, int], quotas: list[dict]) -> tuple[float, str, bool]:
    """Turns per minute, which model binds it, and whether that can be raised."""
    worst = (float("inf"), "none", True)
    for display_name, calls in counts.items():
        quota = quota_for(quotas, display_name)
        if quota is None:
            continue
        turns = quota["Value"] / calls
        if turns < worst[0]:
            worst = (turns, display_name, bool(quota["Adjustable"]))
    return worst


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="ap-southeast-2")
    args = parser.parse_args()

    registry = ModelRegistry()
    quotas = fetch_quotas(args.region)

    print(f"Bedrock request quotas in {args.region} (cross-region profiles)\n")
    print(f"  {'task':<18} {'model':<22}")
    print(f"  {'-' * 18} {'-' * 22}")
    for task in ("classify_intent", "generate_plan", "repair_plan", "generate_prose"):
        try:
            print(f"  {task:<18} {registry.route(task).display_name:<22}")
        except UnroutableTask as exc:
            print(f"  {task:<18} UNROUTABLE: {exc}")

    print(f"\n  {'model':<22} {'req/min':>8} {'adjustable':>11}")
    print(f"  {'-' * 22} {'-' * 8} {'-' * 11}")
    routed = {
        registry.route(t).display_name
        for t in ("classify_intent", "generate_plan", "repair_plan", "generate_prose")
        if _routable(registry, t)
    }
    for display_name in sorted(routed):
        quota = quota_for(quotas, display_name)
        if quota is None:
            print(f"  {display_name:<22} {'not found':>8} {'-':>11}")
            continue
        flag = "yes" if quota["Adjustable"] else "NO"
        print(f"  {display_name:<22} {quota['Value']:>8.0f} {flag:>11}")

    print("\nturns per minute, service-wide across all users\n")
    for label, tasks in (
        ("meal plan, no repair", MEAL_PLAN_TASKS_MIN),
        (f"meal plan, {MAX_REPAIR_ATTEMPTS} repairs", MEAL_PLAN_TASKS_MAX),
        ("price check", PRICE_CHECK_TASKS),
    ):
        counts = calls_per_model(registry, tasks)
        turns, binding, adjustable = ceiling(counts, quotas)
        detail = ", ".join(f"{m} x{c}" for m, c in sorted(counts.items()))
        print(f"  {label:<24} {turns:>5.1f}/min   bound by {binding}")
        print(f"  {'':<24} calls: {detail}")

    counts = calls_per_model(registry, MEAL_PLAN_TASKS_MIN)
    _, binding, adjustable = ceiling(counts, quotas)
    print()
    if adjustable:
        print(
            f"The binding quota ({binding}) IS adjustable — a Service Quotas\n"
            f"increase request is a real option for lifting this ceiling."
        )
    else:
        print(
            f"The binding quota ({binding}) is NOT adjustable. A quota increase\n"
            f"request cannot lift this ceiling; see docs/THROUGHPUT-AND-SCALING.md\n"
            f"for the two options that can, and what they cost."
        )
    return 0


def _routable(registry: ModelRegistry, task: str) -> bool:
    try:
        registry.route(task)
    except UnroutableTask:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
