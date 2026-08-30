"""Local read-only MCP facade (Req 13.1-13.4, Pilot Task 8). See server.py."""

from src.mcp.limits import (
    ENABLED_ENV,
    MAX_EVENTS,
    MAX_MESSAGE_CHARS,
    CallBudget,
    Disabled,
    LimitExceeded,
    is_enabled,
)
from src.mcp.server import TOOLS, Audit, GroceryMCPServer, serve

__all__ = [
    "ENABLED_ENV",
    "MAX_EVENTS",
    "MAX_MESSAGE_CHARS",
    "TOOLS",
    "Audit",
    "CallBudget",
    "Disabled",
    "GroceryMCPServer",
    "LimitExceeded",
    "is_enabled",
    "serve",
]
