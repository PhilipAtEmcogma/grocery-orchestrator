/**
 * Deployment configuration for the Smart Grocery CDK app.
 *
 * Centralises the physical resource names, the production-mode flags, and the
 * paths to the repo's config-as-data files. Names are defined ONCE here because
 * they are a contract between stacks: config/alarms.json binds its metric filter
 * to the log group `/aws/lambda/grocery-orchestrator-dev` and its API-5xx alarm
 * to `ApiName = grocery-orchestrator-api-dev`. If a stack names a resource
 * anything else, the alarms watch nothing. See infra/docs/02-CDK-SCAFFOLD.md §5.
 */
import * as fs from 'fs';
import * as path from 'path';

export interface GroceryConfig {
  readonly stage: string;
  readonly isProduction: boolean;

  /**
   * Appended to every physical name this app creates.
   *
   * A hand-made service plane is already serving on `woqmel35lk`, and its
   * names are the ones below. Deploying CDK resources with identical names
   * would not adopt them -- CloudFormation would try to CREATE them and fail.
   * A suffix lets the CDK plane stand beside the running one so it can be
   * verified before anything is cut over; set it to '' once the hand-made
   * resources are retired. See infra/docs/08 §10.
   */
  readonly suffix: string;

  /** Numbered Guardrail version, never DRAFT (docs/ARCHITECTURE.md §3f). */
  readonly guardrailId: string;
  readonly guardrailVersion: string;

  /**
   * Suffix on the names of the ADOPTED tables -- a different axis from `stage`.
   *
   * `grocery-products-dev` is the name of a table that EXISTS and holds 2,759
   * real price records. It does not become `grocery-products-prod` because
   * somebody synthesised a prod stack, and until 2026-08-31 it did: the adopted
   * names were derived from the stage, so the first real `stage=prod` synth
   * would have referenced two tables that do not exist and adopted nothing --
   * a stack that deploys clean and grants access to nothing.
   *
   * Adoption points at something already there, so its name is an INPUT.
   * `DATA_SUFFIX` overrides it the day a second data environment exists.
   */
  readonly dataSuffix: string;

  // Physical names. Two groups, and the distinction is the point:
  //   - CREATED by this app: named from the stage plus `suffix`.
  //   - ADOPTED from the account: named from `dataSuffix`, never the stage.
  readonly names: {
    readonly productsTable: string;
    readonly idempotencyTable: string;
    readonly mealsTable: string; // planned (Pilot Task 15)
    // CREATED by this app, but named from `dataSuffix` rather than the stage.
    // It is the one table that is both, and the reason is that `src/history`
    // hardcodes `grocery-price-history-dev` and the ingestion Lambda defaults
    // `PRICE_HISTORY_TABLE` to it. A `-cdk`-suffixed table would be a table the
    // running code cannot find, which is worse than no table: the write would
    // still fail and the name would suggest it should not.
    readonly priceHistoryTable: string;
    readonly orchestratorFn: string;
    readonly ingestionFn: string;
    readonly orchestratorRole: string;
    readonly ingestionRole: string;
    readonly restApi: string;
    readonly alarmTopic: string;
    readonly orchestratorLogGroup: string;
    // AgentCore Runtime reviewer (ADR 0002 WS2). CREATED by this app.
    readonly reviewerRuntime: string;
    readonly reviewerRole: string;
    readonly reviewerCodeBucket: string;
  };

  // Repo config-as-data files the stacks read at synth (infra/docs/02 §6).
  readonly configFiles: {
    readonly models: string;
    readonly guardrail: string;
    readonly alarms: string;
    readonly iamOrchestrator: string;
    readonly iamIngestion: string;
    readonly iamReviewer: string;
    readonly stateMachine: string;
    readonly feasibility: string;
    readonly stages: string;
  };

  // Path to the built Lambda archive (scripts/build_lambda.py → build/lambda.zip).
  readonly lambdaAssetPath: string;

  // Production-mode required settings (security.md / tech.md fail-closed).
  // For a future `prod` stage, synth should THROW if these are not real.
  readonly requireGuardrail: boolean;
  readonly corsOrigin: string; // never "*" in production mode
}

const REPO_ROOT = path.resolve(__dirname, '..', '..'); // infra/lib -> repo root

const STAGES_FILE = path.join(REPO_ROOT, 'config', 'stages.json');

/**
 * Which stage names mean production, READ FROM `config/stages.json`.
 *
 * This used to be `stage === 'prod'`, while `src/handler.py` independently held
 * `{prod, production, pilot}`. Two halves of Req 12.5 disagreeing about what
 * production means, and the cheaper half -- this one, which runs at synth,
 * before an account is touched -- was the one that fired LESS. `stage=pilot`
 * synthesised happily with wildcard CORS and then failed at Lambda startup.
 *
 * Policy is data in this repository: the guardrail, the alarms, the IAM roles
 * and the routing table are all JSON that more than one runtime reads. The
 * production stage list is policy, so it is data too.
 */
export function productionStages(): ReadonlySet<string> {
  const raw = JSON.parse(fs.readFileSync(STAGES_FILE, 'utf-8'));
  const names: string[] = raw.production_stages;
  if (!Array.isArray(names) || names.length === 0) {
    throw new Error(`config/stages.json has no production_stages list. Refusing to synth: an ` +
      `empty list silently makes every stage non-production, which is the failure ` +
      `direction Req 12.5 exists to close.`);
  }
  return new Set(names.map((n) => n.trim().toLowerCase()));
}

