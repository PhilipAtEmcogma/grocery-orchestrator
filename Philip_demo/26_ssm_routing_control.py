r"""
DEMO 26 - Retuning which model serves which task, without a deploy
=================================================================

HOW TO RUN
----------
From the repository root:

    python Philip_demo/26_ssm_routing_control.py

On Windows without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/26_ssm_routing_control.py

No AWS account, credentials or network access. The SSM transport is stubbed;
the registry, the router and the qualification gate are the real ones.

MODES
-----
    local  (default and only)  the SSM client is a stub. Every routing
                               decision below is made by production code.

WHAT THIS DEMONSTRATES
----------------------
Pilot Task 7b, closed 2026-09-07, and the argument that made it safe to build
at all:

  1. A parameter nobody reads is a console text box that looks like a control
  2. What is overridable, and the two things that deliberately are NOT
  3. THE SAFETY PROPERTY: an override cannot enable a model
  4. Replacement rather than merge, and why an EMPTY override is refused
  5. Fail-SAFE, in a codebase that otherwise fails closed
  6. What is deployed, and how to see which document is running

WHO THIS IS FOR
---------------
Anyone who might edit that parameter, and anyone reviewing whether they should
be allowed to. Section 3 is the one that decides it.

EXPECTED RESULT
---------------
Every check prints OK. The one to watch is section 3: an override that prefers
`claude-sonnet` -- which the file DISABLES on latency grounds -- does not get
it. Exit code 0.
"""

from __future__ import annotations

import json

from _demo_support import LOCAL, heading, mode_banner, note, require, resolve_mode, section

from src.models.registry import ModelRegistry, ModelTier, UnroutableTask
from src.models.ssm_routing import ROUTING_PARAM_ENV, load_routing_override

mode = resolve_mode(supports=(LOCAL,))
mode_banner(mode, requires="nothing", mocked="the SSM transport only")

heading("DEMO 26 - Retuning routing without a deploy")


# ------------------------------------------------ 1. the shape of the gap

section("1. A parameter nobody reads is not a control")

note("infra/lib/service-stack.ts has published this since 2026-08-30:")
note("")
note("    /grocery/{stage}/models/routing")
note("")
note("...under a comment that said, honestly:")
note("")
note('    "NOT read at runtime yet"')
note('    "the forward path, not a live control"')
note('    "pretending otherwise would be claiming a capability that does')
note('     not exist"')
note("")
note("That was the right thing to write, and it is exactly the shape this")
note("repository keeps removing. src/models/ssm_routing.py is what makes the")
note("parameter real.")


# ----------------------------------------------- 2. what is overridable

section("2. What is overridable, and what deliberately is not")

note("  routing      YES  which model serves which task. A judgement, and the")
note("                    thing an operator retunes when a model gets slow.")
note("  scorecards   NO   measured evidence. An operator who could edit one")
note("                    could QUALIFY A ROUTE BY TYPING.")
note("  models       NO   a capability inventory -- tool use, cache minimums,")
note("                    prices -- that changes with a deploy, not judgement.")
note("")
note("The stack publishes only the routing block. This module honours that")
note("rather than widening it.")


# ------------------------------------------------ 3. THE SAFETY PROPERTY

section("3. THE SAFETY PROPERTY: an override cannot enable a model")

baseline = ModelRegistry()
sonnet = baseline.get("claude-sonnet")
note(f"  config/models.json says claude-sonnet enabled = {sonnet.enabled}")
note("  ...documented as excluded on LATENCY: p50 11.8s against a 20s client")
note("  timeout, 9 of 98 plan calls over the ceiling.")
note("")
note("Now an operator edits SSM to prefer it anyway:")

hostile = ModelRegistry(
    routing_override={"classify_intent": {"tier": "quality", "prefer": ["claude-sonnet"]}}
)
try:
    got = hostile.route("classify_intent")
    outcome = f"routed to {got.key}"
    served_sonnet = got.key == "claude-sonnet"
except UnroutableTask as exc:
    outcome = f"UnroutableTask - {str(exc)[:60]}"
    served_sonnet = False

note(f"    result: {outcome}")
require(not served_sonnet, "an override reached a disabled model")
note("")
note("  [OK  ] the disabled model was NOT served")
note("")
note("Why it holds: `models` and `scorecards` come from the archive, which")
note("only a deploy changes, and route() returns only specs that are enabled,")
note("configured and at the requested tier. So the worst a bad edit does is")
note("make a task UNROUTABLE -- loud, and contract-mapped -- rather than")
note("quietly downgrade the service to something unqualified.")
note("")
note("If that property ever stopped holding, the feature would want")
note("WITHDRAWING rather than fixing. That is why it is tested from three")
note("directions: a disabled model, an unknown model, and a model at a tier")
note("it does not declare.")

