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


# --------------------------------------------------------------------- tasks
#
# The routed task names, defined ONCE and here.
#
# `task` is a parameter of `ModelClient.structured` below, so the set of legal
# values belongs with the protocol that takes them -- and this module is the
# only leaf both the graph and `src/observability/base.py` already import.
# Observability deliberately cannot import the graph (`src/handler.py`: nothing
# below the handler knows observability exists), so a constant either lives here
# or gets written twice.
#
# It DID get written twice, and the two copies disagreed the moment they were
# tested. `src/observability/base.py` held
# `PLAN_TASKS = frozenset({"generate_plan", "repair_plan"})` while
# `src/graph/nodes/plan.py` passed the strings inline; splitting `repair_plan`
# into two tasks on 2026-08-31 updated the graph and left the observability copy
# matching nothing, so `RepairAttempts` silently reported 0 on a turn that
# repaired twice. A metric reading zero looks exactly like a healthy turn.
#
# Three test files and two demos carried a third and fourth copy. See
# `config/models.json` `scorecards._split_note` for why the split happened.

#: One model call producing a `PlanDraft`. QUALITY tier.
TASK_GENERATE_PLAN = "generate_plan"

#: The previous plan was costed and came out over. FAST tier.
TASK_REPAIR_BUDGET = "repair_budget"

#: The previous plan was rejected for something that is not about money. FAST.
TASK_REPAIR_DEFECT = "repair_defect"

#: A turn takes ONE of these per repair attempt, never both.
REPAIR_TASKS = frozenset({TASK_REPAIR_BUDGET, TASK_REPAIR_DEFECT})

#: Every task on the meal-plan path, for latency attribution and call counting.
PLAN_TASKS = frozenset({TASK_GENERATE_PLAN}) | REPAIR_TASKS


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
        task: str = "classify_intent",
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
        task: str = "generate_prose",
    ) -> str:
        """Free-text generation. Used for prose, never for prices."""
        ...

    @property
    def last_usage(self) -> dict:
        """Token counts and latency from the most recent call, for observability."""
        ...


class ModelError(RuntimeError):
    """Raised when the model call fails or the reply cannot be parsed."""


class ModelOutputInvalid(ModelError):
    """
    The model answered, and the answer did not satisfy the schema.

    Distinct from a bare ModelError, which means the call itself failed —
    unreachable endpoint, throttled, timed out, misconfigured. That difference
    decides what the caller should do and what the failure means:

      * The call failed        -> nothing to repair, retrying the same prompt
                                  is pointless, and the user should be told the
                                  service is unavailable.
      * The output was invalid -> the model IS reachable and answering. This is
                                  a quality failure, it is what the repair loop
                                  exists for, and it must count against the
                                  model in an eval rather than being written
                                  off as infrastructure.

    Collapsing the two in either direction misreports one as the other: an
    outage read as an unaffordable budget, or a model that cannot follow its
    own schema read as a network problem.

    A subclass of ModelError so existing `except ModelError` handlers at the
    edges (handler.py) keep catching it.
    """


class GuardrailBlocked(ModelError):
    """Raised when a configured model safety guardrail blocks a request."""
