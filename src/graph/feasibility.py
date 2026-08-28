"""
Is this request possible at all?

The single reviewable source of truth for that question, in the same spirit as
`dietary.py`: one place, one rule, no node deciding for itself.

WHY THIS EXISTS
---------------
Retrieval pre-filters candidates so that everything offered to the model is
affordable. That is what keeps a price-blind model inside a budget — but it
also makes affordability true BY CONSTRUCTION, so affordability stopped being
evidence that the request made sense. "Feed 5 people for 7 days on $15" began
returning a tidy plan assembled from $15 of food, which is worse than a
refusal: it looks like an answer.

WHAT IS AND IS NOT A JUDGEMENT HERE
-----------------------------------
The threshold has two parts and only one of them is an opinion:

  * grams per person per day  -- a JUDGEMENT, and the only one in the planning
    path. It lives in config/feasibility.json so that it can be reviewed and
    changed by someone who knows about food rather than about Python, and so
    that changing it is a config diff rather than a code change.
  * price per gram            -- a FACT, read from the catalogue at call time.
    It moves when the prices move, with nobody having to remember.

Keeping them apart matters: it means "this budget is impossible" is mostly
derived from data, and the part that is not is written down where it can be
argued with.

OPEN REVIEW
-----------
The grams figure has NOT been reviewed by anyone with domain knowledge of
food budgeting. It was calibrated against expectations the project already
held and bounded by tests, which makes it defensible, not justified.
`docs/OPEN-REVIEW-min-grams-per-person-day.md` states the question for a
reviewer who will not read this file. If you are changing the value, read
that first — the tests will tell you which existing expectation your number
contradicts, and that disagreement is usually the real decision.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "feasibility.json"


def _load() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def min_grams_per_person_day() -> int:
    """
    The configured floor, read at call time rather than at import.

    Read fresh so a deployment can change the policy without a code change,
    and so a test can point CONFIG_PATH somewhere else without fighting an
    already-imported constant.
    """
    value = _load()["min_grams_per_person_day"]
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"min_grams_per_person_day must be a positive integer, got {value!r}")
    return value


def minimum_spend(records: list, household: int, days: int) -> Decimal | None:
    """
    The least this request could possibly cost.

    Assumes the shopper buys nothing but the cheapest food by weight in the
    catalogue and nothing else — so no plan can beat it, and a budget below it
    is impossible rather than merely ambitious. That is what makes "I can't do
    this for $15" a fact rather than an opinion about groceries.

    Returns None when no record carries a weight, since then there is nothing
    to reason from. The caller treats that as "cannot tell", never as
    "impossible": refusing a turn because the catalogue lacked a field would
    be a data problem presented to the user as their problem.
    """
    per_gram = [rec.price_nzd / rec.pack_grams for rec in records if getattr(rec, "pack_grams", 0)]
    if not per_gram:
        return None
    grams = household * days * min_grams_per_person_day()
    return min(per_gram) * grams
