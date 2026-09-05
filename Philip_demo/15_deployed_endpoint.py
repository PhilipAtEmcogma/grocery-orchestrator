r"""
DEMO 15 - The deployed service, over HTTPS
==========================================

HOW TO RUN
----------
    python Philip_demo/15_deployed_endpoint.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/15_deployed_endpoint.py

Against the real deployed endpoint (network required; SPENDS A FEW CENTS of
Bedrock and Lambda):

    DEMO_MODE=integration python Philip_demo/15_deployed_endpoint.py

MODES
-----
    local        (default)  the same requests through the LOCAL handler, in
                            process. No network, no credentials, no spend.
    integration             POST to the deployed API Gateway endpoint over
                            HTTPS. Needs NETWORK ACCESS but NO AWS
                            CREDENTIALS - the dev endpoint is unauthenticated
                            (docs/ARCHITECTURE.md section 7). Override the URL
                            with CHAT_ENDPOINT_URL.

PACING. Six turns, spaced. The account's binding Nova Lite quota is 20
requests per minute and CANNOT be raised, and one turn costs 2-4 Bedrock
calls. An unpaced run measures the quota rather than the service: throttled
calls fail at the TAIL of a burst, so the numbers read as "the service got
slow" when they mean "the account stopped answering".

WHAT THIS DEMONSTRATES
----------------------
  1. The wire contract - exactly what a frontend sends and receives
  2. A price check answered by the deployed stack, with capture dates
  3. Region scoping working in production
  4. A meal plan, and the clarification when no budget was stated
  5. Malformed input answered with a contract-valid body, not a stack trace
  6. Idempotency across the network: a replayed turn_id returns the CACHED
     response rather than re-running the graph
  7. Client-observed latency, and why it is not the same number CloudWatch
     reports

WHAT THE DEPLOYED STACK IS THAT LOCAL IS NOT
---------------------------------------------
    local                          deployed
    -------------------------      ---------------------------------
    fixtures/products.json         grocery-products-dev, 2,759 real rows
    ScriptedModelClient            Bedrock Nova Lite / Nova Pro
    no guardrail                   Guardrail b1xezpqe04kx, numbered version
    InMemoryIdempotencyStore       grocery-idempotency-dev, TTL, owner-fenced
    print() to stdout              CloudWatch Logs, EMF metrics, 8 alarms
    a Python call                  API Gateway -> Lambda alias :live, SnapStart

ARCHITECTURE
------------
    this demo (an HTTP client)
        v  POST /dev/chat, application/json
    API Gateway REST  grocery-orchestrator-api-dev   5 rps / burst 10
        v  invoke alias
    Lambda grocery-orchestrator-dev:live             SnapStart, X-Ray
        v
    the same lambda_handler demo 6 drives in process
        v
    DynamoDB + Bedrock + CloudWatch
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

from _demo_support import (
    INTEGRATION,
    LOCAL,
    ModeUnavailable,
    endpoint_url,
    heading,
    mode_banner,
    note,
    resolve_mode,
    section,
    step,
)

try:
    mode = resolve_mode(supports=(LOCAL, INTEGRATION))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

#: Seconds between turns. 6 requests at 8s apart is under the 20/min ceiling
#: even at 4 Bedrock calls per turn.
PACE_SECONDS = 8.0
TIMEOUT_SECONDS = 40.0

URL = endpoint_url()

heading("DEMO 15 - The deployed service, over HTTPS")
if mode == INTEGRATION:
    mode_banner(
        mode,
        requires=f"network access to {URL}. No AWS credentials.",
        mocked="nothing. Real Lambda, real DynamoDB, real Bedrock, real Guardrail.",
    )
    print(f"ENDPOINT    {URL}")
    print(f"PACING      one turn every {PACE_SECONDS:.0f}s")
else:
    mode_banner(
        mode,
        requires="nothing - the handler runs in this process",
        mocked="the price store (fixtures), the model plane (scripted), and the network",
    )
    print("ENDPOINT    (none - lambda_handler is called directly)")


_last_call = [float("-inf")]


def turn(
    message: str,
    *,
    turn_id: str | None = None,
    session: str | None = None,
    raw_body: str | None = None,
) -> tuple[int, dict, float]:
    """
    One turn. Returns (status, payload, seconds).

    The SAME request body in both modes: locally it becomes an API Gateway
    event handed straight to lambda_handler, and over the network it is the
    POST body. That is the point of the exercise - one contract, two
    transports.
    """
    token = uuid.uuid4().hex[:12]
    body = (
        raw_body
        if raw_body is not None
        else json.dumps(
            {
                "version": "1.0",
                "session_id": session or f"sess-d15{token}",
                "turn_id": turn_id or f"turn-d15{token}",
                "message": message,
            }
        )
    )

    if mode == LOCAL:
        from src.handler import lambda_handler

        started = time.perf_counter()
        result = lambda_handler({"httpMethod": "POST", "body": body})
        return result["statusCode"], json.loads(result["body"]), time.perf_counter() - started

    wait = PACE_SECONDS - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()

    request = urllib.request.Request(  # noqa: S310 - fixed https endpoint
        URL, data=body.encode(), headers={"Content-Type": "application/json"}
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.status, json.loads(response.read()), time.perf_counter() - started
    except urllib.error.HTTPError as exc:
        # A 4xx still carries a contract-valid body. That is section 5's whole
        # point, so it is read rather than raised.
        return exc.code, json.loads(exc.read()), time.perf_counter() - started


def summarise(status: int, payload: dict, seconds: float) -> None:
    kinds = [e["type"] for e in payload.get("events", [])]
    print(f"      HTTP {status}   {seconds:.2f}s   events={kinds}")
    for event in payload.get("events", []):
        if event["type"] == "citation":
            c = event["citation"]
            print(
                f"        {c['ref']}  ${float(c['price_nzd']):>7.2f}  "
                f"{c['product_name'][:32]:<32} {c['store']}/{c['store_location']}  "
                f"captured {c['valid_date']}"
            )
        elif event["type"] == "price_comparison":
            print(
                f"        comparison: {event['data']['query_item']}, "
                f"{len(event['data']['options'])} options"
            )
        elif event["type"] == "meal_plan":
            plan = event["data"]
            print(
                f"        plan: {len(plan['meals'])} meals, ${plan['total_nzd']} "
                f"of ${plan['budget_nzd']}, within_budget={plan['within_budget']}, "
                f"repairs={plan['repair_attempts']}"
            )
        elif event["type"] == "error":
            print(f"        error {event['code']} retryable={event['retryable']}")
            print(f"          {event['message'][:110]}")
        elif event["type"] == "clarification":
            print(f"        clarification, missing={event['missing']}")
            print(f"          {event['message'][:110]}")
        elif event["type"] == "no_data":
            print(f"        no_data: {event['requested_item']}")


# --------------------------------------------------------------- the contract
section("1. The wire contract")
print("  Exactly what a frontend POSTs:\n")
print(
    json.dumps(
        {
            "version": "1.0",
            "session_id": "sess-d15abc123",
            "turn_id": "turn-d15abc123",
            "message": "cheapest butter",
        },
        indent=2,
    )
)
note("")
note("session_id and turn_id have an 8-character minimum. turn_id is")
note("client-generated and is the idempotency key - section 6.")
note("`location` and `hints` are optional; FRONTEND-INTEGRATION.md is the")
note("document the frontend team works from.")

# ------------------------------------------------------------- a price check
section("2. A price check")
step(1, "POST 'cheapest butter'")
summarise(*turn("cheapest butter"))
note("")
if mode == INTEGRATION:
    note("Those capture dates are the data team's stated collection date for")
    note("the catalogue actually loaded in grocery-products-dev. Compare them")
    note("with the fixture date a local run prints: different data, same")
    note("contract, same assertions.")
else:
    note("Fixture prices, captured 2026-07-31. The deployed table holds the")
    note("data team's 2,759 collected rows with their own capture date, so the")
    note("PRODUCTS and the DATES both differ in integration mode - which is")
    note("exactly what makes running both worth doing.")

# ------------------------------------------------------------------ regions
section("3. Region scoping")
step(1, "POST 'cheapest butter near Albany'")
summarise(*turn("cheapest butter near Albany"))
note("")
note("Scoped to the North Shore stores. Named regions were the last thing to")
note("be confirmed working in production, on 2026-08-30.")

# --------------------------------------------------------------- meal plans
section("4. Meal plans, and the clarification that precedes one")
step(1, "POST 'feed my flat of 3 this week'   (no budget stated)")
summarise(*turn("feed my flat of 3 this week"))
note("")
note("A clarification, NOT a plan. Version 5 of the deployed function invented")
note("a $0 budget from this message and answered BUDGET_INFEASIBLE - 'I")
note("couldn't build a plan within $0'. A hallucinated constraint silently")
note("changes what the user asked for, and the intent eval asserts null")
note("expectations for exactly this reason.")

step(2, "POST 'feed 3 people for 5 days on $80'")
summarise(*turn("feed 3 people for 5 days on $80"))
note("")
note("Every number in that plan was computed in Python from retrieved")
note("records. The model chose products and wrote meal names; it never saw a")
note("price and has no field to put one in. Demo 2 and demo 3 are that story.")

# -------------------------------------------------------------- malformed
section("5. Malformed input still gets a contract-valid body")
step(1, "POST with a body that is not JSON at all")
summarise(*turn("", raw_body="this is not json"))
step(2, "POST valid JSON that violates the schema")
summarise(*turn("", raw_body=json.dumps({"version": "1.0", "message": "hi"})))
note("")
note("A non-2xx status, and still a well-formed ChatResponse in the body. No")
note("path out of this service returns anything else - not a stack trace, not")
note("an empty body, not an API Gateway default error page. A frontend that")
note("parses one response shape can parse every response.")

# ------------------------------------------------------------- idempotency
section("6. Idempotency, across the network")
replay_id = f"turn-d15{uuid.uuid4().hex[:12]}"
replay_session = f"sess-d15{uuid.uuid4().hex[:12]}"
step(1, f"POST turn_id={replay_id}")
first_status, first_payload, first_seconds = turn(
    "cheapest butter", turn_id=replay_id, session=replay_session
)
summarise(first_status, first_payload, first_seconds)

step(2, "POST the SAME turn_id and the same payload again")
second_status, second_payload, second_seconds = turn(
    "cheapest butter", turn_id=replay_id, session=replay_session
)
summarise(second_status, second_payload, second_seconds)
identical = first_payload.get("events") == second_payload.get("events")
print(f"\n      byte-identical event list: {identical}")
if second_seconds < first_seconds:
    print(
        f"      and {first_seconds / max(second_seconds, 1e-6):.1f}x faster, "
        "because the graph did not run"
    )

step(3, "POST the same turn_id with a DIFFERENT message")
conflict_status, conflict_payload, conflict_seconds = turn(
    "cheapest milk", turn_id=replay_id, session=replay_session
)
summarise(conflict_status, conflict_payload, conflict_seconds)
note("")
note("A conflict, not a silent overwrite. Two different questions under one")
note("turn_id is a client bug, and answering the second one would mean the")
note("cache no longer holds the answer to the first.")
if mode == INTEGRATION:
    note("")
    note("The claim is fenced in grocery-idempotency-dev by a conditional")
    note("write with an owner token, so a superseded invocation cannot")
    note("overwrite a newer claim. Demo 6 walks through that state machine.")
else:
    note("")
    note("Locally this is InMemoryIdempotencyStore, which is single-process")
    note("and therefore CORRECT here and WRONG in production - Lambda")
    note("execution environments share no memory. USE_DYNAMODB=1 selects the")
    note("stored implementation, and demo 17 is about that switch.")

# ----------------------------------------------------------------- latency
section("7. What the numbers above are, and are not")
if mode == INTEGRATION:
    note("Client-observed wall clock, including the API Gateway hop, SnapStart")
    note("restore, Bedrock, DynamoDB and the whole graph. CloudWatch's")
    note("TurnLatency measures the handler and EXCLUDES the gateway, so the")
    note("two numbers differ and both are true.")
    note("")
    note("A handful of samples is not a baseline. The recorded one is warm p95")
    note("2.21s for price checks and 11.7-12.2s for meal plans, measured by")
    note("scripts/measure_latency.py at n=8 and n=3-4 - and even that is")
    note("labelled 'do not quote as qualification', because a p99 over three")
    note("samples is just the maximum.")
    note("")
    note("The first request of a session pays the cold start. In the recorded")
    note("run it was 5.97s against 1.6-2.0s warm, and at n=8 the p95 IS the")
    note("cold start.")
    note("")
    note("    python scripts/measure_latency.py --price-checks 20 --meal-plans 10")
else:
    note("These are in-process microseconds-to-milliseconds against fixtures.")
    note("They say nothing whatever about the deployed service, and are")
    note("printed only so the two runs are comparable in shape.")

    section("8. The deployed endpoint was NOT contacted in this mode")
    note("Everything above ran through src/handler.py in this process, with")
    note("fixtures and the scripted model. No packet left the machine.")
    note("")
    note("    DEMO_MODE=integration python Philip_demo/15_deployed_endpoint.py")
    note("")
    note("needs network access but NO AWS credentials, because the dev stage")
    note("is unauthenticated. It costs a few cents of Bedrock and Lambda, and")
    note("it is paced so it measures the service rather than the quota.")

print("\nDone.")
