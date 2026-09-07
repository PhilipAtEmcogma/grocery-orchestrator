r"""
DEMO 31 - CAPSTONE: one question, every layer it touches
=========================================================

HOW TO RUN
----------
From the repository root:

    python Philip_demo/31_capstone.py

On Windows without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/31_capstone.py

No AWS account, credentials or network access. Everything below is the real
production code path with fixtures behind the repository seams.

MODES
-----
    local  (default and only)  fixtures plus the scripted model client.

WHAT THIS DEMONSTRATES
----------------------
The other thirty demos each take one layer. This one takes ONE SHOPPER
QUESTION and follows it the whole way down, stopping at every control that
gets a say in the answer -- so the shape of the system is visible rather than
described.

  1. The question, and what the service is being asked to promise
  2. INTENT       - and the refusal to invent a constraint
  3. RETRIEVAL    - the only place a Citation can be created
  4. FILTERS      - location and freshness, applied BEFORE the limit
  5. SELECTION    - the model's whole contribution, and its bounds
  6. ASSEMBLY     - deterministic scaling, and two different totals
  7. VALIDATION   - four independent checks, one of which re-derives money
  8. THE ANSWER   - the event stream a frontend receives
  9. WHAT WOULD HAVE STOPPED IT - every refusal that did not fire
 10. AND AROUND IT ALL - the operational layer this turn never sees

WHO THIS IS FOR
---------------
Everyone. It is the demo to run if you have time for exactly one, and the
narration is written to be read aloud.

EXPECTED RESULT
---------------
A complete, grounded meal plan, with every check printing OK -- including
`assert_costed_from_citations`, which re-derives every figure in the plan from
the cited prices rather than checking the plan's sums against each other.
Exit code 0.
"""

from __future__ import annotations

from _demo_support import (
    LOCAL,
    citations,
    heading,
    mode_banner,
    money,
    note,
    request,
    require,
    resolve_mode,
    section,
    show_events,
)

from src.models.scripted import ScriptedModelClient
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import (
    assert_arithmetic,
    assert_costed_from_citations,
    assert_grounded,
    assert_no_literal_money_in_response,
)

mode = resolve_mode(supports=(LOCAL,))
mode_banner(mode, requires="nothing", mocked="the model client and the price store")

heading("DEMO 31 - CAPSTONE: one question, every layer it touches")


# ------------------------------------------------------------ 1. the question

section("1. The question")

MESSAGE = "dinners for a flat of 3 for 5 days, no pork, we have about $120"
note(f'  "{MESSAGE}"')
note("")
note("What the service is being asked to promise is stronger than it looks:")
note("")
note("  - every price it quotes is one that was RETRIEVED, not generated")
note("  - the total is one the shopper can actually pay at a till")
note("  - 'no pork' is honoured or the request is REFUSED, never approximated")
note("  - if it cannot do those things it says so, rather than guessing")

repo = InMemoryPriceRepository()
model = ScriptedModelClient()
req = request(MESSAGE, turn="turn-capstone", household_size=3, days=5, budget_nzd=120)


# --------------------------------------------------------------- 2. intent

section("2. INTENT - and the refusal to invent a constraint")

note("classify_intent extracts household size, days, budget and exclusions.")
note("")
note("What it must NOT do is fill in a blank. Until 2026-08-29 a missing")
note("household size silently became 1 -- 'a plan for one person over one day'")
note("is a real answer to a question nobody asked, and downstream it is")
note("indistinguishable from one the shopper requested.")
note("")
note("Now a missing REQUIRED constraint produces a `clarification` event that")
note("names the ClientHints field a frontend should collect. Deliberately not")
note("an error: nothing failed, and `retryable` cannot express 'retry with")
note("more information' -- a client reading retryable:true resends the same")
note("request and loops.")


# ------------------------------------------------------------ 3. retrieval

section("3. RETRIEVAL - the only place a Citation can be created")

note("This is invariant 1, and it is enforced three independent ways:")
note("")
note("  1. TOPOLOGY   no edge in the graph skips retrieve_prices")
note("  2. TYPE       only the retrieval node constructs a Citation")
note("  3. ASSERTION  assert_grounded rejects a response referencing a ref")
note("                that was never declared")
note("")
note("Any one of the three could be defeated by a determined refactor. Three")
note("independent enforcements is what makes it a property rather than a")
note("convention.")


# -------------------------------------------------------------- 4. filters

section("4. FILTERS - applied BEFORE the limit, which is the whole point")

note("`cheapest_for_product` returns the cheapest N records. If location and")
note("freshness were applied AFTERWARDS, a product whose five cheapest rows")
note("were all out of radius or all stale would come back empty -- and the")
note("graph reads empty as `no_data`: 'I don't have price data for butter',")
note("about a product stocked fresh at the shop down the road.")
note("")
note("So both filters live INSIDE the repository, and all three")
note("implementations apply them before the limit. That ordering is the")
note("requirement, not an optimisation.")


# ------------------------------------------------------------ 5. selection

section("5. SELECTION - the model's whole contribution")

note("Retrieval shortlists recipes that are, all-or-nothing:")
note("")
note("  costable      every ingredient resolved to a real product")
note("  viable        dietary-safe judged from the RESOLVED products, not")
note("                from the recipe's NAME")
note("  affordable    as a SET, not individually -- recipes sharing rice and")
note("                onions cost far less together than apart")
note("")
note("Only then is the model asked anything, and what it is asked is narrow:")
note("pick ids. `RecipeSelection` has ONE field and it holds ids, so there is")
note("no place for a model to put a price even if it wanted to.")
note("")
note("A model that cannot SEE an uncostable recipe cannot select one. That is")
note("stronger than validating the selection afterwards.")


