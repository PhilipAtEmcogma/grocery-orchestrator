r"""
DEMO 22 - Price history, and the reviewer that reads it
========================================================

HOW TO RUN
----------
From the repository root:

    python Philip_demo/22_price_history_and_review.py

On Windows without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/22_price_history_and_review.py

No AWS account, credentials or network access.

MODES
-----
    local  (default and only)  the real history and review modules over
                               fixture-derived rows. No AWS.

WHAT THIS DEMONSTRATES
----------------------
Two features that had no demo, and belong together because one exists to feed
the other:

  1. `to_history_item` - what a history row carries, and what it refuses to
  2. `summarise` - a baseline DERIVED at read time, never stored
  3. Why an empty window gives `None` and not zero
  4. `build_snapshot` - the sanitised view a reviewer is allowed to see
  5. `validate_findings` - why a reviewer cannot propose a replacement value
  6. The fabricated quote, caught by deterministic code
  7. The append that DEGRADES, and the alarm that meters it

WHY THESE TWO ARE ONE DEMO
--------------------------
"This price doubled overnight" is not answerable from a catalogue that only
holds today. It needs history. The reviewer is the only consumer of that
history, and the history module exists because the reviewer needed a baseline
to measure a deviation against.

Neither is on the shopper path. A history row never becomes a Citation, and
the reviewer's findings are advisory -- which is exactly why the boundary
between them and the serving path is worth watching.
"""

from __future__ import annotations

from decimal import Decimal

from _demo_support import (
    LOCAL,
    ModeUnavailable,
    heading,
    mode_banner,
    money,
    note,
    resolve_mode,
    section,
    step,
)

from src.history import PriceHistoryRecord, summarise, to_history_item
from src.retrieval.memory import InMemoryPriceRepository
from src.review.findings import Finding, FindingKind, validate_findings
from src.review.snapshot import (
    build_snapshot,
    implausible_unit_price_values,
    snapshot_to_dicts,
)

try:
    mode = resolve_mode(supports=(LOCAL,))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

heading("DEMO 22 - Price history, and the reviewer that reads it")
mode_banner(
    mode,
    requires="nothing - no AWS account, credentials or network access",
    mocked="DynamoDB; the history and review modules are the real ones",
)

# ------------------------------------------------------------- 1. the row

section("1. What a history row carries")

product_item = {
    "store_key": "paknsave#albany",
    "product_key": "standard-milk-2l",
    "price_nzd": "4.79",
    "unit_price_nzd": "2.40",
    "pack_grams": 2000,
    "on_special": False,
    "valid_date": "2026-08-28",
    "display_name": "Pams Value Standard Milk",
    "store": "paknsave",
    "store_location": "Albany",
    "lat": Decimal("-36.72"),
    "lon": Decimal("174.70"),
    "gsi1_sk": "000000479#paknsave#albany",
    "gsi2_sk": "dairy#000000240",
    "canonical_name": "Standard Milk 2L",
    "category": "dairy",
    "unit": "2l",
}
row = to_history_item(product_item)
for key, value in row.items():
    note(f"  {key:18} {value!r}")
note("")
dropped = sorted(set(product_item) - set(row))
note(f"deliberately NOT carried: {', '.join(dropped)}")
note("")
note("A baseline needs who, what, how much, when. Display names and")
note("coordinates are shopper-facing fields, and a field that does not exist")
note("cannot leak -- the same rule RawOffer follows for prices.")
note("")
note(f"history_pk is the composite: {row['history_pk']!r}")
note(f"valid_date is the sort key:  {row['valid_date']!r}")
note("")
note("So a same-day re-run OVERWRITES an identical row rather than appending")
note("a duplicate. That is why the weekly liveness refresh appends nothing.")

# ------------------------------------------------------- 2. the baseline

section("2. A baseline is derived at read time, never stored")

records = [
    PriceHistoryRecord(
        store_key="paknsave#albany",
        product_key="standard-milk-2l",
        price_nzd=Decimal(p),
        unit_price_nzd=Decimal(u),
        valid_date=d,
        on_special=s,
    )
    for p, u, d, s in (
        ("4.79", "2.40", "2026-08-28", False),
        ("4.82", "2.41", "2026-08-21", False),
        ("3.99", "2.00", "2026-08-14", True),
        ("4.79", "2.40", "2026-08-07", False),
    )
]
baseline = summarise(records, window_days=30)
note(f"window            {baseline.window_days} days")
note(f"sample_count      {baseline.sample_count}")
note(f"average_nzd       {money(baseline.average_nzd)}")
note(f"min / max         {money(baseline.min_nzd)} / {money(baseline.max_nzd)}")
note("")
note("None of that is written anywhere. Storing a rolling average would mean")
note("two facts that can disagree -- the rows, and a summary of the rows --")
note("and the summary is the one nobody would re-derive to check.")

# ------------------------------------------------- 3. empty is not zero

section("3. An empty window gives None, not zero")

empty = summarise([], window_days=30)
note(f"sample_count      {empty.sample_count}")
note(f"average_nzd       {empty.average_nzd!r}")
note("")
note("An average of nothing is UNKNOWN, not $0.00. A reviewer told 'the")
note("baseline is $0' would chase a phantom anomaly on every product it has")
note("never seen before, which is every product on the first run.")

