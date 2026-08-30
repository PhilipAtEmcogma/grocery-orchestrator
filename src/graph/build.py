"""
Graph assembly.

    START
      v
  validate_input
      v
  classify_intent
      |--- general_chat / out_of_scope --------------------------> finalise
      |--- meal_plan + unsupported exclusion --> emit_dietary_unsupported -> finalise
      |--- meal_plan + missing constraint -----> emit_clarification ------> finalise
      v
  retrieve_prices            <-- the ONLY source of prices
      |--- no citations -----> emit_no_data ---------------------> finalise
      |--- all prices stale -> emit_stale_data -------------------> finalise
      |--- budget impossible -> emit_budget_infeasible -----------> finalise
      |--- price_check ------> generate_comparison -> generate_prose -> finalise
      v (meal_plan)
  generate_plan  <----------------+
      v                           |
  validate_plan                   | repair (bounded)
      |--- model unreachable ----> emit_upstream_failure --------> finalise
      |--- errors ---> repair_plan+
      |--- exhausted, over budget -> emit_budget_infeasible ------> finalise
      |--- exhausted, invalid -----> emit_plan_generation_failed -> finalise
      v ok
  generate_prose -> finalise -> END

The upstream_failure branch exists so that "we could not reach the model" and
"your budget does not stretch" cannot collapse into the same message. They are
different facts about different things, only one is retryable, and conflating
them told users to raise a budget that was never the problem.

Two safety guarantees are the shape itself:

* generate_* is unreachable except through retrieve_prices (grounding,
  Invariant 1). There is no edge that skips it.
* A meal_plan turn with a stated dietary exclusion we cannot map refuses
  BEFORE retrieval (Invariant 3). We do not do the work for a plan we
  cannot safely verify — see src/graph/dietary.py for the mapping and the
  fail-closed rule.
"""

from __future__ import annotations

from functools import lru_cache, partial

from langgraph.graph import END, START, StateGraph

from src.graph import nodes
from src.graph.state import GroceryState
from src.models.base import ModelClient
from src.retrieval.base import PriceRepository

#: How many distinct (repo, model) pairs `compiled_graph` keeps. Production has
#: exactly one -- `handler._dependencies()` caches both at module scope -- so
#: this bound exists to stop a long-lived process that churns dependencies from
#: pinning every one it ever saw, not to serve a real workload.
MAX_CACHED_GRAPHS = 8


def build_graph(repo: PriceRepository, model: ModelClient):
    g = StateGraph(GroceryState)

    g.add_node("validate_input", nodes.validate_input)
    g.add_node("classify_intent", partial(nodes.classify_intent, model=model))
    g.add_node("retrieve_prices", partial(nodes.retrieve_prices, repo=repo))
    g.add_node("emit_no_data", nodes.emit_no_data)
    g.add_node("emit_stale_data", nodes.emit_stale_data)
    g.add_node("emit_unknown_region", nodes.emit_unknown_region)
    g.add_node("emit_dietary_unsupported", nodes.emit_dietary_unsupported)
    g.add_node("emit_clarification", nodes.emit_clarification)
    g.add_node("generate_comparison", nodes.generate_comparison)
    g.add_node("generate_plan", partial(nodes.generate_plan, model=model))
    g.add_node("validate_plan", nodes.validate_plan)
    g.add_node("repair_plan", nodes.repair_plan)
    g.add_node("emit_budget_infeasible", nodes.emit_budget_infeasible)
    g.add_node("emit_upstream_failure", nodes.emit_upstream_failure)
    g.add_node("emit_plan_generation_failed", nodes.emit_plan_generation_failed)
    g.add_node("generate_prose", partial(nodes.generate_prose, model=model))
    g.add_node("finalise", nodes.finalise)

    g.add_edge(START, "validate_input")
    g.add_edge("validate_input", "classify_intent")

    g.add_conditional_edges(
        "classify_intent",
        nodes.route_after_intent,
        {
            "retrieve": "retrieve_prices",
            "dietary_unsupported": "emit_dietary_unsupported",
            "clarify": "emit_clarification",
            "finalise": "finalise",
        },
    )
    g.add_edge("emit_dietary_unsupported", "finalise")
    g.add_edge("emit_clarification", "finalise")

    g.add_conditional_edges(
        "retrieve_prices",
        nodes.route_after_retrieval,
        {
            "no_data": "emit_no_data",
            "stale": "emit_stale_data",
            "unknown_region": "emit_unknown_region",
            "comparison": "generate_comparison",
            "plan": "generate_plan",
            "infeasible": "emit_budget_infeasible",
        },
    )

    g.add_edge("emit_no_data", "finalise")
    g.add_edge("emit_stale_data", "finalise")
    g.add_edge("emit_unknown_region", "finalise")
    g.add_edge("generate_comparison", "generate_prose")
    g.add_edge("generate_plan", "validate_plan")

    # The repair cycle. This is the edge Lambda-only orchestration cannot
    # express naturally, and the reason LangGraph earns its place here.
    g.add_conditional_edges(
        "validate_plan",
        nodes.route_after_validation,
        {
            "finalise": "generate_prose",
            "repair": "repair_plan",
            "infeasible": "emit_budget_infeasible",
            "upstream_failed": "emit_upstream_failure",
            "generation_failed": "emit_plan_generation_failed",
        },
    )
    g.add_edge("repair_plan", "generate_plan")
    g.add_edge("emit_budget_infeasible", "finalise")
    g.add_edge("emit_upstream_failure", "finalise")
    g.add_edge("emit_plan_generation_failed", "finalise")
    g.add_edge("generate_prose", "finalise")
    g.add_edge("finalise", END)

    return g.compile()


