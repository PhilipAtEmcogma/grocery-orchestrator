"""
Local dev server. Same handler the Lambda runs, over plain HTTP.

Purpose: unblock the frontend team NOW. They point at http://localhost:8000
and get real, contract-valid responses. When the AWS account lands, the only
thing that changes on their side is the base URL.

Stdlib only — no Flask, no FastAPI, nothing to add to requirements.

    python scripts/dev_server.py
    curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
         -d '{"version":"1.0","session_id":"sess-local01",
              "turn_id":"turn-local01","message":"cheapest butter"}'
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.handler import lambda_handler
from src.retrieval.filters import pin_to_fixture_snapshot

PORT = 8000


class DevHandler(BaseHTTPRequestHandler):
    def _send(self, result: dict) -> None:
        body = (result.get("body") or "").encode("utf-8")
        self.send_response(result["statusCode"])
        for key, value in result.get("headers", {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._send(lambda_handler({"httpMethod": "OPTIONS"}))

    def do_GET(self) -> None:
        """Health check, so the frontend can tell the server is up."""
        if self.path in ("/", "/health"):
            body = json.dumps({"status": "ok", "contract_version": "1.0"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/chat":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"

        result = lambda_handler({"httpMethod": "POST", "body": raw})
        self._send(result)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        """
        Quieter than the default, and no request bodies in the log.

        The parameter must be named `format` to match BaseHTTPRequestHandler,
        even though it shadows the builtin — hence the noqa.
        """
        sys.stderr.write(f"  {self.command} {self.path} -> {args[1]}\n")


def main() -> None:
    print(f"Smart Grocery orchestrator — dev server on http://localhost:{PORT}")
    print("  POST /chat     contract v1.0")
    print("  GET  /health")
    # Fixtures are a captured SNAPSHOT, so freshness is judged as of the
    # capture rather than today. Against the wall clock every price reads as
    # stale once the snapshot ages past the threshold, and the server would
    # answer STALE_DATA to everything for a reason unrelated to the code.
    as_of = pin_to_fixture_snapshot()
    print("\nUsing fixtures + scripted model. No AWS credentials needed.")
    print(f"Price freshness judged as of the fixture capture, {as_of}.")
    print("Ctrl+C to stop.\n")
    HTTPServer(("0.0.0.0", PORT), DevHandler).serve_forever()  # noqa: S104


if __name__ == "__main__":
    main()
