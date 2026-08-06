## What changed

<!-- One or two sentences. -->

## Checks

- [ ] `ruff check .` clean
- [ ] `pytest -q` passing
- [ ] `python validate.py` passing (if the contract or schemas changed)

## If prompts changed

Prompt edits are unmeasured until the eval suite has run. Record the numbers:

| | before | after |
|---|---|---|
| intent accuracy | | |
| meal plan invariants | | |

<!--
  python evals/run_intent.py
  python evals/run_meal_plan.py

  A change that lowers a score is a regression regardless of how it reads.
  Do NOT lower a CI floor to make a build pass.
-->

## If the contract changed

- [ ] Additive only (existing clients unaffected), OR
- [ ] Breaking — `CONTRACT_VERSION` bumped and the frontend team notified

## If a model was enabled

- [ ] Scored against both eval suites, results above

## If infrastructure changed

- [ ] IAM scoped to named resources, no wildcards
- [ ] No secrets in source or environment variables
- [ ] Resource config exported to `infra/manual/` and committed