# ------------------------------------------------------- 4. the snapshot

section("4. The sanitised view a reviewer is allowed to see")

repo = InMemoryPriceRepository()
# butter-500g, because this demo reads the FIXTURE catalogue: the
# standard-milk-2l key above is a Lineage B key and has no fixture rows.
sample = repo.cheapest_for_product("butter-500g", limit=5)
snapshot = build_snapshot(sample, table_name="grocery-products-dev")
note(f"{len(snapshot.rows)} rows, from table {snapshot.captured_from!r}")
note("")
first = snapshot_to_dicts(snapshot)[0]
for key, value in first.items():
    note(f"  {key:18} {value!r}")
note("")
note("SnapshotRow is a SEPARATE type from PriceRecord, on purpose. Passing")
note("the retrieval type would mean the reviewer's input widens every time")
note("retrieval's does, and nobody would notice. The coupling that matters")
note("here is the one that must NOT exist.")

# ----------------------------------------------------- 5. no replacements

section("5. A finding cannot propose a replacement value")

note(
    "dataclass Finding fields: " + ", ".join(f.name for f in Finding.__dataclass_fields__.values())
)
note("")
note("There is no `suggested_value`, and that absence is the design. A")
note("reviewer that could propose a price would be a second source of prices")
note("-- one with no retrieval behind it. What it may do is OBSERVE, and")
note("quote the fields its observation rests on.")

# ------------------------------------------------- 6. the fabricated quote

section("6. The fabricated quote, caught deterministically")

row0 = snapshot.rows[0]
honest = Finding(
    kind=FindingKind.IMPLAUSIBLE_UNIT_PRICE,
    store_key=row0.store_key,
    product_key=row0.product_key,
    observation="unit price looks inconsistent with the pack size",
    quoted={"price_nzd": row0.price_nzd, "unit_price_nzd": row0.unit_price_nzd},
)
fabricated = Finding(
    kind=FindingKind.IMPLAUSIBLE_UNIT_PRICE,
    store_key=row0.store_key,
    product_key=row0.product_key,
    observation="this price is wrong",
    quoted={"price_nzd": "99.99", "unit_price_nzd": "0.01"},
)
off_snapshot = Finding(
    kind=FindingKind.IMPLAUSIBLE_UNIT_PRICE,
    store_key="woolworths#nowhere",
    product_key="invented-item",
    observation="a row the reviewer never saw",
    quoted={"price_nzd": "1.00"},
)
result = validate_findings([honest, fabricated, off_snapshot], snapshot)
note(f"accepted  {len(result.accepted)}")
note(f"rejected  {len(result.rejected)}")
for finding, why in result.rejected:
    note(f"    {why.value:22} {finding.observation}")
note("")
note("No model runs here. The snapshot is the entire universe the reviewer")
note("saw, so a reference outside it cannot be verified -- and an unverifiable")
note("claim about a price is what this codebase refuses everywhere else.")
note("")
note("This caught a real fabricated quote when the reviewer ran live on an")
note("AgentCore Runtime (docs/AGENTCORE-RUNTIME-REVIEWER.md). The caller-side")
note("validator working IS the trust boundary, not a disappointment.")

# ------------------------------------------- 7. the deterministic rule

section("7. The rule that runs before any reviewer")

honest_unit_price = implausible_unit_price_values(
    price_nzd="2.49", unit_price_nzd="4.98", pack_grams=500
)
defective_unit_price = implausible_unit_price_values(
    price_nzd="2.49", unit_price_nzd="2490.00", pack_grams=500
)
step(1, "$2.49 broccoli, 500g -> unit price $4.98/kg")
note(f"    implausible? {honest_unit_price}")
step(2, "the same broccoli written as $2,490.00/kg")
note(f"    implausible? {defective_unit_price}")
note("")
note("That second row REACHED THE LIVE TABLE once, six times over, and the")
note("diff did not stop it -- a diff reports change, and a defect on a first")
note("write is not a change. reject_implausible now refuses the row before")
note("it is written. It ran in production for the first time on 2026-09-04")
note("and refused nothing, which is the answer you want.")

# ------------------------------------------------ 8. degrade, then meter

section("8. The append DEGRADES, and the degradation is metered")

note("_append_history catches, prints a structured line, and returns 0.")
note("")
note("The products write has already succeeded by the time it runs. An")
note("exception there fails a Step Functions branch whose actual job -- the")
note("prices a shopper reads -- was done, and reports a working refresh as a")
note("broken one. Same trade src/handler.py makes for the idempotency store.")
note("")
note("BUT IT IS NOT SWALLOWED. It prints ingestion_history_write_failed, and")
note("grocery-ingestion-history-write-failed-dev alarms on it. A degradation")
note("nobody can see is the failure this repository keeps finding.")
note("")
note("The two ingestion alarms describe DIFFERENT facts: a refresh that threw,")
note("and a refresh that succeeded with its history missing. The guard is the")
note("code property that keeps them different.")

print("\nDone.")
