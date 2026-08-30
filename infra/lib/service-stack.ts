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

    tables.products.grantReadData(role);
    tables.idempotency.grantReadWriteData(role);

    // ---------------------------------------------------------------- SSM

    // Published so an operator can retune without a Lambda release. The code
    // still reads the bundled files today — config/ ships inside the archive —
    // so this is the forward path, not a live control. infra/docs/08 §6 records
    // that gap; wiring the code to read SSM is a separate application task and
    // pretending otherwise would be claiming a capability that does not exist.
    new ssm.StringParameter(this, 'ModelsParam', {
      parameterName: `/grocery/${cfg.stage}${cfg.suffix}/models`,
      stringValue: fs.readFileSync(cfg.configFiles.models, 'utf-8').slice(0, 4096),
      description: 'config/models.json. NOT read at runtime yet - see infra/docs/08 §6.',
    });
    new ssm.StringParameter(this, 'FeasibilityParam', {
      parameterName: `/grocery/${cfg.stage}${cfg.suffix}/feasibility`,
      stringValue: fs.readFileSync(cfg.configFiles.feasibility, 'utf-8').slice(0, 4096),
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
