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
   * TWO CONDITIONS, because there are two ways a frontend can arrive and only
   * one of them touches this app:
   *
   *   1. `FrontendStack` creates its first resource. It is a stub today; the
   *      moment it is implemented, its CloudFront domain becomes `CORS_ORIGIN`
   *      and the frontend is being wired to a deployed URL.
   *   2. `CORS_ORIGIN` is set to a real origin. A frontend hosted ANYWHERE
   *      ELSE -- Netlify, Vercel, an S3 bucket somebody made by hand -- never
   *      touches `FrontendStack`, but it cannot call this API from a browser
   *      without a named origin, because a wildcard `*` is refused for any
   *      production stage and is the giveaway that no real client exists.
   *
   * Condition 1 alone was the first version of this test, and it had a hole
   * exactly the size of "the frontend was not deployed with our CDK" -- which
   * is the likely case, given the existing client is a Vite app with no
   * infrastructure of its own. A tripwire with a hole is the shape this
   * repository keeps finding.
   */
  it('fires when a frontend appears, by either route', () => {
    const app = buildApp();
    const cfg = loadConfig('dev');
    const frontend = Template.fromStack(
      app.node.findChild('Grocery-Frontend-dev') as cdk.Stack,
    ).toJSON();
    const service = Template.fromStack(app.node.findChild('Grocery-Service-dev') as cdk.Stack);

    // CDKMetadata is emitted for every stack and is not a resource anybody
    // declared, so it does not count as the frontend existing.
    const frontendResources = Object.entries(frontend.Resources ?? {}).filter(
      ([id]) => id !== 'CDKMetadata',
    );
    const realOrigin = cfg.corsOrigin !== '*';

    if (frontendResources.length === 0 && !realOrigin) {
      // Neither condition met: no frontend, decision not yet due.
      expect(frontendResources).toEqual([]);
      expect(cfg.corsOrigin).toBe('*');
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
          'A frontend has appeared -- either FrontendStack now creates resources,',
          'or CORS_ORIGIN names a real origin. Either way a browser client is being',
          'wired to this API. That is the exact moment the owner deferred this to on',
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

/**
 * AN OBSERVER MUST NOT BE ABLE TO MUTATE WHAT IT OBSERVES.
 *
 * Generalised from Pilot Task 13c, where the catalogue stream guard was given
 * its own role specifically so it could not write to the table it watches. That
 * was the right call and it was held by ONE hand-written assertion in ONE
 * suite, which protects exactly the function somebody already thought about.
 * The next SQS consumer, Kinesis reader or stream subscriber would have
 * nothing.
 *
 * WHY THE RULE IS WORTH STATING AS A RULE. A component that both watches a
 * resource and can change it turns a false positive into data loss: the guard
 * decides a row is wrong and is able to act on that decision, with no second
 * opinion and no deploy to review. Detection and remediation are separate
 * authorities. The same argument already appears twice in this codebase — the
 * ingestion role's append-only history grant, and the reviewer that may report
 * findings but holds no publication authority (Req 13.8) — which is what makes
 * it a principle here rather than a preference.
 *
 * HOW IT IS CHECKED, and the limitation is stated rather than hidden. For every
 * `AWS::Lambda::EventSourceMapping` in the app, the consuming function's role
 * is resolved and every mutating action it holds is compared against the
 * resource the mapping reads. A DynamoDB stream ARN contains the table name, so
 * "writes the table it streams from" is decidable from the template. It cannot
 * see a grant made through a wildcard resource that happens to cover the table,
 * which is why `service-stack.test.ts` separately asserts the only `Resource:
 * "*"` is X-Ray.
 */
describe('least privilege across the whole app', () => {
  /** Actions that CHANGE data, as opposed to reading or describing it. */
  const MUTATING = /^(dynamodb|s3|sqs):.*(Put|Update|Delete|Write|Create|Restore)/i;

  /**
   * The LOGICAL IDS an intrinsic refers to.
   *
   * THIS IS THE FUNCTION THE FIRST VERSION GOT WRONG, and the bug is worth
   * keeping in the comments because it made the whole guardrail inert. That
   * version flattened intrinsics to strings and compared them with `includes`:
   * a function's role renders as `{"Fn::GetAtt": ["RoleABC", "Arn"]}` and a
   * policy's as `{"Ref": "RoleABC"}`, which flattened to `"${RoleABCArn}"` and
   * `"${RoleABC}"`. Those do not match — the trailing brace differs — so NO
   * policy was ever considered attached, the loop body never executed, and the
   * test passed while checking nothing.
   *
   * It was caught by mutation: granting the stream guard `dynamodb:PutItem` on
   * the table it watches did not fail the test. A guardrail that cannot fail is
   * the exact shape it exists to prevent.
   */
  function refIds(value: unknown): string[] {
    if (Array.isArray(value)) return value.flatMap(refIds);
    if (!value || typeof value !== 'object') return [];
    const o = value as Record<string, unknown>;
    if (typeof o['Ref'] === 'string') return [o['Ref'] as string];
    if (Array.isArray(o['Fn::GetAtt'])) return [String((o['Fn::GetAtt'] as unknown[])[0])];
    return Object.values(o).flatMap(refIds);
  }

  /** Flatten an ARN-ish intrinsic far enough to read the table name out of it. */
  function flat(value: unknown): string {
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) return value.map(flat).join('');
    if (value && typeof value === 'object') {
      const o = value as Record<string, unknown>;
      if (o['Fn::Join']) {
        const [sep, parts] = o['Fn::Join'] as [string, unknown[]];
        return parts.map(flat).join(sep);
      }
      if (o['Ref']) return String(o['Ref']);
      if (o['Fn::GetAtt']) return String((o['Fn::GetAtt'] as unknown[])[0]);
    }
    return JSON.stringify(value);
  }

  function asArray(v: unknown): unknown[] {
    return v === undefined ? [] : Array.isArray(v) ? v : [v];
  }

  /** Every (event source, consuming role) pair in the app, with its policies. */
  function consumers(app: cdk.App) {
    const found: { source: string; actions: string[]; resources: string[] }[] = [];

    for (const stack of app.node.children.filter((c): c is cdk.Stack => c instanceof cdk.Stack)) {
      const resources: Record<string, any> =
        (Template.fromStack(stack).toJSON().Resources as Record<string, any>) ?? {};

      for (const mapping of Object.values(resources).filter(
        (r) => r.Type === 'AWS::Lambda::EventSourceMapping',
      )) {
        const source = flat(mapping.Properties?.EventSourceArn);
        const fnId = refIds(mapping.Properties?.FunctionName)[0];
        const fn = fnId ? resources[fnId] : undefined;
        const roleId = refIds(fn?.Properties?.Role)[0];
        if (!roleId) continue;

        for (const policy of Object.values(resources).filter(
          (r) => r.Type === 'AWS::IAM::Policy',
        )) {
          if (!refIds(policy.Properties?.Roles).includes(roleId)) continue;
          for (const st of policy.Properties?.PolicyDocument?.Statement ?? []) {
            found.push({
              source,
              actions: asArray(st.Action).map(flat),
              resources: asArray(st.Resource).map(flat),
            });
          }
        }
      }
    }
    return found;
  }

  function withStream<T>(fn: () => T): T {
    const previous = process.env.PRODUCTS_STREAM_ARN;
    process.env.PRODUCTS_STREAM_ARN =
      'arn:aws:dynamodb:ap-southeast-2:111111111111:table/grocery-products-dev/stream/2026-09-07T00:00:00.000';
    try {
      return fn();
    } finally {
      if (previous === undefined) delete process.env.PRODUCTS_STREAM_ARN;
      else process.env.PRODUCTS_STREAM_ARN = previous;
    }
  }

  it('finds the consumers it is meant to be checking', () => {
    // The control on the control. Every assertion below loops over this list,
    // and a loop over nothing passes exactly as quietly as a loop that found
    // nothing wrong. CI runs without PRODUCTS_STREAM_ARN, where the stream
    // guard is deliberately absent, so the app is built WITH one here.
    expect(withStream(() => consumers(buildApp())).length).toBeGreaterThan(0);
  });

  it('no function may write to a resource it consumes as an event source', () => {
    for (const { source, actions, resources } of withStream(() => consumers(buildApp()))) {
      const table = source.match(/:table\/([^/]+)/)?.[1];
      if (!table) continue;

      for (const action of actions.filter((a) => MUTATING.test(a))) {
        for (const resource of resources) {
          // A stream ARN is `.../table/NAME/stream/DATE`; a table ARN is
          // `.../table/NAME`. Excluding `/stream/` is what separates "may read
          // the stream" from "may write the table".
          const writesWhatItReads =
            resource.includes(`:table/${table}`) && !resource.includes('/stream/');

          expect({ action, resource, writesWhatItReads }).toEqual({
            action,
            resource,
            writesWhatItReads: false,
          });
        }
      }
    }
  });
});
