/**
 * SCAFFOLD — StatefulStack (Pilot Task 9): adopt the seeded DynamoDB tables.
 *
 * STATUS: skeleton. This uses Strategy A (reference existing tables, UNMANAGED)
 * — the safest option, and the one recommended for the pilot in
 * infra/docs/08-OPEN-DECISIONS.md §2. `fromTableAttributes` returns handles that
 * other stacks can grantRead/grantWrite on, but CloudFormation does NOT manage
 * the tables' lifecycle, so NOTHING here can create, replace, or delete them.
 *
 * ⚠️ BEFORE IMPLEMENTING: confirm the live key schema with `describe-table`
 * (infra/docs/06 §0). Do NOT trust datasets/dynamodb_schema/*.json — that is a
 * different table lineage (SmartGrocery*, see infra/docs/08 §1). The GSI name
 * `GSI1` is proven by config/iam-orchestrator-role.json.
 *
 * To upgrade to Strategy B (full IaC via `cdk import` with RETAIN), see
 * infra/docs/03-STACK-SPECS.md → StatefulStack.
 */
import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Construct } from 'constructs';
import { GroceryConfig } from './config';

export interface StatefulStackProps extends cdk.StackProps {
  readonly cfg: GroceryConfig;
}

export class StatefulStack extends cdk.Stack {
  readonly products: dynamodb.ITable;
  readonly idempotency: dynamodb.ITable;

  constructor(scope: Construct, id: string, props: StatefulStackProps) {
    super(scope, id, props);
    const { names } = props.cfg;

    // Strategy A — reference only, unmanaged. Zero replacement risk.
    this.products = dynamodb.Table.fromTableAttributes(this, 'Products', {
      tableName: names.productsTable, // grocery-products-dev
      globalIndexes: ['GSI1'], // product_key / gsi1_sk (zero-padded price)
    });

    this.idempotency = dynamodb.Table.fromTableAttributes(this, 'Idempotency', {
      tableName: names.idempotencyTable, // grocery-idempotency-dev (TTL)
    });

    // TODO (Pilot Task 15): grocery-meals-dev (recipes + saved plans) when the
    // catalogue lands. Not created here.
  }
}
