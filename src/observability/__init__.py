"""
Observability.

`base.py` and `instrumented.py` are dependency-free and safe to import
anywhere. `powertools.py` is NOT re-exported here on purpose: importing it
pulls in aws-lambda-powertools, and a convenience re-export is exactly how a
handler-only dependency creeps into the graph. Import it by its full path,
from the handler, and nowhere else.
"""

from __future__ import annotations

from src.observability.base import (
    NULL_TELEMETRY,
    NullTelemetry,
    Span,
    Telemetry,
    TurnStats,
    exception_fields,
    has_content,
    request_fields,
    response_fields,
    turn_intent,
)
from src.observability.instrumented import (
    InstrumentedModelClient,
    InstrumentedPriceRepository,
)

__all__ = [
    "NULL_TELEMETRY",
    "InstrumentedModelClient",
    "InstrumentedPriceRepository",
    "NullTelemetry",
    "Span",
    "Telemetry",
    "TurnStats",
    "exception_fields",
    "has_content",
    "request_fields",
    "response_fields",
    "turn_intent",
]
