r"""
DEMO 16 - The AI request pipeline: context, prompt, schema, validation
======================================================================

HOW TO RUN
----------
    python Philip_demo/16_prompt_and_structured_output.py

Windows, without activating the virtualenv:

    .venv\Scripts\python.exe Philip_demo/16_prompt_and_structured_output.py

MODES
-----
    local  (default and only)  every prompt is built for real and printed, and
                               the scripted client answers. No AWS, no
                               credentials, no network. Demo 14 sends these
                               same shapes to Bedrock.

WHAT THIS DEMONSTRATES
----------------------
Every stage between a shopper's sentence and a validated object:

    input -> retrieval -> context rendering -> prompt construction
          -> structured schema -> model -> validation -> repair prompt
          -> deterministic assembly -> prose protocol

  1. Intent extraction, and the null expectations that matter as much
  2. The grounding context, rendered as a table with NO PRICES in it
  3. The plan prompt, delimiter-fenced, with the constraints restated
  4. PlanDraft: a schema with nowhere to put a price
  5. `reasoning`: advertised, not enforced, and why that changed
  6. Two repair prompts, because there are two kinds of failure
  7. The prose protocol - placeholders, and silent degradation

THE SHAPE OF THE WHOLE THING
----------------------------
The model is given a table of products with refs and pack sizes and NO
prices. It returns refs and pack multipliers. Python multiplies. So the
model's job is selection and phrasing, and arithmetic is not a thing it can
get wrong - it is a thing it is not asked.
"""

from __future__ import annotations

import json
from decimal import Decimal

from _demo_support import (
    LOCAL,
    ModeUnavailable,
    heading,
    mode_banner,
    note,
    resolve_mode,
    section,
    step,
)

from src.models.base import ModelTier
from src.models.scripted import ScriptedModelClient
from src.prompts import intent as intent_prompts
from src.prompts import meal_plan as plan_prompts
from src.prompts import prose as prose_prompts
from src.retrieval.memory import InMemoryPriceRepository
from src.schemas.contract import LITERAL_MONEY, Citation, SourceRef, Store

try:
    mode = resolve_mode(supports=(LOCAL,))
except ModeUnavailable as exc:
    raise SystemExit(str(exc)) from exc

heading("DEMO 16 - The AI request pipeline")
mode_banner(
    mode,
    requires="nothing - the prompt modules and fixtures/products.json",
    mocked="the model plane (ScriptedModelClient). Every prompt below is real.",
)

repo = InMemoryPriceRepository()
model = ScriptedModelClient()

# ------------------------------------------------------------------ intent
section("1. Stage one: what did the shopper actually ask for")
message = "feed 3 people for 5 days on $80, vegetarian"
step(1, f"input: {message!r}")
step(2, "build_user_prompt - the message, fenced")
user_prompt = intent_prompts.build_user_prompt(message)
print()
for line in user_prompt.splitlines():
    print(f"      {line}")
step(3, "the system prompt names the schema and the rules")
print(f"\n      {len(intent_prompts.SYSTEM_PROMPT)} characters, first lines:\n")
for line in intent_prompts.SYSTEM_PROMPT.splitlines()[:8]:
    print(f"      {line}")
print("      ...")

step(4, "the model answers into IntentResult")
result = model.structured(
    system=intent_prompts.SYSTEM_PROMPT,
    user=user_prompt,
    schema=intent_prompts.IntentResult,
    tier=ModelTier.FAST,
    task="classify_intent",
)
print()
for line in result.model_dump_json(indent=2).splitlines():
    print(f"      {line}")
note("")
note(f"The delimiters are {intent_prompts.DELIM} / {intent_prompts.DELIM_END}, and")
note("build_user_prompt STRIPS them from the message first - so a shopper who")
note("types the closing delimiter cannot end the fenced region early. Demo 3")
note("shows an injection attempt going through this path.")
note("")
note(f"MAX_EXTRACTED_ITEMS is {intent_prompts.MAX_EXTRACTED_ITEMS}: a cap on how many things")
note("one turn will look up, so a message listing fifty products cannot turn")
note("into fifty retrieval calls. Demo 1 section 4 shows the user being told")
note("what was NOT checked.")

print("\n  What a NULL answer looks like, and why it matters:\n")
vague = model.structured(
    system=intent_prompts.SYSTEM_PROMPT,
    user=intent_prompts.build_user_prompt("feed my flat of 3 this week"),
    schema=intent_prompts.IntentResult,
    tier=ModelTier.FAST,
    task="classify_intent",
)
print(
    f"    'feed my flat of 3 this week' -> budget_nzd={vague.budget_nzd!r} "
    f"days={vague.days!r} household_size={vague.household_size!r}"
)
note("budget_nzd is None, not 0 - nothing in the message named a figure.")
note("days is 7 because 'this week' DOES say so, which is the other half of")
note("the same rule: extract what was stated, invent nothing.")
note("")
note("'Must not invent a budget' is as important an")
note("assertion as 'must extract the stated budget' - a hallucinated")
note("constraint silently changes what the user asked for, and the deployed")
note("service answered 'I couldn't build a plan within $0' because of exactly")
note("this. evals/cases/intent.json asserts the nulls.")

