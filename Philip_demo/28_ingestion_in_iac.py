r"""
DEMO 28 - Codifying a plane that was already running
====================================================

HOW TO RUN
----------
From the repository root:

    python Philip_demo/28_ingestion_in_iac.py

On Windows without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/28_ingestion_in_iac.py

No AWS account, credentials or network access. It reads the CDK source and the
config files this repository ships; it does not synthesise or deploy.

MODES
-----
    local  (default and only)  reads infra/ and config/ from disk.

WHAT THIS DEMONSTRATES
----------------------
Pilot Task 13b. The ingestion plane RAN IN THE ACCOUNT from 2026-09-04 with no
template behind it -- the last live plane with none, and the one holding the
only role in the system that can WRITE the serving catalogue.

  1. What "the last plane with no template" actually meant
  2. Built from the ACCOUNT, not from the design doc, which had drifted
  3. What the stack refuses to do, which is the reviewable part
  4. Why the ASL is reused verbatim, and the one rewrite it needs
  5. Why the schedule is created DISABLED
  6. What deploying it proved

WHO THIS IS FOR
---------------
Anyone reviewing infrastructure changes, and anyone who has ever found a
design document disagreeing with production.

EXPECTED RESULT
---------------
Every check prints OK. The one to watch is section 3: the ingestion role's
price-history grant is APPEND-ONLY -- no Query, no Delete, no Update. Exit 0.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from _demo_support import LOCAL, heading, mode_banner, note, require, resolve_mode, section

mode = resolve_mode(supports=(LOCAL,))
mode_banner(mode, requires="nothing", mocked="nothing")

heading("DEMO 28 - The ingestion plane, in IaC at last")

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------- 1. what it meant

section("1. What 'the last plane with no template' meant")

stack = (ROOT / "infra" / "lib" / "ingestion-stack.ts").read_text(encoding="utf-8")
note(f"  infra/lib/ingestion-stack.ts is now {len(stack.splitlines())} lines")
note("  ...and until 2026-09-07 it was a stub with four TODOs.")
note("")
note("Meanwhile, IN THE ACCOUNT, deployed imperatively on 2026-09-04:")
note("    - an ingestion Lambda")
note("    - a Step Functions state machine")
note("    - an EventBridge Scheduler")
note("    - an IAM role that can WRITE the serving catalogue")
note("")
note("None of it reproducible. None of it under review. Everything else in")
note("this project had a template; this did not, and it is the plane that can")
note("change what shoppers are shown.")


# ------------------------------------------- 2. built from the account

section("2. Built from the ACCOUNT, not from the design document")

note("infra/docs/03 sketched this stack in August. Three details had drifted,")
note("and each was corrected against `describe-*` output rather than followed:")
note("")
note("  the doc said            the account said        which won")
note("  ---------------------   ---------------------   ---------")
note("  events.Rule + UTC cron  EventBridge SCHEDULER   the account")
note("                          Pacific/Auckland")
note("  60-second timeout       120                     the account")
note("  no environment block    PRICE_SOURCE=lineage_b  the account")
note("")
note("The Scheduler one is not just a difference, it is BETTER: NZST is UTC+12")
note("and NZDT is UTC+13, so a fixed UTC cron drifts an hour twice a year --")
note("which the doc's own note apologises for. Scheduler removes the problem")
note("instead of noting it.")

for needle, label in [
    ("scheduler.CfnSchedule", "uses EventBridge Scheduler"),
    ("Pacific/Auckland", "with an explicit timezone"),
    ("Duration.seconds(120)", "120-second timeout"),
    ("PRICE_SOURCE: 'lineage_b'", "reads the real catalogue"),
]:
    mark = "OK  " if needle in stack else "FAIL"
    note(f"  [{mark}] {label}")
    require(needle in stack, f"ingestion-stack.ts no longer contains {needle!r}")

note("")
note("infra/docs/03 is deliberately NOT rewritten to match. It records an")
note("INTENTION; the account records the THING. Editing it would destroy the")
note("fact that the design was refined by contact with reality.")


# --------------------------------------------- 3. what it refuses to do

section("3. What the stack refuses to do -- the reviewable part")

iam = json.loads((ROOT / "config" / "iam-ingestion-role.json").read_text(encoding="utf-8"))
statements = {s["Sid"]: s for s in iam["inline_policy"]["Statement"] if "Sid" in s}

for sid, st in statements.items():
    actions = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
    note(f"  {sid}")
    note(f"      {', '.join(actions)}")

history = statements["DynamoAppendPriceHistory"]
history_actions = set(
    history["Action"] if isinstance(history["Action"], list) else [history["Action"]]
)
forbidden = {"dynamodb:Query", "dynamodb:DeleteItem", "dynamodb:UpdateItem", "dynamodb:Scan"}
leaked = history_actions & forbidden
note("")
note(f"  [{'OK  ' if not leaked else 'FAIL'}] the price-history grant is APPEND-ONLY")
require(not leaked, f"history grant leaked {leaked}")

note("")
note("A history row is the BASELINE a future deviation is measured against.")
note("A role that could rewrite one could rewrite the evidence. Query is")
note("absent too: ingestion appends and never reads history back.")
note("")
note("NO `grantWriteData` ANYWHERE. The role is built statement-by-statement")
note("from this JSON, the same file scripts/apply_iam.py applies. The CDK")
note("grant helpers ADD a statement rather than checking one, with the CDK's")
note("idea of 'write' -- which includes DeleteItem and UpdateItem. The service")
note("suite found that exact violation the first time it ran.")

# Comments stripped first, and the reason is itself worth showing: the stack
# DOCUMENTS its refusal in a comment reading "NO tables.products
# .grantWriteData(role)", so a naive grep for the name finds the very sentence
# promising it is absent. A check that cannot tell code from a comment about
# code is the same class of mistake as a substring metric filter.
code = re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", stack, flags=re.S))
for helper in ("grantWriteData", "grantReadWriteData"):
    require(f".{helper}(" not in code, f"the stack CALLS {helper}, which adds an unreviewed grant")
note("  [OK  ] no data-write grant helper is CALLED (comments stripped first)")
note("         ...the stack does mention one, in a comment refusing it")

require("new dynamodb.Table" not in code, "'new dynamodb.Table' not in code")
note("  [OK  ] the stack creates no table - the catalogue is adopted")


# ----------------------------------------------------- 4. the ASL

section("4. Why the state machine definition is reused verbatim")

asl = json.loads((ROOT / "config" / "ingestion-state-machine.json").read_text(encoding="utf-8"))
catch = asl["States"]["RefreshAllRetailers"]["ItemProcessor"]["States"]["RefreshOneRetailer"][
    "Catch"
][0]

note("Its comments are load-bearing. Two record real defects:")
note("")
note(f"  ResultPath = {catch['ResultPath']!r}")
note("    ...because Map items here are STRINGS, and a ResultPath on a")
note("    non-object raises States.ResultPathMatchFailure -- which aborts the")
note("    very Map the Catch exists to protect.")
note("")
retry = asl["States"]["RefreshAllRetailers"]["ItemProcessor"]["States"]["RefreshOneRetailer"][
    "Retry"
][0]
note(f"  Retry covers {len(retry['ErrorEquals'])} TRANSIENT Lambda errors only")
note("    ...so a ValueError from an unknown retailer fails fast rather than")
note("    being retried three times.")
note("")
note("The L2 stepfunctions-tasks rebuild would give type-checked retries and")
note("silently drop every one of those comments.")
note("")
note("THE ONE REWRITE is the function name. The ASL names")
note("`grocery-ingestion-dev` literally, so without it the CDK state machine")
note("would invoke the HAND-MADE Lambda -- two planes that look independent")
note("while sharing the half that writes to the catalogue.")

require(
    "new RegExp" in stack and "cfg.suffix" in stack,
    "'new RegExp' in stack and 'cfg.suffix' in stack",
)
note("  [OK  ] the stack rewrites the function name")

note("")
note("A SECOND THING THE ASL NEEDED, found by a failed deploy: Step Functions")
note("validates a definition at CREATE time, not at synth. `cdk synth`")
note("rendered it happily and CloudFormation answered:")
note("")
note("    SCHEMA_VALIDATION_FAILED: Field '_comment' is not supported")
note("    at .../RefreshOneRetailer/Catch[0]")
note("")
note("scripts/apply_state_machine.py had stripped comments since it was")
note("written, which is why the hand-made plane deployed fine. The CDK path")
note("passed the file through raw -- so TWO MECHANISMS READ ONE CONFIG FILE")
note("AND APPLIED DIFFERENT TRANSFORMS TO IT.")
require("stripComments" in stack, "'stripComments' in stack")
note("  [OK  ] the stack now mirrors strip_comments()")


# ------------------------------------------------- 5. the schedule

section("5. Why the schedule is created DISABLED")

sources = json.loads((ROOT / "config" / "data-sources.json").read_text(encoding="utf-8"))
note("  LineageBSource.CAPTURED_AT is the constant '2026-08-28'")
note("  the dataset is documented as a ONE-OFF snapshot")
note("")
note("So a nightly run rewrites the same 2,759 rows with the same capture")
note("date. It would cost money, write to the serving catalogue every night,")
note("and change nothing.")
note("")
note("IT ALSO CLOSES A DRIFT. The 2026-08-30 account audit recorded the")
note("hand-made schedule as ENABLED. It is DISABLED in the account today, and")
note("nobody wrote down the change. A schedule whose state lives only in a")
note("console can flip without review -- in either direction, and the")
note("dangerous direction writes to the catalogue.")
note("")
note("  INGESTION_SCHEDULE=1 makes enabling it a reviewed edit.")
require("ingestionScheduleEnabled" in stack, "'ingestionScheduleEnabled' in stack")
note("  [OK  ] the state is config-driven, not hardcoded")
require(
    "_why_the_weekly_refresh" in json.dumps(sources)
    or "_what_the_weekly_refresh_is_actually_for" in sources,
    "'_why_the_weekly_refresh' in json.dumps(sources) or '_what_the_weekly_",
)
note("  [OK  ] config/data-sources.json records the reasoning")


# ---------------------------------------------- 6. what deploying proved

section("6. What deploying it proved")

note("A dry run of the CDK function against the real catalogue:")
note("")
note('    {"retailer":"paknsave","fetched":1377,"rejected":0,')
note('     "added":0,"changed":0,"unchanged":1377,"captured_at":"2026-08-28"}')
note("")
note("Three things at once:")
note("  - PRICE_SOURCE=lineage_b reaches the COLLECTED catalogue, not fixtures")
note("  - the Query grant works, because 'unchanged: 1377' is only knowable")
note("    by diffing against the live table")
note("  - the anomaly rule ran clean over all of them")
note("")
note("And the state machine end to end, started with retailers=['woolworths']")
note("-- chosen because the dataset has NO Woolworths rows, so the full path")
note("could be proved with ZERO writes to the serving catalogue:")
note("")
note('    Status: SUCCEEDED   Output: [{"retailer":"woolworths","fetched":0}]')
note("")
note("Which also re-confirms the two-chain coverage gap as a live number")
note("rather than a claim in a review document.")

print("\nDone.")
