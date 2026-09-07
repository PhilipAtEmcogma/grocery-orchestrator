r"""
DEMO 25 - Telling a quota breach apart from an outage
=====================================================

HOW TO RUN
----------
From the repository root:

    python Philip_demo/25_throttling_and_stale_data.py

On Windows without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/25_throttling_and_stale_data.py

No AWS account, credentials or network access. Nothing is mocked that matters:
the classification and the counting are the real production code paths.

MODES
-----
    local  (default and only)  no AWS. The Bedrock client is constructed with
                               a stub transport, which is how the eval suites
                               and every other test exercise it.

WHAT THIS DEMONSTRATES
----------------------
Pilot Task 12e. Two operational signals that did not exist, and the reason
they were missing was more interesting than either of them:

  1. A note that bundled two facts hid one of them for a week
  2. Why "Bedrock is down" and "we exceeded our quota" MUST NOT look alike
  3. The three names AWS uses, and the one that is deliberately excluded
  4. Where the count is emitted, and why not in the Bedrock adapter
  5. Two alarms that take OPPOSITE dimension decisions, each on purpose

WHO THIS IS FOR
---------------
Whoever is on call. Every claim here is about what a person sees at 3am and
what they should do about it.

EXPECTED RESULT
---------------
Every check prints OK. In particular you should see a `ThrottlingException`
become `ModelThrottled` while a `ValidationException` stays a plain
`ModelError`, and a `ServiceQuotaExceededException` deliberately NOT counted as
a throttle. Exit code 0.
"""

from __future__ import annotations

from _demo_support import LOCAL, heading, mode_banner, note, require, resolve_mode, section

from src.models.base import ModelError, ModelThrottled, ModelTier
from src.models.registry import ModelRegistry
from src.observability import NULL_TELEMETRY, InstrumentedModelClient, TurnStats
from src.observability.base import METRIC_MODEL_THROTTLED

mode = resolve_mode(supports=(LOCAL,))
mode_banner(mode, requires="nothing", mocked="the Bedrock transport only")

heading("DEMO 25 - Telling a quota breach apart from an outage")


# --------------------------------------------- 1. the note that hid a fact

section("1. The deferral note that bundled two facts, and hid one")

note("config/alarms.json said, for a week:")
note("")
note('    "Still absent deliberately: throttling and stale-data alarms,')
note('     because NEITHER HAS A METRIC YET."')
note("")
note("Half of that was true. `TurnError` has carried a `code` dimension since")
note("2026-08-30 -- it is what makes the internal-error alarm possible at all --")
note("so STALE_DATA had been publishing to CloudWatch the whole time. The")
note("missing piece was fifteen lines of alarm.")
note("")
note("A sentence that joins two claims gets read as one claim. That is the")
note("whole finding: a deferral should name each thing it defers separately,")
note("so discharging one is visible.")


# ------------------------------------------------- 2. why they must differ

section("2. Why an outage and a quota breach must not look alike")

note("  Bedrock unreachable   -> escalate; check service health")
note("  We exceeded our quota -> pace, raise the quota, or route elsewhere")
note("")
note("Before this change, `_converse` caught every ClientError and raised one")
note("opaque ModelError. Both incidents produced the identical signal.")
note("")
note("Not hypothetical: Task 16's load gate drove a 21x quota breach and found")
note("14 of 24 turns coming back as CLARIFICATIONS, because a throttled first")
note("call degraded classification and the shopper was asked to rephrase a")
note("request that was already complete.")


# ------------------------------------------------ 3. the classification

section("3. The classification, run against the real client")


def _client(raises: Exception):
    """A BedrockModelClient whose transport raises. No AWS."""
    from src.models.bedrock import BedrockModelClient

    registry = ModelRegistry()
    spec = registry.get("nova-lite")
    client = BedrockModelClient.__new__(BedrockModelClient)
    client._registry = registry
    client._pinned = spec
    client._usage = {}

    class _Stub:
        def converse(self, **kwargs):
            raise raises

    client._client = _Stub()
    return client, spec


