"""
Bounded data-quality review — the deterministic half (Req 13.7-13.8, Task 14).

No AgentCore Runtime is deployed: ADR 0002 is still *Proposed — mentor approval
required*. The reviewer (`review_snapshot`) runs OFFLINE here through the
`ModelClient` seam, driven by a scripted client, so there is no AWS and no live
model. What is tested is the boundary a reviewer sits behind, the enrichment it
reads, and the validation its output must survive -- all required whoever does
the reviewing.

The tests that matter are the REFUSALS. A validator that accepts everything is
worse than no validator, because it launders a fabrication into a reviewed
finding -- and the reviewer's whole trust boundary is that validator, not the
model, so a model that hallucinates a value must be structurally unable to get
it past the review.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from src.models.base import ModelClient, ModelError
from src.prompts.review import ReviewFinding, ReviewReport
from src.retrieval.base import PriceRecord
from src.retrieval.memory import InMemoryPriceRepository
from src.review import (
    MAX_SNAPSHOT_ROWS,
    SNAPSHOT_FIELDS,
    Finding,
    FindingKind,
    Rejection,
    ReviewSnapshot,
    SnapshotRow,
    SnapshotTooLarge,
    build_snapshot,
    implausible_unit_price,
    review_snapshot,
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


# ---------------------------------------------------------------- baseline enrichment


def _baseline(avg: str, samples: int = 30, window: int = 90):
    from src.history import PriceBaseline

    return PriceBaseline(
        history_pk="",
        window_days=window,
        sample_count=samples,
        average_nzd=Decimal(avg),
        min_nzd=Decimal(avg),
        max_nzd=Decimal(avg),
        latest_nzd=Decimal(avg),
        latest_date="2026-07-31",
    )


def test_a_row_with_no_baseline_reads_as_blank_not_zero(snapshot) -> None:
    """
    'Unknown' must not look like '0.00'. A new product's first capture has no
    past to compare to, and a $0 baseline would read as a free item and invite
    a phantom deviation finding.
    """
    row = snapshot.rows[0]
    assert row.baseline_avg_nzd == ""
    assert row.deviation_ratio == ""
    assert row.baseline_samples == 0


def test_the_snapshot_carries_the_baseline_when_supplied(records) -> None:
    """A supplied baseline enriches the matching row, keyed on (store_key, product_key)."""
    r = records[0]
    baselines = {(r.store_key, r.product_key): _baseline(str(r.price_nzd))}
    snap = build_snapshot(records, table_name=TABLE, baselines=baselines)
    row = snap.row_for(r.store_key, r.product_key)
    assert row is not None
    assert row.baseline_avg_nzd == str(r.price_nzd)
    assert row.baseline_samples == 30
    assert row.baseline_window_days == 90
    # Price equals its own average -> ratio 1.00.
    assert row.deviation_ratio == "1.00"


def test_the_deviation_ratio_flags_a_price_far_from_its_own_history(records) -> None:
    """The reviewer's real target: a price that deviates sharply from its baseline."""
    r = records[0]
    # Baseline averages a tenth of the current price -> ratio 10.
    tenth = (r.price_nzd / Decimal(10)).quantize(Decimal("0.01"))
    baselines = {(r.store_key, r.product_key): _baseline(str(tenth))}
    snap = build_snapshot(records, table_name=TABLE, baselines=baselines)
    row = snap.row_for(r.store_key, r.product_key)
    assert row is not None
    assert Decimal(row.deviation_ratio) >= Decimal("9")


def test_baseline_fields_are_in_the_allowlist_and_carry_no_pii(records) -> None:
    """Enrichment is deliberate: the new fields are in SNAPSHOT_FIELDS and are prices/counts."""
    baseline_fields = {
        "baseline_avg_nzd",
        "baseline_min_nzd",
        "baseline_max_nzd",
        "baseline_samples",
        "baseline_window_days",
        "deviation_ratio",
    }
    assert baseline_fields <= set(SNAPSHOT_FIELDS)
    # And they serialise through the allowlist path like every other field.
    r = records[0]
    baselines = {(r.store_key, r.product_key): _baseline(str(r.price_nzd))}
    snap = build_snapshot(records, table_name=TABLE, baselines=baselines)
    for dumped in snapshot_to_dicts(snap):
        assert baseline_fields <= set(dumped)


def test_a_deviation_finding_that_quotes_the_baseline_validates(records) -> None:
    """
    A PRICE_DEVIATION finding quoting deviation_ratio / baseline_avg_nzd is
    checkable against the enriched row -- the whole point of the enrichment.
    """
    r = records[0]
    tenth = (r.price_nzd / Decimal(10)).quantize(Decimal("0.01"))
    baselines = {(r.store_key, r.product_key): _baseline(str(tenth))}
    snap = build_snapshot(records, table_name=TABLE, baselines=baselines)
    row = snap.row_for(r.store_key, r.product_key)
    assert row is not None

    finding = Finding(
        kind=FindingKind.PRICE_DEVIATION,
        store_key=row.store_key,
        product_key=row.product_key,
        observation="price is far above its own 90-day history",
        quoted={
            "price_nzd": row.price_nzd,
            "baseline_avg_nzd": row.baseline_avg_nzd,
            "deviation_ratio": row.deviation_ratio,
        },
    )
    result = validate_findings([finding], snap)
    assert result.accepted_count == 1

    # A deviation finding quoting the WRONG ratio is a fabrication, rejected.
    wrong = Finding(
        kind=FindingKind.PRICE_DEVIATION,
        store_key=row.store_key,
        product_key=row.product_key,
        observation="price is far above its own history",
        quoted={"deviation_ratio": "2.00"},
    )
    assert validate_findings([wrong], snap).rejected[0][1] is Rejection.VALUE_MISMATCH


