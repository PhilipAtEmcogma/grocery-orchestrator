r"""
DEMO 27 - Watching every write to the catalogue, whoever made it
================================================================

HOW TO RUN
----------
From the repository root:

    python Philip_demo/27_catalogue_stream_guard.py

On Windows without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/27_catalogue_stream_guard.py

No AWS account, credentials or network access. The stream RECORDS below are
the real DynamoDB event shape; the rule that reads them is production code.

MODES
-----
    local  (default and only)  the guard is invoked directly with synthetic
                               stream records. No stream, no queue, no Lambda.

WHAT THIS DEMONSTRATES
----------------------
Pilot Task 13c. The task says "where review decoupling is JUSTIFIED", and this
demo is largely about taking that clause seriously:

  1. The incident that justifies it, from this repository's own log
  2. Why more checking INSIDE ingestion could never have caught it
  3. The invariant, and why it is a statement about provenance
  4. The rule, run against real stream records - including the quiet case
  5. Why a FINDING must not reach the dead-letter queue
  6. What was proved in the account, and what a real failure taught

WHO THIS IS FOR
---------------
Anyone who has ever had a script write to a production table. Section 1 is a
true story about this project.

EXPECTED RESULT
---------------
Every check prints OK. The important pair: a fixture-dated row is caught, and
a 500-row legitimate refresh produces ZERO findings. Exit code 0.
"""

from __future__ import annotations

from _demo_support import LOCAL, heading, mode_banner, note, require, resolve_mode, section

from ingestion.sources import LineageBSource
from ingestion.stream_guard import (
    FOREIGN_WRITE_LOG_MESSAGE,
    expected_capture_date,
    inspect,
    lambda_handler,
)

mode = resolve_mode(supports=(LOCAL,))
mode_banner(mode, requires="nothing", mocked="the stream records only")

heading("DEMO 27 - The catalogue stream guard")

CAPTURE = LineageBSource.CAPTURED_AT
FIXTURE_DATE = "2026-07-31"


def record(*, valid_date=CAPTURE, event="INSERT", product="salted-butter-500g", old=None):
    image = {
        "store_key": {"S": "paknsave#albany"},
        "product_key": {"S": product},
        "price_nzd": {"S": "9.49"},
    }
    if valid_date is not None:
        image["valid_date"] = {"S": valid_date}
    rec = {"eventName": event, "dynamodb": {"NewImage": image}}
    if old is not None:
        rec["dynamodb"]["OldImage"] = {"valid_date": {"S": old}}
    return rec


# --------------------------------------------------------- 1. the incident

section("1. The incident that justifies this, from ARCHITECTURE 3t")

note("A plain `scripts/load_seed_data.py` run -- its default action LOADS --")
note("silently re-added 152 fixture rows to the LIVE products table.")
note("")
note("They SHADOWED the real Lineage B prices. The deployed endpoint began")
note("answering with fixture data again. Nobody noticed until a parity re-run")
note("days later.")
note("")
note("The loader is guarded now, with a regression test. That fixes the")
note("INSTANCE. This is the control for the CLASS.")


# ------------------------------------------- 2. why ingestion cannot do it

section("2. Why more checking inside ingestion could not have caught it")

note("`refresh()` already validates, diffs and rejects before it writes.")
note("None of that can see a write it did not make.")
note("")
note("And load_seed_data.py is not the only thing holding credentials for")
note("that table: a console edit, a teammate's script, or a future job all")
note("bypass every check ingestion performs.")
note("")
note("  >>> A STREAM SEES THE WRITE, NOT THE WRITER. <<<")
note("")
note("That is the property the incident needed, and the reason this counts as")
note("justified decoupling rather than a service added because a task names")
note("one.")


# -------------------------------------------------------- 3. the invariant

section("3. The invariant, which is a statement about provenance")

note("  every row carries valid_date = the capture date of the run that wrote it")
note(f"  the live table holds exactly ONE such date: {CAPTURE}")
note(f"  the fixture rows that caused the incident carried: {FIXTURE_DATE}")
note("")
note("  so: a row whose capture date is not the expected one")
note("      did not come from the current ingestion source")
note("")
note(f"  expected_capture_date() -> {expected_capture_date()}")
note("")
note("Defaulted from the ingestion source's OWN constant, not a second copy.")
note("Two copies would eventually disagree, at which point the guard reports")
note("every legitimate write as foreign -- the loudest possible way to be wrong.")
require(expected_capture_date() == CAPTURE, "expected_capture_date() == CAPTURE")
note("  [OK  ] the guard and the ingestion source agree on the date")


