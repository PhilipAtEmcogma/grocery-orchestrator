r"""
DEMO 19 - End to end: one question through every layer
======================================================

HOW TO RUN
----------
    python Philip_demo/19_end_to_end.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/19_end_to_end.py

Through the deployed HTTPS endpoint:

    DEMO_MODE=integration python Philip_demo/19_end_to_end.py

Through the deployed DynamoDB table, in this process:

    DEMO_MODE=aws python Philip_demo/19_end_to_end.py
    USE_BEDROCK=1 BEDROCK_GUARDRAIL_ID=... BEDROCK_GUARDRAIL_VERSION=2 \
        DEMO_MODE=aws python Philip_demo/19_end_to_end.py

MODES
-----
    local        (default)  fixtures + scripted model, in this process.
                            No AWS, no credentials, no network.
    aws                     DynamoPriceRepository against grocery-products-dev,
                            in this process, so every stage stays visible.
                            The model plane stays SCRIPTED unless USE_BEDROCK=1
                            is exported explicitly - and the banner says which.
                            Needs dynamodb:Query, plus Bedrock grants if you
                            opt into the model.
    integration             POST to the deployed API Gateway endpoint. The
                            whole deployed stack answers, and this process can
                            only see what a client sees - which is the point of
                            running it as well as the others.

WHAT THIS DEMONSTRATES
----------------------
The complete path, stage by stage, for two turns:

  1. Which implementation each layer is bound to, in THIS run
  2. A price check, traced through every stage
  3. A meal plan, traced, including the arithmetic the model never did
  4. The four assertions that ran, and what each one can see
  5. The telemetry a turn produces
  6. A ledger of what was real and what was a stand-in

WHY THIS FILE IS LAST
---------------------
Demos 1-18 each take one seam and open it. This one does not open anything;
it shows the seams closed, in order, so the shape of the whole is visible.
Read it after at least demo 1, 2 and 3, or the stage names will not mean much.

THE PATH
--------
    ChatRequest (contract-validated)
        v
    lambda_handler        error boundary, CORS, status codes
        v
    idempotency claim     conditional write, owner-fenced
        v
    run_turn -> build_graph(repo, model)
        v
    validate_input -> classify_intent ---------------> model call 1
        v
    retrieve_prices       <- THE ONLY SOURCE OF PRICES
        v
    generate_comparison | generate_plan -------------> model call 2
        v                       v
        |                  validate_plan -> repair? -> model call 3..n
        v                       v
    generate_prose -------------+--------------------> model call n+1
        v
    finalise
        v
    assert_grounded
    assert_no_model_authored_money
    assert_citations_match_retrieval
    assert_arithmetic
        v
    idempotency complete -> ChatResponse -> EMF metrics
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from decimal import Decimal

from _demo_support import (
    AWS,
    AWS_REGION,
    INTEGRATION,
    LOCAL,
    ModeUnavailable,
    aws_identity,
    blocked,
    citations,
    endpoint_url,
    heading,
    mode_banner,
    note,
    request,
    resolve_mode,
    section,
    step,
    unpin_freshness,
)

from src.models.scripted import ScriptedModelClient
from src.observability.base import NULL_TELEMETRY, TurnStats
from src.observability.instrumented import (
    InstrumentedModelClient,
    InstrumentedPriceRepository,
)
from src.retrieval.memory import InMemoryPriceRepository
from src.runner import run_turn
from src.schemas.contract import (
    ChatResponse,
    assert_arithmetic,
    assert_grounded,
    assert_no_model_authored_money,
)

try:
    mode = resolve_mode(supports=(LOCAL, AWS, INTEGRATION))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

PACE_SECONDS = 8.0
PRICE_QUESTION = "cheapest butter"
PLAN_QUESTION = "feed 3 people for 5 days on $80"

heading("DEMO 19 - End to end: one question through every layer")

# --------------------------------------------------------------------------
# Bind the layers for this mode. Nothing below here is conditional on the
# mode again except the transport, which is the point being made.
# --------------------------------------------------------------------------

repo = model = None
layers: list[tuple[str, str, str]] = []

if mode == INTEGRATION:
    URL = endpoint_url()
    mode_banner(
        mode,
        requires=f"network access to {URL}. No AWS credentials.",
        mocked="nothing. The deployed stack answers, and this process is a client.",
    )
    print(f"ENDPOINT    {URL}")
    layers = [
        ("transport", "HTTPS -> API Gateway -> Lambda alias :live", "real"),
        ("prices", "grocery-products-dev", "real"),
        ("model", "Bedrock, per config/models.json routing", "real"),
        ("guardrail", "attached to every generation call", "real"),
        ("idempotency", "grocery-idempotency-dev, TTL, owner-fenced", "real"),
        ("telemetry", "CloudWatch Logs, EMF, 8 alarms", "real, and not visible here"),
    ]
elif mode == AWS:
    usable, detail = aws_identity()
    if not usable:
        mode_banner(mode, requires="AWS credentials", mocked="nothing was reached")
        raise SystemExit(
            blocked(
                "every AWS call in this demo",
                detail,
                f"configure AWS credentials for the deployment account in {AWS_REGION}, "
                "or run without DEMO_MODE=aws",
            )
        )
    unpin_freshness()
    from src.retrieval.dynamo import DynamoPriceRepository

    repo = DynamoPriceRepository()
    use_bedrock = os.environ.get("USE_BEDROCK") == "1"
    if use_bedrock:
        from src.models.bedrock import BedrockModelClient

        model = BedrockModelClient()
    else:
        model = ScriptedModelClient()
    mode_banner(
        mode,
        requires=f"dynamodb:Query on grocery-products-dev in {AWS_REGION}"
        + (", plus Bedrock grants" if use_bedrock else ""),
        mocked=(
            "nothing"
            if use_bedrock
            else "the model plane (ScriptedModelClient). Export USE_BEDROCK=1 to "
            "use Bedrock; it is not assumed."
        ),
    )
    print(f"CALLER      {detail}")
    layers = [
        ("transport", "an in-process call to run_turn", "local"),
        ("prices", f"DynamoPriceRepository -> {repo.table_name}", "real"),
        ("model", type(model).__name__, "real" if use_bedrock else "stand-in"),
        (
            "guardrail",
            "attached by BedrockModelClient" if use_bedrock else "not reached",
            "real" if use_bedrock else "n/a",
        ),
        ("idempotency", "not exercised - run_turn is below the handler", "n/a"),
        ("telemetry", "TurnStats in this process", "local"),
    ]
else:
    repo = InMemoryPriceRepository()
    model = ScriptedModelClient()
    mode_banner(
        mode,
        requires="nothing - fixtures/products.json and the scripted client",
        mocked="the price store (fixtures) and the model plane (scripted)",
    )
    layers = [
        ("transport", "an in-process call to run_turn", "local"),
        ("prices", "InMemoryPriceRepository -> fixtures/products.json", "stand-in"),
        ("model", "ScriptedModelClient", "stand-in"),
        ("guardrail", "not reached - no Bedrock call is made", "n/a"),
        ("idempotency", "not exercised - run_turn is below the handler", "n/a"),
        ("telemetry", "TurnStats in this process", "local"),
    ]

_last_call = [float("-inf")]


def ask(message: str) -> tuple[ChatResponse | dict, TurnStats | None, float]:
    """
    One turn, in whichever mode this is.

    Returns a ChatResponse in-process, or the decoded JSON payload over HTTP -
    which is exactly the asymmetry worth showing: a client cannot see the
    stages, only the result.
    """
    if mode == INTEGRATION:
        wait = PACE_SECONDS - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()
        token = uuid.uuid4().hex[:12]
        body = json.dumps(
            {
                "version": "1.0",
                "session_id": f"sess-e2e{token}",
                "turn_id": f"turn-e2e{token}",
                "message": message,
            }
        ).encode()
        req = urllib.request.Request(  # noqa: S310 - fixed https endpoint
            URL, data=body, headers={"Content-Type": "application/json"}
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=60) as response:  # noqa: S310
                return json.loads(response.read()), None, time.perf_counter() - started
        except urllib.error.HTTPError as exc:
            return json.loads(exc.read()), None, time.perf_counter() - started

    stats = TurnStats()
    instrumented_repo = InstrumentedPriceRepository(repo, NULL_TELEMETRY, stats)
    instrumented_model = InstrumentedModelClient(model, NULL_TELEMETRY, stats)
    started = time.perf_counter()
    response = run_turn(
        request(message, turn=f"turn-e2e{uuid.uuid4().hex[:8]}"),
        instrumented_repo,
        instrumented_model,
    )
    return response, stats, time.perf_counter() - started


def events_of(result) -> list:
    return result["events"] if isinstance(result, dict) else result.events


def kind(event) -> str:
    return event["type"] if isinstance(event, dict) else event.type


# ------------------------------------------------------------------ layers
section("1. What each layer is bound to, in THIS run")
print(f"  {'layer':<14} {'implementation':<50} status")
print(f"  {'-' * 14} {'-' * 50} ------")
for name, implementation, status in layers:
    print(f"  {name:<14} {implementation[:50]:<50} {status}")
note("")
note("Every one of those bindings is an argument or an environment variable,")
note("never a code path. build_graph(repo, model) takes both; the handler")
note("chooses by environment. That is why the same file can run against")
note("fixtures on a laptop and against the account, and why demo 17 exists to")
note("make sure the choice cannot be made by accident in production.")

# ------------------------------------------------------------- price check
section("2. A price check, stage by stage")
print(f"  Shopper: {PRICE_QUESTION!r}\n")

step(1, "ChatRequest is validated by the contract before anything runs")
step(2, "classify_intent  ->  model call: intent + extracted constraints")
step(3, "retrieve_prices  ->  the ONLY place a price can enter the system")
step(4, "generate_comparison  ->  cheapest computed in Python, from records")
step(5, "generate_prose  ->  placeholders only, degraded if it breaks protocol")
step(6, "finalise  ->  four assertions, then the response")

result, stats, seconds = ask(PRICE_QUESTION)
print(
    f"\n  {seconds:.2f}s   {len(events_of(result))} events: "
    f"{[kind(e) for e in events_of(result)]}\n"
)

if isinstance(result, dict):
    for event in events_of(result):
        if event["type"] == "citation":
            c = event["citation"]
            print(
                f"    {c['ref']}  ${float(c['price_nzd']):>7.2f}  "
                f"{c['product_name'][:30]:<30} {c['store']}/{c['store_location']}"
            )
        elif event["type"] == "price_comparison":
            print(
                f"\n    comparison over {len(event['data']['options'])} options; "
                f"reasoning: {event['data']['reasoning'][:70]}..."
            )
else:
    index = citations(result)
    comparison = next((e for e in result.events if e.type == "price_comparison"), None)
    if comparison:
        for option in comparison.data.options:
            c = index[option.citation_ref]
            marks = "CHEAPEST" if option.is_cheapest else ""
            print(
                f"    {option.citation_ref}  ${c.price_nzd:>7}  "
                f"{c.product_name[:30]:<30} {c.store.value}/{c.store_location}  {marks}"
            )
        print(f"\n    reasoning: {comparison.data.reasoning[:80]}...")
note("")
note("A PriceOption carries a citation_ref and NO price. To print a number you")
note("have to go and look up the cited record, which is the step that makes an")
note("invented price structurally impossible rather than merely discouraged.")

# ---------------------------------------------------------------- the plan
section("3. A meal plan, and the arithmetic the model never attempted")
print(f"  Shopper: {PLAN_QUESTION!r}\n")
step(1, "classify_intent  ->  household 3, days 5, budget $80")
step(2, "retrieve_prices  ->  candidates per category, capped to the budget")
step(3, "render_products  ->  a table of refs and pack sizes, NO PRICES")
step(4, "generate_plan    ->  the model returns refs and pack multipliers")
step(5, "validate_plan    ->  Python multiplies, sums, compares to budget")
step(6, "repair if needed ->  bounded, with every constraint restated")
step(7, "generate_prose, finalise, assertions")

plan_result, plan_stats, plan_seconds = ask(PLAN_QUESTION)
plan_event = next((e for e in events_of(plan_result) if kind(e) == "meal_plan"), None)
print(f"\n  {plan_seconds:.2f}s   {len(events_of(plan_result))} events\n")

if plan_event is None:
    print("    No plan event. The terminal event was:")
    for event in events_of(plan_result):
        if kind(event) in ("error", "clarification", "no_data"):
            body = event if isinstance(event, dict) else event.model_dump(mode="json")
            print(f"      {json.dumps(body)[:220]}")
    note("")
    note("Which is a result, not a failure of the demo. Demo 4 is the file")
    note("about why each terminal path says something true.")
else:
    plan = plan_event["data"] if isinstance(plan_event, dict) else plan_event.data
    meals = plan["meals"] if isinstance(plan, dict) else plan.meals
    total = Decimal(str(plan["total_nzd"] if isinstance(plan, dict) else plan.total_nzd))
    budget = Decimal(str(plan["budget_nzd"] if isinstance(plan, dict) else plan.budget_nzd))
    repairs = plan["repair_attempts"] if isinstance(plan, dict) else plan.repair_attempts
    print(f"    {len(meals)} meals   ${total} of ${budget}   repairs: {repairs}\n")
    for meal in meals[:4]:
        name = meal["name"] if isinstance(meal, dict) else meal.name
        serves = meal["serves"] if isinstance(meal, dict) else meal.serves
        lines = meal["ingredients"] if isinstance(meal, dict) else meal.ingredients
        print(f"      {name[:46]:<46} serves {serves}, {len(lines)} ingredients")
    if len(meals) > 4:
        print(f"      ... {len(meals)} meals in total")
    print(
        f"\n    within_budget: "
        f"{plan['within_budget'] if isinstance(plan, dict) else plan.within_budget}"
        f"   headroom ${budget - total}"
    )
note("")
note("The model chose products and wrote meal names. It never saw a price,")
note("has no field to put one in, and did not add anything up. Every figure")
note("above was computed by Python from records retrieval returned.")

# ------------------------------------------------------------- assertions
section("4. The four assertions, and what each one can see")
checks = (
    (
        "assert_grounded",
        "no literal money in any free-text field; source keys are SHAPED like keys",
        "the response alone",
    ),
    (
        "assert_no_model_authored_money",
        "narrower backstop over the model-authored fields of a plan",
        "the response alone",
    ),
    (
        "assert_citations_match_retrieval",
        "every citation IS a record retrieval returned, and every value equals it",
        "the response AND the retrieval index",
    ),
    ("assert_arithmetic", "every total is the sum of its parts, recomputed", "the plan alone"),
)
print(f"  {'assertion':<34} {'sees':<34} what it proves")
print(f"  {'-' * 34} {'-' * 34} --------------")
for name, proves, sees in checks:
    print(f"  {name:<34} {sees:<34} {proves[:44]}")

if not isinstance(result, dict):
    print("\n  Re-running them here, on the responses above:\n")
    assert_grounded(result)
    assert_no_model_authored_money(result)
    print("    price check: assert_grounded, assert_no_model_authored_money  PASS")
    if plan_event is not None:
        assert_grounded(plan_result)
        assert_no_model_authored_money(plan_result)
        # assert_arithmetic takes the MealPlan itself, not the response: it is
        # run at validate_plan, BEFORE the event exists, because a plan whose
        # totals do not add up should trigger a repair rather than reach a user.
        assert_arithmetic(plan_event.data)
        print("    meal plan:   the same two, plus assert_arithmetic             PASS")
    print("\n    (assert_citations_match_retrieval already ran inside run_turn,")
    print("     which is the only place holding both the response and the")
    print("     retrieval index it was built from.)")
else:
    print("\n  Over HTTPS these ran INSIDE the Lambda, before the response was")
    print("  returned. A client cannot re-run assert_citations_match_retrieval,")
    print("  because the retrieval index never leaves the process - which is")
    print("  why it is asserted there rather than trusted here.")
note("")
note("Two of them can only be checked in one place, and that is a design")
note("statement: a client cannot verify grounding, so the service must not be")
note("able to emit an ungrounded response in the first place.")

# -------------------------------------------------------------- telemetry
section("5. What the turn produced, operationally")
if stats is not None and plan_stats is not None:
    print(f"  {'':<22} {'price check':>12} {'meal plan':>12}")
    print(f"  {'-' * 22} {'-' * 12} {'-' * 12}")
    for label, attribute in (
        ("model calls", "model_calls"),
        ("plan calls", "plan_calls"),
        ("repair attempts", "repair_attempts"),
        ("retrieval calls", "retrieval_calls"),
        ("input tokens", "input_tokens"),
        ("output tokens", "output_tokens"),
    ):
        print(
            f"  {label:<22} {getattr(stats, attribute)!s:>12} "
            f"{getattr(plan_stats, attribute)!s:>12}"
        )
    print(f"  {'model ms':<22} {stats.model_ms:>12.3f} {plan_stats.model_ms:>12.3f}")
    print(f"  {'retrieval ms':<22} {stats.retrieval_ms:>12.3f} {plan_stats.retrieval_ms:>12.3f}")
    print(
        f"  {'models used':<22} {','.join(stats.models_used):>12} "
        f"{','.join(plan_stats.models_used):>12}"
    )
    note("")
    note("Per turn, never global. A Lambda execution environment serves many")
    note("turns, and a counter that survived between them would report the")
    note("wrong number from the second invocation onwards.")
else:
    note("Not visible from here. Over HTTPS the same counters were emitted as")
    note("EMF records into CloudWatch Logs inside the Lambda, and this process")
    note("saw only the response. Demo 7 shows them in process; the dashboard")
    note("`grocery-orchestrator-dev` shows them aggregated.")

# ----------------------------------------------------------------- ledger
section("6. The ledger: what was real in this run")
print(f"  {'layer':<14} {'status':<12} meaning")
print(f"  {'-' * 14} {'-' * 12} -------")
meaning = {
    "real": "an actual deployed resource answered",
    "stand-in": "a deterministic local implementation answered",
    "local": "ran in this process",
    "n/a": "was not part of this path",
    "real, and not visible here": "happened, but inside the Lambda",
}
for name, _implementation, status in layers:
    print(f"  {name:<14} {status:<12} {meaning.get(status, status)}")

print("\n  The three runs answer three different questions:\n")
print("    local        does the orchestration work at all, deterministically")
print("    aws          does it work against the real catalogue")
print("    integration  does the DEPLOYED thing work, as a client sees it")
note("")
note("None of them substitutes for another. A green local run says nothing")
note("about the account; a green integration run says nothing about which")
note("configuration produced it, which is what demo 17 section 9 is for.")
note("")
note("That is the same lesson this repository keeps arriving at from")
note("different directions: evidence is only about the thing it was collected")
note("from, and a claim that looks verified because other claims agree with")
note("it has not been verified at all.")

print("\nDone.")
