"""
Intent classification node.

Replaces the keyword stub. Three responsibilities:
  1. Classify the turn (drives routing AND the frontend's UI treatment)
  2. Extract constraints from natural language
  3. Reconcile those against client hints, message wins on conflict

Degradation policy: if the model call fails, fall back to keyword heuristics
with reduced confidence rather than failing the turn. Intent misclassification
is recoverable (wrong UI card) — unlike a price error, which is not. The
fallback is signalled in state so it is visible in logs rather than silent.
"""

from __future__ import annotations

from decimal import Decimal

from src.graph.state import Constraints, GroceryState
from src.models.base import ModelClient, ModelError, ModelTier
from src.prompts.intent import (
    SYSTEM_PROMPT,
    IntentResult,
    build_user_prompt,
)
from src.schemas.contract import Intent, IntentEvent, NoticeEvent, Store

FALLBACK_CONFIDENCE = 0.45

_MEAL_WORDS = ("meal", "plan", "dinner", "feed", "recipe", "cook")
_PRICE_WORDS = ("cheap", "price", "cost", "how much", "compare")


def _next_seq(state: GroceryState) -> int:
    return len(state.get("events", []))


def _fallback(message: str, hints: dict) -> IntentResult:
    """Keyword classification used only when the model call fails."""
    msg = message.lower()
    if any(w in msg for w in _MEAL_WORDS) or hints.get("budget_nzd"):
        intent = Intent.MEAL_PLAN
    elif any(w in msg for w in _PRICE_WORDS):
        intent = Intent.PRICE_CHECK
    else:
        intent = Intent.GENERAL_CHAT

    return IntentResult(
        intent=intent,
        confidence=FALLBACK_CONFIDENCE,
        query_item=message if intent == Intent.PRICE_CHECK else None,
    )


def _reconcile(
    extracted: IntentResult, hints: dict
) -> tuple[Constraints, list[str]]:
    """
    Merge extracted constraints with client hints.

    The contract states the MESSAGE WINS on conflict: a user who types
    "actually make it $50" is overriding the slider they set earlier. Each
    override is reported so the frontend can explain it rather than silently
    showing a number the user did not choose.
    """
    notices: list[str] = []
    constraints: Constraints = {}

    def take(field: str, extracted_value, hint_value, label: str):
        if extracted_value is not None:
            if hint_value is not None and str(hint_value) != str(extracted_value):
                notices.append(
                    f"Using {label} {extracted_value} from your message "
                    f"rather than {hint_value}."
                )
            return extracted_value
        return hint_value

    household = take(
        "household_size", extracted.household_size,
        hints.get("household_size"), "household size",
    )
    budget = take(
        "budget_nzd", extracted.budget_nzd,
        Decimal(str(hints["budget_nzd"])) if hints.get("budget_nzd") is not None else None,
        "budget of $",
    )
    days = take("days", extracted.days, hints.get("days"), "duration of")

    constraints["household_size"] = household if household is not None else 1
    constraints["days"] = days if days is not None else 1
    if budget is not None:
        constraints["budget_nzd"] = budget

    # Exclusions are additive: a stated exclusion never removes a hinted one,
    # because dropping a dietary restriction is the dangerous direction.
    # hints arrives as an untyped dict from the wire, so values are coerced
    # explicitly rather than trusted.
    hinted_exclusions = [str(x) for x in (hints.get("dietary_exclusions") or [])]
    exclusions: list[str] = sorted(
        {*(extracted.dietary_exclusions or []), *hinted_exclusions}
    )
    constraints["dietary_exclusions"] = exclusions

    hinted_stores = [Store(str(s)) for s in (hints.get("preferred_stores") or [])]
    stores: list[Store] = extracted.preferred_stores or hinted_stores
    constraints["preferred_stores"] = stores

    return constraints, notices


def classify_intent(state: GroceryState, model: ModelClient) -> dict:
    message = state["message"]
    hints = state.get("hints") or {}
    degraded = False

    try:
        extracted = model.structured(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(message),
            schema=IntentResult,
            tier=ModelTier.FAST,
            max_tokens=512,
        )
    except (ModelError, ValueError):
        extracted = _fallback(message, hints)
        degraded = True

    constraints, notices = _reconcile(extracted, hints)

    # query_item drives retrieval. Fall back to the raw message so the
    # repository's own normaliser gets a chance rather than retrieval being
    # skipped entirely.
    constraints["query_item"] = extracted.query_item or message

    seq = _next_seq(state)
    events: list[object] = [
        IntentEvent(
            seq=seq,
            intent=extracted.intent,
            confidence=extracted.confidence,
        )
    ]
    for i, note in enumerate(notices, start=1):
        events.append(NoticeEvent(seq=seq + i, message=note))

    return {
        "intent": extracted.intent,
        "intent_confidence": extracted.confidence,
        "constraints": constraints,
        "intent_degraded": degraded,
        "events": events,
    }
