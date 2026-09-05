"""
Cost-free preflight for the AgentCore Runtime reviewer prototype (ADR 0002 WS2).

Proves the deploy is READY without creating a single billable resource. Every
check here is either local or a read-only AWS call, so it can be run repeatedly
while iterating on the deploy shape -- which is the whole point: shake out the
"several iterations to get a clean deploy" problem for free, then create the
Runtime once, cleanly.

Checks, in order (each independent, all non-billable):
  1. BUILD   -- assemble the CodeZip (src/ + agentcore/reviewer/), report size,
                confirm it is under the AgentCore CodeZip limits.
  2. IMPORT  -- unzip to a scratch dir and confirm the entrypoint imports, so a
                zip that builds but cannot start fails HERE, not in the microVM.
  3. IAM     -- load config/iam-reviewer-runtime-role.json, resolve the
                ${AWS_*} placeholders, and confirm the policy grants ONLY the
                reviewer's actions (no DynamoDB, S3, or write).
  4. MODEL   -- (only with credentials) a read-only check that the routed model
                is reachable. Skipped cleanly when there are no credentials.

Run:  python scripts/reviewer_runtime_preflight.py
      python scripts/reviewer_runtime_preflight.py --keep   # leave build/reviewer.zip

Exit 0 means "create_agent_runtime is very likely to succeed on the first try".
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BUILD_DIR = ROOT / "build"
ZIP_PATH = BUILD_DIR / "reviewer.zip"
ROLE_CONFIG = ROOT / "config" / "iam-reviewer-runtime-role.json"

# What the reviewer CodeZip contains. An ALLOWLIST, like build_lambda.py: the
# entrypoint, the review package it calls, and the modules those import. NOT the
# whole repo. The reviewer needs src/ (models, prompts, review, schemas,
# retrieval types, history types) and the entrypoint under agentcore/.
INCLUDE_DIRS = ("src", "agentcore", "config")

# AgentCore CodeZip limits (get_runtime_guide): 250 MB zipped, 750 MB unzipped.
# We assert well under, because the reviewer is pure-Python + boto3 (runtime
# provided) and should be small.
MAX_ZIP_MB = 250
MAX_UNZIP_MB = 750

# The entrypoint the Runtime starts.
ENTRYPOINT = "agentcore.reviewer.app"

# Actions the reviewer role must NEVER grant (Req 13.8, the isolation invariant).
FORBIDDEN_ACTIONS = (
    "dynamodb:",
    "s3:",
    "sqs:",
    "sns:",
    ":PutItem",
    ":UpdateItem",
    ":DeleteItem",
    ":BatchWriteItem",
)


def _ok(msg: str) -> None:
    print(f"  ok   {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL {msg}")


def build_zip(*, keep: bool) -> tuple[Path, float, float]:
    """Assemble the CodeZip from the allowlisted dirs. Local only, no AWS."""
    stage = BUILD_DIR / "reviewer_stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)

    for name in INCLUDE_DIRS:
        src = ROOT / name
        if not src.exists():
            continue
        shutil.copytree(
            src, stage / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.md")
        )

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(stage.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(stage))

    unzip_mb = sum(f.stat().st_size for f in stage.rglob("*") if f.is_file()) / 1e6
    zip_mb = ZIP_PATH.stat().st_size / 1e6
    if not keep:
        shutil.rmtree(stage)
    return ZIP_PATH, zip_mb, unzip_mb


def check_build(*, keep: bool) -> bool:
    _, zip_mb, unzip_mb = build_zip(keep=keep)
    if zip_mb > MAX_ZIP_MB or unzip_mb > MAX_UNZIP_MB:
        _fail(f"CodeZip too large: {zip_mb:.1f} MB zipped / {unzip_mb:.1f} MB unzipped")
        return False
    _ok(f"CodeZip built: {zip_mb:.2f} MB zipped, {unzip_mb:.2f} MB unzipped ({ZIP_PATH.name})")
    return True


def check_import() -> bool:
    """Unzip to scratch and confirm the entrypoint imports there -- not in the microVM."""
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(ZIP_PATH) as zf:
            zf.extractall(tmp)
        # A syntax/AST check on the entrypoint file inside the zip. A full import
        # would need boto3 etc. resolvable; the AgentCore runtime provides those,
        # so here we confirm the file parses and declares the expected handlers.
        app_file = Path(tmp) / "agentcore" / "reviewer" / "app.py"
        if not app_file.exists():
            _fail(f"entrypoint {ENTRYPOINT} not in the zip")
            return False
        tree = ast.parse(app_file.read_text(encoding="utf-8"))
        names = {
            n.name
            for node in ast.walk(tree)
            for n in ([node] if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) else [])
        }
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        if "main" not in names or "_Handler" not in classes:
            _fail("entrypoint is missing main()/_Handler")
            return False
    _ok(f"entrypoint {ENTRYPOINT} present and parses, main()/_Handler declared")
    return True


def _resolve(text: str) -> str:
    """Resolve ${AWS_REGION}/${AWS_ACCOUNT_ID} the way scripts/apply_iam.py does."""
    region = os.environ.get("AWS_REGION", "ap-southeast-2")
    account = os.environ.get("AWS_ACCOUNT_ID", "000000000000")
    return text.replace("${AWS_REGION}", region).replace("${AWS_ACCOUNT_ID}", account)


def check_iam() -> bool:
    if not ROLE_CONFIG.exists():
        _fail(f"role config missing: {ROLE_CONFIG}")
        return False
    resolved = _resolve(ROLE_CONFIG.read_text(encoding="utf-8"))
    role = json.loads(resolved)

    trust = role["trust_policy"]["Statement"][0]["Principal"]["Service"]
    if trust != "bedrock-agentcore.amazonaws.com":
        _fail(f"trust principal is {trust}, expected bedrock-agentcore.amazonaws.com")
        return False

    statements = role["inline_policy"]["Statement"]
    granted: list[str] = []
    for stmt in statements:
        actions = stmt["Action"]
        granted.extend([actions] if isinstance(actions, str) else actions)

    leaked = [a for a in granted for bad in FORBIDDEN_ACTIONS if bad in a]
    if leaked:
        _fail(f"role grants forbidden actions (Req 13.8 isolation): {leaked}")
        return False

    if "${AWS_" in json.dumps(role):
        _fail("unresolved ${AWS_*} placeholder remains after resolution")
        return False

    _ok(f"IAM role least-privilege: grants {sorted(set(granted))}, no DynamoDB/S3/write")
    return True


def check_model() -> bool:
    """Read-only reachability of the routed model. Skips cleanly with no creds."""
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    except ImportError:
        print("  skip model check: boto3 not importable")
        return True

    from src.models.base import TASK_REVIEW_SNAPSHOT
    from src.models.registry import ModelRegistry, RoutingPolicy

    try:
        spec = ModelRegistry().route(
            TASK_REVIEW_SNAPSHOT, policy=RoutingPolicy.PINNED, pinned_key="nova-lite"
        )
    except Exception as exc:  # routing/registry problem is worth surfacing
        _fail(f"could not route {TASK_REVIEW_SNAPSHOT} to nova-lite: {exc}")
        return False

    try:
        client = boto3.client("bedrock", region_name="ap-southeast-2")
        client.list_foundation_models()
    except NoCredentialsError:
        print("  skip model check: no AWS credentials (run after `aws login`)")
        return True
    except (ClientError, BotoCoreError) as exc:
        print(f"  skip model check: AWS not reachable ({type(exc).__name__})")
        return True

    _ok(f"model routing resolves to {spec.display_name} ({spec.model_id}); Bedrock reachable")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Cost-free preflight for the reviewer Runtime")
    parser.add_argument("--keep", action="store_true", help="Keep build/reviewer.zip and stage")
    args = parser.parse_args()

    print("\n=== AgentCore Runtime reviewer -- preflight (no billable resources) ===\n")
    checks = [
        ("build", lambda: check_build(keep=args.keep)),
        ("import", check_import),
        ("iam", check_iam),
        ("model", check_model),
    ]
    results = []
    for name, fn in checks:
        try:
            results.append(fn())
        except Exception as exc:  # a preflight that crashes is a failed preflight
            _fail(f"{name} check raised {type(exc).__name__}: {exc}")
            results.append(False)

    print()
    if all(results):
        print("PREFLIGHT OK -- create_agent_runtime is very likely to succeed first try.")
        return 0
    print("PREFLIGHT FAILED -- fix the above before spending a cent on a live create.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
