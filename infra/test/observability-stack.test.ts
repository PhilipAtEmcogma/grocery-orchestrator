/**
 * The ObservabilityStack watches everything that is running.
 *
 * The point of this stack is COVERAGE, so coverage is what is asserted. An
 * alarm that exists is not evidence; an alarm bound to a metric nothing
 * publishes, or to a plane nobody is serving from, is the exact failure
 * `config/alarms.json` spends four comments warning about — it deploys clean,
 * renders grey in a console, and reads as a healthy service.
 *
 * The second audit's Finding 3 is the reason this suite leads with the
 * dual-plane assertions: two public, unauthenticated, Bedrock-invoking REST
 * APIs exist, and the alarms bound to a physical API name covered one.
 */
import * as fs from 'fs';
import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { loadConfig } from '../lib/config';
import { StatefulStack } from '../lib/stateful-stack';
import { ServiceStack } from '../lib/service-stack';
import { ObservabilityStack } from '../lib/observability-stack';

const ALARMS = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '..', '..', 'config', 'alarms.json'), 'utf-8'),
);

function build(stage = 'dev') {
  const app = new cdk.App();
  const cfg = loadConfig(stage);
  const env = { account: '111111111111', region: 'ap-southeast-2' };
  const stateful = new StatefulStack(app, 'Stateful', { env, cfg });
  const service = new ServiceStack(app, 'Service', { env, cfg, tables: stateful });
  const obs = new ObservabilityStack(app, 'Obs', { env, cfg, service });
  return { cfg, template: Template.fromStack(obs) };
}

