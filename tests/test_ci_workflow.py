"""
The CI workflow gates what it claims to gate.

Branch protection requires exactly one check, `All checks`, and that job passes
or fails based on its `needs` list. The comment on it says a single required
check means "adding a job later does not mean reconfiguring the protection
rule" — true, and it hides a trap: a new job that is not added to `needs` runs,
reports its own status, and gates NOTHING. The PR goes green with a failing
job on it.

That is the same shape as every other defect worth fixing in this repo: a check
that looks like it is checking. So the wiring is asserted rather than trusted.

Reads the workflow file directly. No network, no GitHub API.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
AGGREGATE = "summary"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_the_workflow_parses(workflow):
    assert workflow["jobs"], "no jobs found — the parse or the path is wrong"


def test_the_aggregate_job_exists(workflow):
    """
    Named in branch protection as the sole required check. If it is renamed,
    protection silently requires a check that never reports, and every PR waits
    forever — or worse, merges if the rule is relaxed to unblock it.
    """
    assert AGGREGATE in workflow["jobs"]
    assert workflow["jobs"][AGGREGATE]["name"] == "All checks"


def test_every_job_gates_the_merge(workflow):
    """
    The one that matters. Every job except the aggregate itself must appear in
    its `needs`, or that job's failure does not block a merge.
    """
    jobs = set(workflow["jobs"]) - {AGGREGATE}
    needs = set(workflow["jobs"][AGGREGATE]["needs"])
    ungated = sorted(jobs - needs)
    assert not ungated, (
        f"these jobs run but do not gate the merge: {ungated}. "
        f"Add them to the `needs` of the '{AGGREGATE}' job."
    )


def test_the_aggregate_needs_nothing_that_does_not_exist(workflow):
    """A typo in `needs` skips the job silently rather than erroring."""
    jobs = set(workflow["jobs"])
    needs = set(workflow["jobs"][AGGREGATE]["needs"])
    missing = sorted(needs - jobs)
    assert not missing, f"`needs` names jobs that do not exist: {missing}"


def test_the_aggregate_runs_even_when_a_job_fails(workflow):
    """
    Without `if: always()` the aggregate is SKIPPED when a dependency fails,
    and a skipped required check does not report failure — it reports nothing,
    which some protection configurations treat as not-blocking.
    """
    assert workflow["jobs"][AGGREGATE].get("if") == "always()"


def test_the_aggregate_actually_inspects_the_results(workflow):
    """
    `if: always()` makes it run; something still has to make it FAIL. Without
    reading needs.*.result it would pass unconditionally, which is worse than
    having no aggregate at all because it looks like a gate.
    """
    steps = workflow["jobs"][AGGREGATE]["steps"]
    script = " ".join(step.get("run", "") for step in steps)
    assert "needs.*.result" in script
    assert "exit 1" in script
