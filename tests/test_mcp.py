"""
Local read-only MCP façade (Req 13.1-13.4, Pilot Task 8).

The two tests that matter are PARITY and the CAPS. Parity is the whole claim of
this surface -- that MCP is a window onto the same service and not a second
implementation that can drift. The caps are what makes it bounded rather than
merely small.
"""

from __future__ import annotations

import io
import json

import pytest

from src.mcp import (
    ENABLED_ENV,
    MAX_EVENTS,
    MAX_MESSAGE_CHARS,
    TOOLS,
    Audit,
    CallBudget,
    Disabled,
    GroceryMCPServer,
    LimitExceeded,
    is_enabled,
    serve,
)

ON = {ENABLED_ENV: "1"}


def _server(**kwargs) -> GroceryMCPServer:
    kwargs.setdefault("env", ON)
    return GroceryMCPServer(**kwargs)


def _expect(response: dict | None) -> dict:
    """
    A request carrying an `id` always gets a response.

    `handle` returns None for notifications, so the type is `dict | None`. That
    is correct and the tests know more than the signature does -- asserted
    rather than cast, so a future change that silently drops a response fails
    here instead of raising a confusing subscript error.
    """
    assert response is not None, "a request with an id must get a response"
    return response


def _call(server: GroceryMCPServer, name: str, **arguments) -> dict:
    return _expect(
        server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
    )


def _payload(response: dict) -> dict:
    return json.loads(response["result"]["content"][0]["text"])


# ---------------------------------------------------------------- protocol


def test_initialize_and_tools_list() -> None:
    server = _server()
    init = _expect(server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
    assert init["result"]["serverInfo"]["name"] == "grocery-orchestrator"

    listed = _expect(server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
    assert {t["name"] for t in listed["result"]["tools"]} == {
        "grocery_ask",
        "grocery_dietary_terms",
    }


def test_a_notification_gets_no_response() -> None:
    """
    Per the spec, and it matters: a client that receives a reply to
    `notifications/initialized` may treat the handshake as failed.
    """
    assert _server().handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_an_unknown_method_and_an_unknown_tool_are_refused() -> None:
    server = _server()
    unknown = _expect(server.handle({"jsonrpc": "2.0", "id": 1, "method": "nope"}))
    assert unknown["error"]["code"] == -32601
    assert "error" in _call(server, "grocery_delete_everything")


# ---------------------------------------------------------------- surface


def test_the_surface_exposes_no_primitive_operation() -> None:
    """
    Req 13.2. The tool list IS the surface, so the assertion is on the list.

    Coarse on purpose: a fine-grained tool ("query the products table") would be
    a database with extra steps, and every invariant this project has lives
    ABOVE that layer. This test is what a reviewer reads to check that claim
    without reading the implementation.
    """
    forbidden = (
        "query",
        "scan",
        "put",
        "delete",
        "write",
        "update",
        "table",
        "dynamo",
        "s3",
        "bucket",
        "sql",
        "exec",
        "shell",
        "file",
        "read_file",
        "fetch",
        "http",
        "url",
        "invoke_model",
        "generate",
        "citation",
        "scrape",
    )
    for tool in TOOLS:
        name = tool["name"].lower()
        for word in forbidden:
            assert word not in name, f"tool {tool['name']!r} sounds like a primitive"


def test_every_tool_declares_a_closed_input_schema() -> None:
    """
    `additionalProperties: false` is the difference between a schema that
    validates and a schema that decorates: without it a client can pass
    anything alongside the declared fields.
    """
    for tool in TOOLS:
        schema = tool["inputSchema"]
        assert schema["additionalProperties"] is False, tool["name"]
        assert schema["type"] == "object"


# ---------------------------------------------------------------- parity


def test_mcp_returns_exactly_what_the_service_returns() -> None:
    """
    REQ 13.4, AND THE WHOLE POINT OF THE FAÇADE.

    Every tool call goes through the same `lambda_handler` API Gateway invokes,
    so grounding, dietary fail-closed behaviour, arithmetic verification and the
    contract are the same assertions on the same code path. If this façade ever
    grew its own retrieval or its own assembly, that claim would quietly stop
    being true -- so it is asserted on the bytes rather than argued.
    """
    captured: dict = {}

    def fake_invoke(event: dict) -> dict:
        captured["event"] = event
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "version": "1.0",
                    "session_id": "s",
                    "turn_id": "t",
                    "events": [{"seq": 0, "type": "session"}, {"seq": 1, "type": "done"}],
                }
            ),
        }

    server = _server(invoke=fake_invoke)
    payload = _payload(_call(server, "grocery_ask", message="cheapest butter"))

    assert payload == json.loads(fake_invoke(captured["event"])["body"])
    # It invoked the Lambda entrypoint's own event shape, not some other path.
    assert captured["event"]["httpMethod"] == "POST"
    assert json.loads(captured["event"]["body"])["message"] == "cheapest butter"


