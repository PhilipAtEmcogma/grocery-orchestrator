"""
Reviewer findings, and the deterministic validation they must survive
(Req 13.7-13.8, Pilot Task 14).

A FINDING IS A HYPOTHESIS, NEVER AN INSTRUCTION. Req 13.8: the reviewer may not
treat candidate price fields as publication authority, publish prices, mutate
production, or have a finding acted on without deterministic reference
validation AND human approval. So nothing here writes, and nothing here decides
— it produces a reviewed list for a person to act on.

THE VALIDATION IS THE SAME SHAPE AS `assert_citations_match_retrieval`, AND FOR
THE SAME REASON. That check exists because a citation naming the right table
with a plausible key and a price nobody retrieved passed cleanly for months:
SHAPE IS NOT IDENTITY. A finding is exactly the same risk wearing different
clothes — "row X has a bad unit price" is worthless unless row X was in the
snapshot and its unit price really is what the finding says.

So every finding is checked three ways:

  1. the reference EXISTS in the snapshot the reviewer was given;
  2. the values it QUOTES match that row exactly;
  3. it reports rather than prescribes — a finding proposing a replacement
     value is refused, because that is publication authority arriving through
     the back door.

A finding that fails any of them is not a low-confidence finding. It is a
fabrication, and it is dropped with the reason recorded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from src.review.snapshot import ReviewSnapshot, SnapshotRow


class FindingKind(StrEnum):
    """
    What a reviewer is allowed to report.

    A CLOSED SET, deliberately. An open `kind` field would let a reviewer invent
    a category nobody has decided how to act on, and "act on it" is a human
    decision that needs a category it recognises.
    """

    IMPLAUSIBLE_UNIT_PRICE = "implausible_unit_price"
    IMPLAUSIBLE_PACK_SIZE = "implausible_pack_size"
    SUSPECT_CATEGORY = "suspect_category"
    DUPLICATE_PRODUCT = "duplicate_product"
    NAME_MISMATCH = "name_mismatch"
    STALE_CAPTURE = "stale_capture"
    # A price far from the product's OWN history -- "this doubled overnight".
    # The class the deterministic rules structurally cannot see (a single row is
    # internally consistent), and the reason the snapshot carries baseline
    # enrichment. A finding of this kind is expected to quote deviation_ratio
    # and/or baseline_avg_nzd so validate_findings can check it against the row.
    PRICE_DEVIATION = "price_deviation"


class Rejection(StrEnum):
    """Why a finding was thrown away. Recorded, never silent."""

    UNKNOWN_REFERENCE = "reference not in the snapshot"
    VALUE_MISMATCH = "quoted a value the row does not have"
    PRESCRIBES_A_VALUE = "proposed a replacement value"
    EMPTY_OBSERVATION = "no observation to check"


#: A finding that proposes a value is prescribing, not reporting. Matched on the
#: OBSERVATION text because that is where it would arrive: a reviewer told not
#: to supply a `corrected_price` field will write "should be $2.49" instead.
_PRESCRIPTIVE = re.compile(
    r"\b(should be|must be|change to|set to|replace with|correct(?:ed)? (?:value|price) is)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Finding:
    """
    One reviewed observation about one row.

    `quoted` is what makes it checkable: the reviewer must restate the field
    values its claim rests on, and validation compares them against the row. A
    finding that merely asserts "this looks wrong" cannot be verified and cannot
    be acted on, so it cannot be accepted either.

    There is deliberately NO field for a proposed value. See the module
    docstring.
    """

    kind: FindingKind
    store_key: str
    product_key: str
    observation: str
    quoted: dict[str, str] = field(default_factory=dict)

    @property
    def reference(self) -> tuple[str, str]:
        return (self.store_key, self.product_key)


@dataclass(frozen=True, slots=True)
class ValidatedFindings:
    """The outcome of a review: what survived, what did not, and why."""

    accepted: tuple[Finding, ...]
    rejected: tuple[tuple[Finding, Rejection], ...]

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def fabrication_rate(self) -> float:
        """
        Share of findings that failed validation.

        Worth watching rather than merely logging: a reviewer whose findings
        stop referring to real rows has changed behaviour, and this is the
        number that shows it before a human notices the findings are useless.
        """
        total = len(self.accepted) + len(self.rejected)
        return len(self.rejected) / total if total else 0.0


def validate_findings(findings: list[Finding], snapshot: ReviewSnapshot) -> ValidatedFindings:
    """
    Check every finding against the snapshot it was supposedly derived from.

    The snapshot is the entire universe the reviewer saw, so a reference
    outside it cannot be verified — and an unverifiable claim about a price is
    the thing this codebase refuses everywhere else.

    Deterministic and offline: no model, no network, no database. That is what
    makes it a gate rather than a second opinion.
    """
    accepted: list[Finding] = []
    rejected: list[tuple[Finding, Rejection]] = []

    for finding in findings:
        row = snapshot.row_for(finding.store_key, finding.product_key)
        if row is None:
            rejected.append((finding, Rejection.UNKNOWN_REFERENCE))
            continue
        if not finding.observation.strip():
            rejected.append((finding, Rejection.EMPTY_OBSERVATION))
            continue
        if _PRESCRIPTIVE.search(finding.observation):
            rejected.append((finding, Rejection.PRESCRIBES_A_VALUE))
            continue
        if not finding.quoted:
            rejected.append((finding, Rejection.EMPTY_OBSERVATION))
            continue
        if not _quotes_match(finding, row):
            rejected.append((finding, Rejection.VALUE_MISMATCH))
            continue
        accepted.append(finding)

    return ValidatedFindings(accepted=tuple(accepted), rejected=tuple(rejected))


def _quotes_match(finding: Finding, row: SnapshotRow) -> bool:
    """
    Every quoted field must exist on the row and match it exactly.

    Exactly, not approximately. A reviewer quoting `price_nzd` as "2.50" for a
    row holding "2.49" has either misread the snapshot or invented the number,
    and neither is a finding worth a human's attention. An unknown field name is
    the same failure: it means the claim rests on something that is not there.
    """
    for name, quoted_value in finding.quoted.items():
        if name not in {f.name for f in row.__dataclass_fields__.values()}:
            return False
        if str(getattr(row, name)) != str(quoted_value):
            return False
    return True
