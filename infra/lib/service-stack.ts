/**
 * ServiceStack (Pilot Task 10) — the deployable service plane.
 *
 * Lambda + published SnapStart alias + REST API + scoped IAM + SSM config +
 * log retention + throttling and a usage plan.
 *
 * IT READS THE SAME `config/*.json` THE APPLY SCRIPTS READ. Every one of those
 * files carries a header saying "under IaC this becomes a CDK construct", and
 * `infra/docs/08` §5 recommends CDK reading the JSON during the migration so
 * both paths agree on one file rather than drifting apart. Porting the policies
 * into TypeScript comes later, when the apply scripts are retired — doing it
 * now would create the two sources of truth the migration exists to remove.
 *
 * DEPLOYED IN PARALLEL, NOT OVER THE TOP. A hand-made service plane is already
 * serving traffic on `woqmel35lk`, and this stack's names carry `cfg.suffix` so
 * it stands beside it rather than colliding with it. That is deliberate:
 *
 *   - `cdk import` of the whole API Gateway tree (RestApi, Resource, two
 *     Methods, two Integrations, Deployment, Stage, UsagePlan) needs every
 *     property to match the live resource exactly, and a mismatch is not a
 *     failed import — it is a REPLACEMENT of a resource that is serving.
 *   - Deploying fresh proves the definition is complete and correct, which an
 *     import cannot: an import inherits whatever the hand-made resource has,
 *     including the parts nobody wrote down.
 *
 * So this deploys, gets verified against the running service, and the cutover
 * becomes a reviewed decision with evidence rather than a leap. It is the same
 * order the Lambda alias moves used: publish, invoke the new version directly,
 * and only then move the pointer.
 */
import * as fs from 'fs';
import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import { GroceryConfig } from './config';
import { StatefulStack } from './stateful-stack';

/**
 * An SSM standard parameter caps at 4,096 bytes. Advanced caps at 8,192 and
 * costs money per parameter per month, so it is a decision rather than a
 * default -- and it would not have saved the models file anyway.
 */
const SSM_STANDARD_MAX_BYTES = 4096;

/**
 * Publish a JSON value to SSM, or FAIL THE SYNTH. Never truncate.
 *
 * The predecessor of this function was `.slice(0, 4096)` inline, and it shipped
 * a fragment of `config/models.json` that does not parse. That is the shape this
 * repository keeps finding and keeps writing rules against: something that looks
 * like the thing you wanted, produced by a step that quietly gave up. A
 * parameter holding half a config file is worse than no parameter, because the
 * name tells the next person it is safe to load.
 *
 * Throwing at synth is the cheapest possible place to find out: before an
 * account, before a deploy, and in a `cdk synth` that CI now runs.
 */
function publishJson(
  scope: Construct,
  id: string,
  opts: { parameterName: string; value: unknown; description: string },
): ssm.StringParameter {
  const body = JSON.stringify(opts.value, null, 2);
  const bytes = Buffer.byteLength(body, 'utf-8');
  if (bytes > SSM_STANDARD_MAX_BYTES) {
    throw new Error(
      `SSM parameter ${opts.parameterName} would be ${bytes} bytes, over the ` +
        `${SSM_STANDARD_MAX_BYTES}-byte standard-tier limit. Publish a smaller ` +
        `slice of the config, or move the file to S3 and put the pointer here. ` +
        `Do NOT truncate: a fragment of JSON under a name that promises the ` +
        `whole file is a defect nothing downstream can detect.`,
    );
  }
  return new ssm.StringParameter(scope, id, {
    parameterName: opts.parameterName,
    stringValue: body,
    description: opts.description,
  });
}

export interface ServiceStackProps extends cdk.StackProps {
  readonly cfg: GroceryConfig;
  readonly tables: StatefulStack;
}

export class ServiceStack extends cdk.Stack {
  public readonly api: apigateway.RestApi;
  public readonly orchestrator: lambda.Function;
  public readonly alias: lambda.Alias;

