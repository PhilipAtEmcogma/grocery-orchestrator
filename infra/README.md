# `infra/` — Infrastructure as Code (design documentation)

> **Status: DESIGN DOCUMENTATION. Nothing here is deployed.**
> This directory currently contains *specifications* for the AWS CDK app that
> will provision the Smart Grocery & Meal Budget Assistant. The CDK app itself
> (TypeScript sources, `package.json`, `cdk.json`) is **not yet written** — it
> is [Pilot Tasks 9–12](../.kiro/specs/grocery-orchestrator/tasks.md) in the
> approved roadmap. These docs exist so the team can review the intended design
> *before* a line of CDK is written, and so that when implementation begins the
> reasoning is already recorded and agreed.

## Why this directory exists

Today the project is deployed to AWS by a set of **imperative apply scripts**
(`scripts/apply_iam.py`, `apply_guardrail.py`, `apply_alarms.py`,
`apply_state_machine.py`, `build_lambda.py`, `load_seed_data.py`) that read the
`config/*.json` policy files and call the AWS APIs directly. That was the right
call for getting a pilot standing quickly — but it has three costs an
infrastructure team eventually pays:

1. **No single source of truth for the deployed shape.** The IAM roles, the
   Guardrail, the alarms and the state machine each live in their own JSON and
   their own apply script. Nothing declares *the whole system* or the
   dependencies between its parts.
2. **No drift detection.** A console click that changes a role or a table
   setting is invisible until something breaks. `cdk diff` makes drift a
   reviewable line in a pull request.
