/**
 * ObservabilityStack (Pilot Task 12) — make the pilot operable, on BOTH planes.
 *
 * Built from `config/alarms.json`, the same file `scripts/apply_alarms.py`
 * applies, for the reason `service-stack.ts` reads the IAM JSON: porting policy
 * into TypeScript would create the second source of truth the migration exists
 * to remove.
 *
 * WHY THIS STACK IS THE NEXT ONE. Two service planes are running and the cutover
 * is deferred (docs/ARCHITECTURE.md §3m). This is the stack that makes the CDK
 * plane safe to cut over TO, and until it exists the dual-plane arrangement
 * means part of what is running is unwatched.
 *
 * HOW MUCH WAS UNWATCHED — a correction to the second audit, which said the CDK
 * plane is "unalarmed, undashboarded". It is more precise and slightly less bad
 * than that, and the precise version is the one worth acting on:
 *
 *   - SIX of the nine alarms already cover both planes. They watch EMF metrics
 *     dimensioned on `service`, and `POWERTOOLS_SERVICE_NAME` is
 *     `grocery-orchestrator` on BOTH — the CDK Lambda does not suffix it. So a
 *     handler error or a latency breach on either plane fires the same alarm.
 *   - TWO were hand-made-only, and they are the two bound to a physical name:
 *     the API 5xx alarm (`ApiName = grocery-orchestrator-api-dev`) and the
 *     handler-escaped metric filter (`/aws/lambda/grocery-orchestrator-dev`).
 *     Those are created per plane below.
 *   - ONE is the ingestion reject filter, which has a single plane.
 *
 * AND THE SHARED DIMENSION IS ITSELF WORTH RECORDING. Six alarms covering both
 * planes sounds like good news and is half of one: because both emit
 * `service=grocery-orchestrator`, a metric cannot say WHICH plane produced it.
 * While dual-running, a latency spike on the unused CDK plane is
 * indistinguishable from one on the plane serving shoppers. Splitting the
 * dimension would fix that and would also split every historical series, so it
 * is deliberately NOT done here — the dual-run is temporary and the cutover is
 * the fix. If dual-running becomes permanent, this is the reason it should not.
 */
import * as fs from 'fs';
import * as cdk from 'aws-cdk-lib';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cwactions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as sns from 'aws-cdk-lib/aws-sns';
import { Construct } from 'constructs';
import { GroceryConfig } from './config';
import { ServiceStack } from './service-stack';

export interface ObservabilityStackProps extends cdk.StackProps {
  readonly cfg: GroceryConfig;
  readonly service: ServiceStack;
}

/** One deployed service plane, as the alarms have to name it. */
interface Plane {
  readonly label: string;
  readonly apiName: string;
  readonly logGroupName: string;
}

const COMPARATORS: Record<string, cloudwatch.ComparisonOperator> = {
  GreaterThanOrEqualToThreshold: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
  GreaterThanThreshold: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
  LessThanThreshold: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
  LessThanOrEqualToThreshold: cloudwatch.ComparisonOperator.LESS_THAN_OR_EQUAL_TO_THRESHOLD,
};

const MISSING_DATA: Record<string, cloudwatch.TreatMissingData> = {
  notBreaching: cloudwatch.TreatMissingData.NOT_BREACHING,
  breaching: cloudwatch.TreatMissingData.BREACHING,
  ignore: cloudwatch.TreatMissingData.IGNORE,
  missing: cloudwatch.TreatMissingData.MISSING,
};

