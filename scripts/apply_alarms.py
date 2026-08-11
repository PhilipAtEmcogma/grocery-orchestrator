"""
Create or update the CloudWatch alarms from config/alarms.json.

The two alarms design.md 12.6 calls for: a metric filter on the
`handler_escaped` log line, and the API Gateway's own `5XXError` metric. They
overlap deliberately — the log filter says what broke and where, the 5xx alarm
fires even if logging is the thing that broke.

    python scripts/apply_alarms.py --dry-run     # validate, no AWS call
    python scripts/apply_alarms.py               # create or update

Config is the source of truth, not the console, for the same reasons the
guardrail policy is (design.md 10.1): reviewable in a pull request, diffable
over time, reproducible in another account.

WHAT validate() IS FOR. Not schema-checking — catching the configurations that
produce an alarm which *silently does nothing*, which is the alarm equivalent
of the guardrail that looks enabled and classifies nothing. Every check below
corresponds to a way of ending up with a green console and no coverage:

  * an alarm watching a metric no filter publishes — a typo in a metric name
    is not an error anywhere in AWS, it is an alarm that never leaves
    INSUFFICIENT_DATA
  * `GreaterThanThreshold` with threshold 1 — needs TWO crashes to fire, so
    the first one, the one you wanted, is silent
  * `Average` instead of `Sum` — with 0-filled datapoints a single error
    averages below 1 across the period and never trips
  * `datapoints_to_alarm` above 1 — a single occurrence must fire; these are
    bug alarms, not capacity alarms
  * `treatMissingData: breaching` — pages on an idle system until someone
    mutes it, and a muted alarm is worse than none
  * a substring metric-filter pattern instead of a JSON selector — matches any
    log line containing the text and pages on a false positive
  * no notification topic — an alarm with no action is a dashboard widget
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

CONFIG = Path(__file__).resolve().parent.parent / "config" / "alarms.json"
REGION = "ap-southeast-2"

# A count alarm has to fire on the first occurrence. These are the settings
# that make that true; anything else is a slower alarm than the config claims.
REQUIRED_STATISTIC = "Sum"
REQUIRED_COMPARISON = "GreaterThanOrEqualToThreshold"


def _strip(obj: Any) -> Any:
    """
    Drop our `_`-prefixed annotations; the AWS APIs reject unknown keys.

    `Any` in and out, deliberately: this is a shape-preserving walk over
    arbitrary decoded JSON, so a narrower signature would be a fiction. The
    caller re-establishes the type it needs.
    """
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip(x) for x in obj]
    return obj


def load_config(path: Path = CONFIG) -> dict:
    stripped = _strip(json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(stripped, dict):
        raise TypeError(f"{path} must contain a JSON object, got {type(stripped).__name__}")
    return stripped


def validate(cfg: dict) -> list[str]:
    """Every problem found, not just the first — see the module docstring."""
    problems: list[str] = []

    filters = cfg.get("metric_filters", [])
    alarms = cfg.get("alarms", [])

    if not alarms:
        problems.append("no alarms defined")

    topic = cfg.get("notification", {}).get("topic_name", "")
    if not topic:
        problems.append(
            "notification.topic_name is empty — an alarm with no action is a "
            "dashboard widget, not an alarm"
        )

    # What the filters actually publish, to check the alarms against.
    published = {(f.get("namespace"), f.get("metric_name")) for f in filters}

    for f in filters:
        name = f.get("name", "<unnamed>")
        pattern = f.get("pattern", "")
        if not (pattern.strip().startswith("{") and "$." in pattern):
            problems.append(
                f"filter '{name}': pattern is not a JSON selector. A substring "
                f"pattern matches any log line containing the text"
            )
        if f.get("default_value") != 0:
            problems.append(
                f"filter '{name}': default_value must be 0, or the metric has "
                f"datapoints only on a match and OK never means 'checked'"
            )

    seen: set[str] = set()
    for alarm in alarms:
        name = alarm.get("name", "<unnamed>")
        if name in seen:
            problems.append(f"alarm '{name}': duplicate name")
        seen.add(name)

        namespace = alarm.get("namespace")
        metric = alarm.get("metric_name")

        # The check worth having. AWS is perfectly happy to alarm on a metric
        # nothing ever writes; it just sits in INSUFFICIENT_DATA looking calm.
        # Only our own namespace can be verified — AWS/* metrics are published
        # by the service, not by us.
        if namespace and not namespace.startswith("AWS/"):
            if (namespace, metric) not in published:
                problems.append(
                    f"alarm '{name}': watches {namespace}/{metric}, which no "
                    f"metric filter in this config publishes"
                )

        if alarm.get("statistic") != REQUIRED_STATISTIC:
            problems.append(
                f"alarm '{name}': statistic is {alarm.get('statistic')!r}, not "
                f"'Sum' — an average over 0-filled datapoints hides a single event"
            )

        threshold = alarm.get("threshold")
        comparison = alarm.get("comparison_operator")
        if comparison != REQUIRED_COMPARISON:
            problems.append(
                f"alarm '{name}': comparison is {comparison!r}. With threshold "
                f"{threshold}, '{REQUIRED_COMPARISON}' is what fires on the first "
                f"occurrence; 'GreaterThanThreshold' needs two"
            )
        if not isinstance(threshold, int | float) or threshold < 1:
            problems.append(f"alarm '{name}': threshold must be >= 1, got {threshold!r}")

        if alarm.get("datapoints_to_alarm") != 1:
            problems.append(
                f"alarm '{name}': datapoints_to_alarm is "
                f"{alarm.get('datapoints_to_alarm')!r}, not 1 — these fire on a "
                f"single occurrence by design"
            )
        if alarm.get("evaluation_periods") != 1:
            problems.append(
                f"alarm '{name}': evaluation_periods is "
                f"{alarm.get('evaluation_periods')!r}, not 1"
            )

        missing = alarm.get("treat_missing_data")
        if missing == "breaching":
            problems.append(
                f"alarm '{name}': treat_missing_data 'breaching' pages on an idle "
                f"system, and a muted alarm is worse than no alarm"
            )
        elif missing != "notBreaching":
            problems.append(
                f"alarm '{name}': treat_missing_data is {missing!r}; "
                f"'notBreaching' is what makes a quiet period an all-clear "
                f"rather than INSUFFICIENT_DATA"
            )

        period = alarm.get("period")
        if not isinstance(period, int) or period <= 0 or period % 60:
            problems.append(f"alarm '{name}': period must be a positive multiple of 60")

    return problems


def summarise(cfg: dict) -> None:
    print(f"Config valid: {len(cfg['alarms'])} alarms, "
          f"{len(cfg.get('metric_filters', []))} metric filters")
    print(f"  region       {cfg['region']}")
    print(f"  topic        {cfg['notification']['topic_name']}")
    for f in cfg.get("metric_filters", []):
        print(f"  filter       {f['name']}")
        print(f"               {f['log_group']}  ->  "
              f"{f['namespace']}/{f['metric_name']}")
    for alarm in cfg["alarms"]:
        dims = ", ".join(f"{k}={v}" for k, v in alarm.get("dimensions", {}).items())
        print(f"  alarm        {alarm['name']}")
        print(f"               {alarm['namespace']}/{alarm['metric_name']}"
              f"{' [' + dims + ']' if dims else ''} "
              f"{alarm['statistic']} >= {alarm['threshold']} "
              f"over {alarm['period']}s")


def apply(cfg: dict, region: str) -> int:
    import boto3
    from botocore.exceptions import ClientError

    sns = boto3.client("sns", region_name=region)
    logs = boto3.client("logs", region_name=region)
    cw = boto3.client("cloudwatch", region_name=region)

    tags = cfg.get("tags", [])

    try:
        # create_topic is idempotent: same name returns the existing ARN.
        topic_arn = sns.create_topic(
            Name=cfg["notification"]["topic_name"],
            Tags=[{"Key": t["key"], "Value": t["value"]} for t in tags],
        )["TopicArn"]
        print(f"\nTopic  {topic_arn}")

        for f in cfg.get("metric_filters", []):
            logs.put_metric_filter(
                logGroupName=f["log_group"],
                filterName=f["name"],
                filterPattern=f["pattern"],
                metricTransformations=[
                    {
                        "metricName": f["metric_name"],
                        "metricNamespace": f["namespace"],
                        "metricValue": f["metric_value"],
                        "defaultValue": float(f["default_value"]),
                    }
                ],
            )
            print(f"Filter {f['name']}")

        for alarm in cfg["alarms"]:
            cw.put_metric_alarm(
                AlarmName=alarm["name"],
                AlarmDescription=alarm["description"],
                Namespace=alarm["namespace"],
                MetricName=alarm["metric_name"],
                Dimensions=[
                    {"Name": k, "Value": v}
                    for k, v in alarm.get("dimensions", {}).items()
                ],
                Statistic=alarm["statistic"],
                Period=alarm["period"],
                EvaluationPeriods=alarm["evaluation_periods"],
                DatapointsToAlarm=alarm["datapoints_to_alarm"],
                Threshold=float(alarm["threshold"]),
                ComparisonOperator=alarm["comparison_operator"],
                TreatMissingData=alarm["treat_missing_data"],
                ActionsEnabled=True,
                AlarmActions=[topic_arn],
                # Recovery too: an alarm you only hear going off leaves you
                # guessing whether it ever came back.
                OKActions=[topic_arn],
                Tags=[{"Key": t["key"], "Value": t["value"]} for t in tags],
            )
            print(f"Alarm  {alarm['name']}")

        # The failure one level out from "an alarm with no action": a topic
        # with no subscriber. Everything above succeeds and nobody is told.
        subs = sns.list_subscriptions_by_topic(TopicArn=topic_arn).get(
            "Subscriptions", []
        )
        confirmed = [s for s in subs if not s.get("SubscriptionArn", "").endswith(
            "PendingConfirmation"
        )]
        if not confirmed:
            print(
                f"\nWARNING: {topic_arn} has no confirmed subscribers. "
                f"The alarms are live and nobody will hear them.\n"
                f"  aws sns subscribe --topic-arn {topic_arn} "
                f"--protocol email --notification-endpoint you@example.com\n"
                f"  ...then confirm the email; a pending subscription is not one."
            )
            return 1
        print(f"\n{len(confirmed)} confirmed subscriber(s).")

    except ClientError as exc:
        print(f"\nAWS call failed: {exc}")
        return 1

    return 0


def main() -> int:
    # No description=__doc__: argparse would print this module's
    # docstring, em-dashes and all, into a cp1252 console.
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--region", default=None)
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args()

    cfg = load_config(args.config)

    problems = validate(cfg)
    if problems:
        print("Validation failed:")
        for p in problems:
            print(f"  - {p}")
        return 1

    summarise(cfg)

    if args.dry_run:
        print("\nDry run - no AWS calls made.")
        return 0

    return apply(cfg, args.region or cfg.get("region", REGION))


if __name__ == "__main__":
    raise SystemExit(main())
