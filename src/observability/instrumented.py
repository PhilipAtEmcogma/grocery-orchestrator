"""
Instrumented decorators over the two protocol boundaries.

These are how the latency breakdown gets measured WITHOUT the graph knowing
that observability exists. `PriceRepository` and `ModelClient` are already
Protocols with swappable implementations; a decorator that implements the
same Protocol and delegates is therefore invisible to every node. The handler
wraps the cached dependencies once per turn, the graph is untouched, and the
eval harness — which builds its own client and never goes through the handler
— is unaffected.

Nothing here imports Powertools. It talks to `Telemetry`, which is a no-op
unless the handler installed the real one.

WHY THERE IS NO SINGLE "repair loop" SPAN. The loop spans four graph nodes
(generate_plan -> validate_plan -> repair_plan -> generate_plan), so wrapping
it as one unit would mean the graph itself opening and closing a span. What
comes out instead is one span per attempt — `model.generate_plan` with
attempt=0, then `model.repair_plan` with attempt=1, 2 — which is a finer
breakdown than a single opaque total: for the 29-second ceiling question, the
useful number is what each attempt costs, not just what they cost together.
`plan_ms` on TurnStats carries the total for the metric.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from src.models.base import ModelClient, ModelTier, T
from src.observability.base import (
    METRIC_MODEL_LATENCY,
    Telemetry,
    TurnStats,
)
from src.retrieval.base import PriceRecord, PriceRepository

if TYPE_CHECKING:
    from src.schemas.contract import Store


class InstrumentedPriceRepository(PriceRepository):
    """
    Spans around every retrieval call.

    One span per call rather than one per turn: a price check resolves each
    item separately, and "which lookup was slow" is a question the aggregate
    cannot answer. The per-turn total lands on TurnStats for the metric.
    """

    def __init__(
        self, inner: PriceRepository, telemetry: Telemetry, stats: TurnStats
    ) -> None:
        self._inner = inner
        self._telemetry = telemetry
        self._stats = stats

    def cheapest_for_product(
        self,
        product_key: str,
        *,
        limit: int = 5,
        stores: list[Store] | None = None,
    ) -> list[PriceRecord]:
        with self._span("cheapest_for_product") as span:
            started = time.perf_counter()
            try:
                found = self._inner.cheapest_for_product(
                    product_key, limit=limit, stores=stores
                )
            finally:
                self._record(started)
            # Counts, not the product key. The key is a catalogue identifier
            # rather than the user's words, but indexed against a session id
            # it still reports what someone was shopping for, and X-Ray
            # annotations are queryable. Req 11.5 is about logs; extending
            # the same rule to traces costs one debugging convenience and
            # removes a whole class of question about them.
            span.annotate(records=len(found), limit=limit)
            return found

    def resolve_product_key(self, user_term: str) -> str | None:
        with self._span("resolve_product_key") as span:
            started = time.perf_counter()
            try:
                key = self._inner.resolve_product_key(user_term)
            finally:
                self._record(started)
            # `user_term` is the user's text and is deliberately NOT
            # annotated (Req 11.5). Whether it resolved is the useful signal:
            # a rise in unresolved terms is a catalogue gap.
            span.annotate(resolved=key is not None)
            return key

    def candidates_for_budget(
        self,
        *,
        categories: list[str],
        exclude_categories: list[str],
        limit_per_category: int = 3,
    ) -> list[PriceRecord]:
        with self._span("candidates_for_budget") as span:
            started = time.perf_counter()
            try:
                found = self._inner.candidates_for_budget(
                    categories=categories,
                    exclude_categories=exclude_categories,
                    limit_per_category=limit_per_category,
                )
            finally:
                self._record(started)
            # The COUNT of excluded categories, never which ones: the
            # exclusion list is derived from dietary restrictions (Req 11.5).
            span.annotate(
                categories=len(categories),
                excluded_categories=len(exclude_categories),
                records=len(found),
            )
            return found

    # ------------------------------------------------------------ internals

    def _span(self, operation: str):
        return self._telemetry.span(f"retrieval.{operation}", operation=operation)

    def _record(self, started: float) -> None:
        self._stats.retrieval_calls += 1
        self._stats.retrieval_ms += _elapsed_ms(started)


class InstrumentedModelClient(ModelClient):
    """
    A span and a latency metric per model call.

    `last_usage` is read in a `finally` on purpose. A guardrail intervention
    raises, and the usage dict recording that intervention is populated
    before the raise — so the blocked turn still reports which model blocked
    it and how long it took, which is exactly the turn you want the numbers
    for.
    """

    def __init__(
        self, inner: ModelClient, telemetry: Telemetry, stats: TurnStats
    ) -> None:
        self._inner = inner
        self._telemetry = telemetry
        self._stats = stats
        self._calls_by_task: dict[str, int] = {}

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
        with self._call(task=task, tier=tier, schema=schema.__name__):
            return self._inner.structured(
                system=system,
                user=user,
                schema=schema,
                tier=tier,
                max_tokens=max_tokens,
                task=task,
            )

    def text(
        self,
        *,
        system: str,
        user: str,
        tier: ModelTier,
        max_tokens: int = 1024,
        task: str = "generate_prose",
    ) -> str:
        with self._call(task=task, tier=tier, schema="text"):
            return self._inner.text(
                system=system, user=user, tier=tier, max_tokens=max_tokens, task=task
            )

    @property
    def last_usage(self) -> dict:
        return self._inner.last_usage

    # ------------------------------------------------------------ internals

    @contextmanager
    def _call(self, *, task: str, tier: ModelTier, schema: str):
        """Owns the span, the timing and the accounting for one model call."""
        # Attempt index within this turn. For plan generation this is the
        # repair count, which is what makes each attempt distinguishable in
        # the trace.
        attempt = self._calls_by_task.get(task, 0)
        self._calls_by_task[task] = attempt + 1

        # `last_usage` reports the client's MOST RECENT call, which is this
        # one only if this one got far enough to record anything. Snapshotting
        # first means a call that failed early is not charged the previous
        # call's tokens — double-counting that would show up as a token spike
        # on exactly the turns that failed.
        previous_usage = self._inner.last_usage or {}
        failed = False

        with self._telemetry.span(
            f"model.{task}", task=task, tier=tier.value, attempt=attempt
        ) as span:
            started = time.perf_counter()
            try:
                yield
            except BaseException:
                failed = True
                raise
            finally:
                elapsed_ms = _elapsed_ms(started)
                usage = self._inner.last_usage or {}
                # A guardrail intervention raises AFTER recording usage, so
                # the blocked call keeps its numbers — which is the call you
                # most want them for.
                if failed and usage == previous_usage:
                    usage = {}
                model = _model_label(usage)

                self._stats.record_model(
                    model=model, task=task, elapsed_ms=elapsed_ms, usage=usage
                )
                span.annotate(
                    model=model,
                    schema=schema,
                    latency_ms=elapsed_ms,
                    input_tokens=_int(usage.get("input_tokens")),
                    output_tokens=_int(usage.get("output_tokens")),
                    cache_read_tokens=_int(usage.get("cache_read_tokens")),
                    guardrail_intervened=bool(usage.get("guardrail_intervened")),
                )
                # Dimensioned, so latency is comparable per model and per
                # task — the number Task 10.5 needs to attribute the plan
                # path's share of the 29-second ceiling.
                self._telemetry.duration(
                    METRIC_MODEL_LATENCY, elapsed_ms, model=model, task=task
                )


def _elapsed_ms(started: float) -> float:
    """Milliseconds to microsecond resolution — see TurnStats on why not int."""
    return round((time.perf_counter() - started) * 1000, 3)


def _model_label(usage: dict) -> str:
    """
    Which model served the call, as a bounded metric dimension.

    `model_key` is the registry key ('claude-haiku'), which is what the
    routing policy is expressed in and therefore what an operator compares.
    Falls back to the raw id, then to 'unknown' — never to a guess that would
    silently attribute latency to the wrong model.
    """
    key = usage.get("model_key")
    if key:
        return str(key)
    ids = usage.get("model_ids") or []
    return str(ids[0]) if ids else "unknown"


def _int(value: object) -> int:
    return int(value) if isinstance(value, int | float) else 0
