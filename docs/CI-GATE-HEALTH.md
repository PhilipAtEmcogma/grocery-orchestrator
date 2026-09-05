# CI gate health — standing recommendations

Observations from merging PRs #17 and #19 (2026-08-27). Each entry records a
gap that was *latent*: the gate was green, and every one of these would let it
go red for a reason unrelated to the change that trips it.

Nothing here blocks a merge. They are ordered by how much warning you get
before they bite.

**Status as of 2026-09-02.** §2 to §6 are resolved and kept for their
reasoning rather than deleted. **§1 is largely closed** — the case counts grew
on 2026-09-02 (intent 30 -> 47, meal-plan 11 -> 20), which is the fix it had
been asking for, and intent now carries four spare cases where it had none.
What survives of §1 is narrower and still real: the meal-plan suite still
cannot rank two models that both score 100%, because every check in it is a
rule-violation check and neither model breaks rules.

Two of them stopped being hypothetical in the meantime, in the same afternoon:
adopting `ruff format` moved line numbers across the tree, which invalidated
the secrets baseline (§3) and then, on rescan, produced the Windows backslash
paths §2 was written about. A latent gap is one that has not bitten *yet*.

---

## 1. Both eval floors are one failing case from red

> **LARGELY CLOSED 2026-09-02 — the case counts grew, which is the fix this
> section spent three revisions asking for.** `f8cd86d` took intent from 30
> cases to 47 and meal-plan from 11 to 20. Intent now has **four** spare cases
> where it had none. The heading is kept because the *reasoning* below is the
> part worth carrying, and because §1 stayed the one open entry in this file
> for a fortnight on the strength of it.

Re-measured 2026-09-02, against the grown suites:

| Eval | Result | Floor | Next failure |
|---|---|---|---|
| `evals/run_intent.py` | 40/47 = 85.1% | 75.0% | 36/47 = 76.6% green; 35/47 = 74.5% — **red** |
| `evals/run_meal_plan.py` | 20/20 = 100% | 90.0% | 18/20 = 90.0% green; 17/20 = 85.0% — **red** |

The floor is inclusive (`actual < floor` fails), so 18/20 = exactly 90.0%
passes. Intent survives four failures and dies on the fifth; meal-plan survives
two and dies on the third.

**THE FLOORS WERE NOT RAISED, AND THAT IS THE DECISION RATHER THAN AN
OVERSIGHT.** `scripts/hooks/pre-commit` says "Floors, not targets. Never lower
one to make a commit pass," and the companion rule in `AGENTS.md` is "raise one
when the baseline genuinely improves." **This baseline did not improve.**
Intent moved 76.7% -> 85.1% because the suite went from 30 cases to 47, so the
instrument changed and the system did not; the two numbers are not comparable
and neither is evidence about the other. A floor raised on that movement would
be a number chosen to fit an answer, which is the thing this file exists to
catch. Raise the floors from a re-measurement taken *after* the suite settles,
or not at all.

**One consequence to carry into Task 16. — CARRIED AND CLOSED 2026-09-04.**
All three were re-measured live against the 47-case suite as gate G5 and written
back into `config/models.json`: **Nova Pro 97.8%, Claude Haiku 4.5 97.8%, Nova
Lite 95.6%** (45 scored, 2 guardrail-excluded). Nova Pro's 100% did not survive
the larger suite, which is the outcome this section predicted; Nova Lite, the
active route, improved. The reasoning below is kept because it is why the
re-measurement was demanded rather than the old figures cited.

The live intent scorecards in
`config/models.json` — Nova Pro 100.0%, Claude Haiku 4.5 96.4%, Nova Lite 92.9%
— were measured on 2026-08-29 against the **30-case** suite, which their
`_source` field says plainly. They are honest about their own provenance and
they are evidence about a suite that no longer exists. Req 14.2 requires every
active route to score at least 90% on "its applicable golden set", and the
applicable golden set has changed underneath all three, so the release gate
wants a re-measurement rather than a citation of these.

Kept for its reasoning, from the 2026-08-29 revision: meal-plan gained its
first spare case when the whole-pack pricing work took the scripted baseline
from 90.9% to 100%. `plan-003`, named below as the boundary case, now passes —
it was under-spending at exactly the 30% floor, and pre-filtering candidates to
the budget lifted utilisation clear of it.

**2026-08-29: this stopped being an argument about percentages.** The
guardrail suite's seven `must_allow` cases scored 7/7 while the deployed policy
was refusing `how much is truffle oil`, `price of mushrooms` and `cheapest
button mushrooms` — mushrooms being an everyday grocery item. The defect was
found by investigating an *intent* eval failure, not by the suite whose entire
job is to catch over-blocking. Seven benign cases were all too ordinary to
include an unusual-but-legitimate product.

