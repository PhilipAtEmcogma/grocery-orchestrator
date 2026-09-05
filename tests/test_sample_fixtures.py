"""
Round-trip test: the committed samples must equal what the server produces.

`validate.py` already checks the samples are *contract-valid*. That catches a
schema regression and nothing else — a sample that is valid but no longer
resembles the current output passes it happily. The frontend team builds
against these files, so a sample that has quietly drifted from the server is
worse than a missing one: it is a fixture that teaches the wrong shape and
gives no sign of doing so.

So this regenerates each fixture through the same entrypoint the dev server
calls and diffs it against the committed bytes. Anything that changes the
output — a renamed field, a reordered event, a different seq, a citation that
gained a key — fails here with the diff.

`tests/test_multi_item.py` covers the same two behaviours at the graph level,
asking "are there three comparisons?". This asks the stricter question: "is
the response byte-for-byte what we published?" Both are worth having; the
behavioural test explains intent, this one pins the artefact.

To regenerate after an intentional change:

    UPDATE_FIXTURES=1 python -m pytest tests/test_sample_fixtures.py

then review the diff and commit it. Regenerating without reading the diff
defeats the entire point of the test.
"""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from src.handler import lambda_handler

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

# The request that produced each committed response. Recorded here so every
# fixture is reproducible from the repo alone — a captured response whose
# request was never written down cannot be regenerated, only re-guessed.
CASES: dict[str, dict[str, Any]] = {
    # FRONTEND-INTEGRATION.md §3.1 — one price_comparison event per item.
    "response_multi_comparison.json": {
        "version": "1.0",
        "session_id": "sess-7f3a9c21",
        "turn_id": "turn-0004-b1c9",
        "message": "price of butter and milk and bread",
    },
    # FRONTEND-INTEGRATION.md §3.2 — no_data alongside results, not instead.
    "response_partial.json": {
        "version": "1.0",
        "session_id": "sess-7f3a9c21",
        "turn_id": "turn-0005-d3a2",
        "message": "price of butter and wagyu ribeye",
    },
    # The meal plan. ADDED 2026-08-31, and it should have been here from the
    # start: Pilot Task 15c changed what this endpoint returns for a meal-plan
    # turn -- meals now carry curated recipe NAMES rather than "Scripted Dinner
    # 1" -- and the committed sample went stale with nothing to notice. It is
    # the published contract the frontend reads, and two of the four samples
    # this file could cover were the two it did.
    "response_meal_plan.json": json.loads(
        (Path(__file__).resolve().parents[1] / "samples" / "request_meal_plan.json").read_text(
            encoding="utf-8"
        )
    ),
}

# `json.tool`, which produced the committed files.
INDENT = 4

# Wall-clock values differ between two identical runs. They are rewritten on
# BOTH sides rather than dropped: replacing a key only where it already exists
# means a field that stops being emitted still surfaces as a diff, where
# deleting it from both would hide exactly that regression.
FIXED_TIME = "2026-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def _fresh_idempotency_store(monkeypatch):
    """
    Reset the cached store between tests.

    It is module-level so it survives warm Lambda invocations. Here that would
    mean the second run of a turn_id replays the first instead of exercising
    the graph — the test would pass without testing anything.
    """
    import src.handler as handler_mod

    monkeypatch.setattr(handler_mod, "_idempotency", None)


def _produce(request: dict[str, Any]) -> dict[str, Any]:
    """One turn, through the same entrypoint scripts/dev_server.py calls."""
    result = lambda_handler({"httpMethod": "POST", "body": json.dumps(request)})
    assert result["statusCode"] == 200, f"handler returned {result['statusCode']}"
    return json.loads(result["body"])


def _normalise(response: dict[str, Any]) -> dict[str, Any]:
    """Rewrite the run-to-run varying fields, leaving everything else alone."""
    out = json.loads(json.dumps(response))
    for event in out.get("events", []):
        if event.get("type") != "done":
            continue
        if "server_time" in event:
            event["server_time"] = FIXED_TIME
        usage = event.get("usage")
        if isinstance(usage, dict) and "latency_ms" in usage:
            usage["latency_ms"] = 0
    return out


