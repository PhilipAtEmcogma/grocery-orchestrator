r"""
DEMO 8 - The MCP facade: a bounded read-only tool surface
========================================================

HOW TO RUN
----------
    python Philip_demo/08_mcp_tool_surface.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/08_mcp_tool_surface.py

To drive the real server as an MCP client would, over stdio JSON-RPC:

    DEMO_MODE=integration python Philip_demo/08_mcp_tool_surface.py

MODES
-----
    local        (default)  the server driven in-process
    integration             the server as a SUBPROCESS over stdio, which is
                            what an MCP client actually does

Neither mode needs AWS, credentials or network access. "Integration" here
means the real transport rather than a remote service.

WHAT THIS DEMONSTRATES
----------------------
  1. The whole tool surface - two tools, and why it is coarse on purpose
  2. The JSON-RPC handshake an MCP client performs
  3. A tool call going through lambda_handler, the same entrypoint API
     Gateway invokes, so every invariant is the same code path
  4. Default-OFF: absence of configuration means absence of surface
  5. The caps, and what each one bounds
  6. The audit record, and what it deliberately does not contain
  7. stdout is the protocol channel and nothing else may touch it

WHY A FACADE RATHER THAN TOOLS OVER THE DATA
--------------------------------------------
A fine-grained tool ("query the products table") would be a database with
extra steps, and every invariant this project has - grounding, dietary
fail-closed, arithmetic verification, the contract - lives ABOVE that layer.
Exposing storage would let an agent produce a price that never passed any of
them. So the surface is the product, not its parts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from _demo_support import (
    INTEGRATION,
    LOCAL,
    ModeUnavailable,
    heading,
    mode_banner,
    note,
    resolve_mode,
    section,
    step,
)

from src.mcp import (
    ENABLED_ENV,
    MAX_EVENTS,
    MAX_MESSAGE_CHARS,
    TOOLS,
    Audit,
    CallBudget,
    GroceryMCPServer,
    LimitExceeded,
    is_enabled,
)

try:
    mode = resolve_mode(supports=(LOCAL, INTEGRATION))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

heading("DEMO 8 - The MCP facade: a bounded read-only tool surface")
mode_banner(
    mode,
    requires="nothing - no AWS, no credentials, no network",
    mocked="the model plane (ScriptedModelClient) and the price store (fixtures)",
)

# The facade is off unless switched on, so the demo switches it on for itself
# rather than making the reader export something first. Passed as an explicit
# env mapping, so the process environment is never mutated.
ENV = {ENABLED_ENV: "1"}

# --------------------------------------------------------------- the surface
section("1. The entire tool surface")
for tool in TOOLS:
    params = list(tool["inputSchema"].get("properties", {})) or "[]"
    print(f"  {tool['name']:<24} params={params}")
    print(f"    {tool['description'][:140]}...")
print("\n  Two tools. Not 'query products', not 'get citation', not 'call the")
print("  model'. Everything below the service is unreachable from here.")

# --------------------------------------------------------------- default off
section("2. Default-OFF, and what that costs an attacker")
print(f"  is_enabled() with no configuration : {is_enabled({})}")
print(f"  is_enabled() with {ENABLED_ENV}=1        : {is_enabled(ENV)}")
disabled = GroceryMCPServer(env={})
reply = disabled.handle(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "grocery_ask", "arguments": {"message": "cheapest butter"}},
    }
)
print("\n  A call against a disabled server:")
print(f"    error {reply['error']['code']}: {reply['error']['message'][:88]}...")
note("")
note("Req 13.6 wants a tested disable path. The cheapest one that cannot")
note("fail is the one where absence of configuration is absence of surface.")

# ----------------------------------------------------------------- handshake
section("3. The handshake an MCP client performs")

audited: list[str] = []
server = GroceryMCPServer(audit=Audit(audited.append), env=ENV)

step(1, "initialize")
init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
print(f"      protocolVersion {init['result']['protocolVersion']}")
print(f"      serverInfo      {init['result']['serverInfo']}")

step(2, "notifications/initialized  (a notification: no id, so no reply)")
notification = server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
print(f"      returned {notification!r}")
note("A client that gets a reply to a notification may treat the handshake")
note("as failed, so returning None is the protocol, not an oversight.")

step(3, "tools/list")
listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
print(f"      {[t['name'] for t in listed['result']['tools']]}")


def show_turn(payload: dict) -> None:
    """Print the event kinds and the first few citations of a tool result."""
    kinds = [e["type"] for e in payload["events"]]
    cites = [e["citation"] for e in payload["events"] if e["type"] == "citation"]
    print(f"  events    {kinds}")
    for c in cites[:3]:
        print(f"    {c['ref']}  {c['product_name']:<34} ${c['price_nzd']:>6} @ {c['store']}")
    if len(cites) > 3:
        print(f"    ... {len(cites)} citations in total")


# --------------------------------------------------------------- a tool call
section("4. A tool call, and where it actually goes")
print("  GroceryMCPServer -> lambda_handler -> graph -> fixture repository.\n")
call = server.handle(
    {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "grocery_ask", "arguments": {"message": "cheapest butter"}},
    }
)
show_turn(json.loads(call["result"]["content"][0]["text"]))
note("")
note("Those citations came through the SAME lambda_handler API Gateway")
note("invokes. Grounding, the arithmetic check, the dietary fail-closed rule")
note("and the contract are not re-implemented here - there is no second")
note("implementation of anything to drift (Req 13.4).")

# -------------------------------------------------------------------- limits
section("5. The caps, and what each one bounds")
print(f"  MAX_MESSAGE_CHARS  {MAX_MESSAGE_CHARS:<5} the contract's own limit, restated so")
print("                           oversized input fails BEFORE the service runs")
print(f"  MAX_EVENTS         {MAX_EVENTS:<5} far above anything the graph emits, far")
print("                           below anything that floods a client's context")

budget = CallBudget(calls_per_minute=2, calls_per_session=3)
print("\n  A CallBudget(2/minute, 3/session), exercised:")
for attempt in (1, 2, 3):
    try:
        budget.check_and_record()
        print(f"    call {attempt}: allowed  (calls_made={budget.calls_made})")
    except LimitExceeded as exc:
        print(f"    call {attempt}: LimitExceeded: {str(exc)[:64]}...")
note("")
note("Two caps because they bound different failures: a rate cap stops a")
note("burst, a session cap stops a slow loop that never bursts and would")
note("otherwise run all afternoon.")

throttled = GroceryMCPServer(
    budget=CallBudget(calls_per_minute=0), audit=Audit(audited.append), env=ENV
)
limited = throttled.handle(
    {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "grocery_ask", "arguments": {"message": "cheapest milk"}},
    }
)
print(f"\n  A rate-limited call: error {limited['error']['code']}")
print(f"    {limited['error']['message'][:96]}...")

# --------------------------------------------------------------------- audit
section("6. The audit, and what is missing from it")
server.handle(
    {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "grocery_ask",
            "arguments": {"message": "x" * (MAX_MESSAGE_CHARS + 1)},
        },
    }
)
terms = server.handle(
    {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "grocery_dietary_terms", "arguments": {}},
    }
)
server.handle(
    {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {"name": "delete_everything", "arguments": {}},
    }
)
for line in audited:
    print(f"  {line}")
note("")
note("tool, outcome, duration, event count. NO ARGUMENTS. A tool argument")
note("here is a shopper's message, and Req 11.5 keeps message text out of")
note("every log this project writes - so the audit that would catch abuse")
note("is not itself the privacy incident.")

supported = json.loads(terms["result"]["content"][0]["text"])["supported_exclusions"]
print(f"\n  grocery_dietary_terms returned {len(supported)} terms, e.g. {supported[:6]}")
note("A client can ask what the assistant can honour BEFORE phrasing a")
note("request it will refuse. The refusal is fail-closed either way.")

# ------------------------------------------------------------- the transport
if mode == INTEGRATION:
    section("7. The real server, as a subprocess over stdio")
    root = Path(__file__).resolve().parent.parent
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "grocery_ask", "arguments": {"message": "cheapest butter"}},
        },
    ]
    step(1, "launching: python scripts/mcp_server.py  (MCP_ENABLED=1)")
    # S603: argv is this interpreter plus a path derived from __file__. No
    # shell, and nothing here comes from user input.
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(root / "scripts" / "mcp_server.py")],
        input="\n".join(json.dumps(r) for r in requests) + "\n",
        capture_output=True,
        text=True,
        cwd=str(root),
        env={**os.environ, ENABLED_ENV: "1", "PYTHONPATH": str(root)},
        timeout=180,
        check=False,
    )
    step(2, f"process exited {proc.returncode}")
    step(3, "parsing every stdout line as JSON-RPC")

    replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    print(f"\n  {len(replies)} responses, every stdout line parsed cleanly:")
    for r in replies:
        print(f"    id={r['id']}  {'result' if 'result' in r else 'error'}")
    show_turn(json.loads(replies[-1]["result"]["content"][0]["text"]))

    section("8. Why a clean stdout is the whole trick")
    stderr_lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
    emf = [ln for ln in stderr_lines if '"_aws"' in ln]
    audit_lines = [ln for ln in stderr_lines if "mcp_tool_call" in ln]
    print(f"  stdout lines  {len(replies):>4}   all JSON-RPC, nothing else")
    print(f"  stderr lines  {len(stderr_lines):>4}   logs, EMF metrics, audit")
    print(f"    of which EMF metric records : {len(emf)}")
    print(f"    of which MCP audit records  : {len(audit_lines)}")
    note("")
    note("The service writes Powertools logs and EMF metric records to stdout")
    note("BY DESIGN - that is where CloudWatch reads them in Lambda. Under MCP")
    note("the same behaviour corrupts every response. serve() rebinds")
    note("sys.stdout to stderr BEFORE importing the handler and keeps the true")
    note("stdout privately for replies. A real subprocess run interleaved")
    note('{"_aws": ...} blobs with the protocol before that existed.')
else:
    section("7. The stdio transport was NOT exercised in this mode")
    note("This run drove the server in-process: the same code, not the same")
    note("channel. The stdout-purity property can only be observed through a")
    note("real subprocess, so it is not claimed here. To see it:")
    note("")
    note("    DEMO_MODE=integration python Philip_demo/08_mcp_tool_surface.py")

print("\nDone.")
