# Open review: `min_grams_per_person_day`

**Status:** awaiting review by someone with domain knowledge of food budgeting
or nutrition. Not blocking; the value in place is defensible and bounded by
tests. Raised 2026-08-28.

**Owner of the decision:** whoever is comfortable saying what "not enough food
to be possible" means in New Zealand grocery terms. That is explicitly *not*
the person who wrote it, who set it by inspecting a fixture catalogue.

**Where it lives:** `config/feasibility.json`, consumed by
`src/graph/feasibility.py`, pinned by `tests/test_feasibility.py`.

---

## What you are being asked

Is **600 grams of food per person per day** the right threshold below which the
orchestrator declares a meal-plan request *impossible* and refuses it?

You do not need to read any code to answer. The question is only: below what
daily quantity is a grocery request not worth attempting, as opposed to merely
tight?

---

## Why the number exists at all

The orchestrator pre-filters the products offered to the model so that
everything it can choose from is affordable. That is how a model which never
sees a price is kept inside a budget — it can only pick from a set that fits.

The side effect is that **affordability became true by construction**, and so
stopped being evidence that the request made sense. Asked to feed 5 people for
7 days on $15, the system happily assembled a plan from $15 of food and
presented it as an answer. That is worse than a refusal, because it looks like
success.

`minimum_spend()` is the check that refuses instead. It asks: buying nothing
but the cheapest food by weight in the catalogue, could this budget cover
`household x days x grams-per-person-day`? If not, the turn is refused before
any model call, with `BUDGET_INFEASIBLE`.

## What is judgement and what is fact

Only one half of the calculation is an opinion:

| Part | Kind | Source |
|---|---|---|
| grams per person per day | **judgement** | `config/feasibility.json` — this review |
| cheapest price per gram | fact | read from the catalogue at call time |
| household, days, budget | fact | the user's request |

This split is deliberate. It keeps "this budget is impossible" mostly derived
from data, and puts the part that is not somewhere it can be argued with.

## What the number is not

- **Not a nutrition target.** 600g/day is well below what anyone should eat.
  It is not advice and is never shown to a user.
- **Not a quality bar.** Requests above it can still be poor value; that is the
  planner's problem, not this check's.
- **Not a hard floor on price.** It combines with the catalogue's cheapest
  price per gram, so it moves when prices move, without anyone editing it.

It marks one thing only: the point past which no arrangement of the available
groceries could satisfy the request.

## How 600 was arrived at

Not by nutrition research. By calibrating against decisions the project had
already made, then checking the choice was not fragile.

At the catalogue's cheapest food by weight (**$1.59/kg** as of 2026-08-28) the
value must:

- **refuse** "feed 5 people for 7 days on $15" — eval case `plan-006`, whose
  own note says *"Genuinely impossible. Must refuse honestly rather than
  produce a plan that starves someone."*
- **refuse** "$5 for two people over three days" — the infeasible-budget
  scenarios in `tests/test_plan.py`
- **admit** all seven cases the eval says must produce a plan, the tightest
  being `plan-001`: 3 people, 7 days, $40

Those constraints leave a window of roughly **525g to 1197g**. 600 sits inside
it, nearer the lower edge. Both edges are recomputed from the catalogue by
`tests/test_feasibility.py`, so a price change that narrows the window fails
the build rather than passing quietly.

## What would change the answer

- **A domain view that 600g is too low.** Raising it refuses more requests as
  impossible. The ceiling is ~1197g before the system starts refusing
  `plan-001`, which the eval says must produce a plan — so if you believe the
  right number is above that, the eval case is what needs revisiting, not just
  the config.
- **A domain view that 600g is too high.** Lowering it accepts more requests.
  Below ~525g the system stops refusing the $5-for-two-people case, which the
  test suite says must be refused.
- **A materially different catalogue.** The window is a property of *these*
  prices. Real supermarket data with a cheaper staple would widen it; a
  narrower price range would tighten it.

## If you decide to change it

1. Edit `min_grams_per_person_day` in `config/feasibility.json`. Nothing else.
2. Run `pytest tests/test_feasibility.py`. It will tell you which existing
   expectation your value contradicts, if any.
3. Update `_calibration` and `_safe_range` in that file to say what you decided
   and why, and replace the `_review` note.

If a test fails, that is the useful output: it names the case your judgement
disagrees with, and that disagreement is the thing to resolve — possibly by
changing the case rather than the number.

## Honest caveats from the author

- The value was chosen so that pre-existing expectations held, then sanity
  checked for margin. That is calibration against prior decisions, not
  independent justification. If those prior decisions were wrong, this
  inherits the error.
- It assumes every gram is interchangeable. Buying 600g of flour a day is not
  a meal, and this check would allow it. The check answers "is this possible",
  not "is this food".
- It is uniform across household members. A household of one adult and a
  household of five including small children get the same per-person floor.
- Related open item: `evals/cases/meal_plan.json` `plan-001` and `plan-005` had
  their budgets raised on the same day for a related reason (whole-pack
  pricing), recorded in `AGENTS.md`. A reviewer looking at feasibility may want
  to look at those cases too.
