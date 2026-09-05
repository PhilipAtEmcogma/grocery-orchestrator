r"""
DEMO 12 - Location scoping and price freshness
==============================================

HOW TO RUN
----------
    python Philip_demo/12_location_and_freshness.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/12_location_and_freshness.py

MODES
-----
    local  (default and only)  fixtures + the scripted model. No AWS, no
                               credentials, no network.

WHAT THIS DEMONSTRATES
----------------------
  1. Named regions resolved from free text, and why a region is a SET OF
     STORES rather than a centre and a radius
  2. The region has to be REMOVED from the message before item extraction
  3. An unrecognised region is reported, never silently ignored
  4. Radius scope for clients that do send coordinates, via haversine
  5. Both filters live INSIDE the repository, applied BEFORE the limit
  6. Freshness: the threshold as data, the capture date as fact, and the
     STALE_DATA turn that refuses to present old prices as current
  7. Why every offline entry point pins the reference date

THE RULE BOTH HALVES SHARE
--------------------------
Silently widening scope is the dangerous direction. A radius that quietly
grows returns the very stores the shopper ruled out; an unmapped region that
is quietly dropped answers a question about Whangarei with Auckland prices.
Both are answered here by refusing rather than by approximating.

ARCHITECTURE
------------
    message / Location
        v
    src.graph.regions      resolve_region, strip_region, locations_for
        v
    retrieve_prices node
        v
    PriceRepository.cheapest_for_product(near=..., locations=..., freshness=...)
        |
        +-- filters applied HERE, inside the repository, before `limit`
        v
    route_after_retrieval  ->  no_data | stale | unknown_region | comparison
"""

from __future__ import annotations

import os
from datetime import date, timedelta

from _demo_support import (
    LOCAL,
    ModeUnavailable,
    citations,
    heading,
    mode_banner,
    note,
    request,
    resolve_mode,
    section,
    show_events,
)

from src.graph.regions import known_regions, locations_for, resolve_region, strip_region
from src.models.scripted import ScriptedModelClient
from src.retrieval.filters import (
    AS_OF_ENV,
    FreshnessFilter,
    NearFilter,
    fixture_snapshot_date,
    haversine_km,
    max_price_age_days,
    reference_date,
)
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import Location

try:
    mode = resolve_mode(supports=(LOCAL,))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

heading("DEMO 12 - Location scoping and price freshness")
mode_banner(
    mode,
    requires="nothing - fixtures and two committed config files",
    mocked="the model plane (ScriptedModelClient) and the price store (fixtures)",
)

repo = InMemoryPriceRepository()
model = ScriptedModelClient()

# ------------------------------------------------------------------ regions
section("1. Named regions, resolved from free text")
print(f"  config/regions.json knows: {known_regions()}\n")
print(f"  {'phrase':<44} {'region':<18} store locations")
print(f"  {'-' * 44} {'-' * 18} ---------------")
for phrase in (
    "cheapest butter near Albany",
    "what's the price of milk on the North Shore",
    "compare bread in west auckland",
    "eggs near Newmarket please",
    "cheapest butter",
    "cheapest butter in Whangarei",
):
    region = resolve_region(phrase)
    if region:
        print(f"  {phrase:<44} {region.name:<18} {sorted(region.store_locations)}")
    else:
        print(f"  {phrase:<44} {'(none)':<18} -")
note("")
note("A region resolves to a SET OF STORE LOCATIONS rather than a centre and a")
note("radius. That is the better model of what someone means - 'North Shore'")
note("is the shops on the Shore - and it is also the only one that can run:")
note("the 3,000-record dataset carries no lat/lon on any row.")

print("\n  Matching is on word boundaries, longest alias first:")
for phrase in ("near Albany", "Albanyville has a shop", "north shore"):
    found = resolve_region(phrase)
    print(f"    {phrase!r:<28} -> {found.name if found else None}")

# ------------------------------------------------------------- strip_region
section("2. The region must be REMOVED before item extraction")
print(f"  {'original message':<44} extractor sees")
print(f"  {'-' * 44} --------------")
for phrase in (
    "cheapest butter near Albany",
    "compare milk and bread on the North Shore",
    "eggs in west auckland",
):
    print(f"  {phrase:<44} {strip_region(phrase)!r}")
