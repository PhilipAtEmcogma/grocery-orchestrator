/**
 * The security invariants of the ingestion plane, as executable checks.
 *
 * WHY THIS FILE MATTERS MORE THAN ITS SIZE SUGGESTS. This is the only role in
 * the system that can WRITE the serving catalogue — 2,759 rows a shopper is
 * shown as fact. Everything else in the app reads. The orchestrator cannot
 * write a price, the reviewer cannot write anything, and the MCP façade goes
 * through the same handler as the API. If a permission leaks anywhere, this is
 * the role where it costs the most.
 *
 * The plane ran in the account from 2026-09-04 with no template behind it and
 * therefore no assertions over it at all. `infra/test/service-stack.test.ts`
 * records what happened the first time THAT suite was run against an
 * already-deployed stack: two live IAM regressions, one of them a `Scan` that
 * Pilot Task 6b had deliberately removed. This file exists so the ingestion
 * plane does not get to find that out the same way.
 *
 * THE ASSERTIONS COMPARE ACTION SETS PER RESOURCE, reusing the flattening
 * helpers' approach from the service suite. Regex over `JSON.stringify` is how
 * that suite once produced a false negative and a false positive in the same
 * run: `PutItem` appears in one statement and the table name in another, and a
 * pattern spanning both matches a policy that grants neither together.
 */
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { loadConfig } from '../lib/config';
import { StatefulStack } from '../lib/stateful-stack';
import { IngestionStack } from '../lib/ingestion-stack';

interface FlatStatement {
  sid: string;
  actions: string[];
  resources: string[];
}

/** CloudFormation intrinsics -> a comparable string. See the service suite. */
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

