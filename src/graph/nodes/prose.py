"""
Prose node.

Generates explanatory text, renders placeholders into non-monetary labels from
retrieved records, and validates that no literal money survived.

DEGRADATION: prose is a nicety. If generation or validation fails, the turn
still delivers the structured payload — a shopper gets their comparison table
without a sentence above it. Failing the whole turn because the explanation
did not render would be the wrong trade.
"""

from __future__ import annotations

import re

from src.graph.state import GroceryState, usage_from
from src.models.base import GuardrailBlocked, ModelClient, ModelError, ModelTier
from src.prompts.prose import (
    MEAL_PLAN_SYSTEM,
    PRICE_CHECK_SYSTEM,
    ProseResult,
    assert_no_literal_money,
    build_meal_plan_prompt,
    build_price_check_prompt,
    referenced_placeholders,
)
from src.schemas.contract import Citation, Intent, TokenEvent

PLACEHOLDER_RE = re.compile(r"\[\[(c\d+|total|budget|savings)\]\]")

# Sentence split, so events are already stream-shaped for the WebSocket
# upgrade. Over REST they simply arrive together.
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _next_seq(state: GroceryState) -> int:
    return len(state.get("events", []))


# Retailers have specific capitalisation. Title-casing the enum value gives
# "Paknsave" and "New World" — one of which is wrong in a way a New Zealand
# reader notices immediately.
STORE_NAMES = {
    "paknsave": "Pak'nSave",
    "woolworths": "Woolworths",
    "new_world": "New World",
}


def store_name(value: str) -> str:
    return STORE_NAMES.get(value, value.replace("_", " ").title())


def _describe(citation: Citation) -> str:
    """How a citation reads inside a sentence — non-monetary label only."""
    return (
        f"{citation.product_name} at {store_name(citation.store.value)} "
        f"{citation.store_location}"
    )


def _placeholder_list(citations: list[Citation]) -> str:
    return "\n".join(
        f"  [[{c.ref}]] — {c.product_name}, "
        f"{store_name(c.store.value)} {c.store_location}"
        f"{' (on special)' if c.on_special else ''}"
        for c in citations
    )


def render(text: str, citations: dict[str, Citation], figures: dict[str, str]) -> str:
    """
    Expand placeholders into verified non-monetary labels.

    An unknown placeholder raises rather than being left visible or silently
    dropped: a shopper reading "cheapest at [[c9]]" has been shown a defect,
    and dropping it would produce a sentence missing its subject.
    """

    def sub(match: re.Match[str]) -> str:
        token = match.group(1)
        if token in figures:
            return figures[token]
        citation = citations.get(token)
        if citation is None:
            raise KeyError(token)
        return _describe(citation)

    return PLACEHOLDER_RE.sub(sub, text)


def generate_prose(state: GroceryState, model: ModelClient) -> dict:
    citations = state.get("citations") or []
    if not citations:
        return {}

    citation_index = state.get("citation_index") or {}
    intent = state.get("intent")
    plan = state.get("plan")

    figures: dict[str, str] = {}
    # Bound here rather than in the PRICE_CHECK branch alone: the check after
    # generation reads it, and a name assigned on only one branch is unbound
    # on the others as far as the type checker -- and a meal-plan turn -- are
    # concerned.
    cheapest_refs: list[str] = []

    if intent == Intent.MEAL_PLAN and plan is not None:
        figures["total"] = "the plan total"
        figures["budget"] = "your budget"

        used = [i.citation_ref for m in plan.meals for i in m.ingredients]
        reused = sorted(
            {
                citation_index[ref].product_name
                for ref in used
                if used.count(ref) > 1 and ref in citation_index
            }
        )
        in_plan = [citation_index[r] for r in dict.fromkeys(used) if r in citation_index]

        system = MEAL_PLAN_SYSTEM
        user = build_meal_plan_prompt(
            days=plan.days,
            household_size=plan.household_size,
            exclusions=plan.dietary_exclusions_applied,
            placeholders=_placeholder_list(in_plan),
            stores=[
                f"{store_name(b.store.value)} {b.store_location}"
                for b in plan.baskets
            ],
            reused=reused,
        )
    elif intent == Intent.PRICE_CHECK:
        figures["savings"] = "the price difference"

        groups = state.get("item_groups") or {}
        # One winner per item the shopper asked about. retrieve_prices fills
        # each group from cheapest_for_product, which reads GSI1's zero-padded
        # price sort key, so refs[0] is that item's cheapest and equal prices
        # resolve by store key. build_comparisons derives is_cheapest from the
        # same ordering, and that shared ordering is the only reason the
        # sentence and the table name the same store.
        cheapest_refs = [refs[0] for refs in groups.values() if refs]
        cheapest = (
            citation_index.get(cheapest_refs[0]) if cheapest_refs else None
        ) or citations[0]

        items = ", ".join(k.rsplit("-", 1)[0].replace("-", " ") for k in groups) or (
            "that item"
        )

        system = PRICE_CHECK_SYSTEM
        user = build_price_check_prompt(
            query_item=items,
            options=_placeholder_list(citations),
            on_special=cheapest.on_special,
            cheapest_refs=cheapest_refs or [cheapest.ref],
        )
    else:
        return {}

    # Read before the try, not inside it: a name bound only on the happy
    # path is unbound on every except branch that needs it.
    _usage_before = model.last_usage

    try:
        result = model.structured(
            system=system,
            user=user,
            schema=ProseResult,
            tier=ModelTier.FAST,
            max_tokens=400,
            task="generate_prose",
        )
        assert_no_literal_money(result.text)

        unknown = referenced_placeholders(result.text) - (
            set(citation_index) | set(figures)
        )
        if unknown:
            raise ValueError(f"prose referenced unknown placeholders: {sorted(unknown)}")

        # Verified against the retrieved records, not against what the model
        # claims (Req 5.4's rule, applied to the price claim). The prompt names
        # the computed winner; citing any other option would put a dearer store
        # in the sentence while the table beside it flags a different one as
        # cheapest. Degrading to the structured payload is the honest failure.
        if intent == Intent.PRICE_CHECK and cheapest_refs:
            cited = referenced_placeholders(result.text) & set(citation_index)
            misattributed = cited - set(cheapest_refs)
            if misattributed:
                raise ValueError(
                    "prose cited a non-cheapest option: "
                    f"{sorted(misattributed)}, computed cheapest "
                    f"{sorted(cheapest_refs)}"
                )

        rendered = render(result.text, citation_index, figures)

    except GuardrailBlocked:
        raise
    except (ModelError, ValueError, KeyError) as exc:
        # Degrade silently to the structured payload. The comparison table or
        # plan is the substance; the sentence above it is not.
        return {"prose_error": str(exc), "usage": usage_from(model, _usage_before)}

    seq = _next_seq(state)
    sentences = [s for s in SENTENCE_END.split(rendered.strip()) if s]
    events = [
        TokenEvent(seq=seq + i, text=s if i == 0 else f" {s}")
        for i, s in enumerate(sentences)
    ]

    return {"prose": rendered, "events": events, "usage": usage_from(model, _usage_before)}
