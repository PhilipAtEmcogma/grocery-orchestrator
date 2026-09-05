"""
AgentCore Runtime reviewer -- the propose/validate split (ADR 0002 WS2, Task 7).

The offline half deploys nothing. What is tested here is the SPLIT that Option A
depends on (`docs/AGENTCORE-RUNTIME-REVIEWER.md` §3): the model half
(`propose_findings`, what the microVM runs) returns RAW findings, and the
validation half (`validate_report`, what the caller runs) is where a fabricated
finding is caught. The point of the split is that the Runtime cannot validate
its own output -- so the test that matters is that validation on the caller's
side rejects a claim the "Runtime" returned.

The Runtime entrypoint is exercised through `_handle_invocation` with a scripted
model injected, so there is no HTTP server and no AWS.
"""

from __future__ import annotations

from src.models.base import ModelClient, ModelError
from src.prompts.review import ReviewFinding, ReviewReport
from src.review import (
    ReviewSnapshot,
    SnapshotRow,
    propose_findings,
    validate_report,
)


def _row(**overrides) -> dict:
    base = {
        "store_key": "paknsave#albany",
        "product_key": "butter-500g",
        "store": "paknsave",
        "store_location": "Albany",
        "display_name": "Pams Butter 500g",
        "canonical_name": "Butter",
        "category": "dairy",
        "price_nzd": "47.90",
        "unit": "500g",
        "unit_price_nzd": "95.80",
        "pack_grams": 500,
        "on_special": False,
        "valid_date": "2026-07-31",
        "baseline_avg_nzd": "4.79",
        "baseline_min_nzd": "4.59",
        "baseline_max_nzd": "4.99",
        "baseline_samples": 30,
        "baseline_window_days": 90,
        "deviation_ratio": "10.00",
    }
    base.update(overrides)
    return base


def _snapshot(row: dict) -> ReviewSnapshot:
    return ReviewSnapshot(rows=(SnapshotRow(**row),), captured_from="grocery-products-dev")


class _FixedModel(ModelClient):
    """Returns a fixed ReviewReport, standing in for the model inside the microVM."""

    def __init__(self, report: ReviewReport | None = None, *, fail: bool = False) -> None:
        self._report = report or ReviewReport(findings=[])
        self._fail = fail

    @property
    def last_usage(self) -> dict:
        return {}

    def text(self, **kwargs) -> str:  # pragma: no cover
        raise ModelError("no text path")

    def structured(self, *, schema, **kwargs):
        if self._fail:
            raise ModelError("scripted upstream failure")
        return self._report


# ---------------------------------------------------------------- the split


def test_propose_returns_raw_findings_without_validating() -> None:
    """
    The model half does NOT validate. A finding quoting a value the row does not
    have comes straight back out of `propose_findings` -- because validation is
    the caller's job, and this is what runs inside the untrusted Runtime.
    """
    row = _row()
    report = ReviewReport(
        findings=[
            ReviewFinding(
                store_key=row["store_key"],
                product_key=row["product_key"],
                kind="price_deviation",
                observation="way off",
                quoted={"deviation_ratio": "2.00"},  # the row says 10.00 -- a lie
            )
        ]
    )
    out = propose_findings([row], table_name="t", model=_FixedModel(report))
    # Returned unchanged -- propose_findings trusts nothing and checks nothing.
    assert len(out.findings) == 1
    assert out.findings[0].quoted["deviation_ratio"] == "2.00"


def test_the_caller_side_validation_rejects_the_lie() -> None:
    """
    The trust boundary: the SAME fabricated finding is rejected by
    `validate_report` against the snapshot the caller holds. A compromised
    Runtime returns claims; the caller's validation is what makes them safe.
    """
    row = _row()
    snapshot = _snapshot(row)
    report = ReviewReport(
        findings=[
            ReviewFinding(
                store_key=row["store_key"],
                product_key=row["product_key"],
                kind="price_deviation",
                observation="way off",
                quoted={"deviation_ratio": "2.00"},  # the row says 10.00
            )
        ]
    )
    result = validate_report(report, snapshot)
    assert result.accepted == ()
    assert result.validated.rejected  # rejected on the caller's side


def test_a_true_finding_survives_the_round_trip() -> None:
    """propose -> validate accepts a finding that quotes the row correctly."""
    row = _row()
    snapshot = _snapshot(row)
    report = ReviewReport(
        findings=[
            ReviewFinding(
                store_key=row["store_key"],
                product_key=row["product_key"],
                kind="price_deviation",
                observation="price is far above its own 90-day history",
                quoted={"deviation_ratio": "10.00"},
            )
        ]
    )
    result = validate_report(report, snapshot)
    assert result.validated.accepted_count == 1


