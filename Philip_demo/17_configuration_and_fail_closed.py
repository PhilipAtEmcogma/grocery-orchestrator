r"""
DEMO 17 - Configuration as a failure mode, and the check that catches it
========================================================================

HOW TO RUN
----------
    python Philip_demo/17_configuration_and_fail_closed.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/17_configuration_and_fail_closed.py

Against the deployed function's real environment:

    DEMO_MODE=aws python Philip_demo/17_configuration_and_fail_closed.py

MODES
-----
    local  (default)  every check is exercised against synthetic environments.
                      No AWS, no credentials, no network.
    aws               additionally reads the DEPLOYED function's configuration
                      with lambda:GetFunctionConfiguration and runs the same
                      check against it. READ ONLY - it changes nothing.
                      Variable NAMES and the guardrail VERSION are printed;
                      values are not.

WHAT THIS DEMONSTRATES
----------------------
  1. The dependency selector, and the demo implementations behind it
  2. The failure that is worse than an outage, because it is invisible
  3. assert_production_configuration, and every problem listed at once
  4. USE_DYNAMODB=true reads as enabled to a human and picks fixtures
  5. DRAFT is refused, because DRAFT moves
  6. Wildcard CORS is refused, and cannot be fixed from this repository yet
  7. An unset APP_STAGE is NOT production, deliberately
  8. AWS mode: what the running function actually has set

THE FAILURE THIS PREVENTS
-------------------------
`_dependencies()` selects by environment. USE_DYNAMODB=1 picks DynamoDB,
USE_BEDROCK=1 picks Bedrock, and anything else falls through to the fixture
repository and the scripted model. Drop one variable in production and the
endpoint keeps returning HTTP 200 with well-formed, grounded, arithmetically
verified citations - computed from 26 invented products by a rule-based
stand-in. Every invariant in this system still holds. No metric looks wrong.
The answers are simply not about real prices.

That is worse than an outage, because an outage is visible.

AND IT IS NOT HYPOTHETICAL. On 2026-08-30 the deployed function was found
applying Guardrail version 1 while every document and all the qualifying
evidence described version 2. A documented must_allow case was being refused
in production while the record said 9/9. Nothing offline can read a deployed
environment variable, so no gate could see it - which is why this check runs
in the environment it is asserting about, and why this demo has an AWS mode.

ARCHITECTURE
------------
    lambda_handler
        v
    _dependencies()
        |
        +-- assert_production_configuration()   <- BEFORE any fallback
        |        APP_STAGE in {prod, production, pilot} ?
        |            no  -> return, fallbacks are the point of the design
        |            yes -> require every variable, or raise ConfigurationError
        v
    USE_DYNAMODB == "1"  ->  DynamoPriceRepository    else InMemoryPriceRepository
    USE_BEDROCK  == "1"  ->  BedrockModelClient       else ScriptedModelClient
    USE_DYNAMODB == "1"  ->  DynamoIdempotencyStore   else InMemoryIdempotencyStore
"""

from __future__ import annotations

import os

from _demo_support import (
    AWS,
    AWS_REGION,
    LOCAL,
    ModeUnavailable,
    aws_identity,
    blocked,
    heading,
    mode_banner,
    note,
    resolve_mode,
    section,
)

from src.handler import (
    PRODUCTION_STAGES,
    STAGE_ENV,
    ConfigurationError,
    assert_production_configuration,
)

try:
    mode = resolve_mode(supports=(LOCAL, AWS))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

#: The deployed function. Overridable so this is not pinned to one account.
FUNCTION_NAME = os.environ.get("ORCHESTRATOR_FUNCTION", "grocery-orchestrator-dev")

heading("DEMO 17 - Configuration as a failure mode")
mode_banner(
    mode,
    requires=(
        "nothing - every environment below is synthetic"
        if mode == LOCAL
        else f"credentials with lambda:GetFunctionConfiguration on {FUNCTION_NAME}"
    ),
    mocked=("nothing is mocked - the check is real, the environments handed to it are constructed"),
)

#: A complete, correct production environment. Every later example is this one
#: with something taken away, so the difference is always visible.
GOOD = {
    "APP_STAGE": "pilot",
    "USE_DYNAMODB": "1",
    "USE_BEDROCK": "1",
    "BEDROCK_GUARDRAIL_ID": "b1xezpqe04kx",
    "BEDROCK_GUARDRAIL_VERSION": "2",
    "CORS_ORIGIN": "https://d111111abcdef8.cloudfront.net",
}


def check(label: str, env: dict[str, str]) -> None:
    """Run the real check and print what it said."""
    try:
        assert_production_configuration(env)
        print(f"  {label:<36} PASSES")
    except ConfigurationError as exc:
        problems = str(exc).split("(Req 12.5): ", 1)[-1].split("; ")
        print(f"  {label:<36} REFUSES to start:")
        for problem in problems:
            print(f"    - {problem}")


