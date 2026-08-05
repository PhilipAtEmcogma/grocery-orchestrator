"""
System/user prompt text and structured-output schemas for each model call.

  intent.py      Prompt + IntentResult schema for classify_intent.
  meal_plan.py    Prompt + PlanDraft schema for generate_plan/repair_plan.

Each PlanDraft/IntentResult schema is a pydantic model passed to
ModelClient.structured() (see src/models/base.py) so the model's reply is
parsed and validated rather than trusted as free text. Notably, PlanDraft
has no price field anywhere in it — see meal_plan.py for why that is the
load-bearing design decision in this whole package.
"""
