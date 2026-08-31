// Jest config for the CDK assertion tests (see test/ and
// infra/docs/02-CDK-SCAFFOLD.md §7). Wired into CI as the `infra` job behind
// the `summary` gate on 2026-08-31 — until then this comment described the job
// as future work while the suite it configures was `describe.skip`, so the
// security posture was defined in code that nothing ran.
module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/test'],
  testMatch: ['**/*.test.ts'],
  transform: {
    '^.+\\.tsx?$': 'ts-jest',
  },
};