# ---------------------------------------------------- 6. run it, and assemble

section("6. ASSEMBLY - deterministic scaling, and TWO different totals")

response = run_turn(req, repo, model)
plans = [e for e in response.events if e.type == "meal_plan"]
require(plans, "the capstone turn did not produce a meal plan")
plan = plans[0].data
index = citations(response)

note(f"  meals            {len(plan.meals)}")
note(f"  citations        {len(index)}")
note(f"  consumption      {money(plan.total_nzd)}   what the food eaten is worth")
note(f"  payable          {money(plan.payable_total_nzd)}   what the till charges")
note(f"  within budget    {plan.within_budget}")
note("")
note("TWO TOTALS, because a shopper cannot buy 0.4 of a pack. Consumption is")
note("the value of what gets eaten; payable is whole packs at shelf price.")
note("Quoting only the first is a promise the shopper cannot keep at the till.")
note("")
note("Pack counts aggregate per product ACROSS meals and round up ONCE. Three")
note("meals each needing half a bag of rice is two bags if you round per meal,")
note("and one bag if you do it correctly.")


# ------------------------------------------------------------ 7. validation

section("7. VALIDATION - four checks, and why the fourth exists")

checks = [
    ("assert_grounded", lambda: assert_grounded(response), "every ref resolves, in order"),
    (
        "assert_no_literal_money_in_response",
        lambda: assert_no_literal_money_in_response(response),
        "no price in free text",
    ),
    ("assert_arithmetic", lambda: assert_arithmetic(plan), "the four sums agree"),
    (
        "assert_costed_from_citations",
        lambda: assert_costed_from_citations(plan, index),
        "every figure RE-DERIVED from cited prices",
    ),
]
for name, fn, why in checks:
    try:
        fn()
        note(f"  [OK  ] {name:36} {why}")
    except Exception as exc:
        note(f"  [FAIL] {name:36} {exc}")
        require(False, f"{name} failed")

note("")
note("THE FOURTH IS THE ONE THAT MATTERS. `assert_arithmetic` checks that four")
note("sums agree WITH EACH OTHER -- and a line cost that is wrong by")
note("construction propagates consistently through all four and passes every")
note("one. `assert_costed_from_citations` re-derives each figure from the")
note("cited prices instead: line cost = price x packs, packs aggregated across")
note("meals and rounded up once, basket totals at shelf price, and every")
note("citation in a basket really at the store it names.")
note("")
note("SHAPE IS NOT IDENTITY. The same argument produced")
note("assert_citations_match_retrieval, after a citation naming the right")
note("table with a plausible key and a price nobody retrieved passed cleanly.")


# ------------------------------------------------------------- 8. the answer

section("8. THE ANSWER - the event stream a frontend receives")

show_events(response)


# --------------------------------------------- 9. the refusals that did not fire

section("9. WHAT WOULD HAVE STOPPED IT - refusals that did not fire today")

note("Each of these is a path this turn could have taken. None is an error")
note("case: every one is a SUCCESSFUL response that declines to answer.")
note("")
note("  clarification        a required constraint was not stated")
note("  unsupported_exclusion  a diet we cannot verify against the catalogue")
note("  no_data              nothing retrievable for the request")
note("  STALE_DATA           everything retrievable is too old to stand behind")
note("  budget_infeasible    the budget genuinely does not stretch")
note("  guardrail            content policy refused the turn")
note("  upstream_failure     the model plane could not answer")
note("")
note("The design rule is that honest failure is a FIRST-CLASS OUTCOME. A")
note("system that must always produce a plan will eventually produce one it")
note("cannot defend -- and this whole repository exists to prevent exactly")
note("that failure.")


# ---------------------------------------------- 10. the operational layer

section("10. AND AROUND IT ALL - what this turn never sees")

note("The turn above is the product. Everything below is what makes it")
note("operable, and none of it is visible from inside a single request:")
note("")
note("  contract      one ChatResponse shape on every path, including 500")
note("  idempotency   owner-fenced claims, canonical request fingerprint")
note("  observability EMF metrics and X-Ray, with Req 11.5 forbidding message")
note("                text, location and dietary data in every log line")
note("  model plane   task-based routing, capability-aware requests, and a")
head = "                qualification gate: no unscored model is routable"
note(head)
note("  SSM routing   which model serves which task, retunable without a")
note("                deploy -- and unable to enable a model  (demo 26)")
note("  throttling    a quota breach distinguishable from an outage (demo 25)")
note("  ingestion     validated, diffed and rejected BEFORE it writes")
note("  stream guard  every write to the catalogue watched, whoever made it,")
note("                because refresh() cannot see a write it did not make")
note("                (demo 27)")
note("  IaC           six stacks; the ingestion plane codified 2026-09-07,")
note("                the last live plane with no template  (demo 28)")
note("  evals         five suites gated in CI, plus a variety measure that")
note("                ranks a MENU rather than a rule  (demo 29)")
note("  guardrails    verified by breaking them, twice found inert  (demo 30)")
note("")
note("THE THROUGH-LINE. Every one of those is the same idea in a different")
note("place: a claim is worth what its evidence is worth. A price without a")
note("citation, a metric without an alarm, an alarm without a drill, a")
note("parameter nobody reads, a test that cannot fail, a document nobody")
note("re-checked -- each is a claim that outran its evidence, and each has")
note("been found and fixed in this repository at least once.")

print("\nDone.")
