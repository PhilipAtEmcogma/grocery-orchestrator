"""
Graph assembly.

    START
      v
  validate_input
      v
  classify_intent
      |--- general_chat / out_of_scope ------------------> finalise
      v
  retrieve_prices            <-- the ONLY source of prices
      |--- no citations -----> emit_no_data -------------> finalise
      |--- price_check ------> generate_comparison ------> finalise
      v (meal_plan)
  generate_plan  <----------------+
      v                           |
  validate_plan                   | repair (bounded)
      |--- errors ---> repair_plan+
      |--- attempts exhausted ---> emit_budget_infeasible -> finalise
      v ok
  finalise -> END

The grounding guarantee is the shape itself: generate_* is unreachable except
through retrieve_prices. There is no edge that skips it.

New to LangGraph? The 30-second model:
  - A StateGraph is a state machine. `GroceryState` (src/graph/state.py) is
    the shared dict every node reads from and writes to.
  - A NODE is a plain function `(state) -> dict`. Its return value is a
    *partial* update — only the keys it changed — which LangGraph merges
    into the running state before calling the next node.
  - An EDGE says "after node A, run node B". A CONDITIONAL edge instead
    calls a small router function that inspects the state and returns a
    string key, which selects which node runs next from the dict passed to
    `add_conditional_edges` (see `route_after_intent` etc. in nodes/__init__.py).
  - `.compile()` turns the declared nodes/edges into an executable object
    with an `.invoke(initial_state)` method — that's what `runner.py` calls.
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from src.graph import nodes
from src.graph.state import GroceryState
from src.models.base import ModelClient
from src.retrieval.base import PriceRepository


def build_graph(repo: PriceRepository, model: ModelClient):
    """Assemble and compile the LangGraph state machine shown in the diagram above.

    `repo` (price data) and `model` (LLM client) are injected here via
    functools.partial, so the node functions themselves stay free of any
    concrete AWS/boto3 dependency and can be swapped out (e.g. for tests).
    """
    g = StateGraph(GroceryState)

    # ---- register every node function under the name used in edges below.
    # partial() binds the repo/model dependency so each node still matches
    # the (state) -> dict signature LangGraph expects.
    g.add_node("validate_input", nodes.validate_input)
    g.add_node("classify_intent", partial(nodes.classify_intent, model=model))
    g.add_node("retrieve_prices", partial(nodes.retrieve_prices, repo=repo))
    g.add_node("emit_no_data", nodes.emit_no_data)
    g.add_node("generate_comparison", nodes.generate_comparison)
    g.add_node("generate_plan", partial(nodes.generate_plan, model=model))
    g.add_node("validate_plan", nodes.validate_plan)
    g.add_node("repair_plan", nodes.repair_plan)
    g.add_node("emit_budget_infeasible", nodes.emit_budget_infeasible)
    g.add_node("finalise", nodes.finalise)

    # ---- unconditional edges: always run validate_input then classify_intent.
    g.add_edge(START, "validate_input")
    g.add_edge("validate_input", "classify_intent")

    # After classifying, either fetch prices (price_check/meal_plan) or skip
    # straight to finalise (general_chat/out_of_scope). The router function's
    # return value selects the edge by matching a key in this dict.
    g.add_conditional_edges(
        "classify_intent",
        nodes.route_after_intent,
        {"retrieve": "retrieve_prices", "finalise": "finalise"},
    )

    # After retrieval, branch on what was found and what the user wanted:
    # no results -> honest "no data" message; otherwise a comparison or a
    # full meal plan depending on intent.
    g.add_conditional_edges(
        "retrieve_prices",
        nodes.route_after_retrieval,
        {
            "no_data": "emit_no_data",
            "comparison": "generate_comparison",
            "plan": "generate_plan",
        },
    )

    g.add_edge("emit_no_data", "finalise")
    g.add_edge("generate_comparison", "finalise")
    g.add_edge("generate_plan", "validate_plan")

    # The repair cycle. This is the edge Lambda-only orchestration cannot
    # express naturally, and the reason LangGraph earns its place here.
    # After validating a generated plan: pass through if it's fine, loop
    # back for another generation attempt if it's fixable, or give up
    # honestly if the attempt budget (MAX_REPAIR_ATTEMPTS) is exhausted.
    g.add_conditional_edges(
        "validate_plan",
        nodes.route_after_validation,
        {
            "finalise": "finalise",
            "repair": "repair_plan",
            "infeasible": "emit_budget_infeasible",
        },
    )
    g.add_edge("repair_plan", "generate_plan")
    g.add_edge("emit_budget_infeasible", "finalise")
    g.add_edge("finalise", END)

    # Compile turns the declared nodes/edges into an executable graph object.
    return g.compile()