# ---------------------------------------------------------------- the reviewer (offline)


class _StubReviewer(ModelClient):
    """
    A ModelClient that returns a fixed ReviewReport (or fails), for driving
    `review_snapshot` with no AWS. The same seam the graph tests use.
    """

    def __init__(self, report: ReviewReport | None = None, *, fail: bool = False) -> None:
        self._report = report or ReviewReport(findings=[])
        self._fail = fail

    @property
    def last_usage(self) -> dict:
        return {}

    def text(self, **kwargs) -> str:  # pragma: no cover - reviewer never calls text
        raise ModelError("no text path")

    def structured(self, *, schema, **kwargs):
        # This stub only ever backs `review_snapshot`, which calls it with
        # schema=ReviewReport, so returning the fixed report is well-typed
        # without a cast on a runtime `schema` variable.
        if self._fail:
            raise ModelError("scripted upstream failure")
        return self._report


def _one_row_snapshot(**overrides) -> ReviewSnapshot:
    base = {
        "store_key": "paknsave#albany",
        "product_key": "butter-500g",
        "store": "paknsave",
        "store_location": "Albany",
        "display_name": "Pams Butter 500g",
        "canonical_name": "Butter",
        "category": "dairy",
        "price_nzd": "47.90",
        "unit": "500g",
        "unit_price_nzd": "95.80",
        "pack_grams": 500,
        "on_special": False,
        "valid_date": "2026-07-31",
        "baseline_avg_nzd": "4.79",
        "baseline_min_nzd": "4.59",
        "baseline_max_nzd": "4.99",
        "baseline_samples": 30,
        "baseline_window_days": 90,
        "deviation_ratio": "10.00",
    }
    base.update(overrides)
    return ReviewSnapshot(rows=(SnapshotRow(**base),), captured_from=TABLE)


def test_the_reviewer_validates_a_true_finding_the_model_returns() -> None:
    """The end-to-end offline path: a model's finding, checked against the snapshot."""
    snap = _one_row_snapshot()
    row = snap.rows[0]
    report = ReviewReport(
        findings=[
            ReviewFinding(
                store_key=row.store_key,
                product_key=row.product_key,
                kind="price_deviation",
                observation="price is far above its own 90-day history",
                quoted={"deviation_ratio": row.deviation_ratio},
            )
        ]
    )
    result = review_snapshot(snap, model=_StubReviewer(report))
    assert result.ran is True
    assert len(result.accepted) == 1
    assert result.accepted[0].kind is FindingKind.PRICE_DEVIATION


def test_the_reviewer_cannot_launder_a_fabricated_value_past_validation() -> None:
    """
    The reviewer's trust boundary is the validator, not the model. A finding
    quoting a value the row does not have is rejected even though the model
    returned it confidently.
    """
    snap = _one_row_snapshot()
    row = snap.rows[0]
    report = ReviewReport(
        findings=[
            ReviewFinding(
                store_key=row.store_key,
                product_key=row.product_key,
                kind="price_deviation",
                observation="price is far above its history",
                quoted={"deviation_ratio": "2.00"},  # the row says 10.00
            )
        ]
    )
    result = review_snapshot(snap, model=_StubReviewer(report))
    assert result.ran is True
    assert result.accepted == ()
    assert result.validated.rejected[0][1] is Rejection.VALUE_MISMATCH


def test_a_model_failure_is_not_a_clean_review() -> None:
    """
    An all-clear the model never produced is not an all-clear. A failed call
    returns ran=False with no findings, so the caller can tell "reviewed,
    nothing found" from "could not review".
    """
    result = review_snapshot(_one_row_snapshot(), model=_StubReviewer(fail=True))
    assert result.ran is False
    assert result.accepted == ()
    assert "failure" in result.error


def test_a_finding_of_an_unknown_kind_is_dropped_not_crashed() -> None:
    """
    A model returning a kind outside FindingKind is a finding no human can act
    on -- dropped, not fatal to the review.
    """
    snap = _one_row_snapshot()
    row = snap.rows[0]
    report = ReviewReport(
        findings=[
            ReviewFinding(
                store_key=row.store_key,
                product_key=row.product_key,
                kind="totally_made_up_kind",
                observation="something",
                quoted={"deviation_ratio": row.deviation_ratio},
            )
        ]
    )
    result = review_snapshot(snap, model=_StubReviewer(report))
    assert result.ran is True
    assert result.accepted == ()
    assert result.validated.rejected == ()  # dropped before validation, not rejected


def test_an_empty_report_is_a_valid_clean_review() -> None:
    """Reporting nothing is correct when the rows are clean, and it 'ran'."""
    result = review_snapshot(_one_row_snapshot(), model=_StubReviewer(ReviewReport(findings=[])))
    assert result.ran is True
    assert result.accepted == ()


def test_the_reportable_kinds_are_a_subset_of_findingkind() -> None:
    """
    The prompt's vocabulary must be catchable by the validator. A kind the
    prompt invites but FindingKind cannot represent would be silently dropped
    by `_to_findings`, so a widening of one must be a deliberate widening of
    the other.
    """
    from src.prompts.review import REPORTABLE_KINDS

    known = {k.value for k in FindingKind}
    assert set(REPORTABLE_KINDS) <= known
