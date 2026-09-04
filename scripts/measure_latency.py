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
    python scripts/measure_latency.py --url https://.../dev/chat
    python scripts/measure_latency.py --price-checks 50 --meal-plans 50

Paced by MODEL CALL BUDGET, not by a flat turns-per-minute rate: a price check
costs two Nova Lite calls and a meal plan costs three, so the same turns/min
figure means different things for each. `--rpm` forces a flat rate and is
allowed to exceed the quota, for when measuring throttling is the point.
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

#: Binding Bedrock quota, in requests per minute. Amazon Nova Lite, 20/min in
#: ap-southeast-2 -- and NOT adjustable, which is the whole reason this file
#: paces at all. `scripts/check_quotas.py` reads it from the account; it is a
#: constant here so that measuring latency needs no extra IAM.
NOVA_LITE_RPM = 20

#: Nova Lite calls each turn kind makes. THIS IS THE THING THAT PACES THE RUN,
#: because turns are not equal and a flat turns-per-minute figure silently
#: stops being right when the graph gains a call.
#:
#: IT ALREADY DID. Until 2026-09-04 this file paced at a flat 9 turns/min,
#: correct when a meal plan made two Nova Lite calls (18/min, inside the quota).
#: Pilot Task 15c added `select_recipes` to every meal-plan turn, so a meal plan
#: costs THREE -- and 9 turns/min became 27 calls/min against a cap of 20. The
#: default would have thrown the meal-plan half of a run into throttling and
#: reported it as latency, which is precisely the confusion
#: `docs/THROUGHPUT-AND-SCALING.md` records: throttling arrives at the TAIL of a
#: run, so it reads as "the last cases were slow" rather than "the account
#: stopped answering".
#:
#: Pacing by call budget rather than by turn count is what stops that happening
#: again the next time a node is added: change the graph, change this number,
#: and every run re-paces itself.
NOVA_LITE_CALLS = {"price_check": 2, "meal_plan": 3}


def gap_seconds(
    kind: str, model_rpm: float = NOVA_LITE_RPM, flat_rpm: float | None = None
) -> float:
    """
    Seconds to wait before a turn of this kind, so the run stays inside the quota.

    A turn's share of the minute is the share of the model's request budget it
    consumes, which is why this takes a KIND rather than a count. `flat_rpm`
    overrides it with a plain turns-per-minute rate, and is deliberately allowed
    to exceed the quota -- measuring throttling on purpose is a legitimate thing
    to want, and the caller is told when they have asked for it.
    """
    if flat_rpm:
        return 60.0 / flat_rpm
    return 60.0 * NOVA_LITE_CALLS[kind] / model_rpm


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
    parser.add_argument(
        "--rpm",
        type=float,
        default=None,
        help=(
            "flat turns/min, overriding the call-budget pacing. Use only to "
            "measure the quota deliberately -- it can exceed it."
        ),
    )
    parser.add_argument(
        "--model-rpm",
        type=float,
        default=NOVA_LITE_RPM,
        help=f"binding Bedrock quota in requests/min (default {NOVA_LITE_RPM}, Nova Lite)",
    )
    parser.add_argument("--timeout", type=float, default=40.0)
    args = parser.parse_args()

    plan = [PRICE_CHECKS[i % len(PRICE_CHECKS)] for i in range(args.price_checks)]
    plan += [MEAL_PLANS[i % len(MEAL_PLANS)] for i in range(args.meal_plans)]

    print(f"{len(plan)} turns against {args.url}")
    if args.rpm:
        print(
            f"paced FLAT at {args.rpm}/min ({60.0 / args.rpm:.1f}s apart) -- "
            f"this can exceed the {args.model_rpm:.0f}/min model quota and measure throttling"
        )
    else:
        rates = {k: args.model_rpm / v for k, v in NOVA_LITE_CALLS.items()}
        print(
            f"paced by CALL BUDGET against {args.model_rpm:.0f} model req/min: "
            + ", ".join(f"{k} {r:.1f} turns/min" for k, r in rates.items())
        )
    print()

    baseline = Baseline()
    for index, message in enumerate(plan):
        if index:
            kind = "meal_plan" if message in MEAL_PLANS else "price_check"
            time.sleep(gap_seconds(kind, args.model_rpm, args.rpm))
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
