# 03 — Stack Specifications

> **Status: Design documentation for Pilot Tasks 9–12. Not yet implemented.**
>
> Construct-level specification of each stack. The TypeScript is **illustrative
> reference**, not the finished app — it shows intent and the properties that
> matter, and omits imports and boilerplate. Treat it as a spec to implement
> against, not code to paste.

---

## StatefulStack (Pilot Task 9) — adopt DynamoDB, never replace

### Intent

Take the two DynamoDB tables that **already exist and hold seeded data** under
CDK management **without recreating, replacing, or emptying them**. This is the
single highest-risk stack: a wrong move deletes seeded prices. Everything about
it is built to make that impossible.

### The tables as they exist

| Table | Partition key | Sort key | GSI | Settings |
|-------|---------------|----------|-----|----------|
| `grocery-products-dev` | `store_key` (see note) | `product_key` | `GSI1` (used by cheapest-price query) | PITR **on**, on-demand |
| `grocery-idempotency-dev` | idempotency key | — | — | TTL **on**, on-demand |

> **Adoption target ✅ decided — Lineage A ([08 §1](08-OPEN-DECISIONS.md)).** The
> CDK adopts `grocery-products-dev` / `grocery-idempotency-dev`; the
> `SmartGrocery*` tables are the raw upstream dataset (see IngestionStack → Data
> source below), never serving tables. The exact key schema still must be
> **confirmed** from the live table before writing the CDK.
>
> **Key-schema confirmation.** The IAM policy proves the products
> table has a base table + `GSI1`, and the ingestion role diffs *by `store_key`*
> (the base partition key). The exact attribute names/types must be read from
> the live table before writing the CDK — do **not** trust the differently-named
> [`datasets/dynamodb_schema/products-table.json`](../../datasets/dynamodb_schema/products-table.json)
> (`primary_key` / `CategoryPriceIndex`), which describes a *different* table
> lineage (`SmartGroceryProducts`, owner `AUT-AWS-DataPipeline`). Run, on the
> deploy account, before implementing:
> ```
> aws dynamodb describe-table --table-name grocery-products-dev     --region ap-southeast-2
> aws dynamodb describe-table --table-name grocery-idempotency-dev  --region ap-southeast-2
> ```
> and mirror the returned key schema, attribute definitions and GSI exactly.
> See [08-OPEN-DECISIONS §1](08-OPEN-DECISIONS.md).

### Two adoption strategies — pick per [08 §2](08-OPEN-DECISIONS.md)

**Strategy A — Reference existing tables, do not manage them (safest).**
The CDK never owns the tables; it only *reads their identifiers* so other stacks
can grant access. Zero risk of replacement because CloudFormation holds no
table resource.

```ts
// lib/stateful-stack.ts — Strategy A (import by name, unmanaged)
export class StatefulStack extends cdk.Stack {
  readonly products: dynamodb.ITable;
  readonly idempotency: dynamodb.ITable;
  constructor(scope: Construct, id: string, props: GroceryStackProps) {
    super(scope, id, props);
    // fromTableAttributes → an ITable the other stacks can grantRead/grantWrite on,
    // but CloudFormation does NOT manage its lifecycle. Nothing here can delete it.
    this.products = dynamodb.Table.fromTableAttributes(this, 'Products', {
      tableName: 'grocery-products-dev',
      globalIndexes: ['GSI1'],
    });
    this.idempotency = dynamodb.Table.fromTableAttributes(this, 'Idempotency', {
      tableName: 'grocery-idempotency-dev',
    });
  }
}
```

**Strategy B — Truly adopt via `cdk import` (full IaC, higher risk).**
Define the tables as real `Table` constructs with `removalPolicy: RETAIN`, then
bring the existing physical tables under management with `cdk import` (which
writes the resource into the stack state *without creating anything*). After
import, `cdk diff` must show **no changes** — any diff means the CDK definition
does not match reality and a deploy would try to "fix" it, which for a table
means replacement.

