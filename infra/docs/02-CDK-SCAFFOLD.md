# 02 — CDK App Scaffold

> **Status: Design documentation for Pilot Tasks 9–12. Not yet implemented.**
>
> This specifies the *structure* of the CDK app: the files, how it splits into
> stacks and why, naming/tagging/context strategy, and how the existing
> `config/*.json` files map into CDK and SSM. Construct-level detail is in
> [03-STACK-SPECS](03-STACK-SPECS.md).

## 1. Why AWS CDK (TypeScript), and why not the alternatives

`tech.md` already locks this: *"IaC: AWS CDK (TypeScript) in `infra/`; planned
under Pilot Tasks 9–10."* The reasoning, recorded so a reviewer can weigh it:

- **CDK vs. raw CloudFormation / SAM.** The system has real conditional logic
  around resource *adoption* — "import these two tables, create everything
  else" — and cross-stack references (the frontend's CloudFront domain becomes
  the API's `CORS_ORIGIN`; the Guardrail's id becomes a Lambda env var). A
  general-purpose language expresses that cleanly; YAML expresses it with
  intrinsic-function gymnastics.
- **CDK vs. Terraform.** Terraform is an excellent tool and a defensible choice.
  CDK wins here for three project-specific reasons: (1) the workshop's stated
  goal is *hands-on experience with AWS services*, and CDK keeps the team inside
  the AWS toolchain (CloudFormation drift, `cdk import`, the console's stack
  view); (2) the L2 constructs encode AWS best practices (e.g. an S3 bucket that
  blocks public access by default), which is free security review; (3) no extra
  state backend to host — CloudFormation *is* the state — which matters at $0.
- **TypeScript vs. Python CDK.** The application is Python; the CDK is
  TypeScript. That is deliberate and worth stating: the frontend is also
  JS/TS, CDK's TypeScript types are the most mature, and keeping IaC in a
  different language from the Lambda code removes any temptation to import
  application modules into infrastructure. The two planes stay decoupled.

The trade-off accepted: the team now maintains two languages. For a project
whose explicit purpose is breadth of AWS experience, that is a feature.

See [ADR 0003](../../docs/adr/0003-infrastructure-as-code-and-resource-adoption.md)
for the formal decision record.

## 2. File structure

```
infra/
├── package.json          # aws-cdk-lib, constructs, aws-cdk (CLI), typescript, jest, ts-node
├── cdk.json              # "app": "npx ts-node bin/grocery.ts"; context defaults
├── tsconfig.json
├── jest.config.js
├── bin/
│   └── grocery.ts        # the App: reads context, instantiates stacks in order
├── lib/
│   ├── config.ts         # loads the deployment config (env, account, region, names)
│   ├── stateful-stack.ts        # Task 9  — adopts DynamoDB tables
│   ├── service-stack.ts         # Task 10 — Lambda, API GW, Guardrail, IAM, SSM, logs
│   ├── ingestion-stack.ts       # Task 13 — EventBridge, Step Functions, ingestion Lambda
│   ├── observability-stack.ts   # Task 12 — alarms, dashboard, SNS, Budgets, artefact bucket
│   ├── frontend-stack.ts        # later   — S3, CloudFront (OAC), deployment
│   └── constructs/
│       └── (small shared constructs if extracted)
├── test/
│   ├── stateful-stack.test.ts
│   ├── service-stack.test.ts
│   └── __snapshots__/
├── README.md
└── docs/                 # this documentation set
```

`config/*.json` **stays where it is** (repo root `config/`). The CDK **reads**
those files at synth time rather than duplicating their content — the guardrail,
alarms and IAM policies have one source of truth, and it is the file the apply
scripts already use. See §6.

## 3. Stack decomposition — and the rule behind it

The system is split into stacks by **lifecycle and blast radius**, not by
service type. The rule: *things that are created once and must never be
accidentally destroyed live apart from things that are deployed many times a
day.*