@lru_cache(maxsize=MAX_CACHED_GRAPHS)
def compiled_graph(repo: PriceRepository, model: ModelClient):
    """
    `build_graph`, memoised on the dependency pair. Call this from a hot path.

    Compiling is not free: measured at **13.4 ms**, which was 78% of an offline
    turn and was repeated on every request because `build_graph` closes over
    the repository and model client. The handler already caches both at module
    scope for exactly this reason (`_dependencies()`), so re-compiling threw
    that saving away one layer further down.

    KEEP THE SAVING IN PROPORTION. A deployed price check has a p95 of 2.21s
    and a meal plan 12.2s, both dominated by Bedrock, so 13 ms is well under 1%
    of a real turn -- this is free rather than important, and it should not be
    quoted as a latency fix. Where it is worth real time is the offline suite
    and the scripted paths, where the compile IS most of the work.

    KEYED ON IDENTITY, WHICH IS THE PROPERTY THAT MATTERS. Neither
    `PriceRepository` nor `ModelClient` implementations define `__eq__`, so
    `lru_cache` hashes them by identity and two different repositories can
    never share a graph. That is not a nicety: `InMemoryPriceRepository` takes
    a fixture path, so a cache that collapsed two of them would answer a turn
    from the wrong catalogue while every assertion in the system passed -- the
    graph would be internally consistent and simply about the wrong data, which
    is the one failure this codebase exists to prevent. `tests/test_graph.py`
    pins both halves: the same pair reuses, a different pair does not.

    Holding the arguments as cache keys also pins them, so their `id()` cannot
    be recycled onto a later object. Bounded by `MAX_CACHED_GRAPHS`.

    THE COMPILED GRAPH CARRIES NO PER-TURN STATE. Nodes are `state -> partial
    state` and every invocation is handed its own state dict, so reuse is safe.
    Nothing here is thread-safe by design and nothing needs to be: Lambda
    serves one request per execution environment, and `scripts/dev_server.py`
    uses `HTTPServer`, not `ThreadingHTTPServer`.

    A graph resolves its node functions from `src.graph.nodes` AT BUILD TIME,
    so a test that monkeypatches a node must clear this cache first or it will
    be served a graph built against the unpatched module. `tests/conftest.py`
    clears it around every test, so no test depends on that by accident.
    """
    return build_graph(repo, model)


def clear_graph_cache() -> None:
    """Drop every memoised graph. For tests that patch a node module."""
    compiled_graph.cache_clear()