note("")
note("'cheapest butter near Albany' extracted the item as 'butter albany',")
note("which resolves to nothing, so the turn returned no_data for a product")
note("we stock. The place is not part of the product name and the extractor")
note("has no way to know that - so the region is resolved from the ORIGINAL")
note("message and removed before the classifier ever sees it.")
note("")
note("Latent before regions existed: the query failed the same way and nobody")
note("noticed, because there was no way to ask for a region in the first place.")
note("")
note("Note the residue on the second row: 'on the' survives, because the")
note("preposition list is near|in|around|at|by and does not include 'on'. The")
note("region is still gone, which is what item extraction needs, so this is")
note("untidy rather than wrong - but it is real output, not a tidied example.")

# --------------------------------------------------------- unknown regions
section("3. An unrecognised region is reported, not dropped")
print(f"  locations_for('North Shore')  -> {sorted(locations_for('North Shore') or [])}")
print(f"  locations_for('Whangarei')    -> {locations_for('Whangarei')}")
note("")
note("The caller must NOT read None as 'no filter'. Ignoring an unrecognised")
note("region would answer a request about Whangarei with Auckland prices and")
note("give no sign the location was dropped.")

print("\n  Through the graph, with an explicit unmappable region:\n")
resp = run_turn(
    request(
        "cheapest butter",
        turn="turn-loc01",
        location=Location(region="Whangarei"),
    ),
    repo,
    model,
)
show_events(resp, skip=("session", "citation", "token"))

print("\n  And with one we do know:\n")
resp = run_turn(
    request(
        "cheapest butter near Albany",
        turn="turn-loc02",
        location=Location(region="North Shore"),
    ),
    repo,
    model,
)
index = citations(resp)
comparison = next((e for e in resp.events if e.type == "price_comparison"), None)
if comparison:
    for opt in comparison.data.options:
        c = index[opt.citation_ref]
        print(f"    ${c.price_nzd:>6}  {c.store.value:<12} {c.store_location}")
else:
    show_events(resp, skip=("session", "token"))
note("")
note("Scoped to the Shore. Compare with demo 1, where the same question")
note("unscoped returns options from every suburb in the catalogue.")

# ------------------------------------------------------------------ radius
section("4. Radius scope, for clients that do send coordinates")
albany = (-36.7280, 174.7000)
stores = {
    "Albany": (-36.7280, 174.7000),
    "Devonport": (-36.8300, 174.7960),
    "Sylvia Park": (-36.8912, 174.8437),
    "Papakura": (-37.0650, 174.9450),
}
near = NearFilter(lat=albany[0], lon=albany[1], radius_km=15.0)
print(f"  A shopper at Albany, radius {near.radius_km} km:\n")
print(f"  {'store':<16} {'distance':>10}   covered")
print(f"  {'-' * 16} {'-' * 10}   -------")
for name, (lat, lon) in stores.items():
    km = haversine_km(albany[0], albany[1], lat, lon)
    print(f"  {name:<16} {km:>7.1f} km   {near.covers(lat, lon)}")
note("")
note("NearFilter is frozen. A filter that could be mutated in transit is one")
note("that can silently WIDEN, and widening returns the very stores the")
note("shopper ruled out.")

print("\n  The value this replaced: 0.0/0.0 as a 'fail-closed' default.")
atlantic = haversine_km(albany[0], albany[1], 0.0, 0.0)
print(f"    distance from Albany to (0.0, 0.0): {atlantic:,.0f} km")
note("A real position in the Atlantic. Every record was excluded and the")
note("graph reported no_data - 'I don't have price data near you' about the")
note("supermarket down the road. See demo 10, section 4.")

# --------------------------------------------------------- inside the repo
section("5. Both filters are applied INSIDE the repository")
key = repo.resolve_product_key("butter")
unfiltered = repo.cheapest_for_product(key, limit=3)
scoped = repo.cheapest_for_product(key, limit=3, locations=frozenset({"Albany", "Devonport"}))
print(f"  cheapest_for_product({key!r}, limit=3)")
for r in unfiltered:
    print(f"    ${r.price_nzd:>6}  {r.store.value:<12} {r.store_location}")
