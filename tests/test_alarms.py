"""
Alarm configuration tests (design.md 12.6).

Two halves, and the second is the one that matters.

`validate()` catches configurations that produce an alarm which silently does
nothing. Those tests are parametrised mutations: each takes the shipped config,
breaks one thing, and requires the validator to say so. A validator nobody has
watched reject anything is the same species of decoration as the alarm it is
meant to prevent.

The second half checks the config against the CODE. An alarm is a string in a
JSON file pointing at a string in a Python file, with nothing in between to
keep them together: rename the log line in `src/handler.py` and the metric
filter still deploys, still looks right in the console, and matches nothing
forever. So the test runs a real turn down the last-resort path, captures what
Powertools actually wrote, and applies the filter pattern to it.
"""

from __future__ import annotations

import io
import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.apply_alarms import CONFIG, load_config, validate

CONFIG_PATH = Path(CONFIG)


@pytest.fixture
def config() -> dict:
    return load_config(CONFIG_PATH)


# ------------------------------------------------------------------ the config


def test_the_shipped_config_is_valid(config):
    """What CI's dry-run step asserts, asserted here too so it fails faster."""
    assert validate(config) == []


def test_both_alarms_from_design_12_6_are_present(config):
    names = {a["metric_name"] for a in config["alarms"]}
    assert "HandlerEscaped" in names, "the handler_escaped alarm is missing"
    assert "5XXError" in names, "the API 5xx alarm is missing"


def test_the_two_alarms_do_not_share_a_failure_mode(config):
    """
    They overlap on purpose: the log filter says what broke and where, the 5xx
    alarm fires even when logging is the thing that broke. That is only true
    while one of them does NOT depend on our log pipeline.
    """
    by_metric = {a["metric_name"]: a for a in config["alarms"]}
    assert by_metric["5XXError"]["namespace"].startswith("AWS/"), (
        "the 5xx alarm must watch the gateway's own metric — an alarm we "
        "publish ourselves cannot survive our own logging breaking"
    )
    assert not by_metric["HandlerEscaped"]["namespace"].startswith("AWS/")


def test_every_resource_is_tagged_for_the_iac_migration(config):
    """design.md 11, point 1: tag on creation, or the conversion stops being
    mechanical."""
    tags = {t["key"]: t["value"] for t in config["tags"]}
    assert tags.get("Project") == "SmartGrocery"
    assert tags.get("Env")
    assert tags.get("ManagedBy") == "config/alarms.json"


def test_names_carry_the_environment_suffix(config):
    """design.md 11, point 2: generated resources must be able to coexist with
    the manually created ones during migration."""
    env = config["environment"]
    for alarm in config["alarms"]:
        assert alarm["name"].endswith(f"-{env}"), alarm["name"]
    for f in config["metric_filters"]:
        assert f["name"].endswith(f"-{env}"), f["name"]
    assert config["notification"]["topic_name"].endswith(f"-{env}")


# ------------------------------------------- validate() rejects what it claims


def _break(config: dict, path: str, value):
    """Return a copy of the config with one dotted path replaced."""
    broken = deepcopy(config)
    target = broken
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[int(part)] if part.isdigit() else target[part]
    last = parts[-1]
    if last.isdigit():
        target[int(last)] = value
    else:
        target[last] = value
    return broken


BREAKAGES = [
    # (dotted path, bad value, phrase the complaint must contain)
    ("alarms.0.statistic", "Average", "not 'Sum'"),
    ("alarms.0.comparison_operator", "GreaterThanThreshold", "needs two"),
    ("alarms.0.threshold", 0, "must be >= 1"),
    ("alarms.0.datapoints_to_alarm", 3, "single occurrence"),
    ("alarms.0.evaluation_periods", 6, "evaluation_periods"),
    ("alarms.0.treat_missing_data", "breaching", "idle system"),
    ("alarms.0.treat_missing_data", "missing", "INSUFFICIENT_DATA"),
    ("alarms.0.period", 45, "multiple of 60"),
    ("alarms.0.metric_name", "HandlerEscpaed", "no metric filter"),
    ("metric_filters.0.pattern", "handler_escaped", "JSON selector"),
    ("metric_filters.0.default_value", 1, "default_value must be 0"),
    ("notification.topic_name", "", "dashboard widget"),
    ("alarms", [], "no alarms defined"),
]


