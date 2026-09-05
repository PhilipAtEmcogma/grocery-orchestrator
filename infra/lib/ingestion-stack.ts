/**
 * SCAFFOLD — IngestionStack (Pilot Task 13): scheduled price refresh.
 *
 * STATUS: stub. Implement against infra/docs/03-STACK-SPECS.md → IngestionStack.
 *
 * Contains, once implemented:
 *   - ingestion Lambda (SAME asset as the orchestrator; handler
 *     ingestion.handler.lambda_handler) with the SEPARATE ingestion role
 *     (write products only; no Bedrock, no idempotency) — config/iam-ingestion-role.json
 *   - Step Functions state machine from config/ingestion-state-machine.json
 *     (Inline Map, per-retailer isolation, Catch INSIDE the item processor)
 *   - EventBridge daily rule (06:00 NZST; mind UTC+12/+13 DST — see 03)
 *
 * Fixture/recorded adapters first — NO live retailer traffic (tech.md).
 */
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { GroceryConfig } from './config';
import { StatefulStack } from './stateful-stack';

export interface IngestionStackProps extends cdk.StackProps {
  readonly cfg: GroceryConfig;
  readonly tables: StatefulStack;
}

export class IngestionStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: IngestionStackProps) {
    super(scope, id, props);
    // const { cfg, tables } = props;

    // TODO: ingestion role from cfg.configFiles.iamIngestion (Query/Put/BatchWrite
    //   on products base table only — no GSI1, no Bedrock, no idempotency).
    // TODO: ingestion Lambda from the shared asset, handler ingestion.handler.lambda_handler.
    // TODO: StateMachine from cfg.configFiles.stateMachine (resolve ${AWS_*} → tokens).
    // TODO: EventBridge Rule (cron) → StartExecution with { retailers: [...] }.

    cdk.Annotations.of(this).addInfo(
      'IngestionStack is a SCAFFOLD stub — implement per infra/docs/03-STACK-SPECS.md before deploy.',
    );
  }
}
