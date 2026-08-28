"""
Build the Lambda deployment archive.

Two callers share this one script and therefore one exclusion list: a
developer running it directly (this repo is developed on Windows) and the
`package` job in `.github/workflows/ci.yml` (ubuntu-latest, natively
manylinux). The CI run is the authoritative build — see verify_import()
below for why the Windows run cannot fully confirm the same thing.

Why the flags are what they are:

- `--platform manylinux2014_x86_64 --only-binary=:all:` downloads Linux
  wheels regardless of host OS, so a package built on a Windows laptop has
  the same shape as one built on ubuntu-latest.
- `--python-version 3.13 --implementation cp` pins wheel resolution to the
  interpreter Lambda actually runs (tech.md), rather than whatever patch
  version happens to be on the build machine.
- `--no-compile` skips writing .pyc files for the excluded platform's
  bytecode magic number, which is dead weight either way.

The unzipped size is measured, not assumed. design.md cites ~47 MB, measured
on Linux; this machine, these resolved versions, may differ, so the number
below is recomputed on every run and the build fails over budget rather than
trusting the design doc.

Run: python scripts/build_lambda.py
Out: build/lambda.zip
"""

from __future__ import annotations

import ast
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = ROOT / "build"
STAGE_DIR = BUILD_DIR / "stage"
# Where the runtime-provided packages are set aside. Never zipped; it exists
# so verify_import() can stand in for /var/runtime.
RUNTIME_DIR = BUILD_DIR / "runtime"
ZIP_PATH = BUILD_DIR / "lambda.zip"
REQUIREMENTS = ROOT / "requirements.txt"

# Application code copied into the archive verbatim. Deliberately an
# allowlist rather than "everything except tests/evals/scripts/.kiro": the
# repo also holds infra/ and top-level docs no handler imports, which have no
# business inflating a Lambda archive. The effect is the same either way —
# tests/, evals/, scripts/ and .kiro/ never appear in the zip — but an
# allowlist can't accidentally sweep in something new added to the repo root
# later.
#
# `ingestion` is here because ONE archive serves TWO functions: the
# orchestrator entered at src.handler.lambda_handler and the price refresh
# entered at ingestion.handler.lambda_handler. Two zips would mean two builds
# to keep in step and two artefacts for the CI `package` job to verify, for
# about 10 KB of Python. The functions stay separate — separate IAM roles,
# separate invocation paths — and only the artefact is shared. `ingestion`
# needs `fixtures`, which was already here.
INCLUDE_DIRS = ["src", "config", "fixtures", "ingestion"]

# Installed but never imported by anything we ship, dead weight either way.
# Transitive pulls from langchain-aws and langsmith respectively.
# verify_unused() checks this claim against src/ directly rather than trusting
# the design doc.
#
# jmespath used to be on this list, on the reasoning that it was a boto3-only
# dependency and therefore moot once boto3 was excluded. That stopped being
# true when Powertools arrived: aws_lambda_powertools.logging.logger imports
# jmespath unguarded, so the claim "nothing of ours reaches for it" was false
# the moment we bundled a package that does. The rule that replaces it is
# statable — bundle everything our dependency tree declares, except what AWS
# documents the runtime provides — and jmespath is declared by a package we
# bundle, so it is ours. It costs ~50 KB.
UNUSED_TRANSITIVE = ["numpy", "zstandard"]

# The opposite case: imported, but never bundled, because the Lambda Python
# runtime ships its own copies in /var/runtime. Bundling ours would only be
# justified by needing a version ahead of the runtime's — see the dependency
# rules in tech.md. boto3 and botocore are ~80 MB together, which is the whole
# reason this distinction exists.
#
# These are NOT deleted. They are moved aside into RUNTIME_DIR, kept out of
# the zip, and put back on the path for verify_import() only — see there.
RUNTIME_PROVIDED = ["boto3", "botocore", "s3transfer"]

