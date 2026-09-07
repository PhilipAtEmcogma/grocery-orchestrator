/**
 * IngestionStack (Pilot Task 13) — the scheduled price refresh, in IaC at last.
 *
 * WHY THIS STACK WAS THE LARGEST REAL GAP. Until 2026-09-07 this file was a
 * stub with four TODOs, while the plane it describes WAS RUNNING IN THE
 * ACCOUNT — deployed imperatively on 2026-09-04 by `scripts/apply_iam.py` and
 * `scripts/apply_state_machine.py`. It was the only live plane with no template
 * behind it: a Lambda, a Step Functions state machine, an IAM role that can
 * write the serving catalogue, and a scheduler, none of them reproducible and
 * none of them under review.
 *
 * BUILT FROM THE ACCOUNT, NOT FROM THE SPEC. `infra/docs/03` sketched this
 * stack in August and three of its details are now wrong, because the hand-made
 * plane moved and the document did not. Each is corrected below with the
 * evidence rather than followed:
 *
 *   - The spec says `events.Rule` with a UTC cron. The account uses
 *     **EventBridge Scheduler** (`grocery-price-refresh-dev`) with
 *     `Pacific/Auckland` as an explicit timezone — which is strictly better,
 *     because it removes the NZST/NZDT drift the spec's own note apologises
 *     for. Scheduler is what this stack builds.
 *   - The spec says a 60-second timeout. The account says **120**, and a
 *     refresh over 2,759 real rows is why. The account wins.
 *   - The spec's Lambda has no environment block. The account carries
 *     `PRICE_SOURCE=lineage_b`, which is the 2026-09-04 decision that made the
 *     refresh read the real catalogue rather than the fixtures.
 *
 * STANDS BESIDE, DOES NOT COLLIDE. Every created resource carries `cfg.suffix`
 * (`-cdk` by default), exactly as `service-stack.ts` does, so this deploys
 * alongside the hand-made plane rather than fighting it for a name. The
 * observability stack learned that lesson the expensive way: CloudFormation
 * REFUSES to create over an existing resource (§3x), so a stack that wants a
 * name something else already holds does not deploy at all.
 *
 * THE TABLES ARE ADOPTED, NEVER CREATED. `props.tables` hands over an `ITable`
 * from `StatefulStack`, whose template holds no table resource. This stack
 * grants against them and cannot replace them — which matters more here than
 * anywhere else in the app, because this is the only role in the system with
 * write access to the 2,759-row serving catalogue.
 */
import * as fs from 'fs';
import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as stepfunctions from 'aws-cdk-lib/aws-stepfunctions';
import { Construct } from 'constructs';
import { GroceryConfig } from './config';
import { StatefulStack } from './stateful-stack';

export interface IngestionStackProps extends cdk.StackProps {
  readonly cfg: GroceryConfig;
  readonly tables: StatefulStack;
}

export class IngestionStack extends cdk.Stack {
  public readonly ingestion: lambda.Function;
  public readonly stateMachine: stepfunctions.StateMachine;