# ------------------------------------------------------------- the context
section("2. Stage two: the grounding context, and what is missing from it")
categories = repo.all_categories
records = repo.candidates_for_budget(
    categories=categories,
    exclude_categories=["meat", "seafood"],
    limit_per_category=2,
    budget_nzd=Decimal("80"),
)
citation_list = [
    Citation(
        ref=f"c{i}",
        store=Store(r.store.value),
        store_location=r.store_location,
        product_name=r.display_name,
        price_nzd=r.price_nzd,
        unit=r.unit,
        unit_price_nzd=r.unit_price_nzd,
        on_special=r.on_special,
        valid_date=r.valid_date,
        source=SourceRef(table=repo.table_name, pk=r.store_key, sk=r.product_key),
    )
    for i, r in enumerate(records, start=1)
]
record_index = {c.ref: r for c, r in zip(citation_list, records, strict=True)}
products_table = plan_prompts.render_products(citation_list, record_index)
print(f"  {len(citation_list)} retrieved products, rendered for the model:\n")
for line in products_table.splitlines()[:12]:
    print(f"    {line}")
print("    ...")
money_in_table = LITERAL_MONEY.search(products_table)
print(f"\n  LITERAL_MONEY.search over that table -> {money_in_table!r}")
print("  (the same regex the contract uses to reject a money-shaped string")
print("   anywhere it must not appear)")
note("")
note("ref, product, store, pack size, on special. No price column. The pack")
note("size IS there, because `packs` is a multiplier on it and without it the")
note("model cannot reason about quantity at all.")
note("")
note("This is the grounding boundary. Everything the model knows about")
note("products, it knows from this table, and the table is built from records")
note("retrieval returned - so a product not in the catalogue cannot appear in")
note("a plan, and a price cannot appear anywhere.")

# ---------------------------------------------------------------- the plan
section("3. Stage three: the plan prompt")
plan_prompt = plan_prompts.build_user_prompt(
    message=message,
    household_size=3,
    days=5,
    budget_nzd=Decimal("80"),
    exclusions=["meat", "seafood"],
    products=products_table,
)
tail = plan_prompt.split("CONSTRAINTS", 1)[1]
print("  Everything after the product table:\n")
for line in ("CONSTRAINTS" + tail).splitlines():
    print(f"    {line}")
note("")
note(f"The shopper's own words sit between {plan_prompts.DELIM} and")
note(f"{plan_prompts.DELIM_END}, stripped of those markers first.")

# ------------------------------------------------------------- the schema
section("4. Stage four: a schema with nowhere to put a price")
schema = plan_prompts.PlanDraft.model_json_schema()
print("  PlanDraft, flattened:\n")
print(f"    PlanDraft      {sorted(schema['properties'])}")
draft_defs = schema.get("$defs", {})
for name in ("DraftMeal", "DraftIngredient"):
    if name in draft_defs:
        print(f"    {name:<14} {sorted(draft_defs[name]['properties'])}")
print("\n  DraftIngredient in full:\n")
print(json.dumps(draft_defs.get("DraftIngredient", {}), indent=6)[:900])
note("")
note("citation_ref, packs, qty_display, item. `extra='forbid'` on every model,")
note("so a model that adds a `price` field is rejected rather than having it")
note("quietly dropped. There is no field to put a number in, which is a")
note("property rather than an instruction.")

step(1, "the model answers into PlanDraft")
draft = model.structured(
    system=plan_prompts.SYSTEM_PROMPT,
    user=plan_prompt,
    schema=plan_prompts.PlanDraft,
    tier=ModelTier.QUALITY,
    task="generate_plan",
)
print(f"\n      {len(draft.meals)} meals")
for meal in draft.meals[:3]:
    refs = ", ".join(f"{i.citation_ref}x{i.packs}" for i in meal.ingredients)
    print(f"        {meal.name[:40]:<40} serves {meal.serves}  [{refs}]")
step(2, "Python multiplies pack price by packs, sums, and compares to budget")
note("")
note("That multiplication is demo 2. The point here is that the model never")
note("attempted it: it returned refs and multipliers, which is a selection")
note("problem, and selection is the thing it is good at.")

