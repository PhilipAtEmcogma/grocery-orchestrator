"""
Local read-only MCP façade (Req 13.1-13.4, Pilot Task 8).

WHAT THIS IS. A bounded window onto the COMPLETE deterministic service, for a
local approved client. Every tool call goes through `lambda_handler` -- the same
entrypoint API Gateway invokes -- so grounding, dietary fail-closed behaviour,
arithmetic verification, Guardrail tagging, idempotency and the contract are the
same assertions on the same code path (Req 13.4). Nothing here reaches into the
graph, and there is no second implementation of anything to drift.

WHAT IT DELIBERATELY IS NOT (Req 13.2). No raw DynamoDB operation, no AWS SDK
call, no filesystem access, no arbitrary network access, no retailer
acquisition, no production write, no citation creation, no unguarded model
generation. The tool list below is the entire surface, and it is coarse on
purpose: a fine-grained tool ("query the products table") would be a database
with extra steps, and every invariant this project has lives ABOVE that layer.

NO SDK DEPENDENCY. The protocol here is MCP over stdio JSON-RPC, implemented
directly. Adding a dependency for a local-only façade would put a package in
`requirements.txt` that the Lambda archive then has to exclude, and the archive
already carries a size budget and an exclusion list that has to be kept honest.
The subset implemented is `initialize`, `tools/list`, `tools/call`.

THE AUDIT RECORDS THAT A CALL HAPPENED, NEVER WHAT WAS ASKED (Req 13.3, 11.5).
A shopper's message is the thing this project keeps out of logs everywhere else,
and a tool argument is a shopper's message. The audit carries the tool name, the
outcome, the duration and the event count -- enough to see abuse or a loop, and
nothing that would make the log a privacy incident.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from src.mcp.limits import (
    MAX_EVENTS,
    MAX_MESSAGE_CHARS,
    CallBudget,
    Disabled,
    LimitExceeded,
    is_enabled,
)

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "grocery-orchestrator"
SERVER_VERSION = "1.0.0"

# JSON-RPC error codes. -32602 is the spec's "invalid params"; the rest are
# application errors in the reserved-for-implementation range.
INVALID_PARAMS = -32602
METHOD_NOT_FOUND = -32601
LIMIT_ERROR = -32001
DISABLED_ERROR = -32002


TOOLS: list[dict[str, Any]] = [
    {
        "name": "grocery_ask",
        "description": (
            "Ask the Smart Grocery assistant a question in natural language -- a price "
            "comparison ('cheapest butter near Albany') or a budget meal plan ('feed 3 "
            "people for 5 days on $80'). Returns the assistant's typed event list: "
            "citations carrying the exact source record for every price, a comparison or "
            "meal plan, and an honest refusal when the data cannot support an answer. "
            "READ ONLY: this never writes prices, creates citations, or reaches storage "
            "directly. Every price is retrieved before it is shown and is checked against "
            "the record it came from."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The shopper's question, in plain English.",
                    "maxLength": MAX_MESSAGE_CHARS,
                }
            },
            "required": ["message"],
            "additionalProperties": False,
        },
    },
    {
        "name": "grocery_dietary_terms",
        "description": (
            "The dietary exclusion terms the assistant can honour, e.g. 'vegetarian', "
            "'dairy-free'. Anything outside this list is refused rather than approximated, "
            "because a plan built on an exclusion we cannot verify is unsafe. Use this to "
            "phrase a request the assistant can actually satisfy."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}


class Audit:
    """
    Privacy-safe operation record (Req 13.3).

    Deliberately not the Powertools logger: this runs as a local process, not in
    Lambda, and importing the observability stack here would breach the rule
    that only `src/handler.py` and `src/observability/powertools.py` may.
    """

    def __init__(self, sink: Callable[[str], None]) -> None:
        self._sink = sink

    def record(self, tool: str, outcome: str, started: float, events: int | None = None) -> None:
        entry = {
            "event": "mcp_tool_call",
            "tool": tool,
            "outcome": outcome,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
        if events is not None:
            entry["event_count"] = events
        # NO ARGUMENTS. A tool argument here is a shopper's message, and Req
        # 11.5 keeps message text out of every log this project writes.
        self._sink(json.dumps(entry))


class GroceryMCPServer:
    """
    The façade. One JSON-RPC request in, one response out.

    `invoke` is injected so tests can drive the server without AWS and so the
    parity test can compare this path against a direct call to the same
    function -- which is the evidence Req 13.4 asks for.
    """

    def __init__(
        self,
        invoke: Callable[[dict], dict] | None = None,
        budget: CallBudget | None = None,
        audit: Audit | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._invoke = invoke or _default_invoke
        self._budget = budget or CallBudget()
        self._audit = audit or Audit(lambda line: None)
        self._env = env

    # ---------------------------------------------------------------- rpc

    def handle(self, request: dict) -> dict | None:
        """
        One JSON-RPC message. Returns None for a notification.

        Notifications (no `id`) get no response, per the spec. `initialized` is
        the one that matters: a client that gets a reply to it may treat the
        handshake as failed.
        """
        method = request.get("method", "")
        request_id = request.get("id")

        if request_id is None:
            return None

        if method == "initialize":
            return _result(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            )
        if method == "tools/list":
            return _result(request_id, {"tools": TOOLS})
        if method == "tools/call":
            return self._call(request_id, request.get("params") or {})
        return _error(request_id, METHOD_NOT_FOUND, f"unknown method {method!r}")

    # ---------------------------------------------------------------- tools

    def _call(self, request_id: Any, params: dict) -> dict:
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        started = time.perf_counter()

        if not is_enabled(self._env):
            self._audit.record(name, "disabled", started)
            return _error(
                request_id,
                DISABLED_ERROR,
                "The MCP facade is disabled. Set MCP_ENABLED=1 to enable it. "
                "It is off by default: absence of configuration means absence "
                "of surface.",
            )
        if name not in TOOL_NAMES:
            self._audit.record(name, "unknown_tool", started)
            return _error(request_id, INVALID_PARAMS, f"unknown tool {name!r}")

        try:
            self._budget.check_and_record()
        except LimitExceeded as exc:
            self._audit.record(name, "rate_limited", started)
            return _error(request_id, LIMIT_ERROR, str(exc))

        if name == "grocery_dietary_terms":
            from src.graph.dietary import supported_terms

            self._audit.record(name, "ok", started)
            return _result(request_id, _content({"supported_exclusions": supported_terms()}))

        return self._ask(request_id, arguments, started)

    def _ask(self, request_id: Any, arguments: dict, started: float) -> dict:
        message = arguments.get("message")
        if not isinstance(message, str) or not message.strip():
            self._audit.record("grocery_ask", "invalid_params", started)
            return _error(request_id, INVALID_PARAMS, "`message` must be a non-empty string")
        if len(message) > MAX_MESSAGE_CHARS:
            self._audit.record("grocery_ask", "message_too_long", started)
            return _error(
                request_id,
                INVALID_PARAMS,
                f"`message` exceeds {MAX_MESSAGE_CHARS} characters",
            )

        # Fresh ids per call. Reusing them would hit the idempotency cache and
        # return a stored outcome, which reads as the service being fast and is
        # really the service not running -- docs/ARCHITECTURE.md section 6.
        import uuid

        token = uuid.uuid4().hex[:12]
        event = {
            "httpMethod": "POST",
            "body": json.dumps(
                {
                    "version": "1.0",
                    "session_id": f"sess-mcp{token}",
                    "turn_id": f"turn-mcp{token}",
                    "message": message,
                }
            ),
        }

        response = self._invoke(event)
        payload = json.loads(response["body"])
        events = payload.get("events", [])

        if len(events) > MAX_EVENTS:
            self._audit.record("grocery_ask", "response_too_large", started, len(events))
            return _error(
                request_id,
                LIMIT_ERROR,
                f"response carried {len(events)} events, over the {MAX_EVENTS} cap",
            )

        self._audit.record("grocery_ask", "ok", started, len(events))
        return _result(request_id, _content(payload))


# ---------------------------------------------------------------- helpers


def _default_invoke(event: dict) -> dict:
    """Imported lazily so importing this module costs nothing until a call."""
    from src.handler import lambda_handler

    return lambda_handler(event)


def _content(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}


def _result(request_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(stdin, stdout, stderr, env: dict[str, str] | None = None) -> None:
    """
    Read JSON-RPC from stdin, write responses to stdout, everything else to stderr.

    STDOUT IS THE PROTOCOL CHANNEL AND NOTHING ELSE MAY TOUCH IT. A stray line
    there is a parse error at the client that looks like a server bug rather
    than a logging mistake.

    That is not just about this module's own audit. The service writes
    Powertools structured logs and EMF metric records to stdout BY DESIGN --
    that is exactly where CloudWatch reads them from in Lambda, and
    `scripts/dev_server.py` documents it as a feature. Under MCP the same
    behaviour corrupts every response: a real subprocess run interleaved
    `{"_aws": ...}` metric blobs with the JSON-RPC and the client could not
    parse it.

    So `sys.stdout` is rebound to stderr for the life of the process, BEFORE
    the handler is imported, and the true stdout is kept privately for
    responses. Rebinding rather than a per-call `redirect_stdout` because
    Powertools' logger binds its stream handler when it is constructed at
    import time -- redirecting around each call would come too late for a
    logger that already captured the original stream.
    """
    if not is_enabled(env):
        raise Disabled(
            "MCP_ENABLED is not set to 1. The facade is off by default; "
            "absence of configuration means absence of surface."
        )

    import sys

    protocol = stdout
    sys.stdout = stderr

    server = GroceryMCPServer(
        audit=Audit(lambda line: print(line, file=stderr, flush=True)), env=env
    )
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = server.handle(request)
        if response is not None:
            print(json.dumps(response), file=protocol, flush=True)
