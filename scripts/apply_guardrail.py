"""
Create or update the Bedrock Guardrail from config/guardrail.json.

The config file is the source of truth, not the console. That makes the
security policy reviewable in a pull request, diffable over time, and
reproducible in another account — none of which is true of clicking through
a console.

    python scripts/apply_guardrail.py --dry-run     # validate, no AWS call
    python scripts/apply_guardrail.py               # create or update
    python scripts/apply_guardrail.py --publish     # cut a numbered version

Publishing matters: DRAFT changes under you. Pin a numbered version in
BEDROCK_GUARDRAIL_VERSION for anything you care about reproducing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CONFIG = Path(__file__).resolve().parent.parent / "config" / "guardrail.json"
REGION = "ap-southeast-2"


def load_config() -> dict:
    raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    # Strip our annotations; the API rejects unknown keys.
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def build_request(cfg: dict) -> dict:
    def clean(obj):
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items() if not k.startswith("_")}
        if isinstance(obj, list):
            return [clean(x) for x in obj]
        return obj

    return {
        "name": cfg["name"],
        "description": cfg["description"],
        "blockedInputMessaging": cfg["blockedInputMessaging"],
        "blockedOutputsMessaging": cfg["blockedOutputsMessaging"],
        "contentPolicyConfig": clean(cfg["contentPolicyConfig"]),
        "topicPolicyConfig": clean(cfg["topicPolicyConfig"]),
        "wordPolicyConfig": clean(cfg["wordPolicyConfig"]),
        "sensitiveInformationPolicyConfig": clean(
            cfg["sensitiveInformationPolicyConfig"]
        ),
        "tags": cfg.get("tags", []),
    }


def validate(request: dict) -> list[str]:
    """Catch the mistakes that produce a guardrail which silently does nothing."""
    problems: list[str] = []

    filters = {
        f["type"]: f for f in request["contentPolicyConfig"]["filtersConfig"]
    }
    if "PROMPT_ATTACK" not in filters:
        problems.append("PROMPT_ATTACK filter missing")
    elif filters["PROMPT_ATTACK"]["inputStrength"] != "HIGH":
        problems.append("PROMPT_ATTACK inputStrength is not HIGH")

    for topic in request["topicPolicyConfig"]["topicsConfig"]:
        if len(topic.get("examples", [])) < 3:
            problems.append(f"topic '{topic['name']}' has fewer than 3 examples")
        if topic["type"] != "DENY":
            problems.append(f"topic '{topic['name']}' is not DENY")

    for key in ("blockedInputMessaging", "blockedOutputsMessaging"):
        if len(request[key]) < 20:
            problems.append(f"{key} is too short to be useful to a user")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--region", default=REGION)
    args = parser.parse_args()

    cfg = load_config()
    request = build_request(cfg)

    problems = validate(request)
    if problems:
        print("Validation failed:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"Config valid: {request['name']}")
    print(f"  {len(request['contentPolicyConfig']['filtersConfig'])} content filters")
    print(f"  {len(request['topicPolicyConfig']['topicsConfig'])} denied topics")
    print(
        f"  {len(request['sensitiveInformationPolicyConfig']['piiEntitiesConfig'])}"
        f" PII rules"
    )

    if args.dry_run:
        print("\nDry run - no AWS calls made.")
        return 0

    import boto3
    from botocore.exceptions import ClientError

    client = boto3.client("bedrock", region_name=args.region)

    existing = None
    for g in client.list_guardrails().get("guardrails", []):
        if g["name"] == request["name"]:
            existing = g["id"]
            break

    try:
        if existing:
            client.update_guardrail(guardrailIdentifier=existing, **request)
            guardrail_id = existing
            print(f"\nUpdated guardrail {guardrail_id}")
        else:
            response = client.create_guardrail(**request)
            guardrail_id = response["guardrailId"]
            print(f"\nCreated guardrail {guardrail_id}")

        if args.publish:
            version = client.create_guardrail_version(
                guardrailIdentifier=guardrail_id,
                description=f"From config/guardrail.json v{cfg['version']}",
            )["version"]
            print(f"Published version {version}")
            print(f"\n  BEDROCK_GUARDRAIL_ID={guardrail_id}")
            print(f"  BEDROCK_GUARDRAIL_VERSION={version}")
        else:
            print(f"\n  BEDROCK_GUARDRAIL_ID={guardrail_id}")
            print("  BEDROCK_GUARDRAIL_VERSION=DRAFT")
            print("\nDRAFT changes under you. Use --publish for anything you")
            print("need to reproduce later.")

    except ClientError as exc:
        print(f"\nAWS call failed: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