@pytest.mark.parametrize(("path", "value", "phrase"), BREAKAGES)
def test_validate_rejects(config, path, value, phrase):
    problems = validate(_break(config, path, value))
    assert problems, f"breaking {path} produced no complaint"
    assert any(phrase in p for p in problems), (
        f"breaking {path} complained, but not about {phrase!r}: {problems}"
    )


def test_a_typo_in_a_metric_name_is_caught_rather_than_deployed(config):
    """
    The one that would otherwise reach production looking healthy. AWS accepts
    an alarm on a metric nothing publishes; it sits in INSUFFICIENT_DATA, which
    a console renders as grey and a human reads as quiet.
    """
    broken = _break(config, "metric_filters.0.metric_name", "HandlerEscapedTypo")
    problems = validate(broken)
    assert any("no metric filter in this config publishes" in p for p in problems)


def test_duplicate_alarm_names_are_caught(config):
    broken = deepcopy(config)
    broken["alarms"].append(deepcopy(broken["alarms"][0]))
    assert any("duplicate name" in p for p in validate(broken))


# --------------------------------------------- the config against the CODE


def _json_selector(pattern: str) -> tuple[str, str]:
    """
    Parse the one CloudWatch filter form used here: `{ $.field = "value" }`.

    Deliberately narrow. A general implementation would be a second thing to
    get wrong, and `validate()` already requires the pattern to be a JSON
    selector, so anything this cannot parse is a config the validator should
    have rejected.
    """
    match = re.fullmatch(r'\s*\{\s*\$\.([\w.]+)\s*=\s*"([^"]*)"\s*\}\s*', pattern)
    assert match, f"unsupported filter pattern for this test: {pattern!r}"
    return match.group(1), match.group(2)


def _filter_for(config: dict, metric_name: str) -> dict:
    """
    The filter publishing a named metric.

    BY NAME, NOT BY INDEX. These tests read `metric_filters[0]` until
    2026-08-31, which was correct while there was one filter and became a test
    of whichever filter happened to be first the moment there were two.
    """
    matches = [f for f in config["metric_filters"] if f["metric_name"] == metric_name]
    assert len(matches) == 1, f"expected exactly one filter publishing {metric_name}"
    return matches[0]


@pytest.fixture
def log_stream():
    """
    Rebind the Powertools handler to a buffer, so the log line can be read
    back as CloudWatch would receive it. Same reasoning as the fixture of the
    same name in test_observability.py, which explains it at length: the
    handler bound its stream at import, and that is not the object pytest's
    capture replaces.
    """
    import logging

    from src.observability.powertools import logger

    handler = logger.registered_handler
    assert isinstance(handler, logging.StreamHandler)
    previous = handler.stream
    buffer = io.StringIO()
    handler.setStream(buffer)
    try:
        yield buffer
    finally:
        handler.setStream(previous)


def test_the_filter_matches_the_line_the_handler_actually_emits(config, log_stream, monkeypatch):
    """
    THE TEST THIS FILE EXISTS FOR.

    The alarm is a string in JSON pointing at a string in Python. Nothing in
    AWS, and nothing in this repo until now, held the two together — rename the
    event in src/handler.py and the filter deploys clean and matches nothing,
    which looks exactly like a service that never crashes.

    So: drive a real turn into the last-resort path, take what Powertools
    wrote, and apply the shipped filter pattern to it.
    """
    import src.handler as handler_mod
    from src.handler import lambda_handler

    def boom(*_args, **_kwargs):
        raise RuntimeError("nobody predicted this")

    monkeypatch.setattr(handler_mod, "_observed_handler", boom)

    result = lambda_handler({"httpMethod": "POST", "body": "{}"})
    assert result["statusCode"] == 500

    records = [
        json.loads(line)
        for line in log_stream.getvalue().splitlines()
        if line.strip().startswith("{")
    ]
    assert records, "the last-resort path wrote no log line at all"

    field, expected = _json_selector(_filter_for(config, "HandlerEscaped")["pattern"])
    matched = [r for r in records if r.get(field) == expected]

    assert matched, (
        f"the metric filter looks for {field}={expected!r}, which no log line "
        f"from a real escaped exception contains. The alarm would never fire.\n"
        f"emitted: {[r.get(field) for r in records]}"
    )
    # And it is the error-level line, not something incidental that happens to
    # carry the same message field.
    assert matched[0]["level"] == "ERROR"


