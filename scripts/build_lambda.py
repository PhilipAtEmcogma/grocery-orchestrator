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
ZIP_PATH = BUILD_DIR / "lambda.zip"
REQUIREMENTS = ROOT / "requirements.txt"

# Application code copied into the archive verbatim. Deliberately an
# allowlist rather than "everything except tests/evals/scripts/.kiro": the
# repo also holds infra/, ingestion/ and top-level docs the handler never
# imports, which have no business inflating a Lambda archive. The effect is
# the same either way — tests/, evals/, scripts/ and .kiro/ never appear in
# the zip — but an allowlist can't accidentally sweep in something new added
# to the repo root later.
INCLUDE_DIRS = ["src", "config", "fixtures"]

# Installed but never imported anywhere in src/, dead weight either way.
# numpy and zstandard are transitive pulls from langchain-aws and langsmith
# respectively. jmespath and s3transfer are boto3-only dependencies that
# become moot the moment boto3 itself is excluded below — nothing of ours
# reaches for them directly, and the runtime's own boto3 brings its own
# copies. verify_unused() checks this claim against src/ directly rather
# than trusting the design doc.
UNUSED_TRANSITIVE = ["numpy", "zstandard", "jmespath", "s3transfer"]

# The opposite case: imported (src/models/bedrock.py uses both directly) but
# never bundled, because the Lambda Python runtime already ships a boto3 and
# botocore of its own in /var/runtime — including their own jmespath and
# s3transfer. Bundling ours too would only be justified by a specific
# Bedrock feature ahead of the runtime's version — see the dependency rules
# in tech.md.
RUNTIME_PROVIDED = ["boto3", "botocore"]

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


def prune_excluded(target: Path, names: list[str]) -> None:
    for script_dir in GENERATED_SCRIPT_DIRS:
        shutil.rmtree(target / script_dir, ignore_errors=True)

    for entry in sorted(target.iterdir()):
        matches = any(
            entry.name == name
            or entry.name.startswith(f"{name}-")
            or entry.name.startswith(f"{name}.")
            for name in names
        )
        if matches:
            if entry.is_dir():
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


def verify_import(zip_path: Path) -> None:
    """Unzip to a scratch dir and confirm the handler actually resolves — a
    zip that builds cleanly but can't be imported fails at cold start in
    production instead of here.

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

        result = subprocess.run(
            [sys.executable, "-c", "from src.handler import lambda_handler"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            raise SystemExit("Packaged archive does not import. See output above.")

        print("OK: from src.handler import lambda_handler resolves.")


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
    print(f"Pruning excluded packages: {', '.join(EXCLUDED_PACKAGES)} ...")
    prune_excluded(STAGE_DIR, EXCLUDED_PACKAGES)
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
    verify_import(ZIP_PATH)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
