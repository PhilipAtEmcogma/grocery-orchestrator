r"""
DEMO 13 - The retrieval boundary: fixtures and DynamoDB behind one Protocol
===========================================================================

HOW TO RUN
----------
    python Philip_demo/13_retrieval_backends.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/13_retrieval_backends.py

Against the deployed table:

    DEMO_MODE=aws python Philip_demo/13_retrieval_backends.py

MODES
-----
    local  (default)  InMemoryPriceRepository over fixtures/products.json.
                      No AWS, no credentials, no network.
    aws               DynamoPriceRepository over grocery-products-dev, in
                      ap-southeast-2. READ ONLY - Query on the base table,
                      GSI1 and GSI2, and nothing else. Needs credentials with
                      the grants in config/iam-orchestrator-role.json.
                      The model plane stays SCRIPTED in both modes, so this
                      costs nothing in Bedrock and isolates the storage
                      question. Demo 14 is the one that calls Bedrock.

WHAT THIS DEMONSTRATES
----------------------
  1. The Protocol the graph depends on, and the two implementations of it
  2. Free-text term -> canonical product key, and the substring match that
     is deliberately refused
  3. cheapest_for_product: what GSI1's sort key buys
  4. candidates_for_budget: what GSI2 replaced, and why
  5. table_name flowing into citation provenance, and being checked
  6. AWS mode: the same calls against the real 2,759-row catalogue
  7. AWS mode: a whole turn through the graph on stored data

THE POINT
---------
Nodes depend on `PriceRepository`, never on boto3. That is what lets the
entire orchestrator be built and tested with no AWS account, and it is what
lets DEMO_MODE=aws below swap the storage layer without a single node
changing. The seam is the design, not a testing convenience.

ARCHITECTURE
------------
    graph node retrieve_prices
        v
    PriceRepository  (Protocol, src/retrieval/base.py)
        |
        +-- InMemoryPriceRepository   fixtures/products.json
        +-- DynamoPriceRepository     grocery-products-dev
                                        base table  (store_key, product_key)
                                        GSI1        product_key / padded price
                                        GSI2        category    / padded price
"""

from __future__ import annotations

import inspect
from decimal import Decimal

from _demo_support import (
    AWS,
    AWS_REGION,
    LOCAL,
    ModeUnavailable,
    aws_identity,
    blocked,
    citations,
    heading,
    mode_banner,
    note,
    request,
    resolve_mode,
    section,
    show_events,
    unpin_freshness,
)

from src.models.scripted import ScriptedModelClient
from src.retrieval.base import PriceRepository
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import assert_citations_match_retrieval

try:
    mode = resolve_mode(supports=(LOCAL, AWS))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

heading("DEMO 13 - The retrieval boundary: fixtures and DynamoDB behind one Protocol")

if mode == AWS:
    usable, detail = aws_identity()
    if not usable:
        mode_banner(mode, requires="AWS credentials", mocked="nothing was reached")
        raise SystemExit(
            blocked(
                "every DynamoDB call in this demo",
                detail,
                "configure AWS credentials for the deployment account in "
                f"{AWS_REGION}, or run without DEMO_MODE=aws to use fixtures",
            )
        )
    # The stored catalogue has its own capture date and must be judged against
    # the wall clock, exactly as production does. Importing _demo_support pins
    # the reference date to the FIXTURE snapshot, which would be the wrong
    # question to ask of real rows.
    unpin_freshness()
    from src.retrieval.dynamo import DynamoPriceRepository

    repo: PriceRepository = DynamoPriceRepository()
    mode_banner(
        mode,
        requires=f"credentials with dynamodb:Query on grocery-products-dev in {AWS_REGION}",
        mocked="the model plane (ScriptedModelClient). The price store is real.",
    )
    print(f"CALLER      {detail}")
else:
    repo = InMemoryPriceRepository()
    mode_banner(
        mode,
        requires="nothing - fixtures/products.json",
        mocked="the price store (fixtures) and the model plane (ScriptedModelClient)",
    )