def test_the_filter_does_not_match_an_ordinary_turn(config, log_stream):
    """
    The other half of a useful alarm: it must be quiet when nothing is wrong.
    A pattern broad enough to match a normal turn is a pager that gets muted in
    a week.
    """
    import uuid

    from src.handler import lambda_handler

    unique = uuid.uuid4().hex[:8]
    result = lambda_handler(
        {
            "httpMethod": "POST",
            "body": json.dumps(
                {
                    "version": "1.0",
                    "session_id": f"sess-{unique}",
                    "turn_id": f"turn-{unique}",
                    "message": "cheapest butter",
                }
            ),
        }
    )
    assert result["statusCode"] == 200

    records = [
        json.loads(line)
        for line in log_stream.getvalue().splitlines()
        if line.strip().startswith("{")
    ]
    field, expected = _json_selector(_filter_for(config, "HandlerEscaped")["pattern"])
    assert not [r for r in records if r.get(field) == expected], (
        "the metric filter matched a successful turn"
    )


def test_every_log_group_names_a_function_this_repo_deploys(config):
    """
    A metric filter on a log group that does not exist is created happily and
    matches nothing. This cannot verify the deployment, but it can verify the
    name is shaped like one of the two functions this repository builds.

    IT USED TO READ `metric_filters[0]` AND ONLY THAT. With one filter that was
    the whole config; the moment a second arrived — the ingestion reject filter,
    2026-08-31 — the test kept passing while checking half of what its name
    claims. That is the shape this repository keeps finding, so it is asserted
    over every filter and the count is asserted too: a loop over an empty list
    passes just as quietly.
    """
    from src.observability.powertools import SERVICE_NAME

    filters = config["metric_filters"]
    assert len(filters) >= 2, "the scan lost a filter, or this test lost its input"

    env = config["environment"]
    functions = set()
    for f in filters:
        log_group = f["log_group"]
        assert log_group.startswith("/aws/lambda/"), f"{f['name']}: {log_group}"
        function = log_group.removeprefix("/aws/lambda/")
        assert function.startswith("grocery-"), (
            f"filter {f['name']} attaches to {log_group}, which is not one of "
            f"this project's functions; it would match nothing, forever"
        )
        assert function.endswith(f"-{env}"), f"{f['name']}: {log_group} is not the {env} stage"
        functions.add(function)

    assert f"{SERVICE_NAME}-{env}" in functions, (
        f"nothing watches the {SERVICE_NAME} log group any more; the "
        f"handler-escaped filter is the one alarm that reports WHAT broke"
    )


# ---------------------------------------------------------------- EMF metrics


def test_declared_emf_metrics_match_the_code(config) -> None:
    """
    `emf_metrics` in the config must be exactly the METRIC_ constants.

    An alarm can watch either a metric filter (declared in this config) or an
    EMF metric the application emits. Only the first is verifiable from the
    config alone, so the second is DECLARED -- and this test is what makes the
    declaration worth anything. Renaming a metric in code without updating the
    config now fails the build, instead of leaving an alarm watching a name
    nothing writes, which looks exactly like a healthy service.
    """
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src/observability/base.py").read_text(
        encoding="utf-8"
    )
    in_code = set(re.findall(r'^METRIC_\w+ = "(\w+)"', source, re.M))
    declared = set(config["emf_metrics"])

    assert declared == in_code, (
        f"config/alarms.json emf_metrics is out of step with the code. "
        f"only in code: {sorted(in_code - declared)}; "
        f"only in config: {sorted(declared - in_code)}"
    )


