/**
 * The security invariants of the deployed service plane, as executable checks.
 *
 * STATUS: RUNNING. It ran for the first time on 2026-08-31 and immediately
 * found two IAM regressions in a stack that was already deployed as
 * `Grocery-Service-dev`. That is the whole argument for this file.
 *
 * WHAT IT USED TO BE, AND WHY THAT MATTERED. Until 2026-08-31 this suite was
 * `describe.skip(...)` under a header reading "SKIPPED until ServiceStack is
 * implemented (it is a stub today)". `ServiceStack` had been 230 lines with
 * zero TODOs and deployed for a day, and no CI job ran `jest`, `tsc` or
 * `cdk synth` at all -- so the file that DEFINES the security posture was the
 * only code in the repository with no gate on it. Seven assertions written
 * specifically to guard it had never executed once.
 *
 * When they were finally run, three separate things were wrong, and they are
 * worth naming because they are three different failure modes:
 *
 *   1. AN ASSERTION HAD INVERTED. `it('orchestrator role CAN Scan products')`
 *      asserted `dynamodb:Scan` was PRESENT. Pilot Task 6b removed that
 *      permission on 2026-08-30 when `candidates_for_budget` moved to GSI2, and
 *      `config/iam-orchestrator-role.json` explains at length why. The check
 *      still passed -- because the Scan really had come back (see 2).
 *   2. THE STACK HAD REGRESSED. `tables.products.grantReadData(role)` and
 *      `tables.idempotency.grantReadWriteData(role)` added a second statement
 *      on top of the JSON policy, granting `dynamodb:Scan` on products (plus
 *      `index/*` and Streams) and `DeleteItem`/`BatchWriteItem` on idempotency.
 *      The config file's own comment had predicted it: "a Scan permission
 *      nothing needs is a Scan somebody can reintroduce without noticing."
 *   3. TWO ASSERTIONS WERE THEATRE. `it('the only Resource:"*" is X-Ray')` had
 *      an empty body -- a comment and no expectation, so un-skipping it would
 *      have produced a green check that verifies nothing. And the write test
 *      matched `/dynamodb:PutItem[\s\S]*grocery-products/` over a JSON blob, a
 *      pattern that spans unrelated statements: it FAILED on a policy that has
 *      no write on products at all, because `PutItem` appears in the
 *      idempotency statement and `grocery-products` appears later in the file.
 *      A false negative and a false positive in the same suite.
 *
 * So the assertions below parse the policy document and compare ACTION SETS PER
 * RESOURCE. Regex over `JSON.stringify` is how (3) happened, and a security
 * check that cannot tell which statement it matched is not a security check.
 *
 * Run: npm test   (jest + ts-jest, see jest.config.js). CI runs it in the
 * `infra` job, which `summary.needs` gates the merge on.
 */
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { loadConfig } from '../lib/config';
import { StatefulStack } from '../lib/stateful-stack';
import { ServiceStack } from '../lib/service-stack';

/** One IAM statement, flattened to strings a test can compare. */
interface FlatStatement {
  sid: string;
  actions: string[];
  resources: string[];
}

/**
 * CloudFormation intrinsics -> a comparable string.
 *
 * `grantReadData` emits `{"Fn::Join": ["", ["arn:", {"Ref": "AWS::Partition"},
 * ":dynamodb:..."]]}` while the JSON policy emits a plain string, and a test
 * that only understood one of those would silently ignore half the statements
 * -- which is how a granted `Scan` sits next to a policy that forbids it and
 * nothing notices.
 */
function flatten(value: unknown): string {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(flatten).join('');
  if (value && typeof value === 'object') {
    const o = value as Record<string, unknown>;
    if (o['Fn::Join']) {
      const [sep, parts] = o['Fn::Join'] as [string, unknown[]];
      return parts.map(flatten).join(sep);
    }
    if (o['Ref']) return `\${${String(o['Ref'])}}`;
    if (o['Fn::GetAtt']) return `\${${flatten(o['Fn::GetAtt'])}}`;
  }
  return JSON.stringify(value);
}

function asArray(value: unknown): string[] {
  if (value === undefined) return [];
  return (Array.isArray(value) ? value : [value]).map(flatten);
}

/** Every statement across every IAM policy and inline role policy in the stack. */
function statements(t: Template): FlatStatement[] {
  const out: FlatStatement[] = [];
  const collect = (doc: any, source: string) => {
    for (const s of doc?.Statement ?? []) {
      out.push({
        sid: s.Sid ?? `(unnamed in ${source})`,
        actions: asArray(s.Action),
        resources: asArray(s.Resource),
      });
    }
  };
  for (const [id, r] of Object.entries(t.findResources('AWS::IAM::Policy'))) {
    collect((r as any).Properties?.PolicyDocument, id);
  }
  for (const [id, r] of Object.entries(t.findResources('AWS::IAM::Role'))) {
    for (const p of (r as any).Properties?.Policies ?? []) {
      collect(p.PolicyDocument, id);
    }
  }
  return out;
}

