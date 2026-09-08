"""
The catalogue stream guard: does it see the incident that justified it?

§3t is the case this exists for. A plain `scripts/load_seed_data.py` run
re-added 152 fixture rows to the live products table; they carried
`valid_date: 2026-07-31` against a catalogue captured `2026-08-28`, they
shadowed the real prices, and the deployed endpoint served fixture data for
days. The first test below replays exactly that.

WHAT THESE TESTS ARE CAREFUL ABOUT. The guard is a detector, and a detector has
two ways to be useless: missing the thing it was built for, and firing on
ordinary traffic until people mute it. Both directions are covered, and the
second one is where the cases are — a legitimate refresh rewrites every row in
the catalogue, so a guard that treated "MODIFY" as suspicious would report 2,759
findings on the first scheduled run.
"""

from __future__ import annotations

import json

import pytest

from ingestion.stream_guard import (
    BATCH_LOG_MESSAGE,
    FOREIGN_WRITE_LOG_MESSAGE,
    SAMPLE_LIMIT,
    expected_capture_date,
    inspect,
    lambda_handler,
)

CAPTURE = "2026-08-28"
FIXTURE_DATE = "2026-07-31"


def _record(
    *,
    valid_date: str | None = CAPTURE,
    event: str = "INSERT",
    store: str = "paknsave#albany",
    product: str = "salted-butter-500g",
    old_valid_date: str | None = None,
) -> dict:
    image: dict = {
        "store_key": {"S": store},
        "product_key": {"S": product},
        "price_nzd": {"S": "9.49"},
    }
    if valid_date is not None:
        image["valid_date"] = {"S": valid_date}

    record: dict = {"eventName": event, "dynamodb": {"NewImage": image}}
    if old_valid_date is not None:
        record["dynamodb"]["OldImage"] = {"valid_date": {"S": old_valid_date}}
    return record


# --------------------------------------------------------------------------
# the incident
# --------------------------------------------------------------------------


def test_it_catches_the_fixture_shadowing_that_actually_happened():
    """
    §3t, replayed. This is the whole reason the guard exists.

    A row carrying the fixtures' capture date arriving in a catalogue captured
    on another day is a write that did not come from the current ingestion
    source, whoever made it and whatever credentials they used.
    """
    findings = inspect([_record(valid_date=FIXTURE_DATE)], expected=CAPTURE)

    assert len(findings) == 1
    assert findings[0]["valid_date"] == FIXTURE_DATE
    assert findings[0]["expected"] == CAPTURE
    assert findings[0]["product_key"] == "salted-butter-500g"


def test_it_distinguishes_a_foreign_row_that_OVERWROTE_a_real_one():
    """
    Shadowing is worse than appearing, and the finding says which happened.

    A foreign row that replaced a real one is the version that changed what
    shoppers were served; a new one merely sits there. The old image is what
    tells them apart.
    """
    findings = inspect(
        [_record(valid_date=FIXTURE_DATE, event="MODIFY", old_valid_date=CAPTURE)],
        expected=CAPTURE,
    )
    assert findings[0]["replaced_valid_date"] == CAPTURE
    assert findings[0]["event"] == "MODIFY"


def test_a_row_with_no_capture_date_at_all_is_a_finding():
    """
    The quietest version of the incident.

    A console edit or a partial `update-item` can land a row with no
    `valid_date`. Treating absent as fine would let through the one case that
    leaves no evidence of where it came from.
    """
    findings = inspect([_record(valid_date=None)], expected=CAPTURE)
    assert len(findings) == 1
    assert findings[0]["valid_date"] is None


# --------------------------------------------------------------------------
# and does not fire on ordinary traffic
# --------------------------------------------------------------------------


def test_a_legitimate_refresh_produces_no_findings():
    """
    THE CASE THAT DECIDES WHETHER ANYONE KEEPS THIS ALARM ON.

    A scheduled refresh rewrites every row it holds -- 2,759 MODIFY events
    carrying the current capture date. A guard that read MODIFY as suspicious
    would report the entire catalogue on its first run and be muted the same
    day.
    """
    batch = [
        _record(event="MODIFY", product=f"product-{i}", old_valid_date=CAPTURE) for i in range(500)
    ]
    assert inspect(batch, expected=CAPTURE) == []


def test_deletions_are_skipped_rather_than_reported():
    """`scripts/load_seed_data.py --remove` is the documented cleanup path."""
    removal = {"eventName": "REMOVE", "dynamodb": {"OldImage": {"valid_date": {"S": CAPTURE}}}}
    assert inspect([removal], expected=CAPTURE) == []


def test_an_empty_batch_is_not_an_error():
    assert inspect([], expected=CAPTURE) == []


