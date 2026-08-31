"""
Sanitised, capped review snapshots (Req 13.7-13.8, Pilot Task 14).

THIS IS THE DETERMINISTIC HALF OF THE DATA-QUALITY REVIEWER, AND IT IS THE HALF
THAT DOES NOT NEED APPROVAL. ADR 0002 is still *Proposed — mentor approval
required*, so no AgentCore Runtime is deployed and no model reviews anything.
What is built here is the boundary a reviewer would sit behind and the
validation its output would have to survive — both of which are required
whoever or whatever does the reviewing, including a human with a spreadsheet.

WHAT THE BOUNDARY IS FOR. Req 13.8 says the reviewer shall not receive shopper
messages, locations, dietary data, sessions or credentials. A price record
contains none of those, so the honest way to guarantee it is not to strip
fields from a rich object — it is to CONSTRUCT the snapshot from an explicit
allowlist, so a field that is added to `PriceRecord` later does not silently
join the snapshot. Deny-by-default beats redaction, because redaction has to be
updated and an allowlist has to be extended.

`lat`/`lon` survive that allowlist deliberately: they are STORE coordinates from
`config/store-locations.json`, not a shopper's position. They are the only
geographic data in the system and they describe supermarkets.

CANDIDATE PRICES ARE UNTRUSTED REVIEW INPUT, NEVER A SECOND SOURCE OF TRUTH
(Req 13.8). A snapshot is something to look at, not something to publish from.
Nothing in this module writes, and `findings.py` refuses any finding that
proposes a value rather than reporting one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.retrieval.base import PriceRecord

#: Rows a single review may see. A cap, not a page size: a reviewer that can ask
#: for the whole catalogue is a reviewer that can exfiltrate it, and the cost
#: and token caps in Req 13.7 mean nothing if the input is unbounded.
MAX_SNAPSHOT_ROWS = 500

#: Fields a reviewer may see. ALLOWLIST, not a denylist -- see the module
#: docstring. Adding a field to PriceRecord must not silently widen this.
SNAPSHOT_FIELDS: tuple[str, ...] = (
    "store_key",
    "product_key",
    "store",
    "store_location",
    "display_name",
    "canonical_name",
    "category",
    "price_nzd",
    "unit",
    "unit_price_nzd",
    "pack_grams",
    "on_special",
    "valid_date",
)


class SnapshotTooLarge(ValueError):
    """More rows were offered than a single review may see."""


@dataclass(frozen=True, slots=True)
class SnapshotRow:
    """
    One catalogue row as a reviewer sees it.

    A separate type from `PriceRecord` on purpose. Passing the retrieval type
    would mean the reviewer's input widens every time retrieval's does, and
    nobody would notice: the coupling that matters here is the one that must
    NOT exist.
    """

    store_key: str
    product_key: str
    store: str
    store_location: str
    display_name: str
    canonical_name: str
    category: str
    price_nzd: str
    unit: str
    unit_price_nzd: str
    pack_grams: int
    on_special: bool
    valid_date: str

    @property
    def reference(self) -> tuple[str, str]:
        """What a finding must cite to be checkable: the base-table key."""
        return (self.store_key, self.product_key)


@dataclass(frozen=True, slots=True)
class ReviewSnapshot:
    """
    What a review run is allowed to look at, and nothing else.

    Frozen and self-describing: `rows` is the entire universe for that review,
    so a finding citing anything outside it is unverifiable by construction --
    which is what `findings.py` uses to reject fabricated references without
    needing to consult the database again.
    """

    rows: tuple[SnapshotRow, ...]
    captured_from: str

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def references(self) -> frozenset[tuple[str, str]]:
        return frozenset(row.reference for row in self.rows)

    def row_for(self, store_key: str, product_key: str) -> SnapshotRow | None:
        return next(
            (r for r in self.rows if r.store_key == store_key and r.product_key == product_key),
            None,
        )


def build_snapshot(
    records: list[PriceRecord],
    *,
    table_name: str,
    max_rows: int = MAX_SNAPSHOT_ROWS,
) -> ReviewSnapshot:
    """
    Price records -> a sanitised, capped snapshot.

    RAISES RATHER THAN TRUNCATING when handed more rows than the cap allows.
    Silently taking the first 500 would make the reviewer's view depend on the
    caller's ordering, and a finding about "the catalogue" would really be a
    finding about whichever rows happened to arrive first. The caller must
    choose the slice deliberately, and then it is on the record what was
    reviewed.

    Money crosses as STRINGS, the same rule as the wire and storage. A Decimal
    would be serialised by whatever transport carries the snapshot, and the
    float round-trip that ruins a cent is exactly what the string convention
    exists to prevent.
    """
    if len(records) > max_rows:
        raise SnapshotTooLarge(
            f"{len(records)} rows offered, cap is {max_rows}. Narrow the slice "
            "deliberately rather than letting the cap choose it: a truncated "
            "snapshot makes a finding about the catalogue really a finding "
            "about whichever rows arrived first."
        )

    rows = tuple(
        SnapshotRow(
            store_key=r.store_key,
            product_key=r.product_key,
            store=r.store.value,
            store_location=r.store_location,
            display_name=r.display_name,
            canonical_name=r.canonical_name,
            category=r.category,
            price_nzd=str(r.price_nzd),
            unit=r.unit,
            unit_price_nzd=str(r.unit_price_nzd),
            pack_grams=r.pack_grams,
            on_special=r.on_special,
            valid_date=r.valid_date,
        )
        for r in records
    )
    return ReviewSnapshot(rows=rows, captured_from=table_name)


def snapshot_to_dicts(snapshot: ReviewSnapshot) -> list[dict]:
    """
    The wire form, built from the allowlist rather than from the object.

    `dataclasses.asdict` would serialise whatever the dataclass happens to
    carry, so a field added to `SnapshotRow` would reach a reviewer without
    anyone deciding it should. Iterating SNAPSHOT_FIELDS makes the allowlist the
    thing that decides, which is the whole point of having one.
    """
    return [{field: getattr(row, field) for field in SNAPSHOT_FIELDS} for row in snapshot.rows]


#: The multiple of the derived unit price at which a row stops being a rounding
#: difference and starts being a defect. An order of magnitude, not a cent: the
#: defect this rule exists for was off by a factor of a THOUSAND, and a check
#: that fires on rounding gets switched off by the third person it wakes.
IMPLAUSIBILITY_FACTOR = 10


def implausible_unit_price_values(
    *, price_nzd: str | Decimal, unit_price_nzd: str | Decimal, pack_grams: int
) -> bool:
    """
    The rule itself, over three values rather than over a row type.

    ONE DEFINITION, TWO CALLERS, AND THAT IS THE POINT. The review boundary
    hands it a `SnapshotRow`; `ingestion.handler` hands it a DynamoDB item it is
    about to write. Those are different shapes at different ends of the system,
    and a rule copied into both is the dangerous kind of duplicate -- nothing is
    wrong, so nothing flags the day one copy is tuned. `LITERAL_MONEY` in
    `src/schemas/contract.py` carries the same rule for the same reason.

    Money arrives as `str` from storage and the wire and as `Decimal` in Python,
    so both are accepted and neither is coerced through float.
    """
    price = Decimal(price_nzd)
    unit_price = Decimal(unit_price_nzd)
    if pack_grams <= 1:
        # `pack_grams == 1` is the SOLD-EACH sentinel: one unit, not one gram.
        # For a sold-each product the unit price IS the price, and the defect
        # below is what happens when the sentinel is read as a weight.
        return unit_price != price
    expected = price * Decimal(1000) / Decimal(pack_grams)
    return (
        unit_price > expected * IMPLAUSIBILITY_FACTOR
        or unit_price * IMPLAUSIBILITY_FACTOR < expected
    )


def implausible_unit_price(row: SnapshotRow) -> bool:
    """
    The defect that actually reached the live table, as a deterministic check.

    `unit_price_nzd` became "2490.00" against a $2.49 item across six rows,
    because a sold-each product (`pack_grams == 1`) was divided as though it
    weighed a gram. It shipped with no signal at all.

    Kept here as CODE, not as something a reviewer is asked to spot. A model
    might notice it; a comparison cannot fail to. The reviewer's value is the
    anomalies nobody thought to write a rule for, and giving it the ones we did
    think of would be paying a language model to do arithmetic.

    IT NOW HAS A CALLER. Between 2026-08-31 and this change it was written,
    tested, and invoked by nothing: `ingestion/handler.py` diffed before writing
    and did not validate, so the one defect class known to have reached the live
    table was still undetected in production. A rule nobody runs is a comment
    with a test suite. See `ingestion.handler.reject_implausible`.
    """
    return implausible_unit_price_values(
        price_nzd=row.price_nzd,
        unit_price_nzd=row.unit_price_nzd,
        pack_grams=row.pack_grams,
    )
