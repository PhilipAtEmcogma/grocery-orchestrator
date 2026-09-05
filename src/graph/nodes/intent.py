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

from src.graph.dietary import map_exclusions
from src.graph.regions import strip_region
from src.graph.state import Constraints, GroceryState, usage_from
from src.models.base import GuardrailBlocked, ModelClient, ModelError, ModelTier
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
        query_items=[message] if intent == Intent.PRICE_CHECK else [],
    )


def _reconcile(extracted: IntentResult, hints: dict) -> tuple[Constraints, list[str]]:
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
                    f"Using {label} {extracted_value} from your message rather than {hint_value}."
                )
            return extracted_value
        return hint_value

    household = take(
        "household_size",
        extracted.household_size,
        hints.get("household_size"),
        "household size",
    )
    budget = take(
        "budget_nzd",
        extracted.budget_nzd,
        Decimal(str(hints["budget_nzd"])) if hints.get("budget_nzd") is not None else None,
        "budget of $",
    )
    days = take("days", extracted.days, hints.get("days"), "duration of")

    # Absent means ABSENT. These used to be silently defaulted to 1, which
    # contradicts Req 6.3 -- "reject inference of unstated constraints" -- and,
    # worse, destroyed the only evidence that the user had not said. A plan for
    # one person over one day is a real answer to a question nobody asked, and
    # it is indistinguishable downstream from a plan the user actually
    # requested.
    #
    # Read sites that legitimately do not care keep their own `.get(..., 1)`:
    # a price check needs no household size. The meal-plan path instead routes
    # to `emit_clarification`, which is the whole point of knowing.
    if household is not None:
        constraints["household_size"] = household
    if days is not None:
        constraints["days"] = days
    if budget is not None:
        constraints["budget_nzd"] = budget

    # Exclusions are additive: a stated exclusion never removes a hinted one,
    # because dropping a dietary restriction is the dangerous direction.
    # hints arrives as an untyped dict from the wire, so values are coerced
    # explicitly rather than trusted.
    hinted_exclusions = [str(x) for x in (hints.get("dietary_exclusions") or [])]
    exclusions: list[str] = sorted({*(extracted.dietary_exclusions or []), *hinted_exclusions})
    constraints["dietary_exclusions"] = exclusions

    hinted_stores = [Store(str(s)) for s in (hints.get("preferred_stores") or [])]
    stores: list[Store] = extracted.preferred_stores or hinted_stores
    constraints["preferred_stores"] = stores

    return constraints, notices


def classify_intent(state: GroceryState, model: ModelClient) -> dict:
    message = state["message"]
    hints = state.get("hints") or {}
    degraded = False

    # Read before the try, not inside it: a name bound only on the happy
    # path is unbound on every except branch that needs it.
    _usage_before = model.last_usage

    try:
        extracted = model.structured(
            system=SYSTEM_PROMPT,
            # The region is resolved separately, from the ORIGINAL message, and
            # removed here so it cannot end up inside the item name. Without
            # this "cheapest butter near Albany" extracts "butter albany",
            # which resolves to nothing and returns no_data for a stocked
            # product.
            user=build_user_prompt(strip_region(message)),
            schema=IntentResult,
            tier=ModelTier.FAST,
            max_tokens=512,
            # Explicit rather than relying on the parameter default: this is
            # both the registry's routing key and the label the trace and the
            # per-model latency metric are grouped by.
            task="classify_intent",
        )
    except GuardrailBlocked:
        raise
    except (ModelError, ValueError):
        extracted = _fallback(message, hints)
        degraded = True

    constraints, notices = _reconcile(extracted, hints)

    # query_items drives retrieval. Fall back to the raw message so the
    # repository's own normaliser gets a chance rather than retrieval being
    # skipped entirely.
    constraints["query_items"] = extracted.query_items or [message]

    # Terms we cannot map to a category — "gluten-free", "no nuts". Recorded
    # in state now so a meal_plan turn refuses BEFORE doing retrieval and
    # generation work for a request we cannot safely fulfil (Req 5.1,
    # Invariant 3). See src/graph/dietary.py for the safety reasoning.
    # `.get()` with a default rather than a subscript: Constraints is total=False,
    # and pyright's flow analysis does not see the assignment three lines above.
    _, unsupported = map_exclusions(constraints.get("dietary_exclusions", []))

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
        "unsupported_exclusions": unsupported,
        "events": events,
        "usage": usage_from(model, _usage_before),
    }
