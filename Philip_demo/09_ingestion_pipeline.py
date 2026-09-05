r"""
DEMO 9 - The ingestion pipeline: source -> normalise -> diff -> write
====================================================================

HOW TO RUN
----------
    python Philip_demo/09_ingestion_pipeline.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/09_ingestion_pipeline.py

Against the deployed table, reporting what a refresh WOULD change:

    DEMO_MODE=aws python Philip_demo/09_ingestion_pipeline.py

MODES
-----
    local  (default)  the whole pipeline except the write. Sources, the
                      normaliser and the diff are pure functions over
                      committed data, so everything but DynamoDB runs offline.
    aws               additionally calls refresh(..., dry_run=True) against
                      grocery-products-dev. Reads only - it computes the diff
                      and writes NOTHING. Needs credentials with
                      dynamodb:Query on the table (config/iam-ingestion-role.json).

WHAT THIS DEMONSTRATES
----------------------
  1. The acquisition gate - live retailer traffic is refused, not configured
  2. A source returns FACTS ONLY, and cannot return an undated price
  3. Normalisation: money as a string, and the two GSI sort keys
  4. The unit-price sentinel that once wrote $2,490.00 against a broccoli
  5. diff-before-write: pure, exhaustively testable, no AWS
  6. Idempotence, shown rather than claimed - a second pass changes nothing
  7. What one Step Functions Map branch actually invokes

ARCHITECTURE
------------
    EventBridge Scheduler (weekly liveness check; DISABLED since 2026-09-03)
        v
    Step Functions, Inline Map, one branch per retailer
        v
    ingestion.handler.lambda_handler  {"retailer": "paknsave"}
        v
    resolve_source -> FixtureSource | LineageBSource   (never a live site)
        v
    ingestion.normalise.to_item      RawOffer -> DynamoDB item
        v
    diff_items                       what would change
        v
    grocery-products-dev             batch_writer, put overwrites on key

Catch sits INSIDE the item processor, so one retailer failing does not abort
the Map and discard the retailers that already succeeded.
"""

from __future__ import annotations

import os
from collections import Counter
from decimal import Decimal

from _demo_support import (
    AWS,
    LOCAL,
    ModeUnavailable,
    aws_identity,
    blocked,
    heading,
    mode_banner,
    note,
    resolve_mode,
    section,
    step,
)

from ingestion.handler import DIFFED_FIELDS, diff_items
from ingestion.normalise import gsi1_sk, gsi2_sk, store_key, to_item, unit_price
from ingestion.sources import KNOWN_RETAILERS, FixtureSource, RawOffer, resolve_source

try:
    mode = resolve_mode(supports=(LOCAL, AWS))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

heading("DEMO 9 - The ingestion pipeline")
mode_banner(
    mode,
    requires=(
        "nothing - committed fixtures only"
        if mode == LOCAL
        else "AWS credentials with dynamodb:Query on grocery-products-dev (read only)"
    ),
    mocked=(
        "the retailer sources (recorded fixtures) AND the DynamoDB write"
        if mode == LOCAL
        else "the retailer sources (recorded fixtures). The table is real."
    ),
)

# ---------------------------------------------------------- acquisition gate
section("1. The acquisition gate")
print("  resolve_source() is the ONLY way ingestion obtains a source, and it")
print("  refuses live acquisition rather than falling back to it quietly.\n")
for retailer in KNOWN_RETAILERS:
    print(f"    {retailer:<12} -> {type(resolve_source(retailer)).__name__}")

os.environ["LIVE_ACQUISITION"] = "1"
try:
    resolve_source("paknsave")
    print("\n  ...returned a source, which would be wrong.")
except NotImplementedError as exc:
    print("\n  With LIVE_ACQUISITION=1:")
    print(f"    NotImplementedError: {str(exc)[:120]}...")
finally:
    del os.environ["LIVE_ACQUISITION"]
note("")
note("That is a tripwire, not a feature flag: adding a live adapter requires")
note("deleting a line that says why it is there. Thirteen conditions in")
note("ACQUISITION-RISK.md section 8 gate it, and condition 1 - a human having")
note("read the three unretrieved sources - is not met.")

# -------------------------------------------------------------- facts only
section("2. A source returns facts, and cannot return an undated price")
source = FixtureSource("paknsave")
offers = source.fetch()
first = offers[0]
print(f"  {len(offers)} offers from {source.retailer}. One of them:\n")
for field in RawOffer.__dataclass_fields__:
    print(f"    {field:<16} {getattr(first, field)!r}")
print("\n  No image, no marketing copy, no description, no review, nothing")
print("  resembling personal data. A field that does not exist cannot be")
print("  published by mistake (ACQUISITION-RISK.md section 8, condition 7).")

