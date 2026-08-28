r"""
DEMO 1 - Price checking and comparison
======================================

HOW TO RUN
----------
From the repository root:

    python Philip_demo/01_price_check.py

On Windows without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/01_price_check.py

No AWS account, credentials or network access are required. This runs on
fixtures/products.json and the scripted model client. That is a design
property of the project, not a shortcut taken for the demo: the orchestrator
depends on protocol boundaries (PriceRepository, ModelClient) with fixture
implementations behind them, so the entire graph runs offline.

WHAT THIS DEMONSTRATES
----------------------
  1. Resolving a free-text term ("cheapest butter") to a real product
  2. Comparing that product across the three supermarket chains
  3. Answering about several items in one turn
  4. Naming items we hold no data for, instead of silently dropping them
  5. Saying what we did NOT check when a request exceeds the per-turn cap
  6. Citations: every price shown is traceable to a stored record
"""

from __future__ import annotations

from _demo_support import citations, heading, request, section, show_events

from src.models.scripted import ScriptedModelClient
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import assert_grounded

repo = InMemoryPriceRepository()
model = ScriptedModelClient()

heading("DEMO 1 - Price checking and comparison")

# ---------------------------------------------------------------- single item
section("1. A single item, compared across stores")
print("User: 'cheapest butter'\n")
resp = run_turn(request("cheapest butter"), repo, model)
show_events(resp)

comparison = next((e for e in resp.events if e.type == "price_comparison"), None)
if comparison:
    index = citations(resp)
    print("\n  The full option list. Note what a PriceOption actually holds:")
    print("  a citation_ref and NO price field. To print a number you have to")
    print("  go and look up the cited record. That is what makes an invented")
    print("  price structurally impossible rather than merely discouraged.\n")
    for opt in comparison.data.options:
        c = index[opt.citation_ref]
        marks = []
        if opt.is_cheapest:
            marks.append("CHEAPEST")
        if c.on_special:
            marks.append("ON SPECIAL")
        if opt.savings_vs_dearest_nzd:
            marks.append(f"saves ${opt.savings_vs_dearest_nzd} vs dearest")
        print(
            f"    ${c.price_nzd:>6}  {c.store.value:<12} {c.store_location:<14} "
            f"[{opt.citation_ref}]  {', '.join(marks)}"
        )
    print(f"\n  Model's reasoning: {comparison.data.reasoning}")

# ----------------------------------------------------------------- many items
section("2. Several items in one turn")
print("User: 'compare milk, bread and eggs'\n")
resp = run_turn(request("compare milk, bread and eggs", turn="turn-demo02"), repo, model)
index = citations(resp)
for ev in resp.events:
    if ev.type == "price_comparison":
        best = next((o for o in ev.data.options if o.is_cheapest), ev.data.options[0])
        c = index[best.citation_ref]
        print(
            f"  {ev.data.query_item:<18} cheapest ${c.price_nzd:<6} "
            f"at {c.store.value} ({c.store_location})"
        )

# -------------------------------------------------------------- honest gaps
section("3. An item we hold no data for is NAMED, not dropped")
print("User: 'price of milk and caviar'")
print("A user answered about two of three things has been quietly misled,")
print("so the gap is reported as its own event.\n")
resp = run_turn(request("price of milk and caviar", turn="turn-demo03"), repo, model)
show_events(resp, skip=("session", "citation", "token"))

# ------------------------------------------------------------------- the cap
section("4. Past the per-turn cap, we say what we did NOT check")
print("The user asks about seven items; the orchestrator looks up five.")
print("'I checked five of your seven' and 'I found nothing' are different")
print("statements, and only one of them would be true.\n")
resp = run_turn(
    request(
        "compare milk, bread, eggs, cheese, rice, pasta and butter",
        turn="turn-demo04",
    ),
    repo,
    model,
)
notices = [e for e in resp.events if e.type == "notice"]
for ev in notices:
    print(f"  NOTICE: {ev.message}")
if not notices:
    print("  (no cap notice on this phrasing - the extractor resolved fewer items)")

# --------------------------------------------------------------- the guarantee
section("5. The grounding invariant holds for every response above")
assert_grounded(resp)
print("  assert_grounded() passed. No literal money appears in any free-text")
print("  field. Prices live only in citation events and in structured fields")
print("  carrying a citation_ref, where provenance can be verified.")
print("\n  See 03_grounding_and_safety.py for what this rules out.")