3. **No reproducibility as one unit.** Standing the system up in a second
   account (a `prod` stage, a teammate's sandbox) means running every script in
   the right order by hand and hoping the order was right.

CDK resolves all three. Crucially, **the `config/*.json` files were written
knowing this migration was coming** — every one of them carries a header
comment such as *"Under IaC this becomes a CDK … construct."* This documentation
set is the plan for honouring those comments.

## What "bring the project live in AWS" actually means here

It does **not** mean building the application — the application already exists
and is mature (see the root [`README.md`](../README.md) and
[`AGENTS.md`](../AGENTS.md)). It means three things, in order:

| Phase | Pilot Task | What it delivers |
|-------|-----------|------------------|
| **Adopt** | Task 9 | A CDK app that *takes ownership of the DynamoDB tables that already exist and hold seeded data* — without recreating or emptying them. |
| **Codify** | Task 10 | The deployable service plane in CDK: the zip Lambda + SnapStart alias, the REST API, the Guardrail, the IAM roles, SSM config, log retention, strict CORS and throttling. |
| **Deploy & observe** | Tasks 11–12 | Deploy the anonymous pilot, then add the CloudWatch dashboards/alarms, X-Ray, Budgets, S3 artefact storage and SNS notifications that make it operable. |

A missing **frontend** (the S3 + CloudFront chat UI from the project's
architecture diagram) and **CI/CD deploy automation** are also part of "live",
and are specified here, but they are secondary to the three phases above.

## How to read these docs

Read them roughly in order. Each is self-contained but they build on each other.

| Doc | Purpose | Primary audience |
|-----|---------|------------------|
| [`docs/00-OVERVIEW.md`](docs/00-OVERVIEW.md) | Scope, what exists vs. what CDK adds, the $0 posture, Well-Architected framing | Everyone |
| [`docs/01-ARCHITECTURE.md`](docs/01-ARCHITECTURE.md) | The AWS services, their roles, how they integrate, request & data flow, trust boundaries | Developers, DevOps |
| [`docs/02-CDK-SCAFFOLD.md`](docs/02-CDK-SCAFFOLD.md) | The CDK app's file structure, how it splits into stacks and why, naming/tagging/context, how `config/*.json` maps into CDK & SSM | Developers building the CDK app |
| [`docs/03-STACK-SPECS.md`](docs/03-STACK-SPECS.md) | Construct-level specification of each stack, with illustrative TypeScript | Developers building the CDK app |
| [`docs/04-SECURITY.md`](docs/04-SECURITY.md) | Least-privilege IAM mapping, secrets, Guardrail, CORS, TLS, PII-safe logging, fail-closed production mode | Security reviewer, DevOps |
| [`docs/05-CICD.md`](docs/05-CICD.md) | The existing CI, the free OIDC deploy path, and the CodePipeline design deferred to a paid/market build | DevOps |
| [`docs/06-DEPLOYMENT-GUIDE.md`](docs/06-DEPLOYMENT-GUIDE.md) | Step-by-step runbook: prerequisites, bootstrap, adopt, deploy, verify, roll back | DevOps, developers |
| [`docs/07-COST-AND-SCALING.md`](docs/07-COST-AND-SCALING.md) | The free-tier cost model, what is deferred because it costs money, quota ceilings, scaling levers | Everyone, cost owner |
| [`docs/08-OPEN-DECISIONS.md`](docs/08-OPEN-DECISIONS.md) | Decisions the team/mentor must make before or during implementation | Team, mentor |
| [`docs/09-FRONTEND.md`](docs/09-FRONTEND.md) | Researched best-practice guide for the S3+CloudFront chat UI: framework choice, OAC, SPA routing, security headers | Frontend dev, DevOps |

The decision record that governs this whole effort is
[`../docs/adr/0003-infrastructure-as-code-and-resource-adoption.md`](../docs/adr/0003-infrastructure-as-code-and-resource-adoption.md).

## Layout of the CDK app

A **reviewable scaffold skeleton now exists** (added 2026-08-29). The build
config (`package.json`, `cdk.json`, `tsconfig.json`, `jest.config.js`), the app
wiring (`bin/grocery.ts`) and the config loader (`lib/config.ts`) are real and
useful; the five stacks are **stubs** — they compile and wire together but create
**no real AWS resources yet** (each carries a `SCAFFOLD stub` annotation and
`TODO`s pointing at the spec in [`docs/03-STACK-SPECS.md`](docs/03-STACK-SPECS.md)).
So `cdk synth` will produce near-empty templates until the stacks are
implemented. `StatefulStack` is the exception: it uses the safe Strategy A
(reference existing tables, unmanaged), which creates nothing.

> **Nothing here deploys anything.** The skeleton exists so reviewers can see and
> agree the structure before the resource code is written. Do **not** `cdk deploy`
> a stub expecting a working service.

```
infra/
├── README.md                 # this file
├── docs/                     # the design documentation (this set) + 09-FRONTEND
├── package.json              # ✅ scaffold — CDK + construct-library deps (pins indicative)
├── cdk.json                  # ✅ scaffold — app entrypoint + context (region guard, stage)
├── tsconfig.json             # ✅ scaffold
├── jest.config.js            # ✅ scaffold — for CDK assertion/snapshot tests
├── .gitignore                # ✅ scaffold — ignores cdk.out, node_modules, manual/ (live evidence)
├── bin/
│   └── grocery.ts            # ✅ scaffold — the App: region guard + wires stacks in order
├── lib/
│   ├── config.ts             # ✅ scaffold — names, flags, config-file paths (the cross-stack contract)
│   ├── stateful-stack.ts     # ✅ scaffold — adopts DynamoDB (Strategy A, safe) (Task 9)
│   ├── service-stack.ts      # 🚧 stub — Lambda + API GW + Guardrail + IAM + SSM (Task 10)
│   ├── ingestion-stack.ts    # 🚧 stub — EventBridge + Step Functions (Task 13)
│   ├── observability-stack.ts# 🚧 stub — alarms + dashboard + SNS + Budgets (Task 12)
│   ├── frontend-stack.ts     # 🚧 stub — S3 + CloudFront static UI (later)
│   └── constructs/           # small reusable constructs, if any
└── test/
    └── service-stack.test.ts # ✅ scaffold — security-invariant assertion template (skipped until built)
```

Legend: ✅ real scaffold you can build on · 🚧 stub with `TODO`s and the spec reference.

## When implementation begins

1. Read [`docs/06-DEPLOYMENT-GUIDE.md`](docs/06-DEPLOYMENT-GUIDE.md) end to end.
2. Confirm the [open decisions](docs/08-OPEN-DECISIONS.md) are resolved.
3. Build **`stateful-stack` first**, and prove *adoption without replacement*
   before anything else — a mistake here can delete seeded price data. The
   deployment guide describes the safe procedure (`RETAIN` policies,
   `cdk import`, and a `cdk diff` that must show **no** table replacement).
4. Everything else can follow the roadmap order.