model = ScriptedModelClient()
print(f"REPOSITORY  {type(repo).__name__}")
print(f"TABLE       {repo.table_name}")

# ---------------------------------------------------------------- the seam
section("1. What the graph is allowed to depend on")
methods = [
    name
    for name, obj in vars(PriceRepository).items()
    if not name.startswith("_") and (inspect.isfunction(obj) or isinstance(obj, property))
]
print(f"  PriceRepository, the whole Protocol: {sorted(methods)}\n")
print("  Neither implementation adds a public method the other lacks, and no")
print("  node imports either one by name. build_graph() takes the repository")
print("  as an argument:\n")
print("      g.add_node('retrieve_prices', partial(nodes.retrieve_prices, repo=repo))")
note("")
note("So swapping fixtures for DynamoDB is an argument, not a code change.")
note("The handler picks by environment - USE_DYNAMODB=1 - and demo 17 shows")
note("what happens when that variable goes missing in production.")

# -------------------------------------------------------------- resolution
section("2. Free text -> canonical key, and the match that is refused")
print(f"  {'user says':<26} {'resolves to':<26} ")
print(f"  {'-' * 26} {'-' * 26}")
for term in (
    "butter",
    "cheapest butter",
    "BUTTER  ",
    "milk",
    "truffle oil",
    "caviar",
    "wagyu ribeye",
):
    print(f"  {term!r:<26} {repo.resolve_product_key(term)!r:<26}")
note("")
note("Exact match after noise stripping, with NO substring fallback.")
note("'truffle oil' contains 'oil' and would resolve to canola oil under a")
note("substring rule, producing a confidently incorrect price. An")
note("unrecognised modifier must yield None so the caller takes the no_data")
note("path: under-matching is recoverable, mis-matching silently lies.")
note("")
note("The synonym table is config/product-synonyms.json - application logic")
note("(noisy free text -> canonical key), not storage logic, so both backends")
note("share the same one regardless of where the rows live.")

# ------------------------------------------------------------------- GSI1
section("3. cheapest_for_product - one query, already sorted")
key = repo.resolve_product_key("butter")
records = repo.cheapest_for_product(key, limit=5)
print(f"  cheapest_for_product({key!r}, limit=5)\n")
print(f"  {'price':>8}  {'store':<12} {'location':<16} {'captured':<12} special")
print(f"  {'-' * 8}  {'-' * 12} {'-' * 16} {'-' * 12} -------")
for r in records:
    print(
        f"  ${r.price_nzd:>7}  {r.store.value:<12} {r.store_location:<16} "
        f"{r.valid_date:<12} {r.on_special}"
    )
print(
    f"\n  ordered ascending: "
    f"{[str(r.price_nzd) for r in records] == sorted(str(r.price_nzd) for r in records)}"
)
note("")
note("GSI1 partitions by product_key and sorts on zero-padded cents, so the")
note("cheapest option is literally the first item DynamoDB returns. No")
note("application-side sort, and the query can stop early. Demo 9 section 3")
note("shows the key being built on the write side.")

# ------------------------------------------------------------------- GSI2
section("4. candidates_for_budget - what GSI2 replaced")
categories = (
    repo.all_categories
    if hasattr(repo, "all_categories")
    else ["produce", "dairy", "meat", "pantry", "bakery"]
)
if not isinstance(categories, list):
    categories = list(categories)
candidates = repo.candidates_for_budget(
    categories=categories,
    exclude_categories=["meat", "seafood"],
    limit_per_category=2,
    budget_nzd=Decimal("60"),
)
print(f"  categories asked for : {sorted(categories)}")
print("  excluded             : ['meat', 'seafood']   (a vegetarian request)")
print(f"  candidates returned  : {len(candidates)}\n")
for r in candidates[:12]:
    print(f"    {r.category:<10} ${r.price_nzd:>6}  {r.display_name[:34]:<34} {r.store.value}")
if len(candidates) > 12:
    print(f"    ... {len(candidates)} in total")
