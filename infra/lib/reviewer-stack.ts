/**
 * ReviewerStack (ADR 0002 WS2, Pilot Task 14) — the data-quality reviewer's
 * AgentCore Runtime, codified. This is the ADR gate-5 step: "CDK defines the
 * resource" before anything is retained.
 *
 * WHAT IT CODIFIES. The prototype (docs/AGENTCORE-RUNTIME-REVIEWER.md §13) was
 * deployed by hand with the CLI and torn down. Everything it did by hand is a
 * property here: the least-privilege execution role read VERBATIM from
 * config/iam-reviewer-runtime-role.json (the same policy-as-data discipline
 * ServiceStack uses — the JSON is the whole policy, and no grant helper adds to
 * it), and the runtime itself as `AWS::BedrockAgentCore::Runtime` with the exact
 * parameter set §15.3 records: HTTP protocol, PYTHON_3_13, entryPoint main.py,
 * the guardrail env, and the CodeZip in S3.
 *
 * TWO THINGS THIS STACK DELIBERATELY DOES NOT DO, and both are recorded rather
 * than hidden:
 *
 *   1. It does NOT create the S3 code bucket or upload the zip. The bucket is
 *      the standard AgentCore code bucket the prototype made; the zip is built
 *      by scripts/build_reviewer_runtime.py and uploaded out-of-band, because
 *      CDK cannot build an arm64 wheel set at synth. The stack REFERENCES the
 *      object by bucket+key; deploying it requires the zip to be there first.
 *      This mirrors how ServiceStack points at scripts/build_lambda.py's output
 *      rather than re-implementing bundling.
 *
 *   2. It is NOT wired into the shopper path, the ingestion trigger, or any S3
 *      artefact sink. Those are the §5.2 event-driven shape and each is its own
 *      later increment. This stack is the runtime and its role, nothing else.
 *
 * REGION AVAILABILITY CAVEAT — READ BEFORE DEPLOYING. As of this writing the
 * `AWS::BedrockAgentCore::Runtime` CloudFormation resource type is documented as
 * available in us-east-1, us-east-2 and us-west-2. Our region is ap-southeast-2
 * (tech.md, non-negotiable), where the L1 type MAY NOT YET be registered even
 * though the API and CLI both work there (the prototype proved that). So
 * `cdk synth` produces a correct template, but `cdk deploy` will fail with
 * "Unrecognized resource type" until CloudFormation support reaches
 * ap-southeast-2. That is a documented WAIT, not a defect in this stack: the
 * definition is right, and the CLI path (scripts + §15.3) remains the way to
 * deploy in the meantime. When the type lands in Sydney, this stack deploys
 * unchanged. We do NOT move the region to chase the resource type — the whole
 * project is pinned to Sydney.
 */
import * as fs from 'fs';
import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import { GroceryConfig } from './config';

export interface ReviewerStackProps extends cdk.StackProps {
  readonly cfg: GroceryConfig;
}

export class ReviewerStack extends cdk.Stack {
  public readonly role: iam.Role;
  public readonly runtime: cdk.CfnResource;

