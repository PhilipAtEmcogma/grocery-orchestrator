"""
Shared helpers for the demos. Not a demo itself.

Every demo imports from here so the files stay about the FEATURE rather than
about printing. Nothing in this module touches AWS.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

# The demos live in a subfolder, so the repo root has to be importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.schemas.contract import ChatRequest, ChatResponse, ClientHints

RULE = "=" * 74


def heading(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


def section(title: str) -> None:
    print(f"\n--- {title} ---")


def request(message: str, *, turn: str = "turn-demo01", **hints) -> ChatRequest:
    """
    Build a ChatRequest.

    session_id and turn_id have an 8-character minimum in the contract, which
    is the first thing that catches people writing their own demo scripts.
    """
    return ChatRequest(
        session_id="sess-demo01",
        turn_id=turn,
        message=message,
        hints=ClientHints(**hints) if hints else None,
    )


def citations(response: ChatResponse) -> dict:
    """
    ref -> Citation.

    Needed by almost every demo, because a PriceOption carries only a
    `citation_ref` and no price. That is the grounding design, not an
    oversight: to show a number you must go and look up the cited record,
    which is exactly the step that makes an invented price impossible.
    """
    return {e.citation.ref: e.citation for e in response.events if e.type == "citation"}


def show_events(response: ChatResponse, *, skip: tuple[str, ...] = ("session",)) -> None:
    """Print the event stream the frontend would receive, one line per event."""
    index = citations(response)
    for ev in response.events:
        if ev.type in skip:
            continue
        print(f"  [{ev.seq:>2}] {ev.type:<16} {_summarise(ev, index)}")


def _summarise(ev, index: dict) -> str:
    if ev.type == "citation":
        c = ev.citation
        return f"{c.ref} {c.product_name} ${c.price_nzd} @ {c.store.value}"
    if ev.type == "price_comparison":
        d = ev.data
        cheapest = next((o for o in d.options if o.is_cheapest), d.options[0])
        cited = index.get(cheapest.citation_ref)
        price = f"${cited.price_nzd}" if cited else f"[{cheapest.citation_ref}]"
        return f"{d.query_item}: {len(d.options)} options, cheapest {price}"
    if ev.type == "meal_plan":
        p = ev.data
        return (
            f"{len(p.meals)} meals, ${p.total_nzd} of ${p.budget_nzd} "
            f"(within_budget={p.within_budget}, repairs={p.repair_attempts})"
        )
    if ev.type == "error":
        return f"{ev.code.value} retryable={ev.retryable} :: {ev.message[:60]}"
    if ev.type == "no_data":
        return f"{ev.requested_item}: {ev.message}"
    if ev.type == "notice":
        return ev.message[:80]
    if ev.type == "token":
        return repr(ev.text[:60])
    return ""


def money(value: Decimal) -> str:
    return f"${value}"
