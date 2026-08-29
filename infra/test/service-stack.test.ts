/**
 * SCAFFOLD — template CDK assertion tests for the security invariants.
 *
 * STATUS: SKIPPED until ServiceStack is implemented (it is a stub today, so the
 * assertions below have nothing to match). Remove `.skip` once the stack builds
 * real constructs. These encode the invariants from infra/docs/04-SECURITY.md §9
 * as tests — the whole point of IaC-as-code is that they can be asserted.
 *
 * Run: npm test   (jest + ts-jest, see jest.config.js)
 */
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { loadConfig } from '../lib/config';
import { StatefulStack } from '../lib/stateful-stack';
import { ServiceStack } from '../lib/service-stack';

describe.skip('ServiceStack security invariants', () => {
  const app = new cdk.App();
  const cfg = loadConfig('dev');
  const env = { account: '111111111111', region: 'ap-southeast-2' };
  const stateful = new StatefulStack(app, 'Stateful', { env, cfg });
  const service = new ServiceStack(app, 'Service', { env, cfg, tables: stateful });
  const t = Template.fromStack(service);

  it('orchestrator role cannot WRITE the products table', () => {
    // No PutItem/DeleteItem/BatchWriteItem on grocery-products-dev.
    const policies = t.findResources('AWS::IAM::Policy');
    const json = JSON.stringify(policies);
    expect(json).not.toMatch(/dynamodb:PutItem[\s\S]*grocery-products/);
    expect(json).not.toMatch(/dynamodb:BatchWriteItem[\s\S]*grocery-products/);
  });

  it('orchestrator role CAN Scan products (meal-plan candidate search needs it)', () => {
    expect(JSON.stringify(t.findResources('AWS::IAM::Policy'))).toMatch(/dynamodb:Scan/);
  });

  it('the only Resource:"*" is X-Ray', () => {
    // Assert every "*" resource statement is an xray:Put* action. See 04-SECURITY §2.
  });

  it('API stage has throttling', () => {
    t.hasResourceProperties('AWS::ApiGateway::Stage', {
      MethodSettings: Match.arrayWith([
        Match.objectLike({ ThrottlingRateLimit: Match.anyValue() }),
      ]),
    });
  });

  it('POWERTOOLS_LOGGER_LOG_EVENT is never set true (privacy, Req 11.5)', () => {
    const fns = t.findResources('AWS::Lambda::Function');
    for (const fn of Object.values(fns)) {
      const vars = (fn as any).Properties?.Environment?.Variables ?? {};
      expect(vars.POWERTOOLS_LOGGER_LOG_EVENT).not.toBe('true');
    }
  });

  it('Guardrail is applied with a numbered version, never DRAFT', () => {
    const fns = t.findResources('AWS::Lambda::Function');
    for (const fn of Object.values(fns)) {
      const vars = (fn as any).Properties?.Environment?.Variables ?? {};
      if (vars.BEDROCK_GUARDRAIL_VERSION !== undefined) {
        expect(vars.BEDROCK_GUARDRAIL_VERSION).not.toBe('DRAFT');
      }
    }
  });
});
