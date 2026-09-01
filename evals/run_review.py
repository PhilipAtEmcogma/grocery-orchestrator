"""
Data-quality reviewer eval (ADR 0002 Workstream 2, Pilot Task 14).

THIS IS AN EXPERIMENT, and the eval is what makes it one. The reviewer earns its
place only if it catches anomalies the deterministic rules structurally cannot,
so this runner measures exactly that and refuses to credit anything else:

  * REVIEWER-ONLY RECALL is the headline. Of the cases labelled `reviewer_only`
    in the dataset -- a price far from its own history, a meat under produce, a
    mislabelled key, none of which `implausible_unit_price` can see -- how many
    did the reviewer report AND survive validation? This is the hypothesis.

  * CODE-CAUGHT cases earn NO credit. They are reported for context (a good
    reviewer will also flag them), but the deterministic rules already catch
    them, so catching them again is not what justifies a model. Counting them
    would let a reviewer that only re-finds arithmetic errors look valuable.

  * FALSE POSITIVES on clean rows are scored against the reviewer, because a
    reviewer that flags everything has the recall of one that flags nothing and
    the cost of a human checking noise. A clean row reported is a failure.

  * FABRICATION is measured, not scored separately, because it cannot happen
    silently: `validate_findings` rejects any finding citing a row outside the
    snapshot or quoting a value the row does not have. A high fabrication rate
    is a model problem the validator already contains; it is surfaced so a live
    run shows how hard the model leaned on the guardrail.

WHY A SCRIPTED BASELINE. With no `--model`, this runs a deterministic stand-in
reviewer that reasons over the baseline, category and name fields the way the
prompt asks a model to. It measures the DATASET and the WIRING -- that the
labels are catchable at all, that the snapshot carries what a reviewer needs,
that validation passes a true finding and rejects a false one -- not a model's
judgement. A live run (`--model nova-lite`) measures the model against the same
floor. Neither run is a route qualification: the reviewer is off the shopper
path and gated separately (ADR 0002).

    python evals/run_review.py                    # scripted baseline (no AWS)
    python evals/run_review.py --model nova-lite  # live, per-model
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models.base import ModelClient, ModelError, ModelTier, T
from src.prompts.review import ReviewFinding, ReviewReport
from src.review import (
    ReviewSnapshot,
    SnapshotRow,
    review_snapshot,
)

CASES = Path(__file__).parent / "cases" / "review_anomalies.json"

#: A price this many times its baseline (or this fraction of it) is worth a
#: finding. The scripted reviewer's thresholds only; a live model reasons for
#: itself. Deliberately loose enough to leave rev-005 (2.6x) near the boundary,
#: which is where a judgement case belongs.
_DEVIATION_HIGH = Decimal("2.5")
_DEVIATION_LOW = Decimal("0.4")

#: Categories a canonical name should not fall under. Tiny on purpose -- the
#: point is to exercise suspect_category, not to ship a taxonomy.
_MEAT_WORDS = ("beef", "chicken", "pork", "lamb", "sausage", "mince", "bacon")


@dataclass
class CaseResult:
    case_id: str
    detectability: str
    label: str
    expected_kind: str | None
    reported: bool
    accepted: bool
    fabricated: bool = False
    ran: bool = True


@dataclass
class Scorecard:
    model_label: str
    results: list[CaseResult] = field(default_factory=list)

    def _subset(self, detectability: str) -> list[CaseResult]:
        return [r for r in self.results if r.detectability == detectability]

    @property
    def reviewer_only(self) -> list[CaseResult]:
        return self._subset("reviewer_only")

    @property
    def clean(self) -> list[CaseResult]:
        return self._subset("clean")

    @property
    def code_caught(self) -> list[CaseResult]:
        return self._subset("caught_by_code")

    @property
    def reviewer_only_recall(self) -> float:
        subset = self.reviewer_only
        if not subset:
            return 0.0
        return sum(1 for r in subset if r.accepted) / len(subset)

    @property
    def false_positive_rate(self) -> float:
        subset = self.clean
        if not subset:
            return 0.0
        return sum(1 for r in subset if r.reported) / len(subset)

    @property
    def any_upstream(self) -> bool:
        return any(not r.ran for r in self.results)


class _ScriptedReviewer(ModelClient):
    """
    A deterministic stand-in reviewer, for the offline baseline.

    Reasons over the enriched row the same way the prompt asks a model to:
    baseline deviation, meat-under-produce, key-vs-name mismatch. It reads the
    SAME rows the model would (parsed back out of the delimited prompt), so it
    exercises the whole path -- prompt build, finding shape, validation -- not a
    shortcut around it. It is not a good reviewer; it is a floor.
    """

    def __init__(self) -> None:
        self._usage: dict = {}

    @property
    def last_usage(self) -> dict:
        return dict(self._usage)

    def text(self, **kwargs) -> str:  # pragma: no cover - reviewer never calls text
        raise ModelError("scripted reviewer has no text path")

    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        tier: ModelTier,
        max_tokens: int = 1024,
        task: str = "classify_intent",
    ) -> T:
        from typing import cast

        if schema is not ReviewReport:
            raise ModelError(f"scripted reviewer has no script for {schema.__name__}")

        rows = self._rows_from_prompt(user)
        findings = [f for row in rows for f in self._review_row(row)]
        self._usage = {"model_ids": ["scripted-reviewer"], "latency_ms": 1}
        return cast(T, ReviewReport(findings=findings))

    @staticmethod
    def _rows_from_prompt(user: str) -> list[dict]:
        start = user.find("<<<")
        end = user.rfind(">>>")
        if start == -1 or end == -1:
            return []
        body = user[start + 3 : end].strip()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return []

    def _review_row(self, row: dict) -> list[ReviewFinding]:
        out: list[ReviewFinding] = []
        sk, pk = row.get("store_key", ""), row.get("product_key", "")

        # price_deviation: only when there IS a baseline, and never for a
        # genuine special below baseline.
        ratio_raw = row.get("deviation_ratio", "")
        if ratio_raw:
            try:
                ratio = Decimal(ratio_raw)
            except InvalidOperation:
                ratio = None
            if ratio is not None:
                on_special = row.get("on_special", False)
                if ratio >= _DEVIATION_HIGH or (ratio <= _DEVIATION_LOW and not on_special):
                    out.append(
                        ReviewFinding(
                            store_key=sk,
                            product_key=pk,
                            kind="price_deviation",
                            observation="price is far from the product's own recent history",
                            quoted={
                                "price_nzd": row.get("price_nzd", ""),
                                "baseline_avg_nzd": row.get("baseline_avg_nzd", ""),
                                "deviation_ratio": ratio_raw,
                            },
                        )
                    )

        # suspect_category: a meat-named product filed under produce.
        name = (row.get("canonical_name", "") + " " + row.get("display_name", "")).lower()
        category = row.get("category", "").lower()
        flagged_category = category == "produce" and any(w in name for w in _MEAT_WORDS)
        if flagged_category:
            out.append(
                ReviewFinding(
                    store_key=sk,
                    product_key=pk,
                    kind="suspect_category",
                    observation="a meat product appears to be filed under produce",
                    quoted={
                        "canonical_name": row.get("canonical_name", ""),
                        "category": row.get("category", ""),
                    },
                )
            )

        # name_mismatch: the product_key names a product the display name does
        # not. Crude: the key's head word absent from the display name. Skipped
        # when suspect_category already fired on this row, to avoid two findings
        # for one underlying defect (rev-003 is both a meat and mis-keyed).
        key_head = pk.split("-")[0]
        display = row.get("display_name", "").lower()
        if key_head and len(key_head) > 2 and key_head not in display and not flagged_category:
            out.append(
                ReviewFinding(
                    store_key=sk,
                    product_key=pk,
                    kind="name_mismatch",
                    observation="the product key and the display name disagree about the product",
                    quoted={
                        "product_key": pk,
                        "display_name": row.get("display_name", ""),
                    },
                )
            )
        return out


def _snapshot_for(case: dict) -> ReviewSnapshot:
    """One case's row -> a one-row snapshot the reviewer can see."""
    row = case["row"]
    fields = {k: row[k] for k in row}
    return ReviewSnapshot(rows=(SnapshotRow(**fields),), captured_from="grocery-products-dev")