EXCLUDED_PACKAGES = UNUSED_TRANSITIVE + RUNTIME_PROVIDED

MAX_UNZIPPED_BYTES = 240 * 1024 * 1024  # Lambda's own ceiling is 250 MB.

# Console-script launchers pip drops under bin/ are built for the *host* OS
# (Windows .exe stubs, even for manylinux wheels) and Lambda never runs them
# — it only imports modules. Dropped unconditionally, not just for excluded
# packages.
GENERATED_SCRIPT_DIRS = ["bin", "Scripts"]


def verify_unused(names: list[str]) -> None:
    """Fail if an excluded package is actually imported somewhere in src/.
    The exclusion list is a claim about the codebase, not a hope."""
    offenders: dict[str, list[str]] = {}
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                top = module.split(".")[0]
                if top in names:
                    offenders.setdefault(top, []).append(str(path.relative_to(ROOT)))

    if offenders:
        for name, files in offenders.items():
            print(f"  {name} is imported by: {', '.join(files)}", file=sys.stderr)
        raise SystemExit(
            "EXCLUDED_PACKAGES claims packages that src/ actually imports. "
            "Fix the list (or stop excluding the package) before packaging."
        )


def install_dependencies(target: Path) -> None:
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-compile",
        "-r",
        str(REQUIREMENTS),
        "--platform",
        "manylinux2014_x86_64",
        "--only-binary=:all:",
        "--python-version",
        "3.13",
        "--implementation",
        "cp",
        "--target",
        str(target),
    ]
    subprocess.run(cmd, check=True)  # noqa: S603 -- fixed argv, no shell, no user input


def _matches(entry: Path, names: list[str]) -> bool:
    return any(
        entry.name == name or entry.name.startswith(f"{name}-") or entry.name.startswith(f"{name}.")
        for name in names
    )


def prune_excluded(target: Path, names: list[str], *, move_to: Path | None = None) -> None:
    """Remove matching top-level entries, or set them aside if `move_to` is given.

    Moving rather than deleting is what lets verify_import() model /var/runtime
    without a second pip download: the packages were already resolved by
    install_dependencies(), so they only need relocating out of the zip.
    """
    for script_dir in GENERATED_SCRIPT_DIRS:
        shutil.rmtree(target / script_dir, ignore_errors=True)

    if move_to is not None:
        move_to.mkdir(parents=True, exist_ok=True)

    for entry in sorted(target.iterdir()):
        if not _matches(entry, names):
            continue
        if move_to is not None:
            shutil.move(str(entry), str(move_to / entry.name))
        elif entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def copy_source(target: Path) -> None:
    for name in INCLUDE_DIRS:
        source = ROOT / name
        if not source.exists():
            continue
        shutil.copytree(
            source,
            target / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )


def directory_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def make_zip(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(source.rglob("*")):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(source))


