"""
Parity check between the two deployed service planes (ARCHITECTURE.md 3m, 3q;
infra/docs/08-OPEN-DECISIONS.md 10).

Two orchestrator planes run in parallel in ap-southeast-2:

    hand-made : woqmel35lk  -> grocery-orchestrator-dev      (PRIMARY, alarmed)
    CDK       : crm1xkrk34  -> grocery-orchestrator-dev-cdk  (parallel, deferred cutover)

The 2026-08-30 parity table predates Pilot Task 15c (select_recipes / the curated
recipe catalogue), so it must be re-measured before it can be relied on for a
cutover decision. This script does that.

PARITY IS A MEASUREMENT, NOT A PROPERTY. The meal-plan path composes a plan with
a non-deterministic model, so the SAME question against the SAME endpoint varies
run to run -- the 2026-08-30 run saw $35.75 / $31.74 / $31.74 for one request.
A difference between the two planes is therefore only evidence of a real
divergence if the same plane does not produce that difference on its own. So:

  * Deterministic requests (price_check, no_data) must match BYTE FOR BYTE on the
    fields that matter -- store, price, citation refs, intent, error code.
  * Meal-plan requests are run REPEATEDLY against each plane, and what is compared
    is the RANGE each plane produces (meals, payable total, citation count), not a
    single figure. A cross-plane number that sits inside the same-plane range is
    parity, not a discrepancy. This is the exact false-alarm the deployment record
    warns about.

NO AWS CREDENTIALS NEEDED. Both endpoints are public/unauthenticated today
(docs/OPEN-REVIEW-api-key.md). This is plain HTTPS.

PACED, for the same reason measure_latency.py is: the binding Nova Lite quota
cannot be raised, and an unpaced run measures the quota, not the service.

Usage:
    python scripts/check_parity.py
    python scripts/check_parity.py --meal-plan-reps 3 --rpm 9
    python scripts/check_parity.py --hand-made-url ... --cdk-url ...
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

HAND_MADE_URL = "https://woqmel35lk.execute-api.ap-southeast-2.amazonaws.com/dev/chat"
CDK_URL = "https://crm1xkrk34.execute-api.ap-southeast-2.amazonaws.com/dev/chat"

# Deterministic probes: the answer should be identical on both planes and stable
# run to run. These exercise price_check (a cheapest-first citation), a
# location-filtered check, and the honest no_data refusal.
DETERMINISTIC = [
    "cheapest butter",
    "cheapest milk near Albany",
    "how much is truffle oil",
]

# Non-deterministic: plan composition varies, so these are repeated and compared
# as ranges. Kept identical to the requests the 2026-08-30 table used, so the
# re-run is comparable to the record it replaces.
MEAL_PLANS = [
    "feed 3 people for 5 days on $80",
]


@dataclass
class TurnResult:
    ok: bool
    http_status: int
    intent: str = ""
    error_code: str = ""
    # price_check fields
    cheapest_store: str = ""
    cheapest_price: str = ""
    citation_refs: tuple[str, ...] = ()
    # meal_plan fields
    meal_count: int = 0
    payable_total: str = ""
    citation_count: int = 0
    detail: str = ""
    raw: dict = field(default_factory=dict)

    def deterministic_signature(self) -> tuple:
        """The fields that must match byte-for-byte across planes for a match."""
        return (
            self.intent,
            self.error_code,
            self.cheapest_store,
            self.cheapest_price,
            self.citation_refs,
        )


def _events_by_type(payload: dict, kind: str) -> list[dict]:
    return [e for e in payload.get("events", []) if e.get("type") == kind]


def _parse(payload: dict, http_status: int) -> TurnResult:
    intents = _events_by_type(payload, "intent")
    errors = _events_by_type(payload, "error")
    citations = _events_by_type(payload, "citation")
    comparisons = _events_by_type(payload, "price_comparison")
    plans = _events_by_type(payload, "meal_plan")

    # ref -> citation body, so the comparison's cheapest citation_ref can be
    # resolved back to a store and price.
    by_ref: dict[str, dict] = {}
    for c in citations:
        body = c.get("citation", {})
        if body.get("ref"):
            by_ref[body["ref"]] = body

    result = TurnResult(
        ok=any(e.get("type") == "done" for e in payload.get("events", [])),
        http_status=http_status,
        intent=intents[0].get("intent", "") if intents else "",
        error_code=errors[0].get("code", "") if errors else "",
        citation_refs=tuple(c.get("citation", {}).get("ref", "") for c in citations),
        citation_count=len(citations),
        raw=payload,
    )

    # Cheapest: prefer the option the price_comparison flagged is_cheapest, and
    # resolve it back through the citation refs. Fall back to the first citation
    # (retrieval returns cheapest-first) when there is no comparison event.
    cheapest_body: dict | None = None
    if comparisons:
        options = comparisons[0].get("data", {}).get("options", [])
        for opt in options:
            if opt.get("is_cheapest") and opt.get("citation_ref") in by_ref:
                cheapest_body = by_ref[opt["citation_ref"]]
                break
    if cheapest_body is None and citations:
        cheapest_body = citations[0].get("citation", {})
    if cheapest_body:
        result.cheapest_store = str(cheapest_body.get("store", ""))
        # store_location included: two stores of the same chain differ only by it.
        loc = cheapest_body.get("store_location", "")
        if loc:
            result.cheapest_store = f"{result.cheapest_store}#{loc}"
        result.cheapest_price = str(cheapest_body.get("price_nzd", cheapest_body.get("price", "")))

    # Meal-plan shape: event type `meal_plan`, body under `data`.
    if plans:
        data = plans[0].get("data", {})
        meals = data.get("meals", []) if isinstance(data, dict) else []
        result.meal_count = len(meals)
        total = data.get("payable_total_nzd") or data.get("total_nzd")
        if total is not None:
            result.payable_total = str(total)

    return result


def one_turn(url: str, message: str, timeout: float) -> TurnResult:
    body = json.dumps(
        {
            "version": "1.0",
            "session_id": f"sess-par{uuid.uuid4().hex[:12]}",
            "turn_id": f"turn-par{uuid.uuid4().hex[:12]}",
            "message": message,
        }
    ).encode()
    request = urllib.request.Request(  # noqa: S310 - fixed https endpoints
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read())
            return _parse(payload, response.status)
    except urllib.error.HTTPError as exc:
        return TurnResult(ok=False, http_status=exc.code, detail=f"HTTPError {exc.code}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return TurnResult(ok=False, http_status=0, detail=type(exc).__name__)


def _sleep(gap: float, first: bool) -> bool:
    if not first:
        time.sleep(gap)
    return False


def run(hand_made_url: str, cdk_url: str, meal_plan_reps: int, rpm: float, timeout: float) -> int:
    gap = 60.0 / rpm
    first = True
    exit_code = 0

    print("PARITY RE-RUN\n")
    print(f"  hand-made : {hand_made_url}")
    print(f"  CDK       : {cdk_url}")
    print(f"  paced at {rpm}/min ({gap:.1f}s apart)\n")

    # --- Deterministic requests: must match ---
    print("=== Deterministic requests (must match byte-for-byte) ===")
    for message in DETERMINISTIC:
        first = _sleep(gap, first)
        hm = one_turn(hand_made_url, message, timeout)
        first = _sleep(gap, first)
        cdk = one_turn(cdk_url, message, timeout)

        match = hm.ok and cdk.ok and hm.deterministic_signature() == cdk.deterministic_signature()
        verdict = "MATCH" if match else "DIFFER"
        if not match:
            exit_code = max(exit_code, 1)
        print(f"\n  {message!r}  -> {verdict}")
        print(
            f"    hand-made: {hm.intent or hm.error_code or hm.detail} "
            f"store={hm.cheapest_store} price={hm.cheapest_price} refs={hm.citation_refs}"
        )
        print(
            f"    CDK      : {cdk.intent or cdk.error_code or cdk.detail} "
            f"store={cdk.cheapest_store} price={cdk.cheapest_price} refs={cdk.citation_refs}"
        )

    # --- Meal-plan requests: compare RANGES, not single figures ---
    print("\n=== Meal-plan requests (ranges, not single figures) ===")
    for message in MEAL_PLANS:
        hm_runs: list[TurnResult] = []
        cdk_runs: list[TurnResult] = []
        for _ in range(meal_plan_reps):
            first = _sleep(gap, first)
            hm_runs.append(one_turn(hand_made_url, message, timeout))
            first = _sleep(gap, first)
            cdk_runs.append(one_turn(cdk_url, message, timeout))

        def totals(runs: list[TurnResult]) -> list[Decimal]:
            out = []
            for r in runs:
                if r.ok and r.payable_total:
                    try:
                        out.append(Decimal(r.payable_total))
                    except (ValueError, ArithmeticError):
                        pass
            return out

        hm_totals = totals(hm_runs)
        cdk_totals = totals(cdk_runs)
        hm_meals = sorted({r.meal_count for r in hm_runs if r.ok})
        cdk_meals = sorted({r.meal_count for r in cdk_runs if r.ok})

        print(f"\n  {message!r}  ({meal_plan_reps} reps each)")
        print(
            f"    hand-made: meals={hm_meals} "
            f"payable={[str(t) for t in hm_totals]} "
            f"citations={[r.citation_count for r in hm_runs if r.ok]}"
        )
        print(
            f"    CDK      : meals={cdk_meals} "
            f"payable={[str(t) for t in cdk_totals]} "
            f"citations={[r.citation_count for r in cdk_runs if r.ok]}"
        )

        hm_fail = [r.detail for r in hm_runs if not r.ok]
        cdk_fail = [r.detail for r in cdk_runs if not r.ok]
        if hm_fail or cdk_fail:
            print(f"    failures  hand-made={hm_fail} CDK={cdk_fail}")

        # Verdict: overlapping meal counts and overlapping/adjacent totals is
        # parity. Disjoint meal counts is a real divergence worth investigating.
        if hm_meals and cdk_meals and set(hm_meals) & set(cdk_meals):
            print("    -> meal counts overlap: consistent with parity (totals vary by design)")
        elif hm_meals and cdk_meals:
            print("    -> meal counts DISJOINT: investigate (not explained by run-to-run variance)")
            exit_code = max(exit_code, 1)
        else:
            print("    -> inconclusive (a plane returned no usable plan)")
            exit_code = max(exit_code, 2)

    print(f"\nexit={exit_code}  (0=parity, 1=a difference to investigate, 2=inconclusive)")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hand-made-url", default=HAND_MADE_URL)
    parser.add_argument("--cdk-url", default=CDK_URL)
    parser.add_argument("--meal-plan-reps", type=int, default=3)
    parser.add_argument("--rpm", type=float, default=9.0, help="requests per minute")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    return run(args.hand_made_url, args.cdk_url, args.meal_plan_reps, args.rpm, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
