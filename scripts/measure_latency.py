"""
Latency baseline against the DEPLOYED endpoint (Req 12.6, Pilot Task 12).

Every latency figure in this repository has been a laptop measurement. The
pilot acceptance targets are about a deployed service -- p95 price checks under
5 seconds, p95 meal plans under 20, p99 meal plans under the ~25-second
escalation trigger -- and nothing had measured that.

This measures it, end to end over HTTPS, including the gateway hop, SnapStart
restore, Bedrock, DynamoDB and the whole graph. Client-side wall clock, because
that is what a shopper experiences; CloudWatch's `TurnLatency` measures the
handler and excludes the gateway.

PACED, AND THE PACING IS NOT OPTIONAL. The account allows a small number of
Bedrock requests per minute and the binding Nova Lite quota CANNOT be raised
(docs/THROUGHPUT-AND-SCALING.md). An unpaced run measures the quota: throttled
calls fail at the TAIL of a burst, so the numbers read as "the service got slow"
when they mean "the account stopped answering". The eval harnesses learned this
the expensive way -- three model bands were scored before anyone checked.

A RUN CARRYING FAILURES IS NOT A SLOW RUN, IT IS A VOID ONE. Percentiles are
reported only over turns that returned a contract-valid answer, and the failure
count is printed separately. Averaging a timeout into a latency figure produces
a number that describes neither.

Usage:
    python scripts/measure_latency.py
    python scripts/measure_latency.py --price-checks 20 --meal-plans 10
    python scripts/measure_latency.py --url https://.../dev/chat --rpm 9
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

DEFAULT_URL = "https://woqmel35lk.execute-api.ap-southeast-2.amazonaws.com/dev/chat"

# Deliberately varied so the catalogue lookup differs per turn -- a repeated
# identical query would measure one hot path and, worse, the idempotency cache.
PRICE_CHECKS = [
    "cheapest butter",
    "cheapest milk",
    "cheapest bananas",
    "how much is bread",
    "cheapest eggs near Albany",
    "cheapest carrots",
    "how much is chicken",
    "cheapest onions",
    "cheapest rice",
    "cheapest pasta near Manukau",
]
MEAL_PLANS = [
    "feed 3 people for 5 days on $80",
    "dinner for 2 for 4 days on $60",
    "feed 4 people for 3 days on $70",
    "vegetarian meal plan for 2 for 3 days on $50",
    "feed 1 person for 7 days on $55",
]


@dataclass
class Sample:
    kind: str
    seconds: float
    ok: bool
    detail: str = ""


@dataclass
class Baseline:
    samples: list[Sample] = field(default_factory=list)

    def of(self, kind: str) -> list[float]:
        return sorted(s.seconds for s in self.samples if s.kind == kind and s.ok)

    def failures(self, kind: str) -> list[Sample]:
        return [s for s in self.samples if s.kind == kind and not s.ok]


def percentile(values: list[float], p: float) -> float:
    """
    Nearest-rank. With a handful of samples an interpolating percentile invents
    a value between two measurements, and at this sample size the honest answer
    is one of the numbers actually observed.
    """
    if not values:
        return float("nan")
    index = min(len(values) - 1, max(0, round(p / 100 * len(values) + 0.5) - 1))
    return values[index]


def one_turn(url: str, message: str, timeout: float) -> Sample:
    session = f"sess-lat{uuid.uuid4().hex[:12]}"
    body = json.dumps(
        {
            "version": "1.0",
            "session_id": session,
            "turn_id": f"turn-lat{uuid.uuid4().hex[:12]}",
            "message": message,
        }
    ).encode()
    request = urllib.request.Request(  # noqa: S310 - fixed https endpoint
        url, data=body, headers={"Content-Type": "application/json"}
    )
    kind = "meal_plan" if message in MEAL_PLANS else "price_check"

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            elapsed = time.perf_counter() - started
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return Sample(kind, time.perf_counter() - started, False, type(exc).__name__)

    events = {e["type"] for e in payload.get("events", [])}
    codes = [e.get("code") for e in payload.get("events", []) if e["type"] == "error"]
    # An honest refusal is a successful turn and belongs in the latency figure:
    # it costs the same work. An INTERNAL_ERROR or a timeout does not.
    if "INTERNAL_ERROR" in codes or "UPSTREAM_TIMEOUT" in codes:
        return Sample(kind, elapsed, False, str(codes))
    if "done" not in events:
        return Sample(kind, elapsed, False, "no done event")
    return Sample(kind, elapsed, True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--price-checks", type=int, default=10)
    parser.add_argument("--meal-plans", type=int, default=5)
    parser.add_argument("--rpm", type=float, default=9.0, help="requests per minute")
    parser.add_argument("--timeout", type=float, default=40.0)
    args = parser.parse_args()

    plan = [PRICE_CHECKS[i % len(PRICE_CHECKS)] for i in range(args.price_checks)]
    plan += [MEAL_PLANS[i % len(MEAL_PLANS)] for i in range(args.meal_plans)]
    gap = 60.0 / args.rpm

    print(f"{len(plan)} turns against {args.url}")
    print(f"paced at {args.rpm}/min ({gap:.1f}s apart) - unpaced measures the quota\n")

    baseline = Baseline()
    for index, message in enumerate(plan):
        if index:
            time.sleep(gap)
        sample = one_turn(args.url, message, args.timeout)
        baseline.samples.append(sample)
        mark = "ok " if sample.ok else "FAIL"
        print(f"  [{index + 1:>2}/{len(plan)}] {mark} {sample.seconds:6.2f}s  {message[:44]}")

    print()
    targets = {"price_check": 5.0, "meal_plan": 20.0}
    exit_code = 0
    for kind, target in targets.items():
        values = baseline.of(kind)
        failed = baseline.failures(kind)
        if not values:
            print(f"{kind}: NO SUCCESSFUL TURNS ({len(failed)} failed) - inconclusive")
            exit_code = 2
            continue
        p50, p95, p99 = (percentile(values, p) for p in (50, 95, 99))
        verdict = "OK" if p95 <= target else "OVER TARGET"
        print(
            f"{kind:12s} n={len(values):<3} p50={p50:5.2f}s p95={p95:5.2f}s "
            f"p99={p99:5.2f}s  (p95 target {target:.0f}s: {verdict})"
        )
        if failed:
            print(f"{'':12s} {len(failed)} excluded: {[s.detail for s in failed]}")
        if p95 > target:
            exit_code = max(exit_code, 1)

    total = [s for s in baseline.samples if s.ok]
    if total:
        print(
            f"\nall turns    n={len(total)} mean={statistics.mean(s.seconds for s in total):.2f}s"
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
