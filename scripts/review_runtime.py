"""
Invoke-side client for the AgentCore Runtime reviewer (ADR 0002 WS2, Task 7).

This is the CALLER's half of Option A (`docs/AGENTCORE-RUNTIME-REVIEWER.md` §3):
it builds a sanitised snapshot, sends it to the Runtime, and runs the
deterministic `validate_report` on the RAW findings the Runtime returns --
outside the Runtime, against the snapshot it holds. A compromised Runtime can
only return claims; every claim is checked here before anyone sees it.

TWO TRANSPORTS, ONE VALIDATION PATH:
  * `--local`  runs the reviewer in-process (a Bedrock or scripted ModelClient),
               so the whole invoke/validate path is exercised with no Runtime.
               This is what the tests use, and what proves the wiring offline.
  * `--arn ARN` invokes the deployed Runtime via bedrock-agentcore. This is the
               live path, used by the prototype in Task 7.

The snapshot source is the labelled dataset (`evals/cases/review_anomalies.json`)
so a live run is measurable against ground truth -- reviewer-only recall and
false positives, exactly as `evals/run_review.py` scores the offline baseline.

    python scripts/review_runtime.py --local                    # in-process, scripted
    python scripts/review_runtime.py --local --model nova-lite  # in-process, live model
    python scripts/review_runtime.py --arn arn:aws:...:runtime/reviewer-xxxx
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.prompts.review import ReviewReport  # noqa: E402
from src.review import (  # noqa: E402
    ReviewSnapshot,
    SnapshotRow,
    snapshot_to_dicts,
    validate_report,
)

REGION = "ap-southeast-2"
CASES = ROOT / "evals" / "cases" / "review_anomalies.json"


@dataclass(frozen=True)
class Invocation:
    """One reviewed snapshot: what went in, the raw findings, the validated result."""

    snapshot: ReviewSnapshot
    report: ReviewReport
    latency_ms: int


def _snapshot_from_cases(payload: dict) -> ReviewSnapshot:
    """
    The labelled dataset -> one snapshot of all its rows.

    All cases in one snapshot so the Runtime sees the catalogue slice a real
    review would, and so a finding can only validate if it cites a row that was
    actually sent -- the whole dataset is the universe for the run.
    """
    rows = tuple(SnapshotRow(**case["row"]) for case in payload["cases"])
    return ReviewSnapshot(rows=rows, captured_from="grocery-products-dev")


def _invoke_local(snapshot: ReviewSnapshot, *, model) -> Invocation:
    """In-process: run the model half here, no Runtime. Same code the microVM runs."""
    from src.review.reviewer import propose_findings

    rows = snapshot_to_dicts(snapshot)
    started = time.perf_counter()
    report = propose_findings(rows, table_name=snapshot.captured_from, model=model)
    elapsed = int((time.perf_counter() - started) * 1000)
    return Invocation(snapshot=snapshot, report=report, latency_ms=elapsed)


def _invoke_runtime(snapshot: ReviewSnapshot, *, arn: str, session_id: str) -> Invocation:
    """Live: send the sanitised rows to the deployed Runtime, get raw findings back."""
    import boto3

    client = boto3.client("bedrock-agentcore", region_name=REGION)
    payload = json.dumps(
        {"table_name": snapshot.captured_from, "rows": snapshot_to_dicts(snapshot)}
    ).encode("utf-8")

    started = time.perf_counter()
    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session_id,
        payload=payload,
    )
    elapsed = int((time.perf_counter() - started) * 1000)

    body = response["response"].read()
    data = json.loads(body)
    report = ReviewReport.model_validate({"findings": data.get("findings", [])})
    return Invocation(snapshot=snapshot, report=report, latency_ms=elapsed)


def _score(payload: dict, invocation: Invocation) -> dict:
    """
    Reviewer-only recall + false positives, the same discipline as run_review.py.

    Validation runs HERE (our side). Code-caught cases earn no credit; clean
    rows flagged are false positives; reviewer_only cases accepted are the
    hypothesis.
    """
    result = validate_report(invocation.report, invocation.snapshot)
    accepted_by_ref = {(f.store_key, f.product_key): f for f in result.accepted}

    reviewer_only = [c for c in payload["cases"] if c["detectability"] == "reviewer_only"]
    clean = [c for c in payload["cases"] if c["detectability"] == "clean"]

    def caught(case: dict) -> bool:
        ref = (case["row"]["store_key"], case["row"]["product_key"])
        f = accepted_by_ref.get(ref)
        return f is not None and f.kind.value == case["anomaly_kind"]

    recall_hits = sum(1 for c in reviewer_only if caught(c))
    fps = [c for c in clean if (c["row"]["store_key"], c["row"]["product_key"]) in accepted_by_ref]

    return {
        "reviewer_only_recall": recall_hits / len(reviewer_only) if reviewer_only else 0.0,
        "reviewer_only": f"{recall_hits}/{len(reviewer_only)}",
        "false_positives": f"{len(fps)}/{len(clean)}",
        "accepted": result.validated.accepted_count,
        "rejected": len(result.validated.rejected),
        "fabrication_rate": result.validated.fabrication_rate,
        "latency_ms": invocation.latency_ms,
    }


def _report(score: dict) -> None:
    print("\n=== AgentCore Runtime reviewer ===")
    recall = score["reviewer_only_recall"]
    print(f"  reviewer-only recall  {recall:.1%}  ({score['reviewer_only']})")
    print(f"  false positives       {score['false_positives']} clean rows flagged")
    print(f"  accepted / rejected   {score['accepted']} / {score['rejected']}")
    print(f"  fabrication rate      {score['fabrication_rate']:.1%}")
    print(f"  latency               {score['latency_ms']} ms")


def main() -> int:
    parser = argparse.ArgumentParser(description="Invoke the reviewer (local or live Runtime)")
    parser.add_argument("--local", action="store_true", help="Run in-process, no Runtime")
    parser.add_argument("--model", help="Model key for --local live run (else scripted)")
    parser.add_argument("--arn", help="Deployed Runtime ARN for the live path")
    parser.add_argument("--session-id", default="reviewer-" + "0" * 27)
    args = parser.parse_args()

    payload = json.loads(CASES.read_text(encoding="utf-8"))
    snapshot = _snapshot_from_cases(payload)

    if args.arn:
        invocation = _invoke_runtime(snapshot, arn=args.arn, session_id=args.session_id)
    elif args.local:
        if args.model:
            import os

            os.environ["USE_BEDROCK"] = "1"
            from src.models.base import TASK_REVIEW_SNAPSHOT
            from src.models.bedrock import BedrockModelClient
            from src.models.registry import ModelRegistry, RoutingPolicy

            spec = ModelRegistry().route(
                TASK_REVIEW_SNAPSHOT, policy=RoutingPolicy.PINNED, pinned_key=args.model
            )
            model = BedrockModelClient(pinned_spec=spec)
        else:
            from evals.run_review import _ScriptedReviewer

            model = _ScriptedReviewer()
        invocation = _invoke_local(snapshot, model=model)
    else:
        parser.error("choose --local or --arn")

    _report(_score(payload, invocation))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
