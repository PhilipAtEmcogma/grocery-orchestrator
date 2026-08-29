#!/usr/bin/env node
/**
 * SCAFFOLD — CDK app entrypoint for the Smart Grocery & Meal Budget Assistant.
 *
 * STATUS: reviewable skeleton. It instantiates the stacks in dependency order,
 * but the stacks themselves are STUBS (they create no real resources yet — see
 * lib/*-stack.ts). `cdk synth` will therefore produce near-empty templates.
 * Implement the stacks against infra/docs/03-STACK-SPECS.md before deploying.
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

// REGION GUARD (tech.md): all resources deploy to ap-southeast-2, never
// ap-southeast-6 (Auckland). Fail synth loudly rather than deploy to the wrong
// region — the same "fail before apply" philosophy as scripts/aws_placeholders.py.
const REGION = 'ap-southeast-2';
if (process.env.CDK_DEFAULT_REGION && process.env.CDK_DEFAULT_REGION !== REGION) {
  throw new Error(
    `This project deploys ONLY to ${REGION} (tech.md). ` +
      `CDK_DEFAULT_REGION=${process.env.CDK_DEFAULT_REGION} is refused.`,
  );
}

// Account comes from the deploy identity, never a literal (aws_placeholders.py
// reasoning): whoever is authenticated IS the account being deployed to.
const env: cdk.Environment = { account: process.env.CDK_DEFAULT_ACCOUNT, region: REGION };

const cfg = loadConfig(stage);

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