That is what too few cases costs: not a score that jitters, but a passing gate
over a live product defect. `allow-008` and `allow-009` now guard the fixed
queries (`must_allow` is 9 cases), and a bare `price of mushrooms` is still
refused and is deliberately NOT a case — a permanently red gate is one people
stop reading. Reproduction in `docs/LIVE-EVAL-RUNBOOK.md` §8.5.

The underlying problem was about case count, not threshold, and growing the
suites is what addressed it: a meal-plan case was worth 9 percentage points at
11 cases and is worth 5 at 20; an intent case was worth 3.3 at 30 and is worth
2.1 at 47. The overshoot is smaller, not gone.

This is not an argument for lowering the floors — `scripts/hooks/pre-commit`
says "Floors, not targets. Never lower one to make a commit pass," and that is
correct. The problem is the *case count*, not the threshold. Forty-seven intent
cases make each case worth 2.1 points; a hundred would make it 1.

Recommended: grow the case files before the next behavioural change, so the
floors measure the system rather than the sampling. `evals/cases/intent.json`
already carries the two known failures (`oos-001`, `inj-002`) and
`plan-003` no longer sits on its boundary, but the lesson stands: boundary
cases are the ones that flip from unrelated changes.

This now overlaps a second problem from the other direction — the meal-plan
suite at 100% for BOTH candidate models can no longer rank them, so the same
"add harder cases" work serves both. See the eval discipline section in
`AGENTS.md`.

## 2. `.secrets.baseline` canonical form is enforced only locally — RESOLVED 2026-08-29

`scripts/normalise_secrets_baseline.py` fixes the Windows/Linux separator
churn, and `scripts/hooks/pre-commit` runs it. CI does not.

That leaves a hole for anyone who has not run
`git config core.hooksPath scripts/hooks` on their clone, or who commits with
`--no-verify`. `detect-secrets` writes `filename` values using the local
`os.sep`, so a rescan on Windows produces `datasets\data\processed\...`.
Those entries are read correctly on any platform, but the *hook* in CI matches
the forward-slash paths `git ls-files` emits on `ubuntu-24.04`, so a
backslash baseline means every recorded false positive reads as a brand-new
secret and the Secret scan fails.

The failure mode is the bad one: it passes on the author's machine and fails
only in CI, which is precisely the gap the pre-commit hook was written to
close for the secret scan in the first place.

Recommended: add a canonical-form check to the Security scanning job, next to
the existing scan —

```yaml
- name: Baseline is canonical
  run: |
    python scripts/normalise_secrets_baseline.py
    git diff --exit-code .secrets.baseline \
      || { echo "::error::.secrets.baseline is not canonical. Run: python scripts/normalise_secrets_baseline.py"; exit 1; }
```

**Resolved: added**, as a `Baseline is canonical` step in the Security
scanning job, ordered BEFORE the scan it explains — a non-canonical baseline
makes the scan report "a new secret was detected" for every recorded false
positive, which sends the reader hunting a secret that is not there.

Implemented as an explicit `if` rather than the `git diff --exit-code` form
sketched above: under `bash -e` the script's own non-zero exit ends the step
before any diff could report why, so the sketch would have failed correctly
with the wrong message.

It earned its place before it was even merged. Adopting `ruff format` moved
line numbers across the tree, the baseline needed a rescan (§3), and rescanning
on Windows wrote seven backslash paths — the exact failure this section
describes, twice in one afternoon.

## 3. The baseline records line numbers, so unrelated edits move it — RESOLVED 2026-08-29

Every entry in `.secrets.baseline` carries a `line_number`. Insert a line above
a known false positive and the recorded number is stale, the scan reports a
new finding, and CI fails on a change that had nothing to do with secrets.

This has now happened twice. PR #17 shifted the `tests/test_guardrail.py`
entry from 193 to 194 by editing the file above it, and that shift was the
entire content of the merge conflict against `main`. Commit `1c0ad88` tripped
it from the other direction, adding
`datasets/data/processed/recipes_latest.json:4` — a line reading
`"api_key_note": "Public development key 1 was used..."`, which is prose about <!-- pragma: allowlist secret -->
a public key, not a credential.

Quoting that line here tripped the scan again while this file was being
committed, which is the point made twice over. It is suppressed with a pragma
in an HTML comment — invisible in rendered Markdown, honoured by
`detect_secrets.filters.allowlist.is_line_allowlisted` all the same.

**Resolved: both test-file entries moved to inline pragmas**, exactly as
recommended, and the baseline now holds one entry — the dataset JSON, which
cannot carry a comment.

    tests/test_guardrail.py   an assertion on a "PASSWORD" guardrail action
    tests/test_handler.py     a fake postgres:// DSN in a RuntimeError message