# ------------------------------------------------------------- the selector
section("1. What each variable actually selects")
print(f"  {'variable':<16} {'set to "1"':<28} anything else")
print(f"  {'-' * 16} {'-' * 28} -------------")
for name, on, off in (
    ("USE_DYNAMODB", "DynamoPriceRepository", "InMemoryPriceRepository (26 fixtures)"),
    ("USE_BEDROCK", "BedrockModelClient", "ScriptedModelClient (rule-based)"),
    ("USE_DYNAMODB", "DynamoIdempotencyStore", "InMemoryIdempotencyStore (per-process)"),
):
    print(f"  {name:<16} {on:<28} {off}")
note("")
note("Those fallbacks are not a mistake - they are the reason the graph runs")
note("on a laptop, why CI needs no credentials, and why demos 1 to 12 exist at")
note("all. The problem is only what they mean in a stage serving shoppers.")

# --------------------------------------------------------- the real failure
section("2. What a misconfigured production stage looks like from outside")
print("  HTTP 200.")
print("  A well-formed ChatResponse.")
print("  Citations with source table, partition key and sort key.")
print("  assert_grounded passes. assert_arithmetic passes.")
print("  assert_citations_match_retrieval passes.")
print("  TurnsProcessed increments. No alarm fires. p95 looks great.")
print("\n  And every price is invented, because 26 fixture products were")
print("  served by a rule-based stand-in.")
note("")
note("Nothing downstream can tell. Every invariant this project has is about")
note("the relationship between what was retrieved and what was published, and")
note("that relationship is intact - it is the retrieval that is wrong.")

# ------------------------------------------------------------- the check
section("3. The check, and every problem at once")
check("a complete production environment", GOOD)
check("nothing set but APP_STAGE=pilot", {"APP_STAGE": "pilot"})
note("")
note("Every problem is listed, not just the first. One deploy and one fix,")
note("rather than a sequence of failed deployments each revealing the next")
note("missing variable.")

# ------------------------------------------------------------- exact match
section("4. USE_DYNAMODB=true reads as enabled, and picks fixtures")
check("USE_DYNAMODB=true", {**GOOD, "USE_DYNAMODB": "true"})
print()
print('  Because the selector compares against "1" exactly:')
print('      if os.environ.get("USE_DYNAMODB") == "1":')
note("")
note("The check compares the same way the selector does. A check that")
note("accepted 'true' would pass a configuration the code then reads as off,")
note("which is the entire failure mode wearing a rosette.")

# ---------------------------------------------------------------- DRAFT
section("5. DRAFT is refused")
check("BEDROCK_GUARDRAIL_VERSION=DRAFT", {**GOOD, "BEDROCK_GUARDRAIL_VERSION": "DRAFT"})
check("guardrail version unset", {**GOOD, "BEDROCK_GUARDRAIL_VERSION": ""})
note("")
note("DRAFT moves, so evidence gathered against it describes whatever the")
note("policy happened to be that day. It is refused here for the same reason")
note("IAM deliberately does not grant it: an unpinnable policy cannot be")
note("qualified, and the 13/13 + 9/9 red-team evidence is about a NUMBER.")

# ------------------------------------------------------------------ CORS
section("6. Wildcard CORS is refused, and cannot be fixed here yet")
check("CORS_ORIGIN=*", {**GOOD, "CORS_ORIGIN": "*"})
note("")
note("The deployed function still sets CORS_ORIGIN=* and this is NOT an")
note("oversight. Strict CORS means naming ONE origin, and the origin is the")
note("frontend's CloudFront domain, which does not exist - that stack is")
note("unbuilt and is teammates' scope. There is nothing to name.")
note("")
note("Which is exactly why APP_STAGE is unset on the deployed function:")
note("arming this check today would fail startup on a value that has no")
note("correct setting yet. Setting APP_STAGE=pilot is the LAST step of the")
note("deploy task, so the check arms at the moment the last thing blocking it")
note("is gone rather than sitting inert while somebody remembers it.")

# ----------------------------------------------------------------- stages
section("7. An unset APP_STAGE is not production, deliberately")
print(f"  production stages: {sorted(PRODUCTION_STAGES)}")
print(f"  the variable:      {STAGE_ENV}\n")
for stage in ("", "dev", "staging", "pilot", "prod", "PRODUCTION"):
    label = f"{STAGE_ENV}={stage!r}" if stage else f"{STAGE_ENV} unset"
    env = {"CORS_ORIGIN": "*"} if not stage else {STAGE_ENV: stage, "CORS_ORIGIN": "*"}
    check(label, env)
note("")
note("Defaulting the other way would break every offline test, both eval")
note("harnesses, the demos and the dev server on the day it landed - which is")
note("a good way to have the check deleted. Setting the stage is the deploy's")
note("job.")

