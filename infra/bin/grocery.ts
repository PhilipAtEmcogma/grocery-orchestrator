#!/usr/bin/env node
/**
 * CDK app entrypoint for the Smart Grocery & Meal Budget Assistant.
 *
 * STATUS, 2026-08-31 — TWO OF FIVE STACKS ARE REAL AND DEPLOYED.
 *
 *   Grocery-Stateful-dev   DEPLOYED. Adopts the seeded tables by reference.
 *                          Its template contains no table resource, which IS
 *                          the adoption evidence.
 *   Grocery-Service-dev    DEPLOYED. Lambda, SnapStart alias, REST API
 *                          `crm1xkrk34`, scoped IAM, SSM, log retention,
 *                          throttling, usage plan. Runs BESIDE the hand-made
 *                          plane under a `-cdk` name suffix; the cutover is
 *                          deferred by decision (docs/ARCHITECTURE.md §3m).
 *   Grocery-Obs-dev        REAL, not yet deployed. SNS, metric filters and
 *                          alarms from config/alarms.json ON BOTH PLANES,
 *                          a dashboard, a $25 budget, and the artefact bucket.
 *                          Deploy it before the cutover, not after.
 *   Grocery-Ingestion-dev  STUB.
 *   Grocery-Frontend-dev   STUB.
 *
 * This header said "the stacks themselves are STUBS ... `cdk synth` will
 * produce near-empty templates" for a day after the service plane went live.
 * Same shape as every other drift finding in this repository: a description
 * that was true when written and that nobody went back to when the thing
 * arrived. `infra/test/service-stack.test.ts` is the control that now makes the
 * difference between stub and real observable rather than narrated.
 *
 * Design references:
 *   - infra/docs/02-CDK-SCAFFOLD.md  (app structure, stack decomposition, context)
 *   - infra/docs/03-STACK-SPECS.md   (what each stack contains)
 *   - infra/docs/06-DEPLOYMENT-GUIDE.md (deploy order; stateful-first)
 */
import * as cdk from 'aws-cdk-lib';
import { loadConfig } from '../lib/config';
import { StatefulStack } from '../lib/stateful-stack';
import { ServiceStack } from '../lib/service-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { IngestionStack } from '../lib/ingestion-stack';
import { FrontendStack } from '../lib/frontend-stack';

const app = new cdk.App();

// Stage drives the -dev suffix and production-mode flags (lib/config.ts).
const stage = (app.node.tryGetContext('stage') as string) ?? 'dev';

// REGION (tech.md): all resources deploy to ap-southeast-2, never
// ap-southeast-6 (Auckland), which has no AgentCore and no SnapStart.
//
// THE CONTROL IS THE PIN, NOT A CHECK ON THE AMBIENT VALUE. Every stack below
// is constructed with `env.region = REGION`, so no profile, no shell variable
// and no `--profile` flag can send a resource anywhere else. That is
// structural, and `infra/test/app.test.ts` asserts it over every stack in the
// app, which means CI now checks it.
//
// This block used to `throw` when `CDK_DEFAULT_REGION !== REGION`, and that
// guard fired in exactly the wrong places. `CDK_DEFAULT_REGION` is set by the
// CDK CLI from the resolved AWS profile, not by the operator -- so it could not
// be satisfied by anyone whose default region differs, and it refused
// `cdk synth`, an operation that touches no account at all. Meanwhile in CI
// there are no credentials, so the variable is unset and the guard never ran:
// blocking for a developer, inert for the gate. Same shape as the rest of this
// fortnight's findings, in the guard itself.
//
// The ambient mismatch is still worth SAYING, because it usually means the
// operator's profile points at a different account than they think -- and the
// account is the part that genuinely does come from ambient credentials.
const REGION = 'ap-southeast-2';
const ambient = process.env.CDK_DEFAULT_REGION;

// Account comes from the deploy identity, never a literal (aws_placeholders.py
// reasoning): whoever is authenticated IS the account being deployed to.
const env: cdk.Environment = { account: process.env.CDK_DEFAULT_ACCOUNT, region: REGION };

const cfg = loadConfig(stage);

if (ambient && ambient !== REGION) {
  cdk.Annotations.of(app).addWarningV2(
    'grocery:ambient-region',
    `Your AWS profile resolves to ${ambient}; every stack in this app is pinned ` +
      `to ${REGION} regardless (tech.md). Check WHICH ACCOUNT you are about to ` +
      `deploy into -- that part does come from the profile.`,
  );
}

// Tags on everything (matches the config/*.json tag blocks; ManagedBy=cdk marks
// what has been migrated off the apply scripts).
cdk.Tags.of(app).add('Project', 'SmartGrocery');
cdk.Tags.of(app).add('Env', stage);
cdk.Tags.of(app).add('ManagedBy', 'cdk');

// ---- Stacks, in dependency order (see infra/docs/02 §3) -------------------

// 1. Stateful FIRST — adopts the seeded DynamoDB tables, never replaces them.
const stateful = new StatefulStack(app, `Grocery-Stateful-${stage}`, { env, cfg });

// 2. Service — depends on the tables for grant + name/ARN references.
const service = new ServiceStack(app, `Grocery-Service-${stage}`, {
  env,
  cfg,
  tables: stateful,
});

// 3. Observability — watches the service (needs the API name + function).
new ObservabilityStack(app, `Grocery-Obs-${stage}`, { env, cfg, service });

// 4. Ingestion — reuses the shared Lambda asset; writes to the products table.
new IngestionStack(app, `Grocery-Ingestion-${stage}`, { env, cfg, tables: stateful });

// 5. Frontend — S3 + CloudFront static UI; its domain becomes the API CORS_ORIGIN.
new FrontendStack(app, `Grocery-Frontend-${stage}`, { env, cfg });

app.synth();
