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
 *
 * ONE TABLE IS CREATED HERE, AND THE EXCEPTION IS DELIBERATE.
 * `grocery-price-history-dev` does NOT exist in the account. Strategy A is
 * about not replacing tables that hold data; a table that has never existed
 * holds none, so creating it takes no risk that A was written to avoid.
 * Adopting it instead is not an option — `fromTableAttributes` against a
 * missing table yields a handle that grants successfully and fails at runtime,
 * which is the failure this stack exists to prevent, inverted.
 *
 * It carries `RemovalPolicy.RETAIN`, so the property that matters — a stack
 * delete cannot take the data — still holds for every table this stack knows
 * about. A `cdk destroy` orphans it rather than dropping it.
 *
 * WHY IT IS BEING ADDED NOW. `src/history` and the history write in
 * `ingestion/handler.py` were merged on 2026-09-02 (`acc53fb`) with neither the
 * table nor the IAM grant, and the ingestion Lambda writes to it UNCONDITIONALLY
 * after the products write on an ENABLED daily schedule. No offline gate could
 * see it: `tests/test_ingestion.py` fakes `boto3.resource` and routes by table
 * name, so it proves the code writes history and structurally cannot know the
 * table is absent. Same blind spot as docs/ARCHITECTURE.md §3f and §3g.
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
  readonly priceHistory: dynamodb.ITable;

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

    // ---- CREATED, not adopted. See the header. ----
    //
    // Keys match `src/history.to_history_item` exactly: partition
    // `history_pk` (store_key#product_key, so one product's whole price history
    // at one store is a single query) and sort `valid_date` (so a new capture
    // date APPENDS and a same-day re-run overwrites an identical row). Getting
    // either wrong turns an append-only log into an overwrite.
    //
    // No GSI. Nothing queries history by anything but the product/store pair,
    // and an index on a table with no reader is cost with no access pattern.
    const priceHistory = new dynamodb.Table(this, 'PriceHistory', {
      tableName: names.priceHistoryTable,
      partitionKey: { name: 'history_pk', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'valid_date', type: dynamodb.AttributeType.STRING },
      // PAY_PER_REQUEST like the other two: a daily refresh is bursty and idle
      // the rest of the day, which is the shape provisioned capacity is worst at.
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      // RETAIN, so this stack still cannot lose data on a destroy. The whole
      // point of the history is that it accumulates -- a baseline rebuilt from
      // scratch is not a baseline, and "this price doubled overnight" needs the
      // overnight.
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      // NO PITR, deliberately, and it is not an oversight. The table is
      // append-only and derived: every row is reproducible from a re-run of
      // ingestion over the same source. PITR on the products table protects
      // data that cannot be recreated; here it would pay to protect a
      // derivative. Revisit if history ever becomes the only copy of anything.
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: false },
    });
    this.priceHistory = priceHistory;

    // `cdk synth` no longer emits "Resources section must exist and be
    // non-empty" for this stack, because of the table above. THE ABSENCE OF A
    // TABLE RESOURCE FOR PRODUCTS AND IDEMPOTENCY IS STILL THE ADOPTION
    // EVIDENCE, and it is now asserted rather than read off a warning:
    // `infra/test/stateful-stack.test.ts` asserts that exactly ONE
    // AWS::DynamoDB::Table exists and that it is the history table, so
    // CloudFormation still cannot create, replace or delete the tables holding
    // 2,759 real price records. That assertion replaces the synth warning,
    // which was evidence only while somebody read it -- and it is strictly
    // better, because it also fails if a future change adds a second table
    // here by accident.

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
    new cdk.CfnOutput(this, 'PriceHistoryTableName', {
      value: priceHistory.tableName,
      description:
        'CREATED by this stack (it did not exist), RETAIN on destroy. Append-only ' +
        'baseline for the data-quality reviewer; never read on the shopper path.',
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
