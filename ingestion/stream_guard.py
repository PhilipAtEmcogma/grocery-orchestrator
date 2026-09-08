"""
Watch every write to the serving catalogue, whoever made it.

PILOT TASK 13'S LAST HALF, and the incident that justifies it is in the log.
`docs/ARCHITECTURE.md` §3t: a plain `scripts/load_seed_data.py` run — its
default action LOADS — silently re-added 152 fixture rows to the live products
table. They SHADOWED the real Lineage B prices, so the deployed endpoint began
answering with fixture data again, and nobody noticed until a parity re-run
days later. The loader is guarded now, with a regression test. This is the
control for the class rather than the instance.

WHY A STREAM AND NOT MORE CHECKING INSIDE INGESTION. `refresh()` already
validates, diffs and rejects before it writes, and none of that can see a write
it did not make. `load_seed_data.py` is not the only thing with credentials for
this table; a console edit, a teammate's script, or a future job all bypass
every check ingestion performs. **A stream sees the write, not the writer** —
which is exactly the property the incident needed and the reason this is a
justified decoupling rather than infrastructure for its own sake.

THE INVARIANT IT ENFORCES, and it is unusually clean. Every row in
`grocery-products-dev` carries `valid_date` = the capture date of the run that
wrote it, and the live table holds exactly ONE such date across all 2,759 rows
(`2026-08-28`, verified 2026-09-07). The fixture rows that caused the incident
carried `2026-07-31`. So:

    a row whose valid_date is not the expected capture date
    did not come from the current ingestion source

That is a statement about provenance, which is the thing this whole repository
is built to protect. It is also why the check is on the DATE rather than on
some "written_by" marker nobody would remember to set.

WHAT IT DOES NOT DO. It does not delete, quarantine or repair anything. It
reports. A stream consumer with write access to the table it watches could
turn a false positive into data loss, and the ingestion role's own append-only
history grant makes the same argument one table over. Detection and remediation
are separate authorities on purpose.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

#: The structured field the CloudWatch metric filter binds to. A JSON selector,
#: never a substring — `config/alarms.json` explains at length why matching the
#: text anywhere in a line would page somebody for an exception that quoted it.
FOREIGN_WRITE_LOG_MESSAGE = "catalogue_foreign_write"

#: Emitted once per batch, so the metric has a denominator. Without it, "zero
#: foreign writes" and "the consumer never ran" are the same picture — the
#: failure mode `config/alarms.json` calls out about `treat_missing_data`.
BATCH_LOG_MESSAGE = "catalogue_writes_observed"

#: How many offending keys to name. The execution history is not a place to
#: dump a catalogue; a handful is enough to recognise a wrong shape.
SAMPLE_LIMIT = 5


def expected_capture_date() -> str:
    """
    The capture date a legitimate row must carry.

    Defaults to the ingestion source's OWN constant rather than to a literal
    repeated here. `LineageBSource.CAPTURED_AT` is the data team's stated
    collection date, and two copies of it would eventually disagree — at which
    point this guard would report every legitimate write as foreign, which is
    the loudest possible way to be wrong.

    The environment override exists for the day a new capture lands before this
    code is redeployed. Over-reporting is the safe direction: a stale expected
    date makes real rows look foreign, which is noisy; a wrongly-widened one
    makes foreign rows look real, which is the incident.
    """
    override = os.environ.get("EXPECTED_CAPTURE_DATE")
    if override:
        return override

    from ingestion.sources import LineageBSource

    return LineageBSource.CAPTURED_AT


def _emit(payload: dict[str, Any]) -> None:
    """
    One JSON object per line on stdout, which is where CloudWatch reads.

    `print` rather than `logging`, matching `ingestion/handler.py`: this
    function runs in a Lambda with no Powertools, and the metric filters in
    `config/alarms.json` bind to the JSON field, not to a log level.
    """
    print(json.dumps(payload, separators=(",", ":")), file=sys.stdout, flush=True)


def _new_image(record: dict[str, Any]) -> dict[str, Any]:
    return (record.get("dynamodb") or {}).get("NewImage") or {}


def _old_image(record: dict[str, Any]) -> dict[str, Any]:
    return (record.get("dynamodb") or {}).get("OldImage") or {}


def _s(image: dict[str, Any], field: str) -> str | None:
    """Read a DynamoDB string attribute, or None if absent or another type."""
    value = image.get(field)
    return value.get("S") if isinstance(value, dict) else None


def inspect(records: list[dict[str, Any]], *, expected: str) -> list[dict[str, Any]]:
    """
    Return one finding per record whose provenance does not check out. Pure.

    Separated from the handler so the whole rule is testable without a Lambda
    event envelope, the same split `refresh()` and `reject_implausible` use.

    A record with NO `valid_date` at all is a finding too. An untyped write —
    a console edit, a partial `update-item` — is exactly as unexplained as one
    carrying the wrong date, and treating "absent" as "fine" would let the
    quietest version of the incident through.
    """
    findings: list[dict[str, Any]] = []
    for record in records:
        # REMOVE carries no NewImage. Deletions are filtered out upstream by the
        # event source mapping, and skipped here too so the rule holds whatever
        # the filter is later changed to -- `scripts/load_seed_data.py --remove`
        # is a legitimate cleanup and must not read as an intrusion.
        if record.get("eventName") == "REMOVE":
            continue

        image = _new_image(record)
        if not image:
            continue

        found = _s(image, "valid_date")
        if found == expected:
            continue

        findings.append(
            {
                "store_key": _s(image, "store_key"),
                "product_key": _s(image, "product_key"),
                "valid_date": found,
                "expected": expected,
                "event": record.get("eventName"),
                # What it replaced, when it replaced something. This is the
                # difference between "a new foreign row appeared" and "a foreign
                # row overwrote a real one", and the second is the shadowing
                # that made the 2026-09-01 endpoint answer with fixture prices.
                "replaced_valid_date": _s(_old_image(record), "valid_date"),
            }
        )
    return findings


def lambda_handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """
    Report writes to the serving catalogue that did not come from ingestion.

    RETURNS NORMALLY ON A FINDING. A finding is a fact about the DATA, not a
    failure of this function, and raising would send a correctly-processed
    batch to the DLQ and retry it — re-reporting the same rows until the
    records expire. The DLQ is for records this code could not process at all,
    which is what makes a message in it meaningful.
    """
    records = event.get("Records") or []
    expected = expected_capture_date()
    findings = inspect(records, expected=expected)

    _emit(
        {
            "message": BATCH_LOG_MESSAGE,
            "records": len(records),
            "foreign": len(findings),
            "expected_capture_date": expected,
        }
    )

    # One line PER finding, not one summarising line. The metric filter counts
    # lines, so a batch containing three foreign rows must move the metric by
    # three -- a single line with a count of three moves it by one, and the
    # alarm would then need to know about batching to be right.
    for finding in findings[:SAMPLE_LIMIT]:
        _emit({"message": FOREIGN_WRITE_LOG_MESSAGE, **finding})
    for finding in findings[SAMPLE_LIMIT:]:
        # Beyond the sample the KEYS are dropped and the signal is kept. The
        # metric must still count every one, or a mass re-seed -- the actual
        # incident, 152 rows -- would report as five.
        _emit(
            {
                "message": FOREIGN_WRITE_LOG_MESSAGE,
                "valid_date": finding["valid_date"],
                "expected": expected,
                "sampled": False,
            }
        )

    return {"records": len(records), "foreign": len(findings)}