```ts
// lib/stateful-stack.ts — Strategy B (managed, RETAIN, adopted via `cdk import`)
this.products = new dynamodb.Table(this, 'Products', {
  tableName: 'grocery-products-dev',
  partitionKey: { name: 'store_key', type: dynamodb.AttributeType.STRING },   // confirm via describe-table
  sortKey:      { name: 'product_key', type: dynamodb.AttributeType.STRING }, // confirm
  billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
  pointInTimeRecovery: true,
  encryption: dynamodb.TableEncryption.AWS_MANAGED,
  removalPolicy: cdk.RemovalPolicy.RETAIN,   // a stack delete keeps the table
});
this.products.addGlobalSecondaryIndex({
  indexName: 'GSI1',
  partitionKey: { /* confirm via describe-table */ } as any,
  projectionType: dynamodb.ProjectionType.ALL,
});
```

**Recommendation:** start with **Strategy A** for the pilot. It delivers the
value the CDK actually needs from these tables — a handle other stacks can grant
against — with *zero* replacement risk, and it can be upgraded to Strategy B
later once the team is comfortable with `cdk import`. Strategy B is the "full
IaC" end state; it is not worth the risk on day one of the migration.

### Acceptance evidence (Task 9)

- `cdk diff` on this stack shows **no table create and no table replace**.
- The seeded row count before and after `cdk deploy` is identical (spot-check
  with a `Scan --select COUNT`).
- PITR remains enabled (a deploy must never toggle it).

---

## ServiceStack (Pilot Task 10) — the deployable service plane

### Lambda functions (two functions, one asset)

```ts
// One asset, built by the existing scripts/build_lambda.py → build/lambda.zip
const asset = lambda.Code.fromAsset('../build/lambda.zip');   // see note on asset build below

const orchestrator = new lambda.Function(this, 'Orchestrator', {
  functionName: 'grocery-orchestrator-dev',
  runtime: lambda.Runtime.PYTHON_3_13,
  handler: 'src.handler.lambda_handler',
  code: asset,
  memorySize: 1024,               // 512–1024; tune against cost in doc 07
  timeout: cdk.Duration.seconds(30),
  role: orchestratorRole,         // from config/iam-orchestrator-role.json — see doc 04
  environment: { /* the env contract from 01 §7 */ },
  tracing: lambda.Tracing.ACTIVE, // X-Ray
  logRetention: logs.RetentionDays.TWO_WEEKS,
});

// SnapStart on a PUBLISHED alias (zip-only; container forfeits it)
const version = orchestrator.currentVersion;              // publishes a new version each deploy
const live = new lambda.Alias(this, 'OrchestratorLive', { aliasName: 'live', version });
// SnapStart is set via the CfnFunction escape hatch until the L2 prop is used:
(orchestrator.node.defaultChild as lambda.CfnFunction).snapStart = { applyOn: 'PublishedVersions' };
```

Notes:

- **API Gateway integrates with the `live` alias, not `$LATEST`.** SnapStart
  only benefits published versions; pointing the API at the alias is what makes
  the cold-start win real.
