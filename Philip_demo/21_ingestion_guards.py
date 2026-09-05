r"""
DEMO 21 - The two refusals that keep invented prices out of a real table
========================================================================

HOW TO RUN
----------
From the repository root:

    python Philip_demo/21_ingestion_guards.py

On Windows without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/21_ingestion_guards.py

No AWS account, credentials or network access.

MODES
-----
    local  (default and only)  the real guard module, driven against a fake
                               DynamoDB table defined in this file. No AWS.

WHAT THIS DEMONSTRATES
----------------------
  1. Why fixture rows in a real table are not duplicates but FABRICATIONS
  2. Layer 1 - a deployment may not DEFAULT to the fixture catalogue
  3. Layer 2 - nothing WRITES fixtures over a table holding the real catalogue
  4. Why neither layer subsumes the other
  5. The probe keys, and the test that stops them silently disarming
  6. Why the refusal was made visible, and what it looked like in production

THE INCIDENT THIS IS ABOUT
--------------------------
The 152-row fixture catalogue was removed from the live products table three
times -- 2026-08-30, 2026-09-01, 2026-09-03 -- and came back every night in
between.

Nobody was re-adding it by hand. The SCHEDULED INGESTION LAMBDA was: it
resolved a FixtureSource from `default_source` in config/data-sources.json,
which is the correct default for a laptop and the wrong one for a deployment,
and wrote the fixtures over the real catalogue at 03:18 every night.

It was silent because it had reached steady state. `added 0, changed 0,
unchanged 152` is exactly what a diff SHOULD report when yesterday's
re-injection is still sitting there, so the one control that could have seen
it correctly reported nothing to see.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from _demo_support import (
    LOCAL,
    ModeUnavailable,
    heading,
    mode_banner,
    note,
    resolve_mode,
    section,
    step,
)

from ingestion.guard import (
    DEPLOYMENT_ENV_VARS,
    REAL_ONLY_STORE_KEYS,
    FixtureGuardError,
    deployment_signal,
    real_catalogue_present,
)
from ingestion.sources import FIXTURES, LINEAGE_B_DIR

try:
    mode = resolve_mode(supports=(LOCAL,))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

heading("DEMO 21 - The two refusals that keep invented prices out of a real table")
mode_banner(
    mode,
    requires="nothing - no AWS account, credentials or network access",
    mocked="DynamoDB (a fake table below); the guard module itself is real",
)


class FakeTable:
    """
    A products table that answers the guard's COUNT-by-store_key probe.

    Models the one query the guard issues. `real_only` is the set of store keys
    this table pretends the collected catalogue occupies.
    """

    def __init__(self, real_only: set[str] | None = None) -> None:
        self._real_only = set(real_only or ())

    def query(self, **kw: Any) -> dict:
        cond = kw.get("KeyConditionExpression")
        values = cond.get_expression()["values"] if cond is not None else []
        store_key = values[-1] if values else None
        return {"Count": 1 if store_key in self._real_only else 0}


# ------------------------------------------------------- 1. why it matters

section("1. Why fixture rows in a real table are not duplicates")

fixture_rows = json.loads(Path(FIXTURES).read_text(encoding="utf-8"))
fixture_keys = {r["product_key"] for r in fixture_rows}
note(
    f"fixtures/products.json: {len(fixture_rows)} hand-written rows, "
    f"{len(fixture_keys)} product keys"
)
note("")
note("The damage is not volume, it is SHADOWING. The synonym table's candidate")
note("ordering falls through to the next catalogue only when a key has no rows,")
note("so a fixture key resolves BEFORE the real one:")
note("")
note("    'milk'  ->  milk-2l            (fixture, invented price)")
note("            ->  standard-milk-2l   (real, never reached)")
note("")
note("Live consequence, recorded in docs/OPEN-REVIEW-near-filter-drift.md:")
note("  'cheapest milk near Albany' answered New World Devonport $4.94 --")
note("  a fabricated price -- while the real Pak'nSAVE Albany $4.79 sat unread.")

# ------------------------------------------------------------- 2. layer one

section("2. Layer 1 - a deployment may not DEFAULT to the fixtures")

note(f"signals that mean 'this is a deployment': {', '.join(DEPLOYMENT_ENV_VARS)}")
note("")
for env, label in (
    ({}, "a laptop, nothing set"),
    ({"APP_STAGE": ""}, "APP_STAGE='' (what an unset var looks like to a deploy tool)"),
    ({"AWS_LAMBDA_FUNCTION_NAME": "grocery-ingestion-dev"}, "inside a Lambda"),
    ({"APP_STAGE": "pilot"}, "a named stage"),
):
    sig = deployment_signal(env)
    verdict = "DEPLOYMENT" if sig else "local"
    note(f"  {label:56} {verdict:10} {sig or ''}")
note("")
note("AWS_LAMBDA_FUNCTION_NAME is listed FIRST and APP_STAGE never alone.")
note("APP_STAGE is unset on the deployed function today -- setting it is the")
note("last step of the production cutover -- so a check keyed on it would have")
note("read as armed in review and done nothing in the account. That is the")
note("exact shape of control this repository keeps finding.")

# ------------------------------------------------------------- 3. layer two

section("3. Layer 2 - nothing WRITES fixtures over the real catalogue")

note(f"probe keys: {', '.join(REAL_ONLY_STORE_KEYS)}")
note("")
note("One Query per probe key with Select=COUNT, stopping at the first hit.")
note("No Scan -- neither the orchestrator nor the ingestion role is granted one.")
note("")
for table, label in (
    (FakeTable(), "an EMPTY table (a first load, nothing to shadow)"),
    (FakeTable({"paknsave#albany"}), "a table holding the real catalogue"),
    (FakeTable({"new_world#albany"}), "the same, found on the second probe"),
):
    found = real_catalogue_present(table)
    verdict = f"REFUSE (found {found})" if found else "allow"
    note(f"  {label:52} {verdict}")

# ---------------------------------------------------- 4. neither subsumes

section("4. Why neither layer subsumes the other")

step(1, "Layer 1 alone would still let an operator shadow the catalogue by")
note("    hand: PRICE_SOURCE=fixtures is explicit, so layer 1 permits it.")
step(2, "Layer 2 alone would leave a deployed Lambda whose DEFAULT behaviour")
note("    is 'serve fixtures', refused only once the real rows are already")
note("    there -- so a fresh table would be seeded with fiction and the guard")
note("    would then protect the fiction.")
note("")
note("Layer 1 is about a default nobody chose. Layer 2 is about a write,")
note("whoever chose it.")

# ------------------------------------------------- 5. keeping probes honest

section("5. The test that stops the probe silently disarming")

note("The probe keys only work while they are BOTH:")
note("  - absent from the fixtures  (or a first load would be refused)")
note("  - present in Lineage B      (or the guard would never fire)")
note("")
fixture_store_keys = {r["store_key"] for r in fixture_rows}
overlap = set(REAL_ONLY_STORE_KEYS) & fixture_store_keys
note(f"probe keys also in the fixtures: {overlap or 'none'}  {'OK' if not overlap else 'BROKEN'}")
note(
    f"Lineage B catalogue present at: {Path(LINEAGE_B_DIR).name}/  "
    f"({len(list(Path(LINEAGE_B_DIR).glob('*.json')))} store files)"
)
note("")
note("tests/test_ingestion.py asserts both against the REAL data, so a")
note("catalogue change that invalidated a probe fails the build rather than")
note("quietly disarming both guards.")

# ------------------------------------------------------ 6. making it visible

section("6. A refusal nobody can see is the failure this repo keeps finding")

note("Raising is not enough. config/ingestion-state-machine.json catches")
note("States.ALL INSIDE the Map's item processor and routes it to a Pass state")
note("-- deliberately, so one broken retailer does not discard the two that")
note("succeeded -- which means a thrown branch produces a SUCCEEDED execution")
note("and AWS/States ExecutionsFailed reports nothing.")
note("")
note("So the Lambda prints a structured line before re-raising, and")
note("grocery-ingestion-refresh-failed-dev alarms on that line instead.")
note("")
note("WATCHED TO FIRE IN THE ACCOUNT on 2026-09-04, not just in tests:")
note("  PRICE_SOURCE removed, one real refresh invoked, variable restored")
note("")
note("  errorType:    FixtureGuardError")
note("  errorMessage: refusing to default to the FIXTURE catalogue in a")
note("                deployment (AWS_LAMBDA_FUNCTION_NAME=grocery-ingestion-dev)")
note("  alarm:        OK -> ALARM at 17:32:04")
note("")
note("Nothing was written: the fixture keys milk-2l and butter-500g stayed at")
note("0 on GSI1 throughout. And the refusal NAMED the signal it acted on --")
note("a check keyed on APP_STAGE would have found it unset on that very")
note("function and let the write through.")

section("The exception type")

note(f"{FixtureGuardError.__name__}: one class for both refusals, deliberately.")
note("They are raised at different layers but they are the same fact --")
note("invented prices about to be treated as collected ones -- and a caller")
note("that wanted to handle one and not the other would be working around")
note("this module.")

print("\nDone.")
