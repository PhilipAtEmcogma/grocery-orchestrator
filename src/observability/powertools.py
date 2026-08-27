"""
The Powertools implementation of the observability boundary.

THIS IS THE ONLY MODULE IN THE REPO THAT IMPORTS `aws_lambda_powertools`, and
it is imported only by `src/handler.py`. The graph, the runner, the model
plane, the retrieval layer and both eval harnesses stay free of it, so they
keep running with no AWS account — which is what makes CI credential-free.
`tests/test_observability.py` asserts that boundary rather than trusting it.

Three utilities, three jobs:

* **Logger** — structured JSON on stdout, correlated by `session_id`, with
  the Lambda context and the cold-start flag injected. `clear_state=True` is
  not optional: appended keys otherwise survive into the next invocation of a
  warm execution environment, and one turn's identifiers appearing on another
  turn's log line is both a correctness bug and a privacy one.

* **Tracer** — X-Ray subsegments. Powertools disables itself outside Lambda
  (it checks `LAMBDA_TASK_ROOT`), so the dev server and the test suite emit
  nothing and need no daemon; `tests/test_observability.py` re-enables the
  X-Ray SDK deliberately to assert the subsegments exist.

* **Metrics** — embedded metric format on stdout, so CloudWatch extracts the
  metrics with no PutMetricData call on the request path.

Dimensioned metrics go out as their own EMF record via `single_metric`.
Powertools' aggregate `Metrics` object applies one dimension set to every
metric in its record, so per-model latency cannot live there without
dimensioning the whole turn by model.
"""

from __future__ import annotations

import logging as _logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit, single_metric

if TYPE_CHECKING:
    from collections.abc import Iterator

SERVICE_NAME = os.environ.get("POWERTOOLS_SERVICE_NAME", "grocery-orchestrator")
METRICS_NAMESPACE = os.environ.get("POWERTOOLS_METRICS_NAMESPACE", "GroceryOrchestrator")

logger = Logger(service=SERVICE_NAME, level=os.environ.get("LOG_LEVEL", "INFO"))
tracer = Tracer(service=SERVICE_NAME)
metrics = Metrics(namespace=METRICS_NAMESPACE, service=SERVICE_NAME)

# Suppress SDK transport loggers unconditionally. At DEBUG they dump the full
# request body (which is the user's message and constraints) into CloudWatch.
# This is Req 11.5: no PII in logs, regardless of the application log level.
# The Lambda runtime sets these to WARNING by default, but an explicit
# LOG_LEVEL=DEBUG would propagate downward without this clamp.
for _sdk_logger_name in ("botocore", "urllib3", "boto3"):
    _logging.getLogger(_sdk_logger_name).setLevel(_logging.WARNING)


class _XraySpan:
    """A live X-Ray subsegment, annotated through the Telemetry protocol."""

    __slots__ = ("_subsegment",)

    def __init__(self, subsegment) -> None:
        self._subsegment = subsegment

    def annotate(self, **annotations: str | int | float | bool) -> None:
        for key, value in annotations.items():
            # X-Ray indexes annotations and rejects None. Skipping rather
            # than coercing keeps a missing value distinguishable from a
            # zero one when reading a trace.
            if value is None:
                continue
            self._subsegment.put_annotation(key=key, value=value)


class PowertoolsTelemetry:
    """
    Telemetry backed by Powertools' Tracer and Metrics.

    Holds no per-turn state: everything turn-scoped lives on `TurnStats`,
    which the handler creates fresh per invocation. A warm execution
    environment reuses this object across turns and must not accumulate
    anything.
    """

    __slots__ = ()

    @contextmanager
    def span(self, name: str, **annotations: str | int | float | bool) -> Iterator[_XraySpan]:
        with tracer.provider.in_subsegment(name=name) as subsegment:
            span = _XraySpan(subsegment)
            span.annotate(**annotations)
            yield span

    def count(self, name: str, value: float = 1.0, **dimensions: str) -> None:
        self._emit(name, MetricUnit.Count, value, dimensions)

    def duration(self, name: str, milliseconds: float, **dimensions: str) -> None:
        self._emit(name, MetricUnit.Milliseconds, milliseconds, dimensions)

    @staticmethod
    def _emit(name: str, unit: MetricUnit, value: float, dimensions: dict[str, str]) -> None:
        if not dimensions:
            metrics.add_metric(name=name, unit=unit, value=value)
            return

        # Its own EMF record, so these dimensions apply to this metric alone.
        with single_metric(
            name=name,
            unit=unit,
            value=value,
            namespace=METRICS_NAMESPACE,
            default_dimensions={"service": SERVICE_NAME},
        ) as metric:
            for key, dimension_value in dimensions.items():
                metric.add_dimension(name=key, value=str(dimension_value))


TELEMETRY = PowertoolsTelemetry()


# Static conformance, the other half of the pair in `base.py` — see the note
# there for why an annotated binding is the only thing that actually checks
# this, and why this half cannot live in that module. `type[Span]` because
# `_XraySpan` needs a live subsegment to instantiate and this must not
# construct one; it still checks that INSTANCES satisfy the protocol.
if TYPE_CHECKING:
    from src.observability.base import Span, Telemetry

    _telemetry_conforms: Telemetry = TELEMETRY
    _span_conforms: type[Span] = _XraySpan


@dataclass(frozen=True)
class LocalLambdaContext:
    """
    Stand-in for the Lambda context object.

    The dev server and the test suite call the handler directly and have no
    context to pass. Substituting this keeps observability on ONE code path
    instead of making the decorators conditional on where the handler runs —
    the tests then exercise the same wrapped handler that Lambda invokes,
    which is the point of testing it at all.
    """

    function_name: str = f"{SERVICE_NAME}-local"
    memory_limit_in_mb: int = 512
    invoked_function_arn: str = f"arn:aws:lambda:local:000000000000:function:{SERVICE_NAME}"
    aws_request_id: str = "local-invocation"

    def get_remaining_time_in_millis(self) -> int:
        return 29_000