- **Asset build seam.** `scripts/build_lambda.py` is the authoritative packager
  (manylinux wheels, runtime-provided excludes, size budget). Options:
  (a) run it in CI before `cdk deploy` and point `fromAsset` at `build/lambda.zip`
  (recommended — keeps one packager); (b) use `PythonFunction` bundling from
  `@aws-cdk/aws-lambda-python-alpha` (re-implements packaging, diverges from the
  script's careful exclude list — **not** recommended). See [05-CICD](05-CICD.md).
- **Ingestion function** is declared in `IngestionStack` from the *same* asset,
  with handler `ingestion.handler.lambda_handler` and the ingestion role.

### API Gateway (REST)

```ts
const api = new apigateway.RestApi(this, 'Api', {
  restApiName: 'grocery-orchestrator-api-dev',      // MUST match config/alarms.json dimension
  deployOptions: {
    stageName: 'dev',
    throttlingRateLimit: 20,      // steady req/s — workshop scale; tune in doc 07
    throttlingBurstLimit: 40,
    tracingEnabled: true,         // X-Ray on the API — matches the live stage since 2026-08-30
    // accessLogDestination + accessLogFormat → CloudWatch (no PII fields)
  },
  // Strict CORS in production mode: ONE origin, never "*". Value = CloudFront domain.
  defaultCorsPreflightOptions: {
    allowOrigins: [props.cfg.corsOrigin],   // e.g. https://dxxxx.cloudfront.net
    allowMethods: ['POST', 'OPTIONS'],
    allowHeaders: ['Content-Type'],
  },
});
const chat = api.root.addResource('chat');
chat.addMethod('POST', new apigateway.LambdaIntegration(live, { proxy: true }));

// Usage plan (security.md: every stage has throttling AND a usage plan)
const plan = api.addUsagePlan('Plan', {
  throttle: { rateLimit: 20, burstLimit: 40 },
  // quota optional at pilot; add when a public surface appears
});
plan.addApiStage({ stage: api.deploymentStage });
```

- **Authorizer:** none for the anonymous pilot. The seam for a **Cognito
  authorizer** is left explicit — adding it later is a one-line
  `authorizer:`/`authorizationType:` change on the method, no restructuring.
  `tech.md`: *"API Gateway WebSocket streaming is a later approved upgrade; the
  event contract remains unchanged."* Same for auth.
- **CORS is a security control, not a convenience.** `security.md` requires
  production mode to *reject* wildcard CORS. The value is the CloudFront domain
  from `FrontendStack`; until that exists, dev may use `*` **only** with
  `USE_*`/`REQUIRE_GUARDRAIL` making the stage non-production.

### Bedrock Guardrail

Built as an L1 `CfnGuardrail` from [`config/guardrail.json`](../../config/guardrail.json),
so the reviewed security policy stays in the reviewed data file:

```ts
const g = JSON.parse(fs.readFileSync('../config/guardrail.json', 'utf8'));
const guardrail = new bedrock.CfnGuardrail(this, 'Guardrail', {
  name: g.name,                                   // grocery-assistant-guardrail-dev
  blockedInputMessaging: g.blockedInputMessaging,
  blockedOutputsMessaging: g.blockedOutputsMessaging,
  contentPolicyConfig: g.contentPolicyConfig,     // shapes line up with CfnGuardrail props
  topicPolicyConfig: g.topicPolicyConfig,
  wordPolicyConfig: g.wordPolicyConfig,
  sensitiveInformationPolicyConfig: g.sensitiveInformationPolicyConfig,
  tags: g.tags,
});
const guardrailVersion = new bedrock.CfnGuardrailVersion(this, 'GuardrailV', {
  guardrailIdentifier: guardrail.attrGuardrailId,
});
// Feed the id + numbered version to the Lambda env (never DRAFT in prod)
orchestrator.addEnvironment('BEDROCK_GUARDRAIL_ID', guardrail.attrGuardrailId);
orchestrator.addEnvironment('BEDROCK_GUARDRAIL_VERSION', guardrailVersion.attrVersion);
```

> **Decision to confirm ([08 §3](08-OPEN-DECISIONS.md)):** a live Guardrail
> `b1xezpqe04kx` (v1) already exists, and the orchestrator IAM policy currently
> hardcodes *that* ARN. If CDK creates a *new* Guardrail, its id differs and the
> IAM resource ARN must follow the CDK token (which it will, if the role is also
> CDK-built — see doc 04). Options: (A) let CDK create and own the Guardrail
> (clean IaC, new id, update nothing by hand because IAM references the token);
> (B) adopt the existing Guardrail id. Recommendation: **A** — the Guardrail is
> cheap to recreate and CDK ownership means the policy is versioned with the
> stack; retire `b1xezpqe04kx` after cutover.

### SSM parameters (runtime config without redeploy)

```ts
// Model catalogue/routing (config/models.json) and the feasibility floor
// (config/feasibility.json) become SSM params, exactly as their headers predict.
new ssm.StringParameter(this, 'ModelsParam', {
  parameterName: '/grocery/dev/models',
  stringValue: fs.readFileSync('../config/models.json', 'utf8'),
});
new ssm.StringParameter(this, 'FeasibilityParam', {
  parameterName: '/grocery/dev/min_grams_per_person_day',
  stringValue: String(JSON.parse(fs.readFileSync('../config/feasibility.json','utf8')).min_grams_per_person_day),
});
```

Standard SSM parameters are free. This is *why* the config files are data: an
operator retunes routing or the feasibility floor by editing a parameter, no
deploy. (If the running code reads these from env today rather than SSM, that
wiring is a small application follow-up — noted in [08 §6](08-OPEN-DECISIONS.md).)

### IAM roles

Both roles are built from their `config/iam-*.json` files. Full mapping,
statement by statement, is in [04-SECURITY](04-SECURITY.md) — it is the security
heart of the design and lives there.

---

## IngestionStack (Pilot Task 13)

```ts
const ingestion = new lambda.Function(this, 'Ingestion', {
  functionName: 'grocery-ingestion-dev',
  runtime: lambda.Runtime.PYTHON_3_13,
  handler: 'ingestion.handler.lambda_handler',
  code: props.asset,                     // SAME asset as the orchestrator
  role: ingestionRole,                   // separate role — writes prices, no model, no idempotency
  timeout: cdk.Duration.seconds(60),
  tracing: lambda.Tracing.ACTIVE,
  logRetention: logs.RetentionDays.TWO_WEEKS,
});

// Step Functions from config/ingestion-state-machine.json (Inline Map, per-retailer isolation)
const asl = fs.readFileSync('../config/ingestion-state-machine.json', 'utf8');
const stateMachine = new stepfunctions.StateMachine(this, 'Refresh', {
  definitionBody: stepfunctions.DefinitionBody.fromString(asl),  // ${AWS_*} → resolve to tokens first
  stateMachineName: 'grocery-ingestion-refresh-dev',
  tracingEnabled: true,
});
ingestion.grantInvoke(stateMachine);

// EventBridge: daily 06:00 NZST (18:00 UTC previous day — confirm DST handling)
new events.Rule(this, 'DailyRefresh', {
  schedule: events.Schedule.cron({ minute: '0', hour: '18' }),   // NZST = UTC+12/13; see note
  targets: [new targets.SfnStateMachine(stateMachine, {
    input: events.RuleTargetInput.fromObject({ retailers: ['pakn_save', 'new_world'] }),
  })],
});
```

Notes:

- **The `${AWS_REGION}`/`${AWS_ACCOUNT_ID}` in the ASL** must be replaced with
  CDK tokens (`this.region`, `this.account`, `ingestion.functionArn`) before
  `fromString`, or rebuilt with the L2 `stepfunctions-tasks.LambdaInvoke` API.
  The L2 route is more work but gives type-checked retries/catch; the ASL route
  reuses the carefully-commented existing definition verbatim. See [08 §4](08-OPEN-DECISIONS.md).
- **DST caveat:** NZST is UTC+12, NZDT (daylight) is UTC+13. A fixed UTC cron
  drifts by an hour across the DST boundary. For a daily refresh that is
  harmless; note it rather than over-engineer a timezone rule.
- **Fixtures first:** the ingestion Lambda uses fixture/recorded adapters; no
  live retailer traffic until separately gated (`tech.md`).

### Data source — Lineage B → Lineage A transform (decided 2026-08-29)

Per [08 §1](08-OPEN-DECISIONS.md), the real collected dataset —
**Lineage B** (`SmartGroceryProducts`, `SmartGroceryRecipes` in
[`datasets/`](../../datasets/), 585 products + 175 recipes) — is now the
**upstream data source**, not a serving table. Ingestion is the boundary that
transforms **B → A** (the `grocery-*-dev` serving schema). This is exactly what
[`ingestion/normalise.py`](../../ingestion/normalise.py) is for; the recorded
adapter reads a B-shaped snapshot and emits A-shaped items.

**Products: `SmartGroceryProducts` (B) → `grocery-products-dev` (A).**

| A field (serving) | Derived from B | Transform note |
|-------------------|----------------|----------------|
| `store_key` (PK) | `store_name`/`store_id` | slug → `paknsave#lincoln-road`, `newworld#albany` |
| `product_key` (SK, GSI1 PK) | `product_name` | **normalise** to canonical key (`butter-500g`) — the load-bearing step (DYNAMODB-SCHEMA.md) |
| `gsi1_sk` | `price` + store | `"{price_cents:09d}#{chain}#{location}"` (zero-padded → cheapest-first) |
| `store` | `store_name` | enum `paknsave` \| `new_world` |
| `store_location` | `store_name` | e.g. `Lincoln Road`, `Albany` |
| `display_name` | `product_name` | as the retailer writes it |
| `canonical_name` | `product_name` | normalised display form |
| `category` | `category` | map B's taxonomy (`Milk`) → A's (`dairy`) |
| `price_nzd` | `price` (N) | **Number → String** — money exactness (never float) |
| `unit`, `unit_price_nzd` | `size` + `price` | parse `size` (`2l`/`500g`); compute unit price → String |
| `pack_grams` | `size` | parse to grams (needed for meal-plan scaling) |
| `on_special` | — | **not in B** → default `false` until a source provides it |
| `valid_date` | ingestion run | ISO date of the refresh |
| `lat`, `lon` | `store_id` | from a small fixed store→coordinates lookup (2 stores) |

The real work is the four fields B lacks — `pack_grams`, `unit_price_nzd`,
`lat`/`lon`, `on_special`, `valid_date` — plus the `product_key` normalisation
and the `category` taxonomy map. Model these in the recorded adapter/normaliser
with tests, exactly as the fixture path is tested today.

**Recipes: `SmartGroceryRecipes` (B) → `grocery-meals-dev` (A, Pilot Task 15).**
Same B→A pattern (recipe rows → `RECIPE#<id>` items, `product_key` references,
tag GSI). ⚠️ **Legal gate:** B's `instructions` come from TheMealDB. The project's
own rule (DYNAMODB-SCHEMA.md "Legal note") is *ingredient lists are facts (free
to use); the written method is copyrightable — write it ourselves, do not scrape
instructions.* So when loading recipes, either honour TheMealDB's terms +
attribution (the B rows carry `attribution`/`source_terms_url`) **or** keep only
the ingredient combinations and author method text originally. Resolve this in
Pilot Task 15 before any recipe `method`/`instructions` is written to
`grocery-meals-dev`.

**The physical `SmartGrocery*` tables** (if they exist in AWS): keep as a
raw-data **staging** store or export the batches to the encrypted, versioned S3
artefact bucket ([ObservabilityStack](#observabilitystack-pilot-task-12)) and
retire the tables. The CDK does **not** adopt them. Confirm existence with
`aws dynamodb list-tables --region ap-southeast-2`.

---

## ObservabilityStack (Pilot Task 12)

Built from [`config/alarms.json`](../../config/alarms.json), plus the dashboard,
Budgets and artefact bucket.

```ts
// SNS topic alarms point at (config/alarms.json refuses an alarm with no topic)
const topic = new sns.Topic(this, 'Alarms', { topicName: 'grocery-orchestrator-alarms-dev' });
// Subscriptions are added by hand (email requires out-of-band confirmation) — documented, not coded.

// Metric filter: the JSON selector { $.message = "handler_escaped" }, with 0-fill default
const logGroup = logs.LogGroup.fromLogGroupName(this, 'OrchLg', '/aws/lambda/grocery-orchestrator-dev');
const mf = new logs.MetricFilter(this, 'HandlerEscaped', {
  logGroup,
  filterPattern: logs.FilterPattern.stringValue('$.message', '=', 'handler_escaped'),
  metricNamespace: 'GroceryOrchestrator',
  metricName: 'HandlerEscaped',
  metricValue: '1',
  defaultValue: 0,          // 0, not absent — a quiet period must read as all-clear, not INSUFFICIENT_DATA
});

// Alarm 1: a handler escaped (always a bug)
new cloudwatch.Alarm(this, 'HandlerEscapedAlarm', {
  metric: new cloudwatch.Metric({ namespace: 'GroceryOrchestrator', metricName: 'HandlerEscaped', statistic: 'Sum', period: cdk.Duration.minutes(5) }),
  threshold: 1, evaluationPeriods: 1, comparisonOperator: GTE,
  treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
}).addAlarmAction(new cwactions.SnsAction(topic));

// Alarm 2: any API 5xx (fires even when our logging is what broke)
new cloudwatch.Alarm(this, 'Api5xxAlarm', {
  metric: new cloudwatch.Metric({ namespace: 'AWS/ApiGateway', metricName: '5XXError',
    dimensionsMap: { ApiName: 'grocery-orchestrator-api-dev' }, statistic: 'Sum', period: cdk.Duration.minutes(5) }),
  threshold: 1, evaluationPeriods: 1, comparisonOperator: GTE,
  treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
}).addAlarmAction(new cwactions.SnsAction(topic));

// AWS Budgets: the backstop on Bedrock spend (2 budgets are free)
new budgets.CfnBudget(this, 'MonthlyBudget', {
  budget: { budgetType: 'COST', timeUnit: 'MONTHLY', budgetLimit: { amount: 5, unit: 'USD' } },
  notificationsWithSubscribers: [/* 80% and 100% → SNS/email */],
});

// Encrypted, versioned artefact bucket (datasets, eval results, review artefacts)
new s3.Bucket(this, 'Artefacts', {
  bucketName: 'grocery-artefacts-dev-<accountid>',
  encryption: s3.BucketEncryption.S3_MANAGED,
  versioned: true,
  blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
  lifecycleRules: [{ noncurrentVersionExpiration: cdk.Duration.days(90) }],
  removalPolicy: cdk.RemovalPolicy.RETAIN,
});
```

The two alarms are exactly the two `config/alarms.json` calls *"worth having on
day one."* The dashboard surfaces the EMF metrics the code already emits
(latency, tokens, cache reads, repairs, guardrail interventions, idempotent
replays, contentless turns) — no new instrumentation, just a view.

---

## FrontendStack (later — the missing UI)

The chat UI does **not exist yet** (there is a `FRONTEND-INTEGRATION.md`
contract but no built UI). This stack hosts whatever the team builds — see the
framework decision in [08 §7](08-OPEN-DECISIONS.md).

```ts
const bucket = new s3.Bucket(this, 'Site', {
  blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,   // not public; CloudFront reaches it via OAC
  encryption: s3.BucketEncryption.S3_MANAGED,
});
const dist = new cloudfront.Distribution(this, 'Cdn', {
  defaultBehavior: {
    origin: origins.S3BucketOrigin.withOriginAccessControl(bucket),  // OAC, not legacy OAI
    viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
  },
  defaultRootObject: 'index.html',
});
new s3deploy.BucketDeployment(this, 'Deploy', {
  sources: [s3deploy.Source.asset('../frontend/dist')],  // build output
  destinationBucket: bucket,
  distribution: dist,                                    // invalidates cache on deploy
});
```

- **The CloudFront domain is the `CORS_ORIGIN`** the ServiceStack needs. Because
  that is a cross-stack, cross-direction reference (frontend → service), resolve
  it by (a) deploying frontend first and passing the domain in as context, or
  (b) using a custom domain known ahead of time. For the pilot, (a) with a
  two-pass deploy is simplest and documented in [06-DEPLOYMENT-GUIDE](06-DEPLOYMENT-GUIDE.md).
- **WAF** goes here *before* the surface is public/owned (`security.md`), not
  now — it costs and the pilot is anonymous/low-traffic. Noted as a market-stage
  add in [07](07-COST-AND-SCALING.md).