def test_a_finding_citing_a_row_not_in_the_snapshot_is_rejected() -> None:
    """
    The Runtime could return a finding about a row the caller never sent. The
    caller's snapshot is the whole universe, so it is rejected -- the reason
    validation must run where the snapshot is, not inside the Runtime.
    """
    snapshot = _snapshot(_row())
    report = ReviewReport(
        findings=[
            ReviewFinding(
                store_key="paknsave#nowhere",
                product_key="ghost-1kg",
                kind="price_deviation",
                observation="not a real row",
                quoted={"deviation_ratio": "9.00"},
            )
        ]
    )
    result = validate_report(report, snapshot)
    assert result.accepted == ()
    assert result.validated.rejected


# ---------------------------------------------------------------- the entrypoint


def test_the_entrypoint_returns_raw_findings_and_never_validates() -> None:
    """
    `_handle_invocation` is the microVM's request handler. It returns raw
    findings (validation is the caller's job) and never imports the validator.
    """
    import agentcore.reviewer.app as app

    row = _row()
    report = ReviewReport(
        findings=[
            ReviewFinding(
                store_key=row["store_key"],
                product_key=row["product_key"],
                kind="price_deviation",
                observation="way off",
                quoted={"deviation_ratio": "2.00"},  # a lie -- but the Runtime does not check
            )
        ]
    )
    app._model = _FixedModel(report)
    try:
        out = app._handle_invocation({"table_name": "t", "rows": [row]})
    finally:
        app._model = None
    assert out["ran"] is True
    assert len(out["findings"]) == 1  # returned as-is, unchecked


def test_the_entrypoint_refuses_an_oversized_snapshot() -> None:
    """Defence in depth: the cap the caller is supposed to enforce, enforced again."""
    import agentcore.reviewer.app as app
    from src.review.snapshot import MAX_SNAPSHOT_ROWS

    out = app._handle_invocation({"table_name": "t", "rows": [_row()] * (MAX_SNAPSHOT_ROWS + 1)})
    assert out["ran"] is False
    assert "cap is" in out["error"]


def test_the_entrypoint_reports_a_model_failure_as_ran_false() -> None:
    """A failed model call is ran=False with no findings -- never a crash, never an invention."""
    import agentcore.reviewer.app as app

    app._model = _FixedModel(fail=True)
    try:
        out = app._handle_invocation({"table_name": "t", "rows": [_row()]})
    finally:
        app._model = None
    assert out["ran"] is False
    assert out["findings"] == []


def test_the_entrypoint_does_not_import_the_validator() -> None:
    """
    The trust boundary made visible in the import graph: the module the microVM
    runs must not import `validate_findings`. If this fails, the validator has
    leaked into the untrusted side and Option A is broken.
    """
    import agentcore.reviewer.app as app

    # The runtime attributes that would exist if the validator were imported.
    # Prose in the docstring may NAME the validator (it explains why it is
    # absent); what must not exist is a binding to it in the module namespace.
    assert not hasattr(app, "validate_findings")
    assert not hasattr(app, "validate_report")


# ---------------------------------------------------------------- the HTTP contract


def test_the_entrypoint_serves_the_real_http_contract() -> None:
    """
    Boot the ACTUAL entrypoint as an HTTP server and call it over a socket, the
    way the deployed microVM is called. This is the cost-free de-risking: a
    serialization or contract bug that would cost a live deploy iteration shows
    up here against a real socket, not in the account.

    Proves GET /ping -> Healthy and POST /invocations -> raw findings JSON, with
    a scripted model injected so no AWS is touched.
    """
    import http.client
    import json
    import threading
    from http.server import ThreadingHTTPServer

    import agentcore.reviewer.app as app

    row = _row()
    report = ReviewReport(
        findings=[
            ReviewFinding(
                store_key=row["store_key"],
                product_key=row["product_key"],
                kind="price_deviation",
                observation="price is far above its own 90-day history",
                quoted={"deviation_ratio": "10.00"},
            )
        ]
    )
    app._model = _FixedModel(report)
    server = ThreadingHTTPServer(("127.0.0.1", 0), app._Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)

        conn.request("GET", "/ping")
        assert json.loads(conn.getresponse().read()) == {"status": "Healthy"}

        body = json.dumps({"table_name": "grocery-products-dev", "rows": [row]})
        headers = {"Content-Type": "application/json"}
        conn.request("POST", "/invocations", body=body, headers=headers)
        data = json.loads(conn.getresponse().read())
        assert data["ran"] is True
        assert len(data["findings"]) == 1
        assert data["findings"][0]["kind"] == "price_deviation"

        # An unknown path is a 404, not a crash.
        conn.request("GET", "/nope")
        assert conn.getresponse().status == 404
    finally:
        server.shutdown()
        app._model = None


def test_the_entrypoint_rejects_invalid_json_over_http() -> None:
    """Malformed body -> 400 with a contract-valid error, never a stack trace to the caller."""
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    import agentcore.reviewer.app as app

    app._model = _FixedModel()
    server = ThreadingHTTPServer(("127.0.0.1", 0), app._Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request(
            "POST", "/invocations", body="{not json", headers={"Content-Type": "application/json"}
        )
        resp = conn.getresponse()
        assert resp.status == 400
    finally:
        server.shutdown()
        app._model = None
