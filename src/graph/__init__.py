"""
The LangGraph state machine and the nodes that make it up.

  build.py    assembles the graph: which nodes exist and which edges connect
              them. Read this file first — the diagram in its docstring is
              the whole turn's control flow in one picture.
  state.py    GroceryState, the dict every node reads from and writes to.
  nodes/      one function per node, grouped by what they do (retrieval,
              validation, intent classification, plan generation).
"""
