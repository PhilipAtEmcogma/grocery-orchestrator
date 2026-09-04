/**
 * StatefulStack: adoption must stay adoption, and the one created table must
 * stay survivable.
 *
 * WHY THIS SUITE EXISTS. `StatefulStack` created nothing at all until
 * 2026-09-03, and its safety property was read off a `cdk synth` WARNING —
 * "Resources section must exist and be non-empty" — which was evidence only
 * while somebody happened to read it. Adding `grocery-price-history-dev`
 * removes that warning permanently, so the property it stood for needs a real
 * assertion or it silently stops being checked.
 *
 * That is the same shape as every other finding in this repository: a control
 * that looked like it was working. Here it is made explicit — exactly ONE
 * table resource, and it is the history table.
 *
 * WHAT WOULD BREAK WITHOUT THESE. `dynamodb.Table.fromTableAttributes` and
 * `new dynamodb.Table` differ by one word at the call site and by everything
 * in the template: the first is a handle, the second is a resource
 * CloudFormation owns and can replace. A replacement is performed by creating
 * the new table and deleting the old, and `grocery-products-dev` holds 2,759
 * real price records.
 */
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { loadConfig } from '../lib/config';
import { StatefulStack } from '../lib/stateful-stack';

function synth() {
  const app = new cdk.App();
  const cfg = loadConfig('dev');
  const stack = new StatefulStack(app, 'Grocery-Stateful-test', {
    env: { account: '111111111111', region: 'ap-southeast-2' },
    cfg,
  });
  return { template: Template.fromStack(stack), cfg };
}

describe('StatefulStack adoption', () => {
  it('creates EXACTLY ONE table, and it is the price history table', () => {
    const { template, cfg } = synth();

    // The count is the assertion. Two would mean something started being
    // created that used to be adopted, which is the data-loss direction.
    template.resourceCountIs('AWS::DynamoDB::Table', 1);
    template.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: cfg.names.priceHistoryTable,
    });
  });

  it('does NOT create the products or idempotency tables', () => {
    const { template, cfg } = synth();
    const tables = template.findResources('AWS::DynamoDB::Table');
    const names = Object.values(tables).map((r) => r.Properties?.TableName);

    expect(names).not.toContain(cfg.names.productsTable);
    expect(names).not.toContain(cfg.names.idempotencyTable);
  });

  it('retains the history table on stack delete', () => {
    const { template } = synth();
    // RETAIN, not DESTROY. A baseline rebuilt from scratch is not a baseline:
    // "this price doubled overnight" needs the overnight.
    template.hasResource('AWS::DynamoDB::Table', {
      DeletionPolicy: 'Retain',
      UpdateReplacePolicy: 'Retain',
    });
  });

  it('keys the history table the way src/history writes it', () => {
    const { template } = synth();
    // history_pk / valid_date. Getting the sort key wrong turns an append-only
    // log into an overwrite, and nothing downstream would notice until a
    // baseline was asked for and had one row per product instead of many.
    template.hasResourceProperties('AWS::DynamoDB::Table', {
      KeySchema: [
        { AttributeName: 'history_pk', KeyType: 'HASH' },
        { AttributeName: 'valid_date', KeyType: 'RANGE' },
      ],
      BillingMode: 'PAY_PER_REQUEST',
    });
  });

  it('names the history table from dataSuffix, never the -cdk name suffix', () => {
    const { template, cfg } = synth();

    // The Python side hardcodes `grocery-price-history-dev`
    // (src/history.HISTORY_TABLE) and the ingestion Lambda defaults
    // PRICE_HISTORY_TABLE to it. A `-cdk`-suffixed table would be one the
    // running code cannot find — the write would still fail, and the name
    // would suggest it should not.
    expect(cfg.names.priceHistoryTable).toBe('grocery-price-history-dev');
    expect(cfg.names.priceHistoryTable).not.toContain(cfg.suffix);
    template.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: Match.exact('grocery-price-history-dev'),
    });
  });
});
