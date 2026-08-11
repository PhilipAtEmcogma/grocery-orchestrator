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


def test_the_filter_matches_the_line_the_handler_actually_emits(
    config, log_stream, monkeypatch
):
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

    field, expected = _json_selector(config["metric_filters"][0]["pattern"])
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
    field, expected = _json_selector(config["metric_filters"][0]["pattern"])
    assert not [r for r in records if r.get(field) == expected], (
        "the metric filter matched a successful turn"
    )


def test_the_log_group_names_the_deployed_function(config):
    """
    A metric filter on a log group that does not exist is created happily and
    matches nothing. This cannot verify the deployment, but it can verify the
    two halves of the name agree with the service the code identifies as.
    """
    from src.observability.powertools import SERVICE_NAME

    log_group = config["metric_filters"][0]["log_group"]
    assert log_group.startswith("/aws/lambda/"), log_group
    function = log_group.removeprefix("/aws/lambda/")
    assert function.startswith(SERVICE_NAME), (
        f"log group {log_group} does not name the {SERVICE_NAME} function; "
        f"the filter would attach to the wrong log group or none"
    )
    assert function.endswith(f"-{config['environment']}")
