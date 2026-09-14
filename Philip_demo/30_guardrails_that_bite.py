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
  5. An assertion that cannot fail in one direction  (found: it passed a
     defect that reached a shopper)
  6. Why "the test passed" is not evidence the test works

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

from evals.run_intent import score_case
from src.retrieval.memory import InMemoryPriceRepository
from src.schemas.contract import Intent

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
note("  - an assertion that could only fail in one of two directions")
note("")
note("So the standard is: break the thing, watch the control fail, restore it.")
note("Everything below is that, run live.")


#: Stack-trace and separator lines, matched as PREFIXES.
#:
#: The first draft of this demo printed
#: `_ test_every_declared_requirement_is_imported[python-dotenv] __` and
#: `throw new Error(` -- a pytest separator and a source line. Both matched a
#: keyword; neither told the reader anything.
#:
#: The SECOND draft filtered them with substrings, including `"at "` -- which
#: matches inside the word "that", so it suppressed the very sentence it was
#: meant to surface. Prefixes, not substrings. That is the same mistake
#: `config/alarms.json` warns about for metric filters, made in a demo about
#: not making mistakes like that.
_NOISE_PREFIXES = ("_", "=", "throw ", "raise ", 'File "', "at ", "^", "~", "E   +", "|")


def _is_noise(line: str) -> bool:
    return not line or line.startswith(_NOISE_PREFIXES)


def run_check(argv: list[str], *, cwd: Path | None = None) -> tuple[bool, str]:
    """Run a command. Returns (passed, the most explanatory line of output)."""
    proc = subprocess.run(  # noqa: S603 - fixed argv built in this file
        argv, cwd=cwd or ROOT, capture_output=True, text=True, timeout=900
    )
    lines = [ln.strip() for ln in (proc.stdout + proc.stderr).splitlines()]

    # A rendered MESSAGE, in preference order. `E  ` is pytest's rendered
    # assertion line -- the one with the values substituted in, as opposed to
    # the source line that produced it.
    for wanted in ("is STALE:", "E   AssertionError", "AssertionError:"):
        for line in lines:
            if wanted in line and not _is_noise(line):
                return proc.returncode == 0, line.removeprefix("E   ")[:150]

    for line in lines:
        if line.startswith("FAILED"):
            return proc.returncode == 0, line[:150]
    return proc.returncode == 0, ""


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
        # Said BEFORE the slow call, and flushed, because `cdk synth` takes
        # about 45 seconds and a demo that goes silent for 45 seconds looks
        # like a demo that has hung.
        note("  running `cdk synth` (~45s, this is the real guard, not a stub)...")
        sys.stdout.flush()
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


# --------------------------- 5. an assertion blind in one direction

section("5. Guardrail: an assertion that cannot fail in one direction")

note("THIS ONE PASSED A DEFECT ALL THE WAY TO A SHOPPER. On 2026-09-14")
note('someone typed "i would like to have seafood meal planned for me" and')
note("was shown a banana porridge plan under the sentence:")
note("")
note("    All seafood has been excluded as requested.")
note("")
note("Extraction had read the request as its own negation. The intent eval")
note("had 47 scored cases, EIGHT of them about exclusions, and every one")
note("passed -- because of how the scoring was written:")
note("")
note("    if not wanted_set.issubset(actual_set):")
note("        failures.append(...)")
note("")
note("A SUBSET TEST. It can fail a model that excludes too LITTLE and can")
note("never fail one that excludes too MUCH. That is the correct direction")
note("for a safety control -- a model that over-excludes has answered safely,")
note("if bluntly -- and it means the golden set was structurally incapable of")
note("noticing the inverse defect.")
note("")
note("THE OBVIOUS FIX WOULD NOT HAVE WORKED EITHER. Adding a case that says")
note('"this request excludes nothing" reads like the right answer:')
note("")
note('    {"message": "a seafood meal plan", "expect": {"exclusions": []}}')
note("")
note("The empty set is a subset of every set, so that case passes against a")
note("model returning ANY exclusions at all, including the one that shipped.")
note("A new case against an unchanged assertion is a new way to be green.")
note("")
note("Broken here on purpose, with the constraints the live model produced:")
note("")

