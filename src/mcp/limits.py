"""
The caps a bounded tool surface needs (Req 13.3, 13.6).

Every limit here is a number somebody could argue with, so each carries the
reason it is that number rather than another. They are deliberately small: this
façade exists to prove the shape of a bounded surface before any managed
exposure, and a generous cap proves nothing.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

# One turn already costs 2-4 Bedrock calls and the account's binding Nova Lite
# quota is 20 requests/minute and cannot be raised
# (docs/THROUGHPUT-AND-SCALING.md). Six tool calls a minute leaves the shopper
# path room to breathe: an agent that exhausts the quota starves the product
# this façade is a window onto.
DEFAULT_CALLS_PER_MINUTE = 6

# A session cap as well as a rate cap, because they bound different failures. A
# rate cap stops a burst; a session cap stops a slow loop that never bursts and
# would otherwise run all afternoon.
DEFAULT_CALLS_PER_SESSION = 60

# Below the 29-second API Gateway ceiling and the 30s Lambda timeout, so a tool
# call cannot outlive the service it wraps.
DEFAULT_TIMEOUT_SECONDS = 25.0

# A shopper message. The contract's own limit; restated rather than imported so
# the façade fails on oversized input before the service is invoked at all.
MAX_MESSAGE_CHARS = 500

# Events in a returned turn. A price comparison across ten stores with
# citations runs to a few dozen; 200 is far above anything the graph emits and
# far below anything that could flood a client's context.
MAX_EVENTS = 200

ENABLED_ENV = "MCP_ENABLED"


class LimitExceeded(RuntimeError):
    """A cap was hit. Carries what and why, never the argument that hit it."""


class Disabled(RuntimeError):
    """The façade is switched off."""


def is_enabled(env: dict[str, str] | None = None) -> bool:
    """
    OFF unless explicitly switched on.

    Default-off, not default-on. This is a tool surface onto a service that
    spends money and answers shoppers; it should exist only where somebody
    decided it should. `security.md` requires a tested disable path for any
    exposed surface (Req 13.6), and the cheapest disable path that cannot fail
    is one where absence of configuration means absence of surface.
    """
    source = os.environ if env is None else env
    return source.get(ENABLED_ENV, "").strip() == "1"


@dataclass
class CallBudget:
    """
    Rate and session limits for one client.

    Kept in memory on purpose: a local façade is a single process with a single
    client, and a stored counter would be infrastructure this surface has not
    earned. When the same caps are needed behind AgentCore Gateway they belong
    in the Gateway, not here -- that is ADR 0002's staging, and duplicating
    them locally would create two limits that can disagree.
    """

    calls_per_minute: int = DEFAULT_CALLS_PER_MINUTE
    calls_per_session: int = DEFAULT_CALLS_PER_SESSION
    _recent: list[float] = field(default_factory=list)
    _total: int = 0

    def check_and_record(self, *, now: float | None = None) -> None:
        """Raise if this call would exceed a cap; otherwise count it."""
        moment = time.monotonic() if now is None else now
        self._recent = [t for t in self._recent if moment - t < 60.0]

        if self._total >= self.calls_per_session:
            raise LimitExceeded(
                f"session limit reached ({self.calls_per_session} calls). "
                "Restart the server to reset -- a long-running loop is the "
                "failure this bounds."
            )
        if len(self._recent) >= self.calls_per_minute:
            raise LimitExceeded(
                f"rate limit reached ({self.calls_per_minute}/minute). The "
                "account's Bedrock quota is shared with the shopper path and "
                "cannot be raised."
            )

        self._recent.append(moment)
        self._total += 1

    @property
    def calls_made(self) -> int:
        return self._total