# --------------------------------------------------------- 4. the rule

section("4. The rule, run against real stream records")

checks = [
    (
        "the incident, replayed",
        [record(valid_date=FIXTURE_DATE)],
        1,
        "a fixture-dated row appears",
    ),
    (
        "shadowing, which is worse",
        [record(valid_date=FIXTURE_DATE, event="MODIFY", old=CAPTURE)],
        1,
        "it OVERWROTE a real row",
    ),
    (
        "the quiet case",
        [record(valid_date=None)],
        1,
        "no capture date at all - a console edit",
    ),
    (
        "a legitimate refresh",
        [record(event="MODIFY", product=f"p{i}", old=CAPTURE) for i in range(500)],
        0,
        "500 rows rewritten with the CURRENT date",
    ),
    (
        "a deletion",
        [{"eventName": "REMOVE", "dynamodb": {"OldImage": {"valid_date": {"S": CAPTURE}}}}],
        0,
        "load_seed_data.py --remove is legitimate cleanup",
    ),
]

for label, records, expect, why in checks:
    found = inspect(records, expected=CAPTURE)
    mark = "OK  " if len(found) == expect else "FAIL"
    note(f"  [{mark}] {label:26} {len(found):>3} finding(s)   ({why})")
    require(len(found) == expect, f"{label}: expected {expect} finding(s), got {len(found)}")

note("")
note("THE FOURTH ROW IS THE ONE THAT DECIDES WHETHER ANYONE KEEPS THIS ALARM")
note("ON. A scheduled refresh rewrites every row it holds -- 2,759 MODIFY")
note("events. A guard that read MODIFY as suspicious would report the entire")
note("catalogue on its first run and be muted the same day.")

note("")
note("What a finding carries:")
finding = inspect([record(valid_date=FIXTURE_DATE, event="MODIFY", old=CAPTURE)], expected=CAPTURE)[
    0
]
for key, value in finding.items():
    note(f"    {key:22} {value}")
note("")
note("`replaced_valid_date` is the difference between 'a foreign row appeared'")
note("and 'a foreign row overwrote a real one'. The second is the shadowing.")


# ------------------------------------------------ 5. findings and the DLQ

section("5. Why a FINDING must not reach the dead-letter queue")

result = lambda_handler({"Records": [record(valid_date=FIXTURE_DATE)]})
note(f"  handler returned normally: {result}")
note("")
note("A finding is a fact about the DATA, not a failure of this function.")
note("")
note("Raising would send a correctly-processed batch to the DLQ and retry it,")
note("re-reporting the same rows until they expire. Worse, it would make a")
note('message in the DLQ mean "we found something" rather than "this code')
note('could not run" -- and only the second reading makes the DLQ worth')
note("checking at all.")
note("")
note(f"The signal is a log line the metric filter binds to: {FOREIGN_WRITE_LOG_MESSAGE!r}")
note("One line PER finding, because the filter counts LINES: a batch with")
note("three foreign rows must move the metric by three. The real incident was")
note("152 rows, and reporting it as one would understate it by 152x.")


# ---------------------------------------------------- 6. what was proved

section("6. What was proved in the account")

note("Stream enabled, consumer deployed, and a row carrying the fixture")
note("capture date written to the LIVE table:")
note("")
note('    {"message":"catalogue_foreign_write","valid_date":"2026-07-31",')
note('     "expected":"2026-08-28","event":"INSERT"}')
note("")
note("Drill rows deleted; catalogue re-verified BY FULL SCAN at 2,759 rows,")
note("one capture date. (`ItemCount` said 2,761 -- it is a ~6-hourly estimate")
note("that had caught an intermediate state. A teardown trusting the cheap")
note("number would have reported phantom rows in the serving catalogue.)")
note("")
note("AND THE RETRY/DLQ EVIDENCE CAME FROM A REAL FAILURE, not a planted one.")
note("The first deploy shipped a STALE build/lambda.zip, built before")
note("stream_guard.py existed, so every invocation died on ImportModuleError:")
note("")
note('    {"condition":"RetryAttemptsExhausted","approximateInvokeCount":3,')
note('     "DDBStreamBatchInfo":{"shardId":"...","startSequenceNumber":"..."}}')
note("")
note("  retry    invoke count 3 = initial + the two configured retries")
note("  redrive  shard and sequence number make replay possible")
note("  backlog  the mapping held at 'PROBLEM: Function call failed'")
note("")
note("That defect produced a second guardrail -- see demo 30.")

print("\nDone.")