def run(model: ModelClient, label: str) -> Scorecard:
    payload = json.loads(CASES.read_text(encoding="utf-8"))
    card = Scorecard(model_label=label)

    for case in payload["cases"]:
        snapshot = _snapshot_for(case)
        result = review_snapshot(snapshot, model=model)
        accepted = result.validated.accepted
        expected_kind = case["anomaly_kind"]

        reported = bool(result.validated.accepted or result.validated.rejected)
        matched = any(f.kind.value == expected_kind for f in accepted) if expected_kind else False

        card.results.append(
            CaseResult(
                case_id=case["id"],
                detectability=case["detectability"],
                label=case["label"],
                expected_kind=expected_kind,
                reported=reported,
                accepted=matched,
                fabricated=bool(result.validated.rejected),
                ran=result.ran,
            )
        )
    return card


def report(card: Scorecard) -> None:
    print(f"\n=== {card.model_label} ===")
    print(
        f"  reviewer-only recall  {card.reviewer_only_recall:.1%}  "
        f"({sum(1 for r in card.reviewer_only if r.accepted)}/{len(card.reviewer_only)})"
        "   <- the hypothesis"
    )
    print(
        f"  false positives       {card.false_positive_rate:.1%}  "
        f"({sum(1 for r in card.clean if r.reported)}/{len(card.clean)} clean rows flagged)"
    )
    code = card.code_caught
    print(
        f"  code-caught (context) {sum(1 for r in code if r.accepted)}/{len(code)}"
        "   (no credit -- the rules already catch these)"
    )

    misses = [r for r in card.reviewer_only if not r.accepted]
    if misses:
        print(f"\n  reviewer-only misses ({len(misses)}):")
        for r in misses:
            print(f"    {r.case_id}: expected {r.expected_kind}, not reported/validated")
    fps = [r for r in card.clean if r.reported]
    if fps:
        print(f"\n  false positives ({len(fps)}):")
        for r in fps:
            print(f"    {r.case_id}: clean row was flagged")


