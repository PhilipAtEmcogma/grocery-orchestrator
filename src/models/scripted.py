"""
Deterministic ModelClient for tests and local development.

Two jobs:
  1. Let the graph run with no AWS account.
  2. Let tests force specific model behaviour — including failure — which is
     genuinely hard to do against a real model on demand. Testing the repair
     loop needs a first response that is over budget; scripting that is the
     only reliable way to get it.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import cast

from src.models.base import ModelClient, ModelError, ModelTier, T
from src.prompts.intent import IntentResult
from src.prompts.meal_plan import DraftIngredient, DraftMeal, PlanDraft
from src.prompts.prose import ProseResult
from src.schemas.contract import Intent

_MEAL_WORDS = ("meal", "plan", "dinner", "feed", "recipe", "cook", "week of")
_PRICE_WORDS = ("cheap", "price", "cost", "how much", "compare", "dearest")
_GREETING = ("hello", "hi ", "hey", "thanks", "who are you", "what can you do")

_ITEM_STOPWORDS = {
    "what", "whats", "is", "are", "the", "a", "an", "of", "for", "me", "my",
    "near", "nearby", "cheapest", "cheap", "price", "prices", "pricing", "cost",
    "costs", "compare", "comparison", "how", "much", "many", "buy", "get",
    "find", "please", "block", "some", "any", "s", "at", "in", "around", "want",
    "need", "today", "this", "week", "dearest", "best",
}

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


class ScriptedModelClient(ModelClient):
    """
    Rule-based stand-in. Deliberately simpler than the real model.

    `force_error` and `overrides` exist so tests can drive specific branches
    without needing the network.
    """

    def __init__(
        self,
        *,
        force_error: bool = False,
        overrides: dict | None = None,
        plan_packs: Decimal | None = None,
        hallucinate_ref: str | None = None,
        prose_writes_money: bool = False,
        prose_bad_placeholder: bool = False,
    ) -> None:
        self.force_error = force_error
        self.overrides = overrides or {}
        # plan_packs inflates portion sizes so the first plan lands over
        # budget on purpose. That is how the repair loop gets tested without
        # a live model, which cannot be made to fail on demand.
        self.plan_packs = plan_packs
        self.hallucinate_ref = hallucinate_ref
        # Knobs for the failure paths. A real model cannot be made to write a
        # literal price on demand, so the rejection cannot otherwise be tested.
        self.prose_writes_money = prose_writes_money
        self.prose_bad_placeholder = prose_bad_placeholder
        self.calls: list[tuple[ModelTier, str]] = []
        self._usage: dict = {}

    # ------------------------------------------------------------ interface

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
        self.calls.append((tier, schema.__name__))
        if self.force_error:
            raise ModelError("scripted failure")

        self._usage = {
            "model_ids": [f"scripted-{tier.value}"],
            "input_tokens": len(system.split()) + len(user.split()),
            "output_tokens": 40,
            "latency_ms": 1,
        }

        # The `is` checks establish at runtime that T is the concrete schema,
        # but a TypeVar cannot be narrowed by an identity comparison, so the
        # cast states the invariant the checker cannot infer. This is dispatch
        # on a type object, which Python's type system does not model.
        if schema is IntentResult:
            return cast(T, self._classify(user))

        if schema is PlanDraft:
            return cast(T, self._plan(user, tier))

        if schema is ProseResult:
            return cast(T, self._prose(user))

        raise ModelError(f"ScriptedModelClient has no script for {schema.__name__}")

    def text(
        self,
        *,
        system: str,
        user: str,
        tier: ModelTier,
        max_tokens: int = 1024,
        task: str = "generate_prose",
    ) -> str:
        self.calls.append((tier, "text"))
        if self.force_error:
            raise ModelError("scripted failure")
        self._usage = {"model_ids": [f"scripted-{tier.value}"], "latency_ms": 1}
        return "Scripted response."

    @property
    def last_usage(self) -> dict:
        return dict(self._usage)

    # ------------------------------------------------------------ scripting

    def _classify(self, user: str) -> IntentResult:
        # Strip the prompt delimiters to recover the raw message.
        msg = re.sub(r"<<<[/A-Z_]*>>>", " ", user).strip().lower()

        budget = self._extract_budget(msg)
        household = self._extract_household(msg)
        days = self._extract_days(msg)
        exclusions = self._extract_exclusions(msg)

        if any(w in msg for w in _MEAL_WORDS) or budget is not None:
            intent, confidence = Intent.MEAL_PLAN, 0.94
            item = None
        elif any(w in msg for w in _PRICE_WORDS):
            intent, confidence = Intent.PRICE_CHECK, 0.96
            item = self._extract_item(msg)
            items = self._extract_items(msg)
            if len(items) > 1:
                return IntentResult(
                    intent=intent, confidence=confidence, query_items=items,
                    household_size=household, budget_nzd=budget, days=days,
                    dietary_exclusions=exclusions,
                )
        elif any(w in msg for w in _GREETING):
            intent, confidence = Intent.GENERAL_CHAT, 0.88
            item = None
        else:
            # A bare product name ("butter?") reads as a price check, but only
            # if it is SHORT. Real product queries are one to three words;
            # anything longer with no price or meal keyword is not a grocery
            # request, and treating it as one is how injection attempts get
            # smuggled into the retrieval path.
            item = self._extract_item(msg)
            if item and len(item.split()) <= 3:
                intent, confidence = Intent.PRICE_CHECK, 0.70
            else:
                intent, confidence = Intent.OUT_OF_SCOPE, 0.65
                item = None

        result = IntentResult(
            intent=intent,
            confidence=confidence,
            query_items=[item] if item else [],
            household_size=household,
            budget_nzd=budget,
            days=days,
            dietary_exclusions=exclusions,
        )
        if self.overrides:
            result = result.model_copy(update=self.overrides)
        return result

    @staticmethod
    def _extract_budget(msg: str) -> Decimal | None:
        m = re.search(r"\$\s*(\d+(?:\.\d{1,2})?)", msg)
        if m:
            return Decimal(m.group(1))
        m = re.search(r"(\d+(?:\.\d{1,2})?)\s*(?:dollars|bucks|nzd)", msg)
        return Decimal(m.group(1)) if m else None

    @staticmethod
    def _extract_household(msg: str) -> int | None:
        m = re.search(r"(?:flat of|family of|household of|for)\s+(\d+)", msg)
        if m:
            return int(m.group(1))
        m = re.search(r"(?:flat of|family of|for)\s+([a-z]+)", msg)
        if m and m.group(1) in _WORD_NUMBERS:
            return _WORD_NUMBERS[m.group(1)]
        m = re.search(r"(\d+)\s*(?:people|persons|of us)", msg)
        return int(m.group(1)) if m else None

    @staticmethod
    def _extract_days(msg: str) -> int | None:
        m = re.search(r"(\d+)\s*(?:days|nights|dinners)", msg)
        if m:
            return int(m.group(1))
        if "this week" in msg or "a week" in msg or "for the week" in msg:
            return 7
        return None

    @staticmethod
    def _extract_exclusions(msg: str) -> list[str]:
        out: list[str] = []
        if re.search(r"no (?:seafood|fish)|without (?:seafood|fish)", msg):
            out.append("seafood")
        if "vegetarian" in msg or "no meat" in msg:
            out.append("vegetarian")
        if "vegan" in msg:
            out.append("vegan")
        if "dairy free" in msg or "dairy-free" in msg or "no dairy" in msg:
            out.append("dairy-free")
        if "gluten free" in msg or "gluten-free" in msg:
            out.append("gluten-free")
        return out

    @staticmethod
    def _extract_item(msg: str) -> str | None:
        cleaned = re.sub(r"[^a-z0-9\s]", " ", msg)
        words = [
            w for w in cleaned.split() if len(w) > 1 and w not in _ITEM_STOPWORDS
        ]
        return " ".join(words) if words else None


    def _plan(self, user: str, tier: ModelTier) -> PlanDraft:
        """
        Build a plan from the refs in the AVAILABLE PRODUCTS table.

        Scales meal count to the requested days and rotates the ingredient
        window, so the eval harness sees realistic variety and reuse numbers
        rather than one meal repeated. Repair passes (FAST tier) shrink
        portions, mimicking a real model responding to "cut $X" feedback.
        """
        refs = re.findall(r"^(c\d+) \|", user, flags=re.MULTILINE)
        if not refs:
            refs = ["c1"]
        if self.hallucinate_ref:
            refs = [self.hallucinate_ref, *refs]

        if self.plan_packs is not None:
            packs = self.plan_packs
        elif tier is ModelTier.FAST:
            packs = Decimal("0.15")
        else:
            packs = Decimal("0.3")

        days_match = re.search(r"Days to cover: (\d+)", user)
        days = int(days_match.group(1)) if days_match else 1
        serves_match = re.search(r"Household size: (\d+)", user)
        serves = int(serves_match.group(1)) if serves_match else 2

        meals = []
        per_meal = 4
        for day in range(min(days, 7)):
            # Rotate the window so meals differ, and overlap it so packs are
            # reused across meals rather than one product per meal.
            start = (day * 2) % max(len(refs) - per_meal, 1)
            chosen = refs[start : start + per_meal] or refs[:per_meal]
            meals.append(
                DraftMeal(
                    name=f"Scripted Dinner {day + 1}",
                    serves=serves,
                    ingredients=[
                        DraftIngredient(
                            citation_ref=ref,
                            packs=packs,
                            qty_display="portion",
                            item=f"item {ref}",
                        )
                        for ref in chosen
                    ],
                )
            )

        return PlanDraft(meals=meals, reasoning="Scripted selection.")


    def _prose(self, user: str) -> ProseResult:
        """Placeholder-only prose, mirroring what a well-behaved model returns."""
        if self.prose_writes_money:
            return ProseResult(
                text="Pak'nSave is cheapest at $2.97 for 500g this week."
            )
        if self.prose_bad_placeholder:
            return ProseResult(text="The cheapest option is [[c99]] this week.")

        refs = re.findall(r"\[\[(c\d+)\]\]", user)
        if "[[total]]" in user:
            return ProseResult(
                text=(
                    "This plan comes to [[total]] against your budget of "
                    "[[budget]]. Reusing one pack across several meals kept "
                    "the cost down."
                )
            )
        if refs:
            return ProseResult(
                text=(
                    f"The cheapest option is [[{refs[0]}]]. "
                    f"That is the best price across the stores near you."
                )
            )
        return ProseResult(text="Here is what I found.")


    @staticmethod
    def _extract_items(msg: str) -> list[str]:
        """
        Split a multi-item request on commas and 'and'.

        Crude on purpose — a real model handles this far better. The point is
        that the eval measures the difference rather than the stub hiding it.
        """
        body = re.sub(r"[^a-z0-9\s,]", " ", msg)
        body = re.sub(r"\band\b", ",", body)
        parts = [p.strip() for p in body.split(",")]

        out: list[str] = []
        for part in parts:
            words = [
                w for w in part.split() if len(w) > 1 and w not in _ITEM_STOPWORDS
            ]
            if words:
                out.append(" ".join(words))
        return out[:5]