/** Statements naming the products table or any of its indexes. */
function touching(all: FlatStatement[], needle: string): FlatStatement[] {
  return all.filter((s) => s.resources.some((r) => r.includes(needle)));
}

function lambdaEnv(t: Template): Record<string, string>[] {
  return Object.values(t.findResources('AWS::Lambda::Function')).map(
    (fn) => ((fn as any).Properties?.Environment?.Variables ?? {}) as Record<string, string>,
  );
}

describe('ServiceStack security invariants', () => {
  const app = new cdk.App();
  const cfg = loadConfig('dev');
  const env = { account: '111111111111', region: 'ap-southeast-2' };
  const stateful = new StatefulStack(app, 'Stateful', { env, cfg });
  const service = new ServiceStack(app, 'Service', { env, cfg, tables: stateful });
  const t = Template.fromStack(service);
  const policy = statements(t);

  // ------------------------------------------------------------------ IAM

  it('grants nothing at all beyond what config/iam-orchestrator-role.json declares', () => {
    // The broadest form of the invariant, and the one that would have caught
    // the grant-helper regression on its own: every statement in the template
    // is either one the JSON declares (by Sid) or one CDK adds for a construct
    // this stack deliberately configures. An unnamed statement touching a data
    // resource is by definition something nobody wrote down.
    const declared = new Set([
      'BedrockInvokeConfiguredModels',
      'BedrockApplyGuardrail',
      'DynamoReadProducts',
      'DynamoIdempotency',
      'XRayTracing',
    ]);
    const undeclared = policy.filter(
      (s) =>
        !declared.has(s.sid) &&
        // Tracing.ACTIVE adds its own X-Ray statement. That one is expected:
        // it comes from a property this stack sets on purpose.
        !s.actions.every((a) => a.startsWith('xray:')),
    );
    expect(
      undeclared.map((s) => `${s.sid}: ${s.actions.join(',')} on ${s.resources.join(',')}`),
    ).toEqual([]);
  });

  it('orchestrator role cannot WRITE the products table', () => {
    const writes = ['dynamodb:PutItem', 'dynamodb:BatchWriteItem', 'dynamodb:DeleteItem',
      'dynamodb:UpdateItem'];
    for (const s of touching(policy, 'grocery-products')) {
      expect({ sid: s.sid, writes: s.actions.filter((a) => writes.includes(a)) }).toEqual({
        sid: s.sid,
        writes: [],
      });
    }
  });

  it('orchestrator role cannot Scan products (Pilot Task 6b removed it)', () => {
    // The inverse of what this test used to assert. `candidates_for_budget`
    // queries GSI2 (category / zero-padded price); nothing pages the base
    // table any more, and the config file is explicit that a Scan permission
    // nothing needs is a Scan somebody can reintroduce without noticing.
    const scanners = touching(policy, 'grocery-products').filter((s) =>
      s.actions.includes('dynamodb:Scan'),
    );
    expect(scanners.map((s) => s.sid)).toEqual([]);
  });

  it('products access is exactly GetItem, BatchGetItem and Query, on the table and both GSIs', () => {
    const reads = touching(policy, 'grocery-products');
    expect(reads).toHaveLength(1);
    expect(reads[0].sid).toBe('DynamoReadProducts');
    expect([...reads[0].actions].sort()).toEqual([
      'dynamodb:BatchGetItem',
      'dynamodb:GetItem',
      'dynamodb:Query',
    ]);
    // Each index is a distinct resource ARN and must be named: omitting one
    // yields a working GetItem and a failing Query (docs/ARCHITECTURE.md §4).
    // A wildcard `index/*` would satisfy "both are covered" while also
    // covering every index nobody has reviewed.
    expect(reads[0].resources.some((r) => r.endsWith('index/GSI1'))).toBe(true);
    expect(reads[0].resources.some((r) => r.endsWith('index/GSI2'))).toBe(true);
    expect(reads[0].resources.filter((r) => r.includes('index/*'))).toEqual([]);
  });

  it('idempotency access carries no DeleteItem - expiry is by TTL', () => {
    // config/iam-orchestrator-role.json: "No Delete -- expiry is by TTL, which
    // requires no permission." `grantReadWriteData` granted DeleteItem and
    // BatchWriteItem anyway.
    const idem = touching(policy, 'grocery-idempotency');
    expect(idem).toHaveLength(1);
    expect([...idem[0].actions].sort()).toEqual([
      'dynamodb:GetItem',
      'dynamodb:PutItem',
      'dynamodb:UpdateItem',
    ]);
  });

  it('the only Resource:"*" is X-Ray', () => {
    // This test had an empty body until 2026-08-31 and passed by having nothing
    // to fail. X-Ray segment writes take no resource, so `*` is the only
    // expressible form; anything else with `*` is an unreviewed grant.
    const wildcards = policy.filter((s) => s.resources.includes('*'));
    expect(wildcards.length).toBeGreaterThan(0); // the check itself must have input
    for (const s of wildcards) {
      expect({ sid: s.sid, nonXray: s.actions.filter((a) => !a.startsWith('xray:')) }).toEqual({
        sid: s.sid,
        nonXray: [],
      });
    }
  });

  // ------------------------------------------------------------- API stage

  it('API stage has throttling', () => {
    t.hasResourceProperties('AWS::ApiGateway::Stage', {
      MethodSettings: Match.arrayWith([
        Match.objectLike({ ThrottlingRateLimit: Match.anyValue() }),
      ]),
    });
  });

  it('API stage has a usage plan attached (security.md line 22)', () => {
    // Throttling AND a usage plan. The stage had throttling and no usage plan
    // at all until 2026-08-30, so half the control was missing and the half
    // that was present is the one that looks like the whole thing.
    expect(Object.keys(t.findResources('AWS::ApiGateway::UsagePlan'))).toHaveLength(1);
  });

  it('API stage never logs request bodies (Req 11.5)', () => {
    // A request body here is the shopper's message.
    for (const stage of Object.values(t.findResources('AWS::ApiGateway::Stage'))) {
      for (const setting of (stage as any).Properties?.MethodSettings ?? []) {
        expect(setting.DataTraceEnabled).not.toBe(true);
      }
    }
  });

  // ----------------------------------------------------------- the function

  it('POWERTOOLS_LOGGER_LOG_EVENT is never set true (privacy, Req 11.5)', () => {
    for (const vars of lambdaEnv(t)) {
      expect(vars.POWERTOOLS_LOGGER_LOG_EVENT).not.toBe('true');
    }
  });

  it('Guardrail is applied with a numbered version, never DRAFT', () => {
    for (const vars of lambdaEnv(t)) {
      if (vars.BEDROCK_GUARDRAIL_VERSION !== undefined) {
        expect(vars.BEDROCK_GUARDRAIL_VERSION).toMatch(/^[0-9]+$/);
      }
    }
  });

  it('APP_STAGE is set, so the Req 12.5 runtime check is not inert', () => {
    // `assert_production_configuration` returns immediately when APP_STAGE is
    // unset, which is how a check implemented on 2026-08-30 stayed dormant
    // (docs/ARCHITECTURE.md §3g). Setting it from cfg.stage makes arming a
    // consequence of the stage rather than a second thing to remember.
    for (const vars of lambdaEnv(t)) {
      expect(vars.APP_STAGE).toBe('dev');
    }
  });

  it('the log group has finite retention', () => {
    // Production's `/aws/lambda/grocery-orchestrator-dev` returns
    // retentionInDays: null -- never expire, which turns any future logging
    // mistake into a permanent one.
    const groups = Object.values(t.findResources('AWS::Logs::LogGroup'));
    expect(groups.length).toBeGreaterThan(0);
    for (const g of groups) {
      expect(typeof (g as any).Properties?.RetentionInDays).toBe('number');
    }
  });

  // ------------------------------------------------------------------ SSM

  it('every published SSM parameter is parseable JSON', () => {
    // `/grocery/dev-cdk/models` held `readFileSync(models.json).slice(0, 4096)`
    // of a 10,930-byte file: invalid JSON under a name that invites someone to
    // load it. Nothing broke because nothing reads it yet, which is the worst
    // reason for a defect to survive.
    const params = Object.values(t.findResources('AWS::SSM::Parameter'));
    expect(params.length).toBeGreaterThan(0);
    for (const p of params) {
      const props = (p as any).Properties;
      expect(() => JSON.parse(props.Value)).not.toThrow();
      // And it must still fit the tier it is published under.
      expect(Buffer.byteLength(props.Value, 'utf-8')).toBeLessThanOrEqual(4096);
    }
  });

  it('the models parameter publishes routing only, never the scorecards', () => {
    // Scorecards are measured evidence, not an operator knob. A parameter an
    // operator can edit is a place a route could be qualified by typing, which
    // is the one thing the qualification gate exists to prevent.
    const param = Object.values(t.findResources('AWS::SSM::Parameter')).find((p) =>
      String((p as any).Properties?.Name).endsWith('/models/routing'),
    );
    expect(param).toBeDefined();
    const body = JSON.parse((param as any).Properties.Value);
    expect(body.routing).toBeDefined();
    expect(body.scorecards).toBeUndefined();
    expect(body.models).toBeUndefined();
  });

  // ------------------------------------------------------------- adoption

  it('the stateful stack creates no table, so CloudFormation cannot delete one', () => {
    // The adoption evidence is an ABSENCE, which is exactly the kind of claim
    // that needs a test: nothing about a passing deploy would tell you the
    // difference between "adopted by reference" and "about to be replaced".
    expect(Template.fromStack(stateful).findResources('AWS::DynamoDB::Table')).toEqual({});
  });
});
