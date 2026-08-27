"""
Prose generation.

The brief asks the assistant to explain its reasoning — "I chose Pak'nSave for
the mince because it's $2.50 cheaper this week". That sentence contains a
price, which is exactly what the model must never produce.

THE MECHANISM: the model writes placeholders, never numbers. `[[c3]]` becomes
"$11.99 at Pak'nSave Sylvia Park" during rendering, from the retrieved record.
`[[total]]` and `[[budget]]` become figures computed in code.

Then `assert_no_literal_money` rejects any output containing a money-shaped
string. So a hallucinated price is not merely instructed against — it fails
validation and the turn degrades to the structured payload without prose.

This is the price-free draft schema applied to free text. The schema trick
does not work here, because prose is one string field; the equivalent
structural guarantee is that a price-shaped substring is invalid output.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

# Distinct enough that a model will not produce it by accident, and unlike
# single braces it does not collide with JSON the model might emit.
PLACEHOLDER = re.compile(r"\[\[(c\d+|total|budget|savings)\]\]")

# Money-shaped strings. Deliberately narrow: "3 meals", "500g" and "2 people"
# are legitimate and must pass. What must not appear is a currency figure.
LITERAL_MONEY = re.compile(
    r"""
    \$\s*\d
    | \d+\.\d{2}\b
    | \b\d+\s*(?:dollars?|bucks|cents?)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


class ProseResult(BaseModel):
    """
    Explanatory text. Note there is no price field, and `text` is validated
    for money-shaped substrings after generation.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        max_length=600,
        description=(
            "Two or three sentences. Reference prices ONLY as placeholders "
            "like [[c1]]. Never write a number with a currency symbol, a "
            "decimal amount, or the word dollars."
        ),
    )


_SHARED_RULES = """\
Absolute rules:
- NEVER write a price, cost, total or saving as a number. You do not have \
accurate figures and anything you write would be wrong.
- Reference a price ONLY by its placeholder, exactly as shown: [[c1]], [[c2]].
- A placeholder expands to the price and store when the reply is assembled, \
so write "cheapest at [[c1]]" and not "cheapest at [[c1]] dollars".
- Use only placeholders that appear in the list given to you.
- Two or three sentences. Plain, warm, direct. No bullet points, no headings.
- New Zealand English. The reader is a budget-conscious shopper, not a \
nutritionist.
"""

PRICE_CHECK_SYSTEM = f"""\
You explain the result of a grocery price comparison to the shopper who asked.

Say which store is cheapest and give the one fact that makes the choice \
useful — that it is on special this week, or that it saves [[savings]] against \
the dearest option. Do not list every store; the comparison table already \
does that.

The winner is not yours to work out. The user block lists the computed \
cheapest placeholder after CHEAPEST:. Cite that placeholder and NO other \
[[c…]] placeholder — not even to name what it beats. Use [[savings]] for the \
comparison instead. You are not shown prices and cannot rank the options \
yourself.

{_SHARED_RULES}"""

MEAL_PLAN_SYSTEM = f"""\
You explain a meal plan to the shopper who asked for it.

Say how the plan fits the budget, name one or two choices that made it work \
(a protein reused across meals, an item on special), and mention if the \
shopping is split across stores. Do not restate the meals; the plan already \
lists them.

If dietary exclusions were applied, confirm them plainly so the shopper can \
see they were respected.

{_SHARED_RULES}"""


def build_price_check_prompt(
    *, query_item: str, options: str, on_special: bool, cheapest_refs: list[str]
) -> str:
    """
    `cheapest_refs` is not decoration. The placeholder list deliberately
    carries no prices -- that is the whole mechanism above -- so a model asked
    to "say which store is cheapest" without being told the answer has to
    guess, and on a tie or a near-tie it guesses a different store than
    `build_comparisons` computed. The sentence then contradicts the table
    beside it, which is Req 4's failure mode dressed as fluency.

    Code determines the winner from retrieved prices; the model only phrases
    it. `generate_prose` rejects output citing anything else.

    CHEAPEST carries the ref and NOTHING ELSE. The rule that explains what to
    do with it lives in PRICE_CHECK_SYSTEM, because this string is wrapped in
    guardrail input tags before it reaches Bedrock (src/models/guardrail.py).
    Imperative sentences inside the tagged region are what a prompt attack
    looks like, and the PROMPT_ATTACK filter cannot tell the difference -- the
    first version of this function put "you must not work out a winner
    yourself" here and every price_check turn came back GUARDRAIL_BLOCKED.
    Instructions go in the system prompt; the tagged block carries data.
    """
    special = (
        "\nThe cheapest option is on special this week." if on_special else ""
    )
    winners = ", ".join(f"[[{ref}]]" for ref in cheapest_refs)
    return (
        f"The shopper asked about: {query_item}\n\n"
        f"AVAILABLE PLACEHOLDERS\n{options}{special}\n\n"
        f"CHEAPEST: {winners}\n\n"
        f"Explain the result."
    )


def build_meal_plan_prompt(
    *,
    days: int,
    household_size: int,
    exclusions: list[str],
    placeholders: str,
    stores: list[str],
    reused: list[str],
) -> str:
    lines = [
        f"Plan covers {days} day(s) for {household_size} person/people.",
        "Total is [[total]] against a budget of [[budget]].",
    ]
    if exclusions:
        lines.append(f"Exclusions applied: {', '.join(exclusions)}")
    if len(stores) > 1:
        lines.append(f"Shopping is split across: {', '.join(stores)}")
    if reused:
        lines.append(f"Reused across meals: {', '.join(reused)}")

    return (
        "\n".join(lines)
        + f"\n\nAVAILABLE PLACEHOLDERS\n{placeholders}\n\n"
        + "Explain the plan."
    )


def assert_no_literal_money(text: str) -> None:
    """
    Reject prose containing a money-shaped string.

    This is the structural guarantee for free text. A model that writes
    "$3.49" fails here rather than shipping an unverifiable figure.
    """
    match = LITERAL_MONEY.search(text)
    if match:
        raise ValueError(
            f"prose contains a literal monetary value: {match.group(0)!r}"
        )


def referenced_placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))