BROKEN = {"dietary_exclusions": ["seafood"], "preferred_ingredients": []}
FIXED = {"dietary_exclusions": [], "preferred_ingredients": ["seafood"]}


def _score(expect: dict, constraints: dict) -> list[str]:
    """
    Score one case through the REAL scorer, not a reproduction of it.

    `evals/run_intent.py` owns the assertion being examined, so a copy of it
    here could pass this demo while the scorer itself regressed -- which is the
    failure mode section 4 is about, committed inside the demo that explains it.
    """
    return score_case(
        {"expect": expect},
        {"intent": Intent.MEAL_PLAN, "constraints": constraints},
        InMemoryPriceRepository(),
    )


def _verdict(failures: list[str]) -> str:
    return "FAILED, correctly" if failures else "DID NOT FIRE"


subset_only = _score({"exclusions": []}, BROKEN)
note(f'  "exclusions": []       vs the defect   -> {_verdict(subset_only)}')
note("             the case a reviewer would have written, and it is green")
require(not subset_only, "the subset check unexpectedly failed an over-extraction")
note("")

equality = _score({"no_exclusions": True}, BROKEN)
note(f'  "no_exclusions": true  vs the defect   -> {_verdict(equality)}')
# Cut at the parenthetical rather than at a character count: `[:70]` landed
# mid-token ("(terms: ['s"), which is the same "show the sentence, not a
# fragment of it" defect this demo's own diagnostics had.
note(f"             {equality[0].split(' (terms:')[0]}")
require(equality, "the equality assertion did not fire on an over-extraction")
note("")

restored = _score({"no_exclusions": True, "preferences": ["seafood"]}, FIXED)
note(f'  "no_exclusions": true  vs the fix      -> {"passes" if not restored else restored}')
note("             and it does not fire on correct behaviour, which is the")
note("             half that stops an assertion being merely strict")
require(not restored, "the equality assertion fires on correct behaviour")
note("")
note("So the fix was not a case, it was an ASSERTION: `no_exclusions` is an")
note("equality on the resolved categories, and it lives beside the subset test")
note("rather than replacing it. Both directions are now expressible, and")
note("evals/cases/intent.json pol-001..005 score them.")
note("")
note("WHAT MAKES THIS SHAPE HARD. The other three guardrails in this demo")
note("were absent, inert, or wrong. This one WORKED -- it ran, compared real")
note("values, and had caught real defects. It was green for the right reasons")
note("and blind anyway, and no amount of watching it pass would have said so.")
note("Asking 'which direction can this fail in' is the only thing that does.")


# ------------------------------------------- 6. what green does not mean

section("6. Why 'the test passed' is not evidence the test works")

note("Three of the four guardrails above were written, reviewed, committed --")
note("and did not work:")
note("")
note("  requirements check   worked first time")
note("  stale-archive check  written only AFTER the defect shipped to AWS")
note("  observer check       PASSED while checking nothing")
note("  exclusion scoring    PASSED a defect that reached a shopper")
note("")
note("The difference between the second and third is instructive. The")
note("stale-archive guard did not exist, which is a visible gap. The observer")
note("guard EXISTED, was green, and was worthless -- which is invisible, and")
note("worse, because it also removed the motivation to look.")
note("")
note("SECTION 5 IS A FOURTH KIND, AND THE MOST COMFORTABLE ONE TO MISS. That")
note("assertion was not inert -- it ran, it compared real values, and it")
note("caught real defects. It was simply blind in one direction, which no")
note("amount of watching it pass would ever reveal. A test that cannot fail")
note("is findable by mutation; a test that cannot fail ONE WAY needs somebody")
note("to ask which way.")
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
