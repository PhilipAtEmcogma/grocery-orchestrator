/**
 * SCAFFOLD — deployment configuration for the Smart Grocery CDK app.
 *
 * Centralises the physical resource names, the production-mode flags, and the
 * paths to the repo's config-as-data files. Names are defined ONCE here because
 * they are a contract between stacks: config/alarms.json binds its metric filter
 * to the log group `/aws/lambda/grocery-orchestrator-dev` and its API-5xx alarm
 * to `ApiName = grocery-orchestrator-api-dev`. If a stack names a resource
 * anything else, the alarms watch nothing. See infra/docs/02-CDK-SCAFFOLD.md §5.
 */
import * as path from 'path';

export interface GroceryConfig {
  readonly stage: string;
  readonly isProduction: boolean;

  // Physical names (the -dev suffix lets generated resources coexist with the
  // manually-created ones during the migration — config/*.json headers).
  readonly names: {
    readonly productsTable: string;
    readonly idempotencyTable: string;
    readonly mealsTable: string; // planned (Pilot Task 15)
    readonly orchestratorFn: string;
    readonly ingestionFn: string;
    readonly orchestratorRole: string;
    readonly ingestionRole: string;
    readonly restApi: string;
    readonly alarmTopic: string;
    readonly orchestratorLogGroup: string;
  };

  // Repo config-as-data files the stacks read at synth (infra/docs/02 §6).
  readonly configFiles: {
    readonly models: string;
    readonly guardrail: string;
    readonly alarms: string;
    readonly iamOrchestrator: string;
    readonly iamIngestion: string;
    readonly stateMachine: string;
    readonly feasibility: string;
  };

  // Path to the built Lambda archive (scripts/build_lambda.py → build/lambda.zip).
  readonly lambdaAssetPath: string;

  // Production-mode required settings (security.md / tech.md fail-closed).
  // For a future `prod` stage, synth should THROW if these are not real.
  readonly requireGuardrail: boolean;
  readonly corsOrigin: string; // never "*" in production mode
}

const REPO_ROOT = path.resolve(__dirname, '..', '..'); // infra/lib -> repo root

export function loadConfig(stage: string): GroceryConfig {
  const isProduction = stage === 'prod';
  const suffix = stage; // dev | prod

  const cfg: GroceryConfig = {
    stage,
    isProduction,
    names: {
      productsTable: `grocery-products-${suffix}`,
      idempotencyTable: `grocery-idempotency-${suffix}`,
      mealsTable: `grocery-meals-${suffix}`,
      orchestratorFn: `grocery-orchestrator-${suffix}`,
      ingestionFn: `grocery-ingestion-${suffix}`,
      orchestratorRole: `grocery-orchestrator-${suffix}-role`,
      ingestionRole: `grocery-ingestion-${suffix}-role`,
      restApi: `grocery-orchestrator-api-${suffix}`,
      alarmTopic: `grocery-orchestrator-alarms-${suffix}`,
      orchestratorLogGroup: `/aws/lambda/grocery-orchestrator-${suffix}`,
    },
    configFiles: {
      models: path.join(REPO_ROOT, 'config', 'models.json'),
      guardrail: path.join(REPO_ROOT, 'config', 'guardrail.json'),
      alarms: path.join(REPO_ROOT, 'config', 'alarms.json'),
      iamOrchestrator: path.join(REPO_ROOT, 'config', 'iam-orchestrator-role.json'),
      iamIngestion: path.join(REPO_ROOT, 'config', 'iam-ingestion-role.json'),
      stateMachine: path.join(REPO_ROOT, 'config', 'ingestion-state-machine.json'),
      feasibility: path.join(REPO_ROOT, 'config', 'feasibility.json'),
    },
    lambdaAssetPath: path.join(REPO_ROOT, 'build', 'lambda.zip'),
    requireGuardrail: true,
    // Pilot/dev may use "*" while non-production; a real origin is injected from
    // the FrontendStack's CloudFront domain (two-pass deploy, infra/docs/06 §3d).
    corsOrigin: (process.env.CORS_ORIGIN as string) ?? '*',
  };

  // Fail-closed for production (security.md). Implement these assertions when a
  // prod stage is introduced; left permissive for the anonymous dev pilot.
  if (isProduction) {
    if (cfg.corsOrigin === '*') {
      throw new Error('Production mode refuses wildcard CORS (security.md). Set CORS_ORIGIN.');
    }
    // TODO: assert USE_DYNAMODB/USE_BEDROCK=1, numbered Guardrail version, named resources.
  }

  return cfg;
}