def test_every_alarm_watches_a_metric_something_publishes(config) -> None:
    """
    The check that stops an alarm sitting calm on a metric nothing writes.

    AWS is happy to create it; it just reports INSUFFICIENT_DATA forever, which
    is indistinguishable from healthy at a glance.
    """
    cfg = config
    publishable = {(f["namespace"], f["metric_name"]) for f in cfg["metric_filters"]}
    publishable |= {("GroceryOrchestrator", m) for m in cfg["emf_metrics"]}

    for alarm in cfg["alarms"]:
        namespace = alarm["namespace"]
        if namespace.startswith("AWS/"):
            continue  # published by the service, not by us
        assert (namespace, alarm["metric_name"]) in publishable, alarm["name"]


def test_the_internal_error_alarm_is_dimensioned_on_the_code(config) -> None:
    """
    An honest refusal must never page anyone.

    BUDGET_INFEASIBLE and NO_DATA are successful outcomes of the product doing
    its job, and they share the TurnError metric with INTERNAL_ERROR. Without
    the `code` dimension this alarm would fire on a shopper asking for a plan
    that genuinely does not fit their budget.
    """
    alarm = next(a for a in config["alarms"] if a["name"].endswith("internal-error-dev"))
    assert alarm["dimensions"].get("code") == "INTERNAL_ERROR"


def test_the_history_failure_filter_matches_the_line_ingestion_emits(config, capsys, monkeypatch):
    """
    The `IngestionHistoryWriteFailed` filter must match what `_append_history`
    actually prints — not what this config says it prints.

    Same pairing as the HandlerEscaped test above, and it exists for the same
    reason: a metric filter and the log line it binds to live in two files, and
    a rename in either one produces an alarm that can never fire. An alarm that
    cannot fire is indistinguishable from a healthy service, which is the
    failure this whole file is about.

    Driven through the REAL function with a boto3 that raises, rather than by
    asserting on the constant. Asserting `HISTORY_FAILED_LOG_MESSAGE ==
    "ingestion_history_write_failed"` would pass even if `_append_history`
    stopped printing the line, stopped catching the exception, or printed a
    different key — all three of which are the actual failure.
    """
    from ingestion import handler as h

    def _boom(*args, **kwargs):
        raise RuntimeError("AccessDeniedException: no permission on the history table")

    monkeypatch.setattr(h.boto3, "resource", _boom)

    written = h._append_history("paknsave", [{"store_key": "s#a", "product_key": "p"}])

    assert written == 0, "a failed history append must report zero rows written"

    printed = [
        line for line in capsys.readouterr().out.splitlines() if line.strip().startswith("{")
    ]
    records = [json.loads(line) for line in printed]

    field, expected = _json_selector(_filter_for(config, "IngestionHistoryWriteFailed")["pattern"])
    matched = [r for r in records if r.get(field) == expected]

    assert matched, (
        f"the metric filter looks for {field}={expected!r}, which no log line "
        f"from _append_history carries. Lines seen: {records}"
    )
    # The alarm is only actionable if the line says which table and which error.
    assert matched[0]["table"] == h.HISTORY_TABLE
    assert matched[0]["error"] == "RuntimeError"


def test_a_history_failure_does_not_fail_the_refresh(config):
    """
    Every ingestion alarm must be reachable, and this one is only reachable
    because the failure DEGRADES.

    If `_append_history` re-raised, a history failure would surface as
    `ExecutionsFailed` and `IngestionHistoryWriteFailed` would never publish a
    datapoint — an alarm on a metric nothing emits, which `_coverage_note`
    already refuses to add. The two ingestion alarms describe two different
    facts, and this is the code property that keeps them different.
    """
    names = {a["name"] for a in config["alarms"]}
    assert "grocery-ingestion-failed-dev" in names
    assert "grocery-ingestion-history-write-failed-dev" in names

    from ingestion.handler import _append_history

    # Does not raise. That IS the assertion; the metric depends on it.
    assert _append_history("paknsave", []) == 0