# --------------------------------------------------------------- a gap
section("8. A gap this surfaced, and did not close")
print("  A ConfigurationError is caught by the handler's error boundary and")
print("  mapped to a contract-valid INTERNAL_ERROR - correct, because 'no path")
print("  out without a contract-valid body' is a hard invariant here.\n")
print("  But it logs `unhandled_exception`, and the HandlerEscaped metric")
print('  filter binds to { $.message = "handler_escaped" }, which only the')
print("  OUTERMOST boundary emits.\n")
print("  So a fully misconfigured production stage would return INTERNAL_ERROR")
print("  on every turn, at HTTP 200, and fire NEITHER alarm: not")
print("  handler-escaped (wrong message) and not api-5xx (not a 5xx).")
note("")
note("That was alarm coverage rather than a defect in this check, and it is")
note("closed: `internal-error` now watches TurnError dimensioned on")
note("code=INTERNAL_ERROR, at 3 in 5 minutes. The dimension is the design -")
note("BUDGET_INFEASIBLE and NO_DATA share the TurnError metric and are the")
note("product working correctly, and an alarm that paged on those is an alarm")
note("people mute.")

# ------------------------------------------------------------------ aws
if mode == AWS:
    section("9. What the running function actually has set")
    usable, detail = aws_identity()
    if not usable:
        raise SystemExit(
            blocked(
                "reading the deployed function's configuration",
                detail,
                f"configure AWS credentials for the deployment account in {AWS_REGION}, "
                "or run without DEMO_MODE=aws",
            )
        )
    print(f"  caller: {detail}\n")
    import boto3

    try:
        config = boto3.client("lambda", region_name=AWS_REGION).get_function_configuration(
            FunctionName=FUNCTION_NAME
        )
    except Exception as exc:
        raise SystemExit(
            blocked(
                "reading the deployed function's configuration",
                f"{type(exc).__name__}: {str(exc)[:200]}",
                f"check {FUNCTION_NAME} exists in {AWS_REGION} and the caller has "
                "lambda:GetFunctionConfiguration on it",
            )
        ) from exc

    live_env = config.get("Environment", {}).get("Variables", {})
    print(f"  function     {config['FunctionName']}")
    print(f"  version      {config['Version']}")
    print(f"  runtime      {config['Runtime']}  {config['Architectures'][0]}")
    print(f"  memory       {config['MemorySize']} MB, timeout {config['Timeout']}s")
    print(f"  last update  {config['LastModified']}\n")

    # Values are deliberately not printed, with two exceptions: the guardrail
    # VERSION, because it is the thing that drifted and a number is not a
    # secret, and CORS_ORIGIN, because whether it is "*" is the whole point.
    shown = {
        "BEDROCK_GUARDRAIL_VERSION",
        "CORS_ORIGIN",
        "USE_DYNAMODB",
        "USE_BEDROCK",
        "APP_STAGE",
        "PRODUCTS_TABLE",
    }
    print(f"  {'variable':<30} {'set':<5} value")
    print(f"  {'-' * 30} {'-' * 5} -----")
    for name in sorted(set(live_env) | set(GOOD)):
        value = live_env.get(name)
        display = (
            value
            if (name in shown and value is not None)
            else ("(set, not printed)" if value is not None else "-")
        )
        print(f"  {name:<30} {'yes' if value is not None else 'NO':<5} {display}")

    print()
    check(f"the live environment of {FUNCTION_NAME}", live_env)
    note("")
    if not live_env.get(STAGE_ENV):
        note(f"{STAGE_ENV} is unset on the deployed function, so the check is")
        note("INERT there today - it returns before testing anything. That is")
        note("section 6: arming it needs a CORS origin that does not exist yet.")
        note("")
        note("Which means this line is the honest summary: the check exists, it")
        note("is correct, and it is not yet doing anything in production.")
    note("")
    note("This section is the reason the demo has an AWS mode at all. Nothing")
    note("offline can read a deployed environment variable, and the guardrail")
    note("drift went unnoticed for days precisely because the eval harness")
    note("measured the configuration it was RUN in - a laptop with")
    note("BEDROCK_GUARDRAIL_VERSION=2 exported by hand - while production had 1.")
    note("Same code, same harness, different answer, and nothing compared them.")
else:
    section("9. The deployed configuration was NOT read in this mode")
    note("Every environment checked above was constructed in this file. That")
    note("proves the check is correct; it proves nothing about the account.")
    note("")
    note("    DEMO_MODE=aws python Philip_demo/17_configuration_and_fail_closed.py")
    note("")
    note("reads the real function's variables with")
    note("lambda:GetFunctionConfiguration and runs the same check against them.")
    note("It writes nothing, and it prints variable NAMES rather than values.")
    note("")
    note("That distinction is the whole finding: evidence is only about the")
    note("configuration it was collected under, and until section 9 runs, this")
    note("repository cannot state which configuration that was.")

print("\nDone.")