undated = RawOffer(
    product_key=first.product_key,
    store=first.store,
    store_location=first.store_location,
    display_name=first.display_name,
    canonical_name=first.canonical_name,
    category=first.category,
    price_nzd=first.price_nzd,
    unit=first.unit,
    pack_grams=first.pack_grams,
    on_special=first.on_special,
    captured_at="",
    lat=first.lat,
    lon=first.lon,
)
try:
    to_item(undated)
    print("\n  ...an undated offer produced an item, which would be wrong.")
except ValueError as exc:
    print(f"\n  An offer with no capture date: ValueError: {exc}")
note("`captured_at` has no default anywhere in this path. A price the shopper")
note("cannot date is a price they cannot evaluate, and a default would let an")
note("undated offer through by omission rather than by decision.")

# ------------------------------------------------------------- normalisation
section("3. Normalisation: what the write side owes the read side")
item = to_item(first)
print(f"  store_key   {item['store_key']!r}")
print(f"  gsi1_sk     {item['gsi1_sk']!r}")
print(f"  gsi2_sk     {item['gsi2_sk']!r}")
print(f"  price_nzd   {item['price_nzd']!r}   <- a STRING at rest")
print(f"  category    {item['category']!r}")
note("")
note("Money is Decimal in Python and a string on the wire and at rest.")
note("DynamoDB's numeric type round-trips through float in most client paths,")
note("and a float cent is a wrong cent.")

print("\n  Why the sort key is zero-padded, in one line:")
cheap, dear = Decimal("2.97"), Decimal("10.00")
padded = [gsi1_sk(p, "paknsave", "Albany") for p in (cheap, dear)]
unpadded = [f"{int(p * 100)}#paknsave#albany" for p in (cheap, dear)]
print(f"    padded    {padded[0]} < {padded[1]}  -> {padded[0] < padded[1]}")
print(f"    unpadded  {unpadded[0]} < {unpadded[1]}  -> {unpadded[0] < unpadded[1]}")
note("Lexicographic order only agrees with numeric order at a fixed width, and")
note("GSI1 exists so 'cheapest X' is the FIRST item the query returns rather")
note("than something the application sorts afterwards.")

print("\n  GSI1 answers 'cheapest of this product'; GSI2 'cheapest in this")
print("  category', which is what a meal plan asks on every turn:")
print(f"    GSI1  pk=product_key  sk={gsi1_sk(cheap, 'paknsave', 'Albany')}")
print(f"    GSI2  pk=category     sk={gsi2_sk(cheap, 'butter-500g', 'paknsave', 'Albany')}")
note("GSI2 replaced a full-table Scan on the meal-plan path, and")
note("dynamodb:Scan was then REMOVED from the orchestrator role - so a live")
note("meal plan succeeding afterwards is itself the proof nothing scans.")

# ------------------------------------------------------------- the sentinel
section("4. The unit-price sentinel, and the defect it now prevents")
print("  pack_grams == 1 means 'sold each', not 'weighs one gram'.\n")
print(f"  {'product':<26} {'price':>8} {'pack_grams':>11} {'unit_price_nzd':>15}")
print(f"  {'-' * 26} {'-' * 8} {'-' * 11} {'-' * 15}")
for name, price, grams in (
    ("Broccoli (sold each)", Decimal("2.49"), 1),
    ("Butter 500g", Decimal("2.97"), 500),
    ("Rice 1kg", Decimal("3.49"), 1000),
):
    print(f"  {name:<26} ${price:>7} {grams:>11} ${unit_price(price, grams):>14}")
naive = Decimal("2.49") * 1000 / 1
print(f"\n  Without the sentinel the broccoli row reads ${naive:.2f} per kilogram.")
note("That is not hypothetical: the first version of this function omitted the")
note("guard and wrote unit_price_nzd '2490.00' into the live products table,")
note("across six rows, with green tests and no signal that anything changed.")
note("unit_price_nzd is read straight into the Citation a shopper sees.")

# -------------------------------------------------------------------- diff
section("5. diff-before-write, which is pure and needs no AWS")
items = [to_item(o) for o in offers]
existing = {(i["store_key"], i["product_key"]): dict(i) for i in items}

step(1, "a refresh over an UNCHANGED catalogue")
same = diff_items(existing, items)
print(f"      added {same['added']}   changed {same['changed']}   unchanged {same['unchanged']}")

