"""
Build the DEPLOYABLE AgentCore Runtime CodeZip for the reviewer (ADR 0002 WS2).

Distinct from `reviewer_runtime_preflight.py`'s pure-source zip: this one bundles
the arm64 Linux dependency wheels AgentCore Runtime needs, because the runtime
provides NONE of them (docs: "package agent code AND its dependencies"). The
runtime is arm64 Amazon Linux, so wheels must be fetched for that platform even
though we build on Windows -- the same cross-platform wheel trick
`scripts/build_lambda.py` uses for Lambda, with a different target triple.

Layout of the produced zip (all at the ROOT, which the runtime mounts at
/var/task and puts first on sys.path):

    main.py            <- entryPoint; hands off to agentcore.reviewer.app.main
    agentcore/         <- the real HTTP entrypoint
    src/               <- the reviewer's imports (models, prompts, review, ...)
    config/            <- models.json etc. the registry reads
    pydantic/ ...      <- installed arm64 dependency wheels

Dependencies are DELIBERATELY MINIMAL: the entrypoint's transitive imports are
pydantic + boto3 + botocore only (bedrock.py calls the Converse API directly, no
langchain). Bundling less is a smaller attack surface and a faster cold start.

Run:  python scripts/build_reviewer_runtime.py
Out:  build/reviewer-runtime.zip  (arm64, deployable)
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = ROOT / "build"
STAGE = BUILD_DIR / "reviewer_runtime_stage"
ZIP_PATH = BUILD_DIR / "reviewer-runtime.zip"

# App code copied verbatim (allowlist, like build_lambda.py). config/ is here
# because the model registry reads config/models.json at import/route time.
INCLUDE_DIRS = ("src", "agentcore", "config")

# The entrypoint's transitive third-party imports, and NOTHING more. pydantic
# carries pydantic-core (a Rust wheel), which is exactly why the arm64 platform
# pin matters. boto3/botocore/pydantic are what src.models.bedrock + base +
# prompts.review actually import; the rest of src is pure stdlib.
DEPS = ("pydantic", "boto3", "botocore")

# arm64 Amazon Linux, Python 3.13 -- the AgentCore Runtime execution environment.
PLATFORM = "aarch64-manylinux2014"
PY_VERSION = "3.13"

MAX_ZIP_MB = 250
MAX_UNZIP_MB = 750


def _stage_app() -> None:
    for name in INCLUDE_DIRS:
        src = ROOT / name
        if src.exists():
            shutil.copytree(
                src, STAGE / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.md")
            )
    # The root-level entrypoint the platform names in entryPoint=['main.py'].
    shutil.copy2(ROOT / "agentcore" / "reviewer" / "main.py", STAGE / "main.py")


def _install_deps() -> None:
    """Fetch arm64 wheels into the stage root, so /var/task holds the deps."""
    cmd = [
        "uv",
        "pip",
        "install",
        "--python-platform",
        PLATFORM,
        "--python-version",
        PY_VERSION,
        "--target",
        str(STAGE),
        "--only-binary=:all:",
        *DEPS,
    ]
    # Fixed argv from module constants, no shell, no user input -- same posture
    # as scripts/build_lambda.py. `uv` is resolved from PATH (the project's
    # documented build tool), which is what S607 flags; accepted for a dev
    # build script that never runs in the Lambda/Runtime itself.
    subprocess.run(cmd, check=True)  # noqa: S603


def _zip() -> tuple[float, float]:
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(STAGE.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                zf.write(f, f.relative_to(STAGE))
    unzip_mb = sum(f.stat().st_size for f in STAGE.rglob("*") if f.is_file()) / 1e6
    return ZIP_PATH.stat().st_size / 1e6, unzip_mb


def main() -> int:
    print("\n=== Building deployable reviewer Runtime CodeZip (arm64) ===\n")
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True, exist_ok=True)

    _stage_app()
    print("  staged app code (src, agentcore, config, main.py)")
    _install_deps()
    print(f"  installed arm64 deps: {', '.join(DEPS)}")

    zip_mb, unzip_mb = _zip()
    print(f"\n  {ZIP_PATH.name}: {zip_mb:.1f} MB zipped, {unzip_mb:.1f} MB unzipped")
    if zip_mb > MAX_ZIP_MB or unzip_mb > MAX_UNZIP_MB:
        print(f"  FAIL over CodeZip limits ({MAX_ZIP_MB}/{MAX_UNZIP_MB} MB)")
        return 1
    print("  OK under CodeZip limits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
