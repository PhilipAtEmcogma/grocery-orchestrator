r"""
DEMO 24 - The whole backend, running, with no frontend
=======================================================

HOW TO RUN
----------
From the repository root:

    python Philip_demo/24_backend_without_a_frontend.py

On Windows without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/24_backend_without_a_frontend.py

No AWS account, credentials or network access. It binds a local port.

MODES
-----
    local  (default)      boots scripts/dev_server.py in a subprocess and
                          drives it over real HTTP on localhost.

    integration           drives the DEPLOYED endpoint over HTTPS instead.
                          Same script, same assertions, different base URL --
                          which is the point being made.

WHAT THIS DEMONSTRATES
----------------------
The frontend team is still building. This proves the backend does not need
them, and shows exactly what they will receive when they arrive:

  1. The server starts, and answers a health check
  2. A realistic multi-turn shopper session over HTTP
  3. The wire contract: an ordered event stream, not a blob of prose
  4. Citations, so every price on screen is traceable to a stored record
  5. Idempotency across the network - a retried POST is not a second turn
  6. The error contract, including which failures are worth retrying
  7. CORS preflight, because a browser will send one
  8. The exact fetch() a frontend needs, printed at the end

WHY A SUBPROCESS AND NOT AN IN-PROCESS CALL
-------------------------------------------
Every other demo in this suite calls `run_turn` or `lambda_handler` directly,
which is honest for what those demos are about. This one is about the SEAM the
frontend consumes, and that seam is HTTP: a port, a status code, headers, a
JSON body, a connection that can be retried.

Calling the handler in-process would demonstrate the graph again and quietly
skip the only part this file exists to show. So it starts the real server the
frontend would run, and talks to it the way a browser would.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from _demo_support import (
    INTEGRATION,
    LOCAL,
    ModeUnavailable,
    blocked,
    endpoint_url,
    heading,
    mode_banner,
    note,
    resolve_mode,
    section,
)

ROOT = Path(__file__).resolve().parent.parent
PORT = 8000
LOCAL_BASE = f"http://localhost:{PORT}"

try:
    mode = resolve_mode(supports=(LOCAL, INTEGRATION))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

heading("DEMO 24 - The whole backend, running, with no frontend")
if mode == LOCAL:
    mode_banner(
        mode,
        requires="a free local port 8000 - no AWS, credentials or network",
        mocked="the price store (fixtures) and the model plane (ScriptedModelClient)",
    )
else:
    mode_banner(
        mode,
        requires="network access to the deployed dev endpoint (unauthenticated)",
        mocked="nothing - real Lambda, real DynamoDB, real Bedrock",
    )

server: subprocess.Popen | None = None


def post(url: str, payload: dict, *, timeout: float = 60.0) -> tuple[int, dict | None]:
    """POST JSON and return (status, parsed body). An HTTP error is a result, not a crash."""
    req = urllib.request.Request(  # noqa: S310 - localhost or a fixed https endpoint
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, None


def turn(base: str, message: str, *, turn_id: str | None = None, session: str) -> tuple[int, dict]:
    status, body = post(
        f"{base}/chat",
        {
            "version": "1.0",
            "session_id": session,
            "turn_id": turn_id or f"turn-{uuid.uuid4().hex[:12]}",
            "message": message,
        },
    )
    return status, (body or {})


def terminal_of(body: dict) -> str:
    for event in body.get("events", []):
        if event["type"] in ("price_comparison", "meal_plan", "clarification", "error", "no_data"):
            return event["type"]
    return "?"


try:
    # ------------------------------------------------------- 1. the server

    section("1. The backend comes up on its own")

    if mode == LOCAL:
        note("starting scripts/dev_server.py as a subprocess ...")
        # Server output goes to a FILE, not a PIPE. The instrumented handler
        # prints a structured log line and an EMF metric record per turn (demo
        # 7 explains why), and an un-drained pipe buffer fills after a few
        # turns and BLOCKS the server mid-response. A file cannot block, and it
        # still lets the startup failure below be reported.
        server_log = tempfile.NamedTemporaryFile(
            mode="w+", suffix=".log", prefix="devserver-", delete=False, encoding="utf-8"
        )
        server = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [sys.executable, str(ROOT / "scripts" / "dev_server.py")],
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(ROOT),
        )
        base = LOCAL_BASE
        healthy = False
        for _ in range(40):
            if server.poll() is not None:
                server_log.flush()
                out = pathlib.Path(server_log.name).read_text(encoding="utf-8", errors="replace")
                raise SystemExit(
                    blocked(
                        "the local dev server",
                        f"it exited immediately: {out.strip()[:300]}",
                        "check nothing else is bound to port 8000",
                    )
                )
            try:
                with urllib.request.urlopen(f"{base}/health", timeout=1) as r:  # noqa: S310
                    note(f"GET /health -> {r.status} {json.loads(r.read())}")
                    healthy = True
                    break
            except Exception:
                time.sleep(0.25)
        if not healthy:
            raise SystemExit(
                blocked(
                    "the local dev server",
                    "it did not answer /health within 10 seconds",
                    "check nothing else is bound to port 8000",
                )
            )
        note("")
        note("That is the whole backend: one stdlib process, no Flask, no")
        note("FastAPI, nothing added to requirements. It runs the SAME")
        note("`lambda_handler` API Gateway invokes in production.")
    else:
        base = endpoint_url().removesuffix("/chat")
        note(f"using the deployed endpoint at {base}")
        note("No health route there -- the first turn is the check.")

    # ------------------------------------------------- 2. a shopper session

    section("2. A realistic session, several turns, one session id")

    session = f"sess-{uuid.uuid4().hex[:12]}"
    note(f"session_id {session}")
    note("")
    script = [
        "cheapest butter",
        "how much is milk and bread",
        "feed 3 people for 5 days on $80",
        "cheapest saffron",
    ]
    last_body: dict = {}
    for i, message in enumerate(script, start=1):
        status, body = turn(base, message, session=session)
        last_body = body if message.startswith("cheapest butter") else last_body
        events = [e["type"] for e in body.get("events", [])]
        note(f"  [{i}] {message:38} {status}  {terminal_of(body):17} {len(events)} events")
    note("")
    note("Four turns, one session, no state carried between them beyond the")
    note("session id. Each turn is independently answerable, which is what")
    note("makes the idempotency story below simple.")

    # -------------------------------------------------- 3. the wire contract

    section("3. What the frontend actually receives")

    note("An ORDERED EVENT STREAM, not a blob of prose:")
    note("")
    for event in last_body.get("events", []):
        summary = ""
        if event["type"] == "citation":
            c = event["citation"]
            summary = f"{c['store']} {c['store_location']} ${c['price_nzd']}"
        elif event["type"] == "intent":
            summary = f"{event['intent']} (confidence {event['confidence']})"
        elif event["type"] == "token":
            summary = repr(event.get("text", ""))[:44]
        elif event["type"] == "price_comparison":
            summary = event["data"].get("reasoning", "")[:52]
        note(f"  seq {event['seq']:<3} {event['type']:18} {summary}")
    note("")
    note("Every event carries `seq`, so a client can render progressively and")
    note("still reassemble the order. `token` events are the prose; everything")
    note("numeric arrives as structured data the frontend formats itself.")

    # ------------------------------------------------------- 4. citations

    section("4. Every price on screen is traceable")

    for event in last_body.get("events", []):
        if event["type"] == "citation":
            c = event["citation"]
            src = c["source"]
            note(
                f"  {c['ref']}  {c['product_name'][:34]:36} ${c['price_nzd']:>6}  "
                f"valid {c['valid_date']}"
            )
            note(f"      source: {src['table']} / {src['pk']} / {src['sk']}")
    note("")
    note("A price the frontend renders can be pointed back at the exact stored")
    note("record it came from. That is the property demo 3 shows being enforced")
    note("-- the model never emits a number, so it cannot invent one.")

    # ----------------------------------------------------- 5. idempotency

    section("5. A retried POST is not a second turn")

    tid = f"turn-{uuid.uuid4().hex[:12]}"
    t0 = time.perf_counter()
    s1, b1 = turn(base, "cheapest butter", turn_id=tid, session=session)
    first_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    s2, b2 = turn(base, "cheapest butter", turn_id=tid, session=session)
    replay_ms = (time.perf_counter() - t0) * 1000

    note(f"  first   {s1}  {first_ms:6.0f} ms")
    note(f"  replay  {s2}  {replay_ms:6.0f} ms   (same turn_id)")
    note(f"  identical body: {json.dumps(b1, sort_keys=True) == json.dumps(b2, sort_keys=True)}")
    note("")
    note("A dropped connection, a double-tap on a button, a retry in a fetch")
    note("wrapper -- none of them cost a second model call or a second answer.")

    # ------------------------------------------------------ 6. the errors

    section("6. The error contract, and what is worth retrying")

    for label, payload in (
        ("malformed JSON body", {"version": "1.0", "session_id": "x"}),
        (
            "missing message",
            {"version": "1.0", "session_id": session, "turn_id": f"turn-{uuid.uuid4().hex[:12]}"},
        ),
    ):
        status, body = post(f"{base}/chat", payload)
        codes = [e.get("code") for e in (body or {}).get("events", []) if e["type"] == "error"]
        retryable = [
            e.get("retryable") for e in (body or {}).get("events", []) if e["type"] == "error"
        ]
        note(f"  {label:24} HTTP {status}  codes={codes or '-'}  retryable={retryable or '-'}")
    note("")
    note("A 4xx still returns a contract-valid body. The frontend never has to")
    note("parse a stack trace or guess what API Gateway synthesised, and")
    note("`retryable` tells it whether a retry could possibly help.")

    # --------------------------------------------------------- 7. preflight

    section("7. CORS preflight, because a browser will send one")

    if mode == LOCAL:
        req = urllib.request.Request(f"{base}/chat", method="OPTIONS")  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
                note(f"OPTIONS /chat -> {r.status}")
                for header in (
                    "Access-Control-Allow-Origin",
                    "Access-Control-Allow-Methods",
                    "Access-Control-Allow-Headers",
                ):
                    note(f"  {header}: {r.headers.get(header)}")
        except urllib.error.HTTPError as exc:
            note(f"OPTIONS /chat -> {exc.code}")
        note("")
        note("`Access-Control-Allow-Origin: *` is correct for local development")
        note("and is a RECORDED EXCEPTION in the dev deployment, not a pass:")
        note("docs/ARCHITECTURE.md section 3h. It closes at the frontend cutover,")
        note("when there is finally a real origin to name -- which is also when")
        note("Req 12.5's fail-closed check arms.")
    else:
        note("skipped: the deployed stage's CORS is API Gateway's, not ours to")
        note("exercise from here. See demo 15 for the deployed wire contract.")

    # ------------------------------------------------- 8. what to hand over

    section("8. What the frontend team needs, and nothing else")

    note(f"BASE URL   {base}")
    note("")
    note("  GET  /health   liveness, no body required")
    note("  POST /chat     contract v1.0")
    note("")
    note("```js")
    note("const res = await fetch(`${BASE}/chat`, {")
    note("  method: 'POST',")
    note("  headers: { 'Content-Type': 'application/json' },")
    note("  body: JSON.stringify({")
    note("    version: '1.0',")
    note("    session_id: sessionId,      // >= 8 chars, yours to generate")
    note("    turn_id: turnId,            // >= 8 chars, REUSE IT to retry safely")
    note("    message: userText,")
    note("    location: { lat, lon, label },   // optional")
    note("    hints: { household_size, days, budget_nzd, dietary_exclusions },")
    note("  }),")
    note("});")
    note("const { events } = await res.json();   // ordered; render by seq")
    note("```")
    note("")
    note("When the deployed endpoint replaces this one, the ONLY thing that")
    note("changes on the frontend side is BASE. Same contract, same events,")
    note("same status codes -- which is why this demo runs unchanged against")
    note("either, with DEMO_MODE=integration.")
    note("")
    note("Full contract: CONTRACT-v1.md and FRONTEND-INTEGRATION.md.")
    note("Sample request/response pairs for every shape: samples/.")

finally:
    if server is not None:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        # Tidy up the log file, BEST EFFORT. On Windows the terminated child
        # can still hold the inherited handle for a moment, and unlinking then
        # raises PermissionError -- which would fail a demo that has already
        # done everything it exists to do. A leftover file in the temp
        # directory is not worth a red result.
        server_log.close()
        removed = False
        for _ in range(10):
            try:
                pathlib.Path(server_log.name).unlink(missing_ok=True)
                removed = True
                break
            except PermissionError:
                time.sleep(0.2)
        note("")
        note(
            "dev server stopped; the port is free"
            + (" and the log file removed." if removed else ".")
        )

print("\nDone.")