# The other two directions, run.
unknown = ModelRegistry(
    routing_override={"classify_intent": {"tier": "fast", "prefer": ["gpt-imaginary"]}}
)
spec = unknown.route("classify_intent")
require(spec.enabled and spec.is_configured, "spec.enabled and spec.is_configured")
note(f"    unknown model preferred -> fell through to {spec.key} (qualified)")

wrong_tier = ModelRegistry(
    routing_override={"classify_intent": {"tier": "quality", "prefer": ["nova-lite"]}}
)
spec2 = wrong_tier.route("classify_intent")
require(
    spec2.key != "nova-lite" and ModelTier.QUALITY in spec2.tiers,
    "an override reached a model at a tier it does not declare",
)
note(f"    nova-lite at a tier it lacks -> {spec2.key} instead")


# --------------------------------------- 4. replacement, and empty refused

section("4. Replacement, not a merge -- and why EMPTY is refused")

overlay = ModelRegistry(
    routing_override={"classify_intent": {"tier": "fast", "prefer": ["claude-haiku"]}}
)
note(f"  file routing tasks     {len(baseline.tasks)}: {baseline.tasks[:3]} ...")
note(f"  overridden routing     {len(overlay.tasks)}: {overlay.tasks}")
note(f"  routing_source         {overlay.routing_source}")
note("")
note("A MERGE would make the effective config a function of two documents, so")
note("an operator who DELETED a route would find it still routing from the")
note("file. What the parameter says is what runs.")
note("")
note("Which is also why an EMPTY block is refused rather than honoured:")


class _Returns:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_parameter(self, **_):
        return {"Parameter": {"Value": self._value}}


import os  # noqa: E402

import boto3  # noqa: E402

os.environ[ROUTING_PARAM_ENV] = "/grocery/demo/models/routing"
_real_client = boto3.client

for payload, label in [
    (json.dumps({"routing": {}}), "an EMPTY routing block"),
    ("not json at all", "unparseable"),
    (json.dumps({"routing": {"classify_intent": "nova-lite"}}), "a rule that is not an object"),
    (
        json.dumps({"routing": {"classify_intent": {"tier": "fast", "prefer": ["claude-haiku"]}}}),
        "a well-formed override",
    ),
]:
    boto3.client = lambda *a, _v=payload, **k: _Returns(_v)  # type: ignore[assignment]
    got = load_routing_override()
    note(f"  {label:34} -> {'ACCEPTED' if got else 'refused, using the file'}")

boto3.client = _real_client  # type: ignore[assignment]
note("")
note('`{"routing": {}}` is not "no opinion", it is "no task has a route",')
note("which would make EVERY task unroutable and take the service down from a")
note("console text box. The bundled file is the better reading.")


# ------------------------------------------------------- 5. fail-safe

section("5. Fail-SAFE here, where the rest of this codebase fails closed")


class _Boom:
    def get_parameter(self, **_):
        raise RuntimeError("AccessDeniedException")


boto3.client = lambda *a, **k: _Boom()  # type: ignore[assignment]
fallback = load_routing_override()
boto3.client = _real_client  # type: ignore[assignment]
os.environ.pop(ROUTING_PARAM_ENV, None)

note(f"  SSM unreachable -> override is {fallback} (so: use the bundled file)")
note("")
note("Everywhere else a missing control fails CLOSED: no guardrail means no")
note("generation, an unknown store raises rather than defaulting.")
note("")
note("Here the fallback is not an absence. It is a complete, reviewed")
note("configuration that this very deploy shipped, already in memory.")
note("Refusing to serve because an optional tuning overlay is unreachable")
note("would turn an operator convenience into an outage.")
note("")
note("It is LOGGED, never silent -- a silent fallback is how somebody comes to")
note("believe they retuned production when they retuned nothing. The log line")
note("carries the parameter name and the exception TYPE, never the value.")


# --------------------------------------------------- 6. what is deployed

section("6. What is live, and how to see which document is running")

note("Deployed on the CDK plane 2026-09-07, and PROVED rather than assumed:")
note("")
note('    {"message": "model_routing_source", "routing_source": "ssm",')
note('     "cold_start": true}')
note("")
note("Getting that line required a fix. The first attempt looked for the")
note("loader's own INFO and found nothing on a turn that had demonstrably")
note("cold-started: the stdlib root logger in Lambda sits at WARNING, so")
note("logging.info() is dropped while Powertools' INFO lines appear.")
note("")
note('So "logged, never silent" was true of the case that goes WRONG and')
note("false of the case that goes RIGHT -- the wrong way round for answering")
note('"did my retune take effect?", because absence of a warning is only')
note("evidence if you already trust the code ran.")
note("")
note(f"  ModelRegistry.routing_source -> '{baseline.routing_source}' offline here")
note("  ...and 'ssm' on the deployed CDK plane.")

print("\nDone.")
