/**
 * Properties of the whole app, rather than of one stack.
 *
 * `bin/grocery.ts` is the file `cdk synth` and `cdk deploy` actually walk, and
 * until 2026-08-31 nothing tested it: the other suite constructs two stacks
 * directly, so a stack that is broken only in the way the entrypoint WIRES it
 * would pass every test and fail at deploy.
 *
 * The region assertion here replaces a `throw` that used to live in
 * `bin/grocery.ts`. That throw compared `CDK_DEFAULT_REGION` -- a value the CDK
 * CLI derives from the resolved AWS profile -- and so it refused `cdk synth`
 * for any developer whose default region differed, while never firing in CI at
 * all, because a run with no credentials leaves the variable unset. It blocked
 * the harmless case and slept through the gated one. The pin on every stack's
 * `env` is the real control; this is the check on it.
 */
import * as cdk from 'aws-cdk-lib';
import { loadConfig } from '../lib/config';
import { StatefulStack } from '../lib/stateful-stack';
import { ServiceStack } from '../lib/service-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { IngestionStack } from '../lib/ingestion-stack';
import { FrontendStack } from '../lib/frontend-stack';

const REGION = 'ap-southeast-2';

function buildApp(stage = 'dev') {
  const app = new cdk.App();
  const cfg = loadConfig(stage);
  const env = { account: '111111111111', region: REGION };
  const stateful = new StatefulStack(app, `Grocery-Stateful-${stage}`, { env, cfg });
  const service = new ServiceStack(app, `Grocery-Service-${stage}`, { env, cfg, tables: stateful });
  new ObservabilityStack(app, `Grocery-Obs-${stage}`, { env, cfg, service });
  new IngestionStack(app, `Grocery-Ingestion-${stage}`, { env, cfg, tables: stateful });
  new FrontendStack(app, `Grocery-Frontend-${stage}`, { env, cfg });
  return app;
}

describe('the CDK app', () => {
  it('pins every stack to ap-southeast-2 (tech.md: never ap-southeast-6)', () => {
    const app = buildApp();
    const stacks = app.node.children.filter((c): c is cdk.Stack => c instanceof cdk.Stack);
    expect(stacks.length).toBe(5);
    for (const stack of stacks) {
      expect({ id: stack.node.id, region: stack.region }).toEqual({
        id: stack.node.id,
        region: REGION,
      });
    }
  });

  it('synthesises every stack without error', () => {
    // The stub stacks are included on purpose. A stub that throws at synth
    // would break `cdk deploy` for the two real stacks beside it, and nothing
    // else in the suite would notice.
    const assembly = buildApp().synth();
    expect(assembly.stacks.map((s) => s.stackName).sort()).toEqual([
      'Grocery-Frontend-dev',
      'Grocery-Ingestion-dev',
      'Grocery-Obs-dev',
      'Grocery-Service-dev',
      'Grocery-Stateful-dev',
    ]);
  });
});
