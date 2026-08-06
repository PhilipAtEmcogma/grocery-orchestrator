"""
Guardrail input tagging.

THE THING THAT IS EASY TO GET WRONG: the PROMPT_ATTACK filter does nothing
unless user input is tagged. You can enable it, see it green in the console,
and have it never fire once. AWS is explicit: without input tags, prompt
attacks are not filtered.

The reason tagging is needed at all is that a system prompt and a prompt
injection look alike. "You are a grocery assistant" and "You are now a
chemistry expert" are the same shape. Tagging tells the guardrail which
region of the prompt is untrusted, so the filter applies there and does not
false-positive on our own instructions.

TAG SUFFIXES ARE PER REQUEST. A fixed tag is guessable: a user who learns the
tag can close it early and smuggle text into the trusted region. A fresh
random suffix per request makes that a guess against 2^64.

WHAT COUNTS AS UNTRUSTED: not just the user's message. The product table is
built from scraped retailer content, which is third-party data we do not
control. AWS guidance is to tag dynamically generated prompts that incorporate
external data, because indirect injection through retrieved content is a real
vector. A product name is a place someone could put an instruction.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

# Bedrock recognises this prefix. The suffix is ours and must vary per request.
TAG_PREFIX = "amazon-bedrock-guardrails-guardContent"


@dataclass(frozen=True, slots=True)
class InputTags:
    """A matched open/close pair for one request."""

    open: str
    close: str
    suffix: str

    def wrap(self, text: str) -> str:
        """
        Enclose untrusted text.

        Any occurrence of our own tag inside the text is stripped first, so a
        user who guesses the format still cannot close the region early.
        """
        cleaned = text.replace(self.open, "").replace(self.close, "")
        return f"{self.open}\n{cleaned}\n{self.close}"


def new_tags() -> InputTags:
    """Fresh tags for one request. Never reuse across requests."""
    suffix = secrets.token_hex(8)
    return InputTags(
        open=f"<{TAG_PREFIX}_{suffix}>",
        close=f"</{TAG_PREFIX}_{suffix}>",
        suffix=suffix,
    )


def guard_content_block(text: str) -> dict:
    """
    Converse API equivalent of input tags.

    The Converse API marks untrusted regions with guardContent blocks rather
    than inline tags. The `guard_content` qualifier is what subjects the block
    to the guardrail's input filters.

    NOTE: unverified against the live API — no account yet. If the guardrail
    reports zero prompt-attack evaluations on a known-malicious input, this
    block shape is the first thing to check. Task 8.9 acceptance requires
    running the red-team cases in evals/cases/guardrail.json against a real
    endpoint and confirming they are blocked.
    """
    return {
        "guardContent": {
            "text": {
                "text": text,
                "qualifiers": ["guard_content"],
            }
        }
    }
