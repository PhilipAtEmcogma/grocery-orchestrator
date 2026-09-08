"""
`requirements.txt` is a claim about what the shipped code needs. This checks it.

WHY IT EXISTS. Nothing verified the runtime dependency set, and on 2026-09-06
three of its six entries were false: `langchain-aws`, `langchain-core` and
`python-dotenv` were declared and imported by nothing. `langchain-aws` was the
expensive one -- 1.1 MB in the archive, and the only package that pulled numpy,
which is why `build_lambda.py` had grown a prune entry for numpy. A dead
dependency had acquired a workaround, and the workaround read like a control.

This is the same shape as `verify_unused()` in `scripts/build_lambda.py`, which
checks the opposite direction: that nothing EXCLUDED from the archive is
imported. Together they close the loop -- excluded means unused, and declared
means used.

WHAT IT DELIBERATELY DOES NOT CHECK. Transitive dependencies. `langchain-core`
is in the archive because langgraph needs it, and that is langgraph's business;
requiring every bundled package to be imported by us would fail on every
dependency of a dependency. The claim under test is about the file we write.

THE IMPORT NAMES ARE RESOLVED FROM METADATA, NOT FROM A HAND-WRITTEN MAP. A
distribution's name and its import name differ often enough to matter here --
`python-dotenv` imports as `dotenv`, `aws-lambda-powertools` as
`aws_lambda_powertools` -- and a hand-maintained mapping is one more thing that
can be wrong. `importlib.metadata.packages_distributions()` reads what pip
actually installed.
"""

from __future__ import annotations

import ast
import re
import sys
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_lambda import INCLUDE_DIRS, REQUIREMENTS, ROOT


def _normalise(name: str) -> str:
    """PEP 503 canonical form, so `python-dotenv` and `python_dotenv` agree."""
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_requirements() -> list[str]:
    """
    Distribution names in `requirements.txt`, without extras or specifiers.

    `aws-lambda-powertools[tracer]` is the distribution `aws-lambda-powertools`;
    the extra selects optional dependencies of it and is not a separate name.
    """
    names: list[str] = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        # Strip extras and any version specifier: the name is the leading run
        # of characters a distribution name is allowed to contain.
        match = re.match(r"^[A-Za-z0-9._-]+", line)
        assert match, f"could not read a distribution name from {raw!r}"
        names.append(match.group(0))
    return names


def import_names_by_distribution() -> dict[str, set[str]]:
    """Invert `packages_distributions()`: distribution -> top-level modules."""
    inverted: dict[str, set[str]] = {}
    for module, dists in packages_distributions().items():
        for dist in dists:
            inverted.setdefault(_normalise(dist), set()).add(module)
    return inverted


def shipped_python_trees() -> list[Path]:
    """
    The directories `build_lambda.py` copies into the archive that hold Python.

    Derived from `INCLUDE_DIRS` rather than restated, so a tree added to the
    archive later is covered here without anyone remembering to add it.
    """
    trees = []
    for name in INCLUDE_DIRS:
        path = ROOT / name
        if path.is_dir() and any(path.rglob("*.py")):
            trees.append(path)
    return trees


def imported_top_level_modules() -> set[str]:
    """Every top-level module name imported by code that ships."""
    modules: set[str] = set()
    for tree in shipped_python_trees():
        for path in tree.rglob("*.py"):
            tree_source = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree_source):
                if isinstance(node, ast.Import):
                    modules.update(alias.name.split(".")[0] for alias in node.names)
                # `level > 0` is a relative import -- `from .base import X` has
                # no top-level distribution behind it and must not be counted.
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    modules.add(node.module.split(".")[0])
    return modules


def test_the_requirements_file_lists_something() -> None:
    """A parse that silently returned nothing would make every check below vacuous."""
    assert declared_requirements(), "no requirements parsed -- the file or the parser is wrong"


def test_shipped_trees_were_found() -> None:
    """Same guard from the other side: an empty scan proves nothing."""
    trees = shipped_python_trees()
    assert trees, "no Python trees resolved from INCLUDE_DIRS"
    assert imported_top_level_modules(), "no imports found across the shipped trees"


@pytest.mark.parametrize("distribution", declared_requirements())
def test_every_declared_requirement_is_imported_by_shipped_code(distribution: str) -> None:
    """
    A runtime dependency nobody imports is archive weight and audit surface.

    Parametrised so the failure names the offending distribution rather than
    reporting a set difference the reader has to diff by eye.
    """
    by_distribution = import_names_by_distribution()
    provided = by_distribution.get(_normalise(distribution))

    assert provided is not None, (
        f"{distribution} is declared in requirements.txt but is not installed, "
        f"so what it imports as cannot be checked. Install it "
        f"(pip install -r requirements-dev.txt) or remove the line."
    )

    imported = imported_top_level_modules()
    assert provided & imported, (
        f"{distribution} is declared in requirements.txt and imported by nothing "
        f"that ships. It provides {sorted(provided)}, none of which appear in "
        f"{[str(p.relative_to(ROOT)) for p in shipped_python_trees()]}. "
        f"Either import it or drop the line -- a declared dependency is a claim "
        f"that the code needs it, and it costs archive size and audit surface."
    )
