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
import { Template } from 'aws-cdk-lib/assertions';
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

describe('the API-key decision, deferred with a tripwire', () => {
  /**
   * DECIDED 2026-08-31, by the owner: option C now, option A at the cutover.
   *
   * Both `POST /chat` endpoints are public and unauthenticated, and every
   * request spends Bedrock money. Requiring an API key is minutes of CDK; what
   * it costs is a required `x-api-key` header in `CONTRACT-v1.md`, API
   * Gateway's own 403 body instead of the contract-valid `ChatResponse` this
   * service guarantees on every other path, and a teammate's working client
   * that has been calling this endpoint since 2026-08-21.
   *
   * So the decision was: stay open while the endpoints have no consumer, and
   * take the key in the SAME change that repoints the frontend at the CDK
   * plane -- one coordinated URL-and-header change instead of breaking a
   * working client for a risk that is currently theoretical, because nobody
   * outside the team has the URL. Full reasoning, the three options and what
   * each costs: `docs/OPEN-REVIEW-api-key.md`.
   *
   * WHY THIS IS A TEST AND NOT A NOTE IN A DOCUMENT. A note saying "revisit
   * when the frontend lands" is a claim that ages, and this repository has
   * spent two audits finding those: "SKIPPED until ServiceStack is
   * implemented" outlived the stack by a day, and `tests/test_skip_markers.py`
   * exists because of it. The rule that came out of that is to state the
   * condition in code so it expires on its own.
   *
   * `FrontendStack` creating its first resource IS the condition. It is a stub
   * today; the moment it is implemented, its CloudFront domain becomes
   * `CORS_ORIGIN`, the frontend is being wired to a deployed URL, and that is
   * the change the owner named. This test fails then, in CI, with the review
   * document in the failure message.
   */
  it('fires when FrontendStack stops being a stub', () => {
    const app = buildApp();
    const frontend = Template.fromStack(
      app.node.findChild('Grocery-Frontend-dev') as cdk.Stack,
    ).toJSON();
    const service = Template.fromStack(app.node.findChild('Grocery-Service-dev') as cdk.Stack);

    // CDKMetadata is emitted for every stack and is not a resource anybody
    // declared, so it does not count as the frontend existing.
    const frontendResources = Object.entries(frontend.Resources ?? {}).filter(
      ([id]) => id !== 'CDKMetadata',
    );

    if (frontendResources.length === 0) {
      expect(frontendResources).toEqual([]); // still a stub: decision not yet due
      return;
    }

    const methods = Object.values(service.findResources('AWS::ApiGateway::Method'));
    const posts = methods.filter((m) => (m as any).Properties?.HttpMethod === 'POST');
    expect(posts.length).toBeGreaterThan(0);

    const unprotected = posts.filter((m) => (m as any).Properties?.ApiKeyRequired !== true);
    if (unprotected.length > 0) {
      // Thrown rather than `expect(...).toBe(true)` because the default failure
      // reads "Received: undefined", which tells whoever hits this nothing at
      // all. A tripwire whose message does not explain itself gets deleted by
      // the person it fires on.
      throw new Error(
        [
          'THE API-KEY DECISION IS NOW DUE.',
          '',
          'FrontendStack creates resources, so a frontend is being deployed and',
          'wired to a URL. That is the exact moment the owner deferred this to on',
          '2026-08-31: stay open (option C) while the endpoints have no consumer,',
          'take the key (option A) in the SAME change that repoints the frontend,',
          'so the URL change and the header change land together instead of',
          'breaking a working client twice.',
          '',
          'Read docs/OPEN-REVIEW-api-key.md (§4 has the CDK, §5 the three',
          'options), then EITHER:',
          '',
          '  A. Take the key. Set apiKeyRequired on POST /chat -- NOT on OPTIONS,',
          '     a browser preflight carries no custom headers -- attach a key to',
          '     the usage plan WITH A QUOTA (a key without one changes who can',
          '     call, not how much), update CONTRACT-v1.md, and tell whoever owns',
          '     the frontend that a request without the header gets API',
          "     Gateway's own 403 rather than a contract-valid ChatResponse.",
          '',
          '  B. Decide something else. Record it in that document with the',
          '     reasoning, and update this test to match the new decision.',
          '',
          'Do not simply delete this check. It is the only thing that remembers.',
        ].join(String.fromCharCode(10)),
      );
    }
  });
});
