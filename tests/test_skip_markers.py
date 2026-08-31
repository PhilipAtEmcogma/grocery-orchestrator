"""
A skipped test must name a condition that can expire, not a sentence that ages.

THIS IS THE SIBLING OF `test_ci_workflow.py`. That one asserts every CI job is
wired into the aggregate check, because a job that runs and gates nothing looks
exactly like a job that gates something. This one asserts the same property one
level down: a suite that is skipped reports "skipped", which in a summary line
is visually next to "passed" and means the opposite.

WHY IT EXISTS, WITH THREE INSTANCES BEHIND IT. Two audits found the same defect
three times, and it arrived by the same route every time -- a control was
written BEFORE the thing it guards existed, and nobody went back when the thing
arrived:

  1. `scripts/check_recipe_coverage.py` resolved against the 26-product fixture
     file while three documents described the real 2,939-row catalogue.
  2. The forcing test behind that instrument watched the FIXTURE catalogue,
     which `scripts/generate_fixtures.py` regenerates to a fixed shape and which
     therefore cannot grow. Its trigger was unreachable by construction.
  3. `infra/test/service-stack.test.ts` was `describe.skip` under the header
     "SKIPPED until ServiceStack is implemented (it is a stub today)". The stack
     had been 230 lines and DEPLOYED for a day. Seven security assertions had
     never executed once, and one of them had silently inverted.

The difference between them and every skip that is fine here is a single
property: **a condition a machine can re-evaluate**.
`@pytest.mark.skipif(not DATASET.exists(), ...)` stops skipping the moment the
dataset is checked out. "SKIPPED until X is implemented" never stops, because
nothing evaluates the English.

So the rule is not "do not skip". It is: state the condition in code, so the
skip expires on its own.

WHAT IS ALLOWED
    @pytest.mark.skipif(<expr>, reason=...)     the expr is the expiry
    if <expr>: pytest.skip("...")               the `if` is the expiry
    (cond ? describe : describe.skip)(...)      the TypeScript form of the same

WHAT IS NOT
    @pytest.mark.skip(reason="until X lands")   nothing re-checks "until"
    @pytest.mark.xfail(reason=...)              without a condition
    describe.skip(...) / it.skip(...) / xit(...)
    describe.only(...) / it.only(...) / fit(...)   -- worse: `.only` silently
        disables every OTHER test in the file, so the suite shrinks without a
        single "skipped" appearing in the summary.

This test covers BOTH languages on purpose. The instance that motivated it is
in TypeScript, and a Python-only version of this check would be one more
control pointed away from where the defect actually was.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: Python trees whose skips are gated. `scripts/` and `src/` are not test code.
PYTHON_ROOTS = ("tests", "evals")

#: TypeScript trees. `node_modules` and `cdk.out` are excluded by construction:
#: they are dependencies and build output, not this repository's claims.
TS_ROOTS = ("infra/test", "infra/lib", "infra/bin")

#: Statements whose body is only reached when something was evaluated. A
#: `pytest.skip()` inside one of these has a condition; one outside them all is
#: an unconditional skip wearing a function call.
_CONDITIONAL_NODES = (ast.If, ast.Try, ast.ExceptHandler, ast.For, ast.While, ast.With)

_TS_MARKER = re.compile(
    r"\b(?:(?P<kind>describe|it|test)\.(?P<mod>skip|only)|(?P<x>xdescribe|xit|fdescribe|fit))\s*\(",
)

#: `//` to end of line, and `/* ... */` across lines.
_TS_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _without_comments(source: str) -> str:
    """
    Blank the comments, keeping every byte offset and newline.

    Comments are blanked rather than deleted so a reported line number still
    points at the real line. And they ARE blanked rather than scanned: this
    repository documents its own defects in prose, and the header of
    `infra/test/service-stack.test.ts` necessarily contains the marker it is
    describing the removal of. The first version of this detector flagged that
    header, which is a false positive with a real cost -- it teaches people to
    stop writing down what went wrong, in a repository whose best artefacts are
    exactly those write-ups.
    """
    return _TS_COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), source)


def _python_files() -> list[Path]:
    return sorted(p for root in PYTHON_ROOTS for p in (ROOT / root).rglob("*.py"))


def _ts_files() -> list[Path]:
    return sorted(p for root in TS_ROOTS for p in (ROOT / root).rglob("*.ts"))


def _decorator_name(node: ast.expr) -> str:
    """`pytest.mark.skipif` from either a Call or a bare Attribute."""
    target = node.func if isinstance(node, ast.Call) else node
    parts: list[str] = []
    while isinstance(target, ast.Attribute):
        parts.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name):
        parts.append(target.id)
    return ".".join(reversed(parts))


def _unconditional_markers(tree: ast.AST) -> list[tuple[int, str]]:
    """`@pytest.mark.skip` and condition-less `@pytest.mark.xfail`."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        for deco in node.decorator_list:
            name = _decorator_name(deco)
            if name == "pytest.mark.skip":
                found.append((deco.lineno, "@pytest.mark.skip"))
            elif name == "pytest.mark.xfail":
                # xfail with a first positional arg is conditional; without one
                # it is a permanent expectation of failure, which ages the same
                # way a skip does.
                positional = deco.args if isinstance(deco, ast.Call) else []
                if not positional:
                    found.append((deco.lineno, "@pytest.mark.xfail without a condition"))
    return found