def test_each_call_uses_fresh_ids() -> None:
    """
    Reused ids hit the idempotency cache and return a STORED outcome.

    That reads as the service being fast and is really the service not running
    -- docs/ARCHITECTURE.md §6 records two fixes that looked inert for an
    afternoon for exactly this reason.
    """
    seen: list[str] = []

    def fake_invoke(event: dict) -> dict:
        seen.append(json.loads(event["body"])["turn_id"])
        return {"statusCode": 200, "body": json.dumps({"events": []})}

    server = _server(invoke=fake_invoke)
    for _ in range(3):
        _call(server, "grocery_ask", message="cheapest butter")
    assert len(set(seen)) == 3


# ---------------------------------------------------------------- caps


def test_disabled_by_default() -> None:
    """
    Absence of configuration means absence of surface.

    Req 13.6 wants a tested disable path, and the cheapest one that cannot fail
    is a default-off switch.
    """
    assert is_enabled({}) is False
    assert is_enabled({ENABLED_ENV: "0"}) is False
    assert is_enabled({ENABLED_ENV: "true"}) is False  # exact match, like USE_DYNAMODB
    assert is_enabled(ON) is True

    off = GroceryMCPServer(env={})
    assert _call(off, "grocery_ask", message="x")["error"]["code"] == -32002


def test_serve_refuses_to_start_when_disabled() -> None:
    with pytest.raises(Disabled, match="MCP_ENABLED"):
        serve(io.StringIO(""), io.StringIO(), io.StringIO(), env={})


def test_the_rate_limit_bites() -> None:
    budget = CallBudget(calls_per_minute=2, calls_per_session=100)
    server = _server(
        invoke=lambda e: {"statusCode": 200, "body": json.dumps({"events": []})}, budget=budget
    )
    assert "result" in _call(server, "grocery_dietary_terms")
    assert "result" in _call(server, "grocery_dietary_terms")
    third = _call(server, "grocery_dietary_terms")
    assert third["error"]["code"] == -32001
    assert "rate limit" in third["error"]["message"]


def test_the_session_limit_bites_separately() -> None:
    """
    Two caps because they bound different failures: a rate cap stops a burst, a
    session cap stops a slow loop that never bursts.
    """
    budget = CallBudget(calls_per_minute=1000, calls_per_session=2)
    server = _server(
        invoke=lambda e: {"statusCode": 200, "body": json.dumps({"events": []})}, budget=budget
    )
    _call(server, "grocery_dietary_terms")
    _call(server, "grocery_dietary_terms")
    assert "session limit" in _call(server, "grocery_dietary_terms")["error"]["message"]


def test_the_budget_raises_rather_than_returning_false() -> None:
    budget = CallBudget(calls_per_minute=1)
    budget.check_and_record()
    with pytest.raises(LimitExceeded):
        budget.check_and_record()


def test_an_oversized_message_is_refused_before_the_service_runs() -> None:
    called = False

    def fake_invoke(event: dict) -> dict:
        nonlocal called
        called = True
        return {"statusCode": 200, "body": json.dumps({"events": []})}

    server = _server(invoke=fake_invoke)
    response = _call(server, "grocery_ask", message="x" * (MAX_MESSAGE_CHARS + 1))
    assert response["error"]["code"] == -32602
    assert called is False, "the cap must be enforced before the service is invoked"


def test_an_empty_message_is_refused() -> None:
    for bad in ("", "   ", None, 42):
        assert "error" in _call(_server(), "grocery_ask", message=bad)


def test_an_oversized_response_is_refused() -> None:
    """A cap on the way out as well as in: a client's context is finite."""
    many = {"events": [{"seq": i, "type": "citation"} for i in range(MAX_EVENTS + 1)]}
    server = _server(invoke=lambda e: {"statusCode": 200, "body": json.dumps(many)})
    assert _call(server, "grocery_ask", message="x")["error"]["code"] == -32001


# ---------------------------------------------------------------- audit


