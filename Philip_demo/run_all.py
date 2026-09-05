r"""
Run every demo in order, in one mode.

HOW TO RUN
----------
    python Philip_demo/run_all.py                     # local: offline, free
    DEMO_MODE=integration python Philip_demo/run_all.py
    DEMO_MODE=aws python Philip_demo/run_all.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/run_all.py

To page through it:

    python Philip_demo/run_all.py | more

Each demo also runs on its own - see the docstring at the top of each file.

MODES
-----
`DEMO_MODE` selects one, and every demo that SUPPORTS it runs in it. Demos
that do not support the selected mode are reported as SKIPPED and are not
counted as passing. The default is `local`: fixtures plus the scripted model
client, with no AWS account, credentials or network access, and no spend.

    local        (default)  offline. ALL 19 demos run here, and pass.
    integration             the deployed HTTPS endpoint, and the MCP server
                            over a real stdio subprocess. Needs network
                            access; needs NO AWS credentials. Costs a few
                            cents of Bedrock and Lambda.
    aws                     the deployed AWS resources through boto3 -
                            DynamoDB, Lambda configuration, and Bedrock where
                            a demo asks for it. Needs credentials.

EXIT CODES, AND WHY THERE ARE THREE
-----------------------------------
    0   every demo that ran, passed
    1   a demo FAILED - it broke, and that is a defect
    2   a demo was BLOCKED - it asked for a dependency this environment does
        not have (no credentials, no network). Nothing broke, and nothing was
        proven either.

A blocked demo has NOT passed, and this script will not say that it did. That
distinction is the whole reason the mode banner exists: "this ran" and "this
reached AWS" are different claims.

Exits non-zero on either, so `run_all.py` in local mode doubles as a smoke
test that the demos still match the code they describe.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

LOCAL, INTEGRATION, AWS = "local", "integration", "aws"

#: filename, title, modes the demo implements.
DEMOS: list[tuple[str, str, tuple[str, ...]]] = [
    ("01_price_check.py", "Price checking and comparison", (LOCAL,)),
    ("02_meal_planning.py", "Meal planning, repair loop, dietary safety", (LOCAL,)),
    ("03_grounding_and_safety.py", "Grounding and safety guarantees", (LOCAL,)),
    ("04_failure_modes.py", "Failure modes and retryability", (LOCAL,)),
    ("05_model_routing.py", "Model routing, registry and cost", (LOCAL,)),
    ("06_http_api_and_idempotency.py", "HTTP API, contract and idempotency", (LOCAL,)),
    ("07_observability.py", "Observability", (LOCAL,)),
    ("08_mcp_tool_surface.py", "The MCP facade", (LOCAL, INTEGRATION)),
    ("09_ingestion_pipeline.py", "The ingestion pipeline", (LOCAL, AWS)),
    ("10_dataset_transform.py", "Lineage B -> Lineage A transform", (LOCAL,)),
    ("11_recipe_coverage_gate.py", "The recipe catalogue and its gate", (LOCAL,)),
    ("12_location_and_freshness.py", "Location scoping and price freshness", (LOCAL,)),
    ("13_retrieval_backends.py", "The retrieval boundary", (LOCAL, AWS)),
    ("14_bedrock_model_plane.py", "The Bedrock model plane", (LOCAL, AWS)),
    ("15_deployed_endpoint.py", "The deployed service over HTTPS", (LOCAL, INTEGRATION)),
    ("16_prompt_and_structured_output.py", "The AI request pipeline", (LOCAL,)),
    ("17_configuration_and_fail_closed.py", "Configuration as a failure mode", (LOCAL, AWS)),
    ("18_evaluation_and_qualification.py", "Evaluation and the qualification gate", (LOCAL,)),
    ("19_end_to_end.py", "End to end, through every layer", (LOCAL, AWS, INTEGRATION)),
    ("20_recipe_selection.py", "Recipe selection: the half before the model", (LOCAL,)),
    ("21_ingestion_guards.py", "The two refusals that guard the catalogue", (LOCAL,)),
    ("22_price_history_and_review.py", "Price history and the data-quality reviewer", (LOCAL,)),
    ("23_degradation_and_throttling.py", "Degradation when the model is unreachable", (LOCAL,)),
    # Last, and deliberately: it binds a port and starts a subprocess, so a
    # failure here is about the environment rather than about the orchestrator.
    (
        "24_backend_without_a_frontend.py",
        "The whole backend, running, no frontend",
        (LOCAL, INTEGRATION),
    ),
]

BLOCKED_EXIT = 2

mode = os.environ.get("DEMO_MODE", "").strip().lower() or LOCAL
if mode not in (LOCAL, INTEGRATION, AWS):
    print(f"DEMO_MODE={mode!r} is not one of: {LOCAL}, {INTEGRATION}, {AWS}")
    raise SystemExit(1)

results: list[tuple[str, str]] = []

print(f"{'=' * 74}")
print(f"Philip_demo  --  running every demo that supports DEMO_MODE={mode}")
print(f"{'=' * 74}")

for filename, title, supported in DEMOS:
    if mode not in supported:
        results.append((filename, "SKIPPED"))
        print(f"\n{'#' * 74}")
        print(f"# {filename}  --  {title}")
        print(f"# SKIPPED: implements {', '.join(supported)}, not {mode}")
        print(f"{'#' * 74}")
        continue

    print(f"\n\n{'#' * 74}")
    print(f"# {filename}  --  {title}")
    print(f"{'#' * 74}")
    sys.stdout.flush()

    # S603: the argument vector is this interpreter plus a filename from the
    # hardcoded DEMOS list above. No shell, and nothing here comes from user
    # input or the environment.
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(HERE / filename)],
        cwd=HERE.parent,
        check=False,
    )
    if result.returncode == 0:
        results.append((filename, "PASS"))
    elif result.returncode == BLOCKED_EXIT:
        results.append((filename, "BLOCKED"))
    else:
        results.append((filename, "FAIL"))

print(f"\n\n{'=' * 74}")
print(f"RESULTS  --  DEMO_MODE={mode}")
print(f"{'=' * 74}")
for filename, status in results:
    print(f"  {status:<8} {filename}")

counts = {
    status: sum(1 for _, s in results if s == status)
    for status in ("PASS", "FAIL", "BLOCKED", "SKIPPED")
}
print(
    f"\n  {counts['PASS']} passed, {counts['FAIL']} failed, "
    f"{counts['BLOCKED']} blocked, {counts['SKIPPED']} skipped "
    f"(not applicable to this mode)"
)

if counts["FAIL"]:
    print("\n  A FAIL is a defect: the demo broke.")
    raise SystemExit(1)
if counts["BLOCKED"]:
    print("\n  A BLOCKED demo asked for a dependency this environment does not")
    print("  have. Nothing broke, and nothing was proven. It has NOT passed.")
    raise SystemExit(BLOCKED_EXIT)
print("\n  Everything that ran, passed.")
