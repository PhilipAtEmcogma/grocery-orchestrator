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

  /**
   * Whether the orchestrator publishes SnapStart-optimised versions.
   *
   * OFF BY DEFAULT SINCE 2026-09-07, AND THIS IS A COST DECISION WITH A
   * MEASURED CAUSE, not a doubt about SnapStart. See
   * `docs/ARCHITECTURE.md` §3x and §3y.
   *
   * SnapStart bills for the cached snapshot of every PUBLISHED VERSION,
   * continuously, whether or not anything invokes it. That is invisible to a
   * request-shaped mental model: on this service, whose invocation charges are
   * literally $0.00, snapshot storage was **79% of September's bill** and put a
   * no-traffic project on course to break its own $25 budget.
   *
   * This plane is the `-cdk` one. The cutover is deferred (§3m), so it serves
   * nobody, and paying for a warm-start optimisation on an endpoint with no
   * users is the clearest possible waste. The HAND-MADE plane keeps SnapStart,
   * because it is the one answering requests and the latency baselines the
   * pilot is measured against (p95 1.94s on a price check) depend on it.
   *
   * TURNING IT BACK ON IS ONE ENV VAR: `SNAPSTART=1 npx cdk deploy
   * Grocery-Service-dev`. Do that before the cutover, not after — the plane
   * that serves shoppers should be the fast one, and §3y is the checklist.
   */
  readonly snapStart: boolean;

  /**
   * Whether the daily ingestion schedule is created ENABLED.
   *
   * OFF BY DEFAULT, and the reason is in `config/data-sources.json`:
   * `LineageBSource.CAPTURED_AT` is the constant `2026-08-28` and the dataset
   * is documented as a one-off snapshot, so a nightly refresh rewrites the same
   * 2,759 rows with the same capture date. It would cost money, write to the
   * serving catalogue every night, and change nothing.
   *
   * THE STATE IS EXPLICIT HERE BECAUSE IT DRIFTED ONCE ALREADY. The 2026-08-30
   * account audit recorded the hand-made schedule as ENABLED; it is DISABLED
   * in the account today and nobody wrote down the change or why. A schedule
   * whose state lives only in the console is one that can flip without a
   * review — in either direction, and the dangerous direction writes to the
   * catalogue.
   *
   * Enable with `INGESTION_SCHEDULE=1` when a source exists that can stamp a
   * NEW capture date — a fresh collection from the data team, or Task 11.4
   * live acquisition. That is the same condition `config/freshness.json` names
   * for reverting `max_price_age_days`, and it is not a coincidence: both are
   * waiting on data that can actually change.
   */
  readonly ingestionScheduleEnabled: boolean;

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

/**
 * Refuse to synthesise against a Lambda archive older than the code it packages.
 *
 * THIS EXISTS BECAUSE IT HAPPENED, on 2026-09-07, in the same session that read
 * §3v ("the orchestrator was five days stale"). `ingestion/stream_guard.py` was
 * written, tested and committed; the deploy used the `build/lambda.zip` sitting
 * on disk from an earlier task; and the function failed on every invocation
 * with `Runtime.ImportModuleError: No module named 'ingestion.stream_guard'`.
 * Three invocations, three retries exhausted, one message in the dead-letter
 * queue — for a file that was correct in git the whole time.
 *
 * `cdk deploy` fingerprints whatever bytes are at `lambdaAssetPath`. It cannot
 * know they are stale, and neither could the operator: the deploy reported
 * success. CI is not the control either — its `infra` job builds the archive
 * before synth, so CI is exactly the environment where this cannot happen and
 * therefore exactly the environment that cannot warn you.
 *
 * The check is a MTIME COMPARISON, not a hash. A hash would mean rebuilding to
 * find out whether a rebuild was needed. Comparing the archive against the
 * newest file in the trees it packages answers the same question for free, and
 * errs toward complaining: touching a file without changing it fails the synth
 * and costs one `python scripts/build_lambda.py`.
 *
 * SKIPPED when the archive is absent, deliberately. `cdk synth` runs in tests
 * and in CI before the build step, and a missing archive is a different, louder
 * failure that CDK already reports.
 */
function assertAssetIsNotStale(assetPath: string): void {
  if (!fs.existsSync(assetPath)) return;

  const builtAt = fs.statSync(assetPath).mtimeMs;
  // The Python trees `scripts/build_lambda.py` copies in. `config/` and
  // `fixtures/` ship too and are checked for the same reason: a routing or
  // feasibility change that never reaches the archive is as invisible as a
  // missing module, and quieter.
  const packaged = ['src', 'ingestion', 'config', 'fixtures'];

  let newest = 0;
  let newestPath = '';
  const walk = (dir: string): void => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === '__pycache__' || entry.name.endsWith('.pyc')) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
        continue;
      }
      const mtime = fs.statSync(full).mtimeMs;
      if (mtime > newest) {
        newest = mtime;
        newestPath = full;
      }
    }
  };
  for (const tree of packaged) {
    const dir = path.join(REPO_ROOT, tree);
    if (fs.existsSync(dir)) walk(dir);
  }

  if (newest > builtAt) {
    const behind = Math.round((newest - builtAt) / 1000);
    throw new Error(
      `build/lambda.zip is STALE: ${path.relative(REPO_ROOT, newestPath)} is ${behind}s ` +
        `newer than the archive. Deploying now would ship code that is not in the ` +
        `repository — on 2026-09-07 exactly that produced ` +
        `Runtime.ImportModuleError for a module that was committed and passing its ` +
        `tests. Run: python scripts/build_lambda.py`,
    );
  }
}

export function loadConfig(stage: string): GroceryConfig {
  const isProduction = productionStages().has(stage.trim().toLowerCase());
  const suffix = stage; // dev | prod

  // Never `stage`. See GroceryConfig.dataSuffix.
  const dataSuffix = process.env.DATA_SUFFIX ?? 'dev';

  // Resolved and CHECKED before the config is handed to any stack, so a stale
  // archive fails at synth rather than at the first invocation in the account.
  const assetPath = path.join(REPO_ROOT, 'build', 'lambda.zip');
  assertAssetIsNotStale(assetPath);

  const cfg: GroceryConfig = {
    stage,
    isProduction,
    dataSuffix,
    // Default '-cdk' so a first deploy cannot collide with the hand-made
    // plane. Deliberately explicit rather than clever: someone cutting over
    // sets NAME_SUFFIX='' and reads the diff.
    suffix: process.env.NAME_SUFFIX ?? '-cdk',
    // Opt-IN, matching USE_DYNAMODB / USE_BEDROCK / MCP_ENABLED: matched
    // exactly against '1', so a typo reads as off rather than as on. Off is
    // the cheap direction and on is the one that bills continuously, so a
    // misread should fall to off.
    snapStart: process.env.SNAPSTART === '1',
    // Same opt-in shape, and for the stronger version of the same reason: the
    // dangerous direction here writes to the serving catalogue on a timer, so
    // a typo must read as off.
    ingestionScheduleEnabled: process.env.INGESTION_SCHEDULE === '1',
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
    lambdaAssetPath: assetPath,
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
