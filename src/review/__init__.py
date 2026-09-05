"""
Bounded data-quality review (Req 13.7-13.8, Pilot Task 14).

OFFLINE-FIRST EXPERIMENT. No AgentCore Runtime is deployed. `review_snapshot`
drives a reviewer through the `ModelClient` seam, so the whole path runs under a
scripted model with no AWS -- which is what makes the experiment measurable
before any Runtime cost. The model only ever PROPOSES: every finding it returns
is validated against the snapshot by `validate_findings` before the reviewer
returns it, and that validation is required whoever does the reviewing,
including a human with a spreadsheet. Deploying the model half to a Runtime is a
later, separately gated step (ADR 0002 Workstream 2, still *Proposed*).
"""

from src.review.findings import (
    Finding,
    FindingKind,
    Rejection,
    ValidatedFindings,
    validate_findings,
)
from src.review.reviewer import (
    ReviewResult,
    propose_findings,
    review_snapshot,
    validate_report,
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
    "ReviewResult",
    "ReviewSnapshot",
    "SnapshotRow",
    "SnapshotTooLarge",
    "ValidatedFindings",
    "build_snapshot",
    "implausible_unit_price",
    "implausible_unit_price_values",
    "propose_findings",
    "review_snapshot",
    "snapshot_to_dicts",
    "validate_findings",
    "validate_report",
]
