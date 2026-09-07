"""
Read the routing block from SSM, so an operator can retune without a deploy.

THE OTHER HALF OF PILOT TASK 7b. `infra/lib/service-stack.ts` has published
`/grocery/{stage}/models/routing` since 2026-08-30, with a comment saying in as
many words that nothing reads it: *"NOT read at runtime yet - infra/docs/08
§6"*. A parameter nobody reads is a console text box that looks like a control,
which is the exact shape this repository keeps finding and removing. This module
is what makes the parameter real.

WHAT IS OVERRIDABLE, AND WHAT DELIBERATELY IS NOT. The stack publishes the
ROUTING BLOCK ONLY, and its reasoning is worth restating here because this is
the module that would be tempted to widen it:

  * `routing` — which model serves which task. A judgement, and the thing an
    operator legitimately retunes when a model gets slow or a quota moves.
  * `scorecards` — NOT published, NOT overridable. Measured evidence. An
    operator who could edit a scorecard could qualify a route by typing, which
    is the one thing the qualification gate exists to prevent.
  * `models` — NOT published. A capability inventory (tool use, cache minimums,
    prices) that changes with a deploy, not with an operator's judgement.

THE SAFETY PROPERTY THAT MAKES THIS ACCEPTABLE AT ALL. An override cannot
enable a model, invent one, or bypass qualification. `ModelRegistry.route()`
only ever returns a spec that is `enabled` and `is_configured` and carries the
requested tier — those come from the FILE, which only a deploy changes. So the
worst an operator can do by editing this parameter is route a task to a model
that cannot serve it, and get `UnroutableTask` — a loud, contract-mapped
failure — rather than a quiet downgrade to something unqualified.
`tests/test_ssm_routing.py` asserts exactly that, because it is the whole
argument for allowing the knob.

FAIL-SAFE, NOT FAIL-CLOSED, AND THAT IS A DELIBERATE ASYMMETRY. Everywhere else
in this codebase a missing control fails closed — no guardrail means no
generation, an unknown store raises rather than defaulting. Here the fallback is
the BUNDLED FILE, which is not an absence: it is a complete, reviewed, deployed
configuration that was correct at the moment the archive was built. Refusing to
serve because an optional tuning overlay is unreachable would turn an operator
convenience into an outage, and would do it for a file we are already holding.

The fallback is LOGGED rather than silent. A silent fallback is how you end up
believing you retuned production when you retuned nothing.

NO ALARM, and that is reasoned rather than lazy: falling back is CORRECT
behaviour producing CORRECT answers from a reviewed config. `docs/ARCHITECTURE.md`
§3l's rule is that an alarm binds to a failure a person must act on tonight;
this is a line in a log a person reads when a retune did not take effect.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

#: The parameter to read. ITS PRESENCE IS THE SWITCH — there is no separate
#: `USE_SSM_ROUTING=1`.
#:
#: Every other toggle here (`USE_DYNAMODB`, `USE_BEDROCK`, `MCP_ENABLED`,
#: `SNAPSTART`) is a bare boolean matched exactly against "1", and this one
#: breaks that pattern on purpose. Those flags have nothing to name; this one
#: does, and two variables would admit a state that means nothing — "enabled,
#: but no parameter" — which someone then has to decide how to interpret.
#: `PRICE_SOURCE=lineage_b` is the precedent: where a value selects the
#: behaviour, the value is the switch.
ROUTING_PARAM_ENV = "MODELS_ROUTING_PARAM"

REGION = os.environ.get("AWS_REGION", "ap-southeast-2")

#: Tight, because this runs on a cold start inside a 29-second API Gateway
#: ceiling and the answer is optional. If SSM is slow we would rather serve
#: from the bundled file than spend the shopper's latency budget on a knob.
_CONNECT_TIMEOUT = 2
_READ_TIMEOUT = 3


def routing_parameter_name() -> str | None:
    """The configured parameter, or None when the overlay is switched off."""
    return os.environ.get(ROUTING_PARAM_ENV) or None


def load_routing_override(name: str | None = None) -> dict[str, Any] | None:
    """
    Fetch the routing block from SSM, or return None and say why.

    Never raises. Every failure mode — unset, unreachable, denied, absent,
    malformed — resolves to None, which the registry reads as "use the file".
    """
    parameter = name or routing_parameter_name()
    if not parameter:
        return None

    try:
        # Imported here, not at module scope, so `src.models.registry` and the
        # eval harness stay importable with no boto3 and no account. Same seam
        # as src/retrieval/dynamo.py and src/history/store.py.
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "ssm",
            region_name=REGION,
            config=Config(
                retries={"max_attempts": 1, "mode": "standard"},
                connect_timeout=_CONNECT_TIMEOUT,
                read_timeout=_READ_TIMEOUT,
            ),
        )
        raw = client.get_parameter(Name=parameter)["Parameter"]["Value"]
    except Exception as exc:  # every failure mode here has the same answer: use the file
        # The TYPE and the parameter name, never the value. A parameter value
        # is configuration and this log line is not the place to publish it.
        logger.warning(
            "ssm_routing_unavailable",
            extra={
                "parameter": parameter,
                "error_type": type(exc).__name__,
                "action": "using the routing block bundled in the archive",
            },
        )
        return None

    return _parse(raw, parameter)


def _parse(raw: str, parameter: str) -> dict[str, Any] | None:
    """
    Validate the fetched value into a routing block, or None.

    Structural only. It checks that the thing is shaped like routing rules —
    it does NOT check that the models named exist or are enabled, because
    `ModelRegistry.route()` already refuses anything unqualified and doing it
    twice in two places is how the two copies come to disagree.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "ssm_routing_unparseable",
            extra={"parameter": parameter, "action": "using the bundled routing block"},
        )
        return None

    routing = payload.get("routing") if isinstance(payload, dict) else None

    # An empty dict is refused as firmly as a malformed one, and this is the
    # case worth being explicit about: `{"routing": {}}` is not "no opinion",
    # it is "no task has a route", which would make EVERY task unroutable and
    # take the whole service down from a console text box. The bundled file is
    # the better reading of an empty override.
    if not isinstance(routing, dict) or not routing:
        logger.warning(
            "ssm_routing_empty_or_malformed",
            extra={"parameter": parameter, "action": "using the bundled routing block"},
        )
        return None

    if not all(isinstance(rule, dict) for rule in routing.values()):
        logger.warning(
            "ssm_routing_rule_not_an_object",
            extra={"parameter": parameter, "action": "using the bundled routing block"},
        )
        return None

    logger.info(
        "ssm_routing_applied",
        extra={"parameter": parameter, "tasks": sorted(routing)},
    )
    return routing