| Stack | Contains | Lifecycle | Removal policy |
|-------|----------|-----------|----------------|
| **StatefulStack** | The two DynamoDB tables (adopted) | Created once; effectively permanent | `RETAIN` — a stack delete must never drop a seeded table |
| **ServiceStack** | Lambda(s), API Gateway, Guardrail, IAM roles, SSM params, log groups | Redeployed on every code change | `DESTROY` is safe (stateless) |
| **IngestionStack** | EventBridge rule, Step Functions, ingestion Lambda | Redeployed with ingestion changes | `DESTROY` safe |
| **ObservabilityStack** | Alarms, dashboard, SNS topic, Budgets, artefact S3 bucket | Redeployed with ops changes | Mostly `DESTROY`; the **artefact bucket is `RETAIN`** |
| **FrontendStack** | Frontend S3 bucket, CloudFront, bucket deployment | Redeployed on UI change | Bucket `RETAIN` or `DESTROY+autoDelete` per [08](08-OPEN-DECISIONS.md) |

Why this particular split matters:

- **Blast radius.** The most dangerous operation in this whole project is
  destroying a table full of seeded prices. Isolating stateful resources in
  their own stack with `RETAIN` means no routine `cdk deploy` of the service
  can reach them. The service can be torn down and rebuilt freely.
- **Deploy cadence.** The service changes constantly; the tables never do.
  Coupling them would force a table's change-set evaluation on every code push.
- **Cross-stack references are one-directional.** `ServiceStack` depends on
  `StatefulStack` (it needs the table names/ARNs), never the reverse. CDK
  enforces the dependency order. The one back-reference — the frontend's domain
  feeding the API's CORS origin — is handled at §5.

An alternative considered: a **single stack**. Rejected because a single stack
puts the seeded tables one `cdk destroy` away from oblivion and forces the whole
system through one change set. For a workshop it would be simpler to write;
the safety cost is not worth the saving.

## 4. Environment, account, and region strategy

- **One region, everywhere: `ap-southeast-2`.** Hardcoded via `env` on every
  stack. `bin/grocery.ts` refuses to synth for any other region. (Guard against
  the `ap-southeast-6` mistake `tech.md` explicitly forbids.)
- **Account comes from the deploy identity, never a literal.** Following
  [`scripts/aws_placeholders.py`](../../scripts/aws_placeholders.py)'s reasoning
  — *"whoever is authenticated IS the account being deployed to"* — the CDK
  reads `CDK_DEFAULT_ACCOUNT` from the environment (populated by the CLI from
  STS), and no account id appears in the source. This keeps the *reproducible in
  another account* promise the config headers make.
- **Stage as context.** A single `--context stage=dev` (default `dev`) drives
  the `-dev` suffix and the production-mode flags. A future `prod` stage flips
  `USE_DYNAMODB`/`USE_BEDROCK`/`REQUIRE_GUARDRAIL` to their strict values and
  demands a real `CORS_ORIGIN`. For the pilot only `dev` exists.

```ts
// bin/grocery.ts (illustrative)
const app = new cdk.App();
const stage = app.node.tryGetContext('stage') ?? 'dev';
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'ap-southeast-2' };
if (process.env.CDK_DEFAULT_REGION && process.env.CDK_DEFAULT_REGION !== 'ap-southeast-2') {
  throw new Error('This project deploys ONLY to ap-southeast-2 (tech.md). Refusing.');
}
const cfg = loadConfig(stage);            // names, flags, model ids
const stateful = new StatefulStack(app, `Grocery-Stateful-${stage}`, { env, cfg });
const service  = new ServiceStack(app,  `Grocery-Service-${stage}`,  { env, cfg, tables: stateful.tables });
const obs      = new ObservabilityStack(app, `Grocery-Obs-${stage}`, { env, cfg, api: service.api, fn: service.orchestrator });
const ingestion= new IngestionStack(app, `Grocery-Ingestion-${stage}`, { env, cfg, tables: stateful.tables, asset: service.asset });
const frontend = new FrontendStack(app, `Grocery-Frontend-${stage}`, { env, cfg });
```

## 5. Naming and tagging

- **Names carry the `-dev` suffix**, matching every existing resource and the
  config headers' migration note. `loadConfig(stage)` centralises the names so
  they are defined once:

  | Logical name | Physical name (dev) |
  |--------------|---------------------|
  | products table | `grocery-products-dev` |
  | idempotency table | `grocery-idempotency-dev` |
  | orchestrator function | `grocery-orchestrator-dev` |
  | ingestion function | `grocery-ingestion-dev` |
  | orchestrator role | `grocery-orchestrator-dev-role` |
  | ingestion role | `grocery-ingestion-dev-role` |
  | REST API | `grocery-orchestrator-api-dev` |
  | alarm SNS topic | `grocery-orchestrator-alarms-dev` |
  | log group | `/aws/lambda/grocery-orchestrator-dev` |

  These are **not cosmetic** — `config/alarms.json` binds its metric filter to
  the log group `/aws/lambda/grocery-orchestrator-dev` and its API-5xx alarm to
  the dimension `ApiName = grocery-orchestrator-api-dev`. If the CDK names the
  function or API anything else, the alarms watch nothing and a broken service
  looks healthy. The names are a contract between stacks.

