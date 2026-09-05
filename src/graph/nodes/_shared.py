"""
Helpers two node modules both need.

`_next_seq` and `_join` were in `src/graph/nodes/__init__.py` when every node
lived there. Splitting retrieval out (2026-08-31) left them wanted at both ends,
and `retrieval.py` importing them from the package `__init__` that imports
`retrieval.py` is a cycle -- the same one `src/graph/recipe_plan.py` hit, which
closed only when it was imported first and so was invisible from every entry
point the graph uses.

A leaf module both can import is the version of that with no ordering to get
wrong.
"""

from __future__ import annotations

from src.graph.state import GroceryState


def _next_seq(state: GroceryState) -> int:
    return len(state.get("events", []))


def _join(items: list[str]) -> str:
    """Human list: 'a', 'a and b', 'a, b and c'. Truncated per item, not overall."""
    clean = [i[:80] for i in items]
    if len(clean) == 1:
        return clean[0]
    return f"{', '.join(clean[:-1])} and {clean[-1]}"
