# CI gate health — standing recommendations

Observations from merging PRs #17 and #19 (2026-08-27). Each entry records a
gap that was *latent*: the gate was green, and every one of these would let it
go red for a reason unrelated to the change that trips it.

Nothing here blocks a merge. They are ordered by how much warning you get
before they bite.

**Status as of 2026-08-29.** §2, §5 and §6 are resolved and kept for their
reasoning rather than deleted. §1, §3 and §4 remain open.

Two of them stopped being hypothetical in the meantime, in the same afternoon:
adopting `ruff format` moved line numbers across the tree, which invalidated
the secrets baseline (§3) and then, on rescan, produced the Windows backslash
paths §2 was written about. A latent gap is one that has not bitten *yet*.

---

## 1. Both eval floors are one failing case from red

Measured on the merged tree:

Re-measured 2026-08-29:

| Eval | Result | Floor | Next failure |
|---|---|---|---|
| `evals/run_intent.py` | 23/30 = 76.7% | 75.0% | 22/30 = 73.3% — **red** |
| `evals/run_meal_plan.py` | 11/11 = 100% | 90.0% | 10/11 = 90.9% green; 9/11 = 81.8% **red** |

Intent still has no spare case. Meal-plan gained one: the whole-pack pricing
work took the scripted baseline from 90.9% to 100%, so it now survives one
failure and dies on the second. `plan-003`, named below as the boundary case,
now passes — it was under-spending at exactly the 30% floor, and pre-filtering
candidates to the budget lifted utilisation clear of it.

The underlying problem is unchanged and is about case count, not threshold: one
meal-plan case is worth 9 percentage points, so the drop overshoots whenever it
comes.

This is not an argument for lowering the floors — `scripts/hooks/pre-commit`
says "Floors, not targets. Never lower one to make a commit pass," and that is
correct. The problem is the *case count*, not the threshold. Thirty intent
cases make each case worth 3.3 points; a hundred would make it 1.

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

## 3. The baseline records line numbers, so unrelated edits move it

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

Recommended: prefer an inline `# pragma: allowlist secret` on the offending
line over a baseline entry, for the two test-file hits at least. The pragma
travels with the line when the file is edited, so it cannot drift, and it is
readable at the point it applies rather than in a JSON file nobody opens. Keep
the baseline for cases where the line cannot carry a comment — the dataset
JSON above is one.

## 4. Green-alone is not green-combined

Dependabot #19 (`ruff` 0.16.3 → 0.16.4) had all seven checks passing, but they
ran against `main` *without* #17's 4,443 new lines. A linter release can add
rules that fire on code the release never saw, so two PRs can each be green
and red together.

Checked explicitly before merging: `ruff` 0.16.4 against `main` + #17 is
`All checks passed!`, matching pinned 0.16.3 on the same tree. So the
combination was safe this time.

Recommended: for any dependency bump that changes a *checker* — `ruff`,
`pyright` — re-run the gate against the merged result rather than trusting the
PR's own checks, whenever a substantial feature branch is open. The pins exist
for exactly this reason; `requirements-dev.txt` already explains why
("a checker release can turn main red with no repo change"). The pin stops the
surprise arriving unannounced; it does not tell you the upgrade is safe.

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