# --------------------------------------------------------------- reasoning
section("5. `reasoning`: advertised, not enforced")
print(f"  REASONING_MAX_CHARS = {plan_prompts.REASONING_MAX_CHARS}")
long_reasoning = "x" * (plan_prompts.REASONING_MAX_CHARS + 500)
trimmed = plan_prompts.PlanDraft(meals=draft.meals, reasoning=long_reasoning)
print(
    f"  a {len(long_reasoning)}-character reasoning field validates, trimmed to "
    f"{len(trimmed.reasoning)}"
)
field_schema = schema["properties"]["reasoning"]
print(f"  and the tool schema still tells the model: maxLength={field_schema.get('maxLength')}")
note("")
note("Nothing downstream reads `reasoning`. assemble_plan ignores it, no")
note("event carries it, assert_grounded never sees it. As a HARD max_length")
note("it could therefore only do harm - Claude Haiku 4.5 overran it on 11 of")
note("11 first attempts in a live run, and each overrun threw away an")
note("otherwise valid plan and spent a repair call regenerating it.")
note("")
note("Rejecting a good plan over the length of a field we discard is a bad")
note("trade at any cap. The hint still goes to the model, because telling it")
note("to be brief is worth doing; a model that ignores the hint is now")
note("truncated rather than rejected.")

# ----------------------------------------------------------------- repair
section("6. Two repair prompts, because there are two kinds of failure")
cheaper = plan_prompts.render_products(citation_list[:4], record_index)
budget_repair = plan_prompts.build_repair_prompt(
    products=products_table,
    over_by=Decimal("12.40"),
    budget=Decimal("80"),
    household_size=3,
    days=5,
    exclusions=["meat", "seafood"],
    previous_items=["Pams Pasta Spirals 500g", "Pams Beef Mince 1kg"],
    cheaper_options=cheaper,
)
print("  BUDGET repair - everything after the product table:\n")
for line in budget_repair.split("CONSTRAINTS", 1)[1].splitlines()[:24]:
    print(f"    {line}")
print("    ...")

defect_repair = plan_prompts.build_defect_repair_prompt(
    products=products_table,
    budget=Decimal("80"),
    household_size=3,
    days=5,
    exclusions=["meat", "seafood"],
    defects=[
        "ingredient referenced c99, which is not in the table",
        "meal name 'Mince Bake ($8)' contains a monetary value",
    ],
)
print("\n  DEFECT repair - everything after the product table:\n")
for line in defect_repair.split("CONSTRAINTS", 1)[1].splitlines()[:18]:
    print(f"    {line}")
note("")
note("Before the second one existed, a draft that failed its schema or cited")
note("an unknown product was told 'your previous plan came to $0 OVER the $80")
note("budget, produce a plan costing at least $0 less'. That describes none of")
note("them, and asking a model to fix a defect nobody named wastes the attempt.")
note("")
note("BOTH restate EVERY constraint, from one shared _constraints_block. The")
note("first version restated only the budget, so a plan for a user with a")
note("stated allergy was regenerated with no knowledge of the allergy. Unit")
note("tests missed it; end-to-end evaluation caught it. One definition means a")
note("second repair prompt cannot reintroduce that by omission.")
note("")
note("Prices ARE stated in these prompts. A prompt is not user-visible output:")
note("Req 3.7 governs what reaches the shopper, and the model needs the budget")
note("figure to plan against it.")

# ------------------------------------------------------------------ prose
section("7. The prose protocol, and silent degradation")
options = "\n".join(f"[[{c.ref}]] {c.product_name} at {c.store.value}" for c in citation_list[:4])
prose_prompt = prose_prompts.build_price_check_prompt(
    query_item="butter",
    options=options,
    on_special=True,
    cheapest_refs=["c1"],
)
print("  The price-check prose prompt:\n")
for line in prose_prompt.splitlines():
    print(f"    {line}")
note("")
note("Placeholders, never prices. The model writes [[c1]] and code substitutes")
note("the figure afterwards, so the sentence and the table cannot disagree.")
note("")
note("CHEAPEST carries the ref and NOTHING ELSE. The rule about what to do")
note("with it lives in the SYSTEM prompt, because this string is wrapped in")
note("guardrail input tags: an imperative sentence inside the tagged region is")
note("what a prompt attack looks like, and the PROMPT_ATTACK filter cannot")
note("tell the difference. The first version put 'you must not work out a")
note("winner yourself' here and every price_check turn came back")
note("GUARDRAIL_BLOCKED.")

print("\n  What the node does with the model's answer:\n")
for text, label in (
    ("The cheapest is [[c1]], on special this week.", "clean"),
    ("The cheapest is $2.97 at Pak'nSave.", "contains literal money"),
    ("The cheapest is [[c9]].", "cites a placeholder that was not offered"),
):
    try:
        prose_prompts.assert_no_literal_money(text)
        refs = prose_prompts.referenced_placeholders(text)
        offered = {c.ref for c in citation_list[:4]} | {"total", "budget", "savings"}
        unknown = refs - offered
        verdict = f"REJECTED: cites {sorted(unknown)}" if unknown else "accepted"
    except ValueError as exc:
        verdict = f"REJECTED: {exc}"
    print(f"    {label:<38} {verdict}")
note("")
note("A rejected sentence is DROPPED and the turn ships the bare table. Prose")
note("is non-essential, so its failure costs a sentence rather than the")
note("answer - Req 3.7 draws the line exactly there. Which also means a model")
note("that cannot follow this protocol produces a product with no prose in it")
note("and no error to show for it, and that is why evals/run_prose.py exists.")

print("\nDone.")