- **Tags on every stack**, via `Tags.of(app)`: `Project=SmartGrocery`,
  `Env=<stage>`, `ManagedBy=cdk`. This matches the tag blocks already in the
  config files (note: those say `ManagedBy=config/...json`; once CDK owns a
  resource the tag becomes `ManagedBy=cdk`, which is itself the signal of what
  has been migrated).

## 6. Config-as-data: how `config/*.json` flows into CDK

The project made a deliberate choice to keep security-relevant configuration as
**reviewable data files**, not console clicks — and each anticipates becoming a
CDK construct. The CDK honours that by **reading the files at synth time**, so
there is still one source of truth:

| Config file | Becomes | Mechanism |
|-------------|---------|-----------|
| [`config/iam-orchestrator-role.json`](../../config/iam-orchestrator-role.json) | The orchestrator `Role` + inline policy | CDK reads the JSON, builds `PolicyStatement`s, substitutes account/region via CDK tokens instead of `aws_placeholders.py` |
| [`config/iam-ingestion-role.json`](../../config/iam-ingestion-role.json) | The ingestion `Role` | same |
| [`config/guardrail.json`](../../config/guardrail.json) | A `CfnGuardrail` (L1) construct | CDK reads the JSON and maps fields to `CfnGuardrail` props (see [03 §3](03-STACK-SPECS.md)) |
| [`config/alarms.json`](../../config/alarms.json) | Metric filter + `Alarm`s + SNS topic | CDK reads and constructs |
| [`config/ingestion-state-machine.json`](../../config/ingestion-state-machine.json) | The `StateMachine` definition | Passed through as an ASL string, or rebuilt with the `stepfunctions` L2 API — see [08](08-OPEN-DECISIONS.md) |
| [`config/models.json`](../../config/models.json) | **SSM Parameters** (not env-baked) | The CDK writes routing/model ids to SSM so operators retune without a deploy — exactly as the file's header predicts |
| [`config/feasibility.json`](../../config/feasibility.json) | An **SSM Parameter** (`min_grams_per_person_day`) | same reasoning: a domain judgement a non-engineer should be able to change |

The `${AWS_ACCOUNT_ID}` / `${AWS_REGION}` placeholders in the IAM and
state-machine JSONs are resolved by **CDK tokens** (`this.account`,
`this.region`, `table.tableArn`) at synth time, replacing the runtime
`aws_placeholders.py` substitution. This is strictly better: the ARNs become
*references* to the constructs, so if a table's name ever changes the policy
follows automatically instead of drifting.

> **Design choice to confirm (see [08 §5](08-OPEN-DECISIONS.md)):** reading the
> JSON at synth keeps one source of truth but couples the CDK to the files'
> shape. The alternative — porting the policy into TypeScript and *deleting* the
> JSON + apply script — is cleaner long-term but throws away the reviewable
> data-file property the project values. Recommendation: read the JSON during
> migration; revisit once the apply scripts are retired.

## 7. Testing the CDK app

Infrastructure is code and gets the same gate discipline as the rest of the
repo (the project runs a strict CI; see [`docs/CI-GATE-HEALTH.md`](../../docs/CI-GATE-HEALTH.md)).

- **Fine-grained assertions** (`aws-cdk-lib/assertions`) for the properties that
  *matter*: the products role has no `dynamodb:PutItem`; the orchestrator role
  has no `*` resource except the two X-Ray actions that cannot be scoped; the
  tables are `RETAIN`; the API stage has throttling; `POWERTOOLS_LOGGER_LOG_EVENT`
  is absent. These encode the security invariants as tests.
- **One snapshot per stack** to catch unintended change — reviewed, not blindly
  updated.
- Wire these into the existing CI as an `infra` job behind the same `summary`
  gate the other five jobs sit behind (see [05-CICD](05-CICD.md)).
