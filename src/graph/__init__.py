"""
The LangGraph state machine.

  build.py    Assembles the graph — start here to see the whole flow as a
              topology diagram.
  state.py    GroceryState — what every node reads from and writes to.
  nodes/      What each step in the diagram actually does. classify_intent,
              generate_plan and generate_prose are model-backed; everything
              else (retrieval, validation, repair bookkeeping, routing,
              finalise) is plain code.

Nodes are functions of state -> partial state, which is what makes them
independently unit-testable without running the whole graph.
"""
