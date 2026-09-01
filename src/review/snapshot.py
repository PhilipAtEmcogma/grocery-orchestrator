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

from src.history import PriceBaseline
from src.retrieval.base import PriceRecord

#: Rows a single review may see. A cap, not a page size: a reviewer that can ask
#: for the whole catalogue is a reviewer that can exfiltrate it, and the cost
#: and token caps in Req 13.7 mean nothing if the input is unbounded.
MAX_SNAPSHOT_ROWS = 500

#: Fields a reviewer may see. ALLOWLIST, not a denylist -- see the module
#: docstring. Adding a field to PriceRecord must not silently widen this.
#:
#: THE BASELINE FIELDS ARE A DELIBERATE EXTENSION, not an accident. They come
#: from `src/history` (the append-only price history), and they are what turns a
#: single-point snapshot into one a reviewer can reason about: "this price is 10x
#: its own 90-day average" is a defect a single row cannot reveal and a baseline
#: can. They are prices, dates and counts -- the same class of non-PII data the
#: rest of the snapshot already carries (history has no shopper data to leak, by
#: construction). Deny-by-default still holds: this list is the whole surface, and
#: a field is here because someone decided it should be.
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
    # Baseline enrichment (from src/history). Empty strings / 0 when a row has
    # no history yet -- a new product's first capture has no past to compare to,
    # and "unknown" must not read as "0.00", which would look like a free item.
    "baseline_avg_nzd",
    "baseline_min_nzd",
    "baseline_max_nzd",
    "baseline_samples",
    "baseline_window_days",
    "deviation_ratio",
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

    # Baseline enrichment from the price history. Defaulted so a row with no
    # history is still a valid snapshot row -- a first capture has no past.
    # Money is strings (the money rule); "unknown" is "" not "0.00", and the
    # ratio is "" when there is nothing to compare against.
    baseline_avg_nzd: str = ""
    baseline_min_nzd: str = ""
    baseline_max_nzd: str = ""
    baseline_samples: int = 0
    baseline_window_days: int = 0
    deviation_ratio: str = ""

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
    baselines: dict[tuple[str, str], PriceBaseline] | None = None,
) -> ReviewSnapshot:
    """
    Price records -> a sanitised, capped snapshot, optionally baseline-enriched.

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

    `baselines` maps a row's `(store_key, product_key)` to its `PriceBaseline`
    from `src/history`. It is OPTIONAL and supplied by the caller -- the same
    seam as `records` -- so this function stays pure: the boto3 read that
    produces the baselines lives in `src/history/store.py`, not here. A row with
    no entry in `baselines` (a new product with no past) gets the empty
    defaults, so "no baseline" reads as blank rather than as a zero price.
    """
    if len(records) > max_rows:
        raise SnapshotTooLarge(
            f"{len(records)} rows offered, cap is {max_rows}. Narrow the slice "
            "deliberately rather than letting the cap choose it: a truncated "
            "snapshot makes a finding about the catalogue really a finding "
            "about whichever rows arrived first."
        )

    baselines = baselines or {}
    rows = tuple(
        _row_with_baseline(r, baselines.get((r.store_key, r.product_key))) for r in records
    )
    return ReviewSnapshot(rows=rows, captured_from=table_name)


def _row_with_baseline(r: PriceRecord, baseline: PriceBaseline | None) -> SnapshotRow:
    """One record plus its baseline (if any) as a snapshot row."""
    base: dict = {
        "store_key": r.store_key,
        "product_key": r.product_key,
        "store": r.store.value,
        "store_location": r.store_location,
        "display_name": r.display_name,
        "canonical_name": r.canonical_name,
        "category": r.category,
        "price_nzd": str(r.price_nzd),
        "unit": r.unit,
        "unit_price_nzd": str(r.unit_price_nzd),
        "pack_grams": r.pack_grams,
        "on_special": r.on_special,
        "valid_date": r.valid_date,
    }
    if baseline is not None and baseline.average_nzd is not None:
        ratio = baseline.deviation_ratio(r.price_nzd)
        base.update(
            baseline_avg_nzd=str(baseline.average_nzd),
            baseline_min_nzd=str(baseline.min_nzd),
            baseline_max_nzd=str(baseline.max_nzd),
            baseline_samples=baseline.sample_count,
            baseline_window_days=baseline.window_days,
            # Quantised so a reviewer quoting it can match it exactly, the way
            # every other quoted value in a finding must.
            deviation_ratio=("" if ratio is None else str(ratio.quantize(Decimal("0.01")))),
        )
    return SnapshotRow(**base)


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