def verify_import(zip_path: Path, runtime_dir: Path) -> None:
    """Unzip to a scratch dir and confirm the handler actually resolves — a
    zip that builds cleanly but can't be imported fails at cold start in
    production instead of here.

    The archive is deliberately NOT self-contained: boto3 and botocore come
    from the runtime. So the question this answers is "does it import in
    Lambda", not "does it import alone", and `runtime_dir` supplies exactly
    the packages RUNTIME_PROVIDED claims are there — nothing else. A package
    that is neither bundled nor on that list still fails here, which is the
    point.

    Until Powertools this distinction was invisible: src/models/bedrock.py
    imports boto3 inside a function, so nothing runtime-provided was reached
    at import time and "extract it alone and import" happened to pass. That
    ended permanently when the Tracer became a module-level object — it has
    to exist to decorate the handler, and constructing it pulls
    aws_xray_sdk.core.sampling.connector, which imports botocore.session
    unguarded.

    PYTHONPATH is searched after the working directory, mirroring Lambda's
    own /var/task-before-/var/runtime order, so a bundled copy would shadow
    the runtime's exactly as it does in production.

    A non-Linux build host cannot do this check honestly: the archive holds
    manylinux wheels (e.g. pydantic_core, a compiled extension), and this
    host's Python cannot load a Linux shared object no matter how correctly
    the archive is built. Attempting it anyway and trying to tell "expected
    platform mismatch" apart from "real packaging bug" by pattern-matching
    the error is exactly the kind of plausible-looking guess this codebase's
    own grounding invariant exists to avoid — so it isn't attempted. The
    `package` job on ubuntu-latest is where this is actually proven.
    """
    if platform.system() != "Linux":
        print(
            f"Skipping: {platform.system()} cannot load the manylinux wheels "
            "in this archive, independent of whether packaging is correct. "
            "The `package` job in ci.yml runs this exact check on "
            "ubuntu-latest — this build is unverified until that job passes."
        )
        return

    with tempfile.TemporaryDirectory(prefix="lambda-verify-") as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_path)

        env = {**os.environ, "PYTHONPATH": str(runtime_dir)}
        result = subprocess.run(
            # BOTH entrypoints. The archive serves two functions, and an
            # import that resolves for one proves nothing about the other:
            # ingestion.handler additionally reads fixtures/products.json at a
            # path resolved relative to the zip root, so a packaging change
            # that drops `fixtures` would leave the orchestrator importable and
            # every scheduled refresh failing.
            [
                sys.executable,
                "-c",
                "from src.handler import lambda_handler; "
                "from ingestion.handler import lambda_handler as ingest; "
                "from ingestion.sources import FIXTURES; "
                "assert FIXTURES.exists(), FIXTURES",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            raise SystemExit("Packaged archive does not import. See output above.")

        print(
            "OK: src.handler and ingestion.handler both resolve "
            f"(archive + {', '.join(RUNTIME_PROVIDED)} from the runtime)."
        )


def main() -> int:
    print("Verifying unused-transitive claim against src/ imports...")
    verify_unused(UNUSED_TRANSITIVE)

    print(f"Cleaning {BUILD_DIR} ...")
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    STAGE_DIR.mkdir(parents=True)

    print("Installing dependencies for manylinux2014_x86_64 / cp313 ...")
    install_dependencies(STAGE_DIR)

    before = directory_size(STAGE_DIR)
    print(f"Pruning unused packages: {', '.join(UNUSED_TRANSITIVE)} ...")
    prune_excluded(STAGE_DIR, UNUSED_TRANSITIVE)
    print(f"Setting aside runtime-provided packages: {', '.join(RUNTIME_PROVIDED)} ...")
    prune_excluded(STAGE_DIR, RUNTIME_PROVIDED, move_to=RUNTIME_DIR)
    after_prune = directory_size(STAGE_DIR)
    print(f"  {before / 1024 / 1024:.1f} MB -> {after_prune / 1024 / 1024:.1f} MB")

    print(f"Copying application code: {', '.join(INCLUDE_DIRS)} ...")
    copy_source(STAGE_DIR)

    total = directory_size(STAGE_DIR)
    print(
        f"Unzipped package size: {total / 1024 / 1024:.1f} MB "
        f"(budget {MAX_UNZIPPED_BYTES / 1024 / 1024:.0f} MB)"
    )
    if total > MAX_UNZIPPED_BYTES:
        raise SystemExit(
            f"Package is {total / 1024 / 1024:.1f} MB, over the "
            f"{MAX_UNZIPPED_BYTES / 1024 / 1024:.0f} MB budget. This is the "
            "number that justified zip-over-container — see design.md §8. "
            "Containerising forfeits SnapStart and is not a fallback."
        )

    print(f"Writing {ZIP_PATH} ...")
    make_zip(STAGE_DIR, ZIP_PATH)
    print(f"  {ZIP_PATH.stat().st_size / 1024 / 1024:.1f} MB zipped")

    print("Verifying the packaged archive imports ...")
    verify_import(ZIP_PATH, RUNTIME_DIR)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