step(2, "the same refresh after one price moved and one row is new")
moved = dict(existing)
victim = items[0]
moved[(victim["store_key"], victim["product_key"])] = {
    **victim,
    "price_nzd": "1.99",
    "gsi1_sk": gsi1_sk(Decimal("1.99"), first.store, first.store_location),
}
moved.pop((items[1]["store_key"], items[1]["product_key"]))
delta = diff_items(moved, items)
print(f"      added {delta['added']}   changed {delta['changed']}   unchanged {delta['unchanged']}")
for change in delta["sample_changed"]:
    for f in change["fields"]:
        print(f"        {change['key']}  {f['field']}: {f['from']} -> {f['to']}")
print(f"      sample_added   {delta['sample_added']}")
note("")
note(f"Diffed fields: {', '.join(DIFFED_FIELDS)}")
note("Named explicitly, so adding a field to to_item() is a decision about")
note("whether a change in it should be visible rather than a silent omission.")
note("")
note("The diff deliberately does NOT block on a threshold. With live")
note("acquisition a genuine special moves a real proportion of a catalogue, so")
note("a percentage gate would be either too loose to catch a defect or would")
note("refuse legitimate refreshes. What it does is make the change VISIBLE in")
note("the execution history, so '150 rows changed on a quiet day' is")
note("answerable after the fact instead of undiscoverable.")

# ------------------------------------------------------------- idempotence
section("6. Idempotence, shown rather than claimed")
second = FixtureSource("paknsave").fetch()
again = diff_items(existing, [to_item(o) for o in second])
print(
    f"  a second full pass: added {again['added']}, changed {again['changed']}, "
    f"unchanged {again['unchanged']}"
)
note("`unchanged` equal to `fetched` is what idempotent looks like from the")
note("outside. put_item overwrites on key match, so a re-run is a no-op in")
note("content - and the diff is the evidence rather than the assertion.")

by_store = Counter(o.store_location for o in offers)
print(f"\n  This retailer's stores: {dict(by_store)}")
print(f"  store_key for each: {[store_key('paknsave', s) for s in sorted(by_store)]}")

# ---------------------------------------------------------------- aws mode
if mode == AWS:
    section("7. A dry-run refresh against grocery-products-dev")
    usable, detail = aws_identity()
    if not usable:
        raise SystemExit(
            blocked(
                "the dry-run refresh against DynamoDB",
                detail,
                "configure AWS credentials for the deployment account in "
                "ap-southeast-2, or re-run without DEMO_MODE=aws for the "
                "offline pipeline above",
            )
        )
    print(f"  credentials: {detail}\n")
    from ingestion.handler import refresh

    # PRICE_SOURCE, explicitly, because this run touches the REAL table.
    #
    # Without it resolve_source() returns the fixture catalogue, and since
    # 2026-09-04 `refresh()` REFUSES to point that at a table already holding
    # the collected one -- fixture product keys shadow the real ones, so the
    # write would serve invented prices (ingestion/guard.py). That refusal is
    # correct and this demo should not work around it: the deployed function
    # runs PRICE_SOURCE=lineage_b, so a demo of the real path sets the same
    # thing. Section 4 above already showed the fixture source, offline, where
    # it is the right catalogue to read.
    os.environ["PRICE_SOURCE"] = "lineage_b"

    step(1, "resolve_source('paknsave')  ->  the collected catalogue (recorded, never a live site)")
    step(2, "to_item() for every offer")
    step(3, "Query grocery-products-dev per store_key for the current rows")
    step(4, "diff  --  and STOP. dry_run=True writes nothing.")
    try:
        report = refresh("paknsave", dry_run=True)
    except Exception as exc:
        raise SystemExit(
            blocked(
                "the dry-run refresh against DynamoDB",
                f"{type(exc).__name__}: {str(exc)[:200]}",
                "check the table exists in ap-southeast-2 and the caller has "
                "dynamodb:Query on it (config/iam-ingestion-role.json)",
            )
        ) from exc

    print()
    for key in (
        "retailer",
        "table",
        "fetched",
        "written",
        "dry_run",
        "captured_at",
        "added",
        "changed",
        "unchanged",
    ):
        print(f"    {key:<12} {report[key]}")
    if report["sample_changed"]:
        print("\n    would change:")
        for change in report["sample_changed"]:
            for f in change["fields"]:
                print(f"      {change['key']}  {f['field']}: {f['from']} -> {f['to']}")
    note("")
    note("`written 0` and `dry_run True` together are the claim being made.")
    note("Run without dry_run to actually write - and run WITH it first")
    note("whenever the normaliser has changed.")
else:
    section("7. The DynamoDB write was NOT attempted in this mode")
    note("Everything above is pure: sources read committed files, the")
    note("normaliser and the diff are functions. The one step that needs an")
    note("account is comparing against the real table's current rows:")
    note("")
    note("    DEMO_MODE=aws python Philip_demo/09_ingestion_pipeline.py")
    note("")
    note("which still writes nothing - it calls refresh(dry_run=True).")

print("\nDone.")
