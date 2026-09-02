/**
 * The security invariants of the reviewer AgentCore Runtime, as executable
 * checks — the CDK-plane mirror of what scripts/reviewer_runtime_preflight.py
 * asserts about config/iam-reviewer-runtime-role.json, plus the runtime's own
 * shape.
 *
 * The point is the same as service-stack.test.ts: the reviewer's isolation
 * (Req 13.8) is the set of actions its role does NOT grant, so the test parses
 * the policy document and compares ACTION SETS PER RESOURCE rather than
 * grepping a stringified blob — the false-positive/false-negative trap that
 * suite documents at length.
 *
 * These run in the `infra` CI job (jest + ts-jest). synth is region-agnostic,
 * so the tests pass regardless of whether AWS::BedrockAgentCore::Runtime is yet
 * deployable in ap-southeast-2 (reviewer-stack.ts header).
 */
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { loadConfig } from '../lib/config';
import { ReviewerStack } from '../lib/reviewer-stack';

interface FlatStatement {
  sid: string;
  actions: string[];
  resources: string[];
}

function flatten(value: unknown): string {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(flatten).join('');
  if (value && typeof value === 'object') {
    const o = value as Record<string, unknown>;
    if (o['Fn::Join']) {
      const [sep, parts] = o['Fn::Join'] as [string, unknown[]];
      return parts.map(flatten).join(sep);
    }
    if (o['Ref']) return `\${${String(o['Ref'])}}`;
    if (o['Fn::GetAtt']) return `\${${flatten(o['Fn::GetAtt'])}}`;
  }
  return JSON.stringify(value);
}

function asArray(value: unknown): string[] {
  if (value === undefined) return [];
  return (Array.isArray(value) ? value : [value]).map(flatten);
}

function statements(t: Template): FlatStatement[] {
  const out: FlatStatement[] = [];
  const collect = (doc: any, source: string) => {
    for (const s of doc?.Statement ?? []) {
      out.push({
        sid: s.Sid ?? `(unnamed in ${source})`,
        actions: asArray(s.Action),
        resources: asArray(s.Resource),
      });
    }
  };
  for (const [id, r] of Object.entries(t.findResources('AWS::IAM::Policy'))) {
    collect((r as any).Properties?.PolicyDocument, id);
  }
  for (const [id, r] of Object.entries(t.findResources('AWS::IAM::Role'))) {
    for (const p of (r as any).Properties?.Policies ?? []) {
      collect(p.PolicyDocument, id);
    }
  }
  return out;
}

