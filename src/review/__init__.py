"""
Bounded data-quality review (Req 13.7-13.8, Pilot Task 14).

DETERMINISTIC HALF ONLY. ADR 0002 is still *Proposed - mentor approval
required*, so no AgentCore Runtime is deployed and no model reviews anything.
What is here is the boundary a reviewer would sit behind and the validation its
output must survive - both required whoever does the reviewing, including a
human with a spreadsheet.
"""

from src.review.findings import (
    Finding,
    FindingKind,
    Rejection,
    ValidatedFindings,
    validate_findings,
)
from src.review.snapshot import (
    MAX_SNAPSHOT_ROWS,
    SNAPSHOT_FIELDS,
    ReviewSnapshot,
    SnapshotRow,
    SnapshotTooLarge,
    build_snapshot,
    implausible_unit_price,
    implausible_unit_price_values,
    snapshot_to_dicts,
)

__all__ = [
    "MAX_SNAPSHOT_ROWS",
    "SNAPSHOT_FIELDS",
    "Finding",
    "FindingKind",
    "Rejection",
    "ReviewSnapshot",
    "SnapshotRow",
    "SnapshotTooLarge",
    "ValidatedFindings",
    "build_snapshot",
    "implausible_unit_price",
    "implausible_unit_price_values",
    "snapshot_to_dicts",
    "validate_findings",
]
