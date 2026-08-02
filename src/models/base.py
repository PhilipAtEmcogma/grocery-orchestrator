"""
The model boundary.

Nodes depend on this Protocol, never on boto3 or langchain directly. Same
reasoning as the retrieval boundary: the graph is buildable and testable with
no AWS account, and CI needs no credentials.

MODEL TIERING lives here rather than being scattered through nodes. Model
selection is an explicit, reviewable policy decision (an assigned deliverable),
not an incidental choice made at each call site.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, TypeVar

from pydantic import BaseModel


class ModelTier(StrEnum):
    """
    Which class of model a call needs. Nodes request a TIER, not a model id.

    FAST     — classification, extraction, repair passes. Cheap, low latency.
               High volume: every turn hits this at least once.
    QUALITY  — meal planning. Creative assembly under multiple simultaneous
               constraints, where a weaker model produces plans that are
               technically valid but unappetising or repetitive.

    Keeping the mapping in one place means retiering a node is a one-line
    change, and the cost/latency consequences of the policy are visible.
    """

    FAST = "fast"
    QUALITY = "quality"


T = TypeVar("T", bound=BaseModel)


class ModelClient(Protocol):
    """Minimal surface. Add methods only when a node genuinely needs one."""

    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        tier: ModelTier,
        max_tokens: int = 1024,
    ) -> T:
        """
        Call the model and parse the reply into `schema`.

        Must raise ModelError on failure rather than returning a partial or
        invented object. Callers decide how to degrade; the client never
        guesses on their behalf.
        """
        ...

    def text(
        self,
        *,
        system: str,
        user: str,
        tier: ModelTier,
        max_tokens: int = 1024,
    ) -> str:
        """Free-text generation. Used for prose, never for prices."""
        ...

    @property
    def last_usage(self) -> dict:
        """Token counts and latency from the most recent call, for observability."""
        ...


class ModelError(RuntimeError):
    """Raised when the model call fails or the reply cannot be parsed."""