# --------------------------------------------------------------------------
# what it emits, which is what the metric filter binds to
# --------------------------------------------------------------------------


def _lines(capsys) -> list[dict]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip().startswith("{")]


def test_every_finding_gets_its_own_line_so_the_metric_counts_them_all(capsys):
    """
    The metric filter counts LINES. A batch of three foreign rows must move the
    metric by three -- one summarising line with a count of three moves it by
    one, and the alarm would then need to understand batching to be right.
    """
    batch = [_record(valid_date=FIXTURE_DATE, product=f"p{i}") for i in range(9)]
    lambda_handler({"Records": batch})

    lines = _lines(capsys)
    foreign = [line for line in lines if line["message"] == FOREIGN_WRITE_LOG_MESSAGE]
    assert len(foreign) == 9, "every foreign row must produce a countable line"

    # Beyond the sample the keys are dropped and the signal is kept: the real
    # incident was 152 rows, and reporting it as five would understate it by 30x.
    named = [line for line in foreign if line.get("product_key")]
    assert len(named) == SAMPLE_LIMIT


def test_a_clean_batch_still_reports_that_it_ran(capsys):
    """
    Without a denominator, "no foreign writes" and "the consumer never ran"
    are the same picture -- the failure mode config/alarms.json calls out
    about treat_missing_data.
    """
    lambda_handler({"Records": [_record() for _ in range(4)]})

    lines = _lines(capsys)
    batch = [line for line in lines if line["message"] == BATCH_LOG_MESSAGE]
    assert len(batch) == 1
    assert batch[0] == {
        "message": BATCH_LOG_MESSAGE,
        "records": 4,
        "foreign": 0,
        "expected_capture_date": CAPTURE,
    }
    assert not [line for line in lines if line["message"] == FOREIGN_WRITE_LOG_MESSAGE]


def test_a_finding_does_not_raise_and_so_does_not_reach_the_dlq(capsys):
    """
    A finding is a fact about the DATA, not a failure of this function.

    Raising would send a correctly-processed batch to the DLQ and retry it,
    re-reporting the same rows until they expire — and it would make a message
    in the DLQ mean "we found something" rather than "this code could not run",
    which is the only reading that makes the DLQ worth checking.
    """
    result = lambda_handler({"Records": [_record(valid_date=FIXTURE_DATE)]})
    assert result == {"records": 1, "foreign": 1}
    capsys.readouterr()


# --------------------------------------------------------------------------
# where the expected date comes from
# --------------------------------------------------------------------------


def test_the_expected_date_defaults_to_the_ingestion_source_constant(monkeypatch):
    """
    One source of truth. Two copies of a capture date would eventually
    disagree, at which point the guard reports every legitimate write as
    foreign — the loudest possible way to be wrong.
    """
    from ingestion.sources import LineageBSource

    monkeypatch.delenv("EXPECTED_CAPTURE_DATE", raising=False)
    assert expected_capture_date() == LineageBSource.CAPTURED_AT


def test_the_expected_date_can_be_overridden_for_a_new_capture(monkeypatch):
    monkeypatch.setenv("EXPECTED_CAPTURE_DATE", "2026-10-01")
    assert expected_capture_date() == "2026-10-01"


def test_the_guard_agrees_with_what_is_actually_in_the_live_catalogue():
    """
    The constant this guard trusts is the one the loaded rows carry.

    Verified against the account on 2026-09-07: all 2,759 rows in
    grocery-products-dev carry valid_date 2026-08-28. If the ingestion source's
    constant ever moves without the catalogue being reloaded, this guard would
    report the whole table — so the two are pinned together here.
    """
    from ingestion.sources import LineageBSource

    assert LineageBSource.CAPTURED_AT == CAPTURE


# --------------------------------------------------------------------------
# the alarm the log line feeds
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["grocery-catalogue-foreign-write-dev"],
)
def test_the_alarm_and_its_filter_exist_and_bind_to_this_message(name):
    """
    A log line nothing meters is a line nobody reads. This holds the pairing
    the way tests/test_alarms.py holds the ingestion ones.
    """
    from scripts.apply_alarms import CONFIG, load_config

    config = load_config(CONFIG)

    alarm = next(a for a in config["alarms"] if a["name"] == name)
    filt = next(f for f in config["metric_filters"] if f["metric_name"] == alarm["metric_name"])
    # The filter must bind to the JSON field this module actually emits -- the
    # drift AWS cannot see, and the reason test_alarms.py checks patterns
    # against real log lines rather than trusting them.
    assert filt["pattern"] == f'{{ $.message = "{FOREIGN_WRITE_LOG_MESSAGE}" }}'
    assert "catalogue-guard" in filt["log_group"]