def _gate(card: Scorecard, floor: float | None) -> int:
    if card.any_upstream:
        print(
            "\nINCONCLUSIVE: the model never answered on at least one case, "
            "so this is not a measurement of the reviewer.",
            file=sys.stderr,
        )
        return 2
    if floor is None:
        return 0
    if card.reviewer_only_recall < floor:
        print(
            f"\nFAIL: reviewer-only recall {card.reviewer_only_recall:.1%} "
            f"is below the floor of {floor:.1%}"
        )
        return 1
    print(
        f"\nOK: reviewer-only recall {card.reviewer_only_recall:.1%} meets the floor of {floor:.1%}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Data-quality reviewer eval (experiment)")
    parser.add_argument("--model", help="Model key to pin, e.g. nova-lite")
    parser.add_argument("--min-pass-rate", type=float)
    args = parser.parse_args()

    if not args.model:
        card = run(_ScriptedReviewer(), "scripted reviewer (no model call)")
        report(card)
        print(
            "\nBaseline only. The scripted reviewer applies fixed thresholds over the "
            "baseline, category and name fields, so this measures the dataset, the "
            "snapshot enrichment and the validation wiring -- not a model's judgement. "
            "This is an EXPERIMENT (ADR 0002 Workstream 2): the reviewer is off the "
            "shopper path and does not qualify any route."
        )
        return _gate(card, args.min_pass_rate)

    import os

    os.environ["USE_BEDROCK"] = "1"

    from src.models.base import TASK_REVIEW_SNAPSHOT
    from src.models.bedrock import BedrockModelClient
    from src.models.registry import ModelRegistry, ModelSpec, RoutingPolicy

    spec: ModelSpec = ModelRegistry().route(
        TASK_REVIEW_SNAPSHOT, policy=RoutingPolicy.PINNED, pinned_key=args.model
    )
    card = run(BedrockModelClient(pinned_spec=spec), spec.display_name)
    report(card)
    return _gate(card, args.min_pass_rate)


if __name__ == "__main__":
    raise SystemExit(main())
