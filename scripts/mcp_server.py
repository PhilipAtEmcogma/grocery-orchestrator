"""
Run the local read-only MCP facade (Pilot Task 8).

    MCP_ENABLED=1 python scripts/mcp_server.py

Speaks MCP over stdio JSON-RPC, so an approved local client (Kiro initially,
Req 13.1) launches it as a subprocess. It is OFF unless MCP_ENABLED=1: absence
of configuration means absence of surface.

Every tool invokes the COMPLETE deterministic service through the same
`lambda_handler` API Gateway calls, so grounding, dietary fail-closed
behaviour, arithmetic, Guardrail and the contract are the same assertions on
the same code path (Req 13.4). No raw storage, SDK, filesystem or network
primitive is exposed (Req 13.2).

By default it runs on fixtures and the scripted model -- no AWS account needed.
Set USE_DYNAMODB=1 and USE_BEDROCK=1 to point it at the real thing, exactly as
the dev server does.

Client configuration (Kiro / Claude Desktop shape):

    {"mcpServers": {"grocery": {
        "command": "python",
        "args": ["scripts/mcp_server.py"],
        "env": {"MCP_ENABLED": "1"}}}}
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.mcp import Disabled, serve  # noqa: E402


def main() -> int:
    try:
        serve(sys.stdin, sys.stdout, sys.stderr)
    except Disabled as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
