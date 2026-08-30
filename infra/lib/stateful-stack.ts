/**
 * StatefulStack (Pilot Task 9) — adopt the seeded DynamoDB tables.
 *
 * STRATEGY A: reference the existing tables, UNMANAGED. `fromTableAttributes`
 * returns handles other stacks can `grantRead`/`grantWrite` on, and
 * CloudFormation does NOT own the tables' lifecycle — so nothing in this app
 * can create, replace or delete them. `infra/docs/08-OPEN-DECISIONS.md` §2
 * recommends A for the pilot and B (`cdk import` with RETAIN) later, once the
 * team has run an import against something disposable.
 *
 * THE POINT OF A IS THAT IT CANNOT LOSE DATA. `grocery-products-dev` holds
 * 2,759 real price records and `grocery-idempotency-dev` holds live turn
 * outcomes. A CDK definition that does not exactly match a live table's schema
 * is a REPLACEMENT, and CloudFormation performs replacements by creating the
 * new resource and deleting the old one. That risk is real and it is not worth
 * taking for a pilot whose tables are already correct.
 *
 * SCHEMA CONFIRMED AGAINST THE ACCOUNT, 2026-08-30, not against a document:
 *
 *   grocery-products-dev     PK store_key, SK product_key, PAY_PER_REQUEST, PITR on
 *     GSI1  product_key / gsi1_sk    cheapest-for-a-product
 *     GSI2  category    / gsi2_sk    meal-plan candidates (Pilot Task 6b)
 *   grocery-idempotency-dev  PK pk, TTL on `ttl`
 *
 * `infra/docs/06` §0 says to confirm with `describe-table` and NOT to trust
 * `datasets/dynamodb_schema/*.json` — those describe the data team's separate
 * `SmartGrocery*` lineage (08 §1), which this app deliberately does not adopt.
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

    // Both GSIs are named because a grant must cover them explicitly: an index
    // is a distinct resource ARN, and omitting one yields a working GetItem
    // and a failing Query — the exact access pattern the index exists for.
    // docs/ARCHITECTURE.md §4 records that costing two attempts.
    this.products = dynamodb.Table.fromTableAttributes(this, 'Products', {
      tableName: names.productsTable,
      globalIndexes: ['GSI1', 'GSI2'],
    });

    this.idempotency = dynamodb.Table.fromTableAttributes(this, 'Idempotency', {
      tableName: names.idempotencyTable,
    });

    // Outputs, not resources. This stack deliberately creates nothing, so the
    // outputs are what make it worth deploying: they publish the adopted names
    // for review and for cross-stack reference, and their presence in the
    // template is the evidence that adoption happened without replacement.
    new cdk.CfnOutput(this, 'ProductsTableName', {
      value: names.productsTable,
      description: 'Adopted, not created. Holds the real catalogue; never replaced by CDK.',
    });
    new cdk.CfnOutput(this, 'IdempotencyTableName', {
      value: names.idempotencyTable,
      description: 'Adopted, not created. Holds live turn outcomes; TTL managed outside CDK.',
    });
    new cdk.CfnOutput(this, 'AdoptionStrategy', {
      value: 'A-reference-unmanaged',
      description:
        'infra/docs/08 §2. CDK holds handles only; it cannot create, replace or delete ' +
        'these tables. Upgrade to B (cdk import + RETAIN) only after rehearsing on a ' +
        'disposable table.',
    });

    // Pilot Task 15 adds grocery-meals-dev for the recipe catalogue. Not here:
    // the catalogue is blocked on data (src/recipes/base.py), and creating a
    // table for a feature that cannot run is infrastructure that reads as a
    // capability and does nothing — docs/ARCHITECTURE.md §7 on the S3 bucket.
  }
}