  constructor(scope: Construct, id: string, props: IngestionStackProps) {
    super(scope, id, props);
    const { cfg } = props;
    const n = cfg.names;

    // ---------------------------------------------------------------- IAM

    // Same shape as service-stack.ts: statements come from the JSON verbatim
    // with `${AWS_*}` resolved from the DEPLOY IDENTITY, never a literal.
    // `tests/test_config_placeholders.py` fails the build if a twelve-digit
    // account id reappears in `config/`, and this must not be the path that
    // reintroduces one.
    //
    // Reading the file rather than restating the policy in TypeScript is the
    // rule the whole `infra/` tree follows: `scripts/apply_iam.py` applies the
    // same document, and porting it here would create the second source of
    // truth the migration exists to remove. The comments in that file — why
    // Query is granted (so ingestion can diff before it writes) and why the
    // history statement has no Query (a baseline you can read back is one you
    // can be tempted to rewrite) — are the reasoning this stack must not lose.
    const iamConfig = JSON.parse(fs.readFileSync(cfg.configFiles.iamIngestion, 'utf-8'));

    const role = new iam.Role(this, 'IngestionRole', {
      roleName: `${n.ingestionRole}${cfg.suffix}`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: iamConfig.description,
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

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

    // NO `tables.products.grantWriteData(role)`, and this is the same refusal
    // service-stack.ts makes at length. The grant helpers ADD a statement on
    // top of the JSON rather than checking it, with the CDK's idea of "write"
    // — which includes `DeleteItem` and `UpdateItem`. The ingestion role is
    // deliberately allowed neither on the history table: a history row is
    // immutable by definition, and a role that could rewrite one could rewrite
    // the baseline a deviation is measured against. A convenience helper would
    // hand it exactly that.

    // -------------------------------------------------------------- logs

    // FINITE RETENTION, which the hand-made ingestion log group does not have:
    // `/aws/lambda/grocery-ingestion-dev` returns `retentionInDays: null`,
    // meaning never expire. infra/docs/04 requires finite retention, and a log
    // that never expires turns any future logging mistake into a permanent one.
    //
    // A REAL `LogGroup`, NOT THE `logRetention` PROP, matching
    // service-stack.ts. `logRetention` is deprecated, and more to the point it
    // is implemented as a CUSTOM RESOURCE: it synthesises an extra Lambda
    // function, an extra role and an extra policy whose entire job is to call
    // `PutRetentionPolicy` once. Three resources and a second function in the
    // account to express a number. Declaring the log group states the same
    // thing as one resource CloudFormation owns directly.
    const logGroup = new logs.LogGroup(this, 'IngestionLogs', {
      logGroupName: `/aws/lambda/${n.ingestionFn}${cfg.suffix}`,
      retention: logs.RetentionDays.TWO_WEEKS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ------------------------------------------------------------- Lambda

    this.ingestion = new lambda.Function(this, 'Ingestion', {
      functionName: `${n.ingestionFn}${cfg.suffix}`,
      // THE SAME ARCHIVE THE ORCHESTRATOR USES. One asset, two functions,
      // entered at different handlers — `scripts/build_lambda.py` explains why
      // (two zips would be two builds to keep in step for about 10 KB of
      // Python). The functions stay separate: separate roles, separate
      // invocation paths, and only the artefact is shared.
      code: lambda.Code.fromAsset(cfg.lambdaAssetPath),
      handler: 'ingestion.handler.lambda_handler',
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.X86_64,
      role,
      // 512 MB and 120 s, both read from the deployed function rather than
      // from infra/docs/03, which says 60 s. A refresh walks 2,759 rows,
      // diffs each against the table and writes the changed ones; 60 s was a
      // guess made before the real catalogue existed.
      memorySize: 512,
      timeout: cdk.Duration.seconds(120),
      tracing: lambda.Tracing.ACTIVE,
      environment: {
        PRODUCTS_TABLE: n.productsTable,
        // The 2026-09-04 decision (config/data-sources.json): the refresh reads
        // the data team's collected catalogue, not the fixtures. Before it, the
        // deployed function ran the fixture default and reported a successful
        // refresh that wrote nothing.
        PRICE_SOURCE: 'lineage_b',
        // SET EXPLICITLY, where the hand-made function relies on the code
        // default. `ingestion/handler.py` falls back to this exact name, so
        // the behaviour is identical — but a table this role writes to should
        // be visible in the function's configuration rather than only in a
        // Python constant. The IAM statement names it; the environment should
        // agree.
        PRICE_HISTORY_TABLE: n.priceHistoryTable,
      },
      logGroup,
    });

    // ------------------------------------------------------ state machine

    // The ASL comes from `config/ingestion-state-machine.json` verbatim, for
    // the reason the IAM does: it is a carefully commented document and the
    // comments are load-bearing. Two of them record real defects —
    // `ResultPath: null` because Map items here are STRINGS and a ResultPath
    // on a non-object raises `States.ResultPathMatchFailure`, which would abort
    // the Map that the Catch exists to protect; and the Retry list covering
    // transient Lambda errors ONLY, so a `ValueError` from an unknown retailer
    // fails fast instead of being retried three times.
    //
    // Rebuilding it with the L2 `stepfunctions-tasks` API (infra/docs/08 §4's
    // alternative) would give type-checked retries and would silently drop
    // every one of those comments.
    //
    // `${AWS_*}` are resolved as in the IAM above, and the FUNCTION NAME is
    // rewritten to this stack's suffixed function. Without that rewrite the
    // CDK state machine would invoke the HAND-MADE Lambda — a plane that looks
    // independent while sharing the half that writes to the catalogue, which
    // is the worst of both arrangements.
    // THE COMMENTS MUST BE STRIPPED BEFORE SUBMISSION, and finding that out
    // cost a failed deploy. Step Functions validates the definition at CREATE
    // time, not at synth: `cdk synth` rendered this happily and CloudFormation
    // answered
    //
    //   SCHEMA_VALIDATION_FAILED: Field '_comment' is not supported at
    //   /States/RefreshAllRetailers/ItemProcessor/States/RefreshOneRetailer/Catch[0]
    //
    // ASL allows `Comment` on a STATE and rejects unknown members elsewhere, so
    // the `_comment` this repo uses to explain the Catch — the one recording
    // why `ResultPath` is null — is exactly the kind of annotation the service
    // refuses. `scripts/apply_state_machine.py` has stripped both since it was
    // written; this stack did not, which is what made the two paths disagree
    // about the same file.
    //
    // Mirrors `strip_comments()` there deliberately, including dropping
    // `Comment` as well as `_comment`. Two mechanisms reading one config file
    // must apply the same transform or the file means two different things.
    const stripComments = (value: unknown): unknown => {
      if (Array.isArray(value)) return value.map(stripComments);
      if (value && typeof value === 'object') {
        return Object.fromEntries(
          Object.entries(value as Record<string, unknown>)
            .filter(([k]) => k !== 'Comment' && k !== '_comment')
            .map(([k, v]) => [k, stripComments(v)]),
        );
      }
      return value;
    };

    const asl = JSON.stringify(
      stripComments(JSON.parse(fs.readFileSync(cfg.configFiles.stateMachine, 'utf-8'))),
    )
      .replace(/\$\{AWS_REGION\}/g, this.region)
      .replace(/\$\{AWS_ACCOUNT_ID\}/g, this.account)
      .replace(
        new RegExp(`function:${n.ingestionFn}(?![\\w-])`, 'g'),
        `function:${n.ingestionFn}${cfg.suffix}`,
      );

    this.stateMachine = new stepfunctions.StateMachine(this, 'Refresh', {
      stateMachineName: `${n.ingestionFn}${cfg.suffix}`,
      definitionBody: stepfunctions.DefinitionBody.fromString(asl),
      tracingEnabled: true,
      timeout: cdk.Duration.minutes(15),
    });

    // Least privilege on the state machine too: it may invoke exactly this
    // function and nothing else. `grantInvoke` scopes to the function ARN.
    this.ingestion.grantInvoke(this.stateMachine);

    // ----------------------------------------------------------- schedule

    // EVENTBRIDGE SCHEDULER, NOT AN EVENTBRIDGE RULE. `infra/docs/03` specifies
    // a Rule with a UTC cron and then apologises for the DST drift that causes
    // — NZST is UTC+12 and NZDT is UTC+13, so a fixed UTC cron moves an hour
    // twice a year. The account already uses Scheduler with
    // `Pacific/Auckland` as an explicit timezone, which removes the problem
    // rather than noting it. Following the account is both more correct and
    // less work.
    //
    // L1 `CfnSchedule` rather than an L2: the L2 scheduler constructs are in a
    // separate alpha module, and an alpha dependency in the stack that writes
    // to the serving catalogue is not a trade worth making for nicer syntax.
    const schedulerRole = new iam.Role(this, 'SchedulerRole', {
      roleName: `grocery-scheduler-${cfg.stage}${cfg.suffix}-role`,
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
      description: 'Lets EventBridge Scheduler start the ingestion state machine, and nothing else.',
    });
    this.stateMachine.grantStartExecution(schedulerRole);

    new scheduler.CfnSchedule(this, 'DailyRefresh', {
      name: `grocery-price-refresh-${cfg.stage}${cfg.suffix}`,
      // CREATED DISABLED, matching the account and matching the argument in
      // `config/data-sources.json`: `LineageBSource.CAPTURED_AT` is the
      // constant 2026-08-28 and the dataset is a one-off snapshot, so a daily
      // run rewrites the same rows with the same capture date. It would cost
      // money, write to the serving catalogue nightly, and change nothing.
      //
      // The hand-made schedule was ENABLED when the 2026-08-30 account audit
      // recorded it and is DISABLED now; nobody wrote down the change. Making
      // the state EXPLICIT here — and config-driven, so enabling it is a
      // reviewed edit rather than a console click — is the fix for that.
      state: cfg.ingestionScheduleEnabled ? 'ENABLED' : 'DISABLED',
      scheduleExpression: 'cron(0 3 * * ? *)',
      // The whole reason for Scheduler over a Rule.
      scheduleExpressionTimezone: 'Pacific/Auckland',
      flexibleTimeWindow: { mode: 'OFF' },
      target: {
        arn: this.stateMachine.stateMachineArn,
        roleArn: schedulerRole.roleArn,
        // ALL THREE RETAILERS, including the one with no rows. Woolworths
        // fetches 0 every run, and that is deliberate: it makes the
        // two-chain coverage gap a recurring number in the execution history
        // beside two branches writing ~1,380 each, rather than a claim in
        // docs/OPEN-REVIEW-chain-coverage.md that nobody re-reads.
        input: JSON.stringify({ retailers: ['paknsave', 'woolworths', 'new_world'] }),
      },
    });

    // ------------------------------------------------------------ outputs

    new cdk.CfnOutput(this, 'IngestionFunctionName', { value: this.ingestion.functionName });
    new cdk.CfnOutput(this, 'StateMachineArn', { value: this.stateMachine.stateMachineArn });
    new cdk.CfnOutput(this, 'ScheduleState', {
      value: cfg.ingestionScheduleEnabled ? 'ENABLED' : 'DISABLED',
      description: 'Set INGESTION_SCHEDULE=1 to enable. See config/data-sources.json for why it is off.',
    });
  }
}