def _unconditional_skip_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """`pytest.skip(...)` reached on every run, rather than under a condition."""
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _decorator_name(node) != "pytest.skip":
            continue
        # `allow_module_level=True` is a deliberate whole-module skip and still
        # needs a condition, so it is not special-cased here.
        current: ast.AST | None = node
        guarded = False
        while current is not None:
            if isinstance(current, _CONDITIONAL_NODES):
                guarded = True
                break
            current = parents.get(current)
        if not guarded:
            found.append((node.lineno, "pytest.skip() not under a condition"))
    return found


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_the_scan_has_input():
    """
    A scan of nothing also passes.

    The secret-scan false green is the reason this assertion is here: every
    planted-credential check passed while `detect-secrets` never ran once, and
    an exit code could not tell the difference. Assert on the input.
    """
    assert len(_python_files()) > 25, "the Python test tree did not resolve"
    assert len(_ts_files()) > 3, "the TypeScript tree did not resolve"


def test_no_python_test_is_skipped_without_a_machine_checkable_condition():
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, what in _unconditional_markers(tree) + _unconditional_skip_calls(tree):
            offenders.append(f"{_rel(path)}:{lineno} {what}")

    assert not offenders, (
        "These skips never expire, because nothing re-evaluates them:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse `@pytest.mark.skipif(<expr>, reason=...)` or an `if` around "
        "`pytest.skip(...)`, so the skip stops the moment its reason stops "
        "being true. See this module's docstring for the three defects behind "
        "this rule."
    )


def test_no_typescript_suite_is_skipped_or_narrowed():
    offenders: list[str] = []
    for path in _ts_files():
        code = _without_comments(path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(code.splitlines(), start=1):
            match = _TS_MARKER.search(line)
            if match is None:
                continue
            # The conditional idiom, which is the TypeScript equivalent of
            # `skipif`: `(cond ? describe : describe.skip)('...', ...)`. A `?`
            # before the marker means something is evaluated, so it expires.
            if "?" in line[: match.start()]:
                continue
            marker = match.group("x") or f"{match.group('kind')}.{match.group('mod')}"
            offenders.append(f"{_rel(path)}:{lineno} {marker}(")

    assert not offenders, (
        "These suites do not run, and a summary line will say 'skipped' rather "
        "than 'failed':\n  "
        + "\n  ".join(offenders)
        + "\n\n`infra/test/service-stack.test.ts` sat behind `describe.skip` "
        "with seven security assertions inside it while the stack it guards was "
        "deployed, and one assertion had inverted in the meantime. Use "
        "`(cond ? describe : describe.skip)(...)` so the skip carries a "
        "condition, or delete the suite. `.only` is refused for the same "
        "reason and is worse: it disables every other test silently."
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import pytest\n\n@pytest.mark.skip(reason='later')\ndef test_a(): ...\n", 1),
        ("import pytest\n\n@pytest.mark.skipif(True, reason='ok')\ndef test_a(): ...\n", 0),
        ("import pytest\n\n@pytest.mark.xfail(reason='known')\ndef test_a(): ...\n", 1),
        ("import pytest\n\n@pytest.mark.xfail(True, reason='ok')\ndef test_a(): ...\n", 0),
        ("import pytest\n\ndef test_a():\n    pytest.skip('nope')\n", 1),
        ("import pytest\n\ndef test_a():\n    if 1:\n        pytest.skip('nope')\n", 0),
    ],
)
def test_the_python_detector_discriminates(source: str, expected: int):
    """
    The detector is checked against planted defects, not trusted.

    Every finding in this repository's history of "a control that looked like it
    was working" would have been caught by watching the control FAIL once. A
    scanner that reports zero offenders is indistinguishable from a scanner that
    cannot see, and this repository has shipped the second one twice.
    """
    tree = ast.parse(source)
    assert len(_unconditional_markers(tree) + _unconditional_skip_calls(tree)) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("describe.skip('x', () => {});", 1),
        ("it.skip('x', () => {});", 1),
        ("test.only('x', () => {});", 1),
        ("xit('x', () => {});", 1),
        ("fdescribe('x', () => {});", 1),
        ("const d = ok ? describe : describe.skip;", 0),
        ("(hasCreds ? describe : describe.skip)('x', () => {});", 0),
        ("describe('x', () => {});", 0),
        ("// it was describe.skip(...) until 2026-08-31", 0),
        ("/* describe.skip(...) */ it('x', () => {});", 0),
        ("it.skip('x', () => {}); // why", 1),
    ],
)
def test_the_typescript_detector_discriminates(line: str, expected: int):
    """
    Including two comment cases, which must NOT be flagged, and one trailing
    comment, which must not hide the marker in front of it.

    The detector is checked against planted defects rather than trusted. Every
    "control that looked like it was working" in this repository's history would
    have been caught by watching the control fail once.
    """
    code = _without_comments(line)
    match = _TS_MARKER.search(code)
    hit = 0 if match is None or "?" in code[: match.start()] else 1
    assert hit == expected
