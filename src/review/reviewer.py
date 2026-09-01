"""
The data-quality reviewer (ADR 0002 Workstream 2, Pilot Task 14).

EXPERIMENT, offline-first, NOT the shopper path. `review_snapshot` is the whole
public surface: a `ReviewSnapshot` plus a `ModelClient` in, a `ValidatedFindings`
out. It is the deterministic scaffolding a reviewer sits inside, and it holds the
last word -- the model proposes findings, this code validates every one against
the snapshot before returning them.

WHY THIS SHAPE. Three properties, each deliberate:

  * The model NEVER has the last word. Its `ReviewReport` is mapped to `Finding`
    objects and run through `validate_findings`, which rejects a finding that
    cites a row outside the snapshot, quotes a value the row does not have, or
    tells the pipeline what to do. A hijacked or hallucinating model cannot get a
    fabricated value past this, so the reviewer's trust boundary is the validator,
    not the model.

  * It is TESTABLE WITH NO AWS. It depends on the `ModelClient` Protocol, so a
    scripted client returning a canned `ReviewReport` exercises the whole path in
    a unit test -- the same seam the graph uses. That is what makes the offline
    experiment measurable before a single Runtime dollar is spent.

  * A MODEL FAILURE is not a review failure. If the model call raises (outage,
    throttle, guardrail block), the reviewer returns an empty validated result
    with the error recorded, never a partial or invented one. A review that
    could not run reports that it could not run; it does not fabricate an
    all-clear.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.models.base import (
    TASK_REVIEW_SNAPSHOT,
    ModelClient,
    ModelError,
    ModelTier,
)
from src.prompts.review import (
    SYSTEM_PROMPT,
    ReviewReport,
    build_review_prompt,
)
from src.review.findings import (
    Finding,
    FindingKind,
    ValidatedFindings,
    validate_findings,
)
from src.review.snapshot import ReviewSnapshot, snapshot_to_dicts

#: The finding kinds a reviewer may emit, as a set for a fast membership check.
#: A model that returns a kind outside `FindingKind` gets that finding dropped
#: rather than crashing the review -- an unknown kind is unverifiable, same
#: disposition as any other unusable finding.
_KNOWN_KINDS = frozenset(k.value for k in FindingKind)


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """
    The outcome of one review run.

    `validated` is the deterministic verdict on the model's findings. `ran` is
    False when the model call itself failed -- distinct from "ran and found
    nothing", because an all-clear the model never produced is not an all-clear.
    `error` carries why, for the operator log, never for a shopper.
    """

    validated: ValidatedFindings
    ran: bool
    error: str = ""

    @property
    def accepted(self) -> tuple[Finding, ...]:
        return self.validated.accepted


def _to_findings(report: ReviewReport) -> list[Finding]:
    """
    Model output -> `Finding` objects, dropping any with an unknown kind.

    The pydantic `ReviewReport` already constrained shapes and lengths; this
    maps its string `kind` onto the closed `FindingKind`. An unrecognised kind
    is dropped here rather than passed on: `validate_findings` works on
    `Finding`, and a kind it cannot represent is a finding no human can act on.
    """
    findings: list[Finding] = []
    for f in report.findings:
        if f.kind not in _KNOWN_KINDS:
            continue
        findings.append(
            Finding(
                kind=FindingKind(f.kind),
                store_key=f.store_key,
                product_key=f.product_key,
                observation=f.observation,
                quoted=dict(f.quoted),
            )
        )
    return findings


def review_snapshot(
    snapshot: ReviewSnapshot,
    *,
    model: ModelClient,
    max_tokens: int = 2048,
) -> ReviewResult:
    """
    Review a snapshot with a model, and validate every finding it returns.

    Pure orchestration: build the delimited prompt from the allowlist-serialised
    rows, ask the model for a `ReviewReport`, map it to `Finding`s, and hand the
    lot to `validate_findings`. No boto3, no database -- the model is injected,
    so this runs under a scripted client with no AWS.

    On a model failure returns `ran=False` with an empty validated result. The
    caller (operator tooling, an eval runner, later a Runtime handler) decides
    what to do with a review that could not run; this function never invents one.
    """
    rows = snapshot_to_dicts(snapshot)
    user = build_review_prompt(rows, table_name=snapshot.captured_from)

    try:
        report = model.structured(
            system=SYSTEM_PROMPT,
            user=user,
            schema=ReviewReport,
            tier=ModelTier.FAST,
            max_tokens=max_tokens,
            task=TASK_REVIEW_SNAPSHOT,
        )
    except ModelError as exc:
        return ReviewResult(
            validated=validate_findings([], snapshot),
            ran=False,
            error=str(exc),
        )

    validated = validate_findings(_to_findings(report), snapshot)
    return ReviewResult(validated=validated, ran=True)