export function loadConfig(stage: string): GroceryConfig {
  const isProduction = productionStages().has(stage.trim().toLowerCase());
  const suffix = stage; // dev | prod

  // Never `stage`. See GroceryConfig.dataSuffix.
  const dataSuffix = process.env.DATA_SUFFIX ?? 'dev';

  const cfg: GroceryConfig = {
    stage,
    isProduction,
    dataSuffix,
    // Default '-cdk' so a first deploy cannot collide with the hand-made
    // plane. Deliberately explicit rather than clever: someone cutting over
    // sets NAME_SUFFIX='' and reads the diff.
    suffix: process.env.NAME_SUFFIX ?? '-cdk',
    guardrailId: process.env.BEDROCK_GUARDRAIL_ID ?? 'b1xezpqe04kx',
    guardrailVersion: process.env.BEDROCK_GUARDRAIL_VERSION ?? '2',
    names: {
      // ---- ADOPTED (dataSuffix, never stage) ----
      productsTable: `grocery-products-${dataSuffix}`,
      idempotencyTable: `grocery-idempotency-${dataSuffix}`,
      mealsTable: `grocery-meals-${dataSuffix}`,
      // dataSuffix, NOT suffix -- see the interface. Created here, but it is a
      // DATA table shared by both service planes, exactly as products and
      // idempotency are, so it must carry the data name.
      priceHistoryTable: `grocery-price-history-${dataSuffix}`,
      // ---- CREATED (stage) ----
      orchestratorFn: `grocery-orchestrator-${suffix}`,
      ingestionFn: `grocery-ingestion-${suffix}`,
      orchestratorRole: `grocery-orchestrator-${suffix}-role`,
      ingestionRole: `grocery-ingestion-${suffix}-role`,
      restApi: `grocery-orchestrator-api-${suffix}`,
      alarmTopic: `grocery-orchestrator-alarms-${suffix}`,
      orchestratorLogGroup: `/aws/lambda/grocery-orchestrator-${suffix}`,
      // AgentCore Runtime name must match [a-zA-Z][a-zA-Z0-9_]{0,47} -- NO
      // hyphens, so the created runtime uses underscores and drops the `-cdk`
      // NAME_SUFFIX (which contains a hyphen). The role and bucket keep the
      // hand-made prototype names (config/iam-reviewer-runtime-role.json), so
      // CDK ADOPTS the role identity the prototype created rather than making a
      // second one -- the same "coexist, then cut over" posture as the service
      // plane, but here the two planes would share one role by name.
      reviewerRuntime: `grocery_reviewer_${suffix.replace(/-/g, '_')}`,
      reviewerRole: `grocery-reviewer-runtime-${suffix}-role`,
      // Just the region suffix; the stack prepends `bedrock-agentcore-code-` and
      // the account id from the deploy identity (never a literal -- the same
      // rule aws_placeholders.py follows). This is the standard AgentCore code
      // bucket the prototype used.
      reviewerCodeBucket: 'bedrock-agentcore-code',
    },
    configFiles: {
      models: path.join(REPO_ROOT, 'config', 'models.json'),
      guardrail: path.join(REPO_ROOT, 'config', 'guardrail.json'),
      alarms: path.join(REPO_ROOT, 'config', 'alarms.json'),
      iamOrchestrator: path.join(REPO_ROOT, 'config', 'iam-orchestrator-role.json'),
      iamIngestion: path.join(REPO_ROOT, 'config', 'iam-ingestion-role.json'),
      iamReviewer: path.join(REPO_ROOT, 'config', 'iam-reviewer-runtime-role.json'),
      stateMachine: path.join(REPO_ROOT, 'config', 'ingestion-state-machine.json'),
      feasibility: path.join(REPO_ROOT, 'config', 'feasibility.json'),
      stages: STAGES_FILE,
    },
    lambdaAssetPath: path.join(REPO_ROOT, 'build', 'lambda.zip'),
    requireGuardrail: true,
    // Pilot/dev may use "*" while non-production; a real origin is injected from
    // the FrontendStack's CloudFront domain (two-pass deploy, infra/docs/06 §3d).
    corsOrigin: (process.env.CORS_ORIGIN as string) ?? '*',
  };

  // Fail-closed for production (security.md). Implement these assertions when a
  // prod stage is introduced; left permissive for the anonymous dev pilot.
  // Fail-closed for production (security.md, Req 12.5). The application makes
  // the same assertions at startup in src/handler.py; this makes them at SYNTH,
  // which is earlier and cheaper -- a stack that cannot be correct should not
  // reach an account.
  if (isProduction) {
    if (cfg.corsOrigin === '*') {
      throw new Error('Production mode refuses wildcard CORS (security.md). Set CORS_ORIGIN.');
    }
    if (!cfg.guardrailId) {
      throw new Error('Production mode requires BEDROCK_GUARDRAIL_ID.');
    }
    if (!/^[0-9]+$/.test(cfg.guardrailVersion)) {
      throw new Error(
        `Production mode requires a NUMBERED Guardrail version, got ` +
          `'${cfg.guardrailVersion}'. DRAFT moves, so evidence gathered against ` +
          `it describes nothing (docs/ARCHITECTURE.md §3f).`,
      );
    }
  }

  return cfg;
}
