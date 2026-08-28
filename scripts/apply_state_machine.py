"""
Create or update the ingestion state machine from config/ingestion-state-machine.json.

Exists for the same reason scripts/apply_iam.py does: the definition was applied
by hand through the CLI, and a definition applied by hand drifts from the file
that claims to describe it. The `Catch`/`ResultPath` defect in the first version
was invisible precisely because nothing re-derived the deployed definition from
the file.

Idempotent: updates an existing state machine rather than failing.

    python scripts/apply_state_machine.py --dry-run
    python scripts/apply_state_machine.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aws_placeholders import (
    assert_resolved,
    current_account_id,
    substitute,
)

CONFIG = Path(__file__).resolve().parent.parent / "config" / "ingestion-state-machine.json"
NAME = "grocery-ingestion-dev"
ROLE = "grocery-ingestion-sfn-dev-role"
REGION = "ap-southeast-2"


def strip_comments(obj: Any) -> Any:
    """
    Drop annotation keys.

    Amazon States Language allows `Comment` on states, but not the `_comment`
    convention this repo uses elsewhere, and a rejected definition names the
    offending member rather than the reason. Both are stripped so the file can
    explain itself without constraining what the service accepts.
    """
    if isinstance(obj, dict):
        return {k: strip_comments(v) for k, v in obj.items() if k not in ("Comment", "_comment")}
    if isinstance(obj, list):
        return [strip_comments(i) for i in obj]
    return obj


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", default=str(CONFIG))
    args = ap.parse_args()

    raw = json.loads(Path(args.config).read_text(encoding="utf-8"))
    definition = strip_comments(raw)

    states = definition["States"]["RefreshAllRetailers"]
    print(f"Config valid: {NAME}")
    print(
        f"  type        {states['Type']}, mode {states['ItemProcessor']['ProcessorConfig']['Mode']}"
    )
    print(f"  concurrency {states.get('MaxConcurrency')}")
    print(f"  states      {', '.join(states['ItemProcessor']['States'])}")

    if args.dry_run:
        print("\nDry run - no AWS calls made.")
        return 0

    account = current_account_id()
    definition = substitute(definition, account_id=account, region=REGION)
    assert_resolved(definition, NAME)

    sfn = boto3.client("stepfunctions", region_name=REGION)
    arn = f"arn:aws:states:{REGION}:{account}:stateMachine:{NAME}"
    role_arn = f"arn:aws:iam::{account}:role/{ROLE}"
    body = json.dumps(definition)

    existing = {sm["name"] for sm in sfn.list_state_machines().get("stateMachines", [])}
    if NAME in existing:
        sfn.update_state_machine(stateMachineArn=arn, definition=body, roleArn=role_arn)
        print(f"\nState machine {NAME} (updated)")
    else:
        sfn.create_state_machine(name=NAME, definition=body, roleArn=role_arn, type="STANDARD")
        print(f"\nState machine {NAME} (created)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
