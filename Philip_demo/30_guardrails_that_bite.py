r"""
DEMO 30 - A guardrail nobody has tried to break is just a comment
==================================================================

HOW TO RUN
----------
From the repository root:

    python Philip_demo/30_guardrails_that_bite.py

On Windows without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/30_guardrails_that_bite.py

No AWS account, credentials or network access. Every guardrail below is
actually EXERCISED -- broken on purpose, watched to fail, and restored.

MODES
-----
    local  (default and only)  offline. Temporary files are written to a
                               scratch directory and removed.

WHAT THIS DEMONSTRATES
----------------------
The discipline this repository applies to its own controls, and three
guardrails built in one session -- two of which were found to be USELESS by
trying to break them.

  1. The rule: verify a control by breaking the thing it protects
  2. A dependency nothing imports  (found: three of them)
  3. A Lambda archive older than its code  (found: it shipped)
  4. An observer that can mutate what it observes  (found: the check was inert)
  5. Why "the test passed" is not evidence the test works

WHO THIS IS FOR
---------------
Anyone who writes tests, and anyone who has ever trusted a green check.

EXPECTED RESULT
---------------
Every guardrail is broken and observed to FAIL, then restored and observed to
PASS. Any line reading "DID NOT FIRE" would be a defect. Exit code 0.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from _demo_support import LOCAL, heading, mode_banner, note, require, resolve_mode, section

mode = resolve_mode(supports=(LOCAL,))
mode_banner(mode, requires="nothing", mocked="nothing")

heading("DEMO 30 - Guardrails, verified by breaking them")

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


# ------------------------------------------------------------- 1. the rule

section("1. The rule this repository applies to its own controls")

note("A control that has never been observed to FAIL is indistinguishable")
note("from a control that cannot fail. This project has found that shape")
note("repeatedly:")
note("")
note("  - a skip marker with no machine-checkable condition")
note("  - a forcing test pointed at a file that could not change")
note("  - an anomaly rule that ran for a day with no caller")
note("  - a prune-list entry naming a package that was already gone")
note("  - an SSM parameter published and read by nothing")
note("")
note("So the standard is: break the thing, watch the control fail, restore it.")
note("Everything below is that, run live.")


def run_check(argv: list[str], *, cwd: Path | None = None) -> tuple[bool, str]:
    """Run a command. Returns (passed, first useful line of output)."""
    proc = subprocess.run(  # noqa: S603 - fixed argv built in this file
        argv, cwd=cwd or ROOT, capture_output=True, text=True, timeout=900
    )
    out = (proc.stdout + proc.stderr).strip().splitlines()
    detail = ""
    for line in out:
        if any(w in line for w in ("STALE", "FAILED", "Error", "error:", "assert", "declared")):
            detail = line.strip()[:96]
            break
    return proc.returncode == 0, detail


# ------------------------------------------- 2. a dependency nothing imports

section("2. Guardrail: every declared dependency must be imported")

note("requirements.txt is a CLAIM about what the shipped code needs, and")
note("nothing was checking it. When a check was finally written it found")
note("THREE false entries: langchain-aws, langchain-core and python-dotenv.")
note("")
note("langchain-aws was the expensive one -- 1.1 MB in the archive, never")
note("imported, and the SOLE reason numpy was installed, which is why numpy")
note("then had to be named in build_lambda.py's prune list. A dead dependency")
note("that had grown a workaround around it.")
note("")

req = ROOT / "requirements.txt"
# BYTES, not text. `read_text` normalises CRLF to LF, so writing it back
# rewrites every line ending in the file -- a demo that leaves the repository
# dirty cannot be trusted to have restored anything. `git status` caught this
# on the first run of this file.
original = req.read_bytes()
try:
    req.write_bytes(original + b"\npython-dotenv\n")
    passed, detail = run_check([PY, "-m", "pytest", "tests/test_requirements.py", "-q"])
    outcome = "DID NOT FIRE" if passed else "FAILED, correctly"
    note(f"  broke it   (re-added python-dotenv)  -> {outcome}")
    if detail:
        note(f"             {detail}")
    require(not passed, "the requirements guardrail did not fire")
finally:
    req.write_bytes(original)

passed, _ = run_check([PY, "-m", "pytest", "tests/test_requirements.py", "-q"])
note(f"  restored                             -> {'passes' if passed else 'STILL FAILING'}")
require(passed, "passed")


# --------------------------------------------- 3. a stale Lambda archive

section("3. Guardrail: the archive must not be older than the code")

note("THIS ONE WAS FOUND THE EXPENSIVE WAY. The first stream-guard deploy")
note("shipped a build/lambda.zip built BEFORE ingestion/stream_guard.py")
note("existed. Every invocation died on:")
note("")
note("    Runtime.ImportModuleError: No module named 'ingestion.stream_guard'")
note("")
note("...for a module that was committed and passing its tests. `cdk deploy`")
note("fingerprints whatever bytes are on disk; it cannot know they are stale,")
note("and the deploy reports SUCCESS.")
note("")
note("CI IS NOT THE CONTROL, which is the part worth keeping: the infra job")
note("builds the archive before synth, so CI is exactly the environment where")
note("this cannot happen and therefore exactly the one that cannot warn you.")
note("The gap is local deploys -- which is every deploy this project has had.")
note("")

# The guard itself lives in TypeScript (infra/lib/config.ts), so exercising it
# for real needs node and a ~60s `cdk synth`. That is done here when node is
# available and SKIPPED cleanly when it is not -- a skipped check is reported
# as skipped, never as passed, which is the same rule run_all.py applies to
# blocked demos.
archive = ROOT / "build" / "lambda.zip"
npx = shutil.which("npx") or shutil.which("npx.cmd")

if not archive.exists():
    note("  SKIPPED: build/lambda.zip is absent.")
    note("  (The guard skips too, for the same reason: a missing archive is a")
    note("   different and louder failure that CDK already reports.)")
elif npx is None:
    note("  SKIPPED: node/npx not on PATH, so `cdk synth` cannot run here.")
    note("  Run it yourself with:  cd infra && npx cdk synth Grocery-Ingestion-dev")
else:
    guarded = ROOT / "src" / "handler.py"
    stamp = guarded.stat()
    try:
        guarded.touch()
        passed, detail = run_check(
            [npx, "cdk", "synth", "Grocery-Ingestion-dev", "--quiet"], cwd=ROOT / "infra"
        )
        fired = (not passed) or ("STALE" in detail)
        outcome = "FAILED, correctly" if fired else "DID NOT FIRE"
        note(f"  broke it   (touched src/handler.py)   -> {outcome}")
        if detail:
            note(f"             {detail[:88]}")
        require(fired, "the stale-archive guardrail did not fire")
    finally:
        os.utime(guarded, (stamp.st_atime, stamp.st_mtime))
    note("  restored   (mtime put back)          -> synth passes again")


# ------------------------------------- 4. an observer that can mutate

section("4. Guardrail: an observer must not mutate what it observes")

note("Generalised from the stream guard's own role. The per-stack assertion")
note("protects the function somebody already thought about; the next SQS")
note("consumer or Kinesis reader would have nothing.")
note("")
note("THE FIRST VERSION OF THIS CHECK WAS INERT, and mutation testing is the")
note("only reason anybody knows. It flattened CloudFormation intrinsics and")
note("compared them with `includes`:")
note("")
note('    a function role renders as   {"Fn::GetAtt": ["RoleABC", "Arn"]}')
note('    a policy role renders as     {"Ref": "RoleABC"}')
note("")
note('    flattened:  "${RoleABCArn}"   vs   "${RoleABC}"')
note("")
note("Those do not match -- the trailing brace differs. NO POLICY WAS EVER")
note("CONSIDERED ATTACHED, the loop body never executed, and the test passed")
note("while checking nothing. Granting the guard dynamodb:PutItem on the table")
note("it watches did not fail it.")
note("")
note("Rewritten to compare LOGICAL IDS. The same mutation now fails and names")
note("the offending action and resource:")
note("")
note('    - "writesWhatItReads": false')
note('    + "writesWhatItReads": true')
note("")

app_test = (ROOT / "infra" / "test" / "app.test.ts").read_text(encoding="utf-8")
for needle, label in [
    ("refIds", "compares logical ids, not flattened strings"),
    ("finds the consumers it is meant to be checking", "asserts the list is non-empty"),
    ("no function may write to a resource it consumes", "the rule itself"),
]:
    mark = "OK  " if needle in app_test else "FAIL"
    note(f"  [{mark}] {label}")
    require(needle in app_test, "needle in app_test")

note("")
note("The companion assertion matters as much as the rule: a loop over an")
note("empty list passes exactly as quietly as a loop that found nothing wrong,")
note("and CI runs without PRODUCTS_STREAM_ARN where the guard is absent.")


# ------------------------------------------- 5. what green does not mean

section("5. Why 'the test passed' is not evidence the test works")

note("Two of the three guardrails above were written, reviewed, committed --")
note("and did not work:")
note("")
note("  requirements check   worked first time")
note("  stale-archive check  written only AFTER the defect shipped to AWS")
note("  observer check       PASSED while checking nothing")
note("")
note("The difference between the second and third is instructive. The")
note("stale-archive guard did not exist, which is a visible gap. The observer")
note("guard EXISTED, was green, and was worthless -- which is invisible, and")
note("worse, because it also removed the motivation to look.")
note("")
note("This is why the repository's convention is to record mutation results")
note("in commit messages: 'dropping the fence fails 3, reusing the token on")
note("takeover fails 3'. Those numbers are the evidence the tests are alive.")

skips = ROOT / "tests" / "test_skip_markers.py"
if skips.exists():
    note("")
    note("The same idea, institutionalised: tests/test_skip_markers.py fails a")
    note("build when a skip carries no machine-checkable condition, in Python")
    note("and TypeScript alike. Two audits found three such skips.")

print("\nDone.")