  constructor(scope: Construct, id: string, props: ReviewerStackProps) {
    super(scope, id, props);
    const { cfg } = props;
    const n = cfg.names;

    // ---------------------------------------------------------------- IAM
    //
    // Same construction as ServiceStack: statements from the JSON verbatim,
    // ${AWS_*} resolved from the deploy identity, NO grant helpers. The role
    // name is the SAME one the prototype created, so this adopts that identity
    // rather than minting a second — config/iam-reviewer-runtime-role.json is
    // the one source of truth for what the reviewer may do, and it is the file
    // scripts/apply_iam.py and this stack both read.

    const iamConfig = JSON.parse(fs.readFileSync(cfg.configFiles.iamReviewer, 'utf-8'));

    this.role = new iam.Role(this, 'ReviewerRole', {
      roleName: n.reviewerRole,
      // Trusts the AgentCore service principal, NOT lambda — this is a runtime,
      // not a Lambda. The JSON's trust_policy says the same; asserted in tests.
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      description: iamConfig.description,
    });

    for (const statement of iamConfig.inline_policy.Statement) {
      const resources = (
        Array.isArray(statement.Resource) ? statement.Resource : [statement.Resource]
      ).map((r: string) =>
        r.replace(/\$\{AWS_REGION\}/g, this.region).replace(/\$\{AWS_ACCOUNT_ID\}/g, this.account),
      );
      this.role.addToPolicy(
        new iam.PolicyStatement({
          sid: statement.Sid,
          effect: iam.Effect.ALLOW,
          actions: Array.isArray(statement.Action) ? statement.Action : [statement.Action],
          resources,
        }),
      );
    }

    // ---------------------------------------------------------------- runtime
    //
    // No L2 construct exists for AgentCore Runtime, so this is an escape-hatch
    // CfnResource of the L1 type. The properties are the §15.3 parameter set,
    // PascalCased to the CloudFormation shape. The CodeZip is referenced by
    // bucket+key; it is built and uploaded out-of-band (see the header).

    const codeBucket = `${n.reviewerCodeBucket}-${this.account}-ap-southeast-2`;

    this.runtime = new cdk.CfnResource(this, 'ReviewerRuntime', {
      type: 'AWS::BedrockAgentCore::Runtime',
      properties: {
        AgentRuntimeName: n.reviewerRuntime,
        Description: 'ADR 0002 WS2 data-quality reviewer (CDK-managed).',
        AgentRuntimeArtifact: {
          CodeConfiguration: {
            Code: { S3: { Bucket: codeBucket, Prefix: 'reviewer/reviewer-runtime.zip' } },
            Runtime: 'PYTHON_3_13',
            EntryPoint: ['main.py'],
          },
        },
        RoleArn: this.role.roleArn,
        NetworkConfiguration: { NetworkMode: 'PUBLIC' },
        // HTTP, never the API default of MCP: the entrypoint speaks
        // /invocations + /ping. This exact value cost a re-derivation in the
        // prototype; it is pinned here so nobody re-derives it.
        ProtocolConfiguration: 'HTTP',
        // Bounds cost: sessions auto-terminate at idle, and never run past
        // maxLifetime. Off the shopper path, so these are generous.
        LifecycleConfiguration: { IdleRuntimeSessionTimeout: 300, MaxLifetime: 1800 },
        EnvironmentVariables: {
          AWS_REGION: 'ap-southeast-2',
          USE_BEDROCK: '1',
          REVIEWER_MODEL_KEY: 'nova-lite',
          // Numbered Guardrail, never DRAFT (security.md). Generation must go
          // through it; the role grants ApplyGuardrail for exactly this.
          BEDROCK_GUARDRAIL_ID: cfg.guardrailId,
          BEDROCK_GUARDRAIL_VERSION: cfg.guardrailVersion,
          REQUIRE_GUARDRAIL: '1',
        },
        Tags: { Project: 'SmartGrocery', Env: cfg.stage, ManagedBy: 'cdk' },
      },
    });

    // ---------------------------------------------------------------- outputs

    new cdk.CfnOutput(this, 'ReviewerRuntimeName', {
      value: n.reviewerRuntime,
      description: 'AgentCore Runtime name (no hyphens; CFN pattern [a-zA-Z][a-zA-Z0-9_]{0,47}).',
    });
    new cdk.CfnOutput(this, 'ReviewerRoleArn', {
      value: this.role.roleArn,
      description: 'Least-privilege reviewer role from config/iam-reviewer-runtime-role.json.',
    });
    new cdk.CfnOutput(this, 'ReviewerCodeLocation', {
      value: `s3://${codeBucket}/reviewer/reviewer-runtime.zip`,
      description:
        'CodeZip built by scripts/build_reviewer_runtime.py and uploaded out-of-band. ' +
        'Must exist before deploy; CDK references it, does not build it.',
    });
    new cdk.CfnOutput(this, 'RegionAvailabilityNote', {
      value:
        'AWS::BedrockAgentCore::Runtime CFN type may not yet be registered in ' +
        'ap-southeast-2. synth is correct; deploy waits for CFN support in Sydney. ' +
        'Use the CLI path (docs/AGENTCORE-RUNTIME-REVIEWER.md §15.3) until then.',
      description: 'Why cdk deploy may fail with Unrecognized resource type today.',
    });
  }
}
