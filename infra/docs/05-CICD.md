# 05 — CI/CD

> **Status: Design documentation. Not yet implemented.**
>
> The boilerplate brief asked for "AWS CodePipeline with GitHub integration."
> Under the **$0 budget rule** the recommended path is **GitHub Actions +
> OIDC** (free), with CodePipeline **specified but deferred** to a market build.
> This doc gives both.

## 1. What already exists

The repo has a mature, credential-free **CI** pipeline in
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml): lint, format
(`ruff format --check`), types (pyright), tests, contract + grounding
validation, dependency + secret scanning, guardrail + alarm policy validation,
eval floors, and the Lambda package build — *"Five jobs behind one required
`summary` check, all credential-free, and a test asserts every job is actually
wired into it"* ([`docs/CI-GATE-HEALTH.md`](../../docs/CI-GATE-HEALTH.md)).

What does **not** exist is **CD** — nothing deploys to AWS automatically. Today
a human runs the apply scripts. That is the gap this doc closes.

## 2. Design principle: adopt ≠ deploy, and CD is gated

Two constraints shape the CD design:

1. **`cdk deploy` of the stateful stack is a reviewed, deliberate operation,
   never automatic.** Pilot Task 11 says *"treat resource adoption and
   deployment as separate reviewed operations."* So CD automates the *stateless*
   stacks (service, ingestion, observability, frontend); the **stateful stack is
   deployed by hand**, with a human reading `cdk diff` first. The pipeline may
   *diff* the stateful stack (safe, read-only) but never *deploys* it
   unattended.
2. **The pilot is `dev`-only.** There is one stage. CD deploys `dev` on merge to
   `main`. A future `prod` stage gets a manual approval gate.

## 3. Recommended: GitHub Actions + OIDC (free, no long-lived keys)

### Why OIDC

GitHub Actions can assume an AWS IAM role via **OpenID Connect** — GitHub
presents a short-lived token, AWS trusts it for a specific repo/branch, and
issues temporary credentials. **No access keys are stored in GitHub secrets.**
This is the current AWS-recommended pattern and it costs nothing.

Reference: https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services

### One-time setup (documented, run by hand)

1. Create the GitHub OIDC identity provider in the account
   (`token.actions.githubusercontent.com`).
2. Create `grocery-deploy-dev-role` trusting **only** this repo, ideally scoped
   to `main` and/or a GitHub **Environment** named `dev` (so environment
   protection rules apply):
   ```
   sub = repo:PhilipAtEmcogma/<repo>:ref:refs/heads/main
   ```
3. Give the role the CDK deploy permissions **via a permissions boundary**, not
   `AdministratorAccess` (see [04 §4](04-SECURITY.md)).
4. `cdk bootstrap aws://<account>/ap-southeast-2` once (creates the CDK
   toolkit/assets bucket + exec roles). Bootstrap is a human action.

### The deploy workflow (illustrative)

```yaml
# .github/workflows/deploy.yml
name: deploy-dev
on:
  push:
    branches: [main]
permissions:
  id-token: write        # required for OIDC
  contents: read
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: dev      # protection rules / required reviewers live here
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.13' }
      - name: Build Lambda archive (the ONE authoritative packager)
        run: python scripts/build_lambda.py     # → build/lambda.zip
      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - run: npm ci
        working-directory: infra
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::<account>:role/grocery-deploy-dev-role
          aws-region: ap-southeast-2
      - name: Diff everything (including stateful — read-only, for the log)
        run: npx cdk diff --context stage=dev
        working-directory: infra
      - name: Deploy STATELESS stacks only (stateful is deployed by hand)
        run: >
          npx cdk deploy --require-approval never --context stage=dev
          Grocery-Service-dev Grocery-Ingestion-dev Grocery-Obs-dev Grocery-Frontend-dev
        working-directory: infra
```

Key points:

- **`scripts/build_lambda.py` stays the single packager.** CD runs it (on
  ubuntu-latest, the authoritative manylinux build the script's own comments
  call out), then `cdk deploy` picks up `build/lambda.zip` as the asset. We do
  **not** re-implement packaging in CDK bundling — that would fork the carefully
  tuned exclude list.
- **The stateful stack is excluded from the deploy line.** It is diffed for
  visibility but only ever deployed by a human who has read that diff.
- **`--require-approval never`** is safe here *because* the dangerous stack is
  excluded and the role is permission-bounded; the stateless stacks can be
  rebuilt freely.
- **Add an `infra` job to `ci.yml`** (synth + assertion tests + snapshot) behind
  the existing `summary` gate, so a bad CDK change fails the PR before it can
  reach `deploy.yml` on merge. This matches the repo's *"every job wired into
  one required check"* discipline.

### Cost: **$0.** GitHub Actions minutes are free for the repo's scale; OIDC and
STS are free; CloudFormation is free.

## 4. Deferred: AWS CodePipeline (market-stage design)

CodePipeline was in the brief and is worth documenting so the team learns it and
can adopt it when there is a budget — but it is **not implemented now** because:

- **It costs.** ~$1/month per active pipeline, plus CodeBuild compute minutes,
  plus an S3 artefact bucket. Small, but non-zero and recurring, which the $0
  rule says to defer with a note. This is that note.
