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

from pydantic import BaseModel

from src.models.base import ModelClient, ModelError, ModelTier
from src.prompts.intent import IntentResult
from src.schemas.contract import Intent

_MEAL_WORDS = ("meal", "plan", "dinner", "feed", "recipe", "cook", "week of")
_PRICE_WORDS = ("cheap", "price", "cost", "how much", "compare", "dearest")
_GREETING = ("hello", "hi ", "hey", "thanks", "who are you", "what can you do")

_ITEM_STOPWORDS = {
    "what", "whats", "is", "the", "a", "an", "of", "for", "me", "my", "near",
    "nearby", "cheapest", "cheap", "price", "cost", "how", "much", "buy", "get",
    "find", "please", "block", "some", "any", "s", "at", "in", "around", "want",
    "need", "today", "this", "week",
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
    ) -> None:
        self.force_error = force_error
        self.overrides = overrides or {}
        self.calls: list[tuple[ModelTier, str]] = []
        self._usage: dict = {}

    # ------------------------------------------------------------ interface

    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[BaseModel],
        tier: ModelTier,
        max_tokens: int = 1024,
    ):
        self.calls.append((tier, schema.__name__))
        if self.force_error:
            raise ModelError("scripted failure")

        self._usage = {
            "model_ids": [f"scripted-{tier.value}"],
            "input_tokens": len(system.split()) + len(user.split()),
            "output_tokens": 40,
            "latency_ms": 1,
        }

        if schema is IntentResult:
            return self._classify(user)

        raise ModelError(f"ScriptedModelClient has no script for {schema.__name__}")

    def text(
        self,
        *,
        system: str,
        user: str,
        tier: ModelTier,
        max_tokens: int = 1024,
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
            query_item=item,
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
