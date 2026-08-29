/**
 * SCAFFOLD — ServiceStack (Pilot Task 10): the deployable service plane.
 *
 * STATUS: stub. Constructs are NOT created yet — implement against
 * infra/docs/03-STACK-SPECS.md → ServiceStack and infra/docs/04-SECURITY.md.
 *
 * Contains, once implemented:
 *   - orchestrator Lambda (Python 3.13, zip, handler src.handler.lambda_handler)
 *     + SnapStart on a PUBLISHED alias `live`  (container forfeits SnapStart)
 *   - API Gateway REST `grocery-orchestrator-api-dev` (CORS strict, throttling,
 *     usage plan; Cognito authorizer seam left for later)
 *   - Bedrock Guardrail (CfnGuardrail from config/guardrail.json) + numbered version
 *   - two least-privilege IAM roles (from config/iam-*.json) — NEVER merged
 *   - SSM params for models.json + feasibility floor
 *   - log group /aws/lambda/grocery-orchestrator-dev with finite retention
 *
 * The env-var contract is in infra/docs/01-ARCHITECTURE.md §7. In particular,
 * POWERTOOLS_LOGGER_LOG_EVENT MUST NOT be set true (privacy, Req 11.5).
 */
import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';
import { GroceryConfig } from './config';
import { StatefulStack } from './stateful-stack';

export interface ServiceStackProps extends cdk.StackProps {
  readonly cfg: GroceryConfig;
  readonly tables: StatefulStack;
}

export class ServiceStack extends cdk.Stack {
  // Exposed for the ObservabilityStack (alarm on 5xx) and IngestionStack (asset).
  public api?: apigateway.RestApi;
  public orchestrator?: lambda.Function;
  public asset?: lambda.Code;

  constructor(scope: Construct, id: string, props: ServiceStackProps) {
    super(scope, id, props);
    // const { cfg, tables } = props;

    // TODO: this.asset = lambda.Code.fromAsset(cfg.lambdaAssetPath);  // build/lambda.zip

    // TODO: orchestrator role from cfg.configFiles.iamOrchestrator (04-SECURITY §2)
    //   - Bedrock invoke (4 inference-profile ARNs + 4 wildcarded foundation-model ARNs)
    //   - Bedrock ApplyGuardrail (numbered version ARN, never DRAFT)
    //   - DynamoDB READ products (+ GSI1); Get/Put/Update idempotency; NO write to products
    //   - X-Ray (the one justified "*")

    // TODO: this.orchestrator = new lambda.Function(...) memory 512-1024, timeout 30s,
    //   tracing ACTIVE, logRetention 14d, env = the 01 §7 contract.
    // TODO: SnapStart on a published alias `live`; API integrates the alias, not $LATEST.

    // TODO: Guardrail = new bedrock.CfnGuardrail(...) from cfg.configFiles.guardrail
    //   + CfnGuardrailVersion; feed id + numbered version into the Lambda env.

    // TODO: this.api = new apigateway.RestApi(cfg.names.restApi, ...) with strict CORS
    //   (cfg.corsOrigin, never "*" in prod), throttling + usage plan; POST /chat → alias.

    // TODO: SSM StringParameters for models.json + feasibility floor (retune w/o deploy).

    cdk.Annotations.of(this).addInfo(
      'ServiceStack is a SCAFFOLD stub — implement per infra/docs/03-STACK-SPECS.md before deploy.',
    );
  }
}