- **It buys little the free path lacks** at pilot scale. Its advantages —
  multi-account promotion, manual approval stages, native integration with
  CodeDeploy/CodeBuild, being itself defined in CDK via `@aws-cdk/pipelines` —
  matter at team/market scale, not for a single-account anonymous pilot.

### The design, for when it's adopted

`aws-cdk-lib/pipelines.CodePipeline` (CDK Pipelines) is the idiomatic choice:
the pipeline *is* CDK, and it **self-mutates** (a change to the pipeline
definition updates the pipeline on the next run). Shape:

```
GitHub (source, via CodeStar connection)
  → CodeBuild: synth  (npm ci; python build_lambda.py; cdk synth)
  → Stage: dev
      → deploy stateless stacks
      → (manual approval)  → deploy prod (when a prod stage exists)
```

- Source via a **CodeStar Connections** GitHub app (OAuth), not a stored token.
- A **manual approval** action before any `prod` deploy — the CodePipeline
  analogue of "adopt ≠ deploy."
- The stateful stack still deployed by hand, or behind a hard approval.
- Reference: https://docs.aws.amazon.com/cdk/v2/guide/cdk_pipeline.html

### Cost note for the ledger

> **Deferred paid service:** AWS CodePipeline + CodeBuild. Est. ~$1/pipeline/mo +
> CodeBuild minutes (~$0.005/min, Linux small) + artefact S3. Adopt at market
> stage for multi-account promotion and approval gates. Until then, GitHub
> Actions + OIDC delivers the same dev CD at $0.

## 5. The three-option spectrum (2026 best-practice review)

Current best practice recognises **three** options, not two — worth laying out
so a teammate can choose with eyes open. They differ in *where the pipeline is
defined* and *what runs it*.

| Option | Pipeline defined in | Runs on | Auth | Cost | Maturity |
|--------|--------------------|---------|------|------|----------|
| **A. Hand-written GitHub Actions** | YAML in `.github/workflows/` | GitHub Actions | OIDC (no keys) | **$0** | stable, universally understood |
| **B. `cdk-pipelines-github`** (cdklabs) | **CDK (TypeScript)** → *synthesises* the workflow YAML | GitHub Actions | OIDC (no keys) | **$0** | **experimental** (API may change) |
| **C. CDK Pipelines / CodePipeline** | CDK (TypeScript) | AWS CodePipeline + CodeBuild | CodeStar connection | ~$1/mo + build min | stable, AWS-native |

**What the current guidance says.** The consensus (e.g. *Towards the Cloud*,
*AWS CDK Best Practices 2026*) leans to **Option A** for most teams:
CDK Pipelines *"adds significant complexity to your CDK application — your
pipeline becomes CDK code that deploys itself, which can be confusing to
debug,"* whereas GitHub Actions is *"platform-agnostic and widely understood… most
developers already know how to work with"* it, and OIDC gives *"better security
through explicit trust policies rather than bootstrap roles."*

**Option B is the interesting middle ground.** [`cdk-pipelines-github`](https://github.com/cdklabs/cdk-pipelines-github)
lets you define the pipeline as CDK — `new GitHubWorkflow(app, 'Pipeline', {
awsCreds: AwsCredentials.fromOpenIdConnect({ … }) })` — and `cdk synth`
**writes `.github/workflows/deploy.yml` for you**. You get CDK-native pipeline
definitions and staged deploys with the *"same surface area"* as CDK Pipelines,
but it runs on **free GitHub Actions with OIDC** instead of paid CodePipeline.
The catch: it is **experimental** — *"subject to non-backward compatible changes
or removal in any future version"* — so for a workshop that values stability,
prefer plain Option A now and keep B on the radar.

### Recommendation (research-backed)

**Adopt Option A — hand-written GitHub Actions + OIDC — now.** It is $0, stable,
the most widely understood, reuses the repo's existing credential-free CI
discipline, and teaches the team OIDC (one more AWS capability). Keep the
**Option C / CodePipeline** design (§4) documented for a market-stage
multi-account build, and note **Option B** as the CDK-native $0 path to graduate
to if the team later wants the pipeline itself in TypeScript and is comfortable
tracking an experimental API.

Concretely: add an `infra` CI job (synth + assertion tests) behind the existing
`summary` gate, and a `deploy.yml` that builds the archive, diffs **all** stacks,
and deploys the **stateless** ones on merge to `main`. Keep the stateful stack a
deliberate, human-run `cdk deploy` after a reviewed `cdk diff`.

## 6. References

- AWS CDK Best Practices 2026 (GitHub Actions + OIDC vs CDK Pipelines; least-privilege grants; RemovalPolicy) — https://towardsthecloud.com/blog/aws-cdk-best-practices
- CDK Pipelines for GitHub Workflows (`cdk-pipelines-github`, experimental) — https://github.com/cdklabs/cdk-pipelines-github
- Continuous integration and delivery using CDK Pipelines (AWS docs) — https://docs.aws.amazon.com/cdk/v2/guide/cdk-pipeline.html
- Configuring OpenID Connect in AWS (GitHub docs) — https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services
- `aws-actions/configure-aws-credentials` (OIDC action) — https://github.com/aws-actions/configure-aws-credentials
- GitHub Actions, CDK and OIDC (worked example) — https://ryancormack.medium.com/github-actions-cdk-and-oidc-f638582a2d5b