excluded_leaked = [r for r in candidates if r.category in {"meat", "seafood"}]
print(f"\n  excluded categories present in the result: {len(excluded_leaked)}")
note("")
note("One Query per wanted category against GSI2, which partitions by")
note("`category` and sorts by price. This replaced a full-table Scan, and the")
note("replacement was deferred until there was load evidence to choose it on:")
note("the catalogue went from 152 seeded rows to 2,759 real ones, and a Scan")
note("reads every row on every meal-plan turn to return about two dozen.")
note("")
note("dynamodb:Scan was then REMOVED from the orchestrator role rather than")
note("left unused. That turns the deploy into its own proof - a live meal")
note("plan succeeding afterwards cannot happen if anything still scans.")
note("")
note("The candidate pool is also capped to the budget BEFORE the model sees")
note("it, because the model cannot see prices and so cannot keep itself")
note("inside one. Demo 2 has the rest of that story.")

# -------------------------------------------------------------- provenance
section("5. table_name is provenance, and it is checked")
print(f"  repo.table_name = {repo.table_name!r}")
print("\n  Every Citation carries the table it came from, and run_turn calls")
print("  assert_citations_match_retrieval(response, table=repo.table_name,")
print("  records=<what retrieval actually returned>).\n")
resp = run_turn(request("cheapest butter", turn="turn-repo01"), repo, model)
index = citations(resp)
for ref, c in list(index.items())[:3]:
    print(f"    {ref}  table={c.source.table!r}")
    print(f"        pk={c.source.pk!r}  sk={c.source.sk!r}")
print("\n  run_turn already ran that assertion against the real record index,")
print("  which is why the call above returned at all. What it looks like when")
print("  the citations do NOT match what retrieval returned:\n")
try:
    assert_citations_match_retrieval(resp, table=repo.table_name, records={})
    print("    ...passed, which would be wrong.")
except AssertionError as exc:
    for line in str(exc).splitlines()[:4]:
        print(f"    {line}")
print("\n  An empty index with citations present fails EVERY citation, which is")
print("  the correct direction. A turn that emitted no citations has nothing")
print("  to prove and passes trivially.")
note("")
note("assert_grounded can only see the response, so it checks that source")
note("keys are SHAPED like keys. This one checks they ARE the keys of records")
note("retrieval actually returned, and that every published value equals the")
note("retrieved one. run_turn is the only place holding both.")

# ------------------------------------------------------------- a whole turn
section("6. A whole turn, on this backend")
print(f"  Repository: {type(repo).__name__}  Table: {repo.table_name}\n")
resp = run_turn(request("compare milk, bread and eggs", turn="turn-repo02"), repo, model)
show_events(resp, skip=("session", "citation", "token"))

plan = run_turn(
    request(
        "feed 3 people for 5 days on $80",
        turn="turn-repo03",
        household_size=3,
        budget_nzd=80,
        days=5,
    ),
    repo,
    model,
)
print()
show_events(plan, skip=("session", "citation", "token"))

if mode == LOCAL:
    section("7. DynamoDB was NOT reached in this mode")
    note("Everything above ran against fixtures/products.json. The exact same")
    note("assertions, the same graph and the same nodes run against the")
    note("deployed table with:")
    note("")
    note("    DEMO_MODE=aws python Philip_demo/13_retrieval_backends.py")
    note("")
    note("which is read-only - Query on the base table and the two indexes.")
    note("Nothing in this demo writes.")
else:
    section("7. What was actually reached")
    note(f"table   {repo.table_name} in {AWS_REGION}")
    note("calls   Query on the base table, GSI1 and GSI2. No Scan - the")
    note("        permission is not granted. No write of any kind.")
    note("model   still ScriptedModelClient, so no Bedrock spend and no")
    note("        question about which plane produced the numbers.")
    note("")
    note("If the counts above differ from the fixture run, that is the point:")
    note("the fixture catalogue is 152 invented rows and the table holds the")
    note("data team's 2,759 collected ones.")

print("\nDone.")