  constructor(scope: Construct, id: string, props: ServiceStackProps) {
    super(scope, id, props);
    const { cfg, tables } = props;
    const n = cfg.names;

    // ---------------------------------------------------------------- IAM

    const iamConfig = JSON.parse(fs.readFileSync(cfg.configFiles.iamOrchestrator, 'utf-8'));

    const role = new iam.Role(this, 'OrchestratorRole', {
      roleName: `${n.orchestratorRole}${cfg.suffix}`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: iamConfig.description,
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    // Statements come from the JSON verbatim, with ${AWS_*} resolved the way
    // scripts/apply_iam.py resolves them — from the deploy identity, never a
    // literal. tests/test_config_placeholders.py fails the build if a
    // twelve-digit account id reappears in config/, and this must not be the
    // path that reintroduces one.
    for (const statement of iamConfig.inline_policy.Statement) {
      const resources = (
        Array.isArray(statement.Resource) ? statement.Resource : [statement.Resource]
      ).map((r: string) =>
        r.replace(/\$\{AWS_REGION\}/g, this.region).replace(/\$\{AWS_ACCOUNT_ID\}/g, this.account),
      );
      role.addToPolicy(
        new iam.PolicyStatement({
          sid: statement.Sid,
          effect: iam.Effect.ALLOW,
          actions: Array.isArray(statement.Action) ? statement.Action : [statement.Action],
          resources,
        }),
      );
    }

    // ---------------------------------------------------------------- logs

    // FINITE RETENTION, which the hand-made log group does not have: the live
    // `/aws/lambda/grocery-orchestrator-dev` returns `retentionInDays: null`,
    // meaning never expire. infra/docs/04-SECURITY.md requires finite retention
    // and Req 11.5 keeps personal data out of logs — a log that never expires
    // turns any future logging mistake into a permanent one.
    const logGroup = new logs.LogGroup(this, 'OrchestratorLogs', {
      logGroupName: `/aws/lambda/${n.orchestratorFn}${cfg.suffix}`,
      retention: logs.RetentionDays.TWO_WEEKS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ---------------------------------------------------------------- lambda

    this.orchestrator = new lambda.Function(this, 'Orchestrator', {
      functionName: `${n.orchestratorFn}${cfg.suffix}`,
      // scripts/build_lambda.py is the authoritative packager (manylinux
      // wheels, runtime-provided excludes, a measured size budget). CDK points
      // at its output rather than re-implementing bundling — infra/docs/03
      // calls the alternative "not recommended" because a second packager
      // diverges from the first's careful exclude list.
      code: lambda.Code.fromAsset(cfg.lambdaAssetPath),
      handler: 'src.handler.lambda_handler',
      runtime: lambda.Runtime.PYTHON_3_13,
      // x86_64 to match what CI verifies. Architecture is immutable after
      // create, and the package carries compiled wheels — docs/ARCHITECTURE §3.
      architecture: lambda.Architecture.X86_64,
      memorySize: 1024,
      timeout: cdk.Duration.seconds(30),
      role,
      tracing: lambda.Tracing.ACTIVE,
      logGroup,
      environment: {
        USE_DYNAMODB: '1',
        USE_BEDROCK: '1',
        REQUIRE_GUARDRAIL: '1',
        BEDROCK_GUARDRAIL_ID: cfg.guardrailId,
        // NUMBERED, never DRAFT. DRAFT moves, so evidence gathered against it
        // describes whatever the policy was that day; IAM deliberately does not
        // grant it either. docs/ARCHITECTURE.md §3f is what happens when the
        // applied version and the reviewed version drift apart.
        BEDROCK_GUARDRAIL_VERSION: cfg.guardrailVersion,
        CORS_ORIGIN: cfg.corsOrigin,
        LOG_LEVEL: 'INFO',
        POWERTOOLS_SERVICE_NAME: 'grocery-orchestrator',
        POWERTOOLS_METRICS_NAMESPACE: 'GroceryOrchestrator',
        // Req 12.5's fail-closed check reads APP_STAGE and does nothing when it
        // is unset -- which is how the check stayed inert after being
        // implemented (docs/ARCHITECTURE.md §3g). Setting it FROM cfg.stage
        // means arming is a consequence of deploying a production stage rather
        // than a second thing somebody has to remember. `cfg.isProduction` and
        // the handler's PRODUCTION_STAGES now read the same config/stages.json,
        // so synth and runtime cannot disagree about what production means.
        APP_STAGE: cfg.stage,
        // POWERTOOLS_LOGGER_LOG_EVENT is deliberately ABSENT. Setting it true
        // dumps the whole API Gateway event — which contains the shopper's
        // message — into CloudWatch, turning a config change into a privacy
        // incident (Req 11.5, design.md §12.4).
      },
    });

    // SnapStart on published versions. The alias is what the API integrates,
    // because SnapStart only benefits published versions and an integration
    // pointed at the unqualified ARN silently forfeits it while still working.
    (this.orchestrator.node.defaultChild as lambda.CfnFunction).snapStart = {
      applyOn: 'PublishedVersions',
    };

    this.alias = new lambda.Alias(this, 'Live', {
      aliasName: 'live',
      version: this.orchestrator.currentVersion,
    });

    // NO `grantReadData` / `grantReadWriteData` HERE, DELIBERATELY, AND THIS IS
    // NOT A STYLE PREFERENCE. Those helpers ADD a second statement on top of the
    // JSON above rather than checking it, and their action sets are the CDK's
    // idea of "read" and "write", not this project's:
    //
    //   - `grantReadData(products)` grants `dynamodb:Scan` on the table AND on
    //     `index/*`. Pilot Task 6b REMOVED Scan on 2026-08-30 once
    //     `candidates_for_budget` moved to GSI2, and
    //     `config/iam-orchestrator-role.json` says why in its own comment: "a
    //     Scan permission nothing needs is a Scan somebody can reintroduce
    //     without noticing." That is exactly what this line did, one commit
    //     later, in a plane that is deployed. It also widens GSI1/GSI2 to
    //     `index/*` and adds Streams reads against a table with no stream.
    //   - `grantReadWriteData(idempotency)` grants `DeleteItem` and
    //     `BatchWriteItem`. The JSON grants GetItem/PutItem/UpdateItem and says
    //     "No Delete -- expiry is by TTL, which requires no permission."
    //
    // The role already carries exactly the statements the JSON declares, with
    // explicit index ARNs. Anything a grant helper would add is by definition
    // something nobody wrote down. `infra/test/service-stack.test.ts` asserts
    // the resulting action sets per resource, so this cannot come back quietly.
    //
    // `tables` is still a required prop: it is what makes StatefulStack a
    // dependency of this stack, so the adopted names resolve from one place.
    void tables;

    // ---------------------------------------------------------------- SSM

    // Published so an operator can retune without a Lambda release. The code
    // still reads the bundled files today — config/ ships inside the archive —
    // so this is the forward path, not a live control. infra/docs/08 §6 records
    // that gap; wiring the code to read SSM is a separate application task and
    // pretending otherwise would be claiming a capability that does not exist.
    //
    // BOTH OF THESE USED TO BE `readFileSync(...).slice(0, 4096)`. That is not a
    // smaller config file, it is INVALID JSON published under a name that
    // invites someone to load it: config/models.json is 10,930 bytes, and
    // `json.loads` on the first 4,096 fails at line 132. Nothing broke only
    // because nothing reads it yet, which is the worst reason for a defect to
    // stay hidden. A silent cap is precisely what this repository refuses
    // everywhere else, and `publishJson` below throws at synth instead.
    //
    // The tier does not rescue it either: SSM standard caps at 4 KB and
    // advanced at 8 KB, so a 10,930-byte file fits NEITHER. This needed a shape
    // change, not a flag.
    //
    // SO WHAT IS PUBLISHED IS THE ROUTING BLOCK, NOT THE WHOLE FILE. Routing is
    // the part Task 7b would let an operator retune — which model serves which
    // task. `scorecards` is 4,868 of the 10,930 bytes and is measured evidence
    // rather than a knob; an operator who could edit it could qualify a route by
    // typing, which is the one thing the qualification gate exists to prevent.
    // `models` is a capability inventory that changes with a deploy, not with an
    // operator's judgement. Neither belongs behind a console text box.
    const models = JSON.parse(fs.readFileSync(cfg.configFiles.models, 'utf-8'));
    publishJson(this, 'ModelsParam', {
      // Renamed from `…/models`: the old name promised the whole file. Changing
      // the parameterName replaces the resource, which also removes the
      // truncated value currently sitting in /grocery/dev-cdk/models.
      parameterName: `/grocery/${cfg.stage}${cfg.suffix}/models/routing`,
      value: {
        _comment:
          'Routing only, from config/models.json. Scorecards and the model ' +
          'inventory are deliberately NOT published here - see service-stack.ts.',
        version: models.version,
        region: models.region,
        default_policy: models.default_policy,
        routing: models.routing,
      },
      description: 'config/models.json routing block. NOT read at runtime yet - infra/docs/08 §6.',
    });
    publishJson(this, 'FeasibilityParam', {
      parameterName: `/grocery/${cfg.stage}${cfg.suffix}/feasibility`,
      // Verbatim: 2,859 bytes today, and it fits. The guard is here anyway
      // because "it fits today" is what the models parameter could have said
      // once too, and the day it stops fitting is the day the old code would
      // have started publishing a fragment instead of failing.
      value: JSON.parse(fs.readFileSync(cfg.configFiles.feasibility, 'utf-8')),
      description: 'config/feasibility.json. NOT read at runtime yet - see infra/docs/08 §6.',
    });

    // ---------------------------------------------------------------- API

    this.api = new apigateway.RestApi(this, 'Api', {
      // MUST match config/alarms.json's ApiName dimension, or the api-5xx alarm
      // watches a metric with no datapoints — which looks exactly like a
      // healthy service. config/alarms.json says so in its own comment.
      restApiName: `${n.restApi}${cfg.suffix}`,
      description: 'Smart Grocery orchestrator. POST /chat returns the event contract.',
      endpointConfiguration: { types: [apigateway.EndpointType.REGIONAL] },
      deployOptions: {
        stageName: cfg.stage,
        throttlingRateLimit: 5,
        throttlingBurstLimit: 10,
        tracingEnabled: true,
        metricsEnabled: true,
        // dataTraceEnabled stays OFF: it logs request/response bodies, and a
        // body here is the shopper's message (Req 11.5).
        dataTraceEnabled: false,
      },
    });

    // CORS is handled by the HANDLER, not by a MOCK preflight. src/handler.py
    // emits its own CORS headers and answers OPTIONS itself, so an API-level
    // preflight would produce duplicate Access-Control-Allow-Origin headers,
    // which browsers reject. Both methods proxy to the alias instead.
    const chat = this.api.root.addResource('chat');
    const integration = new apigateway.LambdaIntegration(this.alias, { proxy: true });
    chat.addMethod('POST', integration);
    chat.addMethod('OPTIONS', integration);

    // security.md line 22: every stage has throttling AND a usage plan. The
    // stage had throttling and there was no usage plan at all until 2026-08-30,
    // so half the control was missing. No API key: the pilot is anonymous, and
    // a usage plan throttles without one.
    const plan = this.api.addUsagePlan('UsagePlan', {
      name: `${n.restApi}${cfg.suffix}-plan`,
      throttle: { rateLimit: 5, burstLimit: 10 },
    });
    plan.addApiStage({ stage: this.api.deploymentStage });

    // ---------------------------------------------------------------- outputs

    new cdk.CfnOutput(this, 'ApiUrl', {
      value: `${this.api.url}chat`,
      description: 'POST here. Compare against the hand-made service before any cutover.',
    });
    new cdk.CfnOutput(this, 'AliasArn', { value: this.alias.functionArn });
    new cdk.CfnOutput(this, 'LogGroupName', {
      value: logGroup.logGroupName,
      description: 'Retention 14 days. The hand-made group has none (never expire).',
    });
  }
}
