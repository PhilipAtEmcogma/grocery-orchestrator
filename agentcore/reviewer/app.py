"""
AgentCore Runtime entrypoint for the data-quality reviewer (ADR 0002 WS2).

THIS IS THE CODE THE microVM RUNS. It is the MODEL HALF ONLY: it receives a
sanitised, capped snapshot of catalogue rows, asks the model which look wrong,
and returns the RAW findings. It does not validate them -- that happens on the
caller's side, outside this Runtime, against the snapshot the caller actually
sent (Option A, `docs/AGENTCORE-RUNTIME-REVIEWER.md` §3).

The trust boundary is visible in the imports: this module imports
`propose_findings` and never imports `validate_findings`. A compromised Runtime
can only return claims; it cannot launder one into a validated finding, because
the validator does not live here.

WHAT IT DOES NOT HAVE: no DynamoDB read path, no S3, no shopper tables, no write
authority. The execution role grants `bedrock:InvokeModel` on one model ARN and
CloudWatch/X-Ray, nothing else (§8 of the design doc). The Runtime cannot read
the catalogue; it only ever sees the rows pushed into a single invocation.

Protocol (AgentCore Runtime, HTTP): listens on 0.0.0.0:8080, ARM64.
  GET  /ping         -> {"status": "Healthy"}
  POST /invocations  -> {"table_name": str, "rows": [ ... ]} -> raw findings JSON
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The Runtime bundles the repo, so `src` is importable. Only the model half is
# imported -- deliberately not `validate_findings`.
from src.models.base import TASK_REVIEW_SNAPSHOT
from src.models.bedrock import BedrockModelClient
from src.models.registry import ModelRegistry, RoutingPolicy
from src.prompts.review import MAX_FINDINGS
from src.review.reviewer import propose_findings
from src.review.snapshot import MAX_SNAPSHOT_ROWS

_PORT = 8080

#: Built once per microVM, reused across invocations in the same session. The
#: pinned spec routes every call to the model qualified for this task; the
#: Runtime never picks a model per request.
_model: BedrockModelClient | None = None


def _get_model() -> BedrockModelClient:
    global _model
    if _model is None:
        spec = ModelRegistry().route(
            TASK_REVIEW_SNAPSHOT,
            policy=RoutingPolicy.PINNED,
            pinned_key=os.environ.get("REVIEWER_MODEL_KEY", "nova-lite"),
        )
        _model = BedrockModelClient(pinned_spec=spec)
    return _model


def _handle_invocation(payload: dict) -> dict:
    """
    Sanitised rows -> raw findings. No validation, by design.

    Enforces the row cap here too: a caller is supposed to cap before sending,
    but the Runtime refusing an oversized snapshot is defence in depth -- an
    unbounded input is an unbounded token bill and the thing the cap exists for.
    """
    rows = payload.get("rows")
    table_name = payload.get("table_name", "unknown")

    if not isinstance(rows, list):
        return {"ran": False, "findings": [], "error": "payload.rows must be a list"}
    if len(rows) > MAX_SNAPSHOT_ROWS:
        return {
            "ran": False,
            "findings": [],
            "error": f"{len(rows)} rows offered, cap is {MAX_SNAPSHOT_ROWS}",
        }

    try:
        report = propose_findings(rows, table_name=table_name, model=_get_model())
    except Exception as exc:  # the boundary returns errors, never raises out
        # A model/guardrail/infra failure is reported as ran=False. The Runtime
        # never invents a finding, and never crashes the invocation -- the
        # caller distinguishes "reviewed, found nothing" from "could not run".
        return {"ran": False, "findings": [], "error": type(exc).__name__ + ": " + str(exc)}

    findings = [f.model_dump() for f in report.findings[:MAX_FINDINGS]]
    return {"ran": True, "findings": findings, "error": ""}


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # http.server naming
        if self.path == "/ping":
            self._send(200, {"status": "Healthy"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # http.server naming
        if self.path != "/invocations":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"ran": False, "findings": [], "error": "invalid JSON"})
            return
        self._send(200, _handle_invocation(payload))

    def log_message(self, *args) -> None:  # silence default stderr logging
        # Deliberately quiet: the request line can echo payload-shaped data, and
        # this Runtime's rule is to log nothing about what it reviewed.
        return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", _PORT), _Handler)  # noqa: S104 - Runtime contract
    server.serve_forever()


if __name__ == "__main__":
    main()
