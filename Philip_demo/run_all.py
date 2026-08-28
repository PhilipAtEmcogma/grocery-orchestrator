r"""
Run every demo in order.

HOW TO RUN
----------
    python Philip_demo/run_all.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/run_all.py

To page through it:

    python Philip_demo/run_all.py | more

Each demo also runs on its own - see the docstring at the top of each file.
Every one is offline: fixtures plus the scripted model client, no AWS account,
credentials or network access.

Exits non-zero if any demo fails, so this doubles as a smoke test that the
demos still match the code they describe.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

DEMOS = [
    ("01_price_check.py", "Price checking and comparison"),
    ("02_meal_planning.py", "Meal planning, repair loop, dietary safety"),
    ("03_grounding_and_safety.py", "Grounding and safety guarantees"),
    ("04_failure_modes.py", "Failure modes and retryability"),
    ("05_model_routing.py", "Model routing, registry and cost"),
    ("06_http_api_and_idempotency.py", "HTTP API, contract and idempotency"),
    ("07_observability.py", "Observability"),
]

failures: list[str] = []

for filename, title in DEMOS:
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
    if result.returncode != 0:
        failures.append(filename)

print(f"\n\n{'=' * 74}")
if failures:
    print(f"FAILED: {', '.join(failures)}")
    sys.exit(1)
print(f"All {len(DEMOS)} demos completed successfully.")