describe('ReviewerStack security invariants', () => {
  const app = new cdk.App();
  const cfg = loadConfig('dev');
  const env = { account: '111111111111', region: 'ap-southeast-2' };
  const stack = new ReviewerStack(app, 'Reviewer', { env, cfg });
  const t = Template.fromStack(stack);
  const policy = statements(t);

  // ------------------------------------------------------------------ IAM

  it('the role grants ONLY the reviewer actions the JSON declares (Req 13.8 isolation)', () => {
    // The broadest form: every statement is one config/iam-reviewer-runtime-role
    // declares (by Sid), and nothing else. A statement nobody wrote down is the
    // exact failure this asserts against — the reviewer's whole safety argument
    // is the absence of data actions.
    const declared = new Set([
      'InvokeReviewerModelThroughGuardrail',
      'ReviewerLogs',
      'XRayTracing',
    ]);
    const undeclared = policy.filter((s) => !declared.has(s.sid));
    expect(
      undeclared.map((s) => `${s.sid}: ${s.actions.join(',')} on ${s.resources.join(',')}`),
    ).toEqual([]);
  });

  it('the role grants NO DynamoDB, S3, SQS, SNS, or write action anywhere', () => {
    // The isolation invariant as a denylist, independent of the allowlist above.
    // If a future edit adds a data path, this fails regardless of how it is named.
    const forbidden = ['dynamodb:', 's3:', 'sqs:', 'sns:', 'PutItem', 'DeleteItem',
      'UpdateItem', 'BatchWriteItem', 'PutObject', 'GetObject'];
    const leaks: string[] = [];
    for (const s of policy) {
      for (const a of s.actions) {
        if (forbidden.some((f) => a.includes(f))) leaks.push(`${s.sid}: ${a}`);
      }
    }
    expect(leaks).toEqual([]);
  });

  it('model invocation is scoped to Nova Lite, never bedrock:* or Resource:*', () => {
    const invoke = policy.find((s) => s.sid === 'InvokeReviewerModelThroughGuardrail');
    expect(invoke).toBeDefined();
    expect([...invoke!.actions].sort()).toEqual(['bedrock:ApplyGuardrail', 'bedrock:InvokeModel']);
    // No blanket wildcards on the model action.
    expect(invoke!.actions).not.toContain('bedrock:*');
    expect(invoke!.resources).not.toContain('*');
    // The foundation-model ARN is region-wildcarded ON PURPOSE (the apac.
    // inference profile fans across APAC regions), but the MODEL id stays
    // pinned — a wildcard region, never a wildcard model.
    expect(invoke!.resources.some((r) => r.includes('foundation-model/amazon.nova-lite-v1:0'))).toBe(
      true,
    );
  });

  it('the only Resource:"*" is X-Ray', () => {
    const wildcards = policy.filter((s) => s.resources.includes('*'));
    expect(wildcards.length).toBeGreaterThan(0);
    for (const s of wildcards) {
      expect({ sid: s.sid, nonXray: s.actions.filter((a) => !a.startsWith('xray:')) }).toEqual({
        sid: s.sid,
        nonXray: [],
      });
    }
  });

  it('the role trusts the AgentCore service principal, not lambda', () => {
    const roles = Object.values(t.findResources('AWS::IAM::Role'));
    expect(roles).toHaveLength(1);
    const trust = (roles[0] as any).Properties.AssumeRolePolicyDocument.Statement[0].Principal
      .Service;
    expect(flatten(trust)).toBe('bedrock-agentcore.amazonaws.com');
  });

  // -------------------------------------------------------------- runtime

  it('the runtime is HTTP protocol (not the API default MCP) and PYTHON_3_13', () => {
    const rt = Object.values(t.findResources('AWS::BedrockAgentCore::Runtime'));
    expect(rt).toHaveLength(1);
    const props = (rt[0] as any).Properties;
    expect(props.ProtocolConfiguration).toBe('HTTP');
    expect(props.AgentRuntimeArtifact.CodeConfiguration.Runtime).toBe('PYTHON_3_13');
    expect(props.AgentRuntimeArtifact.CodeConfiguration.EntryPoint).toEqual(['main.py']);
  });

  it('the runtime name matches the CFN pattern (no hyphens)', () => {
    const rt = Object.values(t.findResources('AWS::BedrockAgentCore::Runtime'));
    const name = (rt[0] as any).Properties.AgentRuntimeName;
    expect(name).toMatch(/^[a-zA-Z][a-zA-Z0-9_]{0,47}$/);
  });

  it('the runtime applies a NUMBERED Guardrail, never DRAFT', () => {
    const rt = Object.values(t.findResources('AWS::BedrockAgentCore::Runtime'));
    const envVars = (rt[0] as any).Properties.EnvironmentVariables;
    expect(envVars.REQUIRE_GUARDRAIL).toBe('1');
    expect(envVars.BEDROCK_GUARDRAIL_VERSION).toMatch(/^[0-9]+$/);
    expect(envVars.BEDROCK_GUARDRAIL_ID).toBeDefined();
  });

  it('the runtime uses the role this stack creates', () => {
    const rt = Object.values(t.findResources('AWS::BedrockAgentCore::Runtime'));
    // RoleArn is a Fn::GetAtt on the role in this stack, not a literal.
    expect(flatten((rt[0] as any).Properties.RoleArn)).toContain('ReviewerRole');
  });

  it('the stack creates no data resource — no table, no bucket, no queue', () => {
    // The reviewer creates a role and a runtime, nothing that holds data. It
    // references the code bucket by name; it does not create it.
    expect(t.findResources('AWS::DynamoDB::Table')).toEqual({});
    expect(t.findResources('AWS::S3::Bucket')).toEqual({});
    expect(t.findResources('AWS::SQS::Queue')).toEqual({});
  });
});
