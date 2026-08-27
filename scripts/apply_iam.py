"""
Create or update the orchestrator's execution role from config/iam-orchestrator-role.json.

Why this exists: the config file said "Apply with scripts/apply_iam.py" before
the script did, and the policy was hand-applied through the CLI twice. A policy
applied by hand is a policy that drifts from the file that claims to describe
it, and the drift is invisible until a permission is missing at runtime -- which
is how `dynamodb:Scan` came to be absent while every price check passed and
every meal plan failed.

Idempotent: re-running against an existing role updates the trust policy and
replaces the inline policy rather than failing.

    python scripts/apply_iam.py --dry-run
    python scripts/apply_iam.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

CONFIG = Path(__file__).resolve().parent.parent / "config" / "iam-orchestrator-role.json"


def strip_comments(obj: Any) -> Any:
    """Drop the `_`-prefixed annotation keys; IAM rejects unknown members."""
    if isinstance(obj, dict):
        return {k: strip_comments(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [strip_comments(i) for i in obj]
    return obj


def summarise(cfg: dict) -> None:
    inline = strip_comments(cfg["inline_policy"])
    print(f"Config valid: role {cfg['role_name']}")
    print(f"  region      {cfg['region']}")
    print(f"  inline      {cfg['inline_policy_name']}")
    for managed in cfg.get("managed_policy_arns", []):
        print(f"  managed     {managed}")
    for stmt in inline["Statement"]:
        resources = stmt["Resource"]
        count = len(resources) if isinstance(resources, list) else 1
        actions = ", ".join(
            stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
        )
        print(f"  statement   {stmt['Sid']}: {actions}  ({count} resource(s))")


def apply(cfg: dict) -> None:
    iam = boto3.client("iam")
    role = cfg["role_name"]
    trust = json.dumps(strip_comments(cfg["trust_policy"]))

    try:
        iam.create_role(
            RoleName=role,
            AssumeRolePolicyDocument=trust,
            Description=cfg.get("description", ""),
        )
        print(f"Role    {role} (created)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        iam.update_assume_role_policy(RoleName=role, PolicyDocument=trust)
        print(f"Role    {role} (exists, trust policy updated)")

    for managed in cfg.get("managed_policy_arns", []):
        iam.attach_role_policy(RoleName=role, PolicyArn=managed)
        print(f"Managed {managed}")

    # put_role_policy REPLACES the named policy wholesale, which is what makes
    # the file authoritative: a statement deleted here is deleted in the account.
    iam.put_role_policy(
        RoleName=role,
        PolicyName=cfg["inline_policy_name"],
        PolicyDocument=json.dumps(strip_comments(cfg["inline_policy"])),
    )
    print(f"Inline  {cfg['inline_policy_name']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", default=str(CONFIG))
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    summarise(cfg)

    if args.dry_run:
        print("\nDry run - no AWS calls made.")
        return 0

    print()
    apply(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