export class ObservabilityStack extends cdk.Stack {
  public readonly topic: sns.Topic;
  public readonly artefacts: s3.Bucket;

  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id, props);
    const { cfg } = props;
    const n = cfg.names;
    const alarms = JSON.parse(fs.readFileSync(cfg.configFiles.alarms, 'utf-8'));

    // The two planes this account is running. `cfg.suffix` is '' after the
    // cutover, at which point they collapse to one and the dedupe below leaves
    // a single set of alarms — so retiring the hand-made plane needs no edit
    // here, which is the property that stops this list going stale.
    const planes: Plane[] = [
      {
        label: 'handmade',
        apiName: n.restApi,
        logGroupName: `/aws/lambda/${n.orchestratorFn}`,
      },
      {
        label: 'cdk',
        apiName: `${n.restApi}${cfg.suffix}`,
        logGroupName: `/aws/lambda/${n.orchestratorFn}${cfg.suffix}`,
      },
    ].filter((p, i, all) => all.findIndex((o) => o.apiName === p.apiName) === i);

    // ---------------------------------------------------------------- SNS

    // `config/alarms.json` refuses an alarm with no topic, because an alarm with
    // no action is a dashboard widget. SUBSCRIPTIONS ARE NOT DECLARED HERE and
    // that is deliberate: an SNS email subscription needs out-of-band
    // confirmation, so a declared one sits PendingConfirmation and reads as
    // subscribed. Added by hand, recorded in the runbook.
    this.topic = new sns.Topic(this, 'Alarms', {
      topicName: alarms.notification.topic_name,
      displayName: 'Smart Grocery orchestrator alarms',
    });

    // ------------------------------------------------------- metric filters

    for (const filter of alarms.metric_filters) {
      const isOrchestrator = filter.log_group.includes(n.orchestratorFn);
      // An orchestrator filter is created once per PLANE; anything else (the
      // ingestion reject filter) once, on the group the config names.
      const targets = isOrchestrator
        ? planes.map((p) => ({ suffix: p.label, logGroupName: p.logGroupName }))
        : [{ suffix: 'only', logGroupName: filter.log_group }];

      for (const target of targets) {
        const logGroup = logs.LogGroup.fromLogGroupName(
          this,
          `Lg-${filter.metric_name}-${target.suffix}`,
          target.logGroupName,
        );
        const [field, value] = parseJsonSelector(filter.pattern);
        new logs.MetricFilter(this, `Mf-${filter.metric_name}-${target.suffix}`, {
          logGroup,
          filterPattern: logs.FilterPattern.stringValue(field, '=', value),
          metricNamespace: filter.namespace,
          metricName: filter.metric_name,
          metricValue: filter.metric_value,
          // 0, NOT absent. Without it the metric has datapoints only on a
          // match, so the alarm sits in INSUFFICIENT_DATA forever and OK never
          // means "checked and fine" — it means "never looked". The config
          // validator refuses any other value.
          defaultValue: filter.default_value,
        });
      }
    }

    // -------------------------------------------------------------- alarms

    for (const spec of alarms.alarms) {
      const dimensions: Record<string, string> = { ...(spec.dimensions ?? {}) };
      // An alarm bound to a physical API name describes ONE plane, so it is
      // created once per plane. Everything else is dimensioned on `service`,
      // which both planes share — see the header on why that is a mixed
      // blessing rather than a win.
      const perPlane = 'ApiName' in dimensions;
      const targets = perPlane ? planes : [null];

      for (const plane of targets) {
        const dims = plane ? { ...dimensions, ApiName: plane.apiName } : dimensions;
        const suffix = plane ? `-${plane.label}` : '';
        const alarm = new cloudwatch.Alarm(this, `Alarm-${spec.metric_name}${suffix}`, {
          alarmName: plane ? `${spec.name}-${plane.label}` : spec.name,
          alarmDescription: spec.description,
          metric: new cloudwatch.Metric({
            namespace: spec.namespace,
            metricName: spec.metric_name,
            dimensionsMap: dims,
            statistic: spec.statistic,
            period: cdk.Duration.seconds(spec.period),
          }),
          threshold: spec.threshold,
          evaluationPeriods: spec.evaluation_periods,
          datapointsToAlarm: spec.datapoints_to_alarm,
          comparisonOperator: COMPARATORS[spec.comparison_operator],
          treatMissingData: MISSING_DATA[spec.treat_missing_data],
        });
        alarm.addAlarmAction(new cwactions.SnsAction(this.topic));
      }
    }

    // ----------------------------------------------------------- dashboard

    const emf = (metricName: string, statistic = 'Sum') =>
      new cloudwatch.Metric({
        namespace: 'GroceryOrchestrator',
        metricName,
        dimensionsMap: { service: 'grocery-orchestrator' },
        statistic,
        period: cdk.Duration.minutes(5),
      });

    const dashboard = new cloudwatch.Dashboard(this, 'Dashboard', {
      dashboardName: `${n.orchestratorFn}${cfg.suffix}`,
    });
    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Turns and errors',
        left: [emf('TurnsProcessed'), emf('TurnError'), emf('TurnWithoutContent')],
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'Latency (p95)',
        left: [emf('TurnLatency', 'p95'), emf('ModelLatency', 'p95'), emf('RetrievalLatency', 'p95')],
        width: 12,
      }),
    );
    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Tokens — the Bedrock bill, before it is a bill',
        left: [emf('InputTokens'), emf('OutputTokens'), emf('CacheReadTokens')],
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'Repair, guardrail, idempotency',
        left: [
          emf('RepairAttempts'),
          emf('RepairExhausted'),
          emf('GuardrailIntervened'),
          emf('IdempotencyUnavailable'),
        ],
        width: 12,
      }),
    );

    // -------------------------------------------------------------- budget

    // Two budgets are free. This is the backstop on Bedrock spend, and it is
    // the control that does not depend on any of our own instrumentation
    // working — the same reason the 5xx alarm watches the gateway's metric
    // rather than one we publish.
    new budgets.CfnBudget(this, 'MonthlyBudget', {
      budget: {
        budgetName: `${n.orchestratorFn}${cfg.suffix}-monthly`,
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: { amount: 25, unit: 'USD' },
      },
      notificationsWithSubscribers: [80, 100].map((threshold) => ({
        notification: {
          notificationType: 'ACTUAL',
          comparisonOperator: 'GREATER_THAN',
          threshold,
          thresholdType: 'PERCENTAGE',
        },
        subscribers: [{ subscriptionType: 'SNS', address: this.topic.topicArn }],
      })),
    });

    // ------------------------------------------------------------ artefacts

    // Eval results, latency baselines and review snapshots live in Markdown
    // today, which means a measurement's provenance is a commit message. One
    // encrypted, versioned bucket with public access blocked. RETAIN, because
    // the point of keeping baselines is that they outlive the stack that made
    // them.
    this.artefacts = new s3.Bucket(this, 'Artefacts', {
      bucketName: `${n.orchestratorFn}${cfg.suffix}-artefacts-${this.account}`,
      encryption: s3.BucketEncryption.S3_MANAGED,
      versioned: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // -------------------------------------------------------------- outputs

    new cdk.CfnOutput(this, 'AlarmTopicArn', {
      value: this.topic.topicArn,
      description: 'Subscribe by hand: email needs out-of-band confirmation.',
    });
    new cdk.CfnOutput(this, 'WatchedPlanes', {
      value: planes.map((p) => `${p.label}:${p.apiName}`).join(', '),
      description: 'Every API plane the 5xx alarm covers. One entry after the cutover.',
    });
    new cdk.CfnOutput(this, 'ArtefactBucket', { value: this.artefacts.bucketName });
  }
}

/**
 * `{ $.message = "handler_escaped" }` -> ['$.message', 'handler_escaped'].
 *
 * Narrow on purpose, and it THROWS rather than falling back to a substring
 * pattern. A substring filter matches any log line containing the text — an
 * exception message quoting it, a test fixture, a future log line about the
 * alarm itself — and each of those is a page at 3am for nothing. Failing the
 * synth is the cheap outcome; silently widening a filter is the expensive one.
 */
function parseJsonSelector(pattern: string): [string, string] {
  const match = /^\s*\{\s*(\$\.[\w.]+)\s*=\s*"([^"]*)"\s*\}\s*$/.exec(pattern);
  if (!match) {
    throw new Error(
      `Metric filter pattern ${pattern} is not the JSON selector form ` +
        `{ $.field = "value" }. config/alarms.json requires a JSON selector, and a ` +
        `substring pattern matches any line containing the text.`,
    );
  }
  return [match[1], match[2]];
}