def _client_error(code: str, status: int = 400):
    from botocore.exceptions import ClientError

    return ClientError(
        {
            "Error": {"Code": code, "Message": "synthetic"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "Converse",
    )


import os  # noqa: E402

os.environ.pop("BEDROCK_GUARDRAIL_ID", None)
os.environ["REQUIRE_GUARDRAIL"] = "0"

cases = [
    ("ThrottlingException", 400, ModelThrottled, "botocore's standard name"),
    ("TooManyRequestsException", 400, ModelThrottled, "Bedrock Runtime's on-demand path"),
    ("ThrottledException", 400, ModelThrottled, "an SDK variant"),
    ("SomeFutureRateException", 429, ModelThrottled, "unknown name, but HTTP 429"),
    ("ValidationException", 400, ModelError, "a bad request, not a rate refusal"),
    ("AccessDeniedException", 400, ModelError, "permissions"),
    ("ServiceQuotaExceededException", 400, ModelError, "a HARD limit - see below"),
]

for code, status, expected, why in cases:
    client, spec = _client(_client_error(code, status))
    try:
        client._converse(system="s", user="u", spec=spec, max_tokens=32)
        raise AssertionError("the stub should have raised")
    except ModelError as exc:
        actual = type(exc)
        # ModelThrottled subclasses ModelError, so "is it exactly a plain
        # ModelError" is the question, not "is it an instance of".
        throttled = isinstance(exc, ModelThrottled)
        want_throttled = expected is ModelThrottled
        mark = "OK  " if throttled == want_throttled else "FAIL"
        label = "ModelThrottled" if throttled else "ModelError"
        note(f"  [{mark}] {code:32} -> {label:15} ({why})")
        require(throttled == want_throttled, f"{code} classified as {actual}")

note("")
note("ServiceQuotaExceededException is EXCLUDED on purpose. It is a hard")
note("account limit, not a rate: pacing does not fix it, and counting it as a")
note("throttle would put a ticket-to-AWS problem on a graph that says")
note('"slow down".')


# ------------------------------------------------------ 4. where it counts

section("4. Where the metric is emitted, and why not in the adapter")


class _Recorder:
    """A Telemetry that records instead of publishing."""

    def __init__(self) -> None:
        self.counts: list[tuple[str, float, dict]] = []

    def span(self, name: str, **annotations) -> object:
        return NULL_TELEMETRY.span(name, **annotations)

    def count(self, name: str, value: float = 1.0, **dimensions: str) -> None:
        self.counts.append((name, value, dimensions))

    def duration(self, name: str, milliseconds: float, **dimensions: str) -> None:
        pass


class _Inner:
    def __init__(self, error: Exception | None) -> None:
        self._error = error
        self.last_usage: dict = {}

    def structured(self, **kw):
        raise NotImplementedError

    def text(self, *, system, user, tier, max_tokens=1024, task="generate_prose"):
        self.last_usage = {"model_key": "nova-lite"}
        if self._error:
            raise self._error
        return "ok"


for error, label in [
    (ModelThrottled("quota"), "a throttle"),
    (ModelError("upstream is down"), "an outage"),
    (None, "a successful call"),
]:
    telemetry = _Recorder()
    wrapped = InstrumentedModelClient(_Inner(error), telemetry, TurnStats())  # type: ignore[arg-type]
    try:
        wrapped.text(system="s", user="u", tier=ModelTier.FAST, task="generate_prose")
    except ModelError:
        pass
    counted = [c for c in telemetry.counts if c[0] == METRIC_MODEL_THROTTLED]
    note(f"  {label:22} -> ModelThrottled counted {len(counted)} time(s)")
    if counted:
        note(f"                            dimensions {counted[0][2]}")

note("")
note("Counted at the INSTRUMENTED SEAM, not in the Bedrock adapter, so the")
note("model plane still imports no observability -- which is what keeps the")
note("eval harness runnable with no AWS account. Same seam that puts")
note("src/retrieval/dynamo.py beside memory.py.")
note("")
note("Over-reporting would be as bad as under-reporting: a metric that counted")
note("every failure would breach its threshold during any outage, and the")
note('operator would learn to read "throttled" as "something is wrong".')


# -------------------------------------------------- 5. two alarms, two ways

section("5. Two alarms that take OPPOSITE dimension decisions")

import json  # noqa: E402
from pathlib import Path  # noqa: E402

alarms = json.loads(
    (Path(__file__).resolve().parent.parent / "config" / "alarms.json").read_text(encoding="utf-8")
)
by_name = {a["name"]: a for a in alarms["alarms"]}

stale = by_name["grocery-orchestrator-stale-data-dev"]
throttle = by_name["grocery-orchestrator-model-throttled-dev"]

note(f"  stale data  metric={stale['metric_name']:14} dimensions={stale['dimensions']}")
note(f"  throttling  metric={throttle['metric_name']:14} dimensions={throttle['dimensions']}")
note("")
note("STALE DATA IS DIMENSIONED, because TurnError is also emitted for NO_DATA")
note("and BUDGET_INFEASIBLE -- correct answers at healthy volume. Undimensioned")
note("it would page on every honest refusal the service makes.")
note("")
note("THROTTLING IS NOT, because a quota is shared across models and tasks.")
note("Binding it to one pair would leave every other pair unwatched while")
note("reading as coverage. The dimensions exist for diagnosis in the console;")
note("the alarm wants the total.")
note("")
note("Each choice looks wrong from the other's side, so each carries a test.")

require(
    stale["dimensions"].get("code") == "STALE_DATA",
    "stale['dimensions'].get('code') == 'STALE_DATA'",
)
require(throttle["dimensions"] == {}, "throttle['dimensions'] == {}")
note("")
note("  [OK  ] both dimension decisions verified against config/alarms.json")


# ------------------------------------------------------------ 6. the cliff

section("6. The stale-data alarm is the control on a dated risk")

freshness = json.loads(
    (Path(__file__).resolve().parent.parent / "config" / "freshness.json").read_text(
        encoding="utf-8"
    )
)
note(f"  max_price_age_days = {freshness['max_price_age_days']}")
note("  catalogue capture  = 2026-08-28 (a constant; no source can stamp a newer one)")
note("")
note("So on 2026-10-12 every priced query starts returning STALE_DATA. This")
note("alarm is what turns that from a date somebody has to remember into a")
note("page on the day it happens.")

note("")
note("NOT YET DEPLOYED: both alarms wait on the observability migration, which")
note("is deferred until after the demo by decision (ARCHITECTURE.md 3y.A).")
note("The METRICS are live on the CDK plane, so both will have history the")
note("moment the alarms exist.")

print("\nDone.")