print(f"\n  cheapest_for_product({key!r}, limit=3, locations={{Albany, Devonport}})")
for r in scoped:
    print(f"    ${r.price_nzd:>6}  {r.store.value:<12} {r.store_location}")
note("")
note("The filter is a repository PARAMETER, not something the node applies")
note("afterwards. If the caller filtered the returned three, a product whose")
note("three cheapest rows are all out of scope would come back empty and the")
note("graph would report no_data about a product stocked fresh nearby. That is")
note("the truncation defect Pilot Task 6 fixed for the store filter, and")
note("pushing these two down the same seam is what stops it coming back.")

# --------------------------------------------------------------- freshness
section("6. Freshness: a threshold that is data, a capture date that is fact")
snapshot = fixture_snapshot_date()
threshold = max_price_age_days()
print(f"  fixture capture date          {snapshot}")
print(f"  max_price_age_days            {threshold}   (config/freshness.json)")
print(f"  reference date for this run   {reference_date()}")
note("")
note("Raised from 14 to 45 on 2026-08-30 by the service owner, recorded in")
note("the config file itself. The rejected alternative was re-stamping the")
note("fixtures' valid_date to today - which fabricates provenance: those")
note("prices were invented on 2026-07-31, and a later stamp asserts a capture")
note("that never happened.")

print(f"\n  {'price captured':<16} {'age':>6}   fresh at {threshold} days?")
print(f"  {'-' * 16} {'-' * 6}   ------------------")
fresh_filter = FreshnessFilter(as_of=snapshot, max_age_days=threshold)
for offset in (0, 14, 45, 46, 120):
    captured = (snapshot - timedelta(days=offset)).isoformat()
    print(
        f"  {captured:<16} {fresh_filter.age_days(captured):>4}d   "
        f"{fresh_filter.is_fresh(captured)}"
    )

stale_as_of = snapshot + timedelta(days=threshold + 30)
print(f"\n  What a fully stale request does, judged as of {stale_as_of}:\n")
stale_filter = FreshnessFilter(as_of=stale_as_of, max_age_days=threshold)
kept = repo.cheapest_for_product(key, limit=5, freshness=stale_filter)
print(f"    records surviving the freshness filter: {len(kept)}")

# The same thing through the whole graph. The reference date is an env var
# precisely so it can be moved for a demonstration like this one; it is put
# back immediately, because a demo that leaves the clock moved would change
# the answer of every section after it.
os.environ[AS_OF_ENV] = stale_as_of.isoformat()
try:
    stale_resp = run_turn(request("cheapest butter", turn="turn-loc03"), repo, model)
finally:
    os.environ[AS_OF_ENV] = snapshot.isoformat()
print()
show_events(stale_resp, skip=("session", "token"))
note("")
note("When EVERY record for a request is stale the turn returns STALE_DATA")
note("naming the newest capture date it found, rather than presenting an old")
note("comparison as current. The claim the product makes is not 'here is a")
note("price' but 'here is the CHEAPEST price', and a comparison drawn from")
note("out-of-date prices can be actively wrong - the winner changes when a")
note("special rotates. ACQUISITION-RISK.md finds the Fair Trading Act attaches")
note("to the comparison published, not to the fetch.")

# ------------------------------------------------------------ the reference
section("7. Why every offline entry point pins the reference date")
today = date.today()
print(f"  today                     {today}")
print(f"  fixture capture           {snapshot}")
print(f"  unpinned age              {(today - snapshot).days} days")
print(f"  threshold                 {threshold} days")
age_today = (today - snapshot).days
if age_today > threshold:
    print("  so against the wall clock every fixture price is STALE, and has")
    print(f"  been for {age_today - threshold} days")
else:
    print("  so against the wall clock they are still fresh - for another")
    print(f"  {threshold - age_today} days, after which every demo turns red on a")
    print("  date nobody chose")
note("")
note("pin_to_fixture_snapshot() is called explicitly by _demo_support, by both")
note("eval harnesses and by the dev server - at the call site rather than")
note("inferred from which repository is wired, because a rule this")
note("consequential should be visible. Production sets nothing and gets the")
note("wall clock, which is the right answer for real ingested data.")
note("")
note("A suite whose result depends on the day you run it is not a suite.")

print("\nDone.")