The first is described rather than quoted, because quoting it here trips the
scanner on this file — which is the third time this section has demonstrated
its own subject while being edited. The other two are recorded above.

The second needed the string bound to a name first: the pragma plus the
original `raise` exceeded the 100-column limit, and wrapping the `raise` would
have put the pragma on a line the scanner does not flag.

Verified rather than assumed. Three lines were inserted at the top of both
files — the exact edit that used to break the scan — and it still passed,
because a pragma travels with its line. It happened a third time in the
meantime: adopting `ruff format` moved line numbers across the tree and
invalidated the baseline, which is what sent someone back to this section.

## 4. Green-alone is not green-combined — RESOLVED 2026-08-29

Dependabot #19 (`ruff` 0.16.3 → 0.16.4) had all seven checks passing, but they
ran against `main` *without* #17's 4,443 new lines. A linter release can add
rules that fire on code the release never saw, so two PRs can each be green
and red together.

Checked explicitly before merging: `ruff` 0.16.4 against `main` + #17 is
`All checks passed!`, matching pinned 0.16.3 on the same tree. So the
combination was safe this time.

**Resolved: it is already enforced, by a control this section did not know
about.** Branch protection on `main` has `strict: true` on its required status
check:

```console
$ gh api repos/.../branches/main/protection/required_status_checks     --jq '{strict: .strict, contexts: .contexts}'
{"contexts":["All checks"],"strict":true}
```

`strict` is "require branches to be up to date before merging". So the
scenario in this section cannot reach `main`: once #17 merges, #19 is out of
date, GitHub blocks its merge until it is updated, and every check re-runs
against the combination. The manual re-run this section recommends is what the
setting does automatically, and `enforce_admins` is on, so it cannot be waved
through.

Worth writing down because a control nobody has recorded is one somebody
switches off.

**The real hole was one layer down, and is now closed.** Protection requires
exactly one check, `All checks`, which passes or fails on its `needs` list.
Its own comment says a single required check means "adding a job later does not
mean reconfiguring the protection rule" — true, and it hides a trap: a new job
absent from `needs` runs, reports its own status, and gates nothing. The PR
goes green with a failing job on it.

`tests/test_ci_workflow.py` asserts the wiring: every job appears in `needs`,
`needs` names no job that does not exist, the aggregate is `if: always()` so a
failed dependency cannot skip it into reporting nothing, and it actually reads
`needs.*.result` rather than passing unconditionally. Verified by adding a job
outside `needs` and watching the test name it.

## 5. `ruff format` is not enforced, and the tree has drifted — RESOLVED 2026-08-29

CI runs `ruff check` only. `ruff format --check .` reports **59 of 104 files
would be reformatted** (re-measured 2026-08-29; it was 39 of 75, so the drift
grows as the repo does).

That is not a bug — formatting was evidently never adopted — but the drift
grows, and adopting it later means one enormous mechanical diff across most of
the repo, landing on top of whatever is in flight.

**Resolved: adopted.** The reformat landed as a single isolated commit against
a clean `main` with nothing else open, `ruff format --check --diff .` now runs
in CI beside `ruff check`, and the pre-commit hook checks the same thing. The
decision and its cost are recorded in `.kiro/steering/tech.md`.

`.git-blame-ignore-revs` carries the reformat commit, because `git blame` on 59
files would otherwise point at it rather than at whoever wrote the line — and
this repository keeps most of its value in comments attached to specific lines.
Run `git config blame.ignoreRevsFile .git-blame-ignore-revs` once per clone;
GitHub reads it automatically.

Kept rather than deleted because the reasoning is the useful part: the drift
grew from 39 of 75 files to 59 of 104 while the question sat open, which is the
argument for deciding these promptly rather than the argument for this
particular answer.

## 6. `actions/upload-artifact@v5` still targets Node 20 — RESOLVED 2026-08-29

Every CI run carries the annotation:

> Node.js 20 is deprecated. The following actions target Node.js 20 but are
> being forced to run on Node.js 24: `actions/upload-artifact@v5`.

Harmless now — the runner forces Node 24 — but it is the only warning
annotation on an otherwise clean run, and a warning that is always present is
one nobody reads. Every other action in `ci.yml` is on `@v7`.

**Resolved: bumped to `@v7`.** `v7.0.1` declares `using: node24` where `v5.0.0`
declared `using: node20`, so the annotation is gone rather than suppressed —
verified by reading each tag's `action.yml` rather than by assuming the version
bump implied it. Every action in `ci.yml` is now on `@v7`, and a new annotation
would show against a quiet baseline.
