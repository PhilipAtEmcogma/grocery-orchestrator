"""
Bounded data-quality review — the deterministic half (Req 13.7-13.8, Task 14).

No AgentCore Runtime is deployed and no model reviews anything: ADR 0002 is
still *Proposed — mentor approval required*. What is tested here is the boundary
a reviewer would sit behind and the validation its output must survive, both of
which are required whoever does the reviewing.

The tests that matter are the REFUSALS. A validator that accepts everything is
worse than no validator, because it launders a fabrication into a reviewed
finding.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from src.retrieval.base import PriceRecord
from src.retrieval.memory import InMemoryPriceRepository
from src.review import (
    MAX_SNAPSHOT_ROWS,
    SNAPSHOT_FIELDS,
    Finding,
    FindingKind,
    Rejection,
    SnapshotTooLarge,
    build_snapshot,
    implausible_unit_price,
    snapshot_to_dicts,
    validate_findings,
)
from src.schemas.contract import Store

TABLE = "grocery-products-dev"


@pytest.fixture(scope="module")
def records() -> list[PriceRecord]:
    return InMemoryPriceRepository().all_records[:20]


@pytest.fixture(scope="module")
def snapshot(records: list[PriceRecord]):
    return build_snapshot(records, table_name=TABLE)


def _finding(row, **overrides) -> Finding:
    base = {
        "kind": FindingKind.IMPLAUSIBLE_UNIT_PRICE,
        "store_key": row.store_key,
        "product_key": row.product_key,
        "observation": "unit price looks inconsistent with the pack size",
        "quoted": {"price_nzd": row.price_nzd},
    }
    return Finding(**{**base, **overrides})


# ---------------------------------------------------------------- boundary


def test_the_snapshot_is_built_from_an_allowlist_not_by_stripping(snapshot) -> None:
    """
    Req 13.8: the reviewer receives no shopper messages, locations, dietary
    data, sessions or credentials.

    Guaranteed by CONSTRUCTION rather than redaction. A field added to
    `PriceRecord` later cannot silently join the snapshot, because the snapshot
    is built from an explicit list — deny-by-default beats redaction, which has
    to be remembered.
    """
    from src.review.snapshot import SnapshotRow

    fields = {f.name for f in dataclasses.fields(SnapshotRow)}
    assert fields == set(SNAPSHOT_FIELDS)

    for row in snapshot_to_dicts(snapshot):
        assert set(row) == set(SNAPSHOT_FIELDS)


def test_the_snapshot_carries_nothing_about_a_shopper(snapshot) -> None:
    """
    The named prohibitions, asserted rather than argued.

    `lat`/`lon` are absent too — not because they are shopper data (they are
    store coordinates from config/store-locations.json) but because a reviewer
    checking a price does not need geography, and a field that is not there
    cannot leak.
    """
    forbidden = {
        "message",
        "session_id",
        "turn_id",
        "location",
        "lat",
        "lon",
        "dietary",
        "exclusions",
        "credential",
        "token",
        "user",
    }
    for row in snapshot_to_dicts(snapshot):
        assert not (set(row) & forbidden)


def test_money_crosses_the_boundary_as_a_string(snapshot) -> None:
    """The same rule as the wire and storage: a float cent is a wrong cent."""
    for row in snapshot.rows:
        assert isinstance(row.price_nzd, str)
        assert isinstance(row.unit_price_nzd, str)


def test_an_oversized_slice_raises_rather_than_truncating(records) -> None:
    """
    Silently taking the first N makes the reviewer's view depend on the
    caller's ordering, so a finding about "the catalogue" would really be a
    finding about whichever rows arrived first. The caller chooses the slice,
    and then it is on the record what was reviewed.
    """
    with pytest.raises(SnapshotTooLarge, match="cap is 3"):
        build_snapshot(records, table_name=TABLE, max_rows=3)
    assert MAX_SNAPSHOT_ROWS >= 1


# ---------------------------------------------------------------- validation


def test_a_finding_about_a_row_the_reviewer_never_saw_is_rejected(snapshot) -> None:
    """
    SHAPE IS NOT IDENTITY — the lesson `assert_citations_match_retrieval`
    exists for, applied to findings.

    A plausible-looking reference to a row that was not in the snapshot is a
    fabrication, not a low-confidence finding.
    """
    ghost = Finding(
        kind=FindingKind.DUPLICATE_PRODUCT,
        store_key="paknsave#nowhere",
        product_key="ghost-1kg",
        observation="appears twice in the catalogue",
        quoted={"price_nzd": "1.00"},
    )
    result = validate_findings([ghost], snapshot)
    assert result.accepted == ()
    assert result.rejected[0][1] is Rejection.UNKNOWN_REFERENCE


def test_a_finding_quoting_a_value_the_row_does_not_have_is_rejected(snapshot) -> None:
    """
    Quoting is what makes a finding checkable, so a wrong quote is the whole
    failure: the reviewer either misread the snapshot or invented the number.
    """
    row = snapshot.rows[0]
    result = validate_findings([_finding(row, quoted={"price_nzd": "999.99"})], snapshot)
    assert result.rejected[0][1] is Rejection.VALUE_MISMATCH


def test_a_finding_quoting_a_field_that_does_not_exist_is_rejected(snapshot) -> None:
    """An unknown field means the claim rests on something that is not there."""
    row = snapshot.rows[0]
    result = validate_findings([_finding(row, quoted={"wholesale_cost": "1.00"})], snapshot)
    assert result.rejected[0][1] is Rejection.VALUE_MISMATCH


@pytest.mark.parametrize(
    "observation",
    [
        "the price should be 2.49",
        "unit price must be recalculated to 4.98",
        "change to 500g",
        "set to the shelf price",
        "the corrected value is 2.49",
    ],
)
def test_a_finding_that_prescribes_a_value_is_rejected(snapshot, observation: str) -> None:
    """
    Req 13.8: candidate price fields are NOT publication authority.

    There is no `corrected_price` field to fill in, so a reviewer inclined to
    prescribe will write it in prose instead — which is the same authority
    arriving through the back door. Reporting is allowed; instructing is not.
    """
    row = snapshot.rows[0]
    result = validate_findings([_finding(row, observation=observation)], snapshot)
    assert result.rejected[0][1] is Rejection.PRESCRIBES_A_VALUE


@pytest.mark.parametrize("observation", ["", "   "])
def test_an_empty_observation_is_rejected(snapshot, observation: str) -> None:
    row = snapshot.rows[0]
    result = validate_findings([_finding(row, observation=observation)], snapshot)
    assert result.rejected[0][1] is Rejection.EMPTY_OBSERVATION


def test_a_finding_with_nothing_quoted_is_rejected(snapshot) -> None:
    """
    "This looks wrong" cannot be verified, so it cannot be accepted.

    A human acting on an unverifiable finding is doing the reviewer's job
    again, which is the opposite of the point.
    """
    row = snapshot.rows[0]
    result = validate_findings([_finding(row, quoted={})], snapshot)
    assert result.rejected[0][1] is Rejection.EMPTY_OBSERVATION


def test_a_well_formed_finding_survives(snapshot) -> None:
    row = snapshot.rows[0]
    result = validate_findings([_finding(row)], snapshot)
    assert result.accepted_count == 1
    assert result.rejected == ()
    assert result.fabrication_rate == 0.0


def test_the_fabrication_rate_is_reported(snapshot) -> None:
    """
    A reviewer whose findings stop referring to real rows has changed
    behaviour, and this is the number that shows it before a human notices the
    findings have become useless.
    """
    row = snapshot.rows[0]
    ghost = Finding(FindingKind.NAME_MISMATCH, "x#y", "z", "differs", {"price_nzd": "1.00"})
    result = validate_findings([_finding(row), ghost], snapshot)
    assert result.fabrication_rate == 0.5


def test_validation_needs_no_model_network_or_database(snapshot) -> None:
    """
    What makes it a GATE rather than a second opinion.

    The snapshot is the entire universe the reviewer saw, so every check is a
    comparison against data already in hand — which is why this can be trusted
    to judge a component that cannot be.
    """
    row = snapshot.rows[0]
    assert validate_findings([_finding(row)], snapshot).accepted_count == 1


# ---------------------------------------------------------------- the rule we keep


def test_the_unit_price_defect_is_caught_by_code_not_left_to_a_reviewer() -> None:
    """
    The defect that actually reached the live table, as a deterministic check.

    `unit_price_nzd` became "2490.00" against a $2.49 item across six rows,
    because a sold-each product (`pack_grams == 1`) was divided as though it
    weighed a gram, and it shipped with no signal at all.

    Kept as CODE. A model might notice it; a comparison cannot fail to. The
    reviewer's value is the anomalies nobody thought to write a rule for, and
    handing it the ones we did think of would be paying a language model to do
    arithmetic.
    """
    broken = PriceRecord(
        product_key="broccoli-each",
        store=Store.PAKNSAVE,
        store_location="Mangere",
        display_name="Pams Broccoli",
        canonical_name="Broccoli",
        category="produce",
        price_nzd=Decimal("2.49"),
        unit="each",
        unit_price_nzd=Decimal("2490.00"),  # the live defect
        pack_grams=1,
        on_special=False,
        valid_date="2026-07-31",
        lat=-36.96,
        lon=174.79,
        store_key="paknsave#mangere",
    )
    snapshot = build_snapshot([broken], table_name=TABLE)
    assert implausible_unit_price(snapshot.rows[0]) is True


def test_a_healthy_catalogue_row_is_not_flagged(snapshot) -> None:
    """A check that fires on rounding differences gets switched off."""
    assert not [r for r in snapshot.rows if implausible_unit_price(r)]