function build(env: Record<string, string | undefined> = {}) {
  const saved: Record<string, string | undefined> = {};
  for (const [k, v] of Object.entries(env)) {
    saved[k] = process.env[k];
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
  try {
    const app = new cdk.App();
    const cfg = loadConfig('dev');
    const account = { account: '111111111111', region: 'ap-southeast-2' };
    const stateful = new StatefulStack(app, 'Stateful', { env: account, cfg });
    const stack = new IngestionStack(app, 'Ingestion', { env: account, cfg, tables: stateful });
    return { t: Template.fromStack(stack), cfg };
  } finally {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
}

describe('IngestionStack security invariants', () => {
  const { t, cfg } = build({ INGESTION_SCHEDULE: undefined });
  const policy = statements(t);

  // ------------------------------------------------------------------ IAM

  it('writes the products table and reads it only enough to diff', () => {
    // Query is granted so ingestion can diff BEFORE it writes. The first
    // version of this role was write-only, and a write-only writer cannot know
    // what it is about to change -- which is how a unit_price_nzd of "2490.00"
    // reached six live rows with no signal.
    const products = policy.filter((s) =>
      s.resources.some((r) => r.includes(cfg.names.productsTable)),
    );
    expect(products.length).toBeGreaterThan(0);

    const actions = new Set(products.flatMap((s) => s.actions));
    expect(actions).toEqual(
      new Set(['dynamodb:Query', 'dynamodb:PutItem', 'dynamodb:BatchWriteItem']),
    );

    // Scoped to the BASE TABLE. The diff queries by store_key, the base
    // partition key, so GSI1 is not needed -- and an index grant nothing uses
    // is an index grant somebody can build on without noticing.
    for (const s of products) {
      for (const r of s.resources) {
        expect(r).not.toContain('index/');
      }
    }
  });

  it('the price history grant is APPEND-ONLY, with no way to read or rewrite', () => {
    // The strongest invariant in this file. A history row is immutable by
    // definition: it is the baseline a future deviation is measured against.
    // A role that could rewrite one could rewrite the evidence.
    //
    // Query is absent too, and deliberately: ingestion appends and never reads
    // history back. The readers are ops and the data-quality reviewer, off the
    // shopper path, under a different identity.
    const history = policy.filter((s) =>
      s.resources.some((r) => r.includes(cfg.names.priceHistoryTable)),
    );
    expect(history.length).toBeGreaterThan(0);

    const actions = new Set(history.flatMap((s) => s.actions));
    expect(actions).toEqual(new Set(['dynamodb:PutItem', 'dynamodb:BatchWriteItem']));

    for (const forbidden of [
      'dynamodb:DeleteItem',
      'dynamodb:UpdateItem',
      'dynamodb:Query',
      'dynamodb:Scan',
      'dynamodb:GetItem',
    ]) {
      expect(actions.has(forbidden)).toBe(false);
    }
  });

  it('has no Bedrock and no idempotency access — the separation IS the role', () => {
    // config/iam-ingestion-role.json calls this out: ingestion writes prices
    // and has no model plane and no turn state. If these ever appear, the two
    // roles have merged and the blast radius of an ingestion bug now includes
    // the shopper path.
    const all = policy.flatMap((s) => s.actions).join(' ');
    expect(all).not.toContain('bedrock:');

    const idempotency = policy.filter((s) =>
      s.resources.some((r) => r.includes(cfg.names.idempotencyTable)),
    );
    expect(idempotency).toEqual([]);
  });

  it('never grants dynamodb:Scan anywhere', () => {
    // Pilot Task 6b removed Scan from the orchestrator on 2026-08-30 and the
    // service suite found it had come back. A whole-table read on a role that
    // can also write is worse.
    expect(policy.flatMap((s) => s.actions)).not.toContain('dynamodb:Scan');
  });

  it('the only Resource:"*" is X-Ray', () => {
    // X-Ray's PutTraceSegments has no resource-level permissions -- the
    // wildcard is the API, not a shortcut. Everything else must name a
    // resource. The statement appears twice (once from the JSON, once added by
    // `tracing: ACTIVE`), which the DEPLOYED service plane also does; the
    // duplication is harmless and identical, and this test asserts the
    // security-relevant claim rather than the tidiness one.
    const wildcards = policy.filter((s) => s.resources.includes('*'));
    expect(wildcards.length).toBeGreaterThan(0);
    for (const s of wildcards) {
      for (const action of s.actions) {
        expect(action.startsWith('xray:')).toBe(true);
      }
    }
  });

  // -------------------------------------------------------- state machine

  it('the state machine invokes THIS plane’s function, not the hand-made one', () => {
    // The ASL in config/ names `grocery-ingestion-dev` literally. Without the
    // rewrite in ingestion-stack.ts, the CDK state machine would invoke the
    // HAND-MADE Lambda -- two planes that look independent while sharing the
    // half that writes to the catalogue, which is worse than either one alone.
    const machines = Object.values(t.findResources('AWS::StepFunctions::StateMachine'));
    expect(machines).toHaveLength(1);

    const asl = flatten((machines[0] as any).Properties?.DefinitionString);
    expect(asl).toContain(`function:${cfg.names.ingestionFn}${cfg.suffix}`);
    // ...and NOT the unsuffixed name on its own. The suffixed name contains the
    // unsuffixed one as a prefix, so the check is that no occurrence is
    // followed by something other than the suffix.
    expect(asl).not.toMatch(new RegExp(`function:${cfg.names.ingestionFn}(?!${cfg.suffix})`));
  });

  it('the definition carries no comment fields, which Step Functions rejects', () => {
    // A REAL FAILED DEPLOY, 2026-09-07. `cdk synth` renders the definition
    // happily -- Step Functions validates it at CREATE time, not at synth --
    // and CloudFormation answered:
    //
    //   SCHEMA_VALIDATION_FAILED: Field '_comment' is not supported at
    //   /States/RefreshAllRetailers/ItemProcessor/States/RefreshOneRetailer/Catch[0]
    //
    // ASL permits `Comment` on a STATE and rejects unknown members elsewhere,
    // so the `_comment` this repo uses to explain the Catch is exactly what the
    // service refuses. `scripts/apply_state_machine.py` has stripped both since
    // it was written; this stack did not, and the two paths disagreed about the
    // same file.
    //
    // Asserted on the RENDERED definition rather than on the stripping
    // function, because the defect was in what got submitted.
    const machine = Object.values(t.findResources('AWS::StepFunctions::StateMachine'))[0] as any;
    const asl = flatten(machine.Properties?.DefinitionString);

    expect(asl).not.toContain('_comment');
    expect(asl).not.toContain('"Comment"');

    // ...and it is still the real definition, not an empty object that would
    // satisfy the two checks above by saying nothing at all.
    const parsed = JSON.parse(asl.replace(/\$\{[^}]+\}/g, 'X'));
    expect(parsed.States.RefreshAllRetailers.Type).toBe('Map');
    expect(parsed.States.RefreshAllRetailers.ItemProcessor.States.RefreshOneRetailer.Catch)
      .toHaveLength(1);
    // The load-bearing one: ResultPath null, because Map items here are STRINGS
    // and a ResultPath on a non-object aborts the Map the Catch protects.
    expect(
      parsed.States.RefreshAllRetailers.ItemProcessor.States.RefreshOneRetailer.Catch[0].ResultPath,
    ).toBeNull();
  });

  it('the invoke grant names one function rather than a wildcard', () => {
    const invokes = policy.filter((s) => s.actions.includes('lambda:InvokeFunction'));
    expect(invokes.length).toBeGreaterThan(0);
    for (const s of invokes) {
      for (const r of s.resources) {
        expect(r).not.toBe('*');
      }
    }
  });

  // ------------------------------------------------------------- schedule

  it('the schedule is created DISABLED by default', () => {
    // config/data-sources.json: LineageBSource.CAPTURED_AT is the constant
    // 2026-08-28 and the dataset is a one-off snapshot, so a nightly run
    // rewrites the same rows with the same capture date -- cost and catalogue
    // writes for no new information.
    //
    // It is also the drift this file is closing. The 2026-08-30 account audit
    // recorded the hand-made schedule as ENABLED; it is DISABLED in the account
    // now, and nobody wrote down the change. A state that lives only in a
    // console can flip without review, and the dangerous direction writes.
    const schedules = Object.values(t.findResources('AWS::Scheduler::Schedule'));
    expect(schedules).toHaveLength(1);
    expect((schedules[0] as any).Properties?.State).toBe('DISABLED');
  });

  it('INGESTION_SCHEDULE=1 enables it, so the off switch is reversible', () => {
    // A disabled feature nobody can re-enable is a deleted feature.
    const { t: on } = build({ INGESTION_SCHEDULE: '1' });
    const schedules = Object.values(on.findResources('AWS::Scheduler::Schedule'));
    expect((schedules[0] as any).Properties?.State).toBe('ENABLED');
  });

  it('the schedule carries an explicit timezone rather than a UTC cron', () => {
    // NZST is UTC+12 and NZDT is UTC+13, so a fixed UTC cron drifts an hour
    // twice a year. infra/docs/03 specified a UTC cron and apologised for
    // exactly this; EventBridge Scheduler removes the problem instead.
    const schedule = Object.values(t.findResources('AWS::Scheduler::Schedule'))[0] as any;
    expect(schedule.Properties?.ScheduleExpressionTimezone).toBe('Pacific/Auckland');
  });

  it('the schedule asks for all three retailers, including the empty one', () => {
    // Woolworths fetches 0 rows every run because the collected dataset has no
    // Woolworths file. Keeping it in the input makes the two-chain coverage gap
    // a recurring number in the execution history rather than a claim in
    // docs/OPEN-REVIEW-chain-coverage.md that nobody re-reads.
    const schedule = Object.values(t.findResources('AWS::Scheduler::Schedule'))[0] as any;
    const input = JSON.parse(schedule.Properties?.Target?.Input);
    expect(input.retailers).toEqual(['paknsave', 'woolworths', 'new_world']);
  });

  // ------------------------------------------------------ everything else

  it('creates no DynamoDB table — the serving catalogue is adopted', () => {
    // The same Strategy A the stateful stack uses. A stack that cannot create a
    // table cannot replace one, and this stack points at 2,759 real rows.
    expect(t.findResources('AWS::DynamoDB::Table')).toEqual({});
  });

  it('declares a log group with finite retention, and no custom resource to set it', () => {
    // `logRetention` on the function is deprecated AND implemented as a custom
    // resource: an extra Lambda, role and policy whose whole job is one
    // PutRetentionPolicy call. A second function in the account to express a
    // number, on the stack whose point is least privilege.
    const groups = Object.values(t.findResources('AWS::Logs::LogGroup'));
    expect(groups).toHaveLength(1);
    expect((groups[0] as any).Properties?.RetentionInDays).toBe(14);
    expect(t.findResources('Custom::LogRetention')).toEqual({});

    // One Lambda in this stack: the ingestion function itself.
    expect(Object.keys(t.findResources('AWS::Lambda::Function'))).toHaveLength(1);
  });

  it('runs the real catalogue rather than the fixtures', () => {
    // Before the 2026-09-04 decision the deployed function used the fixture
    // default and reported a successful refresh that wrote nothing. The env
    // var is what makes the refresh read the collected 2,759 rows.
    const fn = Object.values(t.findResources('AWS::Lambda::Function'))[0] as any;
    const vars = fn.Properties?.Environment?.Variables ?? {};
    expect(vars.PRICE_SOURCE).toBe('lineage_b');
    expect(vars.PRODUCTS_TABLE).toBe(cfg.names.productsTable);
    // Set explicitly here where the hand-made function relies on a code
    // default. A table this role can write to belongs in the configuration.
    expect(vars.PRICE_HISTORY_TABLE).toBe(cfg.names.priceHistoryTable);
  });

  it('every created resource carries the suffix, so nothing collides', () => {
    // CloudFormation REFUSES to create over an existing resource -- proved on
    // 2026-09-07 when the observability stack met twelve alarms that already
    // existed. A stack that wants a name the hand-made plane holds does not
    // deploy at all.
    const fn = Object.values(t.findResources('AWS::Lambda::Function'))[0] as any;
    expect(fn.Properties?.FunctionName).toBe(`${cfg.names.ingestionFn}${cfg.suffix}`);

    const machine = Object.values(t.findResources('AWS::StepFunctions::StateMachine'))[0] as any;
    expect(machine.Properties?.StateMachineName).toBe(`${cfg.names.ingestionFn}${cfg.suffix}`);

    const schedule = Object.values(t.findResources('AWS::Scheduler::Schedule'))[0] as any;
    expect(schedule.Properties?.Name).toContain(cfg.suffix);

    for (const role of Object.values(t.findResources('AWS::IAM::Role'))) {
      const name = (role as any).Properties?.RoleName;
      if (name) expect(name).toContain(cfg.suffix);
    }
  });
});
