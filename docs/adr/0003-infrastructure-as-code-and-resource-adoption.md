# ADR 0003: Provision the system with AWS CDK (TypeScript) and adopt existing data resources without replacement

- **Status:** Proposed — for team review (drafted 2026-08-29)
- **Decision date:** _pending review_
- **Scope:** Infrastructure as Code for the shopper and ingestion planes; the
  migration from imperative `apply_*` scripts to a declarative CDK app; the
  treatment of already-existing, seeded AWS resources.
- **Related requirements:** 9, 10, 11, 12, 13 (deployment, service plane,
  operations, ingestion) and the `tech.md` / `security.md` steering.
- **Relationship to other ADRs:** Independent of ADR 0001/0002. This ADR governs
  *how the system is provisioned*; it does not decide *what* is provisioned
  beyond the already-approved Pilot Tasks 9–12. It leaves seams for the
  ADR-0002 managed services without requiring them.

## Context

The Smart Grocery & Meal Budget Assistant is a mature application already
partly deployed to AWS. Today, AWS resources are created **imperatively**:
`scripts/apply_iam.py`, `apply_guardrail.py`, `apply_alarms.py`,
`apply_state_machine.py`, `build_lambda.py` and `load_seed_data.py` read the
`config/*.json` policy files and call the AWS APIs directly. Two DynamoDB tables
(`grocery-products-dev` with PITR, `grocery-idempotency-dev` with TTL) **already
exist and hold seeded data**. A Bedrock Guardrail (`b1xezpqe04kx`, v1), two IAM
roles, two CloudWatch alarms and a Step Functions definition also exist.

`tech.md` already locks the direction — *"IaC: AWS CDK (TypeScript) in `infra/`;
planned under Pilot Tasks 9–10"* — and every `config/*.json` header explicitly
anticipates becoming a CDK construct. This ADR records the decision and the
non-obvious parts: resource **adoption**, stack **decomposition**, and the
**scope boundary** with the imperative scripts.

The forces:

- **Reproducibility and drift.** Imperative scripts create resources but declare
  no whole-system state and detect no drift. A production infrastructure needs
  both.
- **Data safety.** The single highest-risk operation in the project is
  destroying a table full of seeded prices. Any IaC adoption must make that
  impossible, not merely unlikely.
- **$0 budget.** The solution must add no recurring cost.
- **The workshop's purpose** is hands-on breadth across AWS services, which
  favours staying in the AWS-native toolchain.
- **An existing governance discipline** (Kiro specs, ADRs, a hard
  documentation-alignment gate, mentor approval for some services) that the IaC
  must fit into, not bypass.

## Decision

**1. Use AWS CDK v2 in TypeScript, in `infra/`.** Chosen over raw
CloudFormation/SAM (conditional adoption logic and cross-stack references are
awkward in YAML), over Terraform (CDK keeps the team in the AWS toolchain the
workshop exists to teach, its L2 constructs encode best-practice defaults, and
CloudFormation *is* the state store so there is nothing extra to host at $0), and
in TypeScript rather than Python CDK (keeps IaC decoupled from the Python
application and aligns with the JS/TS frontend).

**2. Decompose into stacks by lifecycle and blast radius**, not by service:
`StatefulStack` (adopted DynamoDB, `RETAIN`), `ServiceStack` (Lambda + API +
Guardrail + IAM + SSM), `IngestionStack`, `ObservabilityStack`, `FrontendStack`.
The stateful resources are isolated so no routine `cdk deploy` of the service can
reach them.

**3. Adopt existing data resources without replacement.** The DynamoDB tables
are brought under CDK either by reference (`fromTableAttributes`, unmanaged —
recommended for the pilot) or by true adoption (`cdk import` with `RETAIN` —
the full-IaC end state), with a **mandatory zero-diff gate** after import: if
`cdk diff` shows any table change, deployment stops. The live key schema is read
from `describe-table` and mirrored exactly before any CDK is written. (Strategy
choice: [`infra/docs/08-OPEN-DECISIONS.md §2`](../../infra/docs/08-OPEN-DECISIONS.md).)

**4. Keep the `config/*.json` files as the source of truth during migration.**
The CDK reads them at synth time (IAM, guardrail, alarms, state machine) and
publishes the runtime-tunable ones (`models.json`, `feasibility.json`) to SSM
Parameter Store, honouring each file's own stated intent. The apply scripts are
retired only after the CDK path is proven; the JSON is ported into TypeScript
(and the scripts deleted) as a later cleanup, not during migration.