def _carry_forward_volatile(live: dict[str, Any], path: Path) -> dict[str, Any]:
    """
    Keep the committed wall-clock values when regenerating.

    Without this, every `UPDATE_FIXTURES=1` run rewrites `server_time` and so
    produces a diff even when nothing changed — which teaches people to commit
    the churn unread, and buries the one line that mattered on the run where
    something did change.
    """
    if not path.exists():
        return live

    committed = json.loads(path.read_text())
    out = json.loads(json.dumps(live))

    def _done(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return [e for e in payload.get("events", []) if e.get("type") == "done"]

    for prev, cur in zip(_done(committed), _done(out), strict=False):
        if "server_time" in prev and "server_time" in cur:
            cur["server_time"] = prev["server_time"]
        prev_usage, cur_usage = prev.get("usage"), cur.get("usage")
        if isinstance(prev_usage, dict) and isinstance(cur_usage, dict):
            # `is not None` matters. Carrying forward suppresses CHURN in a
            # wall-clock value; carrying forward a null suppresses the field
            # appearing at all. When usage went from never-populated to
            # populated, this pinned latency_ms at null beside real token
            # counts -- a combination the server cannot produce, published as
            # the contract the frontend reads.
            if prev_usage.get("latency_ms") is not None and "latency_ms" in cur_usage:
                cur_usage["latency_ms"] = prev_usage["latency_ms"]
    return out


def _render(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=INDENT) + "\n"


def _diff(expected: str, actual: str, filename: str) -> str:
    return "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=f"samples/{filename} (committed)",
            tofile=f"samples/{filename} (live)",
        )
    )


@pytest.mark.parametrize("filename", sorted(CASES))
def test_sample_matches_live_output(filename: str):
    path = SAMPLES / filename
    live = _produce(CASES[filename])

    if os.environ.get("UPDATE_FIXTURES"):
        # newline="\n" because the committed files are LF. Letting Windows
        # translate would rewrite every line of a file whose content did not
        # change.
        path.write_text(_render(_carry_forward_volatile(live, path)), newline="\n")
        pytest.skip(f"regenerated {filename} — review the diff before committing")

    assert path.exists(), f"{filename} is missing. UPDATE_FIXTURES=1 to create it."

    expected = _render(_normalise(json.loads(path.read_text())))
    actual = _render(_normalise(live))

    assert expected == actual, (
        f"\nsamples/{filename} no longer matches what the server produces.\n"
        f"If the change is intentional: UPDATE_FIXTURES=1 python -m pytest "
        f"tests/test_sample_fixtures.py\n\n"
        f"{_diff(expected, actual, filename)}"
    )


@pytest.mark.parametrize("filename", sorted(CASES))
def test_sample_is_byte_identical_not_just_equivalent(filename: str):
    """
    The committed file must also be formatted the way regeneration writes it.

    Otherwise `UPDATE_FIXTURES=1` reformats every fixture it touches and buries
    the one real change in a whole-file diff.
    """
    committed = (SAMPLES / filename).read_text()
    assert committed == _render(json.loads(committed)), (
        f"samples/{filename} is not formatted as json.dumps(indent={INDENT}). "
        f"Regenerate it with UPDATE_FIXTURES=1."
    )


def test_fixtures_still_exercise_the_shapes_they_exist_for():
    """
    A fixture that stops covering its case is worse than a deleted one: it
    still passes the diff above, because the diff only asks whether the file
    matches the server — not whether either still shows the behaviour.
    """
    multi = json.loads((SAMPLES / "response_multi_comparison.json").read_text())
    types = [e["type"] for e in multi["events"]]
    assert types.count("price_comparison") >= 2, (
        "response_multi_comparison.json no longer shows more than one "
        "price_comparison event, which is the only reason it exists"
    )

    partial = json.loads((SAMPLES / "response_partial.json").read_text())
    types = [e["type"] for e in partial["events"]]
    assert "no_data" in types and "price_comparison" in types, (
        "response_partial.json no longer shows a no_data arriving alongside "
        "results, which is the only reason it exists"
    )
