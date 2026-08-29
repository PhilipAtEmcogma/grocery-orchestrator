/**
 * SCAFFOLD — ObservabilityStack (Pilot Task 12): make the pilot operable.
 *
 * STATUS: stub. Implement against infra/docs/03-STACK-SPECS.md → ObservabilityStack.
 *
 * Contains, once implemented:
 *   - SNS topic grocery-orchestrator-alarms-dev (subscriptions added by hand —
 *     email needs out-of-band confirmation)
 *   - metric filter { $.message = "handler_escaped" } with defaultValue 0
 *     (a quiet period must read as all-clear, not INSUFFICIENT_DATA)
 *   - the two day-one alarms from config/alarms.json (handler-escaped; API 5xx)
 *   - a dashboard over the EMF metrics the code already emits
 *   - AWS Budgets (2 free) — the backstop on Bedrock spend
 *   - encrypted + versioned + block-public artefact S3 bucket (RETAIN)
 */
import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { GroceryConfig } from './config';
import { ServiceStack } from './service-stack';

export interface ObservabilityStackProps extends cdk.StackProps {
  readonly cfg: GroceryConfig;
  readonly service: ServiceStack;
}

export class ObservabilityStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id, props);
    // const { cfg, service } = props;

    // TODO: SNS topic (cfg.names.alarmTopic).
    // TODO: MetricFilter on cfg.names.orchestratorLogGroup → GroceryOrchestrator/HandlerEscaped.
    // TODO: two Alarms from cfg.configFiles.alarms, actions → SNS.
    // TODO: Dashboard over latency/tokens/cache/repairs/guardrail/idempotency metrics.
    // TODO: CfnBudget (monthly USD, 80%/100% notifications).
    // TODO: artefact S3 bucket (S3_MANAGED encryption, versioned, BLOCK_ALL, RETAIN).

    cdk.Annotations.of(this).addInfo(
      'ObservabilityStack is a SCAFFOLD stub — implement per infra/docs/03-STACK-SPECS.md before deploy.',
    );
  }
}