**5. Preserve the two-role separation.** The orchestrator (read prices, invoke
Bedrock, apply Guardrail, write idempotency) and ingestion (write prices only)
keep separate least-privilege roles. The CDK never merges them.

**6. Treat adoption and deployment as separate reviewed operations, and gate
CD.** Stateless stacks deploy automatically on merge via GitHub Actions + OIDC
(free, no long-lived keys); the stateful stack is only ever deployed by a human
who has read its `cdk diff`. AWS CodePipeline is documented but deferred under
the $0 rule.

**7. Deploy only to `ap-southeast-2`**, all resources, with the `-dev` naming
suffix, matching every existing resource and the alarm/log-group bindings that
depend on those exact names.

## Consequences

**Positive.**

- One declarative source of truth for the deployed system; `cdk diff` makes
  drift a reviewable line in a PR.
- The seeded tables are structurally protected: isolated stack + `RETAIN`/unmanaged
  + zero-diff gate means no routine operation can destroy them.
- Reproducible in a second account (a `prod` stage, a sandbox) — the promise the
  config headers already make becomes real.
- Adds no recurring cost; the team gains hands-on CDK, OIDC, and CloudFormation
  experience — three more AWS capabilities.
- Security invariants (two-role split, no wildcard except X-Ray, numbered
  Guardrail, PII-safe logging) become **assertable tests** in the CDK app.

**Negative / costs accepted.**

- Two languages to maintain (TypeScript IaC + Python app). Accepted; for a
  breadth-focused workshop it is a feature, and the decoupling is deliberate.
- During migration, config lives in JSON *and* is read by CDK — a transient
  double path until the apply scripts are retired. Mitigated by keeping one
  file as the single source and only porting to TS after cutover.
- `cdk import` (if Strategy B) is a sharp tool; a schema mismatch can propose a
  destructive change. Mitigated by the mandatory zero-diff gate and by
  recommending the unmanaged-reference strategy first.
- The naming question between the service's `grocery-*-dev` tables and the
  data-pipeline's `SmartGrocery*` schema files is **resolved (2026-08-29,
  Philip):** adopt `grocery-*-dev` as the serving schema; `SmartGrocery*` is the
  raw upstream dataset, routed in via a **B→A transform in ingestion**, never
  adopted as a serving table. The accepted cost is that transform work
  (`ingestion/normalise.py`) plus a legal check on TheMealDB recipe text before
  Task 15. See [`08-OPEN-DECISIONS §1`](../../infra/docs/08-OPEN-DECISIONS.md)
  and [`03 → IngestionStack → Data source`](../../infra/docs/03-STACK-SPECS.md).

**Neutral / deferred.**

- Cognito, WAF, Secrets Manager, CodePipeline, RDS, VPC/NAT are not provisioned;
  each is documented with its cost and its trigger condition in
  [`infra/docs/07-COST-AND-SCALING.md`](../../infra/docs/07-COST-AND-SCALING.md).
- The ADR-0002 managed services (AgentCore, managed evaluations) are neither
  required nor blocked; the infrastructure leaves seams (SSM config, separate
  roles, artefact bucket) but provisions nothing pending mentor approval.

## Alternatives considered

- **Stay imperative (keep the apply scripts).** Rejected: no drift detection, no
  whole-system declaration, no one-command reproduction in another account.
- **Single CDK stack.** Rejected: puts seeded tables one `cdk destroy` from loss
  and forces every change through one change set.
- **Terraform.** A defensible choice; rejected here for toolchain-cohesion,
  best-practice-default, and no-extra-state reasons specific to this workshop.
- **CDK re-implements Lambda packaging (bundling).** Rejected: forks the tuned
  `scripts/build_lambda.py` exclude list; CI runs the one packager and CDK
  consumes its artefact.

## Review notes

This ADR is **proposed** and should be reviewed alongside the
[`infra/docs/`](../../infra/docs/) design set and the open decisions in
[`infra/docs/08-OPEN-DECISIONS.md`](../../infra/docs/08-OPEN-DECISIONS.md).
Nothing described here is implemented; `infra/` contains design documentation
only until Pilot Tasks 9–12 are executed.