describe('ObservabilityStack', () => {
  const { cfg, template } = build();

  const alarms = () => Object.values(template.findResources('AWS::CloudWatch::Alarm'));
  const filters = () => Object.values(template.findResources('AWS::Logs::MetricFilter'));
  const props = (r: unknown) => (r as any).Properties;

  // ------------------------------------------------------------ dual plane

  it('the API 5xx alarm covers BOTH service planes', () => {
    // The finding this stack exists to close. Until 2026-08-31 the 5xx alarm
    // was bound to `ApiName = grocery-orchestrator-api-dev` alone, so the CDK
    // plane -- equally public and equally able to invoke Bedrock -- had nothing
    // watching its gateway.
    const watched = alarms()
      .filter((a) => props(a).MetricName === '5XXError')
      .flatMap((a) =>
        (props(a).Dimensions ?? [])
          .filter((d: any) => d.Name === 'ApiName')
          .map((d: any) => d.Value as string),
      );
    expect([...watched].sort()).toEqual(
      [`${cfg.names.restApi}`, `${cfg.names.restApi}${cfg.suffix}`].sort(),
    );
  });

  it('the handler-escaped filter is attached to BOTH orchestrator log groups', () => {
    const groups = filters()
      .filter((f) => props(f).MetricTransformations?.[0]?.MetricName === 'HandlerEscaped')
      .map((f) => props(f).LogGroupName as string);
    expect([...groups].sort()).toEqual(
      [
        `/aws/lambda/${cfg.names.orchestratorFn}`,
        `/aws/lambda/${cfg.names.orchestratorFn}${cfg.suffix}`,
      ].sort(),
    );
  });

  it('collapses to one set of alarms once the hand-made plane is retired', () => {
    // The cutover sets NAME_SUFFIX=''. The two planes become one, and this
    // stack must not then declare the same alarm twice -- CloudFormation would
    // reject the duplicate names and the deploy that retires the old plane
    // would be the deploy that fails.
    const prior = process.env.NAME_SUFFIX;
    try {
      process.env.NAME_SUFFIX = '';
      const after = build();
      const names = Object.values(after.template.findResources('AWS::CloudWatch::Alarm')).map(
        (a) => props(a).AlarmName as string,
      );
      expect(new Set(names).size).toBe(names.length);
      expect(names.filter((n) => n.includes('5xx'))).toHaveLength(1);
    } finally {
      prior === undefined ? delete process.env.NAME_SUFFIX : (process.env.NAME_SUFFIX = prior);
    }
  });

  // --------------------------------------------------- alarms are alarms

  it('every alarm in config/alarms.json is declared', () => {
    const declared = new Set(alarms().map((a) => props(a).MetricName as string));
    for (const spec of ALARMS.alarms) {
      expect(declared).toContain(spec.metric_name);
    }
  });

  it('every alarm has an action, because an alarm with none is a dashboard widget', () => {
    expect(alarms().length).toBeGreaterThan(0);
    for (const alarm of alarms()) {
      expect(props(alarm).AlarmActions ?? []).not.toHaveLength(0);
    }
  });

  it('no alarm treats missing data as breaching', () => {
    // An idle system is not a broken one. `notBreaching` is what makes OK mean
    // "checked and fine" on a service that scales to zero.
    for (const alarm of alarms()) {
      expect(props(alarm).TreatMissingData).not.toBe('breaching');
    }
  });

  it('every metric filter 0-fills, so a quiet period is an affirmative all-clear', () => {
    expect(filters().length).toBeGreaterThan(0);
    for (const filter of filters()) {
      expect(props(filter).MetricTransformations[0].DefaultValue).toBe(0);
    }
  });

  it('every metric filter uses a JSON selector, never a substring', () => {
    // A substring pattern matches any line containing the text -- an exception
    // message quoting it, a test fixture, a log line about the alarm itself.
    for (const filter of filters()) {
      expect(props(filter).FilterPattern).toMatch(/^\{\s*\$\./);
    }
  });

  it('the ingestion reject filter watches the ingestion log group, not the orchestrator', () => {
    const [rejects] = filters().filter(
      (f) => props(f).MetricTransformations?.[0]?.MetricName === 'IngestionRowRejected',
    );
    expect(rejects).toBeDefined();
    expect(props(rejects).LogGroupName).toContain('grocery-ingestion');
  });

  // ------------------------------------------------------------- artefacts

  it('the artefact bucket is encrypted, versioned, private and retained', () => {
    const [bucket] = Object.values(template.findResources('AWS::S3::Bucket'));
    expect(bucket).toBeDefined();
    const p = props(bucket);
    expect(p.BucketEncryption).toBeDefined();
    expect(p.VersioningConfiguration).toEqual({ Status: 'Enabled' });
    expect(p.PublicAccessBlockConfiguration).toEqual({
      BlockPublicAcls: true,
      BlockPublicPolicy: true,
      IgnorePublicAcls: true,
      RestrictPublicBuckets: true,
    });
    // RETAIN: the point of keeping baselines is that they outlive the stack.
    expect((bucket as any).DeletionPolicy).toBe('Retain');
  });

  it('every artefact prefix has a lifecycle rule that expires OLD VERSIONS', () => {
    // Versioning is what makes the restore drill possible and what makes the
    // bucket grow without limit: every overwrite keeps the copy it replaced,
    // forever, unless something deletes it. A versioned bucket with no
    // noncurrent-version rule is a bill that only goes up.
    const [bucket] = Object.values(template.findResources('AWS::S3::Bucket'));
    const rules = props(bucket).LifecycleConfiguration?.Rules ?? [];

    expect(rules.length).toBeGreaterThanOrEqual(4);
    for (const prefix of ['datasets/', 'evaluations/', 'reviews/', 'baselines/']) {
      const rule = rules.find((r: any) => r.Prefix === prefix);
      expect(rule).toBeDefined();
      expect(rule.Status).toBe('Enabled');
      expect(rule.NoncurrentVersionExpiration?.NoncurrentDays).toBeGreaterThan(0);
      // An abandoned multipart upload is billed storage that appears in no
      // listing, so it is the one thing here nobody would ever notice.
      expect(rule.AbortIncompleteMultipartUpload?.DaysAfterInitiation).toBeGreaterThan(0);
    }
  });

  it('no lifecycle rule expires a CURRENT artefact version', () => {
    // The bucket exists so measurements outlive the commit that made them. A
    // rule that quietly deleted the live copy after N days would leave an
    // absence that reads like the measurement was never taken -- worse than
    // not storing it, because nobody would know to look.
    const [bucket] = Object.values(template.findResources('AWS::S3::Bucket'));
    for (const rule of props(bucket).LifecycleConfiguration?.Rules ?? []) {
      expect(rule.ExpirationInDays).toBeUndefined();
      expect(rule.ExpirationDate).toBeUndefined();
    }
  });

  it('two alarms may watch one metric with different dimensions', () => {
    // THIS IS A REGRESSION TEST FOR A REAL SYNTH FAILURE (2026-09-06). The
    // alarm construct id was derived from the METRIC name, which assumed one
    // alarm per metric. Adding the stale-data alarm -- TurnError dimensioned
    // code=STALE_DATA, beside the internal-error alarm dimensioned
    // code=INTERNAL_ERROR -- broke `cdk synth` outright with "There is already
    // a Construct with name 'Alarm-TurnError'".
    //
    // Dimensioning one metric several ways is the NORMAL shape for this
    // config: it is how an honest refusal is told apart from a fault. So the
    // suite should hold that shape rather than the single-alarm assumption.
    const alarms = Object.values(template.findResources('AWS::CloudWatch::Alarm'));
    const onTurnError = alarms.filter((a) => props(a).MetricName === 'TurnError');

    expect(onTurnError.length).toBeGreaterThanOrEqual(2);
    const codes = onTurnError
      .map((a) => props(a).Dimensions?.find((d: any) => d.Name === 'code')?.Value)
      .sort();
    expect(codes).toEqual(['INTERNAL_ERROR', 'STALE_DATA']);
  });

  it('the throttle alarm watches the total, not one model', () => {
    // A quota is shared across models and tasks. Binding this alarm to one
    // dimension pair would leave every other pair unwatched while reading as
    // coverage -- the opposite choice from the stale-data alarm above, and
    // deliberately so.
    const alarms = Object.values(template.findResources('AWS::CloudWatch::Alarm'));
    const throttle = alarms.find((a) => props(a).MetricName === 'ModelThrottled');

    expect(throttle).toBeDefined();
    expect(props(throttle).Dimensions ?? []).toEqual([]);
  });

  it('a budget exists and notifies the alarm topic', () => {
    const [budget] = Object.values(template.findResources('AWS::Budgets::Budget'));
    expect(budget).toBeDefined();
    const notifications = props(budget).NotificationsWithSubscribers;
    expect(notifications.length).toBeGreaterThanOrEqual(2);
    for (const n of notifications) {
      expect(n.Subscribers[0].SubscriptionType).toBe('SNS');
    }
  });

  it('declares no SNS subscription, because an unconfirmed one reads as subscribed', () => {
    // Email subscriptions need out-of-band confirmation. A declared one sits in
    // PendingConfirmation and looks, in a console and in a template, exactly
    // like somebody who would be paged.
    expect(template.findResources('AWS::SNS::Subscription')).toEqual({});
  });
});