def test_the_audit_never_records_the_message() -> None:
    """
    Req 13.3 and 11.5. A tool argument here IS a shopper's message, and this
    project keeps message text out of every log it writes.
    """
    lines: list[str] = []
    server = _server(
        invoke=lambda e: {"statusCode": 200, "body": json.dumps({"events": []})},
        audit=Audit(lines.append),
    )
    sensitive = "cheapest gluten free bread in Remuera for my coeliac flatmate"
    _call(server, "grocery_ask", message=sensitive)

    assert lines, "nothing was audited"
    blob = " ".join(lines)
    assert sensitive not in blob
    for word in ("gluten", "coeliac", "Remuera", "flatmate"):
        assert word not in blob, f"{word!r} leaked into the audit"

    entry = json.loads(lines[0])
    assert entry["tool"] == "grocery_ask"
    assert entry["outcome"] == "ok"
    assert "duration_ms" in entry


def test_refusals_are_audited_too() -> None:
    """An audit that only records successes cannot show abuse."""
    lines: list[str] = []
    server = GroceryMCPServer(env={}, audit=Audit(lines.append))
    _call(server, "grocery_ask", message="x")
    assert json.loads(lines[0])["outcome"] == "disabled"


# ---------------------------------------------------------------- end to end


def test_a_real_turn_through_the_facade_is_grounded() -> None:
    """
    No stub: the whole graph, on fixtures, through the façade.

    Every citation must carry the source keys that make it checkable, which is
    the property Req 13.4 says must hold whichever way the service is invoked.
    """
    from src.retrieval.filters import pin_to_fixture_snapshot

    pin_to_fixture_snapshot()
    payload = _payload(_call(_server(), "grocery_ask", message="cheapest butter"))
    events = payload["events"]

    assert [e["type"] for e in events][:2] == ["session", "intent"]
    assert events[-1]["type"] == "done"
    citations = [e for e in events if e["type"] == "citation"]
    assert citations
    for citation in citations:
        source = citation["citation"]["source"]
        assert source["table"] and source["pk"] and source["sk"]


def test_the_dietary_tool_reports_what_the_graph_actually_honours() -> None:
    from src.graph.dietary import supported_terms

    payload = _payload(_call(_server(), "grocery_dietary_terms"))
    assert payload["supported_exclusions"] == supported_terms()


def test_nothing_but_json_rpc_reaches_stdout() -> None:
    """
    THE PROTOCOL CHANNEL MUST CARRY ONLY THE PROTOCOL.

    This failed the first time it was run as a real subprocess. The service
    writes Powertools structured logs and EMF metric records to stdout BY
    DESIGN -- that is where CloudWatch reads them from in Lambda, and
    `scripts/dev_server.py` documents it as a feature. Under MCP the same
    behaviour interleaved `{"_aws": ...}` blobs with the JSON-RPC and a client
    could not parse the stream.

    Asserted end to end through `serve()` rather than on the handler, because
    the bug lives in the composition: each half is behaving correctly and the
    combination is broken. Run as a real subprocess in
    `test_the_server_runs_as_a_subprocess` too, since an in-process test shares
    this process's already-constructed loggers.
    """
    from src.retrieval.filters import pin_to_fixture_snapshot

    pin_to_fixture_snapshot()
    stdin = io.StringIO(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "grocery_ask", "arguments": {"message": "cheapest butter"}},
            }
        )
        + "\n"
    )
    stdout, stderr = io.StringIO(), io.StringIO()

    import sys

    real_stdout = sys.stdout
    try:
        serve(stdin, stdout, stderr, env=ON)
    finally:
        sys.stdout = real_stdout

    lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
    assert lines, "no response written"
    for line in lines:
        message = json.loads(line)  # raises if anything non-protocol leaked
        assert message["jsonrpc"] == "2.0"


def test_the_server_runs_as_a_subprocess() -> None:
    """
    The way a client actually starts it.

    In-process tests share this process's loggers, which are already bound to a
    stream. Only a real subprocess proves the stdout protection works from a
    cold start -- which is the condition the bug appeared under.
    """
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    requests = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(root / "scripts" / "mcp_server.py")],
        input=requests,
        capture_output=True,
        text=True,
        cwd=root,
        env={**__import__("os").environ, "MCP_ENABLED": "1"},
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-800:]

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 2
    for line in lines:
        json.loads(line)


def test_the_subprocess_refuses_to_start_without_the_switch() -> None:
    """The disable path, exercised the way an operator would hit it."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in __import__("os").environ.items() if k != "MCP_ENABLED"}
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(root / "scripts" / "mcp_server.py")],
        input="",
        capture_output=True,
        text=True,
        cwd=root,
        env=env,
        timeout=120,
    )
    assert result.returncode == 2
    assert "MCP_ENABLED" in result.stderr
    assert not result.stdout.strip(), "a disabled server must write nothing to the protocol channel"
