"""
Shared helpers for the demos. Not a demo itself.

Every demo imports from here so the files stay about the FEATURE rather than
about printing. Nothing in this module touches AWS.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

# The demos live in a subfolder, so the repo root has to be importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.retrieval.filters import pin_to_fixture_snapshot
from src.schemas.contract import ChatRequest, ChatResponse, ClientHints, Location

# Every demo reads the committed fixture catalogue, which is a snapshot
# with a fixed capture date. Judged against the wall clock it goes stale on
# a day nobody chose and every demo starts answering STALE_DATA. Pinned here
# rather than in run_all.py because each demo is documented as runnable on
# its own, and run_all only spawns subprocesses.
pin_to_fixture_snapshot()

RULE = "=" * 74


def heading(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


def section(title: str) -> None:
    print(f"\n--- {title} ---")


def request(
    message: str,
    *,
    turn: str = "turn-demo01",
    location: Location | None = None,
    **hints,
) -> ChatRequest:
    """
    Build a ChatRequest.

    session_id and turn_id have an 8-character minimum in the contract, which
    is the first thing that catches people writing their own demo scripts.

    `location` is passed through rather than folded into **hints because it is
    a separate top-level field on the request: a place is not a preference.
    """
    return ChatRequest(
        session_id="sess-demo01",
        turn_id=turn,
        message=message,
        location=location,
        hints=ClientHints(**hints) if hints else None,
    )


def citations(response: ChatResponse) -> dict:
    """
    ref -> Citation.

    Needed by almost every demo, because a PriceOption carries only a
    `citation_ref` and no price. That is the grounding design, not an
    oversight: to show a number you must go and look up the cited record,
    which is exactly the step that makes an invented price impossible.
    """
    return {e.citation.ref: e.citation for e in response.events if e.type == "citation"}


def show_events(response: ChatResponse, *, skip: tuple[str, ...] = ("session",)) -> None:
    """Print the event stream the frontend would receive, one line per event."""
    index = citations(response)
    for ev in response.events:
        if ev.type in skip:
            continue
        print(f"  [{ev.seq:>2}] {ev.type:<16} {_summarise(ev, index)}")


def _summarise(ev, index: dict) -> str:
    if ev.type == "citation":
        c = ev.citation
        return f"{c.ref} {c.product_name} ${c.price_nzd} @ {c.store.value}"
    if ev.type == "price_comparison":
        d = ev.data
        cheapest = next((o for o in d.options if o.is_cheapest), d.options[0])
        cited = index.get(cheapest.citation_ref)
        price = f"${cited.price_nzd}" if cited else f"[{cheapest.citation_ref}]"
        return f"{d.query_item}: {len(d.options)} options, cheapest {price}"
    if ev.type == "meal_plan":
        p = ev.data
        return (
            f"{len(p.meals)} meals, ${p.total_nzd} of ${p.budget_nzd} "
            f"(within_budget={p.within_budget}, repairs={p.repair_attempts})"
        )
    if ev.type == "error":
        return f"{ev.code.value} retryable={ev.retryable} :: {ev.message[:60]}"
    if ev.type == "no_data":
        return f"{ev.requested_item}: {ev.message}"
    if ev.type == "notice":
        return ev.message[:80]
    if ev.type == "token":
        return repr(ev.text[:60])
    return ""


def money(value: Decimal) -> str:
    return f"${value}"


# ==========================================================================
# Modes
# ==========================================================================
#
# Three of them, because "this demo runs" and "this demo touched AWS" are
# different claims and the suite has to be able to say which one it is making.
#
#   LOCAL        fixtures + the scripted model. No credentials, no network,
#                nothing outside this repository. Demos 1-7 and most of the
#                rest are local ONLY, and that is a property of the project:
#                the graph depends on PriceRepository and ModelClient, not on
#                boto3, so the whole thing runs on a laptop.
#
#   INTEGRATION  a real service that is already deployed, reached over the
#                network as a client would reach it -- the API Gateway
#                endpoint. Needs network access and a URL, but no AWS
#                credentials, because the endpoint is unauthenticated
#                (docs/ARCHITECTURE.md section 7).
#
#   AWS          this project's deployed AWS resources through boto3 --
#                DynamoDB, Bedrock, the Guardrail. Needs credentials and the
#                IAM grants in config/iam-orchestrator-role.json.
#
# A demo declares which modes it supports. DEMO_MODE selects one. Asking for a
# mode that cannot run is a FAILURE (exit 2), never a silent downgrade to
# LOCAL: a demo that quietly answered from fixtures when you asked it for
# Bedrock would be the exact defect docs/ARCHITECTURE.md section 3g exists to
# prevent, reproduced in the demo suite.

LOCAL = "local"
INTEGRATION = "integration"
AWS = "aws"

#: The account's region, from docs/ARCHITECTURE.md. Every deployed resource
#: this suite can reach lives in it.
AWS_REGION = "ap-southeast-2"

MODE_ENV = "DEMO_MODE"

#: Exit code for "you asked for a mode this environment cannot provide".
#: Distinct from 1 so run_all.py can tell a blocked demo from a broken one.
EXIT_BLOCKED = 2

#: The deployed dev endpoint, from docs/ARCHITECTURE.md section 3. Not a
#: secret -- it is an unauthenticated URL recorded in the repository already --
#: but overridable, because a demo pinned to one account contradicts every
#: "reproducible in another account" claim the config headers make.
ENDPOINT_ENV = "CHAT_ENDPOINT_URL"
DEFAULT_ENDPOINT = "https://woqmel35lk.execute-api.ap-southeast-2.amazonaws.com/dev/chat"


class ModeUnavailable(RuntimeError):
    """The requested mode cannot run here. Carries what is missing and how to fix it."""


def resolve_mode(*, supports: tuple[str, ...], default: str = LOCAL) -> str:
    """
    Which mode this run is in.

    Raises rather than falling back when DEMO_MODE names a mode this demo does
    not implement, for the same reason `route()` raises UnroutableTask: a
    silent substitution changes what was measured with no signal.
    """
    import os

    requested = os.environ.get(MODE_ENV, "").strip().lower() or default
    if requested not in supports:
        raise ModeUnavailable(
            f"{MODE_ENV}={requested!r} but this demo supports "
            f"{', '.join(supports)}. Nothing was run."
        )
    return requested


def mode_banner(mode: str, *, requires: str, mocked: str = "nothing") -> None:
    """
    State the mode before any work happens, so the output cannot be misread.

    `requires` and `mocked` are printed rather than inferred. The rule this
    suite follows is that a mocked component must SAY it is mocked -- "using
    the scripted model client" and "called Bedrock" are different sentences
    and only one of them is true at a time.
    """
    print(f"\nMODE        {mode.upper()}")
    print(f"REQUIRES    {requires}")
    print(f"MOCKED      {mocked}")


def note(text: str) -> None:
    """A one-line aside, indented to sit under the current section."""
    print(f"  {text}")


def step(number: int, text: str) -> None:
    """A numbered stage of a pipeline, for demos that show a flow."""
    print(f"  [{number}] {text}")


def blocked(what: str, why: str, fix: str) -> int:
    """
    Report that a mode could not run, and return the exit code for it.

    Deliberately loud and deliberately non-zero. A blocked demo has not
    passed, and the report at the end of this suite says so.
    """
    print(f"\n{RULE}")
    print(f"BLOCKED: {what}")
    print(RULE)
    print(f"  why:  {why}")
    print(f"  fix:  {fix}")
    print("\n  Nothing above this line touched the unavailable dependency, and")
    print("  nothing below it was run. This is a BLOCKED result, not a pass.")
    return EXIT_BLOCKED


def aws_identity() -> tuple[bool, str]:
    """
    (usable, detail) for the ambient AWS credentials.

    One STS call, which needs no project permission at all, so a failure here
    means "no credentials" rather than "credentials without the right grant" --
    those are different problems and telling them apart early saves reading a
    misleading AccessDenied from a service call.
    """
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - boto3 is in requirements
        return False, f"boto3 is not importable: {exc}"
    try:
        ident = boto3.client("sts", region_name=AWS_REGION).get_caller_identity()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"
    return True, f"account {ident['Account']} as {ident['Arn'].rsplit('/', 1)[-1]}"


def unpin_freshness() -> None:
    """
    Undo the fixture-snapshot pin for a demo that reads REAL data.

    Importing this module pins FRESHNESS_AS_OF to the fixture capture date,
    which is right for every offline demo and wrong the moment a demo queries
    `grocery-products-dev`: that catalogue has its own capture date and must be
    judged against the wall clock, exactly as production does.
    """
    import os

    from src.retrieval.filters import AS_OF_ENV

    os.environ.pop(AS_OF_ENV, None)


def endpoint_url() -> str:
    """The deployed chat endpoint for INTEGRATION mode."""
    import os

    return os.environ.get(ENDPOINT_ENV, "").strip() or DEFAULT_ENDPOINT
