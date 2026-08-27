# Claude Code permission hygiene — allowlist audit

Record of the `/doctor` audit run on 2026-08-27 against
`.claude/settings.local.json`, and the standing rules that keep the file from
drifting back.

That file is per-developer and gitignored, so nothing here changed the shared
tree. It is documented anyway because the failure mode is silent: an allowlist
grows one approval at a time, each individually reasonable, and no one ever
re-reads the accumulated result.

---

## 1. What an allow rule actually does

`permissions.allow` entries suppress the confirmation prompt. A matching
command runs **without being shown to you first**. That is the whole mechanism —
there is no secondary check behind it.

Two properties make wildcards more dangerous than they look:

- **Prefix rules are string matches, not flag analysis.** `Bash(git log *)`
  matches every flag `git log` accepts, including `--output=<file>`, which
  writes to disk. Claude Code's own read-only validators
  (`readOnlyCommandValidation.ts`) accept only an enumerated safe-flag set per
  subcommand; a prefix rule bypasses that entirely.
- **A "read-only" subcommand is not a read-only invocation.** `git fetch` looks
  inert and accepts `--upload-pack='<cmd>'`, which executes an arbitrary
  command. `ext::` remote URLs do the same.

## 2. Rules removed on 2026-08-27

The file held 176 rules, 37 of them wildcards. Eighteen were removed; 158
remain. Every removal restored a prompt — none blocked any workflow.

### Arbitrary code execution (8)

| Rule | What it permitted |
|---|---|
| `Bash(python -c ' *)` | Any Python code |
| `Bash(./.venv/Scripts/python.exe -c ' *)` | Any Python code |
| `Bash(.venv/Scripts/python.exe -c ' *)` | Any Python code |
| `Bash(git fetch *)` | `--upload-pack='<cmd>'` and `ext::` remotes run arbitrary commands |
| `Bash(git pull *)` | Same as `git fetch` |
| `Bash(git config *)` | Sets `core.pager`/`core.editor`/aliases to any command, which then runs on the *next* git call |
| `Bash(gh api *)` | POST, DELETE and GraphQL mutations — "GET-only" cannot be expressed as a prefix |
| `Bash(gh auth *)` | `gh auth token` prints the GitHub token; `gh auth logout` |

### Destructive or publishing (10)

| Rule | What it permitted |
|---|---|
| `Bash(git push *)` | Force-push to any branch, `main` included |
| `Bash(git reset *)` | `--hard` discards uncommitted work |
| `Bash(git checkout *)` | `-- .` discards uncommitted work |
| `PowerShell(git checkout *)` | Same, other shell |
| `Bash(git rebase *)` | Rewrites history |
| `Bash(git rm *)` | Deletes tracked files |
| `Bash(git branch *)` | `-D` deletes branches |
| `Bash(git update-index *)` | `--assume-unchanged` hides local edits from status |
| `Bash(gh repo *)` | `gh repo delete` |
| `Bash(gh pr *)` | `gh pr merge`, `gh pr close` |

`git push *` is the one that matters most here. Verified against the GitHub API
on 2026-08-27: `main` carries classic branch protection — recorded in commit
`538dc09`, "Record branch protection on main (Task 9.8)" (#3) — with
`allow_force_pushes` and `allow_deletions` both `false` and `enforce_admins`
`true`. A force-push or a branch delete is therefore refused server-side even
for the repository owner, and no repository ruleset overrides it (`/rulesets`
returns empty). Required status checks are `strict` against the `All checks`
context, and conversation resolution is required.

That protection is real, but it is narrow in a way the removed rule was not.
It covers one branch; `Bash(git push *)` covered every other branch in the
repo. It is enforced at the remote, after the command has already run locally.
And it constrains one outcome — a rewritten or deleted `main` — not the general
case of publishing without being asked. A server-side backstop on one branch is
not a reason to skip the prompt on all of them.

## 3. What was deliberately kept

- **Exact `python -c "…"` rules.** Seven remain, each pinning one complete
  script string (`python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))…"`).
  An exact rule pre-approves that one command and nothing else. Only the
  wildcard forms were removed.
- **`Bash(git add *)`, `Bash(git commit *)`, `Bash(git stash *)`.** Local and
  recoverable. `git commit` is additionally governed by the standing
  instruction to commit only when asked.
- **Genuinely read-only wildcards**: `git diff *`, `git log *`, `git fsck *`,
  `git check-ignore *`, `ruff check *`, `detect-secrets scan *`.
- **`Bash(echo "EXIT=$?")` and `Bash(echo "exit=$?")`.** These are *not*
  duplicates — permission matching is case-sensitive, so they are distinct
  rules. PowerShell's `Group-Object` reports them as one because it compares
  case-insensitively; that is a reporting artefact, not a finding.

## 4. How to undo

The pre-change file was backed up to
`.claude/settings.local.json.doctor-backup` and then deleted once this document
existed, because an untracked backup sitting next to a gitignored file is
exactly the kind of stray that reaches `main` — `datasets/.DS_Store` arrived
that way (see `.gitignore`).

**This section is the restore path.** To reinstate a rule, add its exact string
back to `permissions.allow` in `.claude/settings.local.json`. To reinstate all
eighteen, add every string from the two tables in §2.

Individual rules can also be re-approved the ordinary way: run the command,
and choose the "always allow" option at the prompt. That writes the same entry
back.

## 5. Standing recommendations

**Prefer exact rules to wildcards.** A handful of exact rules beats one
wildcard. `Bash(gh pr view 17)` is a pre-approval you can reason about;
`Bash(gh pr *)` is not.

**Never allowlist these, in any form** — each is arbitrary execution wearing a
read-only costume:

- Interpreters and package runners: `python`, `node`, `npx`, `bunx`, `uv run`
- Task-runner wildcards: `npm run *`, `make *`
- `curl` and `wget` — they POST, and they exfiltrate
- `git fetch`, `git pull` — see §1
- `gh api` in any wildcard form
- `find` with `-exec` or `-delete`
- Anything carrying `-c <key>=<value>`, `--exec-path`, `--upload-pack`, a
  `VAR=x` prefix, a pipe, or a redirection

**Re-read the file when it passes ~100 rules.** Growth is the signal, not any
single entry. `/doctor` performs this audit; the check is "would I approve this
if asked right now?", applied to each wildcard.

**Test runners stay prompted.** `uv run pytest` was denied twice in the audit
window and is correctly absent from the allowlist — it executes the test suite,
which executes project code. Prompting on it is the intended behaviour, not
friction to remove.

## 6. Related configuration

`permissions.defaultMode` is `"auto"` in `~/.claude/settings.json` (user scope,
applies to every project). Auto mode routes each action through a safety
classifier instead of prompting on all of them. It reduces the pressure to
accumulate allow rules, which is the point — but it is not a reason to keep a
broad allowlist, because an explicit allow rule takes precedence and skips the
classifier.

`.gitignore` ignores `.claude/settings.local.json` only, deliberately, "so a
shared `.claude/settings.json` remains possible". That distinction is worth
preserving: per-developer permissions are personal, but a project-scoped
settings file, skills, or agent definitions under `.claude/` are shareable and
reviewable.
